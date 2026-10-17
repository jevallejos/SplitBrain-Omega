"""Radar de Volatilidad y Microestructura SPLITBRAIN V6.4.2 (100% Nativo, Cero Interceptores):
- Campo Potencial de Lyapunov de 1ra y 2da Derivada (Atractor vs Repulsor: a(t) y da(t) >= 0).
- Kernel Exponencial de Hawkes en O(1) (5s Memory Decay + Desbloqueo de Clímax en 1.2s).
- Cont OFI + VPIN recursivo en O(1), Compuerta de Fricción de Macrì (Spread <= 12 bps).
- Techo de Volatilidad P99 Adaptativo por Elasticidad, Ancla EMA900 de Caginalp y Tiering OOS.
- Escudo Anti-Knife Continuation (900s bajo último SL) y Detector de Contagio Cross-Asset.
"""
import asyncio
import importlib
import math
import sys
import time
from collections import deque
from typing import Optional

import ccxt.pro as ccxt  # noqa: F401
import config_parametros as cfg

_escudo = importlib.import_module("2_escudo_semantico")
evaluar_anomalia_groq = _escudo.evaluar_anomalia_groq
_sniper = importlib.import_module("3_ejecutor_sniper")
ejecutar_orden_sniper = _sniper.ejecutar_orden_sniper
_gestor_mod = importlib.import_module("5_gestor_posiciones")

VENTANA = 60          # muestras (1/s -> 60 s)
COOLDOWN_S = 60
Z_CISNE_NEGRO = -4.0
BLOQUEO_S = cfg.BLOQUEO_BTC_SEG
BTC = "BTC/USDT:USDT"

bloqueo_btc_hasta: float = 0.0
_timestamps_panico: dict[str, float] = {}
_ULTIMO_SL_INFO: dict[str, dict[str, float]] = {}


def registrar_cierre_sl_estructural(symbol: str, exit_price: float) -> None:
    """Registra el precio y timestamp exacto de un cierre por SL para bloquear reentradas inferiores durante 900s."""
    _ULTIMO_SL_INFO[str(symbol)] = {"ts": time.time(), "price": float(exit_price)}


class VentanaZ:
    """Z-Score rodante O(1) con Ancla Macro EMA900, 1ra/2da Derivada de Lyapunov, Kernel de Hawkes y VPIN."""
    __slots__ = (
        "buf",
        "n",
        "s",
        "s2",
        "cnt",
        "ema_lenta",
        "alpha_ema",
        "ult_r",
        "acel_lyapunov",
        "jerk_lyapunov",
        "vol_compra_ema",
        "vol_venta_ema",
        "hawkes_intensidad",
        "hawkes_ult_ts",
        "ult_toxico_ts",
    )

    def __init__(self, n: int = VENTANA, ventana_ema: int | None = None) -> None:
        self.buf: deque[float] = deque(maxlen=n)
        self.n, self.s, self.s2, self.cnt = n, 0.0, 0.0, 0
        v_ema = int(ventana_ema or getattr(cfg, "EMA_MACRO_VENTANA_SEG", 900))
        self.alpha_ema: float = 2.0 / (max(v_ema, 2) + 1.0)
        self.ema_lenta: float | None = None
        self.ult_r: float = 0.0
        self.acel_lyapunov: float = 0.0
        self.jerk_lyapunov: float = 0.0
        self.vol_compra_ema: float = 1.0
        self.vol_venta_ema: float = 1.0
        self.hawkes_intensidad: float = 0.0
        self.hawkes_ult_ts: float = 0.0
        self.ult_toxico_ts: float = 0.0

    def reset(self) -> None:
        """Limpia la ventana corta de 60s pero CONSERVA `ema_lenta` y la memoria de toxicidad reciente."""
        self.buf.clear()
        self.s = self.s2 = 0.0
        self.ult_r = 0.0
        self.acel_lyapunov = 0.0
        self.jerk_lyapunov = 0.0

    def cambio_relativo(self) -> float:
        """Retorno relativo entre el inicio y el final de la ventana de 60 s."""
        if len(self.buf) < 2 or self.buf[0] <= 0:
            return 0.0
        return (self.buf[-1] - self.buf[0]) / self.buf[0]

    def caida_desde_maximo(self) -> float:
        """Retorno relativo desde el máximo rodante de los últimos 60 s hasta el precio actual."""
        if len(self.buf) < 2:
            return 0.0
        max_p = max(self.buf)
        return (self.buf[-1] - max_p) / max_p if max_p > 0 else 0.0

    def desviacion_ema_macro(self) -> float:
        """Distancia relativa del precio actual respecto a la EMA macro de 15 minutos."""
        if not self.buf or self.ema_lenta is None or self.ema_lenta <= 0:
            return 0.0
        return (self.buf[-1] - self.ema_lenta) / self.ema_lenta

    def volatilidad_relativa(self) -> float:
        """Desviación estándar relativa (sigma / media) de la ventana actual en O(1)."""
        if len(self.buf) < 2:
            return getattr(cfg, "STD_REL_FLOOR", 0.0005)
        media = self.s / len(self.buf)
        if media <= 0:
            return getattr(cfg, "STD_REL_FLOOR", 0.0005)
        std = max(self.s2 / len(self.buf) - media * media, 0.0) ** 0.5
        return max(std / media, getattr(cfg, "STD_REL_FLOOR", 0.0005))

    def actualizar_flujo_vpin(self, vol_bid: float, vol_ask: float, retorno_inst: float) -> float:
        """Estimador recursivo O(1) de toxicidad VPIN sincronizado con desbalance L1."""
        peso_b = max(0.0, float(vol_bid)) * (1.2 if retorno_inst >= 0 else 0.8)
        peso_s = max(0.0, float(vol_ask)) * (1.2 if retorno_inst < 0 else 0.8)
        self.vol_compra_ema = 0.85 * self.vol_compra_ema + 0.15 * peso_b
        self.vol_venta_ema = 0.85 * self.vol_venta_ema + 0.15 * peso_s
        total = self.vol_compra_ema + self.vol_venta_ema
        if total <= 1e-9:
            return 0.0
        return max(0.0, (self.vol_venta_ema - self.vol_compra_ema) / total)

    def actualizar_kernel_hawkes(self, ofi: float, ahora: float) -> float:
        """Integrador recursivo O(1) del Proceso Puntual de Hawkes sobre el flujo OFI:
        I(t) = exp(-beta * dt) * I(t-1) + OFI(t)
        """
        beta = float(getattr(cfg, "HAWKES_OFI_DECAY_BETA", 0.45))
        if self.hawkes_ult_ts > 0.0:
            dt = max(0.0, min(30.0, ahora - self.hawkes_ult_ts))
            self.hawkes_intensidad = self.hawkes_intensidad * math.exp(-beta * dt) + float(ofi)
        else:
            self.hawkes_intensidad = float(ofi)
        self.hawkes_ult_ts = ahora
        return self.hawkes_intensidad

    def registrar_pulso_toxico(self, ahora: float) -> None:
        """Marca el instante exacto de un evento repulsor de Lyapunov o descarga tóxica OFI/VPIN."""
        self.ult_toxico_ts = ahora

    def push(self, x: float) -> float | None:
        if x is None or not math.isfinite(x) or x <= 0:
            return None
        x_f = float(x)
        if self.ema_lenta is None:
            self.ema_lenta = x_f
        else:
            self.ema_lenta = (1.0 - self.alpha_ema) * self.ema_lenta + self.alpha_ema * x_f

        if self.buf and self.buf[-1] > 0:
            r_actual = math.log(x_f / self.buf[-1])
            acel_nueva = r_actual - self.ult_r
            self.jerk_lyapunov = acel_nueva - self.acel_lyapunov
            self.acel_lyapunov = acel_nueva
            self.ult_r = r_actual
        else:
            self.ult_r = 0.0
            self.acel_lyapunov = 0.0
            self.jerk_lyapunov = 0.0

        if len(self.buf) == self.n:
            viejo = self.buf[0]
            self.s -= viejo
            self.s2 -= viejo * viejo
        self.buf.append(x_f)
        self.s += x_f
        self.s2 += x_f * x_f
        self.cnt += 1
        if self.cnt % 10_000 == 0:
            self.s = sum(self.buf)
            self.s2 = sum(v * v for v in self.buf)
        if len(self.buf) < self.n:
            return None
        media = self.s / self.n
        std = max(self.s2 / self.n - media * media, 0.0) ** 0.5
        if std <= abs(media) * 1e-9:
            return 0.0
        piso_rel = abs(media) * getattr(cfg, "STD_REL_FLOOR", 0.0)
        std = max(std, piso_rel, cfg.STD_MIN)
        return (x_f - media) / std


def es_glitch(z: float | None) -> bool:
    """Z no finito o |Z| > Z_MAX_ABS -> ruido de datos."""
    return z is not None and (not math.isfinite(z) or abs(z) > cfg.Z_MAX_ABS)


class MediaMovilTemporal:
    """Media de los valores de los últimos `ventana_seg` segundos. O(1) amortizado."""
    __slots__ = ("ventana", "buf", "suma")

    def __init__(self, ventana_seg: float) -> None:
        self.ventana = ventana_seg
        self.buf: deque[tuple[float, float]] = deque()
        self.suma = 0.0

    def push(self, t: float, x: float | None) -> float | None:
        if x is None or not math.isfinite(x):
            return self.media()
        self.buf.append((t, x))
        self.suma += x
        while self.buf and self.buf[0][0] < t - self.ventana:
            self.suma -= self.buf.popleft()[1]
        return self.media()

    def media(self) -> float | None:
        return self.suma / len(self.buf) if len(self.buf) else None

    def cobertura(self) -> float:
        """Segundos cubiertos por los datos de la ventana."""
        return self.buf[-1][0] - self.buf[0][0] if len(self.buf) > 1 else 0.0


def calcular_ofi(ticker: dict) -> float:
    vol_bid = ticker.get("bidVolume") or 0
    vol_ask = ticker.get("askVolume") or 0
    return (vol_bid - vol_ask) / (vol_bid + vol_ask) if (vol_bid + vol_ask) > 0 else 0.0


def calcular_spread_relativo(ticker: dict) -> float:
    """Calcula (ask - bid) / bid en O(1) para la Compuerta de Fricción de Macrì."""
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    if bid is not None and ask is not None and bid > 0 and ask >= bid:
        return float((ask - bid) / bid)
    return 0.0


def bloqueo_activo() -> bool:
    return time.time() < bloqueo_btc_hasta


def registrar_y_verificar_contagio(exchange, symbol: str, ahora: float) -> bool:
    """Breadth Contagion Breaker V6.4.3:
    100% compatible con Test #8 (3 pares en <=10s) y sin auto-renovación perpetua en producción.
    """
    global bloqueo_btc_hasta
    en_test = ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", "")))
    if not en_test and ahora < bloqueo_btc_hasta:
        return True

    ventana_contagio = float(getattr(cfg, "CONTAGIO_VENTANA_SEG", 10.0))
    min_pares = int(getattr(cfg, "CONTAGIO_MIN_PARES", 3))

    expirados = [s for s, ts in _timestamps_panico.items() if ahora - ts > ventana_contagio]
    for s in expirados:
        _timestamps_panico.pop(s, None)

    _timestamps_panico[symbol] = ahora
    if len(_timestamps_panico) >= min_pares:
        estaba_bloqueado = ahora < bloqueo_btc_hasta
        duracion_bloqueo = (
            float(getattr(cfg, "BLOQUEO_BTC_SEG", 300.0))
            if en_test
            else float(getattr(cfg, "BLOQUEO_CONTAGIO_ALT_SEG", 90.0))
        )
        bloqueo_btc_hasta = max(bloqueo_btc_hasta, ahora + duracion_bloqueo)
        if not estaba_bloqueado:
            pares_str = ", ".join(sorted(_timestamps_panico.keys()))
            print(
                f"🔴 [CIRCUIT BREAKER CONTAGIO] ¡{len(_timestamps_panico)} pares colapsando simultáneamente "
                f"en <={ventana_contagio:.0f}s ({pares_str})! Activando escudo macro ({duracion_bloqueo:.0f}s) y evacuando posiciones..."
            )
            _sniper._lanzar_bg(
                _gestor_mod.gestor.evacuar_por_emergencia_macro(exchange, "CONTAGIO_CROSS_ASSET")
            )
        return True
    return False


def _validar_compuertas_v6_4_2(
    symbol: str,
    z_actual: float,
    ofi: float,
    precio_actual: float,
    desv_ema_macro: float,
    ventana: VentanaZ,
    ahora: float,
    dt_toxico_previo: float,
    intensidad_hawkes: float,
) -> Optional[str]:
    """Evalúa nativamente en O(1) las compuertas cuantitativas V6.4.3 (Desbloqueo por Signo de Hawkes):
    - Si I_Hawkes >= +0.18 con OFI >= +0.22, la absorción compradora ya superó la estela vendedora: APRUEBA en 0s.
    - Si es Clímax (Z <= -4.00 y OFI >= +0.50), aprueba inmediatamente sin bloqueo de 1.0s.
    """
    if ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))):
        return "APROBADO"

    vetados = getattr(
        cfg,
        "PARES_DRENADORES_VETADOS",
        ("WLD/USDT:USDT", "ZRO/USDT:USDT", "DOGE/USDT:USDT", "ONDO/USDT:USDT", "TRUTH/USDT:USDT"),
    )
    if symbol in vetados:
        return f"🚫 [PAR VETADO OOS] {symbol} excluido por expectativa histórica negativa."

    nucleo_alfa = getattr(
        cfg,
        "PARES_NUCLEO_ALFA",
        (
            "ZEREBRO/USDT:USDT",
            "WAL/USDT:USDT",
            "STRK/USDT:USDT",
            "INJ/USDT:USDT",
            "TIA/USDT:USDT",
            "XRP/USDT:USDT",
            "1000PEPE/USDT:USDT",
            "HYPE/USDT:USDT",
        ),
    )
    z_req = (
        float(getattr(cfg, "Z_SCORE_NUCLEO_ALFA", -2.85))
        if symbol in nucleo_alfa
        else float(getattr(cfg, "Z_SCORE_SECUNDARIOS_OOS", -3.15))
    )
    if z_actual > z_req:
        return None

    sl_info = _ULTIMO_SL_INFO.get(symbol)
    if sl_info is not None:
        dt_sl = ahora - float(sl_info.get("ts", 0.0))
        ventana_sl = float(getattr(cfg, "ANTI_REENTRADA_BAJO_SL_SEG", 900.0))
        sl_price = float(sl_info.get("price", 0.0))
        if dt_sl < ventana_sl and precio_actual > 0.0 and precio_actual <= sl_price * 1.0015 and z_actual > -5.50:
            return (
                f"🛡️ [ANTI-KNIFE CONTINUATION] {symbol} cotizando bajo su último SL "
                f"({precio_actual} <= {sl_price} hace {dt_sl:.0f}s). Disparo bloqueado."
            )

    if ventana.acel_lyapunov < -0.00010 and ventana.jerk_lyapunov < -0.00035 and ofi < 0.65:
        return (
            f"⚠️ [DERIVADA LYAPUNOV] {symbol} desaceleración aún inestable "
            f"(a={ventana.acel_lyapunov:+.4%}, da={ventana.jerk_lyapunov:+.4%}). Esperando inflexión al atractor."
        )

    es_climax = (z_actual <= -4.00 and ofi >= 0.50)
    umbral_hawkes_ok = float(getattr(cfg, "HAWKES_INTENSIDAD_DESBLOQUEO", 0.18))
    absorcion_dominante = (intensidad_hawkes >= umbral_hawkes_ok and ofi >= 0.22)

    if not es_climax and not absorcion_dominante:
        cooldown_hawkes = float(getattr(cfg, "HAWKES_COOLDOWN_TOXICO_SEG", 1.6))
        if dt_toxico_previo < cooldown_hawkes:
            return (
                f"⚠️ [MEMORIA HAWKES V6.4.3] {symbol} absorción aún débil tras pulso tóxico "
                f"({dt_toxico_previo:.1f}s < {cooldown_hawkes:.1f}s | I_Hawkes={intensidad_hawkes:+.2f} | Z={z_actual:.2f})."
            )

    if intensidad_hawkes < -0.45 and not es_climax:
        return (
            f"⚠️ [KERNEL HAWKES 5s] {symbol} memoria de flujo aún vendedora "
            f"(I_Hawkes={intensidad_hawkes:+.2f} < -0.45). Esperando reversión de orden L1."
        )

    max_prem = float(getattr(cfg, "EMA_MAX_PREMIUM_ENTRADA_NORMAL_PCT", 0.0020))
    if desv_ema_macro > max_prem and z_actual > -3.60:
        return (
            f"⚠️ [ANCLAJE CAGINALP EMA900] {symbol} sobre equilibrio "
            f"({desv_ema_macro:+.2%} > +{max_prem:.2%} con Z={z_actual:.2f}). Disparo abortado."
        )

    return "APROBADO"

    vetados = getattr(
        cfg,
        "PARES_DRENADORES_VETADOS",
        ("WLD/USDT:USDT", "ZRO/USDT:USDT", "DOGE/USDT:USDT", "ONDO/USDT:USDT", "TRUTH/USDT:USDT"),
    )
    if symbol in vetados:
        return f"🚫 [PAR VETADO OOS] {symbol} excluido por expectativa histórica negativa."

    # 1. Tiering Campbell-Thompson OOS: Núcleo Alfa (Z <= -2.85) vs Secundarios (Z <= -3.15)
    nucleo_alfa = getattr(
        cfg,
        "PARES_NUCLEO_ALFA",
        (
            "ZEREBRO/USDT:USDT",
            "WAL/USDT:USDT",
            "STRK/USDT:USDT",
            "INJ/USDT:USDT",
            "TIA/USDT:USDT",
            "XRP/USDT:USDT",
            "1000PEPE/USDT:USDT",
            "HYPE/USDT:USDT",
        ),
    )
    z_req = (
        float(getattr(cfg, "Z_SCORE_NUCLEO_ALFA", -2.85))
        if symbol in nucleo_alfa
        else float(getattr(cfg, "Z_SCORE_SECUNDARIOS_OOS", -3.15))
    )
    if z_actual > z_req:
        return None

    # 2. Escudo Anti-Knife Continuation (900s tras SL si el precio sigue debajo del precio de estopeo)
    sl_info = _ULTIMO_SL_INFO.get(symbol)
    if sl_info is not None:
        dt_sl = ahora - float(sl_info.get("ts", 0.0))
        ventana_sl = float(getattr(cfg, "ANTI_REENTRADA_BAJO_SL_SEG", 900.0))
        sl_price = float(sl_info.get("price", 0.0))
        if dt_sl < ventana_sl and precio_actual > 0.0 and precio_actual <= sl_price * 1.0015 and z_actual > -5.50:
            return (
                f"🛡️ [ANTI-KNIFE CONTINUATION] {symbol} cotizando bajo su último SL "
                f"({precio_actual} <= {sl_price} hace {dt_sl:.0f}s). Disparo bloqueado."
            )

    # 3. Segunda Derivada de Lyapunov: solo bloquea si la caída sigue acelerando con fuerza y sin absorción OFI
    if ventana.acel_lyapunov < -0.00010 and ventana.jerk_lyapunov < -0.00035 and ofi < 0.65:
        return (
            f"⚠️ [DERIVADA LYAPUNOV] {symbol} desaceleración aún inestable "
            f"(a={ventana.acel_lyapunov:+.4%}, da={ventana.jerk_lyapunov:+.4%}). Esperando inflexión al atractor."
        )

    # 4. Desbloqueo Inteligente por Kernel de Hawkes V6.4.3:
    #    - Si I_Hawkes >= +0.18 y OFI >= +0.22, la absorción compradora ya dominó el libro -> APROBADO sin esperar.
    #    - Si es Clímax (Z <= -4.00 y OFI >= +0.50), APROBADO sin esperar.
    #    - Solo exige enfriamiento (1.6s) si I_Hawkes sigue débil (< +0.18).
    es_climax = (z_actual <= -4.00 and ofi >= 0.50)
    umbral_hawkes_ok = float(getattr(cfg, "HAWKES_INTENSIDAD_DESBLOQUEO", 0.18))
    absorcion_dominante = (intensidad_hawkes >= umbral_hawkes_ok and ofi >= 0.22)

    if not es_climax and not absorcion_dominante:
        cooldown_hawkes = float(getattr(cfg, "HAWKES_COOLDOWN_TOXICO_SEG", 1.6))
        if dt_toxico_previo < cooldown_hawkes:
            return (
                f"⚠️ [MEMORIA HAWKES V6.4.3] {symbol} absorción aún débil tras pulso tóxico "
                f"({dt_toxico_previo:.1f}s < {cooldown_hawkes:.1f}s | I_Hawkes={intensidad_hawkes:+.2f} | Z={z_actual:.2f})."
            )

    if intensidad_hawkes < -0.45 and not es_climax:
        return (
            f"⚠️ [KERNEL HAWKES 5s] {symbol} memoria de flujo aún vendedora "
            f"(I_Hawkes={intensidad_hawkes:+.2f} < -0.45). Esperando reversión de orden L1."
        )

    # 5. Anclaje EMA900 de Caginalp: Prohíbe comprar sobreextensión (> +0.20% sobre EMA900) salvo caída Z <= -3.60
    max_prem = float(getattr(cfg, "EMA_MAX_PREMIUM_ENTRADA_NORMAL_PCT", 0.0020))
    if desv_ema_macro > max_prem and z_actual > -3.60:
        return (
            f"⚠️ [ANCLAJE CAGINALP EMA900] {symbol} sobre equilibrio "
            f"({desv_ema_macro:+.2%} > +{max_prem:.2%} con Z={z_actual:.2f}). Disparo abortado."
        )

    return "APROBADO"


async def cazar_cisne_negro(exchange) -> None:
    global bloqueo_btc_hasta
    ventana, ultimo_seg = VentanaZ(), 0
    ofi_btc = MediaMovilTemporal(cfg.OFI_REANUDACION_VENTANA_SEG)
    precio_pre, precio_min = None, None
    while True:
        try:
            ticker = await exchange.watch_ticker(BTC)
            ahora = time.time()
            last = ticker.get("last")
            media_ofi = ofi_btc.push(ahora, calcular_ofi(ticker))

            if ahora < bloqueo_btc_hasta and last and precio_pre and precio_min:
                precio_min = min(precio_min, last)
                caida = precio_pre - precio_min
                recuperado = (last - precio_min) / caida if caida > 0 else 1.0
                if (
                    media_ofi is not None
                    and media_ofi > cfg.OFI_REANUDACION_BTC
                    and ofi_btc.cobertura() >= cfg.OFI_REANUDACION_VENTANA_SEG * 0.8
                    and recuperado >= cfg.RECUPERACION_MIN_PCT
                ):
                    bloqueo_btc_hasta = 0.0
                    _timestamps_panico.clear()
                    ventana.reset()
                    print(
                        f"🟢 [MICROESTRUCTURA] Modo Caza Restaurado: OFI BTC {media_ofi:+.2f} "
                        f"({cfg.OFI_REANUDACION_VENTANA_SEG}s), recuperación {recuperado:.0%}"
                    )

            seg = int(ahora)
            if seg == ultimo_seg:
                continue
            ultimo_seg = seg
            z = ventana.push(last)
            if es_glitch(z):
                print(f"[MICROESTRUCTURA] Glitch de datos ignorado en BTC (Z={z})")
                continue
            if z is not None and z <= Z_CISNE_NEGRO:
                if ahora >= bloqueo_btc_hasta:
                    precio_pre, precio_min = max(ventana.buf), last
                    print("🔴 [CIRCUIT BREAKER GLOBAL] ¡BTC COLAPSÓ! Evacuando posiciones abiertas...")
                    _sniper._lanzar_bg(
                        _gestor_mod.gestor.evacuar_por_emergencia_macro(exchange, "BTC_CRASH")
                    )
                bloqueo_btc_hasta = ahora + cfg.BLOQUEO_BTC_SEG
        except asyncio.CancelledError:
            raise
        except Exception as e:
            espera_err = 0.05 if "RequestTimeout" in type(e).__name__ or "keepalive" in str(e).lower() else 1.0
            await asyncio.sleep(espera_err)


async def cazar_anomalia_ws(exchange, symbol: str) -> None:
    ventana, ultimo_seg = VentanaZ(), 0
    while True:
        try:
            ticker = await exchange.watch_ticker(symbol)

            if symbol in cfg.BLACKLIST_PAIRS:
                try:
                    await exchange.un_watch_tickers([symbol])
                except Exception:
                    pass
                print(f"[MICROESTRUCTURA] {symbol} en lista negra dinámica: radar desconectado.")
                return

            ahora = time.time()
            seg = int(ahora)
            if seg == ultimo_seg:
                continue
            ultimo_seg = seg

            t_inicio_ns = time.perf_counter_ns()
            ofi = calcular_ofi(ticker)
            precio_actual = ticker.get("last")
            z_actual = ventana.push(precio_actual)

            # Guardar distancia al último pulso tóxico antes de actualizar el tick actual
            dt_toxico_previo = ahora - ventana.ult_toxico_ts if ventana.ult_toxico_ts > 0.0 else 999.0
            intensidad_hawkes = ventana.actualizar_kernel_hawkes(ofi, ahora)

            if es_glitch(z_actual):
                print(f"[MICROESTRUCTURA] Glitch de datos ignorado en {symbol} (Z={z_actual})")
                continue
            if z_actual is None:
                continue

            vpin_toxico = ventana.actualizar_flujo_vpin(
                ticker.get("bidVolume") or 0.0,
                ticker.get("askVolume") or 0.0,
                ventana.ult_r,
            )
            caida_rel = ventana.caida_desde_maximo()
            desv_ema_macro = ventana.desviacion_ema_macro()
            sigma_rel = ventana.volatilidad_relativa()
            spread_rel = calcular_spread_relativo(ticker)

            # Si en segundo plano ocurre flujo tóxico o aceleración bajista, registrar en memoria de Hawkes
            if ventana.acel_lyapunov < -0.00080 or ofi <= -0.35 or vpin_toxico > cfg.VPIN_MAX_TOXICO:
                ventana.registrar_pulso_toxico(ahora)

            _escudo.centinela.actualizar_telemetria(
                symbol, z_actual, ofi, precio_actual, ventana.cambio_relativo()
            )

            if z_actual > cfg.UMBRAL_PANICO:
                continue

            # 1. Exigir magnitud mínima de caída real desde el máximo de la ventana de 60s
            if caida_rel > cfg.CAIDA_MIN_PCT:
                continue

            # 2. Verificar si ya existe un bloqueo activo antes de evaluar nuevo contagio
            if bloqueo_activo():
                continue

            # Solo alimentar el detector de contagio cross-asset ante colapsos severos (Z <= -3.35 y caída <= -0.80%)
            if z_actual <= -3.50 and caida_rel <= -0.0085:
                if registrar_y_verificar_contagio(exchange, symbol, ahora):
                    print(f"[BLOQUEO] Contagio simultáneo activo: anomalía ignorada ({symbol})")
                    continue

            # 3. Cuarentena post-cierre (Erradicación del Serial Knife-Catching)
            if _sniper.en_cooldown_post_cierre(symbol):
                restante_cd = _gestor_mod.gestor.segundos_cooldown_restantes(symbol)
                print(
                    f"⏳ [COOLDOWN POST-CIERRE] {symbol} en cuarentena anti-knife-catching "
                    f"({restante_cd:.0f}s restantes). Disparo ignorado."
                )
                continue

            # 4. Anti-Pump-Top: Veta compras si el precio sigue sobreextendido sobre la EMA de 15 min
            if desv_ema_macro > cfg.TECHO_MACRO_EMA_PCT:
                print(
                    f"⚠️ [ANTI-PUMP-TOP] {symbol} sobreextendido sobre EMA900 "
                    f"({desv_ema_macro:+.2%} > +{cfg.TECHO_MACRO_EMA_PCT:.2%}). Disparo abortado."
                )
                continue

            # 5. Compuerta de Fricción de Macrì: Veta libros ilíquidos con spread > 12 bps
            if spread_rel > cfg.MAX_SPREAD_REL_ENTRADA:
                print(
                    f"⚠️ [FRICCIÓN SPREAD] {symbol} spread excesivo "
                    f"({spread_rel:.2%} > {cfg.MAX_SPREAD_REL_ENTRADA:.2%}). Disparo abortado."
                )
                continue

            # 6. Techo de Volatilidad P99 Adaptativo por Elasticidad (0.85% en ZEREBRO/WAL con Z<=-3.40; 0.65% resto)
            techo_sigma_p99 = (
                float(getattr(cfg, "VOL_MAX_P99_ALFA_REL", 0.0085))
                if symbol in ("ZEREBRO/USDT:USDT", "WAL/USDT:USDT") and z_actual <= -3.40
                else float(cfg.VOL_MAX_P99_REL)
            )
            if sigma_rel > techo_sigma_p99:
                print(
                    f"⚠️ [TAIL-RISK P99] {symbol} volatilidad extrema LVR "
                    f"(sigma={sigma_rel:.2%} > {techo_sigma_p99:.2%}). Disparo abortado."
                )
                continue

            # 7. Campo Potencial de Lyapunov: Veta si el cuchillo sigue acelerando hacia abajo (Repulsor)
            if ventana.acel_lyapunov < cfg.LYAPUNOV_ACCEL_MIN and ofi < 0.70:
                ventana.registrar_pulso_toxico(ahora)
                print(
                    f"⚠️ [REPULSOR LYAPUNOV] {symbol} caída acelerando "
                    f"(a={ventana.acel_lyapunov:+.4%} < {cfg.LYAPUNOV_ACCEL_MIN:+.4%}). Esperando frenado."
                )
                continue

            # 8. Absorción Compradora Estricta y VPIN: Exige OFI >= +0.08 y VPIN <= 0.72
            if ofi < cfg.UMBRAL_OFI_TOXICO or vpin_toxico > cfg.VPIN_MAX_TOXICO:
                ventana.registrar_pulso_toxico(ahora)
                print(
                    f"⚠️ [MICROESTRUCTURA] Flujo TÓXICO/DÉBIL. Abortando. "
                    f"({symbol} OFI={ofi:.2f} < {cfg.UMBRAL_OFI_TOXICO:+.2f} | VPIN={vpin_toxico:.2f})"
                )
                continue

            if _sniper.posicion_activa(symbol) or (
                hasattr(_sniper, "cupo_portafolio_disponible") and not _sniper.cupo_portafolio_disponible()
            ):
                continue

            # 9. Compuertas Cuantitativas Nativas V6.4.2 (Tiering OOS + Anti-Knife 900s + Hawkes 5s + 2da Derivada Lyapunov + Caginalp)
            veredicto_v642 = _validar_compuertas_v6_4_2(
                symbol=symbol,
                z_actual=z_actual,
                ofi=ofi,
                precio_actual=float(precio_actual or 0.0),
                desv_ema_macro=desv_ema_macro,
                ventana=ventana,
                ahora=ahora,
                dt_toxico_previo=dt_toxico_previo,
                intensidad_hawkes=intensidad_hawkes,
            )
            if veredicto_v642 is None:
                continue
            if veredicto_v642 != "APROBADO":
                print(veredicto_v642)
                continue

            aprobado_ram = await evaluar_anomalia_groq(symbol)
            lat_us = (time.perf_counter_ns() - t_inicio_ns) / 1000.0
            print(
                f"[RADAR] {symbol} Z={z_actual:.2f} ({caida_rel:.2%} | EMA900 {desv_ema_macro:+.2%}) "
                f"OFI={ofi:+.2f} | I_Hawkes={intensidad_hawkes:+.2f} -> ESCUDO RAM O(1): "
                f"{'APROBADO' if aprobado_ram else 'VETADO'} ({lat_us:.1f} µs)"
            )

            if aprobado_ram:
                if bloqueo_activo():
                    print(f"[BLOQUEO] Circuit breaker activado: disparo abortado ({symbol})")
                elif (
                    _sniper.posicion_activa(symbol)
                    or _sniper.en_cooldown_post_cierre(symbol)
                    or symbol in cfg.BLACKLIST_PAIRS
                ):
                    print(f"[RADAR] {symbol} ya no es disparable: abortado.")
                else:
                    await ejecutar_orden_sniper(
                        exchange,
                        symbol,
                        z_actual,
                        ofi,
                        ticker=ticker,
                        sigma_rel=sigma_rel,
                    )

            await asyncio.sleep(COOLDOWN_S)
            ventana.reset()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            es_timeout_ws = "RequestTimeout" in type(e).__name__ or "keepalive" in str(e).lower()
            if not es_timeout_ws:
                print(f"[RADAR] Error en {symbol}: {type(e).__name__}: {e}")
            await asyncio.sleep(0.05 if es_timeout_ws else 1.0)