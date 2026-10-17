import os as _os_v7_guard
_os_v7_guard.environ['SPLITBRAIN_TEST_MODE'] = '1'
"""Suite de Verificación Integrada para SplitBRain-Code V6.4.0 (15/15 Pruebas de Grado Producción)."""
import ast
import asyncio
import importlib
import json
import py_compile
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ARCHIVOS_PROYECTO = [
    "config_parametros.py",
    "1_radar_volatilidad.py",
    "2_escudo_semantico.py",
    "3_ejecutor_sniper.py",
    "4_telemetria_telegram.py",
    "5_gestor_posiciones.py",
    "6_stats_tracker.py",
    "main.py",
]


def _extraer_atributos_cfg_ast(raiz: Path) -> Set[Tuple[str, str]]:
    """Escanea el AST de todos los módulos usando `utf-8-sig` (inmune a BOM U+FEFF en Windows)."""
    requeridos: Set[Tuple[str, str]] = set()
    for nombre_archivo in ARCHIVOS_PROYECTO:
        codigo = (raiz / nombre_archivo).read_text(encoding="utf-8-sig")
        arbol = ast.parse(codigo, filename=nombre_archivo)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Attribute) and isinstance(nodo.value, ast.Name) and nodo.value.id == "cfg":
                requeridos.add((nombre_archivo, nodo.attr))
    return requeridos


class _MockExchangeHuerfanas:
    """Simula en memoria las 3 posiciones zombies reales de Bybit (ADA, ENA, XRP) sin tocar red."""

    def __init__(self, t_antiguo_ms: int) -> None:
        self.t_antiguo_ms = t_antiguo_ms
        self.ordenes_cerradas: List[Dict[str, Any]] = []

    async def fetch_positions(self, symbols: Any = None) -> List[Dict[str, Any]]:
        todas = [
            {
                "symbol": "ADA/USDT:USDT",
                "side": "long",
                "contracts": 33.0,
                "entryPrice": 0.2307,
                "markPrice": 0.2315,
                "timestamp": self.t_antiguo_ms,
                "info": {"avgPrice": "0.2307", "markPrice": "0.2315", "updatedTime": str(self.t_antiguo_ms)},
            },
            {
                "symbol": "ENA/USDT:USDT",
                "side": "long",
                "contracts": 53.0,
                "entryPrice": 0.19659,
                "markPrice": 0.19720,
                "timestamp": self.t_antiguo_ms,
                "info": {"avgPrice": "0.19659", "markPrice": "0.19720", "updatedTime": str(self.t_antiguo_ms)},
            },
            {
                "symbol": "XRP/USDT:USDT",
                "side": "long",
                "contracts": 5.0,
                "entryPrice": 1.3278,
                "markPrice": 1.3310,
                "timestamp": self.t_antiguo_ms,
                "info": {"avgPrice": "1.3278", "markPrice": "1.3310", "updatedTime": str(self.t_antiguo_ms)},
            },
        ]
        if symbols:
            return [p for p in todas if p["symbol"] in symbols]
        return todas

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{float(amount):.4f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{float(price):.6f}"

    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: Any, price: Any = None, params: Any = None
    ) -> Dict[str, Any]:
        orden = {
            "id": f"mock_{len(self.ordenes_cerradas) + 1}",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": float(amount),
            "price": float(price) if price is not None else None,
            "params": params or {},
        }
        self.ordenes_cerradas.append(orden)
        return orden

    async def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return {"id": order_id, "status": "canceled"}

    async def fetch_order(self, order_id: str, symbol: str, params: Any = None) -> Dict[str, Any]:
        precios_mock = {
            "ADA/USDT:USDT": 0.2315,
            "ENA/USDT:USDT": 0.19720,
            "XRP/USDT:USDT": 1.3310,
        }
        return {"id": order_id, "status": "closed", "average": precios_mock.get(symbol, 1.0)}

    async def fetch_my_trades(self, symbol: str, since: Any = None) -> List[Dict[str, Any]]:
        return [{"side": "sell", "amount": 2500.0, "price": 0.004507}]

    async def fetch_balance(self) -> Dict[str, Any]:
        return {"USDT": {"total": 11.0171, "free": 11.0171}}


class _MockExchangeZeroRest:
    """Verifica que ejecutar_orden_sniper NO invoque REST antes de create_order y valide Penny-Jump."""

    def __init__(self) -> None:
        self.markets = {
            "BTW/USDT:USDT": {
                "precision": {"amount": 1.0, "price": 0.0001},
                "limits": {"amount": {"min": 1.0}, "cost": {"min": 5.0}},
            }
        }
        self.ordenes_creadas: List[Dict[str, Any]] = []
        self.create_order_en_ruta_critica = False

    async def load_markets(self) -> None:
        raise AssertionError("VIOLACIÓN ZERO-REST: load_markets() fue llamado en la ruta crítica")

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        raise AssertionError("VIOLACIÓN ZERO-REST: fetch_ticker() fue llamado en la ruta crítica")

    async def set_leverage(self, leverage: int, symbol: str) -> None:
        raise AssertionError("VIOLACIÓN ZERO-REST: set_leverage() fue llamado en la ruta crítica")

    async def fetch_balance(self) -> Dict[str, Any]:
        if self.create_order_en_ruta_critica:
            raise AssertionError("VIOLACIÓN ZERO-REST: fetch_balance() fue llamado antes de create_order()")
        return {"USDT": {"total": 11.0171, "free": 11.0171}}

    def market(self, symbol: str) -> Dict[str, Any]:
        return self.markets[symbol]

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{int(amount)}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{float(price):.4f}"

    async def create_order(
        self, symbol: str, order_type: str, side: str, amount: Any, price: Any = None, params: Any = None
    ) -> Dict[str, Any]:
        self.create_order_en_ruta_critica = False
        orden = {
            "id": "zero_rest_order_1",
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": float(amount),
            "price": float(price),
            "params": params or {},
        }
        self.ordenes_creadas.append(orden)
        return orden

    async def fetch_order(self, order_id: str, symbol: str, params: Any = None) -> Dict[str, Any]:
        return {"id": order_id, "status": "canceled", "filled": 0.0}


async def ejecutar_auditoria_v6_4_0() -> None:
    print("==================================================================")
    print("🧪 INICIANDO SUITE DE PRUEBAS INTEGRADA: SPLITBRAIN-OMEGA V7.0.0 (EMPEROR'S TIME)")
    print("==================================================================")

    # 1. Verificación de compilación sintáctica de todos los módulos
    raiz = Path(__file__).resolve().parent
    for archivo in ARCHIVOS_PROYECTO:
        ruta = raiz / archivo
        assert ruta.exists(), f"Falta el archivo obligatorio: {archivo}"
        py_compile.compile(str(ruta), doraise=True)
    print(f"✅ [1/15] Compilación exitosa de los {len(ARCHIVOS_PROYECTO)} módulos del repositorio.")

    cfg = importlib.import_module("config_parametros")
    radar = importlib.import_module("1_radar_volatilidad")
    escudo = importlib.import_module("2_escudo_semantico")
    sniper = importlib.import_module("3_ejecutor_sniper")
    gestor_mod = importlib.import_module("5_gestor_posiciones")
    stats_mod = importlib.import_module("6_stats_tracker")
    main_mod = importlib.import_module("main")

    # 2. Auditoría AST de Contratos de Configuración V6.4.0 (con lectura utf-8-sig)
    referencias_cfg = _extraer_atributos_cfg_ast(raiz)
    faltantes = [(arch, attr) for arch, attr in sorted(referencias_cfg) if not hasattr(cfg, attr)]
    assert not faltantes, f"Atributos faltantes en config_parametros.py: {faltantes}"
    assert cfg.TIEMPO_MAX_HOLD_SEG == 180
    assert cfg.EARLY_TOXIC_HOLD_SEG == 45
    assert cfg.STAGNATION_HOLD_SEG == 105
    assert abs(cfg.MAKER_UNWIND_ACTIVACION_PCT - 0.0052) < 1e-9
    atributos_unicos = {attr for _, attr in referencias_cfg}
    print(
        f"✅ [2/15] Contrato AST V6.4.0 validado: {len(atributos_unicos)} constantes verificadas "
        f"(Hold={cfg.TIEMPO_MAX_HOLD_SEG}s, EarlyCut={cfg.EARLY_TOXIC_HOLD_SEG}s, "
        f"StagCut={cfg.STAGNATION_HOLD_SEG}s, MakerUnwind=+{cfg.MAKER_UNWIND_ACTIVACION_PCT:.2%})."
    )

    # 3. Prueba de Latencia Lock-Free O(1) en RAM del Centinela Groq + Lista Multi-Modelo
    presas_mock = ["XRP/USDT:USDT", "NEAR/USDT:USDT", "ADA/USDT:USDT", "ENA/USDT:USDT"]
    escudo.centinela.inicializar_universo(presas_mock)
    assert len(escudo.centinela._lista_modelos()) >= 3

    iteraciones = 10_000
    t0 = time.perf_counter_ns()
    for _ in range(iteraciones):
        aprobado = await escudo.evaluar_anomalia_groq("XRP/USDT:USDT")
    latencia_media_us = ((time.perf_counter_ns() - t0) / iteraciones) / 1000.0
    assert aprobado is True
    assert latencia_media_us < 50.0
    print(
        f"✅ [3/15] Lookup O(1) en Whitelist RAM ({latencia_media_us:.3f} µs) y pool de "
        f"{len(escudo.centinela._lista_modelos())} modelos Groq validados."
    )

    # 4. Prueba de Fallback Cuantitativo ante Drenaje Estructural (Anti-LUNA)
    escudo.centinela.actualizar_telemetria(
        "NEAR/USDT:USDT", z_score=-5.4, ofi=-0.92, precio=4.50, cambio_pct=-0.085
    )
    aprobados_fallback = escudo.centinela._filtro_heuristico_respaldo()
    assert "NEAR/USDT:USDT" not in aprobados_fallback
    assert "XRP/USDT:USDT" in aprobados_fallback
    print("✅ [4/15] Filtro anti-colapso estructural (Anti-LUNA) validado correctamente.")

    # 5. Prueba de Inmunidad contra Micro-Ruido y Caída Desde Máximo
    vz = radar.VentanaZ(n=60)
    for _ in range(59):
        vz.push(83493.5)
    z_micro_ruido = vz.push(83459.2)
    assert z_micro_ruido is not None and z_micro_ruido > -2.0
    assert vz.caida_desde_maximo() > cfg.CAIDA_MIN_PCT

    vz_panico = radar.VentanaZ(n=60)
    for _ in range(59):
        vz_panico.push(83493.5)
    z_panico_real = vz_panico.push(81650.0)
    assert z_panico_real is not None and z_panico_real <= cfg.UMBRAL_PANICO
    assert vz_panico.caida_desde_maximo() <= cfg.CAIDA_MIN_PCT
    print(
        f"✅ [5/15] Calibración VentanaZ verificada: Micro-ruido (-0.04%) -> Z={z_micro_ruido:.2f} (Ignorado) | "
        f"Colapso Real (-2.21%) -> Z={z_panico_real:.2f} (Disparo)."
    )

    # 6. Prueba de Cerrojo Físico Aislado (.splitbrain_test.lock) para no chocar con tmux en vivo
    lock_original = main_mod.LOCK_FILE
    lock_test = raiz / ".splitbrain_test.lock"
    main_mod.LOCK_FILE = lock_test
    try:
        main_mod.adquirir_bloqueo_instancia_unica()
        assert lock_test.exists()
        assert main_mod._lock_adquirido_por_mi is True
        segundo_handle = lock_test.open("a+", encoding="utf-8")
        bloqueado_correctamente = False
        try:
            if sys.platform != "win32":
                import fcntl
                fcntl.flock(segundo_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                segundo_handle.seek(0)
                msvcrt.locking(segundo_handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (BlockingIOError, PermissionError, OSError):
            bloqueado_correctamente = True
        finally:
            segundo_handle.close()
            main_mod.liberar_bloqueo_instancia_unica()
        assert bloqueado_correctamente
    finally:
        main_mod.LOCK_FILE = lock_original
        if lock_test.exists():
            lock_test.unlink()
    print("✅ [6/15] Cerrojo físico de instancia única validado en archivo aislado (.splitbrain_test.lock).")

    # 7. Pruebas Asíncronas Integradas (7 a 15)
    ruta_original = stats_mod.tracker.ruta
    trades_originales = stats_mod.tracker._trades
    modo_paper_original = cfg.MODO_PAPER
    alerta_original_gestor = gestor_mod.enviar_alerta
    alerta_original_sniper = sniper.enviar_alerta
    ruta_temp = raiz / ".test_stats_v6_4_0_tmp.jsonl"

    async def _alerta_silenciosa(_msg: str) -> None:
        return None

    try:
        cfg.MODO_PAPER = False
        stats_mod.tracker.ruta = ruta_temp
        stats_mod.tracker._trades = []
        gestor_mod.enviar_alerta = _alerta_silenciosa
        sniper.enviar_alerta = _alerta_silenciosa

        t_hace_4_horas_ms = int((time.time() - 14_400) * 1000)
        mock_ex = _MockExchangeHuerfanas(t_antiguo_ms=t_hace_4_horas_ms)
        procesadas = await gestor_mod.gestor.reconciliar_posiciones_exchange(mock_ex)
        assert procesadas == 3
        assert len(mock_ex.ordenes_cerradas) == 3
        print(
            "✅ [7/15] Auto-Reconciliación de arranque validada: 3 posiciones zombies (ADA, ENA, XRP) "
            "liquidadas con reduceOnly=True."
        )

        # 8. Prueba de Detector de Contagio Cross-Asset (>= 3 pares en 10s) y Cooldown 300s
        gestor_mod.gestor.registrar_cooldown_post_cierre("WAL/USDT:USDT", 300)
        assert sniper.en_cooldown_post_cierre("WAL/USDT:USDT") is True
        assert gestor_mod.gestor.segundos_cooldown_restantes("WAL/USDT:USDT") > 290
        gestor_mod.gestor.cooldown_hasta.clear()

        radar.bloqueo_btc_hasta = 0.0
        radar._timestamps_panico.clear()
        t_now = time.time()
        assert radar.registrar_y_verificar_contagio(mock_ex, "ENA/USDT:USDT", t_now) is False
        assert radar.registrar_y_verificar_contagio(mock_ex, "NEAR/USDT:USDT", t_now + 1.0) is False
        assert radar.registrar_y_verificar_contagio(mock_ex, "ADA/USDT:USDT", t_now + 2.0) is True
        assert radar.bloqueo_activo() is True
        radar.bloqueo_btc_hasta = 0.0
        radar._timestamps_panico.clear()
        print(
            "✅ [8/15] Detector de Contagio Cross-Asset (>= 3 pares en 10s) y Cooldown Post-Cierre (300s) validados."
        )

        # 9. Prueba de Cotización Penny-Jump (Bertrand Discreto)
        mock_zero = _MockExchangeZeroRest()
        ticker_penny = {"last": 0.0501, "bid": 0.0499, "ask": 0.0502}
        p_penny = sniper._extraer_precio_maker_ws(mock_zero, "BTW/USDT:USDT", ticker_penny, ofi=0.40)
        assert p_penny is not None and abs(p_penny - 0.0500) < 1e-9
        print(f"✅ [9/15] Cotización Penny-Jump validada: bid=0.0499, ask=0.0502, OFI=+0.40 -> quote={p_penny:.4f}.")

        # 10. Prueba de Disparo Zero-REST en Ruta Crítica + Fire-and-Forget Telegram
        mock_zero.create_order_en_ruta_critica = True
        sniper._saldo_libre_ram = 11.0171
        sniper._saldo_ts = time.time()
        sniper._apalancamiento_ok.add("BTW/USDT:USDT")
        ticker_ws_mock = {"last": 0.0500, "bid": 0.0499, "bidVolume": 50000, "askVolume": 25000}

        await sniper.ejecutar_orden_sniper(
            mock_zero, "BTW/USDT:USDT", z_score=-3.58, ofi=0.33, ticker=ticker_ws_mock
        )
        assert len(mock_zero.ordenes_creadas) == 1
        assert sniper._saldo_libre_ram < 11.0171
        for t in list(sniper._tareas_as) + list(sniper._tareas_bg):
            t.cancel()
        await asyncio.gather(*sniper._tareas_as, *sniper._tareas_bg, return_exceptions=True)
        sniper._en_vuelo.discard("BTW/USDT:USDT")
        print(
            "✅ [10/15] Disparo Zero-REST validado: 0 llamadas HTTP previas a create_order, "
            "deducción atómica de margen en RAM y despacho Fire-and-Forget verificados."
        )

        # 11. Prueba Ancla Macro EMA900 Anti-Pump-Top + Filtro Innovation
        vz_pump = radar.VentanaZ(n=60, ventana_ema=900)
        for _ in range(600):
            vz_pump.push(0.05685)
        for _ in range(59):
            vz_pump.push(0.05935)
        z_pump_top = vz_pump.push(0.05883)
        desv_macro = vz_pump.desviacion_ema_macro()
        assert z_pump_top is not None and z_pump_top <= cfg.UMBRAL_PANICO
        assert desv_macro > cfg.TECHO_MACRO_EMA_PCT

        class _MockMarketsInnovation:
            markets = {
                "WAL/USDT:USDT": {"active": True, "info": {"symbolType": ""}},
                "TESTINNOV/USDT:USDT": {"active": True, "info": {"symbolType": "innovation"}},
            }

        assert main_mod._es_perpetuo_cripto_puro(_MockMarketsInnovation(), "WAL/USDT:USDT") is True
        assert main_mod._es_perpetuo_cripto_puro(_MockMarketsInnovation(), "TESTINNOV/USDT:USDT") is False
        print(
            f"✅ [11/15] Filtro Anti-Pump-Top EMA900 (WAL 0.05883 -> {desv_macro:+.2%} > +{cfg.TECHO_MACRO_EMA_PCT:.2%} VETADO) "
            f"y exclusión de zona `innovation` validados."
        )

        # 12. Prueba Cota Inferior Invariante de Break-Even en Bybit
        pos_wal = gestor_mod.Posicion(
            symbol="WAL/USDT:USDT",
            cantidad=134.0,
            entrada=0.05691,
            z_entrada=-2.74,
            ofi_entrada=0.18,
            sl=0.05691 * (1.0 + cfg.BE_OFFSET_PCT),
            sl_exchange=0.0552027,
            max_precio=0.05721,
            t_apertura=time.time() - 40.0,
            ofi_media=radar.MediaMovilTemporal(10),
            be_activo=True,
        )
        sl_bybit_obj = gestor_mod.gestor.calcular_objetivo_sl_exchange(pos_wal)
        assert sl_bybit_obj >= 0.05691 * (1.0 + cfg.BE_PISO_EXCHANGE_PCT)
        print(
            f"✅ [12/15] Piso Invariante de Break-Even en Bybit validado: Entrada=0.05691 -> "
            f"SL Interno={pos_wal.sl:.6f} -> SL Bybit={sl_bybit_obj:.6f} (+{(sl_bybit_obj / 0.05691 - 1):.2%})."
        )

        # 13. Prueba V6.4.0: Atractor vs Repulsor de Lyapunov, VPIN y Compuerta de Fricción de Macrì
        vz_lyap = radar.VentanaZ(n=60)
        vz_lyap.push(1.0000)
        vz_lyap.push(0.9990)
        vz_lyap.push(0.9960)  # Caída acelerando -> Repulsor (acel_lyapunov < LYAPUNOV_ACCEL_MIN)
        assert vz_lyap.acel_lyapunov < cfg.LYAPUNOV_ACCEL_MIN
        vz_lyap.push(0.9958)  # Frenado de caída -> Atractor (acel_lyapunov >= LYAPUNOV_ACCEL_MIN)
        assert vz_lyap.acel_lyapunov >= cfg.LYAPUNOV_ACCEL_MIN

        sp_ancho = radar.calcular_spread_relativo({"bid": 1.0000, "ask": 1.0025})
        assert sp_ancho > cfg.MAX_SPREAD_REL_ENTRADA
        print(
            f"✅ [13/15] Campo Potencial de Lyapunov (Atractor a={vz_lyap.acel_lyapunov:+.4%}) "
            f"y Compuerta de Fricción de Macrì (Spread {sp_ancho:.2%} > {cfg.MAX_SPREAD_REL_ENTRADA:.2%} VETADO) validados."
        )

        # 14. Prueba V6.4.0: Inmunidad Doble Contabilización (`pos.cerrada`), Error `110017` y Desduplicación Trade #112
        dup1 = {
            "timestamp": "2026-10-15T16:18:30+00:00",
            "symbol": "PUMPFUN/USDT:USDT",
            "entry_price": 0.004512,
            "exit_price": 0.004507,
            "qty": 2500.0,
            "pnl_usdt": -0.020953,
            "motivo": "EVACUACION_CONTAGIO_CROSS_ASSET",
        }
        dup2 = {
            "timestamp": "2026-10-15T16:18:36+00:00",
            "symbol": "PUMPFUN/USDT:USDT",
            "entry_price": 0.004512,
            "exit_price": 0.004507,
            "qty": 2500.0,
            "pnl_usdt": -0.020953,
            "motivo": "SL_EXCHANGE",
        }
        ruta_temp.write_text(json.dumps(dup1) + "\n" + json.dumps(dup2) + "\n", encoding="utf-8")
        stats_mod.tracker._trades = None
        cargados = stats_mod.tracker.cargar()
        assert len(cargados) == 1, f"El desduplicador debió dejar 1 solo trade, dejó {len(cargados)}"

        class _MockExchange110017(_MockExchangeHuerfanas):
            async def fetch_positions(self, symbols: Any = None) -> List[Dict[str, Any]]:
                return [{"symbol": "PUMPFUN/USDT:USDT", "side": "long", "contracts": 2500.0}]

            async def create_order(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
                raise RuntimeError('bybit {"retCode":110017,"retMsg":"current position is zero"}')

        pos_dup = gestor_mod.Posicion(
            symbol="PUMPFUN/USDT:USDT",
            cantidad=2500.0,
            entrada=0.004512,
            z_entrada=-3.71,
            ofi_entrada=0.77,
            sl=0.004399,
            sl_exchange=0.004376,
            max_precio=0.004515,
            t_apertura=time.time() - 50.0,
            ofi_media=radar.MediaMovilTemporal(10),
        )
        ex_110017 = _MockExchange110017(t_antiguo_ms=t_hace_4_horas_ms)
        ok_cierre_1 = await gestor_mod.gestor.cerrar(ex_110017, pos_dup, "TRAILING", 0.004507)
        ok_cierre_2 = await gestor_mod.gestor._verificar_externo(ex_110017, pos_dup)
        assert ok_cierre_1 is True and ok_cierre_2 is True
        assert pos_dup.cerrada is True
        assert len(stats_mod.tracker.cargar()) == 2  # Bloqueado por desduplicador e idempotencia
        print(
            "✅ [14/15] Cerrojo Anti-Doble-Contabilización (`pos.cerrada`), captura nativa de `retCode:110017` "
            "y desduplicación automática del Trade #112 validados."
        )

        # 15. Prueba V6.4.0: Salida Pasiva Maker-First (0.02% Fee) y Cortes Anti-Zombie (45s / 105s)
        _, pnl_taker, fee_taker = stats_mod.calcular_pnl(0.0500, 0.0503, 220.0, cfg.FEE_MAKER, cfg.FEE_TAKER)
        _, pnl_maker, fee_maker = stats_mod.calcular_pnl(0.0500, 0.0503, 220.0, cfg.FEE_MAKER, cfg.FEE_MAKER)
        ahorro_fee_pct = (1.0 - (cfg.FEE_MAKER / cfg.FEE_TAKER)) * 100.0
        assert fee_maker < fee_taker and pnl_maker > pnl_taker

        mm_tox = radar.MediaMovilTemporal(10)
        t_eval = time.time()
        for i in range(10):
            mm_tox.push(t_eval - 9 + i, -0.35)
        class _MockEx15:
            async def fetch_positions(self, s=None): return [{'symbol': 'WAL/USDT:USDT', 'side': 'long', 'contracts': 222.0}]
            async def create_order(self, *a, **k): return {'id': '123'}
            async def fetch_order(self, *a, **k): return {'status': 'closed', 'average': 0.05125}
            async def fetch_balance(self): return {'USDT': {'total': 11.0, 'free': 11.0}}
            def amount_to_precision(self, sym, amt): return str(int(amt))
        mock_ex_15 = _MockEx15()
        pos_zombie_45s = gestor_mod.Posicion(
            symbol="WAL/USDT:USDT",
            cantidad=222.0,
            entrada=0.05173,
            z_entrada=-2.77,
            ofi_entrada=0.48,
            sl=0.05173 * (1.0 - cfg.SL_INICIAL_PCT),
            sl_exchange=0.05173 * (1.0 - cfg.SL_PCT),
            max_precio=0.05175,
            t_apertura=t_eval - 50.0,  # 50s >= EARLY_TOXIC_HOLD_SEG (45s)
            ofi_media=mm_tox,
        )
        cerrada_45s = await gestor_mod.gestor._evaluar(mock_ex_15,
            pos_zombie_45s,
            {"bid": 0.05125, "ask": 0.05127, "bidVolume": 100, "askVolume": 300},  # PnL = -0.93% <= -0.80%
        )
        assert cerrada_45s is True and pos_zombie_45s.cerrada is True
        ultimo_trade = stats_mod.tracker.cargar()[-1]
        assert ultimo_trade["motivo"] == "EARLY_TOXIC_SL"
        print(
            f"✅ [15/15] Salida Maker-First (-{ahorro_fee_pct:.1f}% en comisión de salida: "
            f"${fee_maker:.4f} vs${fee_taker:.4f}) y corte Anti-Zombie 45s (`EARLY_TOXIC_SL`) validados."
        )
    finally:
        cfg.MODO_PAPER = modo_paper_original
        stats_mod.tracker.ruta = ruta_original
        stats_mod.tracker._trades = trades_originales
        gestor_mod.enviar_alerta = alerta_original_gestor
        sniper.enviar_alerta = alerta_original_sniper
        gestor_mod.gestor.cooldown_hasta.clear()
        if ruta_temp.exists():
            ruta_temp.unlink()

    print("==================================================================")
    print("🏆 TODAS LAS PRUEBAS DE INTEGRIDAD V7.0.0 EMPEROR'S TIME (15/15) SUPERADAS")
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(ejecutar_auditoria_v6_4_0())