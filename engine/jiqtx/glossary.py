# -*- coding: utf-8 -*-
"""
전문 용어 사전 — 보고서에서 마우스를 올리면 뜨는 설명
=====================================================
이 보고서는 통계·계량 용어를 그대로 쓴다. 용어를 쉬운 말로 바꾸면
정확도가 깎이고, 그대로 두면 읽는 사람이 막힌다. 그래서 용어는 두고
설명을 붙인다.

원칙
----
- 정의만 쓰지 않는다. 왜 보는지와 어떤 값이면 문제인지까지 쓴다.
  "PSR은 확률적 샤프 비율입니다" 는 아무 도움이 안 된다.
- 자기완결 HTML 이라 외부 라이브러리를 못 쓴다. 순수 CSS + 약간의 JS.
"""
from __future__ import annotations

from typing import Dict, Tuple

# 용어 → (짧은 뜻, 왜 보는가 / 어떤 값이 문제인가)
TERMS: Dict[str, Tuple[str, str]] = {

    # ── 검증 · 과적합 ────────────────────────────────────────
    "PSR": (
        "확률적 샤프 비율 (Probabilistic Sharpe Ratio)",
        "관측된 샤프가 기준치보다 정말 높을 확률. 표본이 짧거나 수익률이 "
        "비대칭·팻테일이면 샤프는 쉽게 부풀려진다. 95% 미만이면 "
        "'좋아 보이는 것'과 '좋은 것'을 구별하지 못한 상태다."),
    "DSR": (
        "디플레이티드 샤프 비율 (Deflated Sharpe Ratio)",
        "여러 전략을 시도한 뒤 가장 좋은 걸 골랐다는 사실을 보정한 샤프. "
        "100번 던져 나온 최고 기록은 실력이 아니다. "
        "이 값이 90% 미만이면 다중검정 보정 후 유의성이 없다는 뜻."),
    "PBO": (
        "과적합 확률 (Probability of Backtest Overfitting)",
        "표본을 여러 조합으로 갈라 학습·검증을 바꿔 끼웠을 때, 학습에서 "
        "1등이던 설정이 검증에서 중앙값 아래로 떨어질 확률. "
        "50% 넘으면 선택 절차 자체가 과적합, 75% 넘으면 폐기 대상."),
    "Murphy resolution": (
        "머피 분해의 해상도 (Resolution)",
        "예측이 기저율(그냥 평균)보다 얼마나 더 많은 정보를 담고 있는지. "
        "Brier = 신뢰도 − 해상도 + 불확실성. 해상도가 0에 가까우면 "
        "'맞히는 것처럼 보여도 실제로는 평균만 말하고 있다'는 정량적 증거."),
    "Brier skill": (
        "브라이어 스킬 스코어",
        "상수 예측(항상 기저율) 대비 얼마나 나은지. 0 이하면 "
        "'그냥 평균을 말하는 것보다 못하다'."),
    "과적합 갭": (
        "In-sample 정확도 − OOS 정확도",
        "학습 데이터에서의 성적과 처음 보는 데이터에서의 성적 차이. "
        "15%p를 넘으면 모델이 답을 외운 것이지 배운 게 아니다."),
    "Purged CV": (
        "퍼지드 교차검증",
        "라벨이 미래 구간에 걸쳐 있으면 학습·검증 구간이 시간적으로 "
        "겹친다. 겹치는 구간을 제거(purge)하고 여유(embargo)를 둬야 "
        "'미래를 이미 본' 성적이 나오지 않는다."),
    "ABSTAIN": (
        "기권 — 출력 무효화",
        "게이트를 통과하지 못한 모듈은 감점된 점수를 내지 않고 아예 "
        "출력을 취소한다. OOS 정확도 50%는 '약한 신호'가 아니라 "
        "'신호 없음'이고, 올바른 출력은 낮은 점수가 아니라 출력 없음이다."),

    # ── 리스크 ───────────────────────────────────────────────
    "VaR": (
        "밸류앳리스크 (Value at Risk)",
        "주어진 신뢰수준에서의 손실 경계값. VaR 95% 3%는 "
        "'100일 중 약 5일은 3%보다 더 잃는다'는 뜻이지 최대손실이 아니다. "
        "꼬리 안쪽이 얼마나 깊은지는 ES를 봐야 한다."),
    "ES": (
        "기대손실 (Expected Shortfall / CVaR)",
        "VaR을 넘어선 경우들의 평균 손실. VaR이 문턱이라면 ES는 "
        "문턱을 넘었을 때 실제로 얼마나 아픈지를 말한다."),
    "CVaR": (
        "조건부 VaR — ES와 같은 개념",
        "VaR을 초과한 손실들의 평균."),
    "CDaR": (
        "조건부 낙폭 (Conditional Drawdown at Risk)",
        "최악 구간 낙폭들의 평균. 한 번의 최대낙폭보다 "
        "'나쁜 국면이 평균적으로 얼마나 깊은지'를 보여 준다."),
    "Kupiec": (
        "쿠피엑 위반빈도 검정 (POF)",
        "VaR 위반 횟수가 이론적 빈도와 맞는지. p값이 낮으면 그 VaR "
        "모델은 손실 빈도 자체를 틀리게 보고 있다."),
    "Christoffersen": (
        "크리스토퍼슨 독립성 검정",
        "VaR 위반이 몰려서 발생하는지. 위반이 연달아 터지면 횟수가 "
        "맞아도 위험하다 — 모델이 변동성 군집을 못 잡고 있다는 뜻."),
    "MDD": (
        "최대낙폭 (Maximum Drawdown)",
        "고점 대비 최대 하락폭. 수익률보다 이걸 못 견뎌서 그만둔다."),
    "켈리": (
        "켈리 기준 (Kelly Criterion)",
        "장기 성장률을 최대화하는 베팅 비율. 문제는 공식이 아니라 "
        "μ(기대수익)를 안다고 가정한 것이다. μ 추정오차를 넣으면 "
        "성장최적 비율이 급격히 줄고, 낙폭 제약을 걸면 더 줄어든다."),
    "낙폭제약 켈리": (
        "Drawdown-constrained Kelly",
        "성장최적 켈리는 수학적으로 옳아도 운용 불가능한 낙폭을 동반한다. "
        "'95% 확률로 낙폭 X% 이내'를 만족하는 최대 비율로 자른 값."),

    # ── 변동성 · 국면 ────────────────────────────────────────
    "GARCH": (
        "GJR-GARCH-t 조건부 변동성",
        "변동성이 시간에 따라 변하고 군집한다는 사실을 반영한 모델. "
        "GJR은 하락이 상승보다 변동성을 더 키우는 비대칭(레버리지 효과)을, "
        "t는 팻테일을 반영한다."),
    "HAR": (
        "HAR-RV (Heterogeneous AutoRegressive)",
        "일간·주간·월간 실현변동성을 함께 넣어 장기 기억을 잡는 모델."),
    "레짐": (
        "시장 국면 (Regime)",
        "저변동 상승·고변동 하락처럼 통계적 성질이 다른 구간. "
        "국면이 바뀌면 팩터 베타와 상관이 함께 바뀐다."),
    "Jump Model": (
        "통계적 점프 모델",
        "국면 전환을 감지하되, 전환 페널티를 둬서 잡음에 과민반응하지 "
        "않게 한 방법. HMM보다 과도한 스위칭이 적다."),
    "FHS": (
        "필터드 히스토리컬 시뮬레이션",
        "과거 수익률을 그대로 재사용하지 않고, 조건부 변동성으로 "
        "표준화한 잔차를 재추출한 뒤 현재 변동성으로 되돌린다. "
        "'그때는 조용했고 지금은 시끄럽다'를 반영한다."),
    "GPD": (
        "일반화 파레토 분포 (극단값 이론)",
        "정규분포는 꼬리를 과소평가한다. 임계점 초과분만 따로 적합해 "
        "극단 손실 구간을 제대로 모형화한다."),

    # ── 팩터 · 헤지 ──────────────────────────────────────────
    "팩터 R²": (
        "팩터 모델 설명력",
        "수익 변동 중 팩터로 설명되는 비중. 자산군마다 기대 밴드가 다르다. "
        "밴드 아래로 떨어지면 그 자산을 그 팩터로 보는 관점 자체가 "
        "틀렸다는 신호이고, 델타·헤지·스트레스가 전부 무효가 된다."),
    "델타 패널": (
        "리스크 팩터별 민감도 표",
        "'샤프 0.96' 같은 요약이 아니라 무엇이 X만큼 움직이면 얼마를 "
        "잃는가. 정적 베타 대신 시변 베타를 쓰고 하방 베타를 병기한다."),
    "하방 베타": (
        "Downside beta",
        "시장이 하락한 날만 골라 추정한 베타. 이게 전체 베타보다 크면 "
        "'좋을 땐 덜 오르고 나쁠 땐 더 빠지는' 비대칭 자산이다. "
        "정적 베타 스트레스는 이걸 놓친다."),
    "β안정성 CV": (
        "베타 변동계수",
        "롤링 베타의 표준편차 ÷ 평균. 0.8을 넘으면 그 베타를 헤지비율로 "
        "쓰면 안 된다 — 오늘 맞춘 헤지가 내일 틀린다."),
    "SMB": ("소형−대형 (Small Minus Big)",
            "소형주 롱 · 대형주 숏 스프레드. 롱온리 소형주 ETF 수익률을 "
            "그대로 쓰면 팩터가 아니라 그냥 시장이 된다."),
    "HML": ("가치−성장 (High Minus Low)",
            "가치주 롱 · 성장주 숏 스프레드."),
    "RMW": ("수익성 (Robust Minus Weak)",
            "고수익성 롱 · 저수익성 숏 스프레드."),
    "UMD": ("모멘텀 (Up Minus Down)",
            "최근 상승 롱 · 하락 숏 스프레드."),
    "최소분산 헤지비율": (
        "Minimum-variance hedge ratio",
        "잔차 분산을 최소화하는 헤지 수량 = 다변량 팩터 회귀 계수. "
        "따라서 팩터 모델이 틀리면 헤지도 같이 틀린다. "
        "이 연결을 끊고 헤지를 논하면 안 된다."),
    "알파": (
        "팩터로 설명되지 않은 초과수익",
        "진짜 실력일 수도, 모델에 없는 팩터일 수도 있다. "
        "t값이 2 미만이면 통계적으로 0과 구별되지 않는다."),

    # ── 유동성 · 체결 ────────────────────────────────────────
    "EDGE": (
        "EDGE 유효 스프레드 추정량",
        "일봉 OHLC만으로 매수-매도 호가 스프레드를 편향 없이 추정하는 "
        "방법. Corwin-Schultz·Roll은 저스프레드 구간에서 크게 부풀린다."),
    "Amihud": (
        "아미후드 비유동성",
        "거래대금 1단위당 가격이 얼마나 움직이는지. 클수록 "
        "같은 금액을 사고팔 때 시장을 더 밀어낸다."),
    "제곱근 임팩트": (
        "Square-root market impact",
        "체결 물량이 커질수록 비용이 √(참여율)에 비례해 늘어난다는 "
        "경험 법칙. 사이즈를 키울 때 비용이 선형으로 늘지 않는다."),
    "ADV": ("일평균 거래대금", "청산 가능성의 기본 척도."),

    # ── 판정 ─────────────────────────────────────────────────
    "NO_TRADE": (
        "진입하지 않음",
        "약세 판단이 아니다. 현 조건에서 포지션을 잡을 근거가 "
        "부족하거나 리스크 한도에 걸린 상태. 방향 확률은 따로 읽어야 한다."),
    "거부권": (
        "Veto",
        "특정 전문가가 단독으로 진입을 막을 수 있는 권한. "
        "데이터 무결성·체결 가능성·리스크 한도처럼 "
        "'다른 게 아무리 좋아도 안 되는' 조건에만 부여된다."),
    "반증 조건": (
        "Kill criteria",
        "'무엇이 사실이면 이 논지가 죽는가'를 분석 시점에 미리 정해 "
        "둔 것. 사후에 만든 반증 조건은 의미가 없다."),
    "증거 위계": (
        "Evidence hierarchy",
        "반론의 승패를 정하는 순서: ①데이터 무결성 ②체결 가능성 "
        "③표본외 통계 ④표본내 통계 ⑤경제적 메커니즘 ⑥서사. "
        "상위 증거가 하위 주장을 이긴다."),
    "RND": (
        "위험중립 밀도 (Risk-Neutral Density)",
        "옵션 가격에서 역산한 시장의 내재 분포. 우리 모델 분포와 "
        "비교하면 '시장과 어디서 의견이 갈리는지'가 보인다."),
    "드리프트": (
        "기대수익률 추정치 μ̂",
        "표준오차가 σ/√T 라, 일봉 표본에서는 거의 항상 추정치 자체만큼 "
        "크다. 그래서 '상승확률 71%' 같은 값은 시장이 아니라 "
        "가정에 대한 진술이다."),
    "생존편향": (
        "Survivorship bias",
        "데이터 소스에 상장폐지 종목이 없으면, 지금 남아 있는 종목만 "
        "보게 된다. 종목선택 전략은 원리적으로 검증 불가능해진다."),
}


def build_css() -> str:
    """
    툴팁 스타일. 자기완결 HTML 이라 외부 CSS 를 못 쓴다.

    툴팁을 용어 안에 넣지 않는다
    ---------------------------
    예전에는 `.term` 안에 `position:absolute` 인 `.tip` 을 넣었다. 그런데
    보고서에는 잘라내는 조상이 둘 있다 — `details.sec{overflow:hidden}`
    (모든 섹션)과 `.tw{overflow-x:auto}` (표 감싸개). absolute 는 그
    조상 박스에서 잘리므로, 툴팁이 **화면 안쪽에 있어도** 섹션 가장자리나
    표 안에서는 잘렸다. 붙는 방향을 바꾸는 것(tip-left/tip-right)으로는
    고칠 수 없다. 자르는 주체가 뷰포트가 아니라 조상이기 때문이다.

    그래서 `<body>` 바로 아래 **`position:fixed` 단일 레이어** 하나를 두고
    그 안에 내용만 갈아 끼운다. fixed 는 overflow 조상을 벗어난다.
    (조상에 transform/filter 가 있으면 fixed 도 갇히는데, 이 보고서에서
    그런 속성은 sticky 헤더의 backdrop-filter 뿐이고 툴팁은 body 직속이라
    영향을 받지 않는다.)
    """
    return """
/* ── 용어 툴팁 ───────────────────────────────────────────── */
.term{border-bottom:1px dotted #4a5568;cursor:help}
.term:hover,.term:focus{border-bottom-color:#7dd3fc;color:#bae6fd;outline:none}
#tipbox{
  position:fixed;z-index:9999;left:0;top:0;
  width:max-content;max-width:min(360px,88vw);
  background:#111722;color:#dbe3ef;border:1px solid #2b6d84;
  border-radius:8px;padding:11px 13px;
  font-size:12.5px;line-height:1.62;font-weight:400;
  text-align:left;letter-spacing:0;white-space:normal;
  box-shadow:0 12px 34px rgba(0,0,0,.55);
  opacity:0;visibility:hidden;transform:translateY(4px);
  transition:opacity .13s ease,transform .13s ease;pointer-events:none}
#tipbox.on{opacity:1;visibility:visible;transform:translateY(0)}
#tipbox b{color:#7dd3fc;display:block;margin-bottom:5px;font-size:12px;
  letter-spacing:.3px}
#tipbox::after{content:"";position:absolute;left:var(--ax,50%);
  border:6px solid transparent;transform:translateX(-6px)}
#tipbox.up::after{top:100%;border-top-color:#2b6d84}
#tipbox.down::after{bottom:100%;border-bottom-color:#2b6d84}
@media print{#tipbox{display:none}}
"""


def build_js() -> str:
    """
    본문 텍스트에서 용어를 찾아 툴팁을 입힌다.

    서버에서 문자열 치환으로 처리하면 HTML 속성값 안까지 건드려
    마크업이 깨진다. 그래서 브라우저에서 **텍스트 노드만** 훑는다.
    """
    return """
(function(){
  const TERMS = __TERMS_JSON__;
  // 긴 용어부터 매칭해야 'VaR' 이 'CVaR' 를 잘라먹지 않는다
  const keys = Object.keys(TERMS).sort((a,b)=>b.length-a.length);
  const esc = s => s.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&');
  const re = new RegExp('(' + keys.map(esc).join('|') + ')', 'g');
  const SKIP = new Set(['SCRIPT','STYLE','SVG','PATH','TEXT','CODE','PRE']);

  function decorate(root){
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n){
        // /g 정규식의 test() 는 lastIndex 를 남긴다. 리셋하지 않으면
        // 다음 노드를 중간부터 검사해 **한 노드 걸러 하나씩** 통째로
        // 놓친다 (용어의 절반이 조용히 툴팁을 못 얻는다).
        re.lastIndex = 0;
        if(!n.nodeValue || !re.test(n.nodeValue)) return NodeFilter.FILTER_REJECT;
        let p = n.parentElement;
        while(p){
          if(SKIP.has(p.tagName)) return NodeFilter.FILTER_REJECT;
          if(p.classList && p.classList.contains('term'))
            return NodeFilter.FILTER_REJECT;
          p = p.parentElement;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const targets = [];
    let n; while((n = walker.nextNode())) targets.push(n);

    targets.forEach(node=>{
      re.lastIndex = 0;
      const frag = document.createDocumentFragment();
      let last = 0, m;
      while((m = re.exec(node.nodeValue))){
        if(m.index > last)
          frag.appendChild(document.createTextNode(
            node.nodeValue.slice(last, m.index)));
        const t = TERMS[m[1]];
        const span = document.createElement('span');
        span.className = 'term';
        span.tabIndex = 0;
        // 설명은 DOM 에 넣지 않고 데이터로만 들고 있는다. 본문에 심으면
        // 표 폭 계산에 끼어들고, 다음 순회에서 다시 스캔 대상이 된다.
        span.dataset.tt = t[0];
        span.dataset.td = t[1];
        span.appendChild(document.createTextNode(m[1]));
        frag.appendChild(span);
        last = m.index + m[1].length;
      }
      if(last < node.nodeValue.length)
        frag.appendChild(document.createTextNode(node.nodeValue.slice(last)));
      node.parentNode.replaceChild(frag, node);
    });
  }

  // ── 툴팁 레이어 — body 직속 fixed 하나를 돌려 쓴다 ──────────
  const PAD = 8;        // 뷰포트 가장자리 여백
  const GAP = 10;       // 용어와 툴팁 사이
  let box = null;

  function ensureBox(){
    if(box) return box;
    box = document.createElement('div');
    box.id = 'tipbox';
    box.setAttribute('role','tooltip');
    document.body.appendChild(box);
    return box;
  }

  function hide(){ if(box) box.classList.remove('on'); }

  function show(e){
    const el = e.currentTarget;
    const b = ensureBox();
    b.textContent = '';
    const h = document.createElement('b');
    h.textContent = el.dataset.tt || '';
    b.appendChild(h);
    b.appendChild(document.createTextNode(el.dataset.td || ''));

    // visibility:hidden 이어도 레이아웃은 잡히므로 미리 잰다
    b.classList.remove('up','down');
    b.style.left = '0px';
    b.style.top  = '0px';
    const r  = el.getBoundingClientRect();
    const bw = b.offsetWidth, bh = b.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // 가로 — 용어 중앙 정렬 후 뷰포트 안으로 밀어 넣는다
    const cx = r.left + r.width / 2;
    let x = cx - bw / 2;
    x = Math.max(PAD, Math.min(x, vw - bw - PAD));

    // 세로 — 위가 좁으면 아래로 뒤집는다
    let y = r.top - bh - GAP, dir = 'up';
    if(y < PAD){ y = r.bottom + GAP; dir = 'down'; }
    y = Math.max(PAD, Math.min(y, vh - bh - PAD));

    // 화살표는 툴팁이 밀린 만큼 되돌려 용어를 계속 가리킨다
    b.style.setProperty('--ax',
      Math.max(14, Math.min(cx - x, bw - 14)) + 'px');
    b.style.left = x + 'px';
    b.style.top  = y + 'px';
    b.classList.add('on', dir);
  }

  function init(root){
    decorate(root);
    root.querySelectorAll('.term').forEach(el=>{
      if(el.dataset.tipBound) return;
      el.dataset.tipBound = '1';
      el.addEventListener('mouseenter', show);
      el.addEventListener('focus', show);
      el.addEventListener('mouseleave', hide);
      el.addEventListener('blur', hide);
    });
  }

  document.addEventListener('DOMContentLoaded', function(){
    init(document.body);
    // 스크롤/리사이즈하면 좌표가 어긋난다 — 다시 계산하지 말고 감춘다
    window.addEventListener('scroll', hide, {passive:true, capture:true});
    window.addEventListener('resize', hide, {passive:true});
    document.addEventListener('keydown', function(ev){
      if(ev.key === 'Escape') hide();
    });
    // 접혀 있던 섹션이 열리면 그 안도 처리
    document.querySelectorAll('details.sec').forEach(d=>{
      d.addEventListener('toggle', function(){ if(d.open) init(d); },
                         {once:false});
    });
  });
})();
"""
