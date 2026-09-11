/**
 * api.js — API client service for communicating with the Gemini Brain FastAPI backend.
 * Handles JWT authentication, query submission, live SSE streaming, and model health diagnostics.
 */

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

export const fetchQueryResponse = async (payload, token = '') => {
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

/**
 * fetchAllModelsResponse — dev comparison option. Runs the same query
 * against every available model (POST /api/v1/query/all) and returns
 * { responses: [...] }, one QueryResponse-shaped entry per model. No
 * streaming variant — waits for every model before returning.
 */
export const fetchAllModelsResponse = async (payload, token = '') => {
  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch('/api/v1/query/all', {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  const responseData = await response.json().catch(() => null);

  if (!response.ok) {
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
        const errorData = await response.json().catch(() => null);
        if (errorData && errorData.notice) {
          onChunk({ final_result: errorData });
          if (onComplete) onComplete();
          return;
        }
        const errorText = await response.text().catch(() => response.statusText);
        throw new Error(`Streaming failed (${response.status}): ${errorText}`);
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

export const createChatSession = async (organizationId, token = '', sessionId) => {
  const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  const body = { organization_id: organizationId ? Number(organizationId) : undefined };
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

