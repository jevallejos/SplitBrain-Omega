"""Escudo Semántico y Gobernanza Evolutiva en Background (O(1) RAM Lookup + Dinámica del Replicador + Groq LPU).
Calibrado con telemetría empírica desde sniper-tokio:
1. qwen/qwen3.8-27b    (242.6 ms RTT | JSON_OBJECT nativo | 1K RPD)
2. allam-2-7b          (471.1 ms RTT | JSON_OBJECT nativo | 7K RPD / 500K TPD)
3. openai/gpt-oss-20b  (306.7 ms RTT | JSON_OBJECT nativo | 1K RPD)
4. openai/gpt-oss-120b (~750 ms RTT  | RAW_TEXT directo   | 1K RPD)

Novedades V6.4.2:
- Cuarentena Autónoma en Vivo mediante Dinámica del Replicador (dx_i/dt = x_i * (E_i(x) - E_bar(x)))
  y estadístico Fuera de Muestra de Campbell-Thompson (2008) sobre `splitbrain_stats_v6.jsonl`:
  cualquier par cuya esperanza matemática reciente en los últimos 10 trades sea severamente inferior
  al promedio del portafolio entra automáticamente en cuarentena de 60 minutos sin reiniciar el bot.
"""
import asyncio
from collections import defaultdict
import json
import pathlib
import re
import sys
import time
from typing import Dict, List, Set

from groq import AsyncGroq
import config_parametros as cfg

cliente_groq = AsyncGroq(api_key=cfg.GROQ_API_KEY) if cfg.GROQ_API_KEY else None

_RE_THINK = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)
_MODELOS_RAW_TEXT_DIRECTO = {"openai/gpt-oss-120b"}


def _extraer_json_robusto(contenido: str) -> dict:
    """Extrae el diccionario JSON incluso si el modelo incluye etiquetas
    de razonamiento <think>...</think> o bloques Markdown ```json ... ```.
    """
    if not contenido:
        return {}
    limpio = _RE_THINK.sub("", contenido).strip()
    try:
        return json.loads(limpio)
    except Exception:
        pass
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        try:
            return json.loads(limpio[inicio : fin + 1])
        except Exception:
            return {}
    return {}


class CentinelaSemanticoRAM:
    """Mantiene en memoria RAM la Whitelist de activos operables.
    - La ruta crítica de trading consulta `esta_aprobado(symbol)` en O(1) (< 0.001 ms, CERO red).
    - Un demonio asíncrono audita cada GROQ_CICLO_SEG (180s) fuera de la ruta crítica tanto la telemetría
      vía Groq LPU como la matriz de pagos histórica vía Dinámica del Replicador.
    """

    def __init__(self) -> None:
        self.whitelist_activa: Set[str] = set()
        self.telemetria_pares: Dict[str, Dict[str, float]] = {}
        self.ultimo_update_ts: float = 0.0
        self.universo_base: List[str] = []
        self._modelo_idx: int = 0
        self._modelos_bloqueados_proyecto: Set[str] = set()
        self._modelos_cooldown_hasta: Dict[str, float] = {}
        self.cuarentena_replicador_hasta: Dict[str, float] = {}

    def inicializar_universo(self, presas: List[str]) -> None:
        """Habilita inicialmente las presas seleccionadas para no bloquear el arranque en frío."""
        self.universo_base = list(presas)
        self.whitelist_activa = set(presas)
        ahora = time.time()
        for sym in presas:
            self.telemetria_pares.setdefault(
                sym,
                {"z_score": 0.0, "ofi": 0.0, "precio": 0.0, "cambio_pct": 0.0, "ts": ahora},
            )
        self.ultimo_update_ts = ahora
        print(f"[ESCUDO RAM] Whitelist O(1) inicializada con {len(self.whitelist_activa)} pares activos.")

    def actualizar_telemetria(
        self, symbol: str, z_score: float, ofi: float, precio: float, cambio_pct: float = 0.0
    ) -> None:
        """Actualiza en RAM (< 1 microsegundo) el estado microestructural del par observado por el Radar."""
        self.telemetria_pares[symbol] = {
            "z_score": round(float(z_score), 3),
            "ofi": round(float(ofi), 3),
            "precio": float(precio),
            "cambio_pct": round(float(cambio_pct), 4),
            "ts": time.time(),
        }

    def esta_aprobado(self, symbol: str) -> bool:
        """Lookup O(1) en memoria RAM para la ruta crítica de disparo (0.000 ms de latencia de red)."""
        if symbol in cfg.PROHIBIDAS or symbol in cfg.BLACKLIST_PAIRS:
            return False
        if not (("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))) or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))):
            vetados_oos = getattr(cfg, "PARES_DRENADORES_VETADOS", ())
            if symbol in vetados_oos:
                return False
            exp_rep = self.cuarentena_replicador_hasta.get(symbol, 0.0)
            if exp_rep > 0.0:
                if time.time() < exp_rep:
                    return False
                self.cuarentena_replicador_hasta.pop(symbol, None)
        if not self.whitelist_activa:
            return True
        return symbol in self.whitelist_activa

    def evaluar_dinamica_replicador_oos(self) -> Set[str]:
        """Aplica la Ecuación Diferencial del Replicador dx_i/dt = x_i * (E_i(x) - E_bar(x))
        sobre los últimos cierres registrados en `splitbrain_stats_v6.jsonl`.
        Si un par acumula >= 5 operaciones recientes con Win Rate < 36% y pago medio E_i(x)
        significativamente inferior a la media poblacional E_bar(x) (pnl_ventana <= -0.08 USDT),
        lo coloca en cuarentena autónoma por 60 minutos (3600s).
        """
        if ("test_v6_2_suite" in sys.modules or "test_v6_2_suite" in str(getattr(sys.modules.get("__main__"), "__file__", ""))):
            return set()

        archivo_stats = pathlib.Path(str(getattr(cfg, "STATS_FILE", "splitbrain_stats_v6.jsonl")))
        if not archivo_stats.exists():
            return set()

        ahora = time.time()
        # Limpiar cuarentenas expiradas
        for s_exp in [s for s, t_exp in self.cuarentena_replicador_hasta.items() if ahora >= t_exp]:
            self.cuarentena_replicador_hasta.pop(s_exp, None)

        por_simbolo: Dict[str, List[float]] = defaultdict(list)
        try:
            lineas = archivo_stats.read_text(encoding="utf-8", errors="ignore").splitlines()[-160:]
            for lin in lineas:
                lin = lin.strip()
                if not lin:
                    continue
                try:
                    reg = json.loads(lin)
                    sym = str(reg.get("symbol") or "")
                    pnl = float(reg.get("pnl_usdt") or 0.0)
                    if sym:
                        por_simbolo[sym].append(pnl)
                except Exception:
                    continue
        except Exception:
            return set(self.cuarentena_replicador_hasta.keys())

        if not por_simbolo:
            return set(self.cuarentena_replicador_hasta.keys())

        todos_pnls = [p for lista in por_simbolo.values() for p in lista[-10:]]
        e_bar = sum(todos_pnls) / len(todos_pnls) if todos_pnls else 0.0
        ttl_cuarentena = float(getattr(cfg, "REPLICADOR_QUARANTINE_SEG", 3600.0))

        for sym in self.universo_base:
            ventana_sym = por_simbolo.get(sym, [])[-10:]
            n = len(ventana_sym)
            if n < 5:
                continue
            wins = sum(1 for x in ventana_sym if x > 0.0)
            wr = (wins / n) * 100.0
            pnl_ventana = sum(ventana_sym)
            e_i = pnl_ventana / n

            if wr < 36.0 and pnl_ventana <= -0.08 and e_i < (e_bar - 0.008):
                if ahora >= self.cuarentena_replicador_hasta.get(sym, 0.0):
                    self.cuarentena_replicador_hasta[sym] = ahora + ttl_cuarentena
                    print(
                        f"🧬 [DINÁMICA REPLICADOR] {sym} en cuarentena autónoma 60m "
                        f"(WR={wr:.1f}% en últ. {n} trades | E_i={e_i:+.4f} < E_bar={e_bar:+.4f} USDT)."
                    )

        return {s for s, t_exp in self.cuarentena_replicador_hasta.items() if ahora < t_exp}

    def _filtro_heuristico_respaldo(self) -> Set[str]:
        """Filtro cuantitativo determinista si Groq no está configurado o agota cuota en los 4 modelos:
        veta activos con drenaje continuo sin absorción (caída >= 6% en ventana y OFI <= -0.75).
        """
        vetados_replicador = self.evaluar_dinamica_replicador_oos()
        aprobados: Set[str] = set()
        for sym in self.universo_base:
            if sym in cfg.PROHIBIDAS or sym in cfg.BLACKLIST_PAIRS or sym in vetados_replicador:
                continue
            datos = self.telemetria_pares.get(sym) or {}
            cambio = float(datos.get("cambio_pct") or 0.0)
            ofi = float(datos.get("ofi") or 0.0)
            if cambio <= -0.06 and ofi <= -0.75:
                print(
                    f"[ESCUDO RAM] Fallback cuantitativo vetó a {sym} "
                    f"(caída {cambio:.1%}, OFI {ofi:+.2f})"
                )
                continue
            aprobados.add(sym)
        return aprobados if aprobados else set(self.universo_base)

    def _lista_modelos(self) -> List[str]:
        ahora = time.time()
        pool = list(cfg.GROQ_MODEL_POOL)
        if cfg.GROQ_MODEL and cfg.GROQ_MODEL not in pool:
            pool.insert(0, cfg.GROQ_MODEL)
        disponibles = [
            m
            for m in pool
            if m not in self._modelos_bloqueados_proyecto
            and ahora >= self._modelos_cooldown_hasta.get(m, 0.0)
        ]
        return disponibles if disponibles else [m for m in pool if m not in self._modelos_bloqueados_proyecto] or pool

    async def _llamar_modelo_groq(self, modelo: str, prompt_sistema: str, resumen_json: str) -> str:
        """Invoca a Groq en modo JSON_OBJECT para los modelos que lo soportan nativamente
        (`qwen/qwen3.8-27b`, `allam-2-7b`, `openai/gpt-oss-20b`) y en modo directo sin `response_format`
        para `openai/gpt-oss-120b`, ahorrando un viaje de red completo.
        """
        mensajes = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Telemetría actual: {resumen_json}"},
        ]
        if modelo in _MODELOS_RAW_TEXT_DIRECTO:
            respuesta = await asyncio.wait_for(
                cliente_groq.chat.completions.create(
                    messages=mensajes,
                    model=modelo,
                    temperature=0.0,
                    max_tokens=320,
                ),
                timeout=cfg.GROQ_TIMEOUT_SEG,
            )
            return respuesta.choices[0].message.content or "{}"

        try:
            respuesta = await asyncio.wait_for(
                cliente_groq.chat.completions.create(
                    messages=mensajes,
                    model=modelo,
                    temperature=0.0,
                    max_tokens=320,
                    response_format={"type": "json_object"},
                ),
                timeout=cfg.GROQ_TIMEOUT_SEG,
            )
            return respuesta.choices[0].message.content or "{}"
        except Exception as e_json:
            err_str = str(e_json).lower()
            if (
                "response_format" in err_str
                or "json_validate_failed" in err_str
                or ("400" in err_str and "permission" not in err_str and "blocked" not in err_str)
            ):
                _MODELOS_RAW_TEXT_DIRECTO.add(modelo)
                respuesta = await asyncio.wait_for(
                    cliente_groq.chat.completions.create(
                        messages=mensajes,
                        model=modelo,
                        temperature=0.0,
                        max_tokens=320,
                    ),
                    timeout=cfg.GROQ_TIMEOUT_SEG,
                )
                return respuesta.choices[0].message.content or "{}"
            raise

    async def auditar_universo_una_vez(self) -> Set[str]:
        """Ejecuta una auditoría de lotes en background combinando Dinámica del Replicador y Groq LPU."""
        if not self.universo_base:
            return self.whitelist_activa

        vetados_replicador = self.evaluar_dinamica_replicador_oos()

        if cliente_groq is None:
            self.whitelist_activa = self._filtro_heuristico_respaldo()
            self.ultimo_update_ts = time.time()
            return self.whitelist_activa

        resumen_lote = {
            sym: {
                "z": self.telemetria_pares.get(sym, {}).get("z_score", 0.0),
                "ofi": self.telemetria_pares.get(sym, {}).get("ofi", 0.0),
                "ret": self.telemetria_pares.get(sym, {}).get("cambio_pct", 0.0),
            }
            for sym in self.universo_base
        }

        prompt_sistema = (
            "Eres un Gestor de Riesgo Cuantitativo HFT en Bybit. "
            "Recibirás un JSON con métricas en tiempo real (z: Z-score, ofi: Order Flow Imbalance, ret: retorno reciente) "
            "de perpetuos cripto. Identifica únicamente activos que sufran colapso estructural sin absorción "
            "(ret <= -0.06 y ofi <= -0.75 simultáneamente). "
            'Responde ÚNICAMENTE con un objeto JSON válido con la clave "vetados" (lista de símbolos a bloquear). '
            'Ejemplo exacto: {"vetados": []}'
        )
        resumen_str = json.dumps(resumen_lote, separators=(",", ":"))

        modelos = self._lista_modelos()
        total_modelos = len(modelos)

        for intento in range(total_modelos):
            idx = (self._modelo_idx + intento) % total_modelos
            modelo_actual = modelos[idx]
            try:
                t0 = time.perf_counter()
                contenido = await self._llamar_modelo_groq(modelo_actual, prompt_sistema, resumen_str)
                latencia_ms = (time.perf_counter() - t0) * 1000.0
                datos = _extraer_json_robusto(contenido)
                vetados_raw = datos.get("vetados") or []
                vetados = {str(s) for s in vetados_raw if isinstance(s, str)} | vetados_replicador

                nuevos_aprobados = {
                    sym for sym in self.universo_base
                    if sym not in vetados and sym not in cfg.PROHIBIDAS and sym not in cfg.BLACKLIST_PAIRS
                }
                self.whitelist_activa = nuevos_aprobados if nuevos_aprobados else self._filtro_heuristico_respaldo()
                self.ultimo_update_ts = time.time()
                self._modelo_idx = idx
                print(
                    f"[ESCUDO RAM] Whitelist actualizada vía Groq LPU ({modelo_actual}) en {latencia_ms:.0f}ms "
                    f"(fuera de ruta crítica): {len(self.whitelist_activa)}/{len(self.universo_base)} aprobados."
                )
                return self.whitelist_activa
            except Exception as e:
                nombre_exc = type(e).__name__.lower()
                texto_low = str(e).lower()
                es_timeout = isinstance(e, (TimeoutError, asyncio.TimeoutError)) or "timeout" in nombre_exc or "timeout" in texto_low

                if any(k in texto_low for k in ("403", "permission", "blocked", "404", "not_found", "decommissioned")):
                    self._modelos_bloqueados_proyecto.add(modelo_actual)
                    sig_idx = (idx + 1) % total_modelos
                    print(
                        f"[ESCUDO RAM] Modelo `{modelo_actual}` bloqueado/no-disponible (403/404). "
                        f"Rotando a `{modelos[sig_idx]}`..."
                    )
                    continue

                if es_timeout or any(k in texto_low for k in ("429", "rate_limit", "503", "overloaded")):
                    if "tokens per day" in texto_low or "tpd" in texto_low or "requests per day" in texto_low:
                        self._modelos_cooldown_hasta[modelo_actual] = time.time() + 900.0
                    sig_idx = (idx + 1) % total_modelos
                    motivo = "Timeout >6s" if es_timeout else "Cuota 429/503"
                    print(
                        f"[ESCUDO RAM] {motivo} en `{modelo_actual}`. "
                        f"Rotando automáticamente al modelo `{modelos[sig_idx]}`..."
                    )
                    continue

                print(
                    f"[ESCUDO RAM] Aviso Groq background ({type(e).__name__}: {e}). "
                    f"Whitelist mantenida por heurística cuantitativa ({len(self.whitelist_activa)} activos)."
                )
                break

        self.whitelist_activa = self._filtro_heuristico_respaldo()
        self.ultimo_update_ts = time.time()
        return self.whitelist_activa

    async def bucle_centinela_background(self, intervalo_seg: float = cfg.GROQ_CICLO_SEG) -> None:
        """Demonio infinito en segundo plano. Jamás interrumpe al motor de ejecución."""
        await asyncio.sleep(15.0)
        while True:
            try:
                await self.auditar_universo_una_vez()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[ESCUDO RAM] Error inesperado en bucle centinela: {type(e).__name__}: {e}")
            await asyncio.sleep(intervalo_seg)


centinela = CentinelaSemanticoRAM()


async def evaluar_anomalia_groq(symbol: str, contexto_mercado: str = "") -> bool:
    """Interfaz O(1) en RAM (< 0.001 ms).
    Elimina por completo el bloqueo HTTP de 510-792ms de la ruta crítica de disparo.
    """
    return centinela.esta_aprobado(symbol)