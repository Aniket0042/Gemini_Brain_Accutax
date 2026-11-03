/**
 * botText.js — Words and dates the bot pages share: a bot's schedule in one line, its status, run times.
 * Times come from the server in the Gulf time zone ("2026-11-02T08:00:00+04:00"); they are shown as sent, not moved
 * to the browser's zone, so "08:00" means 08:00 in the books' time.
 */

const DAY_NAMES = { mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' };

/**
 * The VAT agent as GET /api/v1/bots/agents describes it (bots/agents_vat.py). The pages use it until the agents
 * load, so they never render empty; keep it in step with the server.
 */
export const DEFAULT_AGENT = {
  key: 'vat',
  name: 'VAT',
  watches: [
    { id: 'duplicate_bill', side: 'purchases', kind: 'document', default_on: true, label: 'A bill looks recorded twice',
      desc: 'Same number, or same total from the same supplier a few days apart.', bar: 'Recorded twice' },
    { id: 'vat_rate_wrong', side: 'purchases', kind: 'document', default_on: true, label: 'VAT on a bill is not 0% or 5%',
      desc: 'Wrong rate, negative VAT, or VAT with no amount.', bar: 'VAT not 0% or 5%' },
    { id: 'bill_missing_details', side: 'purchases', kind: 'document', default_on: false,
      label: 'A bill is missing its supplier or number, or is dated in the future',
      desc: "These bills can't support a VAT claim.", bar: 'Missing details' },
    { id: 'unusual_bill', side: 'purchases', kind: 'document', default_on: false, label: "A bill far above the supplier's usual amount",
      desc: "Compared with that supplier's past bills.", bar: 'Unusually large' },
    { id: 'sales_vat_wrong', side: 'sales', kind: 'document', default_on: true, label: 'A sales invoice with wrong or no output VAT',
      desc: "Zero VAT where 5% is expected, or a rate that isn't 0% or 5%.", bar: 'Sales VAT wrong' },
  ],
  templates: [
    { id: 'vat_weekly_digest', tag: 'Most popular', runs: 'Mondays, 08:00' },
    { id: 'vat_return_reminder', tag: 'Never miss a deadline', runs: 'Before each return, 09:00' },
    { id: 'vat_full_watch', tag: 'Everything on', runs: 'Mondays + before returns' },
  ],
  schedule_kinds: ['weekly', 'monthly', 'before_deadline', 'after_period_end'],
  credits_per_run: 90,
  copy: {
    label: 'VAT agent',
    default_name: 'VAT Watch',
    create_hint: 'Every Monday at 9am check my bills for duplicates and wrong VAT, and alert me',
    examples: [
      'Every Monday at 9am, check my bills for duplicates',
      'Remind me 7 days before each VAT return',
      'Check sales invoices for wrong VAT on the 1st of each month',
    ],
    tip: 'under any VAT answer in chat, press Make this a bot to repeat it on a schedule.',
    stake_label: 'At stake this quarter',
    stake_note: 'VAT that could be wrong on your return if these stay unfixed: copies claimed twice, missing output VAT and wrong rates.',
    problem_word: 'VAT problem',
    readonly_rule: 'Change bills, invoices or file a VAT return',
    notes_placeholder: "Rent from Al Noor Properties is billed in two parts; don't flag those as duplicates",
    watch_short: {
      duplicate_bill: 'duplicates',
      vat_rate_wrong: 'wrong VAT rates',
      bill_missing_details: 'missing details',
      unusual_bill: 'unusual amounts',
      sales_vat_wrong: 'sales VAT',
    },
    rule_titles: {
      same_number: 'Bill may be recorded twice',
      same_total_near_date: 'Bill may be recorded twice',
      similar_vendor_same_day: 'Bill may be recorded twice',
      vat_rate_not_standard: 'VAT is not 0% or 5%',
      vat_wrong_sign: 'VAT and amount have opposite signs',
      vat_without_amount: 'VAT with no amount',
      missing_supplier: 'Bill with no supplier',
      missing_number: 'Bill with no number',
      future_date: 'Bill dated in the future',
      large_for_supplier: "Far above the supplier's usual bill",
      large_one_off_supplier: "A supplier's only bill, and a large one",
      vat_zero: 'No VAT on an amount',
      round_amount: 'Round amount',
      supplier_trn_invalid: "Supplier's TRN missing or wrong",
    },
    sales_rule_titles: {
      vat_zero: 'Sales invoice with no VAT',
      vat_rate_not_standard: 'Sales invoice VAT is not 0% or 5%',
      vat_wrong_sign: 'Sales invoice VAT and amount have opposite signs',
      vat_without_amount: 'Sales invoice with VAT but no amount',
    },
    dismiss_labels: { sales: { vat_zero: "It's zero-rated" } },
    side_titles: { purchases: 'Bills you received', sales: 'Invoices you sent' },
  },
  columns: [
    { key: 'number', label: 'Bill', sales_label: 'Invoice' },
    { key: 'date', label: 'Date' },
    { key: 'party', label: 'Supplier', sales_label: 'Customer' },
    { key: 'amount', label: 'Amount', num: true },
    { key: 'vat', label: 'VAT', num: true },
  ],
};

let AGENTS = { vat: DEFAULT_AGENT };

/** Keep the agents from GET /bots/agents; the VAT fallback stays until they load. */
export function setBotAgents(list = []) {
  if (!Array.isArray(list) || !list.length) return;
  AGENTS = Object.fromEntries(list.map((a) => [a.key, a]));
}

/** One agent's checks, templates and words; VAT for a bot saved before agents existed. */
export function agentMeta(key = 'vat') {
  return AGENTS[key || 'vat'] || AGENTS.vat || DEFAULT_AGENT;
}

export function allBotAgents() {
  return Object.values(AGENTS);
}

/** A finding's title: the agent's words for its rule (the sales side has its own), else the server's text. */
export function ruleTitle(f, agentKey) {
  const copy = agentMeta(agentKey || f?.agent).copy || {};
  if (f?.side === 'sales' && copy.sales_rule_titles?.[f.rule]) return copy.sales_rule_titles[f.rule];
  return copy.rule_titles?.[f?.rule] || f?.rule_text || f?.rule;
}

function ordinal(n) {
  const s = ['th', 'st', 'nd', 'rd'];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function listWords(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** "Every Monday at 08:00", "14, 7 and 1 days before each VAT return, at 09:00". */
export function scheduleLine(schedule) {
  if (!schedule) return '';
  const at = schedule.time || '08:00';
  switch (schedule.kind) {
    case 'weekly':
      return `Every ${DAY_NAMES[schedule.day] || 'week'} at ${at}`;
    case 'monthly':
      return `${ordinal(schedule.day_of_month || 1)} of each month at ${at}`;
    case 'daily':
      return `Every day at ${at}`;
    case 'before_deadline':
      return `${listWords((schedule.days_before || []).map(String))} days before each VAT return, at ${at}`;
    case 'after_period_end':
      return `${schedule.days_after || 0} days after each VAT period ends, at ${at}`;
    default:
      return '';
  }
}

/** The bot card's subtitle: its schedules, then what it checks. */
export function botSummary(draft = {}) {
  const agent = agentMeta(draft.agent);
  const when = (draft.schedules || []).map(scheduleLine).filter(Boolean);
  const watches = draft.watches || [];
  const what = watches.length >= agent.watches.length
    ? 'every check on'
    : listWords(watches.map((w) => agent.copy?.watch_short?.[w]).filter(Boolean));
  const orgs = (draft.org_ids || []).length;
  return [when.join(' + ') || 'No schedule yet', what, orgs > 1 ? `${orgs} organizations` : '']
    .filter(Boolean).join(' · ');
}

/** "AB" for "Audit Bot", "VW" for "VAT Watch". */
export function initials(name = '') {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] || 'B') + (words[1]?.[0] || '')).toUpperCase();
}

/** {label, tone} for the status badge. Tones: active, paused, attention. */
export function botStatus(bot) {
  if (bot?.status === 'error') return { label: 'Needs attention', tone: 'attention' };
  if (bot?.status === 'paused') return { label: 'Paused', tone: 'paused' };
  return { label: 'Active', tone: 'active' };
}

function parts(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso || '');
  if (!m) return null;
  return { date: new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])), time: m[4] ? `${m[4]}:${m[5]}` : '' };
}

/** "Mon 2 Nov, 08:00" (or "Mon 2 Nov" with no time). */
export function runTime(iso) {
  const p = parts(iso);
  if (!p) return '';
  const day = p.date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' });
  return p.time ? `${day}, ${p.time}` : day;
}

/** "Mon 26 Oct" */
export function shortDay(iso) {
  const p = parts(iso);
  return p ? p.date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }) : '';
}

/** "28 Jan 2027" */
export function longDate(iso) {
  const p = parts(iso);
  return p ? p.date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : '';
}

/** "Q4" for a quarter that ends in Dec, "Oct" for a monthly period. */
export function periodLabel(period) {
  const s = parts(period?.start);
  const e = parts(period?.end);
  if (!s || !e) return '';
  const months = (e.date.getUTCFullYear() - s.date.getUTCFullYear()) * 12 + e.date.getUTCMonth() - s.date.getUTCMonth() + 1;
  if (months === 1) return s.date.toLocaleDateString('en-GB', { month: 'short', timeZone: 'UTC' });
  if (months === 3 && s.date.getUTCMonth() % 3 === 0) return `Q${s.date.getUTCMonth() / 3 + 1}`;
  return `${shortDay(period.start)} to ${shortDay(period.end)}`;
}

export function credits(n) {
  return Number(n || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/** What a run says in one line: its headline, else what went wrong. */
export function runText(run) {
  if (!run) return '';
  if (run.status === 'failed') {
    // The error is for the log; the page says what it means for the user.
    if (/timeout|timed out/i.test(run.error || '')) {
      return 'Your books took too long to answer, so this run stopped early and nothing was marked fixed.';
    }
    if (/no access/i.test(run.error || '')) return run.error;
    return "This run stopped before it finished, so nothing was marked fixed. It tries again on schedule.";
  }
  return run.summary?.headline || '';
}

export const TRIGGER_LABEL ={ schedule: 'Scheduled', first_run: 'First run', manual: 'Run now', event: 'New document' };

/** "Weekly · Monday 09:00", for the chips under "Here's what we understood". */
export function scheduleChip(schedule) {
  if (!schedule) return '';
  const at = schedule.time || '08:00';
  if (schedule.kind === 'weekly') return `Weekly · ${DAY_NAMES[schedule.day] || ''} ${at}`.replace('  ', ' ');
  if (schedule.kind === 'monthly') return `Monthly · ${ordinal(schedule.day_of_month || 1)}, ${at}`;
  if (schedule.kind === 'daily') return `Daily · ${at}`;
  if (schedule.kind === 'before_deadline') return `${listWords((schedule.days_before || []).map(String))} days before each return`;
  if (schedule.kind === 'after_period_end') return `${schedule.days_after || 0} days after each period`;
  return '';
}

export const DELIVER_LABEL = { inbox: 'Inbox', bell: 'Bell alerts', email: 'Email digest' };

/** "Checks every Monday at 08:00 and 14, 7 and 1 days before each VAT return, looks for 5 kinds of problems, and
 *  tells you in your inbox and bell." */
export function inShort(draft = {}) {
  const when = (draft.schedules || []).map((s) => scheduleLine(s).replace(/^Every /, 'every ')).filter(Boolean);
  const n = (draft.watches || []).length;
  const where = (draft.deliver || ['inbox']).filter((d) => d !== 'email').map((d) => (d === 'inbox' ? 'inbox' : 'bell'));
  return `Checks ${when.length ? listWords(when) : 'on no schedule yet'}, looks for ${n} kind${n === 1 ? '' : 's'} of problem${n === 1 ? '' : 's'}, and tells you in your ${listWords(where)}.`;
}
