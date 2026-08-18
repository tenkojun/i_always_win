/**
 * JIQT 중앙 인증 Worker
 * =====================
 * - 가입/로그인/세션/어드민 승인
 * - A6 redirect: username → 본인 메인 PC 외부 URL
 *
 * 환경:
 *   DB              : D1 binding
 *   ADMIN_USERNAME  : 초기 어드민 ID (예: JUNHWA)
 *   ADMIN_PASSWORD  : 초기 어드민 패스워드 (secret)
 *   SESSION_SECRET  : 세션 토큰 서명용 (secret, 32+ 바이트)
 */

// ─── 공통 헬퍼 ────────────────────────────────────────────────
const json = (data, init = {}) =>
  new Response(JSON.stringify(data), {
    status: init.status || 200,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
      ...(init.headers || {}),
    },
  });

const nowIso = () => new Date().toISOString();
const today = () => new Date().toISOString().slice(0, 10);

// ─── 비밀번호 해싱 (PBKDF2 — 호환 가능) ──────────────────────
async function hashPassword(pw, saltHex) {
  if (!saltHex) {
    const s = crypto.getRandomValues(new Uint8Array(16));
    saltHex = bufToHex(s);
  }
  const salt = hexToBuf(saltHex);
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(pw),
    'PBKDF2', false, ['deriveBits']
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: 200000, hash: 'SHA-256' },
    key, 256
  );
  return { hash: bufToHex(bits), salt: saltHex };
}

async function verifyPassword(pw, hashHex, saltHex) {
  const { hash } = await hashPassword(pw, saltHex);
  return constantEq(hash, hashHex);
}

function constantEq(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function bufToHex(buf) {
  const a = new Uint8Array(buf);
  return Array.from(a).map(b => b.toString(16).padStart(2, '0')).join('');
}

function hexToBuf(hex) {
  const a = new Uint8Array(hex.length / 2);
  for (let i = 0; i < a.length; i++) a[i] = parseInt(hex.substr(i * 2, 2), 16);
  return a.buffer;
}

function randomToken(bytes = 32) {
  const b = crypto.getRandomValues(new Uint8Array(bytes));
  return bufToHex(b);
}

// ─── 어드민 seed (첫 호출 시) ─────────────────────────────────
async function ensureAdmin(env) {
  const u = await env.DB.prepare(
    'SELECT id, status, role FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(env.ADMIN_USERNAME).first();
  if (!u) {
    const adminPw = env.ADMIN_PASSWORD || 'WNSGHK';  // 기본값 (개발용)
    const { hash, salt } = await hashPassword(adminPw);
    await env.DB.prepare(
      `INSERT INTO users (username, password_hash, salt, nickname,
       status, role, created_at, approved_at)
       VALUES (?, ?, ?, ?, 'active', 'admin', ?, ?)`
    ).bind(env.ADMIN_USERNAME, hash, salt, env.ADMIN_USERNAME,
           nowIso(), nowIso()).run();
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

// ─── 세션 ─────────────────────────────────────────────────────
async function createSession(env, userId, deviceLabel = '') {
  const token = randomToken(32);
  const exp = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
  await env.DB.prepare(
    `INSERT INTO sessions (token, user_id, created_at, expires_at, device_label)
     VALUES (?, ?, ?, ?, ?)`
  ).bind(token, userId, nowIso(), exp, (deviceLabel || '').slice(0, 80)).run();
  // login 통계 갱신
  await env.DB.prepare(
    `UPDATE users SET login_count = COALESCE(login_count,0)+1,
     last_login_at = ? WHERE id = ?`
  ).bind(nowIso(), userId).run();
  return token;
}

async function getSession(env, token) {
  if (!token) return null;
  const r = await env.DB.prepare(
    `SELECT s.token, s.user_id, s.expires_at, s.device_label,
     u.id, u.username, u.nickname, u.status, u.role
     FROM sessions s JOIN users u ON u.id = s.user_id
     WHERE s.token = ?`
  ).bind(token).first();
  if (!r) return null;
  if (new Date(r.expires_at) < new Date()) {
    await env.DB.prepare('DELETE FROM sessions WHERE token=?').bind(token).run();
    return null;
  }
  return r;
}

function getTokenFromReq(req) {
  // Authorization: Bearer <token> 또는 ?token=...
  const auth = req.headers.get('Authorization') || '';
  const m = auth.match(/^Bearer\s+(.+)$/i);
  if (m) return m[1];
  const url = new URL(req.url);
  return url.searchParams.get('token') || '';
}

async function requireAuth(req, env) {
  const sess = await getSession(env, getTokenFromReq(req));
  if (!sess || sess.status !== 'active') return null;
  return sess;
}

async function requireAdmin(req, env) {
  const sess = await requireAuth(req, env);
  if (!sess || sess.role !== 'admin') return null;
  return sess;
}

// ─── 라우트 핸들러 ────────────────────────────────────────────
async function handleRegister(req, env) {
  const body = await req.json().catch(() => ({}));
  const username = (body.username || '').trim();
  const password = body.password || '';
  const nickname = (body.nickname || '').trim().slice(0, 40);
  if (username.length < 3)
    return json({ ok: false, error: 'username 최소 3자' }, { status: 400 });
  if (password.length < 6)
    return json({ ok: false, error: '패스워드 최소 6자' }, { status: 400 });
  if (nickname && nickname.length < 2)
    return json({ ok: false, error: '닉네임 최소 2자' }, { status: 400 });
  const existing = await env.DB.prepare(
    'SELECT id FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(username).first();
  if (existing)
    return json({ ok: false, error: '이미 존재하는 username' }, { status: 400 });
  const { hash, salt } = await hashPassword(password);
  await env.DB.prepare(
    `INSERT INTO users (username, password_hash, salt, nickname,
     status, role, created_at)
     VALUES (?, ?, ?, ?, 'pending', 'user', ?)`
  ).bind(username, hash, salt, nickname || username, nowIso()).run();
  return json({ ok: true,
    message: '가입 신청 완료 — 어드민 승인 대기 중입니다.',
    user: { username, nickname: nickname || username, status: 'pending' } });
}

async function handleLogin(req, env) {
  const body = await req.json().catch(() => ({}));
  const username = (body.username || '').trim();
  const password = body.password || '';
  if (!username || !password)
    return json({ ok: false, error: 'username/password 필요' }, { status: 400 });
  const u = await env.DB.prepare(
    `SELECT id, username, nickname, password_hash, salt, status, role
     FROM users WHERE username = ? COLLATE NOCASE`
  ).bind(username).first();
  if (!u)
    return json({ ok: false, error: '사용자/패스워드 불일치' }, { status: 401 });
  if (u.status === 'pending')
    return json({ ok: false, error: '승인 대기 중입니다' }, { status: 403 });
  if (u.status === 'rejected')
    return json({ ok: false, error: '비활성 계정입니다' }, { status: 403 });
  const ok = await verifyPassword(password, u.password_hash, u.salt);
  if (!ok)
    return json({ ok: false, error: '사용자/패스워드 불일치' }, { status: 401 });
  const token = await createSession(env, u.id,
    req.headers.get('User-Agent') || '');
  return json({
    ok: true, token,
    user: { id: u.id, username: u.username, nickname: u.nickname,
            role: u.role, status: u.status },
  });
}

async function handleLogout(req, env) {
  const token = getTokenFromReq(req);
  if (token)
    await env.DB.prepare('DELETE FROM sessions WHERE token=?').bind(token).run();
  return json({ ok: true });
}

async function handleMe(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ authenticated: false });
  return json({
    authenticated: true,
    user: { id: s.user_id, username: s.username, nickname: s.nickname,
            role: s.role, status: s.status },
  });
}

// ─── 어드민 ──────────────────────────────────────────────────
async function handleAdminUsers(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 });
  const rows = await env.DB.prepare(
    `SELECT id, username, nickname, status, role, created_at,
     approved_at, last_login_at, login_count, claude_used,
     claude_quota_date
     FROM users ORDER BY created_at DESC`
  ).all();
  return json({ users: rows.results || [] });
}

async function handleAdminApprove(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 });
  const { user_id } = await req.json().catch(() => ({}));
  if (!user_id) return json({ ok: false, error: 'user_id 필요' }, { status: 400 });
  await env.DB.prepare(
    `UPDATE users SET status='active', approved_at=?, approved_by=?
     WHERE id=?`
  ).bind(nowIso(), s.user_id, user_id).run();
  return json({ ok: true });
}

async function handleAdminReject(req, env) {
  const s = await requireAdmin(req, env);
  if (!s) return json({ error: 'admin only' }, { status: 403 });
  const { user_id } = await req.json().catch(() => ({}));
  if (!user_id) return json({ ok: false, error: 'user_id 필요' }, { status: 400 });
  if (user_id === s.user_id)
    return json({ ok: false, error: '본인 거부 불가' }, { status: 400 });
  await env.DB.prepare('DELETE FROM sessions WHERE user_id=?').bind(user_id).run();
  await env.DB.prepare("UPDATE users SET status='rejected' WHERE id=?")
    .bind(user_id).run();
  return json({ ok: true });
}

// ─── A6: 메인 PC 등록 + redirect ─────────────────────────────
async function handleRegisterPC(req, env) {
  const s = await requireAuth(req, env);
  if (!s) return json({ error: 'auth required' }, { status: 401 });
  const { pc_label, public_url } = await req.json().catch(() => ({}));
  if (!public_url)
    return json({ ok: false, error: 'public_url 필요' }, { status: 400 });
  await env.DB.prepare(
    `INSERT INTO user_pcs (user_id, pc_label, public_url, last_seen_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT(user_id) DO UPDATE SET
       pc_label=excluded.pc_label,
       public_url=excluded.public_url,
       last_seen_at=excluded.last_seen_at`
  ).bind(s.user_id, (pc_label || '').slice(0, 80),
         public_url.slice(0, 200), nowIso()).run();
  return json({ ok: true });
}

async function handleResolve(username, env) {
  const u = await env.DB.prepare(
    'SELECT id FROM users WHERE username = ? COLLATE NOCASE'
  ).bind(username).first();
  if (!u) return json({ ok: false, error: '사용자 없음' }, { status: 404 });
  const pc = await env.DB.prepare(
    'SELECT public_url, pc_label, last_seen_at FROM user_pcs WHERE user_id = ?'
  ).bind(u.id).first();
  if (!pc || !pc.public_url)
    return json({ ok: false, error: 'PC 미등록' }, { status: 404 });
  // 302 redirect
  return Response.redirect(pc.public_url, 302);
}

// ─── 메인 라우터 ──────────────────────────────────────────────
export default {
  async fetch(req, env, ctx) {
    if (req.method === 'OPTIONS') return json({});
    const url = new URL(req.url);
    const p = url.pathname;
    try {
      // 어드민 seed (lazy)
      ctx.waitUntil(ensureAdmin(env));

      if (p === '/' || p === '/health')
        return json({ ok: true, service: 'jiqt-auth', time: nowIso() });
      if (p === '/auth/register' && req.method === 'POST')
        return await handleRegister(req, env);
      if (p === '/auth/login' && req.method === 'POST')
        return await handleLogin(req, env);
      if (p === '/auth/logout' && req.method === 'POST')
        return await handleLogout(req, env);
      if (p === '/auth/me')
        return await handleMe(req, env);
      if (p === '/admin/users')
        return await handleAdminUsers(req, env);
      if (p === '/admin/approve' && req.method === 'POST')
        return await handleAdminApprove(req, env);
      if (p === '/admin/reject' && req.method === 'POST')
        return await handleAdminReject(req, env);
      if (p === '/pc/register' && req.method === 'POST')
        return await handleRegisterPC(req, env);
      // A6 redirect: /go/<username>
      const goMatch = p.match(/^\/go\/([\w._-]+)$/);
      if (goMatch) return await handleResolve(goMatch[1], env);

      return json({ error: 'not found', path: p }, { status: 404 });
    } catch (e) {
      return json({ error: String(e.message || e) }, { status: 500 });
    }
  },
};
