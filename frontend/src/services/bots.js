/**
 * bots.js — API client and browser-local store for Accutax Bots (Phase 1).
 *
 * In phase 1, bots are kept in this browser only (localStorage). Clearing the
 * browser's storage removes them; phase 2 saves them to the user's account.
 */

const KEY = 'accutax_bots';

function authHeaders(token, extra = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
    ...extra,
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function handleResponse(response, defaultMsg) {
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(errorData.detail || errorData.message || defaultMsg);
  }
  return await response.json();
}

// ── API Calls ──────────────────────────────────────────────────────────────

export async function fetchBotTemplates(agent = 'vat', token = '') {
  const res = await fetch(`/api/v1/bots/templates?agent=${encodeURIComponent(agent)}`, {
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to fetch bot templates');
}

export async function fetchBotWatches(agent = 'vat', token = '') {
  const res = await fetch(`/api/v1/bots/watches?agent=${encodeURIComponent(agent)}`, {
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to fetch bot watches');
}

export async function createDraftFromTemplate(templateId, orgIds = [], token = '') {
  const res = await fetch('/api/v1/bots/draft/from-template', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ template_id: templateId, org_ids: orgIds }),
  });
  return handleResponse(res, 'Failed to create draft from template');
}

export async function createDraftFromSentence(text, orgIds = [], token = '') {
  const res = await fetch('/api/v1/bots/draft/from-sentence', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ text, org_ids: orgIds }),
  });
  return handleResponse(res, 'Failed to create draft from sentence');
}

export async function createDraftFromChat(question, agent = 'vat', orgIds = [], token = '') {
  const res = await fetch('/api/v1/bots/draft/from-chat', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ question, agent, org_ids: orgIds }),
  });
  return handleResponse(res, 'Failed to create draft from chat question');
}

export async function checkBotDraft(draft, token = '') {
  const res = await fetch('/api/v1/bots/draft/check', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ draft }),
  });
  return handleResponse(res, 'Failed to validate draft');
}

export async function tryBotDraft(draft, preset = 'last_month', token = '') {
  const res = await fetch('/api/v1/bots/try', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ draft, preset }),
  });
  return handleResponse(res, 'Could not check last month just now. Try again in a moment.');
}

// ── Server-Backed Store (Phase 2) ──────────────────────────────────────────

export async function fetchUserBots(token = '') {
  const res = await fetch('/api/v1/bots', {
    headers: authHeaders(token),
  });
  const data = await handleResponse(res, 'Failed to list bots');
  return data.bots || [];
}

export async function createBotOnServer(draft, status = 'active', token = '') {
  const res = await fetch('/api/v1/bots', {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ draft, status }),
  });
  return handleResponse(res, 'Failed to save bot');
}

export async function updateBotOnServer(id, patch, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: authHeaders(token),
    body: JSON.stringify(patch),
  });
  return handleResponse(res, 'Failed to update bot');
}

export async function deleteBotOnServer(id, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to delete bot');
}

export async function pauseBotOnServer(id, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}/pause`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to pause bot');
}

export async function resumeBotOnServer(id, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}/resume`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to resume bot');
}

// ── Bots pages: home numbers, one bot, its runs and findings, run now ─────

export async function fetchBotsSummary(token = '') {
  const res = await fetch('/api/v1/bots/summary', { headers: authHeaders(token) });
  return handleResponse(res, 'Failed to load the bots summary');
}

export async function fetchBot(id, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}`, { headers: authHeaders(token) });
  return handleResponse(res, 'Failed to load the bot');
}

export async function fetchBotRuns(id, token = '', limit = 20) {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}/runs?limit=${limit}`, { headers: authHeaders(token) });
  return handleResponse(res, 'Failed to load the run history');
}

export async function fetchBotFindings(id, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}/findings`, { headers: authHeaders(token) });
  return handleResponse(res, 'Failed to load the findings');
}

export async function runBotNow(id, token = '', { first = false } = {}) {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(id)}/run${first ? '?first=true' : ''}`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'The run did not start');
}

export async function migrateLocalBotsToServer(token = '') {
  if (!token) return [];
  const local = listBots();
  if (!local.length) return [];
  const migrated = [];
  for (const bot of local) {
    if (bot.draft) {
      try {
        const saved = await createBotOnServer(bot.draft, bot.status || 'active', token);
        migrated.push(saved);
      } catch (err) {
        console.warn('Could not migrate local bot:', bot.id, err);
      }
    }
  }
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  return migrated;
}

export async function fetchBotInbox(token = '') {
  const res = await fetch('/api/v1/bots/inbox', {
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to fetch bot inbox');
}

export async function markBotInboxSeen(token = '') {
  const res = await fetch('/api/v1/bots/inbox/mark-read', {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to mark inbox as read');
}

export async function dismissBotFinding(botId, key, note = '', supplier = '', token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/findings/${encodeURIComponent(key)}/dismiss`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ note: note || undefined, supplier: supplier || undefined }),
  });
  return handleResponse(res, 'Failed to dismiss finding');
}

export async function undismissBotFinding(botId, key, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/findings/${encodeURIComponent(key)}/undismiss`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to restore finding');
}

export async function fetchBotMemory(botId, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/memory`, {
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to fetch bot memory');
}

export async function addBotMemory(botId, text, source = 'note', token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/memory`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ text, source }),
  });
  return handleResponse(res, 'Failed to add bot memory');
}

export async function deleteBotMemory(botId, memoryId, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/memory/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to delete bot memory');
}

export async function fetchBotApprovals(token = '') {
  const res = await fetch('/api/v1/bots/approvals', {
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to fetch approvals');
}

export async function approveBotAction(botId, approvalId, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to approve action');
}

export async function rejectBotAction(botId, approvalId, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/approvals/${encodeURIComponent(approvalId)}/reject`, {
    method: 'POST',
    headers: authHeaders(token),
  });
  return handleResponse(res, 'Failed to reject action');
}

export async function editBotAction(botId, approvalId, edits = {}, token = '') {
  const res = await fetch(`/api/v1/bots/${encodeURIComponent(botId)}/approvals/${encodeURIComponent(approvalId)}/edit`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(edits),
  });
  return handleResponse(res, 'Failed to update and approve action');
}


// ── Browser Local Storage Store (Phase 1 fallback) ─────────────────────────

function readAllBots() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}') || {};
  } catch {
    return {};
  }
}

function writeAllBots(all) {
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* storage blocked or full */
  }
}

export function listBots() {
  const all = readAllBots();
  return Object.values(all).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
}

export function saveBot(draft) {
  const all = readAllBots();
  const id = 'bot-' + Date.now();
  const bot = {
    id,
    draft,
    status: 'active',
    createdAt: Date.now(),
    lastRun: null,
  };
  all[id] = bot;
  writeAllBots(all);
  return bot;
}

export function updateBot(id, patch) {
  const all = readAllBots();
  if (!all[id]) return null;
  all[id] = { ...all[id], ...patch, updatedAt: Date.now() };
  writeAllBots(all);
  return all[id];
}

export function deleteBot(id) {
  const all = readAllBots();
  delete all[id];
  writeAllBots(all);
}

