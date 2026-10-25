/**
 * Each specialist's last run, per organization scope, kept in this browser so the agent cards can show
 * what the agent last found ("Checked 329 bills · 2h ago · 293 findings"). No database: it is this
 * browser's own record of runs the user started, cleared with the browser's storage.
 */
const KEY = 'accutax_agent_runs';

function readAll() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}') || {};
  } catch {
    return {};
  }
}

/** { agentKey: { at, status: {headline, badge, tone}, historyId, question } } for one scope. */
export function readAgentRuns(scopeKey) {
  return readAll()[scopeKey] || {};
}

/** Record a finished run; returns the scope's runs after it. */
export function recordAgentRun(scopeKey, agentKey, run) {
  const all = readAll();
  const scope = { ...(all[scopeKey] || {}), [agentKey]: run };
  all[scopeKey] = scope;
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch { /* storage full or blocked: the cards just show no last run */ }
  return scope;
}

/** "just now", "5 min ago", "2h ago", "3 days ago". */
export function timeAgo(at, now = Date.now()) {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  const d = Math.round(s / 86400);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}
