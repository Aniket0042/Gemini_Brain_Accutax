/**
 * botText.js — Words and dates the bot pages share: a bot's schedule in one line, its status, run times.
 * Times come from the server in the Gulf time zone ("2026-11-02T08:00:00+04:00"); they are shown as sent, not moved
 * to the browser's zone, so "08:00" means 08:00 in the books' time.
 */

const DAY_NAMES = { mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' };

const WATCH_SHORT = {
  duplicate_bill: 'duplicates',
  vat_rate_wrong: 'wrong VAT rates',
  bill_missing_details: 'missing details',
  unusual_bill: 'unusual amounts',
  sales_vat_wrong: 'sales VAT',
};

export const ALL_WATCHES = Object.keys(WATCH_SHORT);

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
  const when = (draft.schedules || []).map(scheduleLine).filter(Boolean);
  const watches = draft.watches || [];
  const what = watches.length >= ALL_WATCHES.length
    ? 'every check on'
    : listWords(watches.map((w) => WATCH_SHORT[w]).filter(Boolean));
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

/** review_vat rule names in words, for findings lists. */
export const RULE_LABEL = {
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
};

export const TRIGGER_LABEL ={ schedule: 'Scheduled', first_run: 'First run', manual: 'Run now', event: 'New document' };

/** The checks a VAT bot can run, grouped as the builder shows them. Ids match bots/watches.py. */
export const WATCH_INFO = [
  { id: 'duplicate_bill', side: 'purchases', label: 'A bill looks recorded twice',
    desc: 'Same number, or same total from the same supplier a few days apart.' },
  { id: 'vat_rate_wrong', side: 'purchases', label: 'VAT on a bill is not 0% or 5%',
    desc: 'Wrong rate, negative VAT, or VAT with no amount.' },
  { id: 'bill_missing_details', side: 'purchases', label: 'A bill is missing its supplier or number, or is dated in the future',
    desc: "These bills can't support a VAT claim." },
  { id: 'unusual_bill', side: 'purchases', label: "A bill far above the supplier's usual amount",
    desc: "Compared with that supplier's past bills." },
  { id: 'sales_vat_wrong', side: 'sales', label: 'A sales invoice with wrong or no output VAT',
    desc: "Zero VAT where 5% is expected, or a rate that isn't 0% or 5%." },
];

/** Short names for the preview's per-check bars. */
export const WATCH_BAR = {
  duplicate_bill: 'Recorded twice', vat_rate_wrong: 'VAT not 0% or 5%', bill_missing_details: 'Missing details',
  unusual_bill: 'Unusually large', sales_vat_wrong: 'Sales VAT wrong',
};

/** The tag and the "Runs" line on each template card. */
export const TEMPLATE_META = {
  vat_weekly_digest: { tag: 'Most popular', runs: 'Mondays, 08:00' },
  vat_return_reminder: { tag: 'Never miss a deadline', runs: 'Before each return, 09:00' },
  vat_full_watch: { tag: 'Everything on', runs: 'Mondays + before returns' },
};

/** "Weekly · Monday 09:00", for the chips under "Here's what we understood". */
export function scheduleChip(schedule) {
  if (!schedule) return '';
  const at = schedule.time || '08:00';
  if (schedule.kind === 'weekly') return `Weekly · ${DAY_NAMES[schedule.day] || ''} ${at}`.replace('  ', ' ');
  if (schedule.kind === 'monthly') return `Monthly · ${ordinal(schedule.day_of_month || 1)}, ${at}`;
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
