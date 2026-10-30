import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Calendar, Loader2, MoreHorizontal, Pause, Pencil, Play, ShieldCheck, Trash2 } from 'lucide-react';
import {
  addBotMemory, deleteBotMemory, dismissBotFinding, fetchBotFindings, fetchBotMemory, fetchBotRuns, runBotNow,
  undismissBotFinding,
} from '../../services/bots';
import BotMemoryView from './BotMemoryView';
import { botStatus, botSummary, credits, RULE_LABEL, runText, runTime, scheduleLine, shortDay, TRIGGER_LABEL } from './botText';
import './bots.css';
import './bots-pages.css';

const TABS = [
  { id: 'activity', label: 'Activity' },
  { id: 'findings', label: 'Findings' },
  { id: 'learned', label: 'What it learned' },
  { id: 'settings', label: 'Settings' },
];

function money(n, currency = 'AED') {
  if (n == null || Number.isNaN(Number(n))) return '';
  return `${currency || 'AED'} ${Number(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function RunItem({ run, onRunAgain, running }) {
  const failed = run.status === 'failed';
  const s = run.summary || {};
  const tone = failed ? 'danger' : (s.new_findings ? 'warning' : 'ok');
  return (
    <li className="bd-run">
      <span className={`bd-run-dot is-${tone}`} aria-hidden="true" />
      <div className="bd-run-body">
        <div className="bd-run-head">
          <strong>{runTime(run.started_at)}</strong>
          <span className="bd-muted">
            {TRIGGER_LABEL[run.trigger] || run.trigger}
            {failed ? " · didn't finish" : run.status === 'running' ? ' · running' : ` · ${credits(run.credits)} credits`}
          </span>
        </div>
        <p className="bd-run-text">{runText(run)}</p>
        {failed && s.complete === false && <p className="bd-muted">Nothing was marked fixed, because the check stopped early.</p>}
        <div className="bd-run-tags">
          {s.new_findings > 0 && <span className="bd-tag is-warning">{s.new_findings} new</span>}
          {s.fixed_findings > 0 && <span className="bd-tag is-ok">{s.fixed_findings} fixed</span>}
          {failed && (
            <button type="button" className="bh-btn" onClick={onRunAgain} disabled={running}>Run again now</button>
          )}
        </div>
      </div>
    </li>
  );
}

function FindingItem({ f, onDismiss, onRestore }) {
  const d = f.detail || {};
  const bills = d.bills || [];
  return (
    <li className={`bd-finding is-${f.state}`}>
      <div className="bd-finding-head">
        <div>
          <strong>{RULE_LABEL[f.rule] || f.rule}</strong>
          <span className={`bd-tag is-${f.state === 'new' ? 'warning' : f.state === 'fixed' ? 'ok' : 'muted'}`}>
            {f.state === 'dismissed' ? 'Not a problem' : f.state[0].toUpperCase() + f.state.slice(1)}
          </span>
          {d.side && <span className="bd-tag is-muted">{d.side === 'sales' ? 'Sales' : 'Bills'}</span>}
        </div>
        {f.amount != null && <span className="bd-amount">{money(f.amount, d.currency)}</span>}
      </div>
      {d.why && <p className="bd-finding-why">{d.why}</p>}
      {bills.length > 0 && (
        <p className="bd-muted">
          {bills.map((b) => [b.number, b.party, b.date && shortDay(b.date)].filter(Boolean).join(' · ')).join('  |  ')}
        </p>
      )}
      <div className="bd-run-tags">
        {f.state === 'dismissed'
          ? <button type="button" className="bh-btn" onClick={() => onRestore(f)}>Bring it back</button>
          : f.state !== 'fixed' && <button type="button" className="bh-btn" onClick={() => onDismiss(f)}>Not a problem</button>}
      </div>
    </li>
  );
}

/** The compact "What it learned" box on the Activity tab: notes and skipped findings, each one forgettable. */
function LearnedBox({ bot, token, refreshKey, onChanged }) {
  const [items, setItems] = useState([]);
  const [text, setText] = useState('');
  const load = useCallback(() => {
    fetchBotMemory(bot.id, token).then((r) => setItems(Array.isArray(r) ? r : [])).catch(() => setItems([]));
  }, [bot.id, token]);
  useEffect(load, [load, refreshKey]);

  const add = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    await addBotMemory(bot.id, text.trim(), 'note', token).catch(() => null);
    setText('');
    load();
    onChanged?.();
  };
  const forget = async (m) => {
    await deleteBotMemory(bot.id, m.id, token).catch(() => null);
    load();
    onChanged?.();
  };
  return (
    <section className="bd-side-card">
      <h3>What it learned</h3>
      <p className="bd-muted">Your instructions and the findings you marked "Not a problem". Forget one to bring it back.</p>
      <ul className="bd-learned">
        {items.length === 0 && <li className="bd-muted">Nothing yet.</li>}
        {items.map((m) => (
          <li key={m.id}>
            <span className={`bd-tag ${m.source === 'note' ? 'is-info' : 'is-muted'}`}>{m.source === 'note' ? 'Note' : 'Skipped'}</span>
            <span className="bd-learned-text">{m.text}</span>
            <button type="button" className="bd-link" onClick={() => forget(m)}>Forget</button>
          </li>
        ))}
      </ul>
      <form className="bd-teach" onSubmit={add}>
        <input className="input-text-clean" value={text} onChange={(e) => setText(e.target.value)} maxLength={200}
          placeholder="Teach it something…" />
        <button type="submit" className="bh-btn" disabled={!text.trim()}>Add</button>
      </form>
    </section>
  );
}

function SetupBox({ bot, onEdit }) {
  const d = bot.draft || {};
  const rows = [
    ['Runs', (d.schedules || []).map(scheduleLine).join(' + ') || 'No schedule'],
    ['Checks', `${(d.watches || []).length} of 5 · ${d.sensitivity === 'fewer' ? 'Fewer alerts' : d.sensitivity === 'more' ? 'Catch more' : 'Balanced'}`],
    ['Organizations', `${(d.org_ids || []).length}`],
    ['Tells you in', (d.deliver || ['inbox']).map((x) => ({ inbox: 'Inbox', bell: 'Bell', email: 'Email' }[x] || x)).join(', ')],
    ['Can change data', 'Never'],
  ];
  return (
    <section className="bd-side-card">
      <h3>Set up</h3>
      <dl className="bd-setup">
        {rows.map(([k, v]) => (
          <React.Fragment key={k}><dt>{k}</dt><dd className={k === 'Can change data' ? 'is-danger' : ''}>{v}</dd></React.Fragment>
        ))}
      </dl>
      <button type="button" className="bd-link" onClick={() => onEdit(bot)}>Edit settings →</button>
    </section>
  );
}

/**
 * One bot's page: what it is, what it did (runs), what it found, what it learned, and its settings.
 * `bot` comes from GET /api/v1/bots (with `overview`); runs and findings load here.
 */
export default function BotDetail({ bot, token = '', onBack, onEdit, onToggle, onDelete, onChanged }) {
  const [tab, setTab] = useState('activity');
  const [runs, setRuns] = useState([]);
  const [findings, setFindings] = useState([]);
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const [learnedKey, setLearnedKey] = useState(0);

  const load = useCallback(() => {
    if (!bot?.id) return;
    fetchBotRuns(bot.id, token).then((r) => setRuns(r.runs || [])).catch(() => setRuns([]));
    fetchBotFindings(bot.id, token).then((r) => setFindings(r.findings || [])).catch(() => setFindings([]));
  }, [bot?.id, token]);

  useEffect(() => { setTab('activity'); setMessage(''); setMenuOpen(false); }, [bot?.id]);
  useEffect(load, [load]);

  if (!bot) {
    return (
      <div className="bots-page">
        <button type="button" className="bh-btn" onClick={onBack}><ArrowLeft size={14} /> Bots</button>
        <p className="bd-muted" style={{ marginTop: 16 }}>This bot was not found. It may have been deleted.</p>
      </div>
    );
  }

  const ov = bot.overview || {};
  const status = botStatus(bot);
  const paused = bot.status === 'paused';
  const open = findings.filter((f) => f.state === 'new' || f.state === 'seen');
  const ordered = [...open, ...findings.filter((f) => f.state === 'fixed'), ...findings.filter((f) => f.state === 'dismissed')];
  const reminderOnly = (bot.draft?.schedules || []).length && (bot.draft.schedules).every((s) => s.kind === 'before_deadline');
  const Icon = reminderOnly ? Calendar : ShieldCheck;

  const runNow = async () => {
    setRunning(true);
    setMessage('');
    try {
      const res = await runBotNow(bot.id, token);
      setMessage(res.status === 'ok' ? (res.summary?.headline || 'Run finished.') : (res.error || 'The run did not finish.'));
    } catch (e) {
      setMessage(e.message);
    } finally {
      setRunning(false);
      load();
      onChanged?.();
    }
  };
  const dismiss = async (f) => {
    await dismissBotFinding(bot.id, f.key, '', (f.detail?.bills?.[0]?.party) || '', token).catch(() => null);
    load(); setLearnedKey((k) => k + 1); onChanged?.();
  };
  const restore = async (f) => {
    await undismissBotFinding(bot.id, f.key, token).catch(() => null);
    load(); setLearnedKey((k) => k + 1); onChanged?.();
  };

  return (
    <div className="bots-page">
      <button type="button" className="bh-btn bd-back" onClick={onBack}><ArrowLeft size={14} /> Bots</button>

      <header className="bd-head">
        <span className={`bh-bot-icon is-${bot.status} bd-icon`}><Icon size={22} /></span>
        <div className="bd-head-text">
          <div className="bh-card-name-row">
            <h1 className="bots-title">{bot.name}</h1>
            <span className={`bh-badge is-${status.tone}`}><span className="bh-badge-dot" />{status.label}</span>
            <span className="bh-pill">VAT agent</span>
          </div>
          <p className="bots-lede">
            {botSummary(bot.draft)}. Tells you in your inbox{(bot.draft?.deliver || []).includes('bell') ? ' and bell' : ''}.
            {bot.created_at && ` Created ${shortDay(bot.created_at)}.`}
          </p>
          {bot.status === 'error' && bot.status_note && <p className="bd-alert">{bot.status_note}</p>}
        </div>
        <div className="bd-head-actions">
          <button type="button" className="bh-btn" onClick={runNow} disabled={running || paused || bot.status === 'error'}
            title={paused ? 'Resume the bot to run it' : 'Run the checks now'}>
            {running ? <Loader2 size={14} className="bd-spin" /> : <Play size={14} />} Run now
          </button>
          <button type="button" className="bh-btn" onClick={() => onEdit(bot)}><Pencil size={14} /> Edit</button>
          <button type="button" className="bh-btn" onClick={() => onToggle(bot.id)}>
            {paused ? <><Play size={14} /> Resume</> : <><Pause size={14} /> Pause</>}
          </button>
          <div className="bd-menu-wrap">
            <button type="button" className="bh-btn is-icon" aria-label="More" onClick={() => setMenuOpen((v) => !v)}>
              <MoreHorizontal size={14} />
            </button>
            {menuOpen && (
              <div className="bd-menu">
                <button type="button" className="bd-menu-item is-danger" onClick={() => {
                  setMenuOpen(false);
                  if (window.confirm(`Delete ${bot.name}? Its runs, findings and memory are deleted too.`)) onDelete(bot.id);
                }}>
                  <Trash2 size={14} /> Delete bot
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {message && <p className="bd-message">{message}</p>}

      <nav className="bd-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id}
            className={`bd-tab${tab === t.id ? ' active' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
            {t.id === 'findings' && open.length > 0 && <span className="bh-pill">{open.length} open</span>}
          </button>
        ))}
      </nav>

      {tab === 'activity' && (
        <div className="bd-columns">
          <div>
            <div className="bd-stats">
              <div className="bh-stat"><span className="bh-stat-label">Runs</span><span className="bh-stat-value">{ov.runs || 0}</span>
                <span className="bh-stat-note">{bot.created_at ? `since ${shortDay(bot.created_at)}` : ''}</span></div>
              <div className="bh-stat"><span className="bh-stat-label">Found</span><span className="bh-stat-value">{ov.found || 0}</span>
                <span className="bh-stat-note">problems in total</span></div>
              <div className="bh-stat"><span className="bh-stat-label">Fixed</span><span className="bh-stat-value is-ok">{ov.fixed || 0}</span>
                <span className="bh-stat-note">cleared in your books</span></div>
              <div className="bh-stat"><span className="bh-stat-label">Credits</span><span className="bh-stat-value">{credits(ov.credits_this_month)}</span>
                <span className="bh-stat-note">this month</span></div>
            </div>
            <div className="bh-section-head">
              <h2>Run history</h2>
              <span className="bh-section-note">{!paused && bot.next_run_at ? `Next: ${runTime(bot.next_run_at)}` : ''}</span>
            </div>
            {runs.length === 0
              ? <p className="bd-muted">No runs yet.{!paused && bot.next_run_at ? ` The first is ${runTime(bot.next_run_at)}.` : ''}</p>
              : <ul className="bd-runs">{runs.map((r) => <RunItem key={r.id} run={r} onRunAgain={runNow} running={running} />)}</ul>}
          </div>
          <aside className="bd-side">
            <LearnedBox bot={bot} token={token} refreshKey={learnedKey} onChanged={() => { load(); onChanged?.(); }} />
            <SetupBox bot={bot} onEdit={onEdit} />
          </aside>
        </div>
      )}

      {tab === 'findings' && (
        ordered.length === 0
          ? <p className="bd-muted">This bot has not found anything yet.</p>
          : <ul className="bd-findings">{ordered.map((f) => <FindingItem key={f.key} f={f} onDismiss={dismiss} onRestore={restore} />)}</ul>
      )}

      {tab === 'learned' && (
        <BotMemoryView bot={bot} token={token} onMemoryChanged={() => { load(); setLearnedKey((k) => k + 1); onChanged?.(); }} />
      )}

      {tab === 'settings' && (
        <div className="bd-settings"><SetupBox bot={bot} onEdit={onEdit} /></div>
      )}
    </div>
  );
}
