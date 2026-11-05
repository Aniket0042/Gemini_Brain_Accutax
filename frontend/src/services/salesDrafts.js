/**
 * salesDrafts.js — the buttons of a sales invoice / quote draft card (backend api/action_routes.py).
 * Each call returns { block }: the card as it is now, built by the server.
 */

function headers(token) {
  const h = { 'Content-Type': 'application/json', Accept: 'application/json' };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function call(path, { method = 'GET', body, token } = {}) {
  const res = await fetch(path, { method, headers: headers(token), body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || data.message || 'Something went wrong. Try again.');
    err.status = res.status;
    throw err;
  }
  return data;
}

export function fetchDraft(actionId, token) {
  return call(`/api/v1/actions/${encodeURIComponent(actionId)}`, { token });
}

export function saveDraft(actionId, version, token) {
  return call(`/api/v1/actions/${encodeURIComponent(actionId)}/confirm`, { method: 'POST', body: { version }, token });
}

export function discardDraft(actionId, token) {
  return call(`/api/v1/actions/${encodeURIComponent(actionId)}/discard`, { method: 'POST', token });
}
