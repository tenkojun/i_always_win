# -*- mode: python ; coding: utf-8 -*-
#
#  QUANT TERMINAL  ―  PyInstaller 스펙
#  ===================================
#  윈도우에서 빌드:
#      pip install pyinstaller
#      pyinstaller app.spec
#  결과:  dist/QuantTerminal/QuantTerminal.exe
#
#  (윈도우 .exe 는 반드시 윈도우에서 빌드해야 합니다.
#   맥은 같은 방식으로 .app, 리눅스는 실행파일이 나옵니다.)

import os
block_cipher = None

datas = [
    ('webapp/static', 'webapp/static'),
    ('engine/report/assets', 'engine/report/assets'),
    ('docs', 'docs'),   # 정식 Tunnel 가이드 등 사용자 문서
    # auth-worker/ 는 사용자 측 배포 도구이므로 EXE에 포함 X
    # build/dist 는 PyInstaller가 자동 제외
]

hiddenimports = [
    # ── Flask 서버 + 기존 엔진 ──────────────────────────────
    'webapp', 'webapp.server', 'engine',
    'yfinance', 'sklearn', 'sklearn.utils._typedefs',
    'sklearn.neighbors._partition_nodes', 'sklearn.cluster',
    'scipy.special.cython_special',
    'statsmodels', 'arch', 'hmmlearn', 'jinja2', 'flask',
    'matplotlib', 'matplotlib.backends.backend_agg', 'matplotlib.pyplot',
    'mpl_toolkits.mplot3d',  # 3D param surface
    # ── 기존 엔진 (2026-05 추가분) ─────────────────────────
    'engine.auth', 'engine.auth.store', 'engine.auth.middleware',
    'engine.auth.security', 'engine.auth.qr_token', 'engine.auth.prefs',
    'engine.jobs', 'engine.jobs.manager',
    'engine.llm', 'engine.llm.hardware', 'engine.llm.ollama_setup',
    'engine.llm.client', 'engine.llm.news_reasoner',
    'engine.llm.context_builder', 'engine.llm.websearch',
    'engine.llm.text_utils', 'engine.llm.claude_client',
    'engine.llm.chat_store',
    'engine.awareness', 'engine.awareness.gdelt',
    'engine.awareness.event_map', 'engine.awareness.alert_engine',
    'engine.awareness.history',
    'engine.cloud', 'engine.cloud.tunnel', 'engine.cloud.pc_id',
    'engine.portfolio.holdings_store',
    'engine.strategy', 'engine.strategy.vbt_runner',
    'engine.strategy.signal_filter', 'engine.strategy.regime_auto',
    'engine.strategy.strategies_ext', 'engine.strategy.mega_grid',
    'engine.strategy.active_store',
    'engine.data.news_feeds', 'engine.data.news_summary',
    # KIS 한국투자증권 OpenAPI (REST + WebSocket + Provider)
    'engine.data.sources.kis', 'engine.data.sources.kis_provider',
    'engine.data.sources.kis_websocket',
    # 마이크로구조 분석 (틱/호가 기반)
    'engine.microstructure',
    'engine.microstructure.speed_of_tape',
    'engine.microstructure.book_imbalance',
    'engine.microstructure.sweep_detect',
    'engine.microstructure.real_cvd',
    'engine.microstructure.trade_cluster',
    # Paper Trading 엔진 + 리스크 한도 + 자동매매
    'engine.trading', 'engine.trading.paper_trading',
    'engine.trading.risk_limits', 'engine.trading.auto_trader',
    # Tear Sheet PDF
    'engine.report.tearsheet',
    # 마이크로구조 전략 + Walk-Forward 일반화 + Kronos
    'engine.strategy.micro_strategies',
    'engine.strategy.walk_forward',
    'engine.strategy.kronos_predictor',
    'engine.strategy.kronos_installer',
    # WebSocket 클라이언트 의존성
    'websockets',
    'engine.data.analyst', 'engine.data.keyconfig',
    'engine.institutional.precision',
    # ── 2026-05-24 신규 모듈 (배포 직전) ────────────────────
    # C6/C9: 통계 + 분석 이력
    'engine.analyze_history',
    # C12: SSE + 실시간 워커
    'engine.eventbus', 'engine.realtime_worker',
    # 중앙 인증 + A6
    'engine.auth_remote', 'engine.auth_remote.client',
    # C4: 커뮤니티
    'engine.community', 'engine.community.store',
    # C11: Cloudflare Named Tunnel
    'engine.cloud.cf_api', 'engine.cloud.named_tunnel',
    # 벡터화 전략 엔진 + IndicatorFactory + 풍부 메트릭 + AUTO + 컬렉션 + 리서치
    'engine.strategy.vbt_vectorized', 'engine.strategy.vbt_indicators',
    'engine.strategy.pf_stats', 'engine.strategy.auto_analyze',
    'engine.strategy.collection_store', 'engine.strategy.research_ext',
    'engine.strategy.vbt_pro', 'engine.strategy.multi_asset',
    'engine.strategy.multi_strategy', 'engine.strategy.factor_attribution',
    'engine.strategy.portfolio_optimizer',
    'engine.backtest.market_impact',
    # Orderflow + PEAD 통합 엔진
    'engine.orderflow_pead', 'engine.orderflow_pead.loader',
    'engine.orderflow_pead.indicators', 'engine.orderflow_pead.orderflow',
    'engine.orderflow_pead.pead', 'engine.orderflow_pead.strategy',
    'engine.orderflow_pead.backtester', 'engine.orderflow_pead.optimizer',
    'engine.orderflow_pead.plotter', 'engine.orderflow_pead.research',
    'engine.orderflow_pead.main',
    # ── vectorbt + numba (numba가 동적 import 많음) ────────
    'vectorbt', 'vectorbt.indicators', 'vectorbt.signals',
    'vectorbt.generic', 'vectorbt.portfolio', 'vectorbt.data',
    'numba', 'numba.core', 'numba.typed', 'numba.cpython',
    'numba.np', 'numba.core.types', 'llvmlite',
    # ── D#10: PyTorch ML 예측 (LSTM/GRU/Transformer) ──────
    'torch', 'torch.nn', 'torch.nn.functional', 'torch.optim',
    'torch.cuda', 'torch.backends.cudnn',
    'engine.ml', 'engine.ml.models', 'engine.ml.features',
    'engine.strategy.ml_predict',
    # ── stdlib 명시 (PyInstaller 누락 방지) ────────────────
    'sqlite3', 'secrets', 'hashlib', 'queue', 'threading',
    'urllib', 'urllib.request', 'urllib.error',
    'base64', 'io', 'json', 'datetime',
    # ── 외부 라이브러리 ────────────────────────────────────
    'requests', 'requests.adapters',
    'psutil', 'psutil._psutil_windows',
]

a = Analysis(
    ['run_desktop.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # PyTorch 재포함 — LSTM/GRU/Transformer 가격예측 활용 (D#10 GPU 가속)
    # tensorflow는 미사용 (제외 유지)
    excludes=['tensorflow'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='QuantTerminal',
    console=True,          # 콘솔 로그 보이게(문제 진단 쉬움)
    icon=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True,
    name='QuantTerminal',
)
