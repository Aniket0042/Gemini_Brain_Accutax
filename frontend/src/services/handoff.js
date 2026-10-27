/**
 * handoff.js — Continue from the Accutax dashboard with a one-time code.
 *
 * The newer dashboard opens the chat at ?handoff=1&code=... and sends nothing else. The code is redeemed by our
 * backend (POST /api/v1/auth/handoff), server to server with the Accutax backend; it works once and for 60 seconds.
 * The older dashboard (the VM) opens ?handoff=1 without a code and posts the session instead; App.jsx keeps that path.
 */

const CODE_PATTERN = /^[A-Za-z0-9_-]{20,128}$/;
// sessionStorage key: codes this tab already sent, so a re-render or reload never spends one twice.
export const USED_CODES_KEY = 'accutax_handoff_codes_used';

/** The handoff code in a URL's query string, or null when there is none or it is malformed. */
export function handoffCodeFrom(search) {
  try {
    const params = new URLSearchParams(search || '');
    if (params.get('handoff') !== '1') return null;
    const code = params.get('code');
    return code && CODE_PATTERN.test(code) ? code : null;
  } catch {
    return null;
  }
}

/** The URL with the one-time code removed, or null when it has none. */
export function urlWithoutCode(href) {
  try {
    const url = new URL(href);
    if (!url.searchParams.has('code')) return null;
    url.searchParams.delete('code');
    const qs = url.searchParams.toString();
    return `${url.pathname}${qs ? `?${qs}` : ''}${url.hash}`;
  } catch {
    return null;
  }
}

/** Remember the code as spent; false when this tab has spent it before. */
export function claimCode(code, storage) {
  try {
    const used = JSON.parse(storage.getItem(USED_CODES_KEY) || '[]');
    if (used.includes(code)) return false;
    storage.setItem(USED_CODES_KEY, JSON.stringify([...used, code].slice(-10)));
  } catch {
    /* storage unavailable: the backend still refuses a second use */
  }
  return true;
}

/** The redeemed session in the shape the postMessage handoff uses, so both paths share applyWidgetHandoff. */
export function toWidgetHandoff(res) {
  return {
    type: 'accutax-widget-handoff',
    token: res.access_token,
    email: res.email || '',
    name: res.name || res.email || '',
    orgId: res.organization_id ?? null,
    sessionId: res.session_id || null,
    currentPage: res.current_page || null,
    conversation: Array.isArray(res.conversation) ? res.conversation : [],
  };
}
