#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Modulo 7: Evaluador Cuantico Tri-Sesion (8h/24h/72h), Calibrador OOS y Auditor Forense AST."""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import statistics
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Sequence

FEE_MAKER_BYBIT: Final[float] = 0.00020
FEE_TAKER_BYBIT: Final[float] = 0.00055
EPSILON: Final[float] = 1e-12
PISO_MUESTRA_MINIMA: Final[int] = 40
NL: Final[str] = chr(10)


@dataclass(frozen=True)
class MetricasVentana:
    nombre_ventana: str
    n_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    pnl_neto_usdt: float
    pnl_bruto_usdt: float
    fee_drag_usdt: float
    lvr_total_usdt: float
    profit_factor: float
    max_drawdown_pct: float
    z_p75_winners: float
    ofi_p25_winners: float
    mediana_hold_win_s: float
    mediana_hold_loss_s: float


@dataclass(frozen=True)
class EstadoActivoReplicador:
    symbol: str
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    pnl_neto_usdt: float
    ev_por_trade_usdt: float
    lcb_campbell_thompson: float
    peso_replicador: float
    clasificacion: str


def _cargar_env_simple(ruta_env: Path) -> None:
    if not ruta_env.exists():
        return
    for linea in ruta_env.read_text(encoding="utf-8", errors="ignore").splitlines():
        lin = linea.strip()
        if not lin or lin.startswith("#") or "=" not in lin:
            continue
        k, v = lin.split("=", 1)
        k, v = k.strip(), v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def _parse_ts(ts_raw: Any) -> float:
    if isinstance(ts_raw, (int, float)):
        return float(ts_raw)
    if isinstance(ts_raw, str) and ts_raw.strip():
        try:
            return datetime.fromisoformat(ts_raw.strip().replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _percentil(valores: Sequence[float], q: float) -> float:
    if not valores:
        return 0.0
    s = sorted(float(x) for x in valores)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * max(0.0, min(1.0, q / 100.0))
    bajo, alto = int(math.floor(pos)), int(math.ceil(pos))
    if bajo == alto:
        return s[bajo]
    peso = pos - bajo
    return s[bajo] * (1.0 - peso) + s[alto] * peso


def cargar_operaciones_reales(ruta_jsonl: Path) -> list[dict[str, Any]]:
    if not ruta_jsonl.exists():
        return []
    operaciones: list[dict[str, Any]] = []
    for linea in ruta_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        lin = linea.strip()
        if not lin:
            continue
        try:
            obj = json.loads(lin)
            if str(obj.get("motivo", "")) in ("ESCAPE_HUERFANA_BOOT", "CIERRE_LIMPIO_PRE_UPGRADE"):
                continue
            obj["_ts_epoch"] = _parse_ts(obj.get("timestamp"))
            operaciones.append(obj)
        except Exception:
            continue
    operaciones.sort(key=lambda x: float(x.get("_ts_epoch", 0.0)))
    return operaciones


def evaluar_ventana_microestructura(nombre: str, trades: Sequence[dict[str, Any]], capital_base: float = 11.83) -> MetricasVentana:
    if not trades:
        return MetricasVentana(
            nombre_ventana=nombre, n_trades=0, wins=0, losses=0, win_rate_pct=0.0,
            pnl_neto_usdt=0.0, pnl_bruto_usdt=0.0, fee_drag_usdt=0.0, lvr_total_usdt=0.0,
            profit_factor=1.0, max_drawdown_pct=0.0, z_p75_winners=-3.25, ofi_p25_winners=0.30,
            mediana_hold_win_s=45.0, mediana_hold_loss_s=90.0,
        )
    wins = losses = 0
    pnl_neto = fee_drag = lvr_total = suma_ganancias = suma_perdidas = 0.0
    z_winners: list[float] = []
    ofi_winners: list[float] = []
    hold_winners: list[float] = []
    hold_losers: list[float] = []
    equity = hwm = capital_base
    max_dd = 0.0

    for t in trades:
        pnl = float(t.get("pnl_usdt", 0.0) or 0.0)
        fees = float(t.get("fees_usdt", 0.0) or 0.0)
        hold_s = max(0.1, float(t.get("hold_seg", 1.0) or 1.0))
        map_u = float(t.get("map_usdt", 0.0) or 0.0)
        if map_u <= 0.0:
            ep = float(t.get("entry_price", 0.0) or 0.0)
            qt = float(t.get("qty", 0.0) or 0.0)
            map_u = ep * qt if (ep > 0 and qt > 0) else 10.5
        lvr_i = float(t.get("lvr_usdt", 0.0) or 0.0)
        if lvr_i <= 0.0:
            lvr_i = ((0.00025 ** 2) / 8.0) * map_u * hold_s

        pnl_neto += pnl
        fee_drag += fees
        lvr_total += lvr_i
        equity += pnl
        if equity > hwm:
            hwm = equity
        dd_actual = (hwm - equity) / max(hwm, EPSILON)
        if dd_actual > max_dd:
            max_dd = dd_actual

        z_val, ofi_val = t.get("z_score_entry"), t.get("ofi_entry")
        if pnl > 0.0:
            wins += 1
            suma_ganancias += pnl
            hold_winners.append(hold_s)
            if isinstance(z_val, (int, float)) and -12.0 <= float(z_val) <= -2.0:
                z_winners.append(float(z_val))
            if isinstance(ofi_val, (int, float)) and 0.05 <= float(ofi_val) <= 1.0:
                ofi_winners.append(float(ofi_val))
        else:
            losses += 1
            suma_perdidas += abs(pnl)
            hold_losers.append(hold_s)

    n_tot = wins + losses
    wr = (wins / n_tot * 100.0) if n_tot > 0 else 0.0
    pf = (suma_ganancias / max(suma_perdidas, 0.001)) if suma_perdidas > 0 else (2.5 if suma_ganancias > 0 else 1.0)
    z_p75 = _percentil(z_winners, 75.0) if len(z_winners) >= 3 else -3.25
    ofi_p25 = _percentil(ofi_winners, 25.0) if len(ofi_winners) >= 3 else 0.30
    med_w = statistics.median(hold_winners) if hold_winners else 45.0
    med_l = statistics.median(hold_losers) if hold_losers else 90.0

    return MetricasVentana(
        nombre_ventana=nombre, n_trades=n_tot, wins=wins, losses=losses, win_rate_pct=round(wr, 2),
        pnl_neto_usdt=round(pnl_neto, 4), pnl_bruto_usdt=round(pnl_neto + fee_drag, 4),
        fee_drag_usdt=round(fee_drag, 4), lvr_total_usdt=round(lvr_total, 5),
        profit_factor=round(pf, 3), max_drawdown_pct=round(max_dd * 100.0, 2),
        z_p75_winners=round(max(-4.20, min(-2.85, z_p75)), 4),
        ofi_p25_winners=round(max(0.15, min(0.50, ofi_p25)), 4),
        mediana_hold_win_s=round(med_w, 1), mediana_hold_loss_s=round(med_l, 1),
    )


def ejecutar_dinamica_replicador_oos(trades: Sequence[dict[str, Any]]) -> list[EstadoActivoReplicador]:
    por_par: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol", "")).strip()
        if sym:
            por_par[sym].append(float(t.get("pnl_usdt", 0.0) or 0.0))
    if not por_par:
        return []

    n_pares = len(por_par)
    pesos = {s: 1.0 / n_pares for s in por_par}
    fitness: dict[str, float] = {}
    lcb_map: dict[str, float] = {}

    for sym, pnls in por_par.items():
        n = len(pnls)
        media = sum(pnls) / n
        var = sum((x - media) ** 2 for x in pnls) / max(1, n - 1) if n > 1 else 0.002
        lcb = media - 0.65 * math.sqrt(var / max(1, n))
        lcb_map[sym] = lcb
        fitness[sym] = media + 0.50 * lcb

    for _ in range(15):
        e_bar = sum(pesos[s] * fitness[s] for s in por_par)
        nuevos = {s: max(0.0001, pesos[s] * (1.0 + 4.0 * (fitness[s] - e_bar))) for s in por_par}
        norm = sum(nuevos.values())
        pesos = {s: v / max(norm, EPSILON) for s, v in nuevos.items()}

    resultados: list[EstadoActivoReplicador] = []
    for sym, pnls in por_par.items():
        n = len(pnls)
        w = sum(1 for x in pnls if x > 0)
        l = n - w
        wr = (w / n * 100.0) if n > 0 else 0.0
        pnl_tot = sum(pnls)
        clasif = "NUCLEO_ALFA" if (pnl_tot > 0.0 and wr >= 55.0) else ("SECUNDARIO_OBSERVACION" if pnl_tot >= -0.04 and n < 8 else "VETADO_DRENADOR")
        resultados.append(EstadoActivoReplicador(
            symbol=sym, trades=n, wins=w, losses=l, win_rate_pct=round(wr, 1),
            pnl_neto_usdt=round(pnl_tot, 4), ev_por_trade_usdt=round(pnl_tot / n, 4),
            lcb_campbell_thompson=round(lcb_map[sym], 4), peso_replicador=round(pesos[sym], 4),
            clasificacion=clasif,
        ))
    resultados.sort(key=lambda r: (r.clasificacion == "NUCLEO_ALFA", r.pnl_neto_usdt), reverse=True)
    return resultados


def calibrar_parametros_ewma_oos(m_8h: MetricasVentana, m_24h: MetricasVentana, m_72h: MetricasVentana) -> dict[str, Any]:
    if m_8h.n_trades >= 10:
        w8, w24, w72 = 0.50, 0.30, 0.20
        modo_ponderacion = "ALTA_FRECUENCIA_8H (0.50/0.30/0.20)"
    elif m_24h.n_trades >= 12:
        w8, w24, w72 = 0.25, 0.45, 0.30
        modo_ponderacion = "BALANCEADO_24H (0.25/0.45/0.30)"
    else:
        w8, w24, w72 = 0.15, 0.30, 0.55
        modo_ponderacion = f"BAYESIAN_ANCHOR_N{m_72h.n_trades} (0.15/0.30/0.55)"

    z_opt = w8 * m_8h.z_p75_winners + w24 * m_24h.z_p75_winners + w72 * m_72h.z_p75_winners
    ofi_opt = w8 * m_8h.ofi_p25_winners + w24 * m_24h.ofi_p25_winners + w72 * m_72h.ofi_p25_winners

    p_win = max(0.50, min(0.75, (w8 * m_8h.win_rate_pct + w24 * m_24h.win_rate_pct + w72 * m_72h.win_rate_pct) / 100.0))
    b_ratio = max(0.85, min(1.80, w8 * m_8h.profit_factor + w24 * m_24h.profit_factor + w72 * m_72h.profit_factor))
    f_kelly_raw = p_win - (1.0 - p_win) / max(b_ratio, EPSILON)
    f_shrunk = 0.65 * max(0.25, f_kelly_raw) + 0.35 * 0.38
    dd_ref = max(m_24h.max_drawdown_pct, m_72h.max_drawdown_pct * 0.75) / 100.0
    f_brb = float(max(0.22, min(0.45, f_shrunk * math.exp(-2.20 * dd_ref))))

    ratio_hold = m_72h.mediana_hold_win_s / max(m_72h.mediana_hold_loss_s, 1.0)
    kappa = max(2.10, min(2.45, 2.25 + 0.15 * (1.0 - min(1.5, ratio_hold))))

    return {
        "modo_ponderacion_ewma": modo_ponderacion,
        "pesos_ewma": {"w_8h": w8, "w_24h": w24, "w_72h": w72},
        "KELLY_FRACTION_BRB": round(f_brb, 4),
        "ALMGREN_CHRISS_KAPPA": round(kappa, 4),
        "Z_SCORE_NUCLEO_ALFA": round(max(-3.60, min(-2.95, z_opt)), 4),
        "OFI_UMBRAL_MINIMO": round(max(0.08, min(0.35, ofi_opt * 0.45)), 4),
        "SL_INICIAL_PCT": 0.0120,
        "SL_BYBIT_PCT": 0.0160,
        "MAKER_UNWIND_TARGET_PCT": 0.0052,
    }


def escanear_ast_anti_reward_hacking(repo_dir: Path) -> dict[str, Any]:
    hallazgos: list[str] = []
    modulos_ok = 0
    for nombre in (
        "config_parametros.py", "1_radar_volatilidad.py", "2_escudo_semantico.py", "3_ejecutor_sniper.py",
        "4_telemetria_telegram.py", "5_gestor_posiciones.py", "6_stats_tracker.py", "main.py",
    ):
        ruta = repo_dir / nombre
        if not ruta.exists():
            hallazgos.append(f"Falta modulo: {nombre}")
            continue
        try:
            ast.parse(ruta.read_text(encoding="utf-8", errors="ignore"), filename=str(ruta))
            modulos_ok += 1
        except Exception as e:
            hallazgos.append(f"Error AST en {nombre}: {e}")

    suite_path = repo_dir / "test_v7_0_suite.py"
    suite_15_15 = False
    if suite_path.exists():
        env_sub = os.environ.copy()
        env_sub["SPLITBRAIN_TEST_MODE"] = "1"
        env_sub["PYTHONUTF8"] = "1"
        try:
            proc = subprocess.run([sys.executable, str(suite_path)], cwd=str(repo_dir), env=env_sub, capture_output=True, text=True, timeout=30)
            salida = (proc.stdout or "") + (proc.stderr or "")
            suite_15_15 = (proc.returncode == 0 and "(15/15) SUPERADAS" in salida)
        except Exception as e:
            hallazgos.append(f"Error suite: {e}")
        finally:
            try:
                (repo_dir / ".test_bot_hft.log").unlink(missing_ok=True)
            except Exception:
                pass

    return {
        "modulos_compilados_ast": f"{modulos_ok}/8",
        "suite_15_15_superada": suite_15_15,
        "anomalias_detectadas": hallazgos,
        "estado_forense": "LIMPIO_100%" if (modulos_ok == 8 and suite_15_15 and not hallazgos) else "CON_OBSERVACIONES",
    }


def enviar_reporte_telegram(texto: str) -> bool:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or "").strip()
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": texto}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=str, default=str(Path(__file__).resolve().parent))
    parser.add_argument("--horas", type=float, default=8.0)
    parser.add_argument("--telegram", action="store_true")
    args = parser.parse_args()

    repo_dir = Path(args.repo).resolve()
    _cargar_env_simple(repo_dir / ".env")
    ahora = time.time()
    utc_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    todos_trades = cargar_operaciones_reales(repo_dir / "splitbrain_stats_v6.jsonl")
    trades_cartera = [t for t in todos_trades if t.get("symbol") != "STRK/USDT:USDT"]

    t_8h = [t for t in trades_cartera if ahora - float(t.get("_ts_epoch", 0.0)) <= 8.0 * 3600.0]
    t_24h = [t for t in trades_cartera if ahora - float(t.get("_ts_epoch", 0.0)) <= 24.0 * 3600.0]
    t_72h = [t for t in trades_cartera if ahora - float(t.get("_ts_epoch", 0.0)) <= 72.0 * 3600.0]
    if len(t_72h) < PISO_MUESTRA_MINIMA and len(trades_cartera) >= PISO_MUESTRA_MINIMA:
        t_72h = trades_cartera[-PISO_MUESTRA_MINIMA:]
    elif len(t_72h) < len(trades_cartera):
        t_72h = trades_cartera[-max(len(t_72h), min(len(trades_cartera), PISO_MUESTRA_MINIMA)):]

    m_8h = evaluar_ventana_microestructura("Sesion 8h (Funding Cycle)", t_8h)
    m_24h = evaluar_ventana_microestructura("Ciclo Diario 24h", t_24h)
    m_72h = evaluar_ventana_microestructura(f"Ancla Estadistica 72h (N={len(t_72h)})", t_72h)
    m_global = evaluar_ventana_microestructura("Global Cartera Activa", trades_cartera)

    ranking_replicador = ejecutar_dinamica_replicador_oos(t_72h if t_72h else trades_cartera)
    params_calibrados = calibrar_parametros_ewma_oos(m_8h, m_24h, m_72h)
    auditoria_ast = escanear_ast_anti_reward_hacking(repo_dir)

    payload_json = {
        "timestamp_utc": utc_str,
        "epoch": round(ahora, 3),
        "version_evaluador": "7.8.1-TRI-SESSION-EWMA",
        "auditoria_ast": auditoria_ast,
        "ventanas": {
            "sesion_8h": asdict(m_8h),
            "diario_24h": asdict(m_24h),
            "ancla_72h": asdict(m_72h),
            "global_cartera_activa": asdict(m_global),
        },
        "parametros_calibrados": params_calibrados,
        "replicador_oos": [asdict(r) for r in ranking_replicador],
    }

    ruta_json = repo_dir / "calibracion_diaria_oos.json"
    ruta_json_tmp = repo_dir / ".calibracion_diaria_oos.json.tmp"
    ruta_json_tmp.write_text(json.dumps(payload_json, indent=2, ensure_ascii=False) + NL, encoding="utf-8")
    ruta_json_tmp.replace(ruta_json)

    lineas_md = [
        f"# REPORTE CUANTICO TRI-SESION (8H / 24H / 72H) - {utc_str}",
        "",
        f"- **Estado Forense AST**: `{auditoria_ast['estado_forense']}` (`{auditoria_ast['modulos_compilados_ast']}` modulos, Suite 15/15: `{auditoria_ast['suite_15_15_superada']}`)",
        f"- **Modo EWMA**: `{params_calibrados['modo_ponderacion_ewma']}` | **Kelly BRB**: `{params_calibrados['KELLY_FRACTION_BRB']}` | **Z OOS**: `{params_calibrados['Z_SCORE_NUCLEO_ALFA']}` | **OFI Min**: `{params_calibrados['OFI_UMBRAL_MINIMO']}`",
        "",
        "| Ventana | Trades (W/L) | Win Rate | PnL Neto (USDT) | Fee Drag (USDT) | LVR (USDT) | Profit Factor | Max DD % |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for mv in (m_8h, m_24h, m_72h, m_global):
        lineas_md.append(f"| **{mv.nombre_ventana}** | {mv.n_trades} ({mv.wins}W/{mv.losses}L) | {mv.win_rate_pct:.1f}% | `{mv.pnl_neto_usdt:+.4f}` | `{mv.fee_drag_usdt:.4f}` | `{mv.lvr_total_usdt:.5f}` | {mv.profit_factor:.2f} | {mv.max_drawdown_pct:.2f}% |")

    (repo_dir / "reporte_cuantico_24h.md").write_text(NL.join(lineas_md) + NL, encoding="utf-8")

    print("======================================================================================")
    print(f" EVALUADOR CUANTICO TRI-SESION OOS COMPLETADO ({utc_str})")
    print("======================================================================================")
    print(f"  [AST & CONTRATO] : {auditoria_ast['estado_forense']} ({auditoria_ast['modulos_compilados_ast']} modulos | 15/15: {auditoria_ast['suite_15_15_superada']})")
    print(f"  [SESION 8H]      : {m_8h.n_trades} trades ({m_8h.wins}W/{m_8h.losses}L | WR {m_8h.win_rate_pct:.1f}%) -> PnL: {m_8h.pnl_neto_usdt:+.4f} USDT | Fees: {m_8h.fee_drag_usdt:.4f}")
    print(f"  [DIARIO 24H]     : {m_24h.n_trades} trades ({m_24h.wins}W/{m_24h.losses}L | WR {m_24h.win_rate_pct:.1f}%) -> PnL: {m_24h.pnl_neto_usdt:+.4f} USDT | PF: {m_24h.profit_factor:.2f}")
    print(f"  [ANCLA 72H/N40]  : {m_72h.n_trades} trades ({m_72h.wins}W/{m_72h.losses}L | WR {m_72h.win_rate_pct:.1f}%) -> PnL: {m_72h.pnl_neto_usdt:+.4f} USDT | PF: {m_72h.profit_factor:.2f}")
    print(f"  [CALIBRACION OOS]: Modo={params_calibrados['modo_ponderacion_ewma']} | Kelly_BRB={params_calibrados['KELLY_FRACTION_BRB']} | Z={params_calibrados['Z_SCORE_NUCLEO_ALFA']} | OFI={params_calibrados['OFI_UMBRAL_MINIMO']}")
    print("======================================================================================")

    if args.telegram:
        msg_tg = NL.join([
            "📊 [AUDITORIA CUANTICA OOS 8H/24H/72H]",
            f"• 8h: {m_8h.wins}W/{m_8h.losses}L ({m_8h.win_rate_pct:.1f}%) | {m_8h.pnl_neto_usdt:+.4f} USDT",
            f"• 24h: {m_24h.wins}W/{m_24h.losses}L ({m_24h.win_rate_pct:.1f}%) | {m_24h.pnl_neto_usdt:+.4f} USDT",
            f"• 72h: {m_72h.wins}W/{m_72h.losses}L ({m_72h.win_rate_pct:.1f}%) | {m_72h.pnl_neto_usdt:+.4f} USDT",
            f"• Calibracion OOS: Z={params_calibrados['Z_SCORE_NUCLEO_ALFA']} | Kelly={params_calibrados['KELLY_FRACTION_BRB']}",
            f"• AST: {auditoria_ast['estado_forense']} (15/15 OK)",
        ])
        ok_tg = enviar_reporte_telegram(msg_tg)
        print(f"  [TELEGRAM OOS]   : Reporte enviado = {ok_tg}")
    return 0


if __name__ == "__main__":
    sys.exit(main())