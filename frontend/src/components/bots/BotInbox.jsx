import React, { useMemo, useState } from 'react';
import {
  AlertCircle, ArrowLeft, Bot, Building2, Check, CheckCheck, ChevronDown, ChevronUp, Copy, ExternalLink, FileText,
  Clock, MessageSquare, Percent, RotateCcw, UserPlus,
} from 'lucide-react';
import { agentMeta, ruleTitle, runTime, shortDay, TRIGGER_LABEL } from './botText';
import './bots.css';
import './bots-pages.css';
import './bots-builder.css';
import './bots-inbox.css';

const FILTERS = [['all', 'All'], ['new', 'New'], ['seen', 'Seen'], ['fixed', 'Fixed'], ['dismissed', 'Not a problem']];
const DUPLICATE_RULES = new Set(['same_number', 'same_total_near_date', 'similar_vendor_same_day']);
const LEVEL_WORDS = { high: 'Sure', medium: 'Likely', low: 'Possible' };

function money(n, currency = 'AED') {
  if (n == null || Number.isNaN(Number(n))) return '';
  return `${currency} ${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function plain(n) {
  if (n == null || Number.isNaN(Number(n))) return '';
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function iconFor(f) {
  if (DUPLICATE_RULES.has(f.rule)) return Copy;
  if (f.rule === 'bill_overdue' || f.rule === 'bill_due_soon') return Clock;
  if (f.rule === 'new_supplier_large') return UserPlus;
  if (f.side === 'sales') return FileText;
  if (/vat/.test(f.rule)) return Percent;
  return AlertCircle;
}

/** "today", "yesterday" or "Mon 26 Oct", from an ISO time. */
function when(iso) {
  if (!iso) return '';
  const d = iso.slice(0, 10);
  const today = new Date();
  const ymd = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
  if (d === ymd(today)) return 'today';
  const y = new Date(today); y.setDate(today.getDate() - 1);
  if (d === ymd(y)) return 'yesterday';
  return shortDay(iso);
}

// The agent's words name the finding; on the sales side the titles name the invoice.
const title = (f) => ruleTitle(f);

/** What "Not a problem" is called for this finding, in the agent's words (VAT: a sales invoice with no VAT is
 *  usually zero-rated). */
function dismissLabel(f) {
  return agentMeta(f.agent).copy?.dismiss_labels?.[f.side || 'purchases']?.[f.rule] || 'Not a problem';
}

/** A fixed finding in the side panel. VAT: "Bill may be recorded twice B-1 from X no longer found"; agents with
 *  their own words (AP): "Bill B-1 from X is no longer open". */
function fixedLine(f, first) {
  const text = agentMeta(f.agent).copy?.fixed_text;
  const num = first.number ? ` ${first.number}` : '';
  const from = first.party ? ` from ${first.party}` : '';
  return text ? `Bill${num}${from} ${text}` : `${title(f)}${num}${from} no longer found`;
}

function openLabel(f) {
  const n = (f.detail?.bills || []).filter((b) => b.url).length;
  const noun = f.side === 'sales' ? 'invoice' : 'bill';
  if (n <= 1) return `Open ${noun}`;
  return n === 2 ? `Open both ${noun}s` : `Open ${n} ${noun}s`;
}

/** One bill's cell: its number links to the bill; dates and amounts are formatted; the rest as stored. */
function cell(b, c) {
  if (c.key === 'number') return b.url ? <a href={b.url} target="_blank" rel="noreferrer">{b.number || b.id}</a> : (b.number || b.id);
  if (c.key === 'date' || c.key === 'due_date') return b[c.key] ? shortDay(b[c.key]) : '';
  if (c.num) return plain(b[c.key]);
  return b[c.key] || '';
}

function BillsTable({ f }) {
  const bills = f.detail?.bills || [];
  if (bills.length < 2) return null;
  const columns = agentMeta(f.agent).columns || [];
  return (
    <table className="bi-table">
      <thead><tr>{columns.map((c) => (
        <th key={c.key} className={c.num ? 'is-num' : undefined}>{(f.side === 'sales' && c.sales_label) || c.label}</th>
      ))}</tr></thead>
      <tbody>
        {bills.map((b, i) => (
          <tr key={`${b.id}-${i}`}>
            {columns.map((c) => <td key={c.key} className={c.num ? 'is-num' : undefined}>{cell(b, c)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function WhyBox({ f, onEditBot }) {
  const level = f.detail?.level;
  return (
    <div className="bi-why">
      <dl>
        <dt>Bot</dt><dd>{f.bot_name}</dd>
        <dt>Check</dt><dd>{f.watch_label || '—'}</dd>
        <dt>Rule</dt><dd>{f.rule_text ? f.rule_text[0].toUpperCase() + f.rule_text.slice(1) : f.rule}</dd>
        {level && <><dt>Confidence</dt><dd>{LEVEL_WORDS[level] || level}</dd></>}
      </dl>
      <div className="bi-why-foot">
        <button type="button" className="bd-link" onClick={() => onEditBot(f.bot_id)}>Adjust this check in {f.bot_name} →</button>
        <span className="bd-muted">"{dismissLabel(f)}" teaches the bot to skip this one next time.</span>
      </div>
    </div>
  );
}

/** A finding with everything: what is wrong, its bills, and what to do about it. */
function FindingCard({ f, onDismiss, onRestore, onAsk, onEditBot }) {
  const [why, setWhy] = useState(false);
  const Icon = iconFor(f);
  const bills = f.detail?.bills || [];
  const urls = bills.map((b) => b.url).filter(Boolean);
  const amount = f.at_stake || f.amount;
  return (
    <article className={`bi-card is-${f.state}`}>
      <div className="bi-card-head">
        <span className={`bi-icon${f.side === 'sales' ? ' is-sales' : ''}`}><Icon size={16} /></span>
        <div className="bi-card-title">
          <div className="bi-title-row">
            <h3>{title(f)}</h3>
            {f.state === 'new' && <span className="bd-tag is-warning">New</span>}
            {f.state === 'fixed' && <span className="bd-tag is-ok">Fixed</span>}
            {f.state === 'dismissed' && <span className="bd-tag is-muted">Not a problem</span>}
            <span className="bd-tag is-muted">{f.side === 'sales' ? 'Sales' : 'Bills'}</span>
          </div>
          {f.detail?.why && <p className="bi-why-text">{f.detail.why}</p>}
        </div>
        <div className="bi-amount">
          {amount ? <strong>{money(amount, f.currency)}</strong> : null}
          <span className="bd-muted">{f.bot_name} · {when(f.found_at)}</span>
        </div>
      </div>
      <BillsTable f={f} />
      <div className="bi-actions">
        {urls.length > 0 && (
          <button type="button" className="bh-btn is-primary" onClick={() => urls.forEach((u) => window.open(u, '_blank', 'noopener'))}>
            <ExternalLink size={13} /> {openLabel(f)}
          </button>
        )}
        {f.state === 'dismissed'
          ? <button type="button" className="bh-btn" onClick={() => onRestore(f)}><RotateCcw size={13} /> Bring it back</button>
          : f.state !== 'fixed' && <button type="button" className="bh-btn" onClick={() => onDismiss(f)}>{dismissLabel(f)}</button>}
        <button type="button" className="bh-btn" onClick={() => onAsk(f)}><MessageSquare size={13} /> Ask AccuTax</button>
        <button type="button" className="bd-link bi-why-toggle" onClick={() => setWhy((v) => !v)} aria-expanded={why}>
          Why did I get this? {why ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>
      {why && <WhyBox f={f} onEditBot={onEditBot} />}
    </article>
  );
}

/** A finding in one line; it opens into the full card. */
function FindingRow({ f, ...actions }) {
  const [open, setOpen] = useState(false);
  if (open) return <FindingCard f={f} {...actions} />;
  const first = (f.detail?.bills || [])[0] || {};
  const amount = f.at_stake || f.amount;
  return (
    <button type="button" className={`bi-row is-${f.state}`} onClick={() => setOpen(true)}>
      <span className="bi-row-text">
        {[title(f), first.party || first.number, amount ? money(amount, f.currency) : ''].filter(Boolean).join(' · ')}
      </span>
      <span className="bd-muted">{f.bot_name} · {when(f.found_at)}</span>
    </button>
  );
}

function RunBanner({ run, onOpenBot }) {
  if (!run) return null;
  const s = run.summary || {};
  const kind = run.trigger === 'first_run' ? 'its first run' : run.trigger === 'event' ? 'a check on new documents'
    : `a ${(TRIGGER_LABEL[run.trigger] || 'run').toLowerCase()} run`;
  const parts = [
    runTime(run.started_at),
    (s.bills_checked || s.invoices_checked) ? `${(s.bills_checked || 0) + (s.invoices_checked || 0)} documents checked` : '',
    s.groups_found ? `${s.groups_found} duplicate group${s.groups_found === 1 ? '' : 's'}` : '',
    s.findings_found ? `${s.findings_found} ${agentMeta(run.agent).copy?.problem_word || 'problem'}${s.findings_found === 1 ? '' : 's'}` : '',
  ].filter(Boolean);
  return (
    <div className={`bi-banner${run.status === 'failed' ? ' is-failed' : ''}`}>
      <span className="bi-icon"><Bot size={15} /></span>
      <div>
        <strong>{run.bot_name} {run.status === 'failed' ? `could not finish ${kind}` : `finished ${kind}`}</strong>
        <span className="bd-muted">{parts.join(' · ')}</span>
      </div>
      <button type="button" className="bd-link" onClick={() => onOpenBot(run.bot_id)}>View run →</button>
    </div>
  );
}

/**
 * The bots Inbox: what every bot found, newest first, with filters by state and by bot, the VAT at stake this
 * quarter, and what was fixed this week. `inbox` is GET /api/v1/bots/inbox.
 */
export default function BotInbox({ inbox, orgLabel = '', onBack, onMarkRead, onDismiss, onRestore, onAsk, onEditBot, onOpenBot }) {
  const [filter, setFilter] = useState('all');
  const [botFilter, setBotFilter] = useState(null);
  const counts = inbox?.counts || {};
  const findings = inbox?.findings || [];
  // One stake card per agent the inbox has findings from: each agent's stake means something different.
  const stakeAgents = [...new Set(findings.map((f) => f.agent || 'vat'))];
  if (!stakeAgents.length) stakeAgents.push('vat');

  const shown = useMemo(() => findings.filter((f) => {
    if (botFilter && f.bot_id !== botFilter) return false;
    if (filter === 'all') return f.state === 'new' || f.state === 'seen';
    return f.state === filter;
  }), [findings, filter, botFilter]);
  const news = shown.filter((f) => f.state === 'new');
  const rest = shown.filter((f) => f.state !== 'new');
  const actions = { onDismiss, onRestore, onAsk, onEditBot };
  const latest = (inbox?.runs || [])[0];
  const restTitle = { all: 'Seen', seen: 'Seen', fixed: 'Fixed', dismissed: 'Not a problem' }[filter] || '';

  return (
    <div className="bots-page bi-page">
      <div className="bc-top">
        <button type="button" className="bh-btn" onClick={onBack}><ArrowLeft size={14} /> Bots</button>
        {orgLabel && <span className="bh-pill bc-org"><Building2 size={12} /> {orgLabel}</span>}
      </div>

      <div className="bi-columns">
        <div className="bi-main">
          <div className="bh-head">
            <div>
              <h1 className="bots-title">Inbox</h1>
              <p className="bots-lede">Problems your bots found, newest first. Fix them in your books and they clear on the next run.</p>
            </div>
            <button type="button" className="bh-btn" onClick={onMarkRead} disabled={!counts.new}>
              <CheckCheck size={14} /> Mark all read
            </button>
          </div>

          <div className="bi-filters" role="tablist">
            {FILTERS.map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={filter === k}
                className={`bi-filter${filter === k ? ' on' : ''}`} onClick={() => setFilter(k)}>
                {l} <span className={`bi-count${k === 'new' && counts.new ? ' is-new' : ''}`}>{counts[k] || 0}</span>
              </button>
            ))}
          </div>

          <RunBanner run={latest} onOpenBot={onOpenBot} />

          {shown.length === 0 && (
            <div className="bi-empty">
              <Check size={18} />
              <p>{filter === 'all' ? 'Nothing open. Your bots will add what they find here.' : 'Nothing here.'}</p>
            </div>
          )}

          {news.length > 0 && (
            <section className="bi-group">
              <h2 className="bi-group-title">New · {news.length}</h2>
              {news.map((f) => <FindingCard key={`${f.bot_id}-${f.key}`} f={f} {...actions} />)}
            </section>
          )}
          {rest.length > 0 && (
            <section className="bi-group">
              <h2 className="bi-group-title">{restTitle} · {rest.length}</h2>
              <div className="bi-rows">
                {rest.map((f) => <FindingRow key={`${f.bot_id}-${f.key}`} f={f} {...actions} />)}
              </div>
            </section>
          )}
        </div>

        <aside className="bi-side">
          <section className="bd-side-card">
            <span className="bb-eyebrow">Bots</span>
            <ul className="bi-bots">
              <li>
                <button type="button" className={!botFilter ? 'on' : ''} onClick={() => setBotFilter(null)}>
                  <span>All bots</span><span className="bi-count">{counts.all || 0}</span>
                </button>
              </li>
              {(inbox?.bots || []).map((b) => (
                <li key={b.id}>
                  <button type="button" className={botFilter === b.id ? 'on' : ''} onClick={() => setBotFilter(b.id)}>
                    <span className={`bi-dot${b.open ? ' is-open' : ''}`} /><span>{b.name}</span><span className="bi-count">{b.open}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {stakeAgents.map((ag) => {
            const copy = agentMeta(ag).copy || {};
            // Older answers have no per-agent stake: then the one total belongs to VAT.
            const stake = inbox?.at_stake_by_agent ? (inbox.at_stake_by_agent[ag] || []) : (inbox?.at_stake || []);
            return (
              <section key={ag} className="bd-side-card">
                <span className="bb-eyebrow">{copy.stake_label}</span>
                {stake.length === 0
                  ? <p className="bi-stake">AED 0.00</p>
                  : stake.map((s) => <p key={s.currency} className="bi-stake">{money(s.amount, s.currency)}</p>)}
                <p className="bd-muted">{copy.stake_note}</p>
              </section>
            );
          })}

          <section className="bd-side-card">
            <span className="bb-eyebrow">Fixed this week</span>
            {(inbox?.fixed_this_week || []).length === 0 ? <p className="bd-muted">Nothing fixed yet this week.</p> : (
              <ul className="bi-fixed">
                {inbox.fixed_this_week.map((f) => {
                  const first = (f.detail?.bills || [])[0] || {};
                  return (
                    <li key={`${f.bot_id}-${f.key}`}>
                      <Check size={13} />
                      <span>{fixedLine(f, first)} ({shortDay(f.updated_at)})</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
