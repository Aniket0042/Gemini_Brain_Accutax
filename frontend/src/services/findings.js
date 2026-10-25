/**
 * VAT review findings the user marked as not a problem, kept in this browser. Each is the `key` of a
 * vat_review card item (the same problem on the same bills has the same key in every review). The keys go
 * with each question as `dismissed_findings`, so the next review leaves them out. No database: clearing the
 * browser's storage brings them back.
 */
const KEY = 'accutax_dismissed_findings';
const MAX_SENT = 1000;

function readAll() {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}') || {};
  } catch {
    return {};
  }
}

function writeAll(all) {
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch { /* storage full or blocked: the finding stays dismissed on this page only */ }
}

export function isDismissed(key) {
  return Boolean(key && readAll()[key]);
}

export function dismissFinding(key, title) {
  if (!key) return;
  const all = readAll();
  all[key] = { at: Date.now(), title: title || '' };
  writeAll(all);
}

export function restoreFinding(key) {
  const all = readAll();
  delete all[key];
  writeAll(all);
}

/** The keys to send with a question, newest first, at most what the server accepts. */
export function dismissedKeys() {
  return Object.entries(readAll())
    .sort((a, b) => (b[1]?.at || 0) - (a[1]?.at || 0))
    .slice(0, MAX_SENT)
    .map(([key]) => key);
}
