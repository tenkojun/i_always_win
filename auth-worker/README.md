# I ALWAYS WIN 중앙 인증 Worker

Cloudflare Workers + D1 기반 중앙 인증 서버.

## 0. 사전 준비
- [Cloudflare 계정](https://dash.cloudflare.com/sign-up) (무료)
- Node.js 18+
- Wrangler CLI

```sh
npm install -g wrangler
wrangler login
```

## 1. D1 데이터베이스 생성

```sh
cd auth-worker
wrangler d1 create iaw-auth
```

출력 예:
```
✅ Successfully created DB 'iaw-auth'
[[d1_databases]]
binding = "DB"
database_name = "iaw-auth"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

→ `database_id`를 복사해 `wrangler.toml`의 `REPLACE_AFTER_CREATE`에 붙여넣기.

## 2. 스키마 마이그레이션

```sh
npm run db:init
```

## 3. 시크릿 등록

```sh
# 어드민 패스워드 (배포 후 1회)
wrangler secret put ADMIN_PASSWORD
# 입력 프롬프트: WNSGHK (또는 원하는 강한 패스워드)

# 세션 서명 (선택 — 현재는 미사용, 향후 HMAC용)
wrangler secret put SESSION_SECRET
# 입력 프롬프트: 32+ 바이트 랜덤 (예: openssl rand -hex 32)
```

## 4. 배포

```sh
npm run deploy
```

출력 예:
```
✨ Deployment complete!
   https://iaw-auth.<your-account>.workers.dev
```

→ 이 URL을 EXE 설정창의 "중앙 인증 서버 URL"에 입력.

## 5. 검증

```sh
# 헬스체크
curl https://iaw-auth.<your-account>.workers.dev/health

# 어드민 로그인 테스트
curl -X POST https://iaw-auth.<your-account>.workers.dev/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"JUNHWA","password":"<your-admin-password>"}'
```

## 엔드포인트

| Method | Path | 설명 |
|---|---|---|
| GET | `/health` | 헬스체크 |
| POST | `/auth/register` | 가입 신청 (`username, password, nickname`) |
| POST | `/auth/login` | 로그인 → `{token, user}` |
| POST | `/auth/logout` | 세션 무효화 (Authorization: Bearer) |
| GET | `/auth/me` | 현재 인증 사용자 |
| GET | `/admin/users` | 사용자 목록 (어드민) |
| POST | `/admin/approve` | 가입 승인 (`user_id`) |
| POST | `/admin/reject` | 거부 (`user_id`) |
| POST | `/pc/register` | 본인 메인 PC 등록 (`pc_label, public_url`) |
| GET | `/go/<username>` | A6 redirect — 본인 메인 PC URL로 302 |

## 비용

100명 사용자 + 일일 평균 50 요청 기준 — **완전 무료** (Workers 100k/일, D1 5M read/일 한도).

## 백업

```sh
# D1 export (수동 백업)
wrangler d1 export iaw-auth --output=backup-$(date +%F).sql
```
