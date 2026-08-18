# I ALWAYS WIN

기관급 퀀트 **분석** 터미널. 종목 하나를 넣으면 데이터 무결성부터
유동성·변동성·레짐·팩터·리스크까지 훑어 한글 리포트로 뱉는다.

> 이 소프트웨어의 모든 출력은 **정보 제공 목적**이며 투자 권유가 아니다.

개발자 · **Tenko jun - 정준화**

---

## 실행

```bash
pip install -r requirements.txt
python run_desktop.py
```

브라우저가 아니라 앱 창이 뜬다 (`pywebview`). 서버는 `127.0.0.1:8765`.

폰에서 보려면 같은 와이파이에서 `http://<이 PC의 IP>:8765`.
집 밖에서 보려면 설정 → **외부 접근**에서 터널을 켠다.

### EXE 빌드

```bash
pyinstaller app.spec --noconfirm
```

`dist/` 아래에 콘솔 창 없는 실행 파일이 나온다.

---

## API 키

**이 저장소에는 키가 들어 있지 않다.** 키 없이도 전부 동작한다 —
야후 파이낸스 + Stooq 무키 폴백이 기본이다.

키는 앱을 띄운 뒤 **설정 → API 키**에서 넣는다. 넣으면 데이터 품질이
올라가는 보강재일 뿐, 없다고 기능이 잠기지 않는다.

| 제공자 | 쓰임 | 무료 한도 |
|---|---|---|
| Finnhub | 실시간 시세·뉴스·펀더멘털 | 분당 60콜 |
| Alpha Vantage | 기술지표·펀더멘털 | 분당 5 / 일 25콜 |
| FMP | 재무제표·SEC 공시·밸류에이션 | 일 250콜 |
| DeepL · Brave · Anthropic | 번역·검색·에이전트 채팅 | 각자 다름 |

입력한 키는 `.data/keys.json` (권한 0600)에만 저장되고 `.gitignore`
대상이다. 환경변수(`FINNHUB_API_KEY` 등)를 쓰면 그쪽이 우선한다.

---

## 상태는 전부 `.data/` 안에

프로그램 폴더 밖에 상태가 있으면 백업·이전·삭제가 반쪽이 된다.
그래서 앱이 만드는 모든 것을 한곳에 모았다.

```
.data/
├── keys.json      API 키 (0600)
├── auth.db        계정·세션·분석 이력·커뮤니티
├── pc_id          이 PC 식별자
├── chats/         에이전트 대화
├── cache/         가격 캐시
└── bin/           cloudflared 등 자동 내려받은 바이너리
```

경로는 [`engine/paths.py`](engine/paths.py) 한 곳에서만 결정된다.
앱 폴더가 쓰기 불가면 `%LOCALAPPDATA%/i_always_win` 으로 자동 강등하고,
예전 `~/.jiqt` 가 남아 있으면 첫 실행 때 한 번 옮겨 온다(원본은 보존).

`.data/` 전체가 `.gitignore` 대상이라 키가 저장소로 샐 일이 없다.

---

## 구조

```
.
├── version.py           앱 이름·버전·개발자 단일 소스
├── run_desktop.py       데스크톱 런처 (앱 창)
├── main.py              분석 오케스트레이터
├── app.spec             PyInstaller
├── auth-worker/         중앙 인증 (Cloudflare Workers + D1)
├── webapp/
│   ├── server.py        Flask API
│   └── static/          단일 페이지 앱
└── engine/
    ├── paths.py         런타임 경로 단일 결정
    ├── data/            다중소스 데이터 레이어 (무키 폴백)
    ├── analysis/        타임프레임 분석
    ├── institutional/   PSR/DSR/CDaR/스트레스/스코어카드
    ├── risk/            리스크 메트릭
    ├── ml/              예측 모델
    ├── factor/ volatility/ orderflow/
    ├── signal_engine/   메타 의사결정 (거부권·충돌해소)
    ├── explain/         판정 인과 추적 (XAI)
    ├── portfolio/       보유 종목
    ├── awareness/       이벤트·알림
    ├── llm/             로컬 LLM · 에이전트
    ├── auth/ auth_remote/  로컬·중앙 인증
    ├── cloud/           외부 접근 (터널)
    ├── jobs/            백그라운드 작업 큐
    └── report/          HTML·JSON 리포트
```

---

## 이 앱이 하지 않는 것

전략 백테스트·페이퍼 트레이딩·리서치 도구는 v2.2.0에서 **제거했다**.
한 종목을 깊게 보는 일과 전략을 돌려 보는 일은 다른 제품이고,
둘을 한 앱에 두면 양쪽 다 어정쩡해진다.

자세한 내역은 [CHANGELOG.md](CHANGELOG.md).

---

## 한계 (읽고 쓸 것)

- **생존편향** — 야후 파이낸스에 상장폐지 종목이 없다. 종목선택 전략 검증 불가.
- **일봉 한계** — 진짜 실현변동성·오더플로우는 인트라데이가 필요하다.
- **펀더멘털은 point-in-time 이 아니다** — 시계열 백테스트 금지, 현재 진단만.
- **드리프트 표준오차는 σ/√T** 라 일봉 표본에서 거의 항상 추정치만큼 크다.
  상승확률은 시장이 아니라 가정에 대한 진술이다.
- 적중률은 크게 오르지 않는다. 개선은 거짓신호 제거·리스크추정·사이징 규율에서 나온다.
