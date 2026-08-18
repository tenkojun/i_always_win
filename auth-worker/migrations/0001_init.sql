-- I ALWAYS WIN 중앙 인증 — D1 스키마 (v1)
-- 적용: wrangler d1 execute iaw-auth --file=migrations/0001_init.sql

CREATE TABLE IF NOT EXISTS users (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  username       TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash  TEXT NOT NULL,
  salt           TEXT NOT NULL,
  nickname       TEXT,
  status         TEXT NOT NULL DEFAULT 'pending',  -- pending|active|rejected
  role           TEXT NOT NULL DEFAULT 'user',     -- user|admin
  claude_used    INTEGER NOT NULL DEFAULT 0,
  claude_quota_date TEXT,
  login_count    INTEGER NOT NULL DEFAULT 0,
  last_login_at  TEXT,
  created_at     TEXT NOT NULL,
  approved_at    TEXT,
  approved_by    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

CREATE TABLE IF NOT EXISTS sessions (
  token        TEXT PRIMARY KEY,
  user_id      INTEGER NOT NULL,
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  device_label TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_exp  ON sessions(expires_at);

-- A6: 사용자별 메인 PC 매핑 (username → 외부 접근 URL)
CREATE TABLE IF NOT EXISTS user_pcs (
  user_id        INTEGER PRIMARY KEY,
  pc_label       TEXT,
  public_url     TEXT,        -- 예: https://iaw-terminal.trycloudflare.com
  last_seen_at   TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
