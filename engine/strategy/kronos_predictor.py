"""
Kronos foundation model wrapper (K-line forecasting)
============================================================
shiyu-coder/Kronos — 캔들스틱 foundation model (MIT 라이선스).
Pre-trained on 12B+ K-lines from 45 exchanges. Zero-shot 예측 가능.

이 모듈:
- HuggingFace에서 모델 lazy load (~/.cache/huggingface/)
- OHLCV → 미래 N봉 예측 (open/high/low/close/volume)
- 예측 → 매수/매도 시그널 변환
- ml_predict와 동일 인터페이스 (run_backtest 호환)

지원 모델:
- kronos_mini  (4.1M, 컨텍스트 2048, ~16MB) — CPU 친화
- kronos_small (24.7M, 컨텍스트 512, ~100MB) — CPU OK
- kronos_base  (102M, 컨텍스트 512, ~410MB) — GPU 권장

한계:
1. 첫 사용 시 HuggingFace 다운로드 필요 (~16~410MB)
2. transformers / huggingface_hub 패키지 필요 (선택 dependency)
3. base+는 CPU에서 추론 5~30초/예측 → 백테스트 매우 느림
4. 한국 시장 데이터 비중 적음 → fine-tuning 권장 (zero-shot은 미국/중국 우수)
5. sample_count로 비결정적 — 백테스트 재현성 위해 seed 필요
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Kronos repo를 PYTHONPATH에 자동 추가 (설치되어 있으면)
_KRONOS_REPO = Path.home() / ".jiqt" / "kronos" / "Kronos"
if _KRONOS_REPO.exists() and str(_KRONOS_REPO) not in sys.path:
    sys.path.insert(0, str(_KRONOS_REPO))

# ── Optional dependencies ────────────────────────────────────
_KRONOS_AVAILABLE = False
_KRONOS_IMPORT_ERROR = None
try:
    import torch
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False


def _try_import_kronos():
    """Kronos 코드 import 시도. 실패 시 (False, error_msg)."""
    global _KRONOS_AVAILABLE, _KRONOS_IMPORT_ERROR
    if _KRONOS_AVAILABLE:
        return True, None
    if _KRONOS_IMPORT_ERROR is not None:
        return False, _KRONOS_IMPORT_ERROR
    try:
        import transformers  # noqa
        import huggingface_hub  # noqa
        import einops  # noqa
    except ImportError as e:
        _KRONOS_IMPORT_ERROR = (
            f"필수 패키지 미설치: {e}. "
            "pip install transformers huggingface_hub einops 후 재시도")
        return False, _KRONOS_IMPORT_ERROR
    # Kronos 모델 코드 (shiyu-coder/Kronos의 model.py) 필요.
    # 별도 설치 옵션 — pip install로는 안 받음, 사용자가 clone 또는 우리가 minimal port
    # 일단 transformers AutoModelForCausalLM으로 시도하되 실패 시 안내
    _KRONOS_AVAILABLE = True
    return True, None


# 모델 메타
KRONOS_MODELS = {
    "kronos_mini": {
        "model_id":     "NeoQuasar/Kronos-mini",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-2k",
        "max_context":  2048,
        "size_mb":      16,
        "params":       "4.1M",
        "label":        "Kronos · mini (4M, CPU)",
        "desc":         "Foundation model 작은 버전 — CPU 친화, 빠른 추론",
    },
    "kronos_small": {
        "model_id":     "NeoQuasar/Kronos-small",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context":  512,
        "size_mb":      100,
        "params":       "24.7M",
        "label":        "Kronos · small (25M, CPU 권장)",
        "desc":         "균형 잡힌 성능 — CPU에서도 1~3초/예측",
    },
    "kronos_base": {
        "model_id":     "NeoQuasar/Kronos-base",
        "tokenizer_id": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context":  512,
        "size_mb":      410,
        "params":       "102.3M",
        "label":        "Kronos · base (102M, GPU 권장)",
        "desc":         "고성능 — GPU 권장, CPU에서 20초+/예측",
    },
}


# ── 모델 캐시 ────────────────────────────────────────────────
_MODEL_CACHE: Dict[str, Any] = {}


def is_available() -> Tuple[bool, Optional[str]]:
    """Kronos 사용 가능 여부 + 에러 메시지."""
    return _try_import_kronos()


def _hf_local_path(repo_id: str) -> Path:
    """HF repo_id → JIQT 로컬 경로 (Windows symlink 회피용)."""
    return Path.home() / ".jiqt" / "kronos" / "hf_models" / repo_id.replace("/", "__")


def check_model_downloaded(model_key: str) -> Dict[str, Any]:
    """모델이 다운로드 됐는지 확인 (로컬 경로 우선, 사이즈 검증).

    부분 다운로드(예상 사이즈 50% 미만)는 미다운로드로 처리.
    """
    if model_key not in KRONOS_MODELS:
        return {"downloaded": False, "error": "unknown model key"}
    meta = KRONOS_MODELS[model_key]
    expected_mb = float(meta.get("size_mb") or 0)
    threshold = expected_mb * 0.5 if expected_mb else 1.0
    # 1) JIQT 로컬 경로 우선
    local_model = _hf_local_path(meta["model_id"])
    if local_model.exists():
        has_weights = any(local_model.rglob("*.safetensors")) or \
                      any(local_model.rglob("*.bin"))
        if has_weights:
            size_b = sum(p.stat().st_size for p in local_model.rglob("*")
                          if p.is_file())
            size_mb = round(size_b / 1e6, 1)
            if size_mb >= threshold:
                return {"downloaded": True, "size_mb": size_mb,
                        "model_id": meta["model_id"],
                        "local_path": str(local_model)}
            return {"downloaded": False, "partial": True,
                    "size_mb": size_mb, "expected_mb": expected_mb,
                    "model_id": meta["model_id"],
                    "msg": f"부분 다운로드 ({size_mb}/{expected_mb}MB)"}
    # 2) HF 캐시 fallback (옛 다운로드) — 사이즈도 검증
    try:
        from huggingface_hub import scan_cache_dir
        cache = scan_cache_dir()
        for repo in cache.repos:
            if repo.repo_id == meta["model_id"]:
                size_mb = round(repo.size_on_disk / 1e6, 1)
                if size_mb >= threshold:
                    return {"downloaded": True, "size_mb": size_mb,
                            "model_id": meta["model_id"]}
                return {"downloaded": False, "partial": True,
                        "size_mb": size_mb, "expected_mb": expected_mb,
                        "model_id": meta["model_id"],
                        "msg": f"부분 캐시 ({size_mb}/{expected_mb}MB)"}
    except Exception:
        pass
    return {"downloaded": False, "model_id": meta["model_id"]}


def get_status() -> Dict[str, Any]:
    """전체 Kronos 상태 — UI 표시용."""
    ok, err = is_available()
    out = {
        "available":     ok,
        "torch_available": _HAS_TORCH,
        "cuda_available": False,
        "import_error":  err,
        "repo_installed": _KRONOS_REPO.exists() and (
            (_KRONOS_REPO / "model" / "__init__.py").exists() or
            (_KRONOS_REPO / "model.py").exists() or
            any(_KRONOS_REPO.glob("model*.py"))),
        "repo_path":     str(_KRONOS_REPO),
        "models":        {},
    }
    if _HAS_TORCH:
        try:
            out["cuda_available"] = bool(torch.cuda.is_available())
            if out["cuda_available"]:
                out["gpu_name"] = torch.cuda.get_device_name(0)
                out["gpu_memory_gb"] = round(
                    torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
        except Exception:
            pass
    # GPU 기반 권장 모델
    if out["cuda_available"]:
        mem = out.get("gpu_memory_gb", 0)
        if mem >= 8:
            out["recommended"] = "kronos_base"
            out["speed_note"] = f"⚡ GPU {mem}GB — base 권장 (1초/예측)"
        elif mem >= 4:
            out["recommended"] = "kronos_small"
            out["speed_note"] = f"⚡ GPU {mem}GB — small 권장"
        else:
            out["recommended"] = "kronos_mini"
            out["speed_note"] = f"⚡ GPU {mem}GB — mini 권장"
    else:
        out["recommended"] = "kronos_small"
        out["speed_note"] = "CPU 모드 — small 권장 (base는 매우 느림)"
    for k, meta in KRONOS_MODELS.items():
        chk = check_model_downloaded(k)
        out["models"][k] = {**meta, **chk}
    return out


# ── 핵심: 예측 ────────────────────────────────────────────────
def kronos_predict(ohlcv: pd.DataFrame,
                    model_key: str = "kronos_small",
                    lookback: int = 256,
                    pred_len: int = 5,
                    temperature: float = 1.0,
                    top_p: float = 0.9,
                    sample_count: int = 1,
                    device: Optional[str] = None,
                    seed: int = 42) -> Dict[str, Any]:
    """OHLCV → 미래 pred_len 봉 예측.

    ohlcv: pandas DataFrame with [open, high, low, close, volume] + DatetimeIndex
    lookback: 입력 컨텍스트 봉 수 (모델 max_context 이내)
    pred_len: 예측할 미래 봉 수
    """
    ok, err = is_available()
    if not ok:
        return {"ok": False, "error": err or "Kronos 불가"}
    if model_key not in KRONOS_MODELS:
        return {"ok": False, "error": f"unknown model {model_key}"}
    meta = KRONOS_MODELS[model_key]
    lookback = min(lookback, meta["max_context"])
    data_n = len(ohlcv)
    # 자동 lookback 축소 (데이터 부족 시 친절하게 줄임)
    auto_shrunk = False
    requested_lookback = lookback
    if data_n < lookback + 10:
        adj = max(64, data_n - 15)
        if adj < 64 or data_n < 80:
            return {"ok": False,
                    "error": (f"데이터 부족 (n={data_n} · 최소 80봉 필요). "
                                "더 긴 period_days 또는 다른 종목으로 시도하세요.")}
        lookback = adj
        auto_shrunk = True

    # 모델 로드 (캐시)
    cache_key = model_key
    if cache_key not in _MODEL_CACHE:
        load_r = _load_model(model_key, device=device)
        if not load_r.get("ok"):
            return load_r
        _MODEL_CACHE[cache_key] = load_r

    cached = _MODEL_CACHE[cache_key]
    predictor = cached.get("predictor")
    if predictor is None:
        return {"ok": False, "error": "predictor 로드 실패"}

    # 입력 구성
    try:
        x_df = ohlcv.iloc[-lookback:][["open", "high", "low", "close"]].copy()
        if "volume" in ohlcv.columns:
            x_df["volume"] = ohlcv["volume"].iloc[-lookback:]
        # 인덱스를 datetime으로 보장 (없으면 일봉 가정 + 합성)
        idx = ohlcv.index
        if not isinstance(idx, pd.DatetimeIndex):
            try:
                idx = pd.to_datetime(idx)
            except Exception:
                idx = pd.date_range(end=pd.Timestamp.now().normalize(),
                                     periods=len(ohlcv), freq="D")
        # 봉 간격 추정
        if len(idx) >= 2:
            freq = idx[-1] - idx[-2]
            if not isinstance(freq, pd.Timedelta) or freq == pd.Timedelta(0):
                freq = pd.Timedelta(days=1)
        else:
            freq = pd.Timedelta(days=1)
        # ⚠ Kronos는 .dt.minute/hour 등 Series accessor를 사용 → 반드시 pd.Series로
        x_timestamp = pd.Series(pd.to_datetime(idx[-lookback:]))
        y_timestamp = pd.Series(pd.date_range(
            start=idx[-1] + freq, periods=pred_len, freq=freq))
        # seed 고정 (재현성)
        if _HAS_TORCH:
            torch.manual_seed(seed)
            np.random.seed(seed)
        t0 = time.time()
        pred_df = predictor.predict(
            df=x_df, x_timestamp=x_timestamp, y_timestamp=y_timestamp,
            pred_len=pred_len, T=temperature, top_p=top_p,
            sample_count=sample_count)
        elapsed = time.time() - t0
        # 예측 → dict
        last_close = float(x_df["close"].iloc[-1])
        forecast = []
        for ts, row in pred_df.iterrows():
            forecast.append({
                "date":  ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open":  float(row.get("open", 0)),
                "high":  float(row.get("high", 0)),
                "low":   float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)) if "volume" in row else 0,
            })
        # 방향 + 예측 수익률
        end_close = forecast[-1]["close"] if forecast else last_close
        pred_return = (end_close / last_close - 1) if last_close > 0 else 0
        direction = ("📈 상승" if pred_return > 0.005
                      else "📉 하락" if pred_return < -0.005
                      else "➡️ 횡보")
        return {
            "ok":          True,
            "model":       model_key,
            "lookback":    lookback,
            "lookback_auto_shrunk": auto_shrunk,
            "requested_lookback":   requested_lookback,
            "data_n":      data_n,
            "pred_len":    pred_len,
            "device":      cached.get("device"),
            "elapsed_sec": round(elapsed, 2),
            "last_close":  last_close,
            "predicted_end_close": float(end_close),
            "predicted_return":    round(pred_return, 4),
            "direction":   direction,
            "forecast":    forecast,
        }
    except Exception as e:
        import traceback
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-500:]}


def _load_model(model_key: str,
                 device: Optional[str] = None) -> Dict[str, Any]:
    """HuggingFace에서 모델 + 토크나이저 로드.

    주의: shiyu-coder/Kronos의 model.py 코드가 필요함.
    옵션 1: pip install로 안 됨 → repo clone 필요
    옵션 2: 코드 minimal port (Kronos / KronosTokenizer / KronosPredictor 클래스)
    여기는 옵션 2 (lightweight stub) — model.py가 있으면 import 시도, 없으면 안내.
    """
    meta = KRONOS_MODELS[model_key]
    dev = device or ("cuda" if _HAS_TORCH and torch.cuda.is_available()
                      else "cpu")
    try:
        # 시도 1: 사용자가 Kronos를 PYTHONPATH에 추가했을 경우
        try:
            from model import Kronos, KronosTokenizer, KronosPredictor
        except ImportError:
            # 시도 2: shiyu-coder/Kronos repo가 ~/.jiqt/kronos/Kronos 에 clone돼 있을 경우
            kronos_repo = os.path.expanduser("~/.jiqt/kronos/Kronos")
            if os.path.isdir(kronos_repo):
                import sys as _sys
                if kronos_repo not in _sys.path:
                    _sys.path.insert(0, kronos_repo)
                from model import Kronos, KronosTokenizer, KronosPredictor
            else:
                return {"ok": False, "error":
                    "Kronos 모델 코드 미설치.\n"
                    "설치: git clone https://github.com/shiyu-coder/Kronos.git "
                    f"~/.jiqt/kronos/Kronos\n"
                    "그 후 첫 실행 시 HuggingFace 모델 자동 다운로드 (16~410MB)"}
        # 로컬 경로 우선 (Windows symlink 권한 회피로 받은 곳)
        local_tok = _hf_local_path(meta["tokenizer_id"])
        local_model = _hf_local_path(meta["model_id"])
        tok_src = str(local_tok) if local_tok.exists() and \
                  any(local_tok.rglob("*")) else meta["tokenizer_id"]
        model_src = str(local_model) if local_model.exists() and \
                    any(local_model.rglob("*")) else meta["model_id"]
        tokenizer = KronosTokenizer.from_pretrained(tok_src)
        model = Kronos.from_pretrained(model_src)
        predictor = KronosPredictor(model, tokenizer,
                                     max_context=meta["max_context"],
                                     device=dev)
        return {"ok": True, "predictor": predictor, "device": dev}
    except Exception as e:
        import traceback
        return {"ok": False,
                "error": f"모델 로드 실패: {type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-400:]}


# ════════════════════════════════════════════════════════════
#  시그널 변환 (ml_predict 인터페이스 호환)
# ════════════════════════════════════════════════════════════
def generate_kronos_signals(ohlcv: pd.DataFrame,
                             model_key: str = "kronos_small",
                             lookback: int = 256,
                             pred_len: int = 5,
                             buy_thresh: float = 0.01,
                             sell_thresh: float = -0.01,
                             slide_step: int = 5
                             ) -> Tuple[pd.Series, pd.Series]:
    """Kronos 예측 → 매수/매도 시그널.

    매 slide_step 봉마다 예측 → 예측 수익률 > buy_thresh → 매수
    < sell_thresh → 매도. slide_step=5 즉 5봉마다 재예측.

    백테스트가 느리므로 slide_step 크게 잡는 게 현실적.
    """
    n = len(ohlcv)
    idx = ohlcv.index
    entries = pd.Series(False, index=idx)
    exits = pd.Series(False, index=idx)
    if n < lookback + pred_len:
        return entries, exits
    # 슬라이딩 예측
    last_pred_return = 0
    for i in range(lookback, n - pred_len, slide_step):
        sub = ohlcv.iloc[:i]
        try:
            r = kronos_predict(sub, model_key=model_key,
                                lookback=lookback, pred_len=pred_len,
                                sample_count=1)
            if not r.get("ok"):
                continue
            pr = r.get("predicted_return", 0)
            # 시그널: 임계 교차
            if pr > buy_thresh and last_pred_return <= buy_thresh:
                entries.iloc[i] = True
            elif pr < sell_thresh and last_pred_return >= sell_thresh:
                exits.iloc[i] = True
            last_pred_return = pr
        except Exception:
            continue
    return entries, exits
