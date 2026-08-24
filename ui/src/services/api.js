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

