# I ALWAYS WIN — 중앙 인증 Worker

Cloudflare Workers + D1 기반 중앙 인증 서버.

**왜 중앙에 두는가.** 계정·승인·세션이 내 PC의 SQLite 안에만 있으면,
PC가 꺼져 있는 동안에는 아무도 로그인할 수 없고 가입 신청도 받을 수 없다.
인증만 떼어내 Cloudflare 엣지에 올리면 PC 전원과 무관하게 살아 있다.
분석 엔진은 그대로 PC에 남는다 — 중앙에 올리는 건 신원 확인뿐이다.

전부 **무료 티어**로 돌아간다 (Workers 10만 요청/일, D1 500만 행 읽기/일,
10만 행 쓰기/일, 5GB 저장).

---

## 0. 사전 준비

- [Cloudflare 계정](https://dash.cloudflare.com/sign-up) (무료)
- Node.js 18+

```bash
npm install -g wrangler
wrangler login
```

## 1. D1 데이터베이스 생성

```bash
cd auth-worker
wrangler d1 create iaw-auth
```

출력에 나온 `database_id` 를 `wrangler.toml` 의 `REPLACE_AFTER_CREATE`
자리에 붙여넣는다.

## 2. 스키마 적용

```bash
npm run db:init
```

`0001_init.sql`(계정·세션·PC) 과 `0002_hardening.sql`(레이트리밋·감사)이
차례로 올라간다. 둘 다 `IF NOT EXISTS` 라 이미 쓰던 DB에도 그대로 적용된다.

## 3. 어드민 시크릿 등록

```bash
wrangler secret put ADMIN_PASSWORD
```

**이 시크릿이 없으면 어드민 계정이 만들어지지 않는다.** 예전에는 코드에
기본 패스워드가 박혀 있었는데, 저장소가 공개되면 그대로 뚫리는 값이라
없앴다. 8자 이상, 다른 곳에서 쓰지 않는 값으로 정한다.

어드민 ID는 `wrangler.toml` 의 `ADMIN_USERNAME` (기본 `JUNHWA`).

## 4. 배포

```bash
npm run deploy
```

```
✨ Deployment complete!
   https://iaw-auth.<your-account>.workers.dev
```

이 URL을 앱 **설정 → 중앙 인증** 에 입력한다.

## 5. 검증

```bash
curl https://iaw-auth.<your-account>.workers.dev/health
```

```json
{"ok":true,"service":"iaw-auth","version":2,"time":"..."}
```

---

## 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | 헬스체크 |
| POST | `/auth/register` | 가입 신청 (`username, password, nickname`) |
| POST | `/auth/login` | 로그인 → `{token, user, expires_in_days}` |
| POST | `/auth/logout` | 이 세션만 무효화 |
| POST | `/auth/logout_all` | 내 모든 기기 로그아웃 |
| POST | `/auth/change_password` | `old_password, new_password` — 다른 기기는 자동 로그아웃 |
| GET | `/auth/me` | 현재 인증 사용자 |
| GET | `/auth/sessions` | 내 활성 세션 목록 (기기·발급시각) |
| GET | `/admin/users` | 사용자 목록 (어드민) |
| POST | `/admin/approve` | 가입 승인 (`user_id`) |
| POST | `/admin/reject` | 거부 (`user_id`) — 세션도 함께 폐기 |
| POST | `/pc/register` | 내 PC 외부 URL 등록 (`pc_label, public_url`) |
| GET | `/pc/status` | 내 PC 등록 상태 + `/go/` 주소 |
| POST | `/pc/unregister` | 등록 해제 |
| GET | `/go/<username>` | 그 사용자의 PC 주소로 302 |

인증은 `Authorization: Bearer <token>` 헤더.

---

## 보안 설계

**세션 토큰** — `crypto.getRandomValues` 로 뽑은 256비트 난수를 D1에
저장한다. JWT처럼 서명해서 들고 다니게 하지 않는 이유는 폐기 때문이다.
서명형 토큰은 만료 전에 무효화하기가 어렵지만, 저장형은 한 줄 삭제로
즉시 끊긴다. 계정을 거부하거나 비밀번호를 바꿀 때 실제로 그렇게 한다.

**비밀번호** — PBKDF2-SHA256 **10만 회** + 계정별 16바이트 salt.
비교는 상수시간. 10만은 임의로 고른 값이 아니라 **Workers 의 상한**이다 —
그 이상은 `NotSupportedError` 로 요청이 죽는다. 참고로
`wrangler dev --local` 은 이 제한을 강제하지 않으니, 인증 관련 변경은
반드시 배포본에서 한 번 찔러 보고 끝내야 한다.

**무차별 대입** — username과 IP를 각각 센다. 30분 창에서 8회 실패하면
15분 잠금. 성공하면 카운터를 지운다. 없는 사용자로 로그인을 시도해도
해시 계산을 한 번 돌려 응답 시간으로 계정 존재 여부가 새지 않게 한다.

**오픈 리다이렉트 차단** — `/go/<username>` 은 사용자가 등록한 주소로
302를 보낸다. 검증 없이 보내면 누구나 임의 주소를 등록하고 이 도메인의
신뢰를 빌릴 수 있다. 그래서 등록 시점과 리다이렉트 시점 **양쪽에서**
https 여부·호스트 형태를 확인하고 사설/로컬 대역은 거부한다.

**오류 노출** — 500 응답에 예외 내용을 싣지 않는다. 상세는
`wrangler tail` 로만 본다.

---

## 무료 티어를 의식한 부분

- **어드민 시드**는 isolate당 한 번만 확인한다. 매 요청 확인하면
  요청 수만큼 D1 읽기가 발생한다.
- **만료 세션 청소**는 로그인 20회에 1번꼴(5% 확률)로만 돈다.
  쓰기 쿼터를 아끼면서도 테이블이 무한정 자라지 않는다.
- `/admin/users` 는 500건으로 자른다.

사용자 100명 · 1인당 하루 50요청이어도 무료 한도의 5% 수준이다.

---

## 운영

```bash
# 실시간 로그
npm run tail

# 백업 (수동)
wrangler d1 export iaw-auth --output=backup-$(date +%F).sql

# 승인 대기자 확인 (어드민 토큰 필요)
curl -H "Authorization: Bearer <token>" \
     https://iaw-auth.<account>.workers.dev/admin/users
```

### CORS 제한 (선택)

기본값은 `*` 다. 앱 도메인이 정해졌으면 `wrangler.toml` 의 `[vars]` 에
`ALLOWED_ORIGINS = "https://terminal.example.com"` 를 넣는다
(쉼표로 여러 개).
