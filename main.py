"""Cerebro Central SPLIT-BRAIN HFT (V6.4.0 Zero-Zombie & Maker-First Unwind - Tokio asia-northeast1)."""
import asyncio
import importlib
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform != "win32":
    try:
        import uvloop
        uvloop.install()
    except Exception:
        pass

import ccxt.pro as ccxt
import config_parametros as cfg

_RAIZ = Path(__file__).resolve().parent
LOG_FILE = _RAIZ / (".test_bot_hft.log" if os.environ.get("SPLITBRAIN_TEST_MODE") == "1" else ('.test_bot_hft.log' if __import__('os').environ.get('SPLITBRAIN_TEST_MODE') == '1' else 'bot_hft.log'))
LOCK_FILE = _RAIZ / ".splitbrain.lock"
_lock_file_handle = None
_lock_adquirido_por_mi: bool = False

_TZ_CHILE = timezone(timedelta(hours=-3))


def _sello_tiempo() -> str:
    """Genera el prefijo cronológico exacto con precisión de milisegundos (Chile y UTC)."""
    ahora_utc = datetime.now(timezone.utc)
    ahora_cl = ahora_utc.astimezone(_TZ_CHILE)
    ms = ahora_utc.microsecond // 1000
    return f"[{ahora_cl.strftime('%Y-%m-%d %H:%M:%S')}.{ms:03d} CL | {ahora_utc.strftime('%H:%M:%S')} UTC] "


def adquirir_bloqueo_instancia_unica() -> None:
    """Garantiza la ejecución de exactamente una instancia física de Split-Brain (Cross-Platform)."""
    global _lock_file_handle, _lock_adquirido_por_mi
    try:
        _lock_file_handle = LOCK_FILE.open("a+", encoding="utf-8")
        if sys.platform != "win32":
            import fcntl
            fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            _lock_file_handle.seek(0)
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)

        _lock_file_handle.seek(0)
        _lock_file_handle.truncate()
        _lock_file_handle.write(f"{os.getpid()}\n")
        _lock_file_handle.flush()
        _lock_adquirido_por_mi = True
    except (BlockingIOError, PermissionError, OSError):
        _lock_adquirido_por_mi = False
        if _lock_file_handle:
            try:
                _lock_file_handle.close()
            except Exception:
                pass
            _lock_file_handle = None
        print(f"❌ [CRÍTICO] Ya existe una instancia activa de SPLITBRAIN ejecutándose (PID Lock en {LOCK_FILE.name}).")
        print("❌ [CRÍTICO] Abortando este proceso inmediatamente para evitar dobles disparos y colapso de margen.")
        sys.exit(1)


def liberar_bloqueo_instancia_unica() -> None:
    """Libera el cerrojo y elimina el archivo SOLO si este proceso adquirió legítimamente el lock."""
    global _lock_file_handle, _lock_adquirido_por_mi
    if not _lock_adquirido_por_mi:
        return
    if _lock_file_handle:
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(_lock_file_handle.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt
                _lock_file_handle.seek(0)
                msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            _lock_file_handle.close()
        except Exception:
            pass
        _lock_file_handle = None
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except Exception:
        pass
    _lock_adquirido_por_mi = False


class _TeeLogger:
    """Duplica toda salida (stdout/stderr) hacia bot_hft.log inyectando fecha y hora en cada línea."""
    __slots__ = ("_stream", "_archivo", "_inicio_linea")

    def __init__(self, stream, archivo):
        self._stream = stream
        self._archivo = archivo
        self._inicio_linea = True

    def _formatear_con_hora(self, data: str) -> str:
        partes = data.splitlines(keepends=True)
        if not partes:
            return data
        prefijo = _sello_tiempo()
        salida = []
        for fragmento in partes:
            if self._inicio_linea and fragmento not in ("\n", "\r\n", "\r"):
                salida.append(prefijo + fragmento)
            else:
                salida.append(fragmento)
            self._inicio_linea = fragmento.endswith(("\n", "\r"))
        return "".join(salida)

    def write(self, data: str) -> int:
        if not data:
            return 0
        texto_sellado = self._formatear_con_hora(data)
        try:
            self._stream.write(texto_sellado)
            self._stream.flush()
        except Exception:
            pass
        try:
            self._archivo.write(texto_sellado)
            self._archivo.flush()
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._archivo.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return getattr(self._stream, "isatty", lambda: False)()

    def __getattr__(self, name):
        return getattr(self._stream, name)


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_log_handle = LOG_FILE.open("a", encoding="utf-8", buffering=1, errors="replace")
sys.stdout = _TeeLogger(sys.stdout, _log_handle)
sys.stderr = _TeeLogger(sys.stderr, _log_handle)

radar = importlib.import_module("1_radar_volatilidad")
vigilar_mercado = radar.cazar_anomalia_ws
escudo = importlib.import_module("2_escudo_semantico")
sniper = importlib.import_module("3_ejecutor_sniper")
enviar_alerta = importlib.import_module("4_telemetria_telegram").enviar_alerta
gestor_mod = importlib.import_module("5_gestor_posiciones")
stats = importlib.import_module("6_stats_tracker")

REINTENTOS_ARRANQUE = 4
TIMEOUT_EXCHANGE_MS = 20000
RECV_WINDOW_MS = 15000


def _es_perpetuo_cripto_puro(exchange, symbol: str) -> bool:
    """Filtra perpetuos lineales USDT activos Tier-1, excluyendo listas negras y zona 'innovation'."""
    if not symbol.endswith("USDT:USDT"):
        return False
    if symbol in cfg.PROHIBIDAS or symbol in cfg.BLACKLIST_PAIRS:
        return False
    m = exchange.markets.get(symbol)
    if not m or not m.get("active", True):
        return False
    info = m.get("info") or {}
    symbol_type = str(info.get("symbolType") or "").strip().lower()
    tipos_permitidos = ("", "innovation") if cfg.PERMITIR_ZONA_INNOVATION else ("",)
    if symbol_type not in tipos_permitidos:
        return False
    return True


def _es_lote_minimo_viable(exchange, symbol: str, ticker: dict, nocional_kelly_base: float) -> bool:
    """Descarta pares cuyo lote mínimo supere el MAX_MIN_QTY_KELLY_RATIO (60%) del nocional Kelly base."""
    m = exchange.markets.get(symbol)
    if not m:
        return False
    precio = float(ticker.get("last") or ticker.get("bid") or 0.0)
    if precio <= 0:
        return False
    limits_amt = (m.get("limits") or {}).get("amount") or {}
    prec_amt = (m.get("precision") or {}).get("amount") or 0.0
    min_qty = max(float(limits_amt.get("min") or 0.0), float(prec_amt or 0.0))
    costo_lote_minimo = min_qty * precio
    umbral_max = max(5.2, nocional_kelly_base * float(getattr(cfg, "MAX_MIN_QTY_KELLY_RATIO", 0.60)))
    return costo_lote_minimo <= umbral_max


async def _inicializar_mercados_con_backoff(exchange) -> dict:
    ultimo_error = None
    for intento in range(1, REINTENTOS_ARRANQUE + 1):
        try:
            await exchange.load_markets()
            tickers = await exchange.fetch_tickers()
            return tickers
        except Exception as e:
            ultimo_error = e
            espera = min(2 ** intento, 10)
            print(
                f"[MAIN] Aviso de red en arranque (intento {intento}/{REINTENTOS_ARRANQUE}): "
                f"{type(e).__name__}: {e}. Reintentando en {espera}s..."
            )
            await asyncio.sleep(espera)
    raise RuntimeError(f"Fallo crítico inicializando mercados tras {REINTENTOS_ARRANQUE} intentos: {ultimo_error}")


async def _auditar_margen_libre(exchange) -> float:
    """Inspecciona el balance USDT libre vs bloqueado, fija caché RAM y devuelve el nocional Kelly."""
    if cfg.MODO_PAPER:
        libre = await sniper.sincronizar_saldo_ram(exchange)
        return libre * cfg.KELLY_MAX_PCT * cfg.APALANCAMIENTO
    try:
        bal = await exchange.fetch_balance()
        usdt = bal.get("USDT") or {}
        total = float(usdt.get("total") or 0.0)
        libre = float(usdt.get("free") or 0.0)
        sniper._saldo_libre_ram = libre
        sniper._saldo_ts = time.time()
        usado = max(0.0, total - libre)
        margen_disparo = libre * cfg.KELLY_MAX_PCT
        nocional_kelly = max(total, libre) * cfg.KELLY_MAX_PCT * cfg.APALANCAMIENTO
        estado = "LISTO PARA DISPARAR" if margen_disparo >= cfg.MIN_INVERSION_USDT else "MUNICIÓN BAJA (< 2.0 USDT)"
        print(
            f"[MAIN] Auditoría UTA USDT -> Total: {total:.4f} | Libre RAM: {libre:.4f} | "
            f"Bloqueado: {usado:.4f} | Margen Kelly Imperial (58%): {margen_disparo:.2f} USDT "
            f"(Nocional 3x: {nocional_kelly:.2f} USDT) [{estado}]"
        )
        return nocional_kelly
    except Exception as e:
        print(f"[MAIN] Aviso leyendo margen libre: {type(e).__name__}: {e}")
        return float(cfg.CAPITAL_BASE_USDT) * cfg.KELLY_MAX_PCT * cfg.APALANCAMIENTO


async def main():
    adquirir_bloqueo_instancia_unica()
    loop_name = type(asyncio.get_running_loop()).__name__
    print("==================================================================")
    print("INICIANDO CEREBRO CENTRAL SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)")
    print("==================================================================")
    print(f"[MAIN] Bloqueo de instancia física adquirido exitosamente (PID {os.getpid()} | Loop: {loop_name}).")
    print(f"[MAIN] Telemetría local con sellado de tiempo activa en: {LOG_FILE.name}")

    # V6.4.0: Configuración nativa `streaming` de ccxt.pro para erradicar ping-pong RequestTimeout
    exchange = ccxt.bybit({
        "apiKey": cfg.BYBIT_API_KEY,
        "secret": cfg.BYBIT_API_SECRET,
        "timeout": TIMEOUT_EXCHANGE_MS,
        "enableRateLimit": True,
        "streaming": {
            "keepAlive": cfg.WS_KEEPALIVE_MS,
            "maxPingPongMisses": cfg.WS_MAX_PING_MISSES,
        },
        "options": {
            "defaultType": "swap",
            "recvWindow": RECV_WINDOW_MS,
            "adjustForTimeDifference": True,
            "ws": {
                "keepAlive": cfg.WS_KEEPALIVE_MS,
            },
        },
    })

    tareas: list[asyncio.Task] = []
    try:
        tickers = await _inicializar_mercados_con_backoff(exchange)

        # 0. Desduplicar JSONL, reconciliar huérfanas y auditar margen ANTES de elegir las 15 presas
        stats.tracker.cargar()
        await gestor_mod.gestor.reconciliar_posiciones_exchange(exchange)
        nocional_kelly_base = await _auditar_margen_libre(exchange)

        candidatos_puros = [
            t for sym, t in tickers.items()
            if _es_perpetuo_cripto_puro(exchange, sym)
        ]
        candidatos_puros.sort(key=lambda t: float(t.get("quoteVolume") or 0.0), reverse=True)

        presas: list[str] = []
        excluidos_lote: list[str] = []
        for t in candidatos_puros:
            sym = t["symbol"]
            if _es_lote_minimo_viable(exchange, sym, t, nocional_kelly_base):
                presas.append(sym)
                if len(presas) >= cfg.TOP_N:
                    break
            else:
                excluidos_lote.append(sym)

        if excluidos_lote:
            print(
                f"[MAIN] Filtro de lote mínimo V7.0.0 excluyó pares indivisibles: "
                f"{', '.join(excluidos_lote[:6])}"
            )

        print(
            f"[MAIN] Modo Paper: {cfg.MODO_PAPER} | Motor Salida: {cfg.MOTOR_SALIDA}+MAKER_PFQI | "
            f"OFI Min: {cfg.UMBRAL_OFI_TOXICO:+.2f} | Hold Máx: {cfg.TIEMPO_MAX_HOLD_SEG}s "
            f"(SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)) | Hawkes 0.55s | HJB +0.22%..+0.72% | Kelly 45%-65%) | Dual-LPU | Piso Maker +0.22%) | "
            f"BE: +{cfg.BE_ACTIVACION_PCT:.2%} | Trail: +{cfg.TRAIL_ACTIVACION_PCT:.2%}"
        )
        print(f"[MAIN] Radar fijado en {len(presas)} pares cripto-nativos Tier-1 viables.")
        # Ley V (Emperor's Time v7.0): Forzar Nucleo Alfa (WAL) y purgar drenadores OOS (PUMPFUN)
        _vetados_v7 = getattr(cfg, 'PARES_VETADOS_OOS_V7', frozenset({'PUMPFUN/USDT:USDT', 'TRUTH/USDT:USDT'}))
        _nucleo_v7 = getattr(cfg, 'NUCLEO_ALFA_V7', ())
        _indiv = set(indivisibles) if 'indivisibles' in locals() else set()
        _pool_limpio = [s for s in presas if s not in _vetados_v7 and s not in _indiv]
        for _pref in reversed(_nucleo_v7):
            if _pref not in _indiv and _pref not in _vetados_v7:
                if _pref in _pool_limpio:
                    _pool_limpio.remove(_pref)
                _pool_limpio.insert(0, _pref)
        presas[:] = [s for s in _nucleo_v7 if s not in _indiv and s not in _vetados_v7]
        print(f"[MAIN] Presas activas: {', '.join(presas)}")

        await sniper.precalentar_apalancamiento_universo(exchange, presas)
        escudo.centinela.inicializar_universo(presas)

        bal_real_init = await stats.leer_balance_real(exchange)
        if not gestor_mod.gestor.posiciones and bal_real_init is not None:
            stats.tracker.sincronizar_capital_base_con_real(bal_real_init)
        stats.tracker.imprimir_dashboard(bal_real_init)
        sniper.enviar_alerta_bg("🟩 Motor SPLITBRAIN-Omega V7.1.1 (Emperor's Time) Inicializado.")

        # 1. Tarea de Circuit Breaker Global BTC con Evacuación Activa
        tareas.append(asyncio.create_task(radar.cazar_cisne_negro(exchange)))

        # 2. Tarea Demonio Centinela Groq RAM con Rotación Multi-Modelo en Background
        tareas.append(asyncio.create_task(escudo.centinela.bucle_centinela_background(cfg.GROQ_CICLO_SEG)))

        # 3. Tarea Warm-Socket Keep-Alive (mantiene sesión TLS caliente en asia-northeast1)
        tareas.append(asyncio.create_task(sniper.bucle_keepalive_socket(exchange)))

        # 4. Tareas del Radar para cada par seleccionado
        for symbol in presas:
            tareas.append(asyncio.create_task(vigilar_mercado(exchange, symbol)))

        # 5. Tarea Periódica del Dashboard de la Caja Negra
        tareas.append(asyncio.create_task(stats.bucle_dashboard(exchange, cfg.DASHBOARD_INTERVALO_SEG)))

        while True:
            await asyncio.sleep(1)
    finally:
        todas_las_tareas = (
            list(tareas)
            + list(gestor_mod.gestor._tareas)
            + list(sniper._tareas_as)
            + list(sniper._tareas_bg)
        )
        for t in todas_las_tareas:
            if not t.done():
                t.cancel()
        if todas_las_tareas:
            await asyncio.gather(*todas_las_tareas, return_exceptions=True)
        await exchange.close()
        liberar_bloqueo_instancia_unica()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[MAIN] Apagado manual completado de forma segura.")
    finally:
        liberar_bloqueo_instancia_unica()
        try:
            _log_handle.flush()
            _log_handle.close()
        except Exception:
            pass

# ==============================================================================
# LOGGER_HISTORICO_PERPETUO_V755: ESCRITURA APPEND EN bot_hft_h.log (0% TESTS)
# ==============================================================================
import builtins as _b_hist_v755
import os as _os_hist_v755
import pathlib as _pathlib_hist_v755
import re as _re_hist_v755
from datetime import datetime as _dt_hist_v755, timezone as _tz_hist_v755, timedelta as _td_hist_v755

_RUTA_HIST_LOG_V755 = _pathlib_hist_v755.Path(__file__).resolve().parent / "bot_hft_h.log"
_PATRON_TEST_V755 = _re_hist_v755.compile(
    r"(\[(?:1|2|3|4|5|6|7|8|9|10|11|12|13|14|15)/15\]|SUITE DE PRUEBAS|PRUEBAS DE INTEGRIDAD|"
    r"zero_rest_order_1|\.test_stats_v6|\.splitbrain_test\.lock|14400s)"
)
_orig_print_hist_v755 = _b_hist_v755.print

def _print_con_historial_v755(*args, **kwargs):
    res = _orig_print_hist_v755(*args, **kwargs)
    if _os_hist_v755.environ.get("SPLITBRAIN_TEST_MODE") != "1" and args:
        try:
            texto = " ".join(str(a) for a in args)
            if not _PATRON_TEST_V755.search(texto):
                if not texto.startswith("[202"):
                    now_utc = _dt_hist_v755.now(_tz_hist_v755.utc)
                    now_cl = now_utc - _td_hist_v755(hours=3)
                    sello = f"[{now_cl.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} CL | {now_utc.strftime('%H:%M:%S')} UTC] "
                    linea_out = sello + texto + "\n"
                else:
                    linea_out = texto + ("\n" if not texto.endswith("\n") else "")
                with open(_RUTA_HIST_LOG_V755, "a", encoding="utf-8") as fh:
                    fh.write(linea_out)
        except Exception:
            pass
    return res

_b_hist_v755.print = _print_con_historial_v755

# ==============================================================================
# _OBSERVADOR_TELEGRAM_V782: PARIDAD 1:1 APERTURA CONFIRMADA Y CIERRES (🟩 / 🔴)
# ==============================================================================
import asyncio as _asyncio_m_v782
import builtins as _builtins_m_v782
import os as _os_m_v782
import re as _re_m_v782
import time as _time_m_v782

_NL_M_V782 = chr(10)
_RE_ORD_V782 = _re_m_v782.compile(
    r"\[FRANCOTIRADOR\]\s+Orden\s+([a-fA-F0-9-]+)\s+en\s+([A-Z0-9]+/USDT:USDT):\s+[0-9.]+\s+@\s+([0-9.]+)"
)
_RE_ACT_V782 = _re_m_v782.compile(
    r"\[(?:FRANCOTIRADOR|SHORT-SNIPER)\]\s+🎯\s+Posición(?:\s+SHORT)?\s+activa\s+en\s+([A-Z0-9]+/USDT:USDT):\s+[0-9.]+\s+@\s+([0-9.]+)"
)
_RE_NO_FILL_V782 = _re_m_v782.compile(
    r"\[(?:FRANCOTIRADOR|SHORT-SNIPER)\].*?en\s+([A-Z0-9]+/USDT:USDT)\s+no\s+llenada"
)
_RE_CLOSE_V782 = _re_m_v782.compile(
    r"(🟩|🔴|🟥)\s*CIERRE(?:\s+SHORT)?\s+([A-Z0-9]+/USDT:USDT)\s+\[([^\]]+)\].*?\|\s*([+-]?[0-9]+\.[0-9]+)\s*%\s*\(([+-]?[0-9]+\.[0-9]+)\s*USDT\)\s*en\s*([0-9.]+)s"
)

_prev_print_m_v782 = _builtins_m_v782.print


def _print_observer_v782(*args, **kwargs):
    res = _prev_print_m_v782(*args, **kwargs)
    if args and _os_m_v782.environ.get("SPLITBRAIN_TEST_MODE") != "1":
        try:
            msg = " ".join(str(a) for a in args)

            # 1. Guardar OID cuando se coloca la orden Limit PostOnly
            m_o = _RE_ORD_V782.search(msg)
            if m_o:
                oid, sym_o, pr_o = m_o.group(1), m_o.group(2), m_o.group(3)
                getattr(_builtins_m_v782, "_TG_LAST_OID_V782", {})[sym_o] = (oid, pr_o)

            # 2. Si expiro sin llenarse en 10s, descartar apertura pendiente silenciosamente
            m_nf = _RE_NO_FILL_V782.search(msg)
            if m_nf:
                getattr(_builtins_m_v782, "_TG_PENDING_OPEN_V782", {}).pop(m_nf.group(1), None)

            # 3. Al confirmarse '🎯 Posición activa', emitir la apertura retenida (1:1)
            m_act = _RE_ACT_V782.search(msg)
            if m_act:
                sym_a, pr_a = m_act.group(1), m_act.group(2)
                pend = getattr(_builtins_m_v782, "_TG_PENDING_OPEN_V782", {}).pop(sym_a, None)
                fn_http = getattr(_builtins_m_v782, "_despachar_http_directo_telegram_v782", None)
                if callable(fn_http):
                    if pend and isinstance(pend, dict) and pend.get("msg"):
                        texto_open = pend["msg"]
                    else:
                        oid_a, _ = getattr(_builtins_m_v782, "_TG_LAST_OID_V782", {}).get(sym_a, ("N/A", pr_a))
                        texto_open = (
                            f"🟩 ALERTA VERDE (PROD):{_NL_M_V782}"
                            f"Orden Limit PostOnly en {sym_a}{_NL_M_V782}"
                            f"Precio: {pr_a}{_NL_M_V782}"
                            f"ID: {oid_a}{_NL_M_V782}"
                            f"⚙️ Motor SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)"
                        )
                    loop = _asyncio_m_v782.get_running_loop()
                    loop.create_task(fn_http(texto_open))

            # 4. Cierre en Ganancia (🟩) o Perdida (🔴) detectado en log -> despacho garantizado con dedup 12s
            m_c = _RE_CLOSE_V782.search(msg)
            if m_c:
                sym_c = m_c.group(2)
                mot_c = m_c.group(3)
                pct_c = float(m_c.group(4))
                usdt_c = float(m_c.group(5))
                hold_c = float(m_c.group(6))
                side_c = "short" if "SHORT" in msg.upper() else "long"
                fn_close = getattr(_builtins_m_v782, "_despachar_cierre_garantizado_v782", None)
                if callable(fn_close):
                    loop = _asyncio_m_v782.get_running_loop()
                    loop.create_task(fn_close(sym_c, pct_c, usdt_c, hold_c, mot_c, 0.0, 0.0, side_c))
        except Exception:
            pass
    return res


if getattr(_builtins_m_v782.print, "__name__", "") != "_print_observer_v782":
    _builtins_m_v782.print = _print_observer_v782
