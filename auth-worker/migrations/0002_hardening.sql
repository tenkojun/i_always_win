-- Plutus 중앙 인증 — 스키마 v2 (레이트리밋 · 기기 바인딩 · 감사)
-- 적용: wrangler d1 execute iaw-auth --file=migrations/0002_hardening.sql
--
-- 0001 을 이미 적용한 DB 에도 그대로 올릴 수 있다(전부 IF NOT EXISTS).

-- ── 로그인 시도 기록 (무차별 대입 차단) ────────────────────────
-- 성공하면 해당 키의 기록을 지운다. 실패만 쌓인다.
CREATE TABLE IF NOT EXISTS login_attempts (
  key        TEXT PRIMARY KEY,   -- 'u:<username>' 또는 'ip:<addr>'
  fails      INTEGER NOT NULL DEFAULT 0,
  first_at   TEXT NOT NULL,
  last_at    TEXT NOT NULL,
  locked_until TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_last ON login_attempts(last_at);

-- ── 감사 로그 ──────────────────────────────────────────────────
-- 승인/거부/비밀번호 변경처럼 되돌리기 어려운 일만 남긴다.
CREATE TABLE IF NOT EXISTS audit_log (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  actor_id  INTEGER,
  action    TEXT NOT NULL,
  target    TEXT,
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
