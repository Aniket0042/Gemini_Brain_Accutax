import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowLeft, Bell, Building2, Clock, Inbox, Loader2, Mail, MessageCircle, Play, Plus, Power,
  ShieldCheck, Trash2, X,
} from 'lucide-react';
import { checkBotDraft, createBotOnServer, runBotNow, tryBotDraft, updateBotOnServer } from '../../services/bots';
import { credits, inShort, RULE_LABEL, runTime, shortDay, WATCH_BAR, WATCH_INFO } from './botText';
import './bots.css';
import './bots-pages.css';
import './bots-builder.css';

const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'],
  ['sat', 'Saturday'], ['sun', 'Sunday']];
const KINDS = [['weekly', 'Every week'], ['monthly', 'Every month'], ['before_deadline', 'Before the VAT return'],
  ['after_period_end', 'After a period ends']];
const DEFAULT_BEFORE = [14, 7, 1];
const MAX_SCHEDULES = 6;

function newSchedule(kind) {
  if (kind === 'monthly') return { kind, day_of_month: 1, time: '08:00' };
  if (kind === 'before_deadline') return { kind, days_before: [...DEFAULT_BEFORE], time: '09:00' };
  if (kind === 'after_period_end') return { kind, days_after: 3, time: '09:00' };
  return { kind: 'weekly', day: 'mon', time: '08:00' };
}

function Section({ icon: Icon, title, aside, children }) {
  return (
    <section className="bb-section">
      <header className="bb-section-head">
        <h2>{Icon && <Icon size={15} />} {title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

function Toggle({ on, onChange, label, disabled }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      className={`bb-toggle${on ? ' on' : ''}`} onClick={() => onChange(!on)}>
      <span className="bb-toggle-knob" />
    </button>
  );
}

function ScheduleRow({ s, index, onChange, onRemove, canRemove }) {
  const set = (patch) => onChange({ ...s, ...patch });
  const toggleDay = (d) => {
    const has = (s.days_before || []).includes(d);
    const next = has ? s.days_before.filter((x) => x !== d) : [...(s.days_before || []), d].sort((a, b) => b - a);
    set({ days_before: next });
  };
  const addDay = () => {
    const raw = window.prompt('How many days before the return? (1 to 60)');
    const n = parseInt(raw, 10);
    if (n >= 1 && n <= 60 && !(s.days_before || []).includes(n)) toggleDay(n);
  };
  const shownDays = [...new Set([...DEFAULT_BEFORE, ...(s.days_before || [])])].sort((a, b) => b - a);
  return (
    <div className="bb-schedule">
      <select className="bb-select" aria-label={`Schedule ${index + 1} type`} value={s.kind}
        onChange={(e) => onChange(newSchedule(e.target.value))}>
        {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      {s.kind === 'weekly' && (
        <>
          <span className="bd-muted">on</span>
          <select className="bb-select" aria-label="Day" value={s.day || 'mon'} onChange={(e) => set({ day: e.target.value })}>
            {DAYS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </>
      )}
      {s.kind === 'monthly' && (
        <>
          <span className="bd-muted">on day</span>
          <select className="bb-select is-narrow" aria-label="Day of month" value={s.day_of_month || 1}
            onChange={(e) => set({ day_of_month: Number(e.target.value) })}>
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </>
      )}
      {s.kind === 'before_deadline' && (
        <div className="bb-day-chips">
          {shownDays.map((d) => (
            <button key={d} type="button" className={`bb-day-chip${(s.days_before || []).includes(d) ? ' on' : ''}`}
              onClick={() => toggleDay(d)}>{d} day{d === 1 ? '' : 's'}</button>
          ))}
          <button type="button" className="bb-add-day" onClick={addDay}><Plus size={12} /> Day</button>
        </div>
      )}
      {s.kind === 'after_period_end' && (
        <>
          <input type="number" className="bb-input is-narrow" min={0} max={27} aria-label="Days after the period ends"
            value={s.days_after ?? 3} onChange={(e) => set({ days_after: Math.max(0, Math.min(27, Number(e.target.value) || 0)) })} />
          <span className="bd-muted">days after</span>
        </>
      )}
      <span className="bd-muted">at</span>
      <input type="time" className="bb-input is-time" aria-label="Time" value={s.time || '08:00'}
        onChange={(e) => set({ time: e.target.value })} />
      <button type="button" className="btn-icon-subtle bb-remove" onClick={onRemove} disabled={!canRemove}
        aria-label="Remove this time" title={canRemove ? 'Remove' : 'A bot needs at least one time'}>
        <Trash2 size={14} />
      </button>
    </div>
  );
}

/** The preview's list of what last month's check found: duplicate groups and other findings, first 20. */
function PreviewFindings({ result }) {
  const items = [];
  for (const rev of result?.reviews || []) {
    for (const g of rev.groups || []) {
      const b = (g.bills || [])[0] || {};
      items.push({ key: g.key, label: RULE_LABEL[g.rule] || g.rule, who: b.vendor_name, amount: g.extra_amount, when: b.document_date });
    }
    for (const f of rev.findings || []) {
      const b = f.bill || {};
      items.push({ key: f.key, label: RULE_LABEL[f.rule] || f.rule, who: b.vendor_name, amount: b.net_purchases, when: b.document_date });
    }
  }
  if (!items.length) return null;
  return (
    <ul className="bb-preview-list">
      {items.slice(0, 20).map((it) => (
        <li key={it.key}>
          <span className="bb-preview-list-label">{it.label}</span>
          <span className="bd-muted">{[it.who, it.when && shortDay(it.when)].filter(Boolean).join(' · ')}</span>
        </li>
      ))}
      {items.length > 20 && <li className="bd-muted">and {items.length - 20} more</li>}
    </ul>
  );
}

/**
 * The bot builder: every setting on the left, and on the right what the bot would have found last month, its next
 * runs and its cost. `initial` is a draft response ({draft, issues, next_runs, monthly_credits}); with `botId` the
 * page edits that bot and saves changes, without it Turn on creates the bot and runs it once.
 */
export default function BotBuilderPage({ initial, botId = null, botStatus = null, token = '', orgs = [], onBack, onDone }) {
  const [draft, setDraft] = useState(initial?.draft || {});
  const [issues, setIssues] = useState(initial?.issues || []);
  const [nextRuns, setNextRuns] = useState(initial?.next_runs || []);
  const [monthly, setMonthly] = useState(initial?.monthly_credits || 0);
  const [preview, setPreview] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [showFindings, setShowFindings] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [note, setNote] = useState('');
  const [addingOrg, setAddingOrg] = useState(false);
  const checkTimer = useRef(null);
  const previewTimer = useRef(null);
  const editing = Boolean(botId);

  const orgName = (id) => orgs.find((o) => o.id === id)?.name || `Organization ${id}`;
  const blocking = issues.filter((i) => !i.fixed);
  const questions = draft.questions || [];

  // Every change: the server checks the draft and works out the next runs and the cost (400 ms after typing stops).
  const change = (patch) => {
    const next = { ...draft, ...patch };
    setDraft(next);
    clearTimeout(checkTimer.current);
    checkTimer.current = setTimeout(async () => {
      try {
        const res = await checkBotDraft(next, token);
        setIssues(res.issues || []);
        setNextRuns(res.next_runs || []);
        setMonthly(res.monthly_credits || 0);
      } catch { /* keep the last answer; the save shows any real problem */ }
    }, 400);
  };

  // Opening a saved bot (no next runs yet): ask the server once for its checks, next runs and cost.
  useEffect(() => {
    if ((initial?.next_runs || []).length) return;
    checkBotDraft(draft, token).then((res) => {
      setIssues(res.issues || []);
      setNextRuns(res.next_runs || []);
      setMonthly(res.monthly_credits || 0);
    }).catch(() => null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runPreview = async (d = draft) => {
    setPreviewBusy(true);
    setPreviewError('');
    try {
      setPreview(await tryBotDraft(d, 'last_month', token));
    } catch (e) {
      setPreviewError(e.message || 'The preview did not run.');
    } finally {
      setPreviewBusy(false);
    }
  };

  // The preview reruns when what it checks changes (checks, sensitivity, organizations), not on every keystroke.
  const previewKey = useMemo(
    () => JSON.stringify([draft.watches, draft.sensitivity, draft.org_ids]),
    [draft.watches, draft.sensitivity, draft.org_ids],
  );
  useEffect(() => {
    clearTimeout(previewTimer.current);
    previewTimer.current = setTimeout(() => runPreview(draft), preview ? 800 : 0);
    return () => clearTimeout(previewTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  const setSchedule = (i, s) => change({ schedules: draft.schedules.map((x, j) => (j === i ? s : x)) });
  const removeSchedule = (i) => change({ schedules: draft.schedules.filter((_, j) => j !== i) });
  const addSchedule = () => change({ schedules: [...(draft.schedules || []), newSchedule(draft.schedules?.length ? 'before_deadline' : 'weekly')] });
  const toggleWatch = (id, on) => change({ watches: on ? [...(draft.watches || []), id] : (draft.watches || []).filter((w) => w !== id) });
  const toggleDeliver = (d, on) => change({ deliver: on ? [...(draft.deliver || ['inbox']), d] : (draft.deliver || []).filter((x) => x !== d) });
  const removeOrg = (id) => change({ org_ids: (draft.org_ids || []).filter((o) => o !== id) });
  const addOrg = (id) => { setAddingOrg(false); if (id) change({ org_ids: [...(draft.org_ids || []), Number(id)] }); };
  const addNote = (e) => {
    e.preventDefault();
    if (!note.trim()) return;
    change({ notes: [...(draft.notes || []), note.trim()] });
    setNote('');
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const clean = { ...draft, questions: [] };
      if (editing) {
        await updateBotOnServer(botId, { draft: clean }, token);
        onDone(botId);
      } else {
        const bot = await createBotOnServer(clean, 'active', token);
        // It runs once right away, and introduces itself in chat with the results.
        const run = await runBotNow(bot.id, token, { first: true })
          .catch((e) => ({ status: 'failed', error: e.message }));
        onDone(bot.id, { created: true, bot, run });
      }
    } catch (e) {
      setError(e.message || 'Could not save the bot.');
      setSaving(false);
    }
  };

  const monthName = preview?.period?.start
    ? new Date(`${preview.period.start}T00:00:00Z`).toLocaleDateString('en-GB', { month: 'long', timeZone: 'UTC' }) : 'last month';
  const byWatch = preview?.by_watch || {};
  const maxBar = Math.max(1, ...Object.values(byWatch));
  const alerts = preview?.would_send?.alerts ?? 0;
  const otherOrgs = orgs.filter((o) => !(draft.org_ids || []).includes(o.id));
  const watchesOn = (draft.watches || []).length;

  return (
    <div className="bots-page bb-page">
      <div className="bb-top">
        <button type="button" className="bh-btn" onClick={onBack}><ArrowLeft size={14} /> Back</button>
        <strong className="bb-top-name">{draft.name || 'New bot'}</strong>
        <span className={`bh-badge ${editing && botStatus === 'active' ? 'is-active' : 'is-paused'}`}>
          <span className="bh-badge-dot" />{editing ? (botStatus === 'paused' ? 'Paused' : 'Active') : 'Draft'}
        </span>
        <span className="bh-pill">VAT agent</span>
        <span className="bb-top-status bd-muted">{editing ? 'Changes are saved when you press Save' : 'Not turned on yet · nothing runs until you turn it on'}</span>
        <button type="button" className="bh-btn" onClick={() => runPreview()} disabled={previewBusy}>
          {previewBusy ? <Loader2 size={14} className="bd-spin" /> : <Play size={14} />} Try it on last month
        </button>
        <button type="button" className="bh-btn is-primary" onClick={save} disabled={saving || blocking.length > 0}
          title={blocking.length ? blocking[0].message : undefined}>
          {saving ? <Loader2 size={14} className="bd-spin" /> : <Power size={14} />} {editing ? 'Save changes' : 'Turn on'}
        </button>
      </div>

      {error && <p className="bd-alert">{error}</p>}

      <div className="bb-columns">
        <div className="bb-main">
          {(questions.length > 0 || blocking.length > 0) && (
            <div className="bb-attention">
              <AlertTriangle size={15} />
              <div>
                {questions.map((q) => <p key={q}>{q}</p>)}
                {blocking.map((i) => <p key={i.field + i.message}>{i.message}</p>)}
              </div>
            </div>
          )}

          <section className="bb-section">
            <label className="bb-label" htmlFor="bb-name">Name</label>
            <input id="bb-name" className="bb-input" value={draft.name || ''} maxLength={60}
              onChange={(e) => change({ name: e.target.value })} />
            <span className="bb-label">Organizations it checks</span>
            <div className="bb-orgs">
              {(draft.org_ids || []).map((id) => (
                <span key={id} className="bb-org-chip">
                  <Building2 size={12} /> {orgName(id)}
                  <button type="button" aria-label={`Remove ${orgName(id)}`} onClick={() => removeOrg(id)}><X size={12} /></button>
                </span>
              ))}
              {otherOrgs.length > 0 && (draft.org_ids || []).length < 10 && (addingOrg ? (
                <select className="bb-select" autoFocus defaultValue="" onChange={(e) => addOrg(e.target.value)} onBlur={() => setAddingOrg(false)}>
                  <option value="" disabled>Choose an organization</option>
                  {otherOrgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                </select>
              ) : (
                <button type="button" className="bb-add" onClick={() => setAddingOrg(true)}><Plus size={13} /> Add organization</button>
              ))}
            </div>
            <p className="bd-muted">{(draft.org_ids || []).length <= 1 ? 'Using your current organization. ' : ''}Up to 10.</p>
            <p className="bb-inshort"><strong>In short:</strong> {inShort(draft)}</p>
          </section>

          <Section icon={Clock} title="When it runs">
            <div className="bb-schedules">
              {(draft.schedules || []).map((s, i) => (
                <ScheduleRow key={i} s={s} index={i} onChange={(v) => setSchedule(i, v)} onRemove={() => removeSchedule(i)}
                  canRemove={(draft.schedules || []).length > 1} />
              ))}
            </div>
            <div className="bb-section-foot">
              {(draft.schedules || []).length < MAX_SCHEDULES && (
                <button type="button" className="bb-add" onClick={addSchedule}><Plus size={13} /> Add another time</button>
              )}
              <span className="bd-muted">Times are Gulf Standard Time. Quarters follow calendar quarters.</span>
            </div>
          </Section>

          <Section
            icon={ShieldCheck}
            title={<>What it checks <span className="bh-pill">{watchesOn} of {WATCH_INFO.length} on</span></>}
            aside={(
              <div className="bb-seg" role="group" aria-label="Sensitivity">
                <span className="bd-muted">Sensitivity</span>
                {[['fewer', 'Fewer alerts'], ['balanced', 'Balanced'], ['more', 'Catch more']].map(([k, l]) => (
                  <button key={k} type="button" className={`bb-seg-btn${(draft.sensitivity || 'balanced') === k ? ' on' : ''}`}
                    onClick={() => change({ sensitivity: k })}>{l}</button>
                ))}
              </div>
            )}
          >
            <p className="bd-muted">Balanced flags what is likely wrong. "Fewer" drops the least certain checks; "Catch more" also flags round-number bills.</p>
            {[['purchases', 'Bills you received'], ['sales', 'Invoices you sent']].map(([side, title]) => (
              <div key={side} className="bb-watch-group">
                <h3>{title}</h3>
                {WATCH_INFO.filter((w) => w.side === side).map((w) => {
                  const on = (draft.watches || []).includes(w.id);
                  return (
                    <div key={w.id} className="bb-watch">
                      <Toggle on={on} label={w.label} onChange={(v) => toggleWatch(w.id, v)} />
                      <div><strong>{w.label}</strong><span className="bd-muted">{w.desc}</span></div>
                    </div>
                  );
                })}
              </div>
            ))}
          </Section>

          <Section icon={Bell} title="Where it tells you">
            <div className="bb-deliver">
              <div className="bb-deliver-item is-on"><Inbox size={15} /><div><strong>Bot inbox</strong><span className="bd-muted">Always on</span></div></div>
              <div className={`bb-deliver-item${(draft.deliver || []).includes('bell') ? ' is-on' : ''}`}>
                <Bell size={15} /><div><strong>Bell alerts</strong><span className="bd-muted">When something new is found</span></div>
                <Toggle on={(draft.deliver || []).includes('bell')} label="Bell alerts" onChange={(v) => toggleDeliver('bell', v)} />
              </div>
              <div className="bb-deliver-item is-soon"><Mail size={15} /><div><strong>Email digest</strong><span className="bd-muted">Coming soon</span></div></div>
              <div className="bb-deliver-item is-soon"><MessageCircle size={15} /><div><strong>WhatsApp</strong><span className="bd-muted">Coming soon</span></div></div>
            </div>
          </Section>

          <Section icon={ShieldCheck} title="What it's allowed to do">
            <div className="bb-rules">
              <div className="bb-rule"><span>Read and review bills and invoices</span><span className="bd-tag is-ok">Allowed</span></div>
              <div className="bb-rule"><span>Export its findings</span><span className="bd-tag is-ok">Allowed</span></div>
              <div className="bb-rule">
                <span>Email anyone outside your organization</span>
                <select className="bb-select" aria-label="Email outside your organization" value={draft.rules?.email_outside || 'ask'}
                  onChange={(e) => change({ rules: { ...(draft.rules || {}), email_outside: e.target.value } })}>
                  <option value="ask">Ask me first</option>
                  <option value="never">Never</option>
                </select>
              </div>
              <div className="bb-rule"><span>Change bills, invoices or file a VAT return</span><span className="bd-tag is-danger">Never · read-only</span></div>
            </div>
          </Section>

          <Section title="Instructions for this bot" aside={<span className="bd-muted">Shapes how it writes, never the figures</span>}>
            <ul className="bb-notes">
              {(draft.notes || []).map((n, i) => (
                <li key={`${i}-${n}`}>
                  <span>{n}</span>
                  <button type="button" className="btn-icon-subtle" aria-label="Remove instruction"
                    onClick={() => change({ notes: draft.notes.filter((_, j) => j !== i) })}><X size={13} /></button>
                </li>
              ))}
            </ul>
            <form className="bd-teach" onSubmit={addNote}>
              <input className="input-text-clean" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)}
                placeholder="Rent from Al Noor Properties is billed in two parts; don't flag those as duplicates" />
              <button type="submit" className="bh-btn" disabled={!note.trim()}>Add</button>
            </form>
          </Section>
        </div>

        <aside className="bb-side">
          <section className="bd-side-card bb-preview">
            <span className="bb-eyebrow">Preview</span>
            <h3>If it had run in {monthName}…</h3>
            <p className="bd-muted">
              {preview?.period?.start ? `Real check on ${shortDay(preview.period.start)} – ${shortDay(preview.period.end)} · read-only` : 'A real check on last month · read-only'}
            </p>
            {previewBusy && !preview && <p className="bd-muted"><Loader2 size={13} className="bd-spin" /> Checking last month…</p>}
            {previewError && <p className="bd-alert">{previewError}</p>}
            {preview && (
              <>
                <div className={`bb-preview-stats${previewBusy ? ' is-stale' : ''}`}>
                  <div><strong>{alerts}</strong><span>alerts</span></div>
                  <div><strong>{preview.would_send?.digests ?? 0}</strong><span>digests</span></div>
                  <div><strong>{(preview.bills_checked || 0) + (preview.invoices_checked || 0)}</strong><span>documents checked</span></div>
                </div>
                <ul className="bb-bars">
                  {(draft.watches || []).map((w) => (
                    <li key={w}>
                      <span>{WATCH_BAR[w] || w}</span>
                      <span className="bb-bar"><span style={{ width: `${(100 * (byWatch[w] || 0)) / maxBar}%` }} /></span>
                      <strong>{byWatch[w] || 0}</strong>
                    </li>
                  ))}
                </ul>
                {alerts > 0 && (
                  <button type="button" className="bh-btn bb-wide" onClick={() => setShowFindings((v) => !v)}>
                    {showFindings ? 'Hide findings' : `See these ${alerts} findings`}
                  </button>
                )}
                {showFindings && <PreviewFindings result={preview} />}
              </>
            )}
          </section>

          <section className="bd-side-card">
            <h3>Next runs</h3>
            {nextRuns.length === 0 ? <p className="bd-muted">Add a time to see when it runs.</p> : (
              <ul className="bb-next">
                {nextRuns.map((r) => (
                  <li key={r.at + r.reason}>
                    <span className={`bb-next-dot${/return/i.test(r.reason) ? ' is-return' : ''}`} />
                    <span>{runTime(r.at)}</span>
                    <span className="bd-muted">{/^weekly|^monthly/i.test(r.reason) ? r.reason[0].toUpperCase() + r.reason.slice(1) : r.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="bd-side-card">
            <div className="bb-cost"><strong>≈ {credits(monthly)}</strong><span className="bd-muted">credits a month</span></div>
            <p className="bd-muted">Each run is about 90 credits. Return reminders run only before each return.</p>
            <button type="button" className="bh-btn is-primary bb-wide" onClick={save} disabled={saving || blocking.length > 0}>
              {saving ? <Loader2 size={14} className="bd-spin" /> : <Power size={14} />}
              {editing ? 'Save changes' : `Turn on ${draft.name || 'bot'}`}
            </button>
            {!editing && <p className="bd-muted bb-center">It runs once right away so you see real results.</p>}
          </section>
        </aside>
      </div>
    </div>
  );
}
