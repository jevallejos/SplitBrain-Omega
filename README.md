# SplitBrain-Omega v7.0.0 — Emperor's Time (HFT Microstructure Engine)

Motor autónomo de arbitraje estadístico y creación de mercado de alta frecuencia sobre futuros perpetuos UTA (`Bybit V5` + `Groq LPU`), gobernado por la doctrina **Emperor's Time (v7.0.0)**.

## Arquitectura Nuclear (12 Archivos Estrictos)

| Archivo | Función en SplitBrain-Omega v7.0 |
| :--- | :--- |
| `config_parametros.py` | Constantes canónicas v7.0, Núcleo Alfa (`WAL` restituido) y Blacklist OOS (`PUMPFUN` purgado). |
| `main.py` | Orquestador asíncrono, cerrojo físico `.splitbrain.lock` y reconciliador de arranque Zero-Zombie. |
| `1_radar_volatilidad.py` | Radar WebSocket L1/L2, Filtro Escarlata de Dislocación Física (`<= -0.72%`), Lyapunov, VPIN y Hawkes. |
| `2_escudo_semantico.py` | Whitelist O(1) en RAM (< 2 µs) y rotación asíncrona multi-modelo en Groq LPU fuera de ruta crítica. |
| `3_ejecutor_sniper.py` | Ejecutor Zero-REST con TTL pasivo relámpago (`2.4s`), Penny-Jump y Modo Sniper IOC en capitulación. |
| `4_telemetria_telegram.py` | Despachador asíncrono no bloqueante de telemetría operativa. |
| `5_gestor_posiciones.py` | Motor de salida Emperor's Time: Cosecha 50/50 (+0.48%), Trailing libre (+0.75%) y Escudo Pasivo Maker (+0.10%). |
| `6_stats_tracker.py` | Caja Negra contable JSONL con cálculo de `MAP`, `LVR` y desduplicación atómica. |
| `test_v7_0_suite.py` | Suite determinista de validación de integridad 16/16 en entorno aislado. |

## Las 5 Leyes de "Emperor's Time" (v7.0.0)

1. **Filtro Escarlata de Dislocación Física Real (`<= -0.72%`)**: Prohibido disparar en micro-oscilaciones de baja volatilidad (`-0.48%` a `-0.55%`) aunque el Z-Score supere `-3.0`.
2. **TTL Relámpago en Cola (`2.4s`) y Modo Sniper IOC**: Las órdenes `PostOnly` que no se llenan en `2.4s` (o cuyo OFI cae de `+0.20`) se cancelan en el acto para impedir selección adversa tardía. En capitulaciones extremas (`Z <= -3.60`, caída `<= -1.15%`, `OFI >= +0.75`), dispara con límite IOC protegido.
3. **Escudo Pasivo Maker (`+0.10%`) en lugar de Cortes Taker en Pérdida**: Erradicación de cierres Taker prematuros (`Maker-Scratch +0.05%`, `STAGNATION_EXIT` y `EARLY_TOXIC_SL`). Toda posición lenta tras `55s` coloca salida pasiva `PostOnly` en `+0.10%` (`+0.06%` neto positivo).
4. **Restauración del Payoff Asimétrico (`+0.48%` Maker / `+0.75%` Trailing) y Supervivencia (`-1.85%` / `-2.50%`)**: Holgura real de Stop-Loss en Bybit (`-2.50%`) para evitar barridos por mecha en los primeros segundos.
5. **Saneamiento Total del Universo**: `WAL/USDT:USDT` fijado en el Núcleo Alfa y `PUMPFUN/USDT:USDT` purgado de raíz.
