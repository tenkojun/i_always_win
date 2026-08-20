-- Plutus 중앙 인증 — 스키마 v3 (회원 등급 중앙화)
-- 적용: wrangler d1 execute iaw-auth --file=migrations/0003_tier.sql --remote
--
-- 앞선 마이그레이션과 같이 **몇 번을 다시 올려도 안전**하다(전부 IF NOT EXISTS).
--
-- 왜 users 에 컬럼을 추가하지 않는가
-- ---------------------------------
-- SQLite 의 ALTER TABLE ADD COLUMN 에는 IF NOT EXISTS 가 없다. 그러면 이
-- 파일을 두 번 올리는 순간 에러가 나고, 마이그레이션을 "한 번만 올려야 하는
-- 것"으로 만들어 버린다. 별도 테이블로 두면 그 함정이 사라진다.
-- 로컬(engine/auth/quota.py)의 user_tier 와 모양도 같아서 옮기기 쉽다.

-- ── 회원 등급 ──────────────────────────────────────────────────
-- 행이 없으면 free 다. 즉 가입만으로는 아무 행도 안 생긴다.
--
-- **여기가 등급의 유일한 진실이다.** 전에는 각 PC 의 .data/auth.db 에
-- 있었는데, 그러면 (1) 다른 PC 에서 로그인하면 등급이 사라지고
-- (2) 그 파일을 직접 고치면 누구나 플래티넘이 됐다. 서버가 모르는 값이라
-- 막을 방법이 없었다.
-- CASCADE 는 보험일 뿐이다. SQLite 는 `PRAGMA foreign_keys` 가 켜져 있을
-- 때만 외래키를 강제하고 기본값은 꺼짐이라, D1 에서 실제로 돌지 안 돌지에
-- 기대면 안 된다(실측: OFF 면 등급 행이 그대로 남는다).
--
-- 남아도 안전한 이유는 따로 있다 — users.id 가 AUTOINCREMENT 라
-- **지운 id 를 다시 쓰지 않는다.** 그래서 고아 등급 행이 남더라도 새
-- 사용자가 그 등급을 물려받는 일은 생기지 않는다. 게다가 계정 거부는
-- 삭제가 아니라 status 변경이라 애초에 삭제가 거의 없다.
CREATE TABLE IF NOT EXISTS user_tier (
  user_id    INTEGER PRIMARY KEY,
  tier       TEXT NOT NULL DEFAULT 'free',   -- free|premium|platinum
  updated_at TEXT NOT NULL,
  updated_by INTEGER,                        -- 등급을 바꾼 관리자
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_user_tier_tier ON user_tier(tier);
