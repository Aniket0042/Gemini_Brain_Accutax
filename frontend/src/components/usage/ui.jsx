import React from 'react';

// Shared pieces of the AI usage screens: number and date formats, the status words, pills, bars, stat cards, avatars,
// small icons and a CSV download. 1 credit = $0.001; a typical question is about TYPICAL credits.

export const TYPICAL = 20;

export const num = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: 0 }));
export const usd = (v, d = 2) => `$${Number(v || 0).toFixed(d)}`;
export const questions = (credits, typical = TYPICAL) => Math.max(0, Math.floor(Number(credits || 0) / typical));

const asDate = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d : null;
};
export const shortDate = (iso) => asDate(iso)?.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) || '';
export const longDate = (iso) => asDate(iso)?.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) || '';

// "Today, 5:04 PM", "Yesterday, 9:10 AM" or "Oct 25, 11:30 PM".
export const whenLabel = (iso) => {
  const d = asDate(iso);
  if (!d) return '';
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday, ${time}`;
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}, ${time}`;
};

// "9 h 04 m" until an ISO time.
export const timeUntil = (iso) => {
  const d = asDate(iso);
  if (!d) return '';
  const mins = Math.max(0, Math.round((d.getTime() - Date.now()) / 60000));
  const h = Math.floor(mins / 60);
  return h ? `${h} h ${String(mins % 60).padStart(2, '0')} m` : `${mins} m`;
};

// How far through the month we are (0..1), from the month's renewal time.
export const monthElapsed = (resetsIso) => {
  const end = asDate(resetsIso);
  if (!end) return null;
  const start = new Date(end);
  start.setMonth(end.getMonth() - 1);
  const f = (Date.now() - start.getTime()) / (end.getTime() - start.getTime());
  return Math.min(1, Math.max(0.02, f));
};

// The month's percent at renewal if use keeps its pace so far.
export const forecastPercent = (percent, resetsIso) => {
  const f = monthElapsed(resetsIso);
  return percent == null || f == null ? null : Math.round(percent / f);
};

export const STATE = {
  ok: { label: 'Healthy', tone: 'good' },
  warn: { label: 'Running low', tone: 'warn' },
  lite: { label: 'Lite model', tone: 'warn' },
  blocked: { label: 'Used up', tone: 'bad' },
};
export const toneOfState = (s) => STATE[s]?.tone || 'good';

export const RESULT = {
  ok: { label: 'Answered', tone: 'good' }, ready: { label: 'Read', tone: 'good' },
  degraded: { label: 'Timed out', tone: 'bad' }, failed: { label: 'Failed', tone: 'bad' },
  partial: { label: 'Some figures missing', tone: 'warn' }, error: { label: 'Error', tone: 'bad' },
};
export const KIND = { agent_answer: 'Question', document_read: 'File read', privacy_demo: 'Privacy demo',
  health_check: 'Health check', unscoped: 'Other (no owner)' };
export const notOk = (status) => !['ok', 'ready'].includes(status);

export const modelName = (id) => (/haiku/i.test(id) ? 'Haiku' : /sonnet/i.test(id) ? 'Sonnet' : /opus/i.test(id) ? 'Opus'
  : String(id || '').replace(/^.*\./, ''));
export const modelsLabel = (e) => {
  const names = [...new Set((e.models || []).map(modelName))].join(', ') || '—';
  return e.calls > 1 ? `${names} ×${e.calls}` : names;
};

// How big one question was, in words: Light, Standard, or Heavy (about N typical questions).
export const sizeOf = (credits, typical = TYPICAL) => {
  const c = Number(credits || 0);
  if (c <= typical * 0.75) return { label: 'Light', tone: 'good' };
  if (c <= typical * 2.5) return { label: 'Standard', tone: '' };
  return { label: `Heavy · ≈ ${Math.round(c / typical)} questions`, tone: 'warn' };
};

export const Pill = ({ tone = '', dot = false, children, title }) => (
  <span className={`uz-pill${tone ? ` is-${tone}` : ''}`} title={title}>{dot && <i />}{children}</span>
);

export const StatePill = ({ state, suffix = '' }) => (
  <Pill tone={toneOfState(state)} dot>{(STATE[state] || STATE.ok).label}{suffix}</Pill>
);

// A bar: `pct` filled, optional hatched `forecast` behind it and a `marker` line (both in percent).
export const Bar = ({ pct, tone = 'good', forecast = null, marker = null, thin = false, label }) => (
  <div className={`uz-bar${thin ? ' is-thin' : ''}`} role="img" aria-label={label || `${Math.round(pct || 0)}% used`}>
    {forecast != null && forecast > (pct || 0) && <div className="uz-bar-forecast" style={{ width: `${Math.min(100, forecast)}%` }} />}
    <div className={`uz-bar-fill${tone !== 'good' ? ` is-${tone}` : ''}`} style={{ width: `${Math.min(100, Math.max(0, pct || 0))}%` }} />
    {marker != null && <div className="uz-bar-marker" style={{ left: `${marker}%` }} />}
  </div>
);

// A row of blocks, one per question of a small daily limit.
export const Segments = ({ total, used, tone = 'good', label }) => (
  <div className={`uz-segs${tone !== 'good' ? ` is-${tone}` : ''}`} role="img" aria-label={label}>
    {Array.from({ length: total }, (_, i) => <span key={i} className={i < used ? 'is-on' : ''} />)}
  </div>
);

export const Stat = ({ label, value, unit, sub, tone = '', subTone = '' }) => (
  <div className={`uz-stat${tone ? ` is-${tone}` : ''}`}>
    <div className="uz-stat-label">{label}</div>
    <div className="uz-stat-value">{value}{unit && <small> {unit}</small>}</div>
    {sub && <div className={`uz-stat-sub${subTone ? ` is-${subTone}` : ''}`}>{sub}</div>}
  </div>
);

export const initials = (email) => {
  const name = String(email || '?').split('@')[0].replace(/[^a-zA-Z0-9]+/g, ' ').trim();
  const parts = name.split(' ');
  return ((parts[0]?.[0] || '?') + (parts[1]?.[0] || parts[0]?.[1] || '')).toUpperCase();
};
const toneOf = (key) => [...String(key || '')].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) % 5, 0);
export const Avatar = ({ email, large = false }) => (
  <span className={`uz-avatar uz-tone-${toneOf(email)}${large ? ' is-lg' : ''}`} aria-hidden="true">{initials(email)}</span>
);

export const Person = ({ email, sub }) => (
  <div className="uz-person"><Avatar email={email} /><div style={{ minWidth: 0 }}><b>{email}</b>
    {sub && <div className="uz-muted uz-small">{sub}</div>}</div></div>
);

const svg = (paths, size = 16) => ({ className, style }) => (
  <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" className={className} style={style}>{paths}</svg>
);
export const IconClose = svg(<path d="M6 6l12 12M18 6L6 18" />, 18);
export const IconSearch = svg(<><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></>);
export const IconInfo = svg(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>);
export const IconBulb = svg(<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z" />, 18);
export const IconWarn = svg(<><path d="M12 3l9.5 17h-19z" /><path d="M12 10v4M12 17h.01" /></>, 18);
export const IconStop = svg(<><circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16h.01" /></>, 18);
export const IconTrend = svg(<><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></>, 18);
export const IconChevron = svg(<path d="M9 6l6 6-6 6" />);
export const IconBack = svg(<path d="M15 6l-6 6 6 6" />);
export const IconSpark = svg(<><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 17l.7 1.8 1.8.7-1.8.7L19 22l-.7-1.8-1.8-.7 1.8-.7z" /></>, 20);
export const IconTeam = svg(<><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5c1.9.6 3.1 2.4 3.5 5.5" /></>, 20);
export const IconShield = svg(<><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z" /><path d="M9 12l2 2 4-4" /></>, 20);

// Rows as a CSV file the browser downloads. `columns`: [[heading, row => value], ...].
export const downloadCsv = (filename, columns, rows) => {
  const cell = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const text = [columns.map((c) => cell(c[0])).join(','), ...rows.map((r) => columns.map((c) => cell(c[1](r))).join(','))].join('\n');
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

export const Loading = () => <div className="uz-empty">Loading…</div>;
export const ErrorBox = ({ message }) => <div className="uz-error" role="alert">{message}</div>;
