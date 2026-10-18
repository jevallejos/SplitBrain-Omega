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
                "Motor SPLITBRAIN V6.4.0 (Zero-Zombie & Maker-First Unwind)",
                "Motor SPLITBRAIN-Omega V7.7.2 (Emperor Time - Tri-Cortex Ensemble)",
            )
        return await _orig_enviar_tg_v7(mensaje, *args, **kwargs)
except NameError:
    pass

# ==============================================================================
# FILTRO_ESTRICTO_TELEGRAM_V773: SOLO APERTURA + CIERRE VERDE (🟩) + CIERRE ROJO (🔴)
# Bloquea 100% de transacciones abortadas, filtros de microestructura y tests.
# ==============================================================================
import builtins as _b_tg_v773
import os as _os_tg_v773
import time as _time_tg_v773

if not hasattr(_b_tg_v773, "_TG_SENT_CACHE_V773"):
    _b_tg_v773._TG_SENT_CACHE_V773 = {}


def _es_mensaje_permitido_telegram_v773(texto: str) -> bool:
    if _os_tg_v773.environ.get("SPLITBRAIN_TEST_MODE") == "1":
        return False
    t = str(texto or "").strip()
    if not t:
        return False
    # Bloquear cualquier mensaje de test, orden huerfana de boot o disparo abortado
    prohibidos = (
        "ESCAPE_HUERFANA_BOOT",
        "zero_rest_order",
        "14400s",
        "Disparo abortado",
        "Flujo TÓXICO",
        "REPULSOR LYAPUNOV",
        "MEMORIA HAWKES",
        "FRICCIÓN SPREAD",
        "ANTI-PUMP-TOP",
        "PAR VETADO",
        "no llenada",
        "INICIANDO CEREBRO",
    )
    if any(p in t for p in prohibidos):
        return False
    # Permitir UNICAMENTE: 1) Apertura de orden, 2) Cierre en ganancia (🟩), 3) Cierre en perdida (🔴/🟥)
    es_apertura = ("ALERTA VERDE (PROD)" in t and "Orden Limit PostOnly" in t)
    es_cierre_w = ("🟩" in t and "[CIERRE " in t)
    es_cierre_l = (("🔴" in t or "🟥" in t) and "[CIERRE " in t)
    if not (es_apertura or es_cierre_w or es_cierre_l):
        return False
    ahora = _time_tg_v773.time()
    cache = _b_tg_v773._TG_SENT_CACHE_V773
    if ahora - cache.get(t, 0.0) < 10.0:
        return False
    cache[t] = ahora
    return True
