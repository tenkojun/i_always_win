/**
 * I ALWAYS WIN — 중앙 인증 Worker
 * ================================
 * 내 PC 가 꺼져 있어도 계정·승인·세션이 살아 있어야 한다.
 * 그래서 인증은 Cloudflare Workers + D1 에 둔다(둘 다 무료 티어).
 *
 * 무료 티어를 의식한 설계
 * -----------------------
 * - Workers 무료: 요청 10만/일. D1 무료: 읽기 500만행/일, 쓰기 10만행/일.
 * - 그래서 매 요청마다 DB 를 건드리지 않는다. 어드민 시드는 isolate 당
 *   한 번만, 만료 세션 청소는 로그인 때 확률적으로만 돈다.
 *
 * 환경 변수 / 시크릿
 * ------------------
 *   DB              : D1 바인딩
 *   ADMIN_USERNAME  : 초기 어드민 ID (vars)
 *   ADMIN_PASSWORD  : 초기 어드민 패스워드 (secret · **필수**)
 *   ALLOWED_ORIGINS : CORS 허용 origin 쉼표 목록 (vars, 선택. 기본 '*')
 *
 * 세션 토큰은 crypto.getRandomValues 로 뽑은 256비트 난수를 D1 에
 * 저장하는 방식이다. 추측이 불가능하고 서버에서 즉시 폐기할 수 있으므로
 * 별도의 HMAC 서명은 두지 않는다(서명형 JWT 는 폐기가 어렵다).
 */

// ═══════════════════════════════════════════════════════════════
//  공통 헬퍼
// ═══════════════════════════════════════════════════════════════
const SESSION_DAYS = 30;
const MAX_FAILS = 8;              // 이 횟수를 넘기면 잠금
const LOCK_MINUTES = 15;          // 잠금 지속
const FAIL_WINDOW_MINUTES = 30;   // 이 시간이 지나면 실패 카운트 리셋

const nowIso = () => new Date().toISOString();
const plusMinutes = (m) => new Date(Date.now() + m * 60000).toISOString();

function corsHeaders(env, req) {
  const allow = (env.ALLOWED_ORIGINS || '*').trim();
  let origin = '*';
  if (allow !== '*') {
    const list = allow.split(',').map(s => s.trim()).filter(Boolean);
    const got = req ? (req.headers.get('Origin') || '') : '';
    origin = list.includes(got) ? got : list[0] || '*';
  }
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

function json(data, init = {}, env = {}, req = null) {
  return new Response(JSON.stringify(data), {
    status: init.status || 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'no-referrer',
      ...corsHeaders(env, req),
      ...(init.headers || {}),
    },
  });
}

// ═══════════════════════════════════════════════════════════════
//  비밀번호 (PBKDF2-SHA256, 20만 회)
// ═══════════════════════════════════════════════════════════════
function bufToHex(buf) {
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, '0')).join('');
}

function hexToBuf(hex) {
  const a = new Uint8Array(hex.length / 2);
  for (let i = 0; i < a.length; i++) a[i] = parseInt(hex.substr(i * 2, 2), 16);
  return a.buffer;
}

function randomToken(bytes = 32) {
  return bufToHex(crypto.getRandomValues(new Uint8Array(bytes)));
}

async function hashPassword(pw, saltHex) {
  if (!saltHex) saltHex = bufToHex(crypto.getRandomValues(new Uint8Array(16)));
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(pw), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: hexToBuf(saltHex), iterations: 200000,
      hash: 'SHA-256' }, key, 256);
  return { hash: bufToHex(bits), salt: saltHex };
}

/** 길이·내용 모두 상수시간에 가깝게 비교. */
function constantEq(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function verifyPassword(pw, hashHex, saltHex) {
  const { hash } = await hashPassword(pw, saltHex);
  return constantEq(hash, hashHex);
}

// ═══════════════════════════════════════════════════════════════
//  어드민 시드 — isolate 당 1회
// ═══════════════════════════════════════════════════════════════
let _adminChecked = false;

async function ensureAdmin(env) {
  if (_adminChecked) return { cached: true };
  _adminChecked = true;

  const name = env.ADMIN_USERNAME;
  if (!name) return { skipped: 'ADMIN_USERNAME 없음' };

  const u = await env.DB.prepare(
    'SELECT id, status, role FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(name).first();

  if (!u) {
    // 기본 패스워드를 코드에 두지 않는다. 시크릿이 없으면 시드하지 않는다.
    if (!env.ADMIN_PASSWORD) {
      _adminChecked = false;          // 시크릿이 생기면 다음에 다시 시도
      return { skipped: 'ADMIN_PASSWORD 시크릿 미설정' };
    }
    const { hash, salt } = await hashPassword(env.ADMIN_PASSWORD);
    await env.DB.prepare(
      `INSERT INTO users (username, password_hash, salt, nickname,
       status, role, created_at, approved_at)
       VALUES (?, ?, ?, ?, 'active', 'admin', ?, ?)`
    ).bind(name, hash, salt, name, nowIso(), nowIso()).run();
    return { seeded: true };
  }

  if (u.status !== 'active' || u.role !== 'admin') {
    await env.DB.prepare(
      "UPDATE users SET status='active', role='admin' WHERE id=?"
    ).bind(u.id).run();
    return { reactivated: true };
  }
  return { ok: true };
}

async function audit(env, actorId, action, target, detail) {
  try {
    await env.DB.prepare(
      `INSERT INTO audit_log (ts, actor_id, action, target, detail)
       VALUES (?, ?, ?, ?, ?)`
    ).bind(nowIso(), actorId || null, action,
           String(target ?? '').slice(0, 120),
           String(detail ?? '').slice(0, 400)).run();
  } catch (_) { /* 감사 실패가 본 기능을 막지는 않는다 */ }
}

// ═══════════════════════════════════════════════════════════════
//  레이트리밋 — username 과 IP 를 각각 센다
// ═══════════════════════════════════════════════════════════════
async function rateCheck(env, keys) {
  const now = Date.now();
  for (const k of keys) {
    const r = await env.DB.prepare(
      'SELECT fails, first_at, locked_until FROM login_attempts WHERE key=?'
    ).bind(k).first();
    if (!r) continue;
    if (r.locked_until && new Date(r.locked_until).getTime() > now) {
      const left = Math.ceil(
        (new Date(r.locked_until).getTime() - now) / 60000);
      return { locked: true, minutes: Math.max(1, left) };
    }
  }
  return { locked: false };
}

async function rateFail(env, keys) {
  const now = Date.now();
  for (const k of keys) {
    const r = await env.DB.prepare(
      'SELECT fails, first_at FROM login_attempts WHERE key=?'
    ).bind(k).first();

    // 관측 창을 넘겼으면 새로 센다
    const stale = r && (now - new Date(r.first_at).getTime())
                        > FAIL_WINDOW_MINUTES * 60000;
    const fails = (!r || stale) ? 1 : r.fails + 1;
    const lock = fails >= MAX_FAILS ? plusMinutes(LOCK_MINUTES) : null;

    await env.DB.prepare(
      `INSERT INTO login_attempts (key, fails, first_at, last_at, locked_until)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET
         fails=excluded.fails, first_at=excluded.first_at,
         last_at=excluded.last_at, locked_until=excluded.locked_until`
    ).bind(k, fails, (!r || stale) ? nowIso() : r.first_at, nowIso(), lock)
     .run();
  }
}

async function rateClear(env, keys) {
  for (const k of keys) {
    await env.DB.prepare('DELETE FROM login_attempts WHERE key=?')
      .bind(k).run();
  }
}

function clientIp(req) {
  return req.headers.get('CF-Connecting-IP')
      || req.headers.get('X-Forwarded-For')
      || 'unknown';
}

// ═══════════════════════════════════════════════════════════════
//  세션
// ═══════════════════════════════════════════════════════════════
async function createSession(env, userId, deviceLabel = '') {
  const token = randomToken(32);
  const exp = new Date(Date.now() + SESSION_DAYS * 86400000).toISOString();
  await env.DB.prepare(
    `INSERT INTO sessions (token, user_id, created_at, expires_at, device_label)
     VALUES (?, ?, ?, ?, ?)`
  ).bind(token, userId, nowIso(), exp,
         (deviceLabel || '').slice(0, 80)).run();
  await env.DB.prepare(
    `UPDATE users SET login_count = COALESCE(login_count,0)+1,
     last_login_at = ? WHERE id = ?`
  ).bind(nowIso(), userId).run();
  return token;
}

/** 만료 세션 청소 — 로그인 20회에 1번꼴로만 돈다(무료 쓰기 쿼터 절약). */
async function maybeSweep(env) {
  if (Math.random() > 0.05) return;
  try {
    await env.DB.prepare('DELETE FROM sessions WHERE expires_at < ?')
      .bind(nowIso()).run();
    await env.DB.prepare('DELETE FROM login_attempts WHERE last_at < ?')
      .bind(new Date(Date.now() - 86400000).toISOString()).run();
  } catch (_) { /* 청소 실패는 무시 */ }
}

async function getSession(env, token) {
  if (!token) return null;
  const r = await env.DB.prepare(
    `SELECT s.token, s.user_id, s.expires_at, s.device_label,
     u.username, u.nickname, u.status, u.role
     FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token = ?`
  ).bind(token).first();
  if (!r) return null;
  if (new Date(r.expires_at) < new Date()) {
    await env.DB.prepare('DELETE FROM sessions WHERE token=?')
      .bind(token).run();
    return null;
  }
  return r;
}

function getTokenFromReq(req) {
  const auth = req.headers.get('Authorization') || '';
  const m = auth.match(/^Bearer\s+(.+)$/i);
  if (m) return m[1].trim();
  return new URL(req.url).searchParams.get('token') || '';
}

async function requireAuth(req, env) {
  const s = await getSession(env, getTokenFromReq(req));
  return (s && s.status === 'active') ? s : null;
}

async function requireAdmin(req, env) {
  const s = await requireAuth(req, env);
  return (s && s.role === 'admin') ? s : null;
}

// ═══════════════════════════════════════════════════════════════
//  인증 라우트
// ═══════════════════════════════════════════════════════════════
const USERNAME_RE = /^[A-Za-z0-9._-]{3,32}$/;

function passwordProblem(pw) {
  if (typeof pw !== 'string' || pw.length < 8)
    return '패스워드는 최소 8자';
  if (pw.length > 200) return '패스워드가 너무 깁니다';
  if (/^\d+$/.test(pw)) return '숫자만으로는 안 됩니다';
  return null;
}

async function handleRegister(req, env) {
  const body = await req.json().catch(() => ({}));
  const username = (body.username || '').trim();
  const password = body.password || '';
  const nickname = (body.nickname || '').trim().slice(0, 40);

  if (!USERNAME_RE.test(username))
    return json({ ok: false,
      error: 'username 은 영문/숫자/._- 3~32자' }, { status: 400 }, env, req);
  const pwErr = passwordProblem(password);
  if (pwErr) return json({ ok: false, error: pwErr }, { status: 400 }, env, req);

  const existing = await env.DB.prepare(
    'SELECT id FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(username).first();
  if (existing)
    return json({ ok: false, error: '이미 존재하는 username' },
                { status: 400 }, env, req);

  const { hash, salt } = await hashPassword(password);
  await env.DB.prepare(
    `INSERT INTO users (username, password_hash, salt, nickname,
     status, role, created_at)
     VALUES (?, ?, ?, ?, 'pending', 'user', ?)`
  ).bind(username, hash, salt, nickname || username, nowIso()).run();

  return json({ ok: true,
    message: '가입 신청 완료 — 어드민 승인 대기 중입니다.',
    user: { username, nickname: nickname || username, status: 'pending' },
  }, {}, env, req);
}

async function handleLogin(req, env) {
  const body = await req.json().catch(() => ({}));
  const username = (body.username || '').trim();
  const password = body.password || '';
  if (!username || !password)
    return json({ ok: false, error: 'username/password 필요' },
                { status: 400 }, env, req);

  const keys = [`u:${username.toLowerCase()}`, `ip:${clientIp(req)}`];
  const gate = await rateCheck(env, keys);
  if (gate.locked)
    return json({ ok: false,
      error: `로그인 시도가 많아 ${gate.minutes}분 잠겼습니다`,
      locked_minutes: gate.minutes }, { status: 429 }, env, req);

  const u = await env.DB.prepare(
    `SELECT id, username, nickname, password_hash, salt, status, role
     FROM users WHERE username = ? COLLATE NOCASE`
  ).bind(username).first();

  // 사용자 존재 여부를 응답으로 흘리지 않는다.
  if (!u) {
    await hashPassword(password);          // 타이밍 맞추기
    await rateFail(env, keys);
    return json({ ok: false, error: '사용자/패스워드 불일치' },
                { status: 401 }, env, req);
  }

  const ok = await verifyPassword(password, u.password_hash, u.salt);
  if (!ok) {
    await rateFail(env, keys);
    return json({ ok: false, error: '사용자/패스워드 불일치' },
                { status: 401 }, env, req);
  }

  if (u.status === 'pending')
    return json({ ok: false, error: '승인 대기 중입니다' },
                { status: 403 }, env, req);
  if (u.status !== 'active')
    return json({ ok: false, error: '비활성 계정입니다' },
                { status: 403 }, env, req);

  await rateClear(env, keys);
  await maybeSweep(env);
  const token = await createSession(env, u.id,
    req.headers.get('User-Agent') || '');
  return json({
    ok: true, token,
    expires_in_days: SESSION_DAYS,
    user: { id: u.id, username: u.username, nickname: u.nickname,
            role: u.role, status: u.status },
  }, {}, env, req);
}

async function handleLogout(req, env) {
  const token = getTokenFromReq(req);
  if (token)
    await env.DB.prepare('DELETE FROM sessions WHERE token=?')
      .bind(token).run();
  return json({ ok: true }, {}, env, req);
}

async function handleLogoutAll(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ ok: false, error: 'auth required' },
                      { status: 401 }, env, req);
  await env.DB.prepare('DELETE FROM sessions WHERE user_id=?')
    .bind(s.user_id).run();
  await audit(env, s.user_id, 'logout_all', s.username, '');
  return json({ ok: true }, {}, env, req);
}

async function handleMe(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ authenticated: false }, {}, env, req);
  return json({
    authenticated: true,
    user: { id: s.user_id, username: s.username, nickname: s.nickname,
            role: s.role, status: s.status },
  }, {}, env, req);
}

async function handleSessions(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ ok: false, error: 'auth required' },
                      { status: 401 }, env, req);
  const rows = await env.DB.prepare(
    `SELECT created_at, expires_at, device_label,
            (token = ?) AS current
     FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 50`
  ).bind(s.token, s.user_id).all();
  return json({ ok: true, sessions: rows.results || [] }, {}, env, req);
}

async function handleChangePassword(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ ok: false, error: 'auth required' },
                      { status: 401 }, env, req);
  const { old_password, new_password } = await req.json().catch(() => ({}));
  const pwErr = passwordProblem(new_password);
  if (pwErr) return json({ ok: false, error: pwErr }, { status: 400 }, env, req);

  const u = await env.DB.prepare(
    'SELECT password_hash, salt FROM users WHERE id=?'
  ).bind(s.user_id).first();
  if (!u || !await verifyPassword(old_password || '',
                                  u.password_hash, u.salt))
    return json({ ok: false, error: '현재 패스워드가 다릅니다' },
                { status: 403 }, env, req);

  const { hash, salt } = await hashPassword(new_password);
  await env.DB.prepare(
    'UPDATE users SET password_hash=?, salt=? WHERE id=?'
  ).bind(hash, salt, s.user_id).run();
  // 비밀번호를 바꿨으면 다른 기기 세션은 모두 끊는다.
  await env.DB.prepare('DELETE FROM sessions WHERE user_id=? AND token<>?')
    .bind(s.user_id, s.token).run();
  await audit(env, s.user_id, 'change_password', s.username, '');
  return json({ ok: true, message: '변경 완료 — 다른 기기는 로그아웃됩니다' },
              {}, env, req);
}

// ═══════════════════════════════════════════════════════════════
//  어드민
// ═══════════════════════════════════════════════════════════════
async function handleAdminUsers(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 }, env, req);
  const rows = await env.DB.prepare(
    `SELECT id, username, nickname, status, role, created_at,
     approved_at, last_login_at, login_count, claude_used, claude_quota_date
     FROM users ORDER BY created_at DESC LIMIT 500`
  ).all();
  return json({ users: rows.results || [] }, {}, env, req);
}

async function handleAdminApprove(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 }, env, req);
  const { user_id } = await req.json().catch(() => ({}));
  if (!user_id) return json({ ok: false, error: 'user_id 필요' },
                            { status: 400 }, env, req);
  await env.DB.prepare(
    "UPDATE users SET status='active', approved_at=?, approved_by=? WHERE id=?"
  ).bind(nowIso(), s.user_id, user_id).run();
  await audit(env, s.user_id, 'approve', user_id, '');
  return json({ ok: true }, {}, env, req);
}

async function handleAdminReject(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 }, env, req);
  const { user_id } = await req.json().catch(() => ({}));
  if (!user_id) return json({ ok: false, error: 'user_id 필요' },
                            { status: 400 }, env, req);
  if (Number(user_id) === Number(s.user_id))
    return json({ ok: false, error: '본인 거부 불가' },
                { status: 400 }, env, req);
  await env.DB.prepare('DELETE FROM sessions WHERE user_id=?')
    .bind(user_id).run();
  await env.DB.prepare("UPDATE users SET status='rejected' WHERE id=?")
    .bind(user_id).run();
  await audit(env, s.user_id, 'reject', user_id, '');
  return json({ ok: true }, {}, env, req);
}

// ═══════════════════════════════════════════════════════════════
//  메인 PC 등록 + /go/<username> 리다이렉트
// ═══════════════════════════════════════════════════════════════
/**
 * 사용자가 준 URL 로 그대로 리다이렉트하면 오픈 리다이렉트가 된다.
 * (누구나 /pc/register 로 임의 주소를 올리고, /go/<id> 링크를 뿌려
 *  이 도메인의 신뢰를 빌릴 수 있다.)
 * 그래서 https 만, 호스트 형태가 정상인 것만 받는다.
 */
function normalizePublicUrl(raw) {
  let u;
  try { u = new URL(String(raw || '').trim()); }
  catch (_) { return { error: 'URL 형식이 아닙니다' }; }
  if (u.protocol !== 'https:')
    return { error: 'https:// 주소만 등록할 수 있습니다' };
  if (!/^[a-z0-9.-]+$/i.test(u.hostname) || !u.hostname.includes('.'))
    return { error: '호스트명이 올바르지 않습니다' };
  if (/^(localhost|127\.|0\.|10\.|192\.168\.|169\.254\.)/i.test(u.hostname))
    return { error: '사설/로컬 주소는 등록할 수 없습니다' };
  u.hash = '';
  const s = u.toString();
  if (s.length > 300) return { error: 'URL 이 너무 깁니다' };
  return { url: s };
}

async function handleRegisterPC(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ error: 'auth required' }, { status: 401 }, env, req);
  const { pc_label, public_url } = await req.json().catch(() => ({}));
  const norm = normalizePublicUrl(public_url);
  if (norm.error)
    return json({ ok: false, error: norm.error }, { status: 400 }, env, req);

  await env.DB.prepare(
    `INSERT INTO user_pcs (user_id, pc_label, public_url, last_seen_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(user_id) DO UPDATE SET
       pc_label=excluded.pc_label,
       public_url=excluded.public_url,
       last_seen_at=excluded.last_seen_at`
  ).bind(s.user_id, (pc_label || '').slice(0, 80), norm.url, nowIso()).run();
  return json({ ok: true, public_url: norm.url }, {}, env, req);
}

async function handlePCStatus(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ error: 'auth required' }, { status: 401 }, env, req);
  const pc = await env.DB.prepare(
    'SELECT pc_label, public_url, last_seen_at FROM user_pcs WHERE user_id=?'
  ).bind(s.user_id).first();
  return json({ ok: true, registered: !!pc, pc: pc || null,
                go_url: `/go/${s.username}` }, {}, env, req);
}

async function handleUnregisterPC(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ error: 'auth required' }, { status: 401 }, env, req);
  await env.DB.prepare('DELETE FROM user_pcs WHERE user_id=?')
    .bind(s.user_id).run();
  return json({ ok: true }, {}, env, req);
}

async function handleResolve(username, env, req) {
  const u = await env.DB.prepare(
    'SELECT id FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(username).first();
  if (!u) return json({ ok: false, error: '사용자 없음' },
                      { status: 404 }, env, req);
  const pc = await env.DB.prepare(
    'SELECT public_url FROM user_pcs WHERE user_id = ?'
  ).bind(u.id).first();
  if (!pc || !pc.public_url)
    return json({ ok: false, error: 'PC 미등록 — 앱에서 외부 접근을 켜세요' },
                { status: 404 }, env, req);
  // 저장 시점에 검증했지만, 스키마가 손대졌을 가능성까지 막는다.
  const norm = normalizePublicUrl(pc.public_url);
  if (norm.error)
    return json({ ok: false, error: '등록된 주소가 올바르지 않습니다' },
                { status: 400 }, env, req);
  return Response.redirect(norm.url, 302);
}

// ═══════════════════════════════════════════════════════════════
//  라우터
// ═══════════════════════════════════════════════════════════════
export default {
  async fetch(req, env, ctx) {
    if (req.method === 'OPTIONS')
      return new Response(null, { status: 204, headers: corsHeaders(env, req) });

    const p = new URL(req.url).pathname;
    try {
      if (!env.DB)
        return json({ ok: false, error: 'D1 바인딩(DB)이 없습니다' },
                    { status: 500 }, env, req);

      ctx.waitUntil(ensureAdmin(env));

      if (p === '/' || p === '/health')
        return json({ ok: true, service: 'iaw-auth', version: 2,
                      time: nowIso() }, {}, env, req);

      const POST = req.method === 'POST';
      if (p === '/auth/register' && POST)  return handleRegister(req, env);
      if (p === '/auth/login' && POST)     return handleLogin(req, env);
      if (p === '/auth/logout' && POST)    return handleLogout(req, env);
      if (p === '/auth/logout_all' && POST) return handleLogoutAll(req, env);
      if (p === '/auth/change_password' && POST)
        return handleChangePassword(req, env);
      if (p === '/auth/me')                return handleMe(req, env);
      if (p === '/auth/sessions')          return handleSessions(req, env);

      if (p === '/admin/users')            return handleAdminUsers(req, env);
      if (p === '/admin/approve' && POST)  return handleAdminApprove(req, env);
      if (p === '/admin/reject' && POST)   return handleAdminReject(req, env);

      if (p === '/pc/register' && POST)    return handleRegisterPC(req, env);
      if (p === '/pc/status')              return handlePCStatus(req, env);
      if (p === '/pc/unregister' && POST)  return handleUnregisterPC(req, env);

      const go = p.match(/^\/go\/([A-Za-z0-9._-]{3,32})$/);
      if (go) return handleResolve(go[1], env, req);

      return json({ error: 'not found', path: p }, { status: 404 }, env, req);
    } catch (e) {
      // 내부 오류 내용을 그대로 노출하지 않는다.
      console.error('worker error', p, e && e.stack || e);
      return json({ error: '서버 오류' }, { status: 500 }, env, req);
    }
  },
};
