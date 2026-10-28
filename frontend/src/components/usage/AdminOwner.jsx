import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { addOwnerTopup, fetchUsageAdminOwner, setOwnerPlan } from '../../services/api';
import { PersonActivity } from './TeamLimits';
import { alertText } from './AdminOverview';
import {
  Avatar, Bar, ErrorBox, IconBack, IconChevron, Loading, Pill, StatePill, TYPICAL, downloadCsv, longDate, modelsLabel,
  notOk, num, RESULT, shortDate, toneOfState, usd, whenLabel, KIND,
} from './ui';

// One owner, for the Accutax admin: the month's allowance and what it cost, the people, all activity (with person and
// result filters and a CSV export), the plan form, extra credits with a preview, and what happened to the account.

const REASONS = ['Pilot', 'Goodwill', 'Refund for failed answers', 'Paid top-up'];
const PAGE = 10;

const PlanForm = ({ data, token, ownerId, onSaved }) => {
  const row = data.plan_row || {};
  const [plan, setPlan] = useState(row.plan || 'standard');
  const [monthly, setMonthly] = useState(row.monthly_credits ?? '');
  const [daily, setDaily] = useState(row.user_daily_credits ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const dirty = plan !== (row.plan || 'standard') || String(monthly) !== String(row.monthly_credits ?? '')
    || String(daily) !== String(row.user_daily_credits ?? '');
  const chosen = data.plans.find((p) => p.key === plan) || {};
  const b = data.bucket;
  const planSize = chosen.per_seat == null ? null : chosen.per_seat * Math.min(b.seats_counted || b.seats, chosen.max_seats || Infinity);
  const save = async () => {
    setBusy(true);
    setError('');
    try {
      await setOwnerPlan(ownerId, plan, monthly === '' ? null : Number(monthly), daily === '' ? null : Number(daily), token);
      onSaved();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <form className="uz-card" onSubmit={(e) => { e.preventDefault(); if (dirty) save(); }}>
      <h3 className="uz-h-sm">Plan</h3>
      {error && <ErrorBox message={error} />}
      <label className="uz-field">Plan
        <select className="uz-select" value={plan} onChange={(e) => setPlan(e.target.value)}>
          {data.plans.map((p) => (
            <option key={p.key} value={p.key}>{p.label}{p.per_seat ? ` · ${num(p.per_seat)} credits / seat, max ${p.max_seats}` : ''}</option>
          ))}
        </select>
      </label>
      <div className="uz-grid" style={{ '--min': '130px' }}>
        <div className="uz-field">Seats
          <div className="uz-input" style={{ display: 'flex', alignItems: 'center', color: 'var(--ink-soft)' }}>
            {b.seats}{b.seats_counted > b.seats ? ` of ${b.seats_counted}` : ''}
          </div>
          <small>Owner + accepted collaborators on the 1st</small>
        </div>
        <label className="uz-field">Default daily limit
          <input className="uz-input" type="number" min="0" step="20" value={daily} onChange={(e) => setDaily(e.target.value)}
            placeholder={chosen.user_daily == null ? 'no limit' : `${num(chosen.user_daily)} credits`} />
          <small>{daily === '' ? 'plan default' : `≈ ${num(Number(daily) / TYPICAL)} questions a day`}</small>
        </label>
      </div>
      <label className="uz-field"><span className="uz-field-label">Custom monthly credits <small>(optional)</small></span>
        <input className="uz-input" type="number" min="0" step="1000" value={monthly} onChange={(e) => setMonthly(e.target.value)}
          placeholder={planSize == null ? 'no limit on this plan' : `Uses plan × seats = ${num(planSize)}`} />
        <small>Overrides the plan for this owner only. Takes effect at once.</small>
      </label>
      <button type="submit" className={`uz-btn is-wide${dirty ? ' is-primary' : ''}`} disabled={!dirty || busy}>
        {busy ? 'Saving…' : dirty ? 'Save plan' : 'No changes to save'}
      </button>
    </form>
  );
};

const TopupForm = ({ data, token, ownerId, onSaved }) => {
  const [preset, setPreset] = useState(5000);
  const [custom, setCustom] = useState('');
  const [reason, setReason] = useState(REASONS[0]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const amount = preset === 'custom' ? Number(custom || 0) : preset;
  const b = data.bucket;
  const expires = new Date();
  expires.setFullYear(expires.getFullYear() + 1);
  const add = async () => {
    setBusy(true);
    setError('');
    try {
      await addOwnerTopup(ownerId, amount, note.trim() ? `${reason} — ${note.trim()}` : reason, 365, token);
      setNote('');
      setCustom('');
      onSaved();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <form className="uz-card" onSubmit={(e) => { e.preventDefault(); if (amount > 0) add(); }}>
      <div><h3 className="uz-h-sm">Add credits</h3>
        <p className="uz-muted uz-small" style={{ margin: '2px 0 0' }}>One-off credits on top of the monthly allowance, valid for a year.</p></div>
      {error && <ErrorBox message={error} />}
      <div role="group" aria-label="Amount" className="uz-grid" style={{ '--min': '80px', gap: 8 }}>
        {[1000, 5000, 'custom'].map((p) => (
          <button key={p} type="button" className="uz-chip" style={{ minHeight: 42, borderRadius: 10 }} aria-pressed={preset === p} onClick={() => setPreset(p)}>
            {p === 'custom' ? 'Custom' : num(p)}
          </button>
        ))}
      </div>
      {preset === 'custom' && (
        <label className="uz-field">Credits
          <input className="uz-input" type="number" min="1" value={custom} onChange={(e) => setCustom(e.target.value)} autoFocus />
        </label>
      )}
      <label className="uz-field"><span className="uz-field-label">Reason <span className="uz-req" aria-hidden="true">*</span></span>
        <select className="uz-select" value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASONS.map((r) => <option key={r}>{r}</option>)}
        </select>
      </label>
      <label className="uz-field"><span className="uz-field-label">Note <small>(kept with the top-up)</small></span>
        <input className="uz-input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. agreed on a call, Oct 28" />
      </label>
      <div className="uz-summary-box">
        <div><span className="uz-muted">Available after</span><b>{b.bucket == null ? 'no limit' : `${num(Math.max(0, b.bucket - b.used) + amount)} credits`}</b></div>
        <div><span className="uz-muted">Expires</span><span>{longDate(expires.toISOString())}</span></div>
        <div><span className="uz-muted">Cost to Accutax if all used</span><span>≈ {usd(amount / 1000)}</span></div>
      </div>
      <button type="submit" className="uz-btn is-primary is-wide" disabled={!(amount > 0) || busy}>{busy ? 'Adding…' : `Add ${num(amount)} credits`}</button>
    </form>
  );
};

const historyLine = (h) => {
  if (h.kind === 'alert') return alertText(h);
  if (h.kind === 'topup') return `+${num(h.credits)} credits · ${h.reason || 'top-up'}${h.expires_at ? `, until ${shortDate(h.expires_at)}` : ''}`;
  return `Plan set to ${h.plan}${h.monthly_credits != null ? ` · ${num(h.monthly_credits)} credits a month` : ''}`;
};

export const AdminOwner = ({ token, ownerId, focus = null, onBack }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [person, setPerson] = useState('all');
  const [result, setResult] = useState('all');
  const [shown, setShown] = useState(PAGE);
  const [open, setOpen] = useState(focus);
  const [showIdle, setShowIdle] = useState(false);
  const load = useCallback(() => {
    setError('');
    fetchUsageAdminOwner(ownerId, token).then(setData).catch((e) => setError(e.message));
  }, [ownerId, token]);
  useEffect(load, [load]);
  useEffect(() => { setOpen(focus); }, [focus]);

  const events = data?.activity.events || [];
  const filtered = useMemo(() => events.filter((e) => (person === 'all' || String(e.user_id) === person)
    && (result === 'all' || (result === 'notok' ? notOk(e.status) : e.mode === 'lite'))), [events, person, result]);

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const b = data.bucket;
  const t = data.activity.totals;
  const active = data.members.filter((m) => m.questions || m.user_id === focus);
  const idle = data.members.filter((m) => !m.questions && m.user_id !== focus);
  const avg = t.questions ? t.question_credits / t.questions : 0;
  const exportCsv = () => downloadCsv(`ai-usage-${(data.email || ownerId).replace(/[^a-z0-9]+/gi, '-')}-${new Date().toISOString().slice(0, 7)}.csv`, [
    ['When', (e) => e.at], ['Person', (e) => e.email || e.user_id], ['What', (e) => KIND[e.kind] || e.kind],
    ['Organizations', (e) => e.organization_ids.join(' ')], ['Model', (e) => modelsLabel(e)], ['Mode', (e) => e.mode],
    ['Tokens in', (e) => e.input_tokens], ['Tokens out', (e) => e.output_tokens], ['Cached tokens', (e) => e.cached_tokens],
    ['Credits', (e) => e.credits], ['Result', (e) => (RESULT[e.status] || {}).label || e.status]], events);

  return (
    <div className="uz-stack">
      <header className="uz-stack" style={{ gap: 12 }}>
        <nav aria-label="Breadcrumb" className="uz-crumbs">
          <button type="button" className="uz-link" onClick={onBack} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}><IconBack />AI spend</button>
          <span aria-hidden="true">/</span><span>Owners</span><span aria-hidden="true">/</span><span style={{ color: 'var(--ink)' }}>{data.email || `Owner ${ownerId}`}</span>
        </nav>
        <div className="uz-section-head" style={{ alignItems: 'center' }}>
          <div className="uz-person" style={{ gap: 14 }}>
            <Avatar email={data.email || String(ownerId)} large />
            <div style={{ minWidth: 0 }}>
              <h3 className="uz-h" style={{ fontSize: 21, overflowWrap: 'anywhere' }}>{data.email || `Owner ${ownerId}`}</h3>
              <div className="uz-muted uz-small">Owner · user {ownerId} · {data.organizations} organization{data.organizations === 1 ? '' : 's'} · {data.members.length} {data.members.length === 1 ? 'person' : 'people'}</div>
            </div>
          </div>
          <button type="button" className="uz-btn" onClick={exportCsv} disabled={!events.length}>Export CSV</button>
        </div>
      </header>

      <div className="uz-row" style={{ gap: 20 }}>
        <div className="uz-stack" style={{ flex: '999 1 600px', minWidth: 0, gap: 20 }}>
          <section aria-label="Allowance" className="uz-card">
            <div className="uz-section-head" style={{ alignItems: 'center' }}>
              <h3 className="uz-eyebrow">{new Date().toLocaleDateString(undefined, { month: 'long' })} allowance · {b.plan_label}</h3>
              <StatePill state={b.state} suffix={` · renews ${shortDate(b.resets_at)}`} />
            </div>
            <div className="uz-grid" style={{ '--min': '140px', gap: 16 }}>
              <div><div className="uz-muted uz-small">Used</div><div className="uz-stat-value">{b.percent == null ? '—' : `${b.percent}%`}</div></div>
              <div><div className="uz-muted uz-small">Credits</div><div className="uz-stat-value">{num(b.used)} <small>/ {b.bucket == null ? '∞' : num(b.bucket)}</small></div></div>
              <div><div className="uz-muted uz-small">Cost to Accutax</div><div className="uz-stat-value">{usd(b.used / 1000)}</div></div>
              <div><div className="uz-muted uz-small">Avg per question</div><div className="uz-stat-value">{num(avg)} <small>credits</small></div></div>
            </div>
            {b.bucket != null && <Bar pct={b.percent} tone={toneOfState(b.state)} thin />}
            {(b.topups > 0 || t.not_ok > 0) && (
              <div className="uz-legend">
                {b.topups > 0 && <span>Includes {num(b.topups)} top-up credits</span>}
                {t.not_ok > 0 && <span style={{ color: 'var(--danger-ink)' }}>{t.not_ok} not fully answered · {num(t.not_ok_credits)} credits charged</span>}
              </div>
            )}
          </section>

          <section aria-labelledby="uz-ppl" className="uz-card is-flush">
            <div className="uz-card-head"><h3 id="uz-ppl" className="uz-h-sm">People</h3>
              <span className="uz-muted uz-small">Limits are set by the owner; change them only on request</span></div>
            <div className="uz-table-wrap">
              <table className="uz-table" style={{ minWidth: 680 }}>
                <thead><tr><th>Person</th><th className="is-right">Questions</th><th className="is-right">Credits</th><th>Today</th><th>Daily limit</th><th>Access</th><th aria-label="Details" /></tr></thead>
                <tbody>
                  {[...active, ...(showIdle ? idle : [])].map((m) => (
                    <React.Fragment key={m.user_id}>
                      <tr className={m.role === 'owner' ? 'is-mine' : ''}>
                        <td><div style={{ fontWeight: 500 }}>{m.email || `User ${m.user_id}`}</div>
                          <div className="uz-muted uz-small">{m.role === 'owner' ? 'Owner' : m.invitation !== 'ACTIVE' ? 'Collaborator · invited' : 'Collaborator'}</div></td>
                        <td className="is-right">{num(m.questions)}</td>
                        <td className="is-right">{num(m.month_credits, 1)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{num(m.today_credits)} / {m.daily_cap == null ? '∞' : num(m.daily_cap)}</td>
                        <td style={{ whiteSpace: 'nowrap' }}>{m.daily_cap == null ? 'none' : `${num(Math.round(m.daily_cap / TYPICAL))} q · ${num(m.daily_cap)} cr`}
                          {m.cap_override == null && <span className="uz-muted"> (default)</span>}</td>
                        <td style={{ color: m.disabled ? 'var(--danger-ink)' : 'var(--success-ink)' }}>{m.disabled ? 'Off' : 'On'}</td>
                        <td className="is-right">
                          <button type="button" className="uz-btn is-ghost" aria-expanded={open === m.user_id} onClick={() => setOpen(open === m.user_id ? null : m.user_id)}>
                            <IconChevron style={{ transform: open === m.user_id ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
                            <span className="uz-sr">Questions of {m.email}</span>
                          </button>
                        </td>
                      </tr>
                      {open === m.user_id && <PersonActivity userId={m.user_id} ownerId={ownerId} token={token} colSpan={7} />}
                    </React.Fragment>
                  ))}
                  {!showIdle && idle.length > 0 && (
                    <tr><td colSpan={7}><span className="uz-muted">+ {idle.length} {idle.length === 1 ? 'person' : 'people'} with no activity this month</span>
                      {' · '}<button type="button" className="uz-link" onClick={() => setShowIdle(true)}>Show</button></td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="uz-log" className="uz-card is-flush">
            <div className="uz-card-head">
              <h3 id="uz-log" className="uz-h-sm">Activity · {num(t.events)} events</h3>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <label className="uz-sr" htmlFor="uz-f-person">Person</label>
                <select id="uz-f-person" className="uz-select" style={{ width: 'auto', minHeight: 34 }} value={person} onChange={(e) => { setPerson(e.target.value); setShown(PAGE); }}>
                  <option value="all">Everyone</option>
                  {data.members.filter((m) => m.questions).map((m) => <option key={m.user_id} value={String(m.user_id)}>{m.email}</option>)}
                </select>
                <label className="uz-sr" htmlFor="uz-f-result">Result</label>
                <select id="uz-f-result" className="uz-select" style={{ width: 'auto', minHeight: 34 }} value={result} onChange={(e) => { setResult(e.target.value); setShown(PAGE); }}>
                  <option value="all">All results</option><option value="notok">Not fully answered</option><option value="lite">Lite model</option>
                </select>
              </div>
            </div>
            <div className="uz-table-wrap">
              <table className="uz-table" style={{ minWidth: 820, fontSize: 13 }}>
                <thead><tr><th>When</th><th>Person</th><th>Scope</th><th>Model</th><th className="is-right">Tokens in / out</th><th className="is-right">Credits</th><th>Result</th></tr></thead>
                <tbody>
                  {filtered.slice(0, shown).map((e) => (
                    <tr key={e.id} className={notOk(e.status) ? 'is-bad' : ''}>
                      <td style={{ whiteSpace: 'nowrap' }}>{whenLabel(e.at)}</td>
                      <td>{(e.email || `User ${e.user_id}`).split('@')[0]}</td>
                      <td className="uz-muted">{KIND[e.kind] === 'Question' ? '' : `${KIND[e.kind] || e.kind} · `}{e.organization_ids.length === 1 ? '1 org' : `${e.organization_ids.length} orgs`}</td>
                      <td>{modelsLabel(e)}{e.mode === 'lite' && <Pill tone="warn">lite</Pill>}</td>
                      <td className="is-right uz-mono"><div>{num(e.input_tokens)} / {num(e.output_tokens)}</div>
                        {!!e.cached_tokens && <div className="uz-muted" style={{ fontSize: 11 }}>+{num(e.cached_tokens)} cached</div>}</td>
                      <td className="is-right">{num(e.credits, 1)}</td>
                      <td style={{ color: notOk(e.status) ? 'var(--danger-ink)' : 'var(--success-ink)' }}>{(RESULT[e.status] || {}).label || e.status}</td>
                    </tr>
                  ))}
                  {!filtered.length && <tr><td colSpan={7} className="uz-empty">No activity in this view.</td></tr>}
                </tbody>
              </table>
            </div>
            {filtered.length > shown && (
              <div className="uz-table-foot">
                <button type="button" className="uz-btn is-ghost is-wide" onClick={() => setShown(shown + PAGE)}>
                  Load {Math.min(PAGE, filtered.length - shown)} more <span className="uz-muted">· {filtered.length - shown} left</span>
                </button>
              </div>
            )}
          </section>
        </div>

        <aside aria-label="Plan and credits" className="uz-stack" style={{ flex: '1 1 320px', minWidth: 0, gap: 20 }}>
          <PlanForm key={JSON.stringify(data.plan_row)} data={data} token={token} ownerId={ownerId} onSaved={load} />
          <TopupForm data={data} token={token} ownerId={ownerId} onSaved={load} />
          <section aria-labelledby="uz-hist" className="uz-card">
            <h3 id="uz-hist" className="uz-h-sm">History</h3>
            <ol className="uz-history">
              {data.history.map((h, i) => (
                <li key={i}><div>{historyLine(h)}</div>
                  <div className="uz-muted" style={{ fontSize: 12 }}>{shortDate(h.at)} · {h.kind === 'alert' ? 'system' : h.kind === 'plan' ? `admin ${h.by ?? ''}`.trim() : 'admin'}</div></li>
              ))}
              {!data.history.length && <li className="uz-muted">Nothing yet.</li>}
            </ol>
          </section>
        </aside>
      </div>
    </div>
  );
};
