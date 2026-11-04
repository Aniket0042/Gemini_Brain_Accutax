// Legacy sign-in, for deployments without the Accutax BFF (the VM): the AI backend's own login and a bearer token.
// Same surface as session-client.mjs where the app uses it, so App and api.js do not care which one runs.
const KEY = 'accutax_ai_legacy_session';

/** The user id a token names. Only used to key this tab's storage; the backend still verifies the token. */
export function tokenUserId(token) {
  try {
    const part = String(token).split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const claims = JSON.parse(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '=')));
    const id = Number(claims.userId ?? claims.user_id ?? claims.sub);
    return Number.isSafeInteger(id) ? id : null;
  } catch {
    return null;
  }
}

export function createLegacyClient({ fetcher, storage, location = globalThis.location }) {
  const listeners = new Set();
  const read = () => {
    try {
      const value = JSON.parse(storage.getItem(KEY) || 'null');
      return value?.token && value?.user?.id != null ? { authenticated: true, ...value } : null;
    } catch {
      return null;
    }
  };
  let profile = read();
  const setProfile = (value) => {
    profile = value ? { authenticated: true, ...value } : null;
    try {
      if (value) storage.setItem(KEY, JSON.stringify(value));
      else storage.removeItem(KEY);
    } catch { /* storage blocked: this tab keeps the session in memory */ }
    for (const listener of listeners) listener(profile);
  };
  // 'ai/api/v1/x' (the BFF path) and '/api/v1/x' both mean this origin's /api/v1/x.
  const endpoint = (path) => new URL('/' + String(path).replace(/^\/+/, '').replace(/^ai\//, ''), location.origin).href;
  const isApi = (input) => {
    try {
      const url = new URL(typeof input === 'object' && input?.url ? input.url : String(input), location.origin);
      return url.origin === location.origin && url.pathname.startsWith('/api/v1/');
    } catch {
      return false;
    }
  };
  const authHeaders = () => (profile ? { Authorization: `Bearer ${profile.token}` } : {});
  const prepare = async (input, init = {}) => {
    if (!isApi(input) || !profile) return init;
    const headers = new Headers(typeof input === 'object' && input?.headers ? input.headers : undefined);
    new Headers(init.headers).forEach((value, key) => headers.set(key, value));
    headers.set('Authorization', `Bearer ${profile.token}`);
    return { ...init, headers };
  };
  const observe = (response) => {
    if (response.status === 401 && profile) setProfile(null);
    return response;
  };
  const request = async (path, init = {}) => {
    const url = endpoint(path);
    return observe(await fetcher(url, await prepare(url, init)));
  };
  const json = async (path, init = {}) => {
    const response = await request(path, init);
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      const error = new Error(typeof body?.detail === 'string' ? body.detail : 'The request could not be completed.');
      error.status = response.status;
      throw error;
    }
    return body;
  };
  return {
    endpoint, isApi, prepare, observe, request, json, authHeaders,
    load: async () => profile,
    getSnapshot: () => profile,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    /** Email and password against the AI backend. Resolves to the profile; rejects with a message for the user. */
    async signIn(email, password) {
      const response = await fetcher(endpoint('api/v1/auth/login-json'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ username: email, password }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error((typeof body?.detail === 'string' && body.detail) || 'Authentication failed');
      setProfile({ token: body.access_token, user: { id: body.user_id, email: body.email, name: body.email } });
      return profile;
    },
    /** A token the Accutax dashboard handed over (widget handoff). */
    useToken(token, { email = '', name = '' } = {}) {
      setProfile({ token, user: { id: tokenUserId(token) ?? 'accutax', email, name: name || email || 'Accutax user' } });
      return profile;
    },
    async logout() { setProfile(null); },
    async activity() {},
    login() {},
  };
}
