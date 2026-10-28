import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchMemberUsage, fetchOwnerUsage, setMemberCap } from '../../services/api';
import {
  Bar, ErrorBox, IconChevron, IconSearch, Loading, Person, Pill, RESULT, StatePill, forecastPercent, longDate,
  modelsLabel, notOk, num, questions, shortDate, toneOfState, whenLabel, KIND,
} from './ui';

// "Team & limits": the owner's page. The month's allowance with a forecast to renewal, what happens when it runs out,
// and the team: each person's month and day, their daily limit (in questions) and AI access, saved together.

const PRESETS = [5, 10, 25, 50, 100, 250];

// A person's newest questions in this bucket, under their row. `ownerId` null: the caller's own bucket.
export const PersonActivity = ({ userId, ownerId = null, token, colSpan }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { fetchMemberUsage(userId, ownerId, token).then(setData).catch((e) => setError(e.message)); }, [userId, ownerId, token]);
  return (
    <tr className="uz-activity-row">
      <td colSpan={colSpan} style={{ background: 'var(--surface-2)', padding: '12px 18px 16px' }}>
        {error ? <ErrorBox message={error} /> : !data ? <span className="uz-muted">Loading…</span> : (
          <div className="uz-stack" style={{ gap: 10 }}>
            <div className="uz-legend" style={{ fontSize: 12.5 }}>
              <span><b style={{ color: 'var(--ink)' }}>{num(data.totals.events)}</b>&nbsp;questions and files</span>
              <span><b style={{ color: 'var(--ink)' }}>{num(data.totals.input_tokens)} / {num(data.totals.output_tokens)}</b>&nbsp;tokens in / out</span>
              <span><b style={{ color: 'var(--ink)' }}>{num(data.totals.credits, 1)}</b>&nbsp;credits (${num(data.totals.cost_usd, 3)})</span>
              {!!data.totals.lite_events && <span>{data.totals.lite_events} on the lite model</span>}
              {!!data.totals.not_ok && <span style={{ color: 'var(--danger-ink)' }}>{data.totals.not_ok} not fully answered</span>}
            </div>
            {data.events.length ? (
              <div className="uz-scrollbox" style={{ maxHeight: 260, border: '1px solid var(--border)', borderRadius: 10, background: 'var(--surface)' }}>
                <table className="uz-table" style={{ fontSize: 12.5 }}>
                  <thead><tr><th>When</th><th>What</th><th>Model</th><th className="is-right">Tokens in / out</th><th className="is-right">Credits</th><th>Result</th></tr></thead>
                  <tbody>
                    {data.events.map((e) => (
                      <tr key={e.id}>
                        <td style={{ whiteSpace: 'nowrap' }}>{whenLabel(e.at)}</td>
                        <td>{KIND[e.kind] || e.kind}<span className="uz-muted"> · {e.organization_ids.length === 1 ? '1 org' : `${e.organization_ids.length} orgs`}</span></td>
                        <td>{modelsLabel(e)}{e.mode === 'lite' && <span className="uz-muted"> · lite</span>}</td>
                        <td className="is-right uz-mono">{num(e.input_tokens)} / {num(e.output_tokens)}</td>
                        <td className="is-right">{num(e.credits, 1)}</td>
                        <td style={{ color: notOk(e.status) ? 'var(--danger-ink)' : 'var(--success-ink)' }}>{(RESULT[e.status] || {}).label || e.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <span className="uz-muted">No AI use this month.</span>}
          </div>
        )}
      </td>
    </tr>
  );
};

const limitLabel = (credits, typical) => `${num(Math.round(credits / typical))} / day`;

export const TeamLimits = ({ token, meId }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(null);
  const load = useCallback(() => {
    setError('');
    fetchOwnerUsage(token).then((d) => { setData(d); setDraft({}); }).catch((e) => setError(e.message));
  }, [token]);
  useEffect(load, [load]);

  const typical = data?.typical_question_credits || 20;
  const members = data?.members || [];
  const changed = useMemo(() => members.filter((m) => {
    const d = draft[m.user_id];
    return d && (String(d.cap) !== String(m.cap_override ?? '') || d.off !== m.disabled);
  }), [members, draft]);
  const shownMembers = members.filter((m) => !query.trim() || (m.email || '').toLowerCase().includes(query.trim().toLowerCase()));

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const b = data.bucket;
  const forecast = forecastPercent(b.percent, b.resets_at);
  const leftQ = b.bucket == null ? null : questions(b.bucket - b.used, typical);
  const valueOf = (m) => draft[m.user_id] || { cap: m.cap_override ?? '', off: m.disabled };
  const setRow = (m, patch) => setDraft((all) => ({ ...all, [m.user_id]: { ...valueOf(m), ...patch } }));
  const save = async () => {
    setSaving(true);
    setError('');
    try {
      for (const m of changed) {
        const d = draft[m.user_id];
        await setMemberCap(m.user_id, d.cap === '' ? null : Number(d.cap), d.off, token);
      }
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };
  const limitChanges = changed.filter((m) => String(draft[m.user_id].cap) !== String(m.cap_override ?? '')).length;
  const accessChanges = changed.filter((m) => draft[m.user_id].off !== m.disabled).length;

  return (
    <div className="uz-stack">
      <section aria-labelledby="uz-allow" className="uz-card" style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 28 }}>
        <div className="uz-stack" style={{ flex: '999 1 520px', minWidth: 0, gap: 16 }}>
          <div className="uz-section-head" style={{ alignItems: 'center' }}>
            <h3 id="uz-allow" className="uz-eyebrow">Monthly allowance · {b.plan_label} plan</h3>
            <StatePill state={b.state} suffix={b.state === 'ok' ? ' · no action needed' : ''} />
          </div>
          {b.bucket == null ? (
            <div className="uz-big"><b>No limit</b><span>this month</span></div>
          ) : (
            <div className="uz-big">
              <b>≈ {num(leftQ)}</b><span>questions left</span>
              <em>{num(b.used)} of {num(b.bucket)} credits used ({b.percent}%)</em>
            </div>
          )}
          {b.bucket != null && (
            <div className="uz-stack" style={{ gap: 8 }}>
              <Bar pct={b.percent} tone={toneOfState(b.state)} forecast={forecast} marker={80}
                label={`${b.percent}% used${forecast != null ? `, forecast ${forecast}% by renewal` : ''}`} />
              <div className="uz-legend">
                <span><i className="uz-key" style={{ background: 'var(--success)' }} />Used</span>
                {forecast != null && <span><i className="uz-key uz-bar-forecast" style={{ position: 'static' }} />Forecast by {shortDate(b.resets_at)}: ~{forecast}%</span>}
                <span><i className="uz-key" style={{ width: 2, background: 'var(--warning)' }} />Warning at 80%</span>
              </div>
            </div>
          )}
          <p className="uz-muted uz-small" style={{ margin: 0 }}>
            Renews <b style={{ color: 'var(--ink)' }}>{longDate(b.resets_at)}</b> (in {data.renews_in_days} day{data.renews_in_days === 1 ? '' : 's'})
            {' '}· {b.seats} seat{b.seats === 1 ? '' : 's'}{b.seats_counted > b.seats ? ` (plan max ${b.max_seats})` : ''}
            {b.topups ? ` · +${num(b.topups)} top-up credits` : ''} · covers all {data.organizations} of your organizations
          </p>
        </div>
        <div className="uz-split uz-stack" style={{ flex: '1 1 260px', minWidth: 0, gap: 14 }}>
          <h3 className="uz-h-sm">If the allowance runs out</h3>
          <ol className="uz-steps">
            <li><span>1</span><span>Answers switch to a lighter, cheaper model for a short grace period (10% more).</span></li>
            <li><span>2</span><span>Then AI pauses for everyone until it renews. Accutax can add credits sooner.</span></li>
          </ol>
          <p className="uz-muted uz-small" style={{ margin: 0 }}>Everyone in your organizations sees a warning when 80% is used.</p>
        </div>
      </section>

      <section aria-labelledby="uz-team" className="uz-stack" style={{ gap: 14 }}>
        <div className="uz-section-head">
          <div>
            <h3 id="uz-team" className="uz-h">Team · {members.length} {members.length === 1 ? 'person' : 'people'}</h3>
            <p className="uz-muted uz-small" style={{ margin: '2px 0 0' }}>Daily limits stop one person from using up everyone's allowance. They reset at midnight.</p>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
            <label className="uz-search" style={{ minWidth: 220 }}>
              <IconSearch /><span className="uz-sr">Search people</span>
              <input type="search" placeholder="Search people" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <Pill title="Set by your plan; Accutax can change it">Default limit: {members[0]?.default_cap == null ? 'none' : limitLabel(members[0].default_cap, typical)}</Pill>
          </div>
        </div>

        <div className="uz-card is-flush">
          <div className="uz-table-wrap">
            <table className="uz-table" style={{ minWidth: 860 }}>
              <thead><tr><th>Person</th><th style={{ width: 210 }}>This month</th><th>Today</th><th>Daily limit</th><th>AI access</th><th aria-label="Details" /></tr></thead>
              <tbody>
                {shownMembers.map((m) => {
                  const v = valueOf(m);
                  const isChanged = changed.includes(m);
                  const share = b.used ? (100 * m.month_credits) / b.used : 0;
                  const capCredits = v.cap === '' ? m.default_cap : Number(v.cap);
                  const preset = v.cap === '' ? '' : String(v.cap);
                  const custom = v.cap !== '' && !PRESETS.some((q) => q * typical === Number(v.cap));
                  const sub = m.role === 'owner' ? `Owner${m.user_id === meId ? ' · you' : ''}`
                    : m.invitation !== 'ACTIVE' ? 'Collaborator · invited, not accepted yet'
                      : m.counts_next_month ? `Collaborator · joined ${shortDate(m.joined_at)}, counts from next month` : 'Collaborator';
                  return (
                    <React.Fragment key={m.user_id}>
                      <tr className={isChanged ? 'is-changed' : ''}>
                        <td><Person email={m.email || `User ${m.user_id}`} sub={sub} /></td>
                        <td style={{ minWidth: 170 }}>
                          {m.questions ? (
                            <>
                              <div className="uz-section-head" style={{ fontSize: 13 }}><span>{num(m.questions)} questions</span><span className="uz-muted">{Math.round(share)}%</span></div>
                              <div style={{ marginTop: 6 }}><Bar thin pct={share} label={`${Math.round(share)}% of the team's use`} /></div>
                            </>
                          ) : <span className="uz-muted uz-small">No questions yet</span>}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{num(m.today_credits)} of {m.daily_cap == null ? '∞' : num(m.daily_cap)}</td>
                        <td>
                          <label className="uz-sr" htmlFor={`uz-lim-${m.user_id}`}>Daily limit for {m.email}</label>
                          <div>
                            <select id={`uz-lim-${m.user_id}`} className={`uz-select${String(v.cap) !== String(m.cap_override ?? '') ? ' is-changed' : ''}`}
                              style={{ minWidth: 190 }} value={custom ? String(v.cap) : preset} onChange={(e) => setRow(m, { cap: e.target.value })}>
                              <option value="">Plan default · {m.default_cap == null ? 'no limit' : limitLabel(m.default_cap, typical)}</option>
                              {PRESETS.map((q) => <option key={q} value={String(q * typical)}>{q} questions / day</option>)}
                              {custom && <option value={String(v.cap)}>Custom · {num(v.cap)} credits / day</option>}
                            </select>
                          </div>
                          <div className="uz-muted uz-small" style={{ marginTop: 4 }}>
                            {capCredits == null ? 'no daily limit' : `${num(capCredits)} credits`}
                            {String(v.cap) !== String(m.cap_override ?? '') && (
                              <span style={{ color: 'var(--warning-ink)' }}> · was {m.cap_override == null ? 'default' : limitLabel(m.cap_override, typical)}</span>
                            )}
                          </div>
                        </td>
                        <td>
                          <label className="uz-switch">
                            <input type="checkbox" role="switch" checked={!v.off} onChange={(e) => setRow(m, { off: !e.target.checked })} />
                            <span>{v.off ? 'Off' : 'On'}</span>
                          </label>
                          {v.off !== m.disabled && <div className="uz-small" style={{ color: 'var(--warning-ink)' }}>was {m.disabled ? 'off' : 'on'}</div>}
                        </td>
                        <td className="is-right">
                          <button type="button" className="uz-btn is-ghost" aria-expanded={open === m.user_id}
                            onClick={() => setOpen(open === m.user_id ? null : m.user_id)}
                            title={open === m.user_id ? 'Hide their questions' : 'Show their questions this month'}>
                            <IconChevron style={{ transform: open === m.user_id ? 'rotate(90deg)' : 'none', transition: 'transform 120ms' }} />
                            <span className="uz-sr">Questions of {m.email}</span>
                          </button>
                        </td>
                      </tr>
                      {open === m.user_id && <PersonActivity userId={m.user_id} token={token} colSpan={6} />}
                    </React.Fragment>
                  );
                })}
                {!shownMembers.length && <tr><td colSpan={6} className="uz-empty">Nobody matches.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
        <p className="uz-muted uz-small" style={{ margin: 0 }}>
          People who join during a month start adding to your allowance from the next month. Need a bigger allowance? Ask Accutax for a plan change or extra credits.
        </p>
      </section>

      {changed.length > 0 && (
        <div className="uz-savebar" role="region" aria-label="Unsaved changes">
          <span><b>{changed.length} unsaved change{changed.length === 1 ? '' : 's'}</b>
            <span className="uz-muted"> · {[limitChanges && `${limitChanges} limit${limitChanges === 1 ? '' : 's'}`,
              accessChanges && `${accessChanges} access`].filter(Boolean).join(', ')}</span></span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="uz-btn" onClick={() => setDraft({})} disabled={saving}>Discard</button>
            <button type="button" className="uz-btn is-primary" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
          </div>
        </div>
      )}
    </div>
  );
};
