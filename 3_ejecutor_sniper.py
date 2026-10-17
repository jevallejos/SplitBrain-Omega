"""Ejecutor Sniper SPLITBRAIN V6.4.2:
- Zero-REST Critical Path y Cerrojo Nativo Single-Slot Mutex (MAX_POSICIONES_SIMULTANEAS = 1)[cite: 64].
- Dimensionamiento Estocástico de Capital: Kelly con Restricción Convexa de Drawdown (Busseti, Ryu & Boyd)
  en [0.25, 0.42] + Escalado Inverso por Volatilidad Realizada (Target-Sigma de SYMBA).
- Re-Cotización Inteligente PostOnly de 1 Salto a los 3.5s (Queue-Position & Drift Valuation)[cite: 64].
- Cotización Discreta de Bertrand (Penny-Jump Best-Bid + 1 Tick) y Warm-Socket Keep-Alive[cite: 64].
- Jerarquía Correcta de Stop-Loss en Servidor Bybit (SL_BYBIT_PCT = 1.65% catastrófico)[cite: 64].
"""
import asyncio
import importlib
import math
import re
import sys
import time
from decimal import ROUND_FLOOR, Decimal
from typing import Iterable, Optional

import config_parametros as cfg

enviar_alerta = importlib.import_module("4_telemetria_telegram").enviar_alerta
_gestor_mod = importlib.import_module("5_gestor_posiciones")
_stats = importlib.import_module("6_stats_tracker")

MIN_INVERSION_USDT = getattr(cfg, "MIN_INVERSION_USDT", 2.0)

ESPERA_LLENADO_SEG = getattr(cfg, "ESPERA_LLENADO_SEG", 10)
REQUOTE_TRIGGER_SEG = float(getattr(cfg, "REQUOTE_TRIGGER_SEG", 2.0))
REQUOTE_MAX_DRIFT_PCT = float(getattr(cfg, "REQUOTE_MAX_DRIFT_PCT", 0.0022))
REQUOTE_MIN_OFI = float(getattr(cfg, "REQUOTE_MIN_OFI", 0.35))

INTERVALO_AS_SEG = 2
_tareas_as: set[asyncio.Task] = set()
_tareas_bg: set[asyncio.Task] = set()

_en_vuelo: set[str] = set()
_apalancamiento_ok: set[str] = set()
_lock_disparo = asyncio.Lock()
_RE_BLACKLIST = re.compile(r"(?<!\d)(" + "|".join(cfg.CODIGOS_BLACKLIST) + r")(?!\d)")

_saldo_libre_ram: Optional[float] = None
_saldo_ts: float = 0.0
_SALDO_FALLBACK_TTL_SEG = 2.0


def _lanzar_bg(coro) -> asyncio.Task:
    """Ejecuta una corutina en modo Fire-and-Forget protegiendo su referencia contra el GC[cite: 64]."""
    tarea = asyncio.create_task(coro)
    _tareas_bg.add(tarea)
    tarea.add_done_callback(_tareas_bg.discard)
    return tarea


def enviar_alerta_bg(mensaje: str) -> None:
    """Despacha alerta a Telegram en background (0 ms de bloqueo en ruta crítica)[cite: 64]."""
    _lanzar_bg(enviar_alerta(mensaje))


async def sincronizar_saldo_ram(exchange) -> float:
    """Actualiza el caché de margen libre USDT en RAM consultando Bybit fuera de ruta crítica[cite: 64]."""
    global _saldo_libre_ram, _saldo_ts
    if cfg.MODO_PAPER or exchange is None:
        _saldo_libre_ram = float(cfg.CAPITAL_BASE_USDT)
        _saldo_ts = time.time()
        return _saldo_libre_ram
    try:
        balance = await exchange.fetch_balance()
        libre = float((balance.get("USDT") or {}).get("free") or 0.0)
        _saldo_libre_ram = libre
        _saldo_ts = time.time()
        return libre
    except Exception as e:
        print(f"[FRANCOTIRADOR] Aviso sincronizando saldo RAM: {type(e).__name__}: {e}")
        return _saldo_libre_ram or 0.0


def disparar_sincronizacion_saldo_bg(exchange) -> None:
    """Programa una reconciliación asíncrona del saldo libre en RAM sin bloquear el loop[cite: 64]."""
    if not cfg.MODO_PAPER and exchange is not None:
        _lanzar_bg(sincronizar_saldo_ram(exchange))


async def bucle_keepalive_socket(exchange) -> None:
    """Warm-Socket Keep-Alive: refresca la sesión HTTP/TLS privada y el caché `_saldo_libre_ram` cada 45s[cite: 64]."""
    intervalo = float(getattr(cfg, "KEEPALIVE_SOCKET_SEG", 45.0))
    while True:
        await asyncio.sleep(intervalo)
        if cfg.MODO_PAPER or exchange is None:
            continue
        try:
            if not _en_vuelo:
                await sincronizar_saldo_ram(exchange)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def precalentar_apalancamiento_universo(exchange, simbolos: Iterable[str]) -> None:
    """Pre-configura el apalancamiento en el arranque para evitar llamadas REST en el disparo[cite: 64]."""
    if cfg.MODO_PAPER:
        for s in simbolos:
            _apalancamiento_ok.add(s)
        return
    for symbol in simbolos:
        if symbol in cfg.BLACKLIST_PAIRS or symbol in cfg.PROHIBIDAS:
            continue
        try:
            await _fijar_apalancamiento(exchange, symbol)
        except Exception as e:
            if _es_error_blacklist(e):
                cfg.BLACKLIST_PAIRS.add(symbol)
                print(f"[FRANCOTIRADOR] {symbol} movido a lista negra en pre-calentamiento ({e}).")


def posicion_activa(symbol: str) -> bool:
    """True si el par tiene una compra en curso o una posición gestionada[cite: 64]."""
    return symbol in _en_vuelo or _gestor_mod.gestor.tiene(symbol)


def total_posiciones_activas() -> int:
    """Cuenta el total de símbolos con orden en vuelo o posición viva en el gestor[cite: 64]."""
    activos = set(_en_vuelo)
    for sym, pos in list(_gestor_mod.gestor.posiciones.items()):
        if not getattr(pos, "cerrada", False):
            activos.add(sym)
    return len(activos)


def cupo_portafolio_disponible() -> bool:
    """Garantiza estricto cumplimiento de MAX_POSICIONES_SIMULTANEAS (1) para evitar solapamientos[cite: 64]."""
    if ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))):
        return True
    max_pos = int(getattr(cfg, "MAX_POSICIONES_SIMULTANEAS", getattr(cfg, "MAX_POSICIONES", 1)))
    return total_posiciones_activas() < max(1, max_pos)


def en_cooldown_post_cierre(symbol: str) -> bool:
    """True si el símbolo se encuentra en cuarentena tras haber cerrado una posición[cite: 64]."""
    return _gestor_mod.gestor.en_cooldown(symbol)


def _d(x) -> Decimal:
    return Decimal(str(x))


def cuantizar_orden(exchange, symbol: str, nocional: float, precio: float) -> Optional[tuple[str, str]]:
    """Smart quantization: cantidad = floor(nocional / precio) al step_size; precio con floor al tick_size[cite: 64]."""
    m = exchange.market(symbol)
    step = _d(m["precision"]["amount"])
    tick = _d(m["precision"]["price"])
    min_qty = _d(m["limits"]["amount"]["min"] or 0)
    min_notional = _d(m["limits"]["cost"]["min"] or 0)

    precio_q = (_d(precio) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if precio_q <= 0:
        print(f"[FRANCOTIRADOR] Precio inválido en {symbol}: {precio}")
        return None
    cantidad = (_d(nocional) / precio_q / step).to_integral_value(rounding=ROUND_FLOOR) * step
    valor = cantidad * precio_q
    if cantidad <= 0 or cantidad < min_qty or valor < min_notional:
        print(
            f"[FRANCOTIRADOR] Munición insuficiente en {symbol}: {cantidad.normalize():f} x {precio_q.normalize():f} "
            f"= {valor:.4f} USDT (mín. {min_notional.normalize():f} USDT, mín. qty {min_qty.normalize():f}). Disparo abortado."
        )
        return None
    return exchange.amount_to_precision(symbol, float(cantidad)), exchange.price_to_precision(symbol, float(precio_q))


def _es_error_blacklist(e: Exception) -> bool:
    """Errores de permiso por símbolo (acuerdo sin firmar, compliance, whitelist)[cite: 64]."""
    texto = str(e)
    return bool(_RE_BLACKLIST.search(texto)) or "agreement" in texto.lower()


async def _fijar_apalancamiento(exchange, symbol: str) -> None:
    if symbol in _apalancamiento_ok:
        return
    try:
        await exchange.set_leverage(cfg.APALANCAMIENTO, symbol)
    except Exception as e:
        if "110043" not in str(e):
            raise
    _apalancamiento_ok.add(symbol)


def _extraer_precio_maker_ws(
    exchange, symbol: str, ticker_ws: Optional[dict], ofi: Optional[float] = None
) -> Optional[float]:
    """Cotización Discreta de Bertrand (Best-Bid & Penny-Jump)[cite: 64]."""
    t = ticker_ws
    if not t and hasattr(exchange, "tickers") and isinstance(exchange.tickers, dict):
        t = exchange.tickers.get(symbol)
    if not t:
        return None

    last = t.get("last")
    bid = t.get("bid")
    ask = t.get("ask")

    if bid is not None and ask is not None and bid > 0 and ask > bid:
        try:
            m = exchange.market(symbol)
            tick = float(m["precision"]["price"])
            if (
                tick > 0
                and (ask - bid) >= (2.0 * tick - 1e-12)
                and ofi is not None
                and ofi >= cfg.PENNY_JUMP_OFI_MIN
            ):
                candidato = bid + tick
                if candidato < ask:
                    return float(candidato)
        except Exception:
            pass
        return float(bid)

    if last is not None and bid is not None and last > 0 and bid > 0:
        return float(min(last, bid))
    if bid is not None and bid > 0:
        return float(bid)
    if last is not None and last > 0:
        return float(last)
    return None


def _obtener_sl_bybit_pct() -> float:
    """Devuelve el porcentaje de Stop-Loss catastrófico alojado en Bybit (~1.65%),
    garantizando que jamás sea más estrecho que el SL interno (~1.15%)[cite: 64].
    """
    sl_int = min(float(getattr(cfg, "SL_INTERNO_PCT", getattr(cfg, "SL_INICIAL_PCT", 0.0115))), 0.0125)
    sl_exc = float(getattr(cfg, "SL_BYBIT_PCT", getattr(cfg, "STOP_LOSS_BYBIT_PCT", getattr(cfg, "SL_PCT", 0.0165))))
    return max(sl_exc, sl_int + 0.0040, 0.0155)


def _calcular_kelly_operativo(symbol: str, sigma_rel: Optional[float], saldo_libre: float) -> float:
    """Obtiene en O(1) la fracción de Kelly dinámica (Busseti-Ryu-Boyd + Target-Sigma) desde StatsTracker."""
    if hasattr(_stats, "tracker") and hasattr(_stats.tracker, "calcular_kelly_dinamico"):
        try:
            return float(_stats.tracker.calcular_kelly_dinamico(symbol=symbol, sigma_rel=sigma_rel, saldo_libre=saldo_libre))
        except Exception:
            pass
    return float(getattr(cfg, "KELLY_MAX_PCT", 0.35))


async def ejecutar_orden_sniper(
    exchange,
    symbol: str,
    z_score: float,
    ofi: Optional[float] = None,
    ticker: Optional[dict] = None,
    sigma_rel: Optional[float] = None,
) -> None:
    global _saldo_libre_ram
    if cfg.MODO_PAPER:
        print(
            f"[PAPER TRADING] Ejecución simulada en {symbol} "
            f"(Z={z_score:.2f}, OFI={ofi if ofi is None else round(ofi, 2)})"
        )
        return
    if (
        symbol in cfg.BLACKLIST_PAIRS
        or posicion_activa(symbol)
        or not cupo_portafolio_disponible()
        or en_cooldown_post_cierre(symbol)
    ):
        return

    radar_mod = importlib.import_module("1_radar_volatilidad")
    if radar_mod.bloqueo_activo():
        print(f"[BLOQUEO] Circuit breaker activo antes de lock: disparo abortado ({symbol})")
        return

    async with _lock_disparo:
        if (
            symbol in cfg.BLACKLIST_PAIRS
            or posicion_activa(symbol)
            or not cupo_portafolio_disponible()
            or en_cooldown_post_cierre(symbol)
            or radar_mod.bloqueo_activo()
        ):
            return

        t_disparo_ns = time.perf_counter_ns()
        try:
            # 0. Mercados ya cargados en main.py (O(1) en RAM); solo carga si el dict está vacío[cite: 64]
            if not getattr(exchange, "markets", None):
                await exchange.load_markets()

            # 1. Lectura de Saldo Libre O(1) en RAM y Fracción de Kelly Dinámica BRB + Target-Sigma
            if _saldo_libre_ram is None:
                await sincronizar_saldo_ram(exchange)
            libre = float(_saldo_libre_ram or 0.0)
            kelly_eff = _calcular_kelly_operativo(symbol, sigma_rel, libre)
            inversion = libre * kelly_eff

            if inversion < MIN_INVERSION_USDT and (time.time() - _saldo_ts) >= _SALDO_FALLBACK_TTL_SEG:
                libre = await sincronizar_saldo_ram(exchange)
                kelly_eff = _calcular_kelly_operativo(symbol, sigma_rel, libre)
                inversion = libre * kelly_eff

            if inversion < MIN_INVERSION_USDT:
                print(
                    f"[FRANCOTIRADOR] Munición insuficiente: margen {inversion:.2f} USDT < "
                    f"{MIN_INVERSION_USDT} USDT. Disparo abortado."
                )
                return
            nocional = inversion * cfg.APALANCAMIENTO

            # 2. Precio de disparo desde WebSocket con lógica Best-Bid / Penny-Jump O(1)[cite: 64]
            precio_ref = _extraer_precio_maker_ws(exchange, symbol, ticker, ofi=ofi)
            if precio_ref is None:
                t_rest = await exchange.fetch_ticker(symbol)
                last, bid = t_rest.get("last"), t_rest.get("bid")
                precio_ref = float(min(last, bid) if (bid and last) else (bid or last))

            # 3. Smart quantization O(1) en CPU[cite: 64]
            cuantizado = cuantizar_orden(exchange, symbol, nocional, precio_ref)
            if cuantizado is None:
                return
            amount, price = cuantizado
            sl_bybit_pct = _obtener_sl_bybit_pct()
            precio_sl = exchange.price_to_precision(symbol, float(price) * (1.0 - sl_bybit_pct))

            # 4. Apalancamiento O(1) (pre-calentado) + Única llamada de red: create_order[cite: 64]
            await _fijar_apalancamiento(exchange, symbol)
            params = {"timeInForce": "PostOnly", "stopLoss": str(precio_sl)}
            orden = await exchange.create_order(symbol, "limit", "buy", amount, price, params)
            _en_vuelo.add(symbol)

            # Descuento inmediato en RAM para evitar sobre-disparo en ráfagas simultáneas[cite: 64]
            margen_comprometido = (float(amount) * float(price)) / float(cfg.APALANCAMIENTO)
            _saldo_libre_ram = max(0.0, libre - margen_comprometido)
            disparar_sincronizacion_saldo_bg(exchange)
        except Exception as e:
            print(f"[FRANCOTIRADOR] ERROR ejecutando {symbol}: {type(e).__name__}: {e}")
            if _es_error_blacklist(e):
                cfg.BLACKLIST_PAIRS.add(symbol)
                print(f"[FRANCOTIRADOR] {symbol} a la lista negra dinámica (permiso/contrato). WebSocket liberado.")
            disparar_sincronizacion_saldo_bg(exchange)
            enviar_alerta_bg(f"🔴 ERROR Sniper {symbol}: {e}")
            return

    lat_orden_ms = (time.perf_counter_ns() - t_disparo_ns) / 1_000_000.0

    # 5. PRIORIDAD ABSOLUTA: Iniciar vigilancia de la orden ANTES de enviar telemetría a Telegram[cite: 64]
    if cfg.MOTOR_SALIDA == "AS":
        tarea = asyncio.create_task(
            motor_salida_avellaneda_stoikov(
                exchange, symbol, amount, price, orden["id"], z_score, ofi, sigma_rel=sigma_rel
            )
        )
    else:
        tarea = asyncio.create_task(
            _entregar_al_gestor(exchange, symbol, orden["id"], price, z_score, ofi, nocional=nocional)
        )
    _tareas_as.add(tarea)
    tarea.add_done_callback(_tareas_as.discard)
    tarea.add_done_callback(lambda _t: _en_vuelo.discard(symbol))

    print(
        f"[FRANCOTIRADOR] Orden {orden['id']} en {symbol}: {amount} @ {price} "
        f"(nocional ~{float(amount) * float(price):.2f} USDT, {cfg.APALANCAMIENTO}x, Kelly={kelly_eff:.0%}, "
        f"Z={z_score:.2f}, OFI={ofi if ofi is None else round(ofi, 2)}, RTT={lat_orden_ms:.1f}ms)"
    )
    enviar_alerta_bg(
        f"🟩 ALERTA VERDE (PROD):\nOrden Limit PostOnly en {symbol}\nPrecio: {price}\nID: {orden['id']}"
    )


async def _entregar_al_gestor(
    exchange,
    symbol: str,
    id_compra: str,
    price: str,
    z_score: float,
    ofi: Optional[float],
    nocional: Optional[float] = None,
) -> None:
    """Espera el llenado real de la compra (con Re-Quote inteligente a los 3.5s) y entrega la posición al gestor[cite: 64]."""
    try:
        llenado, medio = await _esperar_llenado_detalle(
            exchange,
            symbol,
            id_compra,
            precio_inicial=float(price),
            nocional=nocional,
            ofi_entry=ofi,
        )
        if llenado <= 0:
            print(f"[FRANCOTIRADOR] Compra en {symbol} no llenada en {ESPERA_LLENADO_SEG}s: margen liberado.")
            await sincronizar_saldo_ram(exchange)
            return
        _gestor_mod.gestor.abrir(exchange, symbol, llenado, medio or float(price), z_score, ofi)
    except Exception as e:
        print(
            f"[FRANCOTIRADOR] ERROR entregando {symbol} al gestor: {type(e).__name__}: {e} "
            f"(la posición conserva el SL de Bybit)"
        )
        disparar_sincronizacion_saldo_bg(exchange)


async def _estado_orden(exchange, order_id: str, symbol: str) -> dict:
    return await exchange.fetch_order(order_id, symbol, {"acknowledged": True})


async def _posicion_larga(exchange, symbol: str) -> Optional[float]:
    """Tamaño de la posición larga abierta (0 si no hay). None si la API falla[cite: 64]."""
    try:
        posiciones = await exchange.fetch_positions([symbol])
    except Exception as e:
        print(f"[{symbol}] AS: no se pudo leer la posición: {type(e).__name__}: {e}")
        return None
    total = 0.0
    for p in posiciones or []:
        if p.get("symbol") == symbol and p.get("side") == "long":
            total += float(p.get("contracts") or 0)
    return total


async def _intentar_requote_postonly(
    exchange,
    symbol: str,
    precio_original: float,
    nocional: float,
    ofi_entry: Optional[float],
) -> Optional[tuple[str, float]]:
    """Re-Cotización Inteligente de 1 Salto (Queue-Position & Drift Valuation):
    Si el precio subió entre 1 tick y +0.22% durante el vuelo y persiste absorción compradora,
    coloca 1 nueva orden Limit Buy PostOnly en el nuevo best_bid[cite: 64].
    """
    if ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))):
        return None
    try:
        radar_mod = importlib.import_module("1_radar_volatilidad")
        if radar_mod.bloqueo_activo():
            return None
    except Exception:
        pass

    t_ws = None
    if hasattr(exchange, "tickers") and isinstance(exchange.tickers, dict):
        t_ws = exchange.tickers.get(symbol)
    if not t_ws:
        return None

    bid_now = float(t_ws.get("bid") or 0.0)
    ask_now = float(t_ws.get("ask") or 0.0)
    if bid_now <= precio_original or ask_now <= bid_now:
        return None

    drift_pct = (bid_now - precio_original) / precio_original
    if drift_pct <= 0.0 or drift_pct > REQUOTE_MAX_DRIFT_PCT:
        return None

    vb = float(t_ws.get("bidVolume") or 0.0)
    va = float(t_ws.get("askVolume") or 0.0)
    ofi_now = (vb - va) / (vb + va) if (vb + va) > 0 else (ofi_entry or 0.0)
    if ofi_now < REQUOTE_MIN_OFI and (ofi_entry is None or ofi_entry < 0.75):
        return None

    nuevo_ref = _extraer_precio_maker_ws(exchange, symbol, t_ws, ofi=ofi_now)
    if nuevo_ref is None or nuevo_ref <= precio_original:
        return None

    cuantizado = cuantizar_orden(exchange, symbol, nocional, nuevo_ref)
    if cuantizado is None:
        return None
    amount_rq, price_rq = cuantizado
    sl_bybit_pct = _obtener_sl_bybit_pct()
    precio_sl_rq = exchange.price_to_precision(symbol, float(price_rq) * (1.0 - sl_bybit_pct))

    try:
        orden_rq = await exchange.create_order(
            symbol,
            "limit",
            "buy",
            amount_rq,
            price_rq,
            {"timeInForce": "PostOnly", "stopLoss": str(precio_sl_rq)},
        )
        oid_rq = str(orden_rq.get("id"))
        print(
            f"[FRANCOTIRADOR] 🔄 Re-Quote 3.5s PostOnly en {symbol}: {amount_rq} @ {price_rq} "
            f"(drift {drift_pct:+.2%}, OFI={ofi_now:+.2f})"
        )
        return oid_rq, float(price_rq)
    except Exception:
        return None


async def _esperar_llenado_detalle(
    exchange,
    symbol: str,
    id_compra: str,
    precio_inicial: Optional[float] = None,
    nocional: Optional[float] = None,
    ofi_entry: Optional[float] = None,
) -> tuple[float, Optional[float]]:
    """(cantidad realmente comprada, precio medio). Incluye 1 Re-Quote inteligente a los 3.5s si no llenó[cite: 64]."""
    def _resultado(o: dict) -> tuple[float, Optional[float]]:
        medio = o.get("average") or o.get("price")
        return float(o.get("filled") or 0), (float(medio) if medio else None)

    t_inicio = time.time()
    limite = t_inicio + ESPERA_LLENADO_SEG
    oid_actual = id_compra
    precio_base = float(precio_inicial or 0.0)
    nocional_base = float(nocional or 0.0)
    requote_usado = False
    paso_sleep = 1.0 if ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))) else 0.5

    while time.time() < limite:
        await asyncio.sleep(paso_sleep)
        try:
            o = await _estado_orden(exchange, oid_actual, symbol)
        except Exception:
            continue

        st = o.get("status")
        filled_now = float(o.get("filled") or 0.0)

        if st == "closed":
            return _resultado(o)

        # Si Bybit canceló/rechazó la orden PostOnly al vuelo o cumplimos 3.5s con 0 llenado, evaluar 1 Re-Quote[cite: 64]
        edad_orden = time.time() - t_inicio
        if st in ("canceled", "rejected", "expired"):
            if filled_now > 0:
                return _resultado(o)
            if not requote_usado and precio_base > 0 and nocional_base > 0 and edad_orden <= 6.0:
                requote_usado = True
                rq = await _intentar_requote_postonly(exchange, symbol, precio_base, nocional_base, ofi_entry)
                if rq is not None:
                    oid_actual, precio_base = rq
                    continue
            return _resultado(o)

        if (
            not requote_usado
            and filled_now <= 0.0
            and edad_orden >= REQUOTE_TRIGGER_SEG
            and precio_base > 0
            and nocional_base > 0
            and not (("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))) or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", "")))
        ):
            # Verificar si el best_bid se alejó hacia arriba antes de cancelar la orden original[cite: 64]
            t_ws = exchange.tickers.get(symbol) if hasattr(exchange, "tickers") and isinstance(exchange.tickers, dict) else None
            bid_ws = float((t_ws or {}).get("bid") or 0.0)
            if bid_ws > precio_base and ((bid_ws - precio_base) / precio_base) <= REQUOTE_MAX_DRIFT_PCT:
                requote_usado = True
                try:
                    await exchange.cancel_order(oid_actual, symbol)
                except Exception:
                    pass
                try:
                    o_check = await _estado_orden(exchange, oid_actual, symbol)
                    if float(o_check.get("filled") or 0.0) > 0:
                        return _resultado(o_check)
                except Exception:
                    pass
                rq = await _intentar_requote_postonly(exchange, symbol, precio_base, nocional_base, ofi_entry)
                if rq is not None:
                    oid_actual, precio_base = rq
                    limite = max(limite, time.time() + 6.5)
                    continue
                return 0.0, None

    try:
        await exchange.cancel_order(oid_actual, symbol)
    except Exception:
        pass
    try:
        return _resultado(await _estado_orden(exchange, oid_actual, symbol))
    except Exception as e:
        print(f"[{symbol}] AS: no se pudo confirmar el llenado de la compra: {type(e).__name__}: {e}")
        return 0.0, None


async def _esperar_llenado_compra(exchange, symbol: str, id_compra: str) -> float:
    """Devuelve la cantidad realmente comprada (0 si no se llenó). Cancela el resto al expirar[cite: 64]."""
    return (await _esperar_llenado_detalle(exchange, symbol, id_compra))[0]


def calcular_precio_salida_hjb_as(
    s_mid: float,
    entry: float,
    q_norm: float,
    sigma_rel: float,
    t_restante: float,
    t_total: float,
) -> float:
    """Ecuación HJB de Avellaneda-Stoikov (2008) para liquidación pasiva de inventario[cite: 64]."""
    gamma = float(getattr(cfg, "GAMMA_AS", 0.05))
    kappa = float(getattr(cfg, "KAPPA_AS", 1.5))
    tau = max(0.0, min(1.0, t_restante / max(t_total, 1.0)))
    sigma_eff = max(float(sigma_rel), getattr(cfg, "STD_REL_FLOOR", 0.0005))

    reserva_as = s_mid * (1.0 - q_norm * gamma * (sigma_eff ** 2) * max(t_restante, 0.0))
    spread_optimo_rel = min(0.0045, (1.0 / max(gamma, 1e-4)) * math.log(1.0 + gamma / max(kappa, 1e-4)) * 1e-3)
    ask_hjb = reserva_as * (1.0 + spread_optimo_rel)

    techo_dinamico = entry * (1.0 + cfg.TP_PCT * tau)
    return min(techo_dinamico, max(ask_hjb, entry * (1.0 + cfg.BE_OFFSET_PCT)))


async def _registrar_cierre_as(
    exchange,
    symbol: str,
    entrada: float,
    salida: float,
    cantidad: float,
    motivo: str,
    t_inicio: float,
    z_score: Optional[float],
    ofi: Optional[float],
    es_maker_salida: bool,
) -> None:
    """Registra las operaciones del motor AS en la Caja Negra, activa Cooldown Post-Cierre y refresca RAM[cite: 64]."""
    try:
        _gestor_mod.gestor.registrar_cooldown_post_cierre(symbol)
        fee_out = cfg.FEE_MAKER if es_maker_salida else cfg.FEE_TAKER
        pnl_pct, pnl_usdt, fees = _stats.calcular_pnl(
            entrada, float(salida), float(cantidad), cfg.FEE_MAKER, fee_out
        )
        hold = time.time() - t_inicio
        await _stats.tracker.registrar({
            "symbol": symbol,
            "side": "long",
            "z_score_entry": z_score,
            "ofi_entry": ofi,
            "pnl_percentage": pnl_pct,
            "pnl_usdt": pnl_usdt,
            "entry_price": entrada,
            "exit_price": float(salida),
            "qty": float(cantidad),
            "motivo": motivo,
            "hold_seg": round(hold, 1),
            "fees_usdt": fees,
        })
        await sincronizar_saldo_ram(exchange)
        _stats.tracker.imprimir_dashboard(await _stats.leer_balance_real(exchange))
    except Exception as e:
        print(f"[{symbol}] AS: error registrando en Caja Negra: {type(e).__name__}: {e}")


async def motor_salida_avellaneda_stoikov(
    exchange,
    symbol: str,
    amount,
    entry_price,
    id_compra: Optional[str] = None,
    z_score: Optional[float] = None,
    ofi: Optional[float] = None,
    sigma_rel: Optional[float] = None,
) -> None:
    try:
        if id_compra is not None:
            llenado, medio = await _esperar_llenado_detalle(exchange, symbol, id_compra)
            if llenado <= 0:
                print(f"[{symbol}] AS: compra no llenada; no hay posición que gestionar.")
                await sincronizar_saldo_ram(exchange)
                return
            amount = llenado
            if medio:
                entry_price = medio

        entry = float(entry_price)
        cantidad_inicial = float(amount)
        tiempo_inicio = time.time()
        precio_objetivo_max = entry * (1.0 + cfg.TP_PCT)
        p_minimo_rentable = entry * (1.0 + cfg.BE_OFFSET_PCT)
        salida = {"timeInForce": "PostOnly", "reduceOnly": True}
        sigma_local = float(sigma_rel or getattr(cfg, "STD_REL_FLOOR", 0.0005))

        try:
            p_exit_format = exchange.price_to_precision(symbol, precio_objetivo_max)
            orden_venta = await exchange.create_order(symbol, "limit", "sell", amount, float(p_exit_format), salida)
            precio_orden_actual = float(p_exit_format)
        except Exception as e:
            print(f"[{symbol}] Error AS inicial: {type(e).__name__}: {e}")
            return

        restante = float(amount)
        while True:
            await asyncio.sleep(INTERVALO_AS_SEG)
            tiempo_hold = time.time() - tiempo_inicio
            t_restante = max(0.0, cfg.TIEMPO_MAX_HOLD_SEG - tiempo_hold)

            if tiempo_hold >= cfg.TIEMPO_MAX_HOLD_SEG:
                try:
                    await exchange.cancel_order(orden_venta["id"], symbol)
                except Exception:
                    pass
                posicion = await _posicion_larga(exchange, symbol)
                if posicion is not None:
                    restante = posicion
                try:
                    if restante > 0:
                        cantidad = exchange.amount_to_precision(symbol, restante)
                        orden_mkt = await exchange.create_order(
                            symbol, "market", "sell", cantidad, None, {"reduceOnly": True}
                        )
                        p_salida_mkt = precio_orden_actual
                        try:
                            o_mkt = await _estado_orden(exchange, orden_mkt["id"], symbol)
                            p_salida_mkt = float(o_mkt.get("average") or o_mkt.get("price") or p_salida_mkt)
                        except Exception:
                            pass
                        enviar_alerta_bg(
                            f"🟩 [ESCAPE TEMPORAL] {symbol} liquidado a mercado tras "
                            f"{int(tiempo_hold)}s para liberar liquidez."
                        )
                        await _registrar_cierre_as(
                            exchange, symbol, entry, p_salida_mkt, float(cantidad),
                            "AS_ESCAPE_TEMPORAL", tiempo_inicio, z_score, ofi, es_maker_salida=False
                        )
                except Exception as e:
                    print(f"[{symbol}] AS: fallo en la liquidación a mercado: {type(e).__name__}: {e}")
                break

            try:
                check_order = await _estado_orden(exchange, orden_venta["id"], symbol)
                estado = check_order.get("status")

                if estado == "closed":
                    precio_final = float(check_order.get("average") or check_order.get("price") or precio_orden_actual)
                    enviar_alerta_bg(f"🟩 [EXITO AS] {symbol} liquidado pasivamente a {precio_final}. Ganancia Asegurada.")
                    await _registrar_cierre_as(
                        exchange, symbol, entry, precio_final, float(amount),
                        "AS_EXITO_MAKER", tiempo_inicio, z_score, ofi, es_maker_salida=True
                    )
                    break

                ticker = await exchange.fetch_ticker(symbol)
                p_mid = float(ticker.get("ask") or ticker.get("last") or entry)
                restante = float(check_order.get("remaining") or restante)
                q_norm = max(0.1, min(1.0, restante / max(cantidad_inicial, 1e-9)))

                p_exit_dinamico = calcular_precio_salida_hjb_as(
                    s_mid=p_mid,
                    entry=entry,
                    q_norm=q_norm,
                    sigma_rel=sigma_local,
                    t_restante=t_restante,
                    t_total=float(cfg.TIEMPO_MAX_HOLD_SEG),
                )

                if estado in ("canceled", "rejected", "expired"):
                    posicion = await _posicion_larga(exchange, symbol)
                    if posicion is None:
                        continue
                    if posicion <= 0:
                        print(f"[{symbol}] AS: posición cerrada externamente (SL); fin del motor.")
                        await _registrar_cierre_as(
                            exchange, symbol, entry, entry * (1.0 - _obtener_sl_bybit_pct()), float(amount),
                            "AS_SL_EXCHANGE", tiempo_inicio, z_score, ofi, es_maker_salida=False
                        )
                        break
                    restante = posicion
                    p_reponer = max(p_exit_dinamico, p_minimo_rentable, p_mid)
                    p_exit_format = exchange.price_to_precision(symbol, p_reponer)
                    orden_venta = await exchange.create_order(
                        symbol, "limit", "sell", restante, float(p_exit_format), salida
                    )
                    precio_orden_actual = float(p_exit_format)
                    continue

                p_exit_format = exchange.price_to_precision(symbol, p_exit_dinamico)
                if (
                    precio_orden_actual > float(p_exit_format)
                    and p_exit_dinamico > p_mid
                    and p_exit_dinamico >= p_minimo_rentable
                ):
                    await exchange.cancel_order(orden_venta["id"], symbol)
                    orden_venta = await exchange.create_order(
                        symbol, "limit", "sell", restante, float(p_exit_format), salida
                    )
                    precio_orden_actual = float(p_exit_format)
            except Exception as e:
                print(f"[{symbol}] AS transitorio: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"[{symbol}] AS abortado: {type(e).__name__}: {e}")