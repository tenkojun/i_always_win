# -*- mode: python ; coding: utf-8 -*-
#
#  Plutus  ―  PyInstaller 스펙
#  ==================================
#  빌드:
#      pip install pyinstaller
#      python tools/make_version_info.py     # 버전 리소스 갱신
#      pyinstaller app.spec --noconfirm
#  결과:
#      dist/Plutus/Plutus.exe
#
#  콘솔 창을 띄우지 않는다(console=False). 그래서 진단 로그는
#  화면이 아니라 .data/logs/app.log 로 간다 (run_desktop.py 참조).
#
#  (윈도우 .exe 는 윈도우에서 빌드해야 한다.)

import os
import sys

sys.path.insert(0, os.getcwd())
from version import APP_NAME  # noqa: E402

# 윈도우 버전 리소스는 version.py 에서 파생되는 생성물이라 저장소에 두지
# 않는다. 없으면 여기서 만든다 — 스펙만 보고 빌드해도 되게.
if not os.path.exists('version_info.txt'):
    import runpy
    runpy.run_path(os.path.join('tools', 'make_version_info.py'),
                   run_name='__main__')

block_cipher = None

datas = [
    ('webapp/static', 'webapp/static'),
    ('assets', 'assets'),          # 앱 아이콘
    ('docs', 'docs'),              # 외부 접근 가이드 등 사용자 문서
    ('version.py', '.'),           # 버전 단일 소스
    # auth-worker/ 는 사용자 측 배포 도구라 EXE 에 넣지 않는다
]

hiddenimports = [
    # ── 앱 ──────────────────────────────────────────────────
    'version',
    'webapp', 'webapp.server',
    'engine', 'engine.paths', 'engine.console',

    # ── 정밀 분석 엔진 (동적 임포트가 많아 전부 명시) ───────
    'engine.jiqtx',
    'engine.jiqtx.config', 'engine.jiqtx.statcore', 'engine.jiqtx.micro',
    'engine.jiqtx.vol', 'engine.jiqtx.regime', 'engine.jiqtx.simulate',
    'engine.jiqtx.taxonomy', 'engine.jiqtx.factors', 'engine.jiqtx.equity',
    'engine.jiqtx.ml', 'engine.jiqtx.options', 'engine.jiqtx.risk',
    'engine.jiqtx.thesis', 'engine.jiqtx.trade', 'engine.jiqtx.agents',
    'engine.jiqtx.panel', 'engine.jiqtx.charts',
    'engine.jiqtx.dynamic_report', 'engine.jiqtx.portfolio',
    'engine.jiqtx.portfolio_report', 'engine.jiqtx.data',
    'engine.jiqtx.ledger', 'engine.jiqtx.pipeline', 'engine.jiqtx.replay',
    'engine.jiqtx.report',
    # v2.13~2.14 신규 — 용어사전 · 다지평 · 거시보드 · 보고서 테마
    'engine.jiqtx.glossary', 'engine.jiqtx.horizons',
    'engine.jiqtx.macro_board', 'engine.jiqtx.report_theme',

    # ── 앱 지원 모듈 ────────────────────────────────────────
    # v2.15.0 에서 구엔진(analysis/institutional/risk/factor/volatility/
    # orderflow/ml/signal_engine/explain/report)을 제거했다. jiqtx 가
    # 전부 대체하므로 여기서도 뺀다.
    'engine.data.loader', 'engine.data.keyconfig',
    'engine.portfolio', 'engine.awareness', 'engine.community',
    'engine.llm', 'engine.auth', 'engine.auth.middleware',
    'engine.auth_remote', 'engine.cloud', 'engine.cloud.tunnel',
    'engine.cloud.supervisor', 'engine.cloud.named_tunnel',
    'engine.jobs', 'engine.analyze_history',

    # ── 과학 스택 (PyInstaller 가 자주 놓치는 것들) ─────────
    'numpy', 'pandas', 'scipy', 'scipy.special.cython_special',
    'scipy.optimize', 'scipy.stats',
    'sklearn', 'sklearn.utils._typedefs',
    'sklearn.neighbors._partition_nodes', 'sklearn.cluster',
    'sklearn.ensemble', 'sklearn.linear_model', 'sklearn.isotonic',
    # statsmodels / arch / hmmlearn 은 설치돼 있지 않고 jiqtx 는 이들을
    # 쓰지 않는다 (GJR-GARCH-t·HAR·Jump Model 을 scipy 로 직접 구현).
    # 명시해 두면 매 빌드마다 "Hidden import not found" 경고만 남는다.

    # ── 웹 / 데스크톱 ───────────────────────────────────────
    'flask', 'jinja2', 'werkzeug', 'webview',
    'yfinance', 'requests', 'requests.adapters',
    'psutil',

    # ── stdlib 명시 ─────────────────────────────────────────
    'sqlite3', 'secrets', 'hashlib', 'queue', 'threading',
    'urllib', 'urllib.request', 'urllib.error',
    'base64', 'io', 'json', 'datetime', 'dataclasses',
]

# v2.2.0 에서 제거한 기능이 끌고 오던 무거운 의존성.
# 남겨 두면 EXE 만 수백 MB 커진다.
excludes = [
    'tensorflow', 'vectorbt', 'numba', 'llvmlite', 'shap',
    'pyarrow',                     # 캐시를 pickle 로 바꿔 불필요 (-81MB)
    'transformers', 'huggingface_hub', 'einops',
    'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    'notebook', 'IPython', 'jupyter',
    # v2.15.0 에서 구엔진(engine/report/plotter.py)을 지우면서 matplotlib
    # 계열이 필요 없어졌다. 보고서 차트는 전부 인라인 SVG 를 문자열로
    # 만들기 때문에 그리기 라이브러리가 아예 없어도 된다.
    # 그런데도 딸려 들어와 26MB 를 먹고 있었다 (matplotlib 15M + PIL 11M).
    'matplotlib', 'PIL', 'Pillow',
]

a = Analysis(
    ['run_desktop.py'],
    pathex=[os.getcwd()],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Plutus',
    console=False,                 # ← 콘솔 창 없음. 로그는 .data/logs/
    icon='assets/app.ico',
    version='version_info.txt',    # 파일 속성에 이름·버전·개발자
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True,
    name='Plutus',
)
