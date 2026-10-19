import aiohttp
import config_parametros as cfg

# Filtro institucional: solo se transmiten victorias, nunca ruido rojo.
PALABRAS_SILENCIADAS = ['🔴', 'ERROR', 'denegada', 'abortó', 'BLOQUEO', 'COLAPSÓ',
                        'encasquillada', 'ignorada', 'TÓXICO', '⚠️', 'ZOMBIE']


async def enviar_alerta(mensaje: str):
    # Lista blanca V6.0: solo los mensajes verdes llegan al operador.
    if ("🟩" not in mensaje and "🔴 [CIERRE" not in mensaje) and "🟩" not in str(mensaje):
        return

    if any(p in str(mensaje) for p in PALABRAS_SILENCIADAS):
        return

    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": cfg.TELEGRAM_CHAT_ID, "text": str(mensaje)}
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                await resp.read()
    except Exception:
        # Telegram caído o lento: jamás bloquear el event loop.
        pass

# NORMALIZADOR_TELEGRAM_V7_1_1: Garantiza firma oficial en todo despacho Telegram
try:
    _orig_enviar_tg_v7 = enviar_alerta_telegram
    async def enviar_alerta_telegram(mensaje: str, *args, **kwargs):
        if isinstance(mensaje, str):
            mensaje = mensaje.replace(
                "Motor SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)",
                "Motor SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)",
            )
        return await _orig_enviar_tg_v7(mensaje, *args, **kwargs)
except NameError:
    pass

# ==============================================================================
# PROTOCOLO_TELEGRAM_V782: SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)
# Apareamiento 1:1 + Cobertura Garantizada de Cierres en Perdida (🔴) y Ganancia (🟩)
# ==============================================================================
import asyncio as _asyncio_tg_v782
import builtins as _builtins_tg_v782
import os as _os_tg_v782
import pathlib as _pathlib_tg_v782
import re as _re_tg_v782
import time as _time_tg_v782
import aiohttp as _aiohttp_tg_v782

try:
    import dotenv as _dotenv_tg_v782
    _dotenv_tg_v782.load_dotenv(_pathlib_tg_v782.Path(__file__).resolve().parent / ".env")
except Exception:
    pass

_NL_V782 = chr(10)
_FULL_TITLE_V782 = "SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)"
_TITULO_CANONICO_TG_V782 = "Motor SPLITBRAIN-OMEGA V7.8.2 (EMPEROR TIME - GODSPEED FINAL)"
_builtins_tg_v782._TG_PENDING_OPEN_V782 = getattr(_builtins_tg_v782, "_TG_PENDING_OPEN_V782", {})
_builtins_tg_v782._TG_DEDUP_CACHE_V782 = getattr(_builtins_tg_v782, "_TG_DEDUP_CACHE_V782", {})
_builtins_tg_v782._TG_LAST_OID_V782 = getattr(_builtins_tg_v782, "_TG_LAST_OID_V782", {})


def _normalizar_firma_v782(texto: str) -> str:
    if not isinstance(texto, str):
        return str(texto)
    texto = _re_tg_v782.sub(
        r"Motor\s+SPLIT[-]?BRAIN(?:-Omega|-OMEGA)?\s*V[67]\.[0-9.]+\s*\([^\"'\r\n\)]*\)",
        _TITULO_CANONICO_TG_V782,
        texto,
    )
    texto = _re_tg_v782.sub(
        r"SPLITBRAIN(?:-Omega|-OMEGA)\s+V7\.[0-9.]+\s*\([^\"'\r\n\)]*\)",
        _FULL_TITLE_V782,
        texto,
    )
    return texto


def _es_mensaje_cierre_v782(texto: str) -> bool:
    t = str(texto or "")
    u = t.upper()
    if "ESCAPE_HUERFANA_BOOT" in u or "14400S" in u or "ZERO_REST_ORDER" in u:
        return False
    patrones_cierre = (
        "[CIERRE",
        "🔴",
        "🟥",
        "CIERRE SL",
        "CIERRE TP",
        "CIERRE SHORT",
        "STOP_LOSS",
        "SL_PROTECTION",
        "SL_INTERNO",
        "SL_BYBIT",
        "ESCAPE_TEMPORAL",
        "MAKER_UNWIND",
        "SHORT_SL",
        "SHORT_ESCAPE",
        "SHORT_MAKER",
        "USDT) EN ",
    )
    if any(p in u or p in t for p in patrones_cierre):
        return True
    if "🟩" in t and "ALERTA VERDE" not in u and ("CIERRE" in u or "USDT" in u):
        return True
    return False


def _es_ruido_bloqueado_v782(texto: str) -> bool:
    t = str(texto or "")
    u = t.upper()
    if "ESCAPE_HUERFANA_BOOT" in u or "14400S" in u or "ZERO_REST_ORDER" in u:
        return True
    if _es_mensaje_cierre_v782(t):
        return False
    patrones_ruido = (
        "[FILTRO",
        "[MICROESTRUCTURA]",
        "REPULSOR LYAPUNOV",
        "MEMORIA HAWKES",
        "FRICCIÓN SPREAD",
        "FRICCION SPREAD",
        "ANTI-PUMP-TOP",
        "ANCLAJE CAGINALP",
        "FLUJO TÓXICO",
        "FLUJO TOXICO",
        "DISPARO ABORTADO",
        "PAR VETADO",
        "NO LLENADA",
        "MARGEN LIBERADO",
        "ORDEN CANCELADA",
        "TEST_MODE",
        "MODO PAPER: TRUE",
    )
    return any(r in u for r in patrones_ruido)


def _extraer_simbolo_v782(texto: str) -> str:
    m = _re_tg_v782.search(r"([A-Z0-9]+/USDT:USDT)", str(texto or ""))
    if m:
        return m.group(1)
    m2 = _re_tg_v782.search(r"\b(ZEREBRO|WAL|INJ|XRP|AVAX|TAO|ARB|SUI|SOL|DOGE|PEPE|WIF)\b", str(texto or ""))
    return f"{m2.group(1)}/USDT:USDT" if m2 else "GLOBAL"


def _verificar_dedup_v782(clave: str, ttl_seg: float = 12.0) -> bool:
    ahora = _time_tg_v782.time()
    cache = _builtins_tg_v782._TG_DEDUP_CACHE_V782
    for k in list(cache.keys()):
        if ahora - cache[k] > ttl_seg:
            cache.pop(k, None)
    if clave in cache:
        return False
    cache[clave] = ahora
    return True


def _obtener_credenciales_tg_v782() -> tuple[str, str]:
    token = (
        _os_tg_v782.environ.get("TELEGRAM_BOT_TOKEN")
        or _os_tg_v782.environ.get("TELEGRAM_TOKEN")
        or ""
    ).strip()
    chat_id = (
        _os_tg_v782.environ.get("TELEGRAM_CHAT_ID")
        or _os_tg_v782.environ.get("CHAT_ID")
        or ""
    ).strip()
    if not token or not chat_id:
        g = globals()
        cfg_mod = g.get("cfg")
        if cfg_mod is not None:
            token = token or str(getattr(cfg_mod, "TELEGRAM_BOT_TOKEN", getattr(cfg_mod, "TELEGRAM_TOKEN", "")) or "").strip()
            chat_id = chat_id or str(getattr(cfg_mod, "TELEGRAM_CHAT_ID", getattr(cfg_mod, "CHAT_ID", "")) or "").strip()
        token = token or str(g.get("TELEGRAM_BOT_TOKEN", g.get("TELEGRAM_TOKEN", "")) or "").strip()
        chat_id = chat_id or str(g.get("TELEGRAM_CHAT_ID", g.get("CHAT_ID", "")) or "").strip()
    return token, chat_id


async def _despachar_http_directo_telegram_v782(texto: str) -> bool:
    if _os_tg_v782.environ.get("SPLITBRAIN_TEST_MODE") == "1":
        return False
    token, chat_id = _obtener_credenciales_tg_v782()
    texto_final = _normalizar_firma_v782(str(texto or "").strip())
    if not texto_final:
        return False
    if _FULL_TITLE_V782 not in texto_final and ("ALERTA VERDE" in texto_final or "[CIERRE" in texto_final):
        texto_final = texto_final + _NL_V782 + "⚙️ " + _TITULO_CANONICO_TG_V782

    if not token or not chat_id:
        orig_fn = getattr(_builtins_tg_v782, "_ORIG_ENVIAR_ALERTA_V782", None)
        if callable(orig_fn):
            try:
                await orig_fn(texto_final)
                return True
            except Exception:
                return False
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    timeout = _aiohttp_tg_v782.ClientTimeout(total=8.0)
    try:
        async with _aiohttp_tg_v782.ClientSession(timeout=timeout) as session:
            payload = {"chat_id": chat_id, "text": texto_final}
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
    except Exception:
        return False


async def despachar_cierre_garantizado_v782(
    symbol: str,
    pnl_pct: float,
    pnl_usdt: float,
    hold_seg: float,
    motivo: str = "CIERRE",
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    side: str = "long",
) -> bool:
    if _os_tg_v782.environ.get("SPLITBRAIN_TEST_MODE") == "1":
        return False
    mot_str = str(motivo or "CIERRE").strip()
    if mot_str == "ESCAPE_HUERFANA_BOOT" or float(hold_seg or 0.0) >= 14000.0:
        return False
    sym = str(symbol or "ACTIVO").strip()
    if not _verificar_dedup_v782(f"CLOSE:{sym}", ttl_seg=12.0):
        return False

    es_ganancia = float(pnl_usdt) >= 0.0
    icono = "🟩" if es_ganancia else "🔴"
    lado_tag = " SHORT" if str(side).lower() == "short" else ""
    linea1 = f"{icono} [CIERRE{lado_tag} {mot_str}] {sym}"
    linea2 = f"{float(pnl_pct):+.2f} % ({float(pnl_usdt):+.4f} USDT) en {float(hold_seg):.0f}s"
    if entry_price and exit_price:
        linea2 += f" ({entry_price} -> {exit_price})"
    linea3 = f"⚙️ {_TITULO_CANONICO_TG_V782}"
    mensaje = linea1 + _NL_V782 + linea2 + _NL_V782 + linea3
    return await _despachar_http_directo_telegram_v782(mensaje)


_builtins_tg_v782._despachar_cierre_garantizado_v782 = despachar_cierre_garantizado_v782
_builtins_tg_v782._despachar_http_directo_telegram_v782 = _despachar_http_directo_telegram_v782
_builtins_tg_v782._es_mensaje_cierre_v782 = _es_mensaje_cierre_v782
_builtins_tg_v782._es_ruido_bloqueado_v782 = _es_ruido_bloqueado_v782

# Envolver funciones de nivel de modulo en 4_telemetria_telegram.py (ej. enviar_alerta)
for _fn_tg_name in ("enviar_alerta", "enviar_alerta_telegram", "enviar_mensaje", "notificar"):
    if _fn_tg_name in globals() and callable(globals()[_fn_tg_name]):
        _orig_fn_ref = globals()[_fn_tg_name]
        if not hasattr(_builtins_tg_v782, "_ORIG_ENVIAR_ALERTA_V782"):
            _builtins_tg_v782._ORIG_ENVIAR_ALERTA_V782 = _orig_fn_ref

        async def _wrapped_fn_tg_v782(mensaje: str, *args, __orig=_orig_fn_ref, **kwargs):
            if _os_tg_v782.environ.get("SPLITBRAIN_TEST_MODE") == "1":
                return None
            confirmado = bool(kwargs.pop("_confirmado_fill_v782", False))
            msg_norm = _normalizar_firma_v782(str(mensaje or "").strip())
            if not msg_norm or _es_ruido_bloqueado_v782(msg_norm):
                return None

            sym = _extraer_simbolo_v782(msg_norm)

            # Retener orden limite inicial hasta confirmar '🎯 Posición activa' en Bybit
            if ("ALERTA VERDE" in msg_norm.upper() and "ORDEN LIMIT" in msg_norm.upper()) and not confirmado and not _es_mensaje_cierre_v782(msg_norm):
                _builtins_tg_v782._TG_PENDING_OPEN_V782[sym] = {
                    "msg": msg_norm,
                    "ts": _time_tg_v782.time(),
                }
                return None

            if _es_mensaje_cierre_v782(msg_norm):
                if not _verificar_dedup_v782(f"CLOSE:{sym}", ttl_seg=12.0):
                    return None
                return await _despachar_http_directo_telegram_v782(msg_norm)

            if "ALERTA VERDE" in msg_norm.upper():
                if not _verificar_dedup_v782(f"OPEN:{sym}", ttl_seg=12.0):
                    return None
                return await _despachar_http_directo_telegram_v782(msg_norm)

            return await _despachar_http_directo_telegram_v782(msg_norm)

        globals()[_fn_tg_name] = _wrapped_fn_tg_v782
