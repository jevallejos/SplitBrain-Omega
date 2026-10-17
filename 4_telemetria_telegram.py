import aiohttp
import config_parametros as cfg

# Filtro institucional: solo se transmiten victorias, nunca ruido rojo.
PALABRAS_SILENCIADAS = ['🔴', 'ERROR', 'denegada', 'abortó', 'BLOQUEO', 'COLAPSÓ',
                        'encasquillada', 'ignorada', 'TÓXICO', '⚠️', 'ZOMBIE']


async def enviar_alerta(mensaje: str):
    # Lista blanca V6.0: solo los mensajes verdes llegan al operador.
    if "🟩" not in str(mensaje):
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
                "Motor SPLITBRAIN-Omega V7.5.2 (Emperor Time - Tri-Cortex Ensemble)",
            )
        return await _orig_enviar_tg_v7(mensaje, *args, **kwargs)
except NameError:
    pass
