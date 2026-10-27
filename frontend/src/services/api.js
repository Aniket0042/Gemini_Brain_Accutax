/**
 * api.js — API client service for communicating with the Gemini Brain FastAPI backend.
 * Handles JWT authentication, query submission, live SSE streaming, and model health diagnostics.
 */

import { uploadErrorMessage } from './attachments';

export const loginUser = async (email, password) => {
  const response = await fetch('/api/v1/auth/login-json', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ username: email, password }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(errorData.detail || 'Authentication failed');
  }

  return await response.json();
};

// Redeem the dashboard's one-time handoff code (services/handoff.js). Never retried: a code works once.
export const redeemHandoffCode = async (code) => {
  const response = await fetch('/api/v1/auth/handoff', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({ code }),
  });

  if (!response.ok) {
    // Our backend explains itself in `detail`; a proxy or network error does not, so say it plainly.
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.detail || 'Could not continue from the dashboard. Sign in below.');
  }

  return await response.json();
};

export const fetchQueryResponse = async (payload, token = '', signal = undefined) => {
  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch('/api/v1/query', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal,
  });

  const responseData = await response.json().catch(() => null);

  if (!response.ok) {
    if (responseData && responseData.notice) {
      return responseData;
    }
    throw new Error((responseData && responseData.detail) || `Server returned status ${response.status}`);
  }

  return responseData;
};

export const fetchModelHealth = async (token = '') => {
  const headers = { 'Accept': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch('/api/v1/health/models', {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch model health diagnostics: ${response.statusText}`);
  }

  return await response.json();
};

export const streamQueryResponse = (payload, onChunk, onError, onComplete, token = '') => {
  const controller = new AbortController();

  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'text/event-stream',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  fetch('/api/v1/query/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        if (response.status === 401) {
          localStorage.removeItem('gemini_brain_user');
        }
        // Read the body once: a second read of a used body fails.
        const raw = await response.text().catch(() => '');
        let errorData = null;
        try { errorData = JSON.parse(raw); } catch { /* not JSON */ }
        if (errorData && errorData.notice) {
          onChunk({ final_result: errorData });
          if (onComplete) onComplete();
          return;
        }
        // A refused attachment (404, 409, 422 ...) comes with a plain message for the user.
        const detail = errorData && errorData.detail;
        if (typeof detail === 'string' && detail) throw new Error(detail);
        if (detail && typeof detail === 'object' && detail.message) throw new Error(String(detail.message));
        throw new Error(`Streaming failed (${response.status}): ${raw || response.statusText}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';

        for (const part of parts) {
          const lines = part.split('\n');
          for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('data: ')) {
              const jsonStr = trimmed.slice(6);
              if (jsonStr === '[DONE]') {
                continue;
              }
              try {
                const data = JSON.parse(jsonStr);
                onChunk(data);
              } catch (e) {
                console.warn('Could not parse SSE chunk:', jsonStr);
              }
            }
          }
        }
      }
      if (onComplete) onComplete();
    })
    .catch((err) => {
      if (err.name !== 'AbortError' && onError) {
        onError(err.message || 'Stream connection error');
      }
    });

  return controller;
};

// GET /models lists the one answering model; the app only uses the call to
// spot an expired session (see App.jsx).
/** The specialist agents (GET /api/v1/agents): name, job, tools, and the question "Run now" sends. */
export const fetchAgents = async (token = '') => {
  const headers = { 'Accept': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  const response = await fetch('/api/v1/agents', { method: 'GET', headers });
  if (!response.ok) {
    throw new Error(`Failed to load the agents: ${response.statusText}`);
  }
  return await response.json();
};

export const fetchModelCatalog = async (token = '') => {
  const headers = { 'Accept': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch('/api/v1/models', {
    method: 'GET',
    headers,
  });

  if (response.status === 401) {
    // The stored token is no longer valid — most often because the service was
    // restarted. Surface it as an expired session so the app can send the user
    // back to sign in, rather than leaving them apparently logged in with a
    // half-working UI.
    const err = new Error('Your session has expired. Please sign in again.');
    err.code = 'SESSION_EXPIRED';
    throw err;
  }

  if (!response.ok) {
    throw new Error(`Failed to load the model catalog: ${response.statusText}`);
  }

  return await response.json();
};

export const fetchTenants = async (token = '') => {
  const headers = { 'Accept': 'application/json' };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch('/api/v1/tenants', {
    method: 'GET',
    headers,
  });

  if (!response.ok) {
    throw new Error(`Failed to fetch accessible tenants: ${response.statusText}`);
  }

  return await response.json();
};

export const fetchChatSessions = async (organizationId, token = '') => {
  const headers = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const params = new URLSearchParams();
  if (organizationId != null && organizationId !== '') {
    params.set('organization_id', String(organizationId));
  }
  const qs = params.toString();
  const response = await fetch(`/api/v1/sessions${qs ? `?${qs}` : ''}`, { headers });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((data && data.detail) || 'Failed to list chat sessions');
  }
  return data;
};

export const fetchSessionMessages = async (sessionId, token = '', limit = 50) => {
  const headers = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`,
    { headers }
  );
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((data && data.detail) || 'Failed to load chat messages');
  }
  return data;
};

// `organizationId` may be one id or an array of ids. A multi-org thread is
// created with its whole selection, which the backend then fixes for the
// thread's lifetime.
export const createChatSession = async (organizationId, token = '', sessionId) => {
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const ids = (Array.isArray(organizationId) ? organizationId : [organizationId])
    .filter((o) => o != null && o !== '')
    .map(Number);
  const body = ids.length > 1
    ? { organization_ids: ids }
    : { organization_id: ids.length === 1 ? ids[0] : undefined };
  if (sessionId) body.session_id = sessionId;
  const response = await fetch('/api/v1/sessions', {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error((data && data.detail) || 'Failed to create chat session');
  }
  return data;
};

export const deleteChatSession = async (sessionId, token = '') => {
  const headers = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
    headers,
  });
  if (response.status === 204 || response.ok) return true;
  const data = await response.json().catch(() => null);
  throw new Error((data && data.detail) || 'Failed to delete chat session');
};

// ── Files attached in chat (docs/DOCUMENT_UPLOAD_PLAN.md) ────────────────────

const authHeaders = (token) => (token ? { Authorization: `Bearer ${token}` } : {});

// What the composer needs: whether this user may attach files, and the limits. A failure means "off".
export const fetchUploadConfig = async (token = '') => {
  try {
    const response = await fetch('/api/v1/uploads/config', { headers: { Accept: 'application/json', ...authHeaders(token) } });
    if (!response.ok) return { enabled: false };
    return await response.json();
  } catch {
    return { enabled: false };
  }
};

const failure = (status, body) => {
  const err = new Error(uploadErrorMessage(status, body));
  err.status = status;
  err.code = body && body.detail && body.detail.code ? body.detail.code : '';
  return err;
};

// Upload one file with progress (fetch has none). Returns { promise, abort }; the promise resolves to the
// server's UploadInfo and rejects with an Error whose message is for the user.
export const uploadFile = (file, { sessionId, organizationIds = [], token = '', onProgress } = {}) => {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open('POST', '/api/v1/uploads');
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(failure(xhr.status, body));
    };
    xhr.onerror = () => reject(failure(0, null));
    xhr.onabort = () => reject(Object.assign(new Error('Upload cancelled.'), { aborted: true }));
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('session_id', sessionId);
    form.append('organization_ids', (organizationIds || []).join(','));
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
};

export const fetchUpload = async (uploadId, token = '') => {
  const response = await fetch(`/api/v1/uploads/${encodeURIComponent(uploadId)}`, {
    headers: { Accept: 'application/json', ...authHeaders(token) },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);
  return body;
};

// Removes a file that was not sent yet. Best effort: a draft the server keeps expires after a day.
export const deleteUpload = async (uploadId, token = '') => {
  try {
    await fetch(`/api/v1/uploads/${encodeURIComponent(uploadId)}`, { method: 'DELETE', headers: authHeaders(token) });
  } catch { /* ignore */ }
};

// ── Privacy snapshots (PII masking proof; admins in PII_SNAPSHOT_ADMINS only) ──
export const fetchPrivacyAccess = async (token = '') => {
  try {
    const response = await fetch('/api/v1/privacy/access', { headers: { Accept: 'application/json', ...authHeaders(token) } });
    return response.ok ? await response.json() : { admin: false };
  } catch {
    return { admin: false };
  }
};

export const fetchPrivacySnapshots = async (token = '', limit = 100, includeTest = false) => {
  const response = await fetch(`/api/v1/privacy/snapshots?limit=${limit}${includeTest ? '&include_test=true' : ''}`, {
    headers: { Accept: 'application/json', ...authHeaders(token) },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);
  return body?.snapshots || [];
};

export const fetchPrivacySnapshot = async (id, token = '') => {
  const response = await fetch(`/api/v1/privacy/snapshots/${encodeURIComponent(id)}`, {
    headers: { Accept: 'application/json', ...authHeaders(token) },
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);
  return body;
};

// The snapshot as a self-contained HTML page, saved as a file (open it and print to PDF to share).
export const downloadPrivacySnapshot = async (id, token = '') => {
  const response = await fetch(`/api/v1/privacy/snapshots/${encodeURIComponent(id)}/export`, { headers: authHeaders(token) });
  if (!response.ok) throw failure(response.status, await response.json().catch(() => null));
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = `privacy-snapshot-${id}.html`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
};

// Live before/after demo: one question with masking on; the reply has the same shape as a snapshot.
export const runPrivacyDemo = async (question, organizationIds = [], token = '') => {
  const response = await fetch('/api/v1/privacy/demo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ question, organization_ids: organizationIds }),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw failure(response.status, body);
  return body;
};

// ── AI usage (docs/ORG_TOKEN_LIMITS_STRATEGY.md): bars for everyone, the owner page, the admin page ──
const usageJson = async (path, token, init = {}) => {
  const response = await fetch(`/api/v1/usage${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...authHeaders(token) },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch { /* keep the status */ }
    throw new Error(typeof detail === 'string' ? detail : detail.message || 'Request failed');
  }
  return response.json();
};

// null when usage tracking is off or the call fails: the app then shows no meter.
export const fetchUsage = async (orgIds = [], token = '') => {
  try {
    const qs = orgIds.length ? `?org_ids=${orgIds.map(Number).join(',')}` : '';
    return await usageJson(qs, token);
  } catch {
    return null;
  }
};
export const fetchOwnerUsage = (token = '') => usageJson('/owner', token);
export const fetchMyUsage = (token = '') => usageJson('/me', token);
export const setMemberCap = (userId, dailyCredits, disabled, token = '') =>
  usageJson(`/owner/members/${encodeURIComponent(userId)}`, token, {
    method: 'PUT', body: JSON.stringify({ daily_credits: dailyCredits, disabled }),
  });
export const fetchUsageAdmin = (token = '') => usageJson('/admin', token);
// One person's use of a bucket this month; ownerId null: the caller's own bucket (owner page).
export const fetchMemberUsage = (userId, ownerId = null, token = '') => usageJson(
  ownerId == null
    ? `/owner/members/${encodeURIComponent(userId)}/events`
    : `/admin/owners/${encodeURIComponent(ownerId)}/members/${encodeURIComponent(userId)}/events`,
  token,
);
export const searchUsageOwners = (text, token = '') => usageJson(`/admin/owners?q=${encodeURIComponent(text)}`, token);
export const fetchUsageAdminOwner = (ownerId, token = '') => usageJson(`/admin/owners/${encodeURIComponent(ownerId)}`, token);
export const setOwnerPlan = (ownerId, plan, monthlyCredits, userDailyCredits, token = '') =>
  usageJson(`/admin/owners/${encodeURIComponent(ownerId)}/plan`, token, {
    method: 'PUT', body: JSON.stringify({ plan, monthly_credits: monthlyCredits, user_daily_credits: userDailyCredits }),
  });
export const addOwnerTopup = (ownerId, credits, reason, expiresDays, token = '') =>
  usageJson(`/admin/owners/${encodeURIComponent(ownerId)}/topups`, token, {
    method: 'POST', body: JSON.stringify({ credits, reason, expires_days: expiresDays }),
  });
