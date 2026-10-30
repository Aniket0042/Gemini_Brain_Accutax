import React from 'react';
import { AlertTriangle, Bot, Inbox, Settings2, ShieldCheck } from 'lucide-react';
import { credits, longDate, periodLabel, runTime, scheduleLine, shortDay } from './botText';
import './bots-pages.css';
import './bots-intro.css';

function money(n, currency = 'AED') {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return `${currency} ${Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function listWords(items) {
  if (items.length <= 1) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

function clock(ts) {
  const d = new Date(ts || Date.now());
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
}

/** "Q4 so far (1–30 Oct)" for a quarter still running, "Q3 (1 Jul–30 Sep)" for a whole one, else the dates. */
function periodWords(period) {
  if (!period?.start || !period?.end) return '';
  const s = new Date(`${period.start.slice(0, 10)}T00:00:00Z`);
  const e = new Date(`${period.end.slice(0, 10)}T00:00:00Z`);
  const fmt = (d, withMonth = true) => d.toLocaleDateString('en-GB', { day: 'numeric', ...(withMonth ? { month: 'short' } : {}), timeZone: 'UTC' });
  const range = s.getUTCMonth() === e.getUTCMonth() ? `${fmt(s, false)}–${fmt(e)}` : `${fmt(s)}–${fmt(e)}`;
  const quarterStart = s.getUTCDate() === 1 && s.getUTCMonth() % 3 === 0;
  const sameQuarter = Math.floor(s.getUTCMonth() / 3) === Math.floor(e.getUTCMonth() / 3) && s.getUTCFullYear() === e.getUTCFullYear();
  if (quarterStart && sameQuarter) {
    const q = `Q${Math.floor(s.getUTCMonth() / 3) + 1}`;
    const quarterEnd = new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth() + 3, 0));
    return e.getTime() >= quarterEnd.getTime() ? `${q} (${range})` : `${q} so far (${range})`;
  }
  return range;
}

/**
 * The bot's first message in chat, right after it is turned on: what it checked, what it found, the return
 * position, when it runs next, and buttons to its findings, its settings and its page. Every figure comes from the
 * first run's summary (POST /bots/{id}/run?first=true); the words around them are written here.
 * msg: {role: 'bot_intro', bot, run, timestamp}.
 */
export default function BotIntroMessage({ msg, onOpenInbox, onEditBot, onOpenBot }) {
  const { bot = {}, run = {} } = msg;
  const s = run.summary || {};
  const failed = run.status !== 'ok';
  const bills = s.bills_checked || 0;
  const invoices = s.invoices_checked || 0;
  const groups = s.groups_found || 0;
  const problems = s.findings_found || 0;
  const rp = s.return_position;
  const total = groups + problems;
  const schedules = (bot.draft?.schedules || []).map((x) => scheduleLine(x).replace(/^Every /, 'every ')).filter(Boolean);
  const where = (bot.draft?.deliver || ['inbox']).includes('bell') ? 'inbox and bell' : 'inbox';

  return (
    <div className="bim">
      <div className="bim-divider"><span>{bot.name} turned on · {clock(msg.timestamp)}</span></div>
      <article className="bim-card">
        <header className="bim-head">
          <span className="bh-bot-icon"><ShieldCheck size={18} /></span>
          <strong>{bot.name}</strong>
          <span className="bh-pill"><Bot size={11} /> Bot</span>
          <span className="bd-muted">First run · just now</span>
        </header>

        {failed ? (
          <div className="bim-failed">
            <AlertTriangle size={15} />
            <p>
              Hi, I'm {bot.name}. I couldn't finish my first check just now, so I'll try again on my next run.
              {run.error ? ` (${run.error})` : ''}
            </p>
          </div>
        ) : (
          <>
            <p className="bim-text">
              Hi, I'm {bot.name}. I checked {plural(bills, 'bill', 'bills')}
              {invoices ? ` and ${plural(invoices, 'sales invoice', 'sales invoices')}` : ''}
              {s.period?.start ? ` for ${periodWords(s.period)}` : ''}.{' '}
              {total === 0
                ? 'Everything looks right.'
                : `I found ${groups ? plural(groups, 'likely duplicate group', 'likely duplicate groups') : ''}${groups && problems ? ' and ' : ''}${problems ? plural(problems, groups ? 'other VAT problem' : 'VAT problem', groups ? 'other VAT problems' : 'VAT problems') : ''}.`}
            </p>
            <div className="bim-stats">
              <div><strong>{bills + invoices}</strong><span>documents checked</span></div>
              <div><strong>{groups}</strong><span>duplicate groups</span></div>
              <div><strong>{problems}</strong><span>VAT problems</span></div>
              {rp && <div><strong>{money(rp.net_vat_payable, rp.currency)}</strong><span>net VAT so far</span></div>}
            </div>
            {rp && (
              <div className="bim-return">
                <div className="bim-return-head">
                  <strong>{periodLabel(rp.period)} return position</strong>
                  <span className="bd-muted">Due {longDate(rp.due_date)}{rp.organizations > 1 ? ` · first of ${rp.organizations} organizations` : ''}</span>
                </div>
                <dl>
                  <dt>Output VAT</dt><dd>{money(rp.output_vat, rp.currency)}</dd>
                  <dt>Input VAT</dt><dd>{money(rp.input_vat, rp.currency)}</dd>
                  <dt>{Number(rp.net_vat_payable) < 0 ? 'Net refundable' : 'Net payable'}</dt>
                  <dd><strong>{money(Math.abs(Number(rp.net_vat_payable)), rp.currency)}</strong></dd>
                </dl>
              </div>
            )}
          </>
        )}

        <p className="bim-text">
          From now on I'll run {schedules.length ? listWords(schedules) : 'when you run me'}, and send results to your {where}.
          I only read your books; I never change a bill or file anything.
        </p>

        <div className="bim-actions">
          {!failed && total > 0 && (
            <button type="button" className="bh-btn is-primary" onClick={onOpenInbox}>
              <Inbox size={14} /> See the {plural(total, 'finding', 'findings')}
            </button>
          )}
          <button type="button" className="bh-btn" onClick={() => onEditBot(bot.id)}><Settings2 size={14} /> Adjust bot</button>
          <button type="button" className="bh-btn" onClick={() => onOpenBot(bot.id)}>Open {bot.name}</button>
        </div>
        <p className="bd-muted bim-foot">
          {bot.next_run_at ? `Next run: ${runTime(bot.next_run_at)}` : 'No run planned'}
          {run.credits != null ? ` · used ${credits(run.credits)} credits` : ''}
        </p>
      </article>
    </div>
  );
}
