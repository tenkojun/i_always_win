"""
한국투자증권 (KIS) OpenAPI REST 클라이언트
============================================================
공식 GitHub: https://github.com/koreainvestment/open-trading-api
API 포털: https://apiportal.koreainvestment.com/

지원:
- OAuth 토큰 발급 (실전/모의 별도)
- 국내 주식 시세 (현재가, 일/분봉, 호가)
- 해외 주식 시세 (NASDAQ/NYSE)
- 종목 검색
- 잔고/계좌 조회 (P4 페이퍼/실거래용)

키 저장: .data/kis_keys.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


# ── 키 저장 경로 ──────────────────────────────────────────────
from engine.paths import DATA_DIR as _HOME, ensure_dirs as _ensure_dirs
_ensure_dirs()
_KEY_PATH = _HOME / "kis_keys.json"
_TOKEN_PATH = _HOME / "kis_tokens.json"

# 엔드포인트
_BASE_REAL = "https://openapi.koreainvestment.com:9443"
_BASE_VTS  = "https://openapivts.koreainvestment.com:29443"  # 모의


def _now() -> str:
    return dt.datetime.utcnow().isoformat()


# ════════════════════════════════════════════════════════════
#  키 관리
# ════════════════════════════════════════════════════════════
def load_keys() -> Dict[str, Any]:
    """저장된 KIS 키 dict 반환. 없으면 빈 dict."""
    if not _KEY_PATH.exists():
        return {}
    try:
        return json.loads(_KEY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_keys(keys: Dict[str, Any]) -> Dict[str, Any]:
    """키 저장. 구조:
       {real: {app_key, app_secret, account_no?}, vts: {...}}
    """
    if not isinstance(keys, dict):
        return {"ok": False, "error": "keys는 dict"}
    try:
        _KEY_PATH.write_text(
            json.dumps(keys, ensure_ascii=False, indent=2), encoding="utf-8")
        # 파일 권한 600 (Windows는 무시)
        try:
            os.chmod(_KEY_PATH, 0o600)
        except Exception:
            pass
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _load_tokens() -> Dict[str, Any]:
    if not _TOKEN_PATH.exists():
        return {}
    try:
        return json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_tokens(tokens: Dict[str, Any]) -> None:
    try:
        _TOKEN_PATH.write_text(
            json.dumps(tokens, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(_TOKEN_PATH, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def has_keys(mode: str = "real") -> bool:
    """mode: 'real' or 'vts' (모의)"""
    k = load_keys().get(mode, {})
    return bool(k.get("app_key") and k.get("app_secret"))


# ════════════════════════════════════════════════════════════
#  OAuth 토큰 발급/캐시
# ════════════════════════════════════════════════════════════
def _base_url(mode: str) -> str:
    return _BASE_VTS if mode == "vts" else _BASE_REAL


def issue_token(mode: str = "real", force: bool = False) -> Dict[str, Any]:
    """OAuth 토큰 발급. 24시간 유효 — 캐시 활용.

    Returns: {ok, access_token, expires_at, ...}
    """
    keys = load_keys()
    k = keys.get(mode, {})
    if not k.get("app_key") or not k.get("app_secret"):
        return {"ok": False, "error": f"{mode} 앱키/시크릿 미설정"}

    # 캐시된 토큰
    if not force:
        tokens = _load_tokens()
        cached = tokens.get(mode)
        if cached and cached.get("expires_at"):
            try:
                exp = dt.datetime.fromisoformat(cached["expires_at"])
                # 만료 30분 전까지 사용
                if exp > dt.datetime.utcnow() + dt.timedelta(minutes=30):
                    return {"ok": True, **cached, "cached": True}
            except Exception:
                pass

    url = _base_url(mode) + "/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": k["app_key"],
        "appsecret": k["app_secret"],
    }
    try:
        r = requests.post(url, json=body, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}",
                    "response": r.text[:300]}
        d = r.json()
        tok = d.get("access_token")
        if not tok:
            return {"ok": False, "error": "access_token 없음",
                    "response": d}
        expires_in = int(d.get("expires_in", 86400))
        expires_at = (dt.datetime.utcnow() +
                       dt.timedelta(seconds=expires_in)).isoformat()
        # 캐시
        tokens = _load_tokens()
        tokens[mode] = {
            "access_token": tok,
            "token_type": d.get("token_type", "Bearer"),
            "expires_in": expires_in,
            "expires_at": expires_at,
            "issued_at": _now(),
        }
        _save_tokens(tokens)
        return {"ok": True, "access_token": tok, "expires_at": expires_at,
                "cached": False}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _auth_header(mode: str) -> Optional[Dict[str, str]]:
    keys = load_keys().get(mode, {})
    tok = issue_token(mode)
    if not tok.get("ok"):
        return None
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {tok['access_token']}",
        "appkey":    keys["app_key"],
        "appsecret": keys["app_secret"],
    }


# ════════════════════════════════════════════════════════════
#  API 호출 헬퍼
# ════════════════════════════════════════════════════════════
def _call(path: str, params: Dict[str, Any], tr_id: str,
          mode: str = "real", method: str = "GET") -> Dict[str, Any]:
    """공통 API 호출 — tr_id별 헤더 + JSON 응답."""
    header = _auth_header(mode)
    if header is None:
        return {"ok": False, "error": "인증 실패 — 키 확인"}
    header["tr_id"] = tr_id
    url = _base_url(mode) + path
    try:
        if method == "GET":
            r = requests.get(url, headers=header, params=params, timeout=10)
        else:
            r = requests.post(url, headers=header, json=params, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}",
                    "response": r.text[:300]}
        d = r.json()
        # rt_cd 0 = 성공
        if d.get("rt_cd") and d.get("rt_cd") != "0":
            return {"ok": False, "error": d.get("msg1", "?"),
                    "rt_cd": d.get("rt_cd"), "response": d}
        return {"ok": True, **d}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ════════════════════════════════════════════════════════════
#  국내 주식 (KOSPI/KOSDAQ)
# ════════════════════════════════════════════════════════════
def quote_kr(ticker: str, mode: str = "real") -> Dict[str, Any]:
    """국내 주식 현재가. ticker = '005930' (삼성전자) 등 6자리.

    응답 예: {ok, price, change, change_pct, volume, high, low, open, prev_close}
    """
    # tr_id: 실전 FHKST01010100 / 모의 FHKST01010100 (동일)
    tr = "FHKST01010100"
    r = _call("/uapi/domestic-stock/v1/quotations/inquire-price",
                 {"FID_COND_MRKT_DIV_CODE": "J",
                  "FID_INPUT_ISCD": ticker},
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    out = r.get("output", {}) or {}
    try:
        price = float(out.get("stck_prpr", 0))
        prev = float(out.get("stck_sdpr", 0))
        return {
            "ok": True, "ticker": ticker, "market": "KRX",
            "price":     price,
            "change":    float(out.get("prdy_vrss", 0)),
            "change_pct": float(out.get("prdy_ctrt", 0)),
            "open":      float(out.get("stck_oprc", 0)),
            "high":      float(out.get("stck_hgpr", 0)),
            "low":       float(out.get("stck_lwpr", 0)),
            "prev_close": prev,
            "volume":    int(float(out.get("acml_vol", 0))),
            "market_cap": int(float(out.get("hts_avls", 0)) * 1e8) if out.get("hts_avls") else None,
            "name":      out.get("hts_kor_isnm") or "",
            "raw":       out,
        }
    except Exception as e:
        return {"ok": False, "error": f"파싱 실패: {e}", "raw": out}


def daily_bars_kr(ticker: str, days: int = 100,
                   mode: str = "real") -> Dict[str, Any]:
    """국내 주식 일봉 (최근 N일)."""
    tr = "FHKST01010400"
    end = dt.datetime.now().strftime("%Y%m%d")
    r = _call("/uapi/domestic-stock/v1/quotations/inquire-daily-price",
                 {"FID_COND_MRKT_DIV_CODE": "J",
                  "FID_INPUT_ISCD": ticker,
                  "FID_PERIOD_DIV_CODE": "D",
                  "FID_ORG_ADJ_PRC": "0"},
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    out = r.get("output", []) or []
    bars = []
    for row in out[:days]:
        try:
            bars.append({
                "date":  row.get("stck_bsop_date"),
                "open":  float(row.get("stck_oprc", 0)),
                "high":  float(row.get("stck_hgpr", 0)),
                "low":   float(row.get("stck_lwpr", 0)),
                "close": float(row.get("stck_clpr", 0)),
                "volume": int(float(row.get("acml_vol", 0))),
            })
        except Exception:
            continue
    return {"ok": True, "ticker": ticker, "n_bars": len(bars), "bars": bars}


def orderbook_kr(ticker: str, mode: str = "real") -> Dict[str, Any]:
    """국내 호가 10단계 + 잔량 (실시간 한 스냅샷)."""
    tr = "FHKST01010200"
    r = _call("/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                 {"FID_COND_MRKT_DIV_CODE": "J",
                  "FID_INPUT_ISCD": ticker},
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    o1 = (r.get("output1") or {})
    bids, asks = [], []
    for i in range(1, 11):
        try:
            bid_p = float(o1.get(f"bidp{i}", 0))
            bid_v = int(float(o1.get(f"bidp_rsqn{i}", 0)))
            ask_p = float(o1.get(f"askp{i}", 0))
            ask_v = int(float(o1.get(f"askp_rsqn{i}", 0)))
            if bid_p > 0: bids.append({"price": bid_p, "size": bid_v})
            if ask_p > 0: asks.append({"price": ask_p, "size": ask_v})
        except Exception:
            continue
    return {"ok": True, "ticker": ticker,
            "bids": bids, "asks": asks,
            "total_bid_size": sum(b["size"] for b in bids),
            "total_ask_size": sum(a["size"] for a in asks),
            "raw": o1}


# ════════════════════════════════════════════════════════════
#  해외 주식 (NASDAQ/NYSE)
# ════════════════════════════════════════════════════════════
_OVERSEA_EXCH = {
    "NASD": "NASDAQ", "NAS": "NASDAQ",
    "NYSE": "NYSE",   "NYS": "NYSE",
    "AMEX": "AMEX",
}


def quote_us(ticker: str, exchange: str = "NASD",
              mode: str = "real") -> Dict[str, Any]:
    """미국 주식 현재가. ticker = 'AAPL' 등.
    exchange: NASD(나스닥) / NYSE / AMEX
    """
    tr = "HHDFS00000300"
    r = _call("/uapi/overseas-price/v1/quotations/price",
                 {"AUTH": "",
                  "EXCD": exchange,
                  "SYMB": ticker.upper()},
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    out = r.get("output", {}) or {}
    try:
        return {
            "ok": True, "ticker": ticker.upper(),
            "market": _OVERSEA_EXCH.get(exchange, exchange),
            "price":     float(out.get("last", 0)),
            "change":    float(out.get("diff", 0)),
            "change_pct": float(out.get("rate", 0)),
            "open":      float(out.get("open", 0)),
            "high":      float(out.get("high", 0)),
            "low":       float(out.get("low", 0)),
            "prev_close": float(out.get("base", 0)),
            "volume":    int(float(out.get("tvol", 0))),
            "raw":       out,
        }
    except Exception as e:
        return {"ok": False, "error": f"파싱 실패: {e}", "raw": out}


# ════════════════════════════════════════════════════════════
#  계좌 (실거래/모의)
# ════════════════════════════════════════════════════════════
def account_balance(mode: str = "vts") -> Dict[str, Any]:
    """계좌 잔고 (모의 권장).
    keys[mode]['account_no'] (8자리 + 02) 필요"""
    keys = load_keys().get(mode, {})
    acno = keys.get("account_no")
    if not acno or "-" not in acno:
        return {"ok": False,
                "error": "account_no 미설정 (예: 12345678-01)"}
    cano, prdt = acno.split("-")
    # 실전 TTTC8434R / 모의 VTTC8434R
    tr = "VTTC8434R" if mode == "vts" else "TTTC8434R"
    r = _call("/uapi/domestic-stock/v1/trading/inquire-balance",
                 {"CANO": cano, "ACNT_PRDT_CD": prdt,
                  "AFHR_FLPR_YN": "N", "OFL_YN": "",
                  "INQR_DVSN": "02", "UNPR_DVSN": "01",
                  "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
                  "PRCS_DVSN": "01", "CTX_AREA_FK100": "",
                  "CTX_AREA_NK100": ""},
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    holdings_raw = r.get("output1", []) or []
    summary = (r.get("output2") or [{}])[0]
    holdings = []
    for h in holdings_raw:
        try:
            qty = int(float(h.get("hldg_qty", 0)))
            if qty <= 0: continue
            holdings.append({
                "ticker": h.get("pdno"),
                "name":   h.get("prdt_name"),
                "qty":    qty,
                "avg_price": float(h.get("pchs_avg_pric", 0)),
                "current_price": float(h.get("prpr", 0)),
                "eval_amount": float(h.get("evlu_amt", 0)),
                "eval_pnl":    float(h.get("evlu_pfls_amt", 0)),
                "eval_pnl_pct": float(h.get("evlu_pfls_rt", 0)),
            })
        except Exception:
            continue
    try:
        cash = float(summary.get("dnca_tot_amt", 0))
        total_eval = float(summary.get("tot_evlu_amt", 0))
        total_pnl = float(summary.get("evlu_pfls_smtl_amt", 0))
    except Exception:
        cash = total_eval = total_pnl = 0
    return {"ok": True, "mode": mode,
            "cash":       cash,
            "total_eval": total_eval,
            "total_pnl":  total_pnl,
            "n_holdings": len(holdings),
            "holdings":   holdings}


# ════════════════════════════════════════════════════════════
#  연결 테스트 (UI에서 키 검증용)
# ════════════════════════════════════════════════════════════
def daily_bars_us(ticker: str, exchange: str = "NASD",
                    days: int = 100,
                    mode: str = "real") -> Dict[str, Any]:
    """미국 주식 일봉 (최근 N일). KIS overseas API."""
    tr = "HHDFS76240000"
    r = _call("/uapi/overseas-price/v1/quotations/dailyprice",
                 {"AUTH": "", "EXCD": exchange,
                  "SYMB": ticker.upper(),
                  "GUBN": "0",   # 0=일, 1=주, 2=월
                  "BYMD": dt.datetime.now().strftime("%Y%m%d"),
                  "MODP": "1"},  # 권리조정
                 tr_id=tr, mode=mode)
    if not r.get("ok"):
        return r
    out = r.get("output2", []) or []
    bars = []
    for row in out[:days]:
        try:
            bars.append({
                "date":  row.get("xymd"),
                "open":  float(row.get("open", 0)),
                "high":  float(row.get("high", 0)),
                "low":   float(row.get("low", 0)),
                "close": float(row.get("clos", 0)),
                "volume": int(float(row.get("tvol", 0))),
            })
        except Exception:
            continue
    return {"ok": True, "ticker": ticker.upper(),
            "n_bars": len(bars), "bars": bars}


# ════════════════════════════════════════════════════════════
#  fetch_ohlcv_best 통합용 — Provider 인터페이스
# ════════════════════════════════════════════════════════════
def fetch_ohlcv_kis(ticker: str, start: Optional[str] = None,
                     end: Optional[str] = None,
                     interval: str = "1d",
                     mode: str = "real"):
    """fetch_ohlcv_best 폴백 체인 통합용.
    Provider.fetch_ohlcv와 동일 시그너처. pandas DataFrame 반환.

    ticker가 6자리 숫자면 국내, 영문이면 미국으로 자동 판별.
    """
    import pandas as pd
    is_kr = ticker.isdigit() and len(ticker) == 6
    days = 250
    if start:
        try:
            d0 = dt.datetime.strptime(start, "%Y-%m-%d")
            days = max(10, (dt.datetime.now() - d0).days + 5)
        except Exception:
            pass
    if is_kr:
        r = daily_bars_kr(ticker, days=days, mode=mode)
    else:
        r = daily_bars_us(ticker, days=days, mode=mode)
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "KIS fetch 실패"))
    bars = r.get("bars") or []
    if not bars:
        raise RuntimeError("KIS bars 비어 있음")
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    # 표준 컬럼 보장
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def test_connection(mode: str = "real") -> Dict[str, Any]:
    """키 입력 후 토큰 발급 + 간단한 시세 조회로 정상 동작 확인."""
    tok = issue_token(mode, force=True)
    if not tok.get("ok"):
        return {"ok": False, "stage": "token", "error": tok.get("error")}
    # 삼성전자(국내) 또는 AAPL(해외)
    q = quote_kr("005930", mode=mode)
    if not q.get("ok"):
        # 국내 실패 시 해외 시도
        q2 = quote_us("AAPL", mode=mode)
        if not q2.get("ok"):
            return {"ok": False, "stage": "quote",
                    "error": f"국내/해외 시세 모두 실패: {q.get('error')} / {q2.get('error')}"}
        return {"ok": True, "stage": "quote_us",
                "test_symbol": "AAPL", "price": q2.get("price"),
                "mode": mode, "msg": f"AAPL ${q2.get('price')} 시세 정상"}
    return {"ok": True, "stage": "quote_kr",
            "test_symbol": "삼성전자(005930)",
            "price": q.get("price"), "mode": mode,
            "msg": f"삼성전자 {q.get('price'):,.0f}원 시세 정상"}
