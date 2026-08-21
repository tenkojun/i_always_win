# 제3자 저작물

Plutus 본체는 MIT 다. 아래 둘은 **화면에서 쓰는 자바스크립트 라이브러리**로,
`webapp/static/vendor/` 에 그대로 들여왔다.

## 왜 들여왔는가

전에는 CDN(unpkg · jsDelivr)에서 실행 시점에 받아 왔다. 그러면 세 가지가 문제다.

1. **공급망** — CDN 이나 해당 경로가 오염되면 임의의 JS 가 *이 앱의 출처로*
   실행된다. 세션이 붙은 요청을 마음대로 보낼 수 있고, 화면의 내용을 읽을 수
   있다. API 키와 계정을 다루는 앱에서 감수할 이유가 없다.
2. **가용성** — 인터넷이 없거나 CDN 이 막힌 망에서는 차트가 통째로 안 뜬다.
   데스크톱 앱이 남의 서버 가동률에 묶일 이유가 없다.
3. **무결성 검증 불가** — jsDelivr 은 파일을 동적으로 재압축한다. 그쪽 파일
   주석이 *"Do NOT use SRI with dynamically generated files"* 라고 직접
   경고한다. 즉 SRI 로도 못 막는다.

들여온 뒤에는 셋 다 사라진다. 합쳐서 182KB 다.

## 목록

### TradingView Lightweight Charts v4.1.3
- 파일: `webapp/static/vendor/lightweight-charts-4.1.3.js` (160,943 bytes)
- 저작권: Copyright (c) 2024 TradingView, Inc.
- 라이선스: **Apache License 2.0**
- 원본: https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js
- SHA-384: `JZigAjwiaZtkUbA44CWkPaT3iBb/mU5pO6QOANp+OqHd4q+1+7MG1kzp2OOP9ZfP`

### qrcode-generator v1.4.4
- 파일: `webapp/static/vendor/qrcode-generator-1.4.4.js` (20,768 bytes)
- 저작권: Copyright (c) Kazuhiko Arase
- 라이선스: **MIT**
- 원본: https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.min.js
- SHA-384: `lQXOAyZwHXE55JFyrOMB7nY2Wv+m5ZWNtJcHrd1rceRQXAYNLak8ukN5TjBTcIwz`

두 라이선스 모두 재배포를 허용하며 저작권 표시를 요구한다. 이 문서가 그
표시이고, 각 파일 상단의 라이선스 헤더도 지우지 않았다.

## 여전히 외부에서 오는 것

**웹폰트** (JetBrains Mono · IBM Plex Sans KR) 는 Google Fonts 에서 받는다.
들여오지 않은 이유는 한글 웹폰트가 수 MB 라 배포본이 크게 늘기 때문이다.

위험도가 다르다 — 폰트는 **실행되지 않는다.** 최악의 경우가 글꼴이 안 예쁘게
나오는 것이고, 코드 실행으로 이어지지 않는다. CSS 폴백
(`monospace` · `sans-serif`)이 걸려 있어 못 받아도 화면은 정상 동작한다.

다만 **실행할 때마다 Google 에 요청이 나간다**는 점은 남는다. 완전한 오프라인
동작과 요청 0건이 필요해지면 그때 들여온다.

## 보고서는 예외 없이 0개

`engine/jiqtx` 가 만드는 HTML 보고서에는 제3자 저작물이 **하나도** 없다.
차트는 인라인 SVG 를 문자열로 만들고, 폰트도 시스템 폰트만 쓴다. 인터넷 없이
열려야 하고, 몇 년 뒤에 열어도 그때 모습 그대로여야 하기 때문이다.
