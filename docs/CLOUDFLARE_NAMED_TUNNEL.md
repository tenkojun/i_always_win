# 정식(Named) Cloudflare Tunnel 설정 가이드

JIQT의 Quick Tunnel은 임시 URL이라 매번 바뀌고 인증이 없습니다.
정식 운영을 원한다면 **Named Tunnel**로 고정 URL + Cloudflare Access 인증을 추가하세요.

소요 시간: 약 30분
비용: 무료 (Cloudflare 계정 + 도메인 필요)

---

## 1단계: Cloudflare 계정 + 도메인 준비

### 1-1. 계정 생성
- https://dash.cloudflare.com/sign-up → 무료 가입

### 1-2. 도메인 추가 (둘 중 하나)
**옵션 A: 이미 도메인이 있는 경우**
1. Cloudflare 대시보드 → "Add a Site" → 도메인 입력
2. Free 플랜 선택
3. 표시되는 nameserver 2개를 도메인 등록업체(가비아/후이즈/Namecheap 등)의
   네임서버 설정에 등록
4. 적용까지 5분 ~ 24시간

**옵션 B: 도메인이 없는 경우**
- Cloudflare Registrar에서 직접 구매 ($8.57/년부터, .com)
- 또는 무료 옵션: [freenom.com](https://freenom.com) (.tk/.ml/.ga 등)
  - 주의: freenom은 안정성 낮음, 운영용은 비추

---

## 2단계: cloudflared 인증

JIQT는 cloudflared를 자동 다운로드합니다 (⚙ → 외부 접근 → 1단계).
다운로드 후 PowerShell에서:

```powershell
# cloudflared 위치 확인
$cf = "$env:USERPROFILE\.jiqt\bin\cloudflared.exe"

# Cloudflare 계정 로그인 (브라우저 열림)
& $cf tunnel login
```

브라우저에서 Cloudflare 로그인 → 도메인 선택 → "Authorize" 클릭.
인증 결과가 `~/.cloudflared/cert.pem` 에 저장됩니다.

---

## 3단계: Named Tunnel 생성

```powershell
# tunnel 생성 (이름은 자유, 예: jiqt-junhwa)
& $cf tunnel create jiqt-junhwa

# 생성된 tunnel UUID 확인
& $cf tunnel list
```

출력 예시:
```
ID                                    NAME           CREATED
12345678-abcd-1234-efgh-567890abcdef  jiqt-junhwa    2026-05-22T01:00:00Z
```

---

## 4단계: DNS 라우팅

원하는 서브도메인을 tunnel에 연결합니다.

```powershell
# 예: terminal.example.com → 이 PC의 8765 포트
& $cf tunnel route dns jiqt-junhwa terminal.example.com
```

Cloudflare가 자동으로 CNAME 레코드를 추가합니다.

---

## 5단계: config.yml 작성

다음 파일을 `~/.cloudflared/config.yml`에 생성:

```yaml
tunnel: 12345678-abcd-1234-efgh-567890abcdef
credentials-file: C:\Users\jun\.cloudflared\12345678-abcd-1234-efgh-567890abcdef.json

ingress:
  - hostname: terminal.example.com
    service: http://localhost:8765
  - service: http_status:404
```

- `tunnel:` — 3단계에서 받은 UUID
- `credentials-file:` — 자동 생성된 JSON 파일 경로
- `hostname:` — 4단계에서 설정한 도메인
- `service:` — JIQT가 실행 중인 포트 (기본 8765)

---

## 6단계: tunnel 실행

```powershell
& $cf tunnel run jiqt-junhwa
```

이제 https://terminal.example.com 으로 접속하면 자동으로 이 PC의 JIQT에 연결됩니다.

### 백그라운드 서비스로 등록 (Windows)

```powershell
# 관리자 권한 PowerShell에서:
& $cf service install
```

PC 재부팅 시 자동으로 tunnel 시작됩니다.

---

## 7단계 (선택): Cloudflare Access 인증 추가

URL을 아는 사람은 누구나 접속할 수 있으면 위험합니다.
Cloudflare Access(Zero Trust, 무료 50명)로 추가 인증:

### 7-1. Zero Trust 대시보드 활성화
- https://one.dash.cloudflare.com 접속
- 팀명 입력 (예: junhwa-jiqt) — 무료 플랜 선택

### 7-2. Application 생성
- Access → Applications → Add an application → Self-hosted
- Application name: JIQT Terminal
- Subdomain: terminal
- Domain: example.com

### 7-3. Policy 추가
- Policy name: 본인만
- Action: Allow
- Include → Emails → 본인 이메일 주소

이제 https://terminal.example.com 접속 시:
1. Cloudflare 로그인 화면 표시
2. 본인 이메일 입력 → OTP 코드 메일 수신
3. 인증 통과 후 JIQT 로그인 화면

**이중 보호**: Cloudflare Access (외부 차단) + JIQT 자체 계정 시스템.

---

## 트러블슈팅

### Q. `cloudflared tunnel login`이 브라우저를 안 엽니다
PowerShell에서 직접 출력된 URL을 복사해서 브라우저에 붙여넣기.

### Q. config.yml의 UUID가 헷갈립니다
```powershell
& $cf tunnel list
```
NAME 열의 `jiqt-junhwa`에 해당하는 ID 사용.

### Q. CNAME 충돌
- DNS 탭에서 기존 같은 서브도메인 레코드 삭제 후 4단계 재실행.

### Q. service install 실패
- 관리자 권한 PowerShell 필수.
- 또는 작업 스케줄러로 직접 등록.

### Q. JIQT가 8765가 아닌 다른 포트면?
- config.yml의 `service:` 줄 수정.

---

## 비교: Quick Tunnel vs Named Tunnel

| 항목 | Quick | Named |
|------|-------|-------|
| 설정 시간 | 5분 | 30분 |
| URL | 매번 변경 | 고정 |
| 도메인 | trycloudflare.com | 본인 도메인 |
| 인증 | JIQT 로그인만 | + Cloudflare Access |
| 비용 | 0 | 0 (도메인 별도) |
| 신뢰성 | 낮음 (URL 변경) | 높음 |
| 운영 추천 | ✗ | ✓ |
