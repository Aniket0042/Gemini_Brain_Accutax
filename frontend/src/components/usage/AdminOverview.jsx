import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchUsageAdmin, searchUsageOwners } from '../../services/api';
import {
  Bar, ErrorBox, IconChevron, IconSearch, IconStop, IconTrend, IconWarn, Loading, Stat, StatePill, TYPICAL,
  modelName, num, shortDate, toneOfState, usd, whenLabel,
} from './ui';

// The Accutax admin's overview: spend today and this month with a forecast, what needs attention, spend per day split
// into owners and no owner, spend by model, and every owner with their allowance. Search finds owners and people.

const ALERT_TEXT = {
  50: 'Half of the month\'s allowance used', 80: '80% of the month\'s allowance used', 100: 'Monthly allowance used up',
  lite_end: 'Lite model grace used up, AI paused', platform_alert: 'Platform spend passed the daily alert',
  platform_pause: 'Platform spend hit the daily pause',
};
export const alertText = (a) => {
  if (!String(a.level).startsWith('spike:')) return ALERT_TEXT[a.level] || a.level;
  const today = Number(a.detail?.today || 0);
  const usual = Number(a.detail?.usual_day || 0);
  // No usual day yet (a new account): say how much, not a meaningless multiple.
  return usual >= 1 ? `Usage spike · ${Math.round(today / usual)}× a usual day` : `Usage spike · ${num(today)} credits in one day`;
};

const KIND_PLURAL = { agent_answer: 'Questions', document_read: 'File reads', privacy_demo: 'Privacy demos',
  health_check: 'Health checks', unscoped: 'Other (no owner)' };
const MODEL_COLORS = ['var(--info, #6aa6ff)', 'var(--success)', 'var(--violet, #b48ae6)', 'var(--warning)'];

// Owners and people by email or user id; picking one opens that owner (and the person, for a collaborator).
const Search = ({ token, onOpenOwner }) => {
  const [query, setQuery] = useState('');
  const [found, setFound] = useState(null);
  const input = useRef(null);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === '/' && !/^(input|textarea|select)$/i.test(e.target.tagName)) { e.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    const text = query.trim();
    if (!text) { setFound(null); return undefined; }
    const t = setTimeout(() => searchUsageOwners(text, token).then(setFound).catch(() => setFound({ owners: [], collaborators: [] })), 250);
    return () => clearTimeout(t);
  }, [query, token]);
  const total = found ? found.owners.length + found.collaborators.length : 0;
  return (
    <div style={{ position: 'relative' }}>
      <label className="uz-search">
        <IconSearch /><span className="uz-sr">Find an owner or a person</span>
        <input ref={input} type="search" placeholder="Find owner or person by email or ID" value={query}
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && setQuery('')} />
        <span className="uz-kbd" aria-hidden="true">/</span>
      </label>
      {found && (
        <div className="uz-search-pop">
          <div className="uz-muted uz-small" style={{ padding: '2px 4px' }}>{total ? `${total} match${total === 1 ? '' : 'es'}${total > 5 ? ' · scroll for more' : ''}` : 'Nobody matches.'}</div>
          <div className="uz-results">
            {found.owners.map((o) => (
              <button key={`o${o.owner_user_id}`} type="button" className="uz-result" onClick={() => { setQuery(''); onOpenOwner(o.owner_user_id); }}>
                <b style={{ fontWeight: 600 }}>{o.email || `Owner ${o.owner_user_id}`}</b>
                <span className="uz-muted uz-small">owner · user {o.owner_user_id} · {o.organizations} organization{o.organizations === 1 ? '' : 's'}</span>
              </button>
            ))}
            {found.collaborators.map((c) => (
              <button key={`c${c.email}-${c.owner_user_id}`} type="button" className="uz-result"
                onClick={() => { setQuery(''); onOpenOwner(c.owner_user_id, c.user_id); }}>
                <b style={{ fontWeight: 600 }}>{c.email}</b>
                <span className="uz-muted uz-small">collaborator{c.status === 'INVITED' ? ' (invited)' : ''}{c.user_id ? ` · user ${c.user_id}` : ''} · owner {c.owner_email}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// Spend per day for 14 days, owners and no owner stacked; a card over the hovered day (the bill card's look).
const SpendChart = ({ days, timezone }) => {
  const [hover, setHover] = useState(null);
  const byDay = Object.fromEntries(days.map((d) => [d.day, d]));
  const series = Array.from({ length: 14 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (13 - i));
    const key = d.toLocaleDateString('en-CA', { timeZone: timezone });
    const row = byDay[key] || {};
    return { key, label: d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: timezone }),
      owners: row.owners_usd || 0, none: row.no_owner_usd || 0, questions: row.questions || 0 };
  });
  const peak = Math.max(...series.map((d) => d.owners + d.none), 0.01);
  const top = peak <= 1 ? Math.ceil(peak * 4) / 4 : Math.ceil(peak * 2) / 2;
  return (
    <div>
      <div className="uz-chart">
        <div className="uz-chart-y" aria-hidden="true"><span>{usd(top)}</span><span>{usd(top / 2)}</span><span>$0</span></div>
        <div className="uz-chart-plot" role="group" onMouseLeave={() => setHover(null)}
          aria-label={`AI spend per day, last 14 days: ${series.filter((d) => d.owners + d.none > 0).map((d) => `${d.label} ${usd(d.owners + d.none)}`).join(', ') || 'none'}`}>
          {series.map((d, i) => {
            const total = d.owners + d.none;
            return (
              <div key={d.key} className="uz-col" tabIndex={0} aria-label={`${d.label}: ${usd(total)}`}
                onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}>
                {total > 0 && (
                  <div className="uz-col-stack" style={{ height: `${Math.max(3, (100 * total) / top)}%`, opacity: hover == null || hover === i ? 1 : 0.4 }}>
                    {d.none > 0 && <div style={{ flex: d.none, background: 'var(--warning)' }} />}
                    {d.owners > 0 && <div style={{ flex: d.owners, background: 'var(--accent)' }} />}
                  </div>
                )}
                {hover === i && (
                  <span role="tooltip" className={`usage-pop${i < 3 ? ' at-start' : i > 10 ? ' at-end' : ''}`}>
                    <span className="usage-pop-pill">{d.label}</span>
                    <span className="usage-pop-row"><span>Owners</span><b>{usd(d.owners)}</b></span>
                    <span className="usage-pop-row"><span>No owner</span><b>{usd(d.none)}</b></span>
                    <span className="usage-pop-row"><span>Questions</span><b>{d.questions}</b></span>
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>
      <div className="uz-chart-x"><span>{series[0].label}</span><span>{series[7].label}</span><span>{series[13].label}</span></div>
    </div>
  );
};

const FILTERS = [
  ['all', 'All', () => true],
  ['over80', 'Over 80%', (o) => (o.percent ?? 0) >= 80],
  ['lite', 'Lite model', (o) => o.state === 'lite'],
  ['blocked', 'Used up', (o) => o.state === 'blocked'],
  ['idle', 'No use yet', (o) => !o.questions],
];

export const OwnersTable = ({ owners, onOpenOwner }) => {
  const [filter, setFilter] = useState('all');
  const pick = FILTERS.find((f) => f[0] === filter)[2];
  const rows = owners.filter(pick);
  return (
    <section aria-labelledby="uz-owners" className="uz-stack" style={{ gap: 12 }}>
      <div className="uz-section-head" style={{ alignItems: 'center' }}>
        <h3 id="uz-owners" className="uz-h">Owners this month</h3>
        <div role="group" aria-label="Filter owners" style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {FILTERS.map(([key, label, fn]) => (
            <button key={key} type="button" className="uz-chip" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {label} · {owners.filter(fn).length}
            </button>
          ))}
        </div>
      </div>
      <div className="uz-card is-flush">
        <div className="uz-table-wrap">
          <table className="uz-table" style={{ minWidth: 760 }}>
            <thead><tr><th>Owner</th><th>Plan</th><th style={{ width: 220 }}>Allowance used</th><th>People</th><th>Questions</th><th>Cost</th><th>Status</th><th aria-label="Open" /></tr></thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.owner_user_id} className="is-click" onClick={() => onOpenOwner(o.owner_user_id)}>
                  <td>
                    <button type="button" className="uz-link" style={{ color: 'var(--ink)' }} onClick={(e) => { e.stopPropagation(); onOpenOwner(o.owner_user_id); }}>
                      {o.email || `Owner ${o.owner_user_id}`}
                    </button>
                    <div className="uz-muted uz-small" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {(o.people || []).join(', ') || 'No use this month'}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{o.plan_label} · {o.seats} seat{o.seats === 1 ? '' : 's'}</td>
                  <td>
                    {o.bucket == null ? <span className="uz-muted">No limit</span> : (
                      <>
                        <div className="uz-nowrap" style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 13 }}><span>{o.percent}%</span><span className="uz-muted">{num(o.used)} / {num(o.bucket)}</span></div>
                        <div style={{ marginTop: 6 }}><Bar thin pct={o.percent} tone={toneOfState(o.state)} /></div>
                      </>
                    )}
                  </td>
                  <td>{num(o.users)}</td>
                  <td>{num(o.questions)}</td>
                  <td>{usd(o.used / 1000)}</td>
                  <td><StatePill state={o.state} /></td>
                  <td className="is-right"><IconChevron className="uz-muted" /></td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={8} className="uz-empty">No owner in this view.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
};

export const AlertsList = ({ alerts, owners, onOpenOwner }) => {
  const emails = Object.fromEntries(owners.map((o) => [o.owner_user_id, o.email]));
  return (
    <div className="uz-card is-flush">
      <ul className="uz-attn">
        {alerts.map((a, i) => {
          const spike = String(a.level).startsWith('spike:');
          const bad = ['100', 'lite_end', 'platform_pause'].includes(String(a.level));
          return (
            <li key={i}>
              {spike ? <IconTrend style={{ color: 'var(--warning)' }} /> : bad ? <IconStop style={{ color: 'var(--danger)' }} /> : <IconWarn style={{ color: 'var(--warning)' }} />}
              <div className="uz-stack" style={{ gap: 6, minWidth: 0 }}>
                <div><b>{alertText(a)}{a.owner_user_id ? ` · ${emails[a.owner_user_id] || `owner ${a.owner_user_id}`}` : ' · platform'}</b>
                  <div className="uz-muted uz-small">{whenLabel(a.created_at)} · {a.cycle}</div></div>
                {!!a.owner_user_id && <button type="button" className="uz-link uz-small" style={{ alignSelf: 'flex-start' }} onClick={() => onOpenOwner(a.owner_user_id)}>Open owner →</button>}
              </div>
            </li>
          );
        })}
        {!alerts.length && <li className="uz-muted">No alerts.</li>}
      </ul>
    </div>
  );
};

export const AdminOverview = ({ token, onOpenOwner }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState('overview');
  useEffect(() => { fetchUsageAdmin(token).then(setData).catch((e) => setError(e.message)); }, [token]);

  const monthAlerts = useMemo(() => {
    if (!data) return [];
    const month = new Date().toISOString().slice(0, 7);
    return data.alerts.filter((a) => String(a.cycle).startsWith(month));
  }, [data]);

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const v = data.overview;
  const p = data.platform;
  const q = v.kinds.find((k) => k.kind === 'agent_answer') || { events: 0, credits: 0, lite: 0, not_ok: 0 };
  const avg = q.events ? q.credits / q.events : 0;
  const now = new Date();
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  const forecast = p.month_usd / Math.max(1 / daysInMonth, (now.getDate() - 1 + now.getHours() / 24) / daysInMonth);
  const people = new Set(v.top_people.map((t) => t.user_id)).size;
  const att = v.attention;
  const noOwnerShare = att.all_credits ? (100 * att.no_owner_credits) / att.all_credits : 0;
  const attention = [];
  if (noOwnerShare >= 20) {
    attention.push({ icon: <IconWarn style={{ color: 'var(--warning)' }} />, title: `${Math.round(noOwnerShare)}% of spend has no owner`,
      text: `${num(att.no_owner_credits)} credits (${usd(att.no_owner_credits / 1000)}) across ${num(att.no_owner_events)} calls: model health checks, privacy demos, test runs and other AI use outside a chat. It is Accutax's own cost.` });
  }
  if (att.not_ok_events) {
    attention.push({ icon: <IconStop style={{ color: 'var(--danger)' }} />, title: `${att.not_ok_events} answer${att.not_ok_events === 1 ? '' : 's'} not fully delivered`,
      text: `Customers were charged ${num(att.not_ok_credits)} credits for them. Give the owners a goodwill top-up if they ask.`,
      action: ['Show owners', () => setTab('owners')] });
  }
  monthAlerts.filter((a) => String(a.level).startsWith('spike:') || ['80', '100', 'lite_end'].includes(String(a.level))).slice(0, 4)
    .forEach((a) => {
      const email = data.owners.find((o) => o.owner_user_id === a.owner_user_id)?.email || `owner ${a.owner_user_id}`;
      attention.push({ icon: <IconTrend style={{ color: 'var(--warning)' }} />, title: `${alertText(a)} · ${email}`,
        text: `${shortDate(a.created_at)}`, action: ['Open owner →', () => onOpenOwner(a.owner_user_id)] });
    });
  const modelTotal = v.models.reduce((a, m) => a + m.cost_usd, 0) || 1;

  return (
    <div className="uz-stack">
      <div className="uz-section-head" style={{ alignItems: 'center' }}>
        <div role="tablist" aria-label="Admin sections" style={{ display: 'flex', gap: 2 }}>
          {[['overview', 'Overview'], ['owners', 'Owners'], ['alerts', 'Alerts']].map(([key, label]) => (
            <button key={key} type="button" role="tab" className="uz-tab" aria-selected={tab === key} onClick={() => setTab(key)}>
              {label}{key === 'alerts' && monthAlerts.length > 0 && <span className="uz-count">{monthAlerts.length}</span>}
            </button>
          ))}
        </div>
        <Search token={token} onOpenOwner={onOpenOwner} />
      </div>

      {tab === 'overview' && (
        <>
          <section aria-label="Key numbers" className="uz-grid" style={{ '--min': '160px' }}>
            <div className="uz-stat">
              <div className="uz-stat-label">Spend today (UTC)</div>
              <div className="uz-stat-value">{usd(p.today_usd)}</div>
              <Bar thin pct={(100 * p.today_usd) / p.pause_usd} tone={p.today_usd >= p.alert_usd ? 'warn' : 'good'} marker={(100 * p.alert_usd) / p.pause_usd}
                label={`${usd(p.today_usd)} of the ${usd(p.pause_usd, 0)} daily pause`} />
              <div className="uz-stat-sub" style={{ display: 'flex', justifyContent: 'space-between' }}><span>Alert {usd(p.alert_usd, 0)}</span><span>Auto-pause {usd(p.pause_usd, 0)}</span></div>
            </div>
            <Stat label="Month to date" value={usd(p.month_usd)} sub={`Forecast ≈ ${usd(forecast)} by month end`} />
            <Stat label="Questions" value={num(q.events)} sub={`${people} people · ${data.owners.length} owner${data.owners.length === 1 ? '' : 's'} · ${q.lite} on lite model`} />
            <Stat label="Avg cost per question" value={num(avg)} unit="credits" tone={avg > TYPICAL * 1.5 ? 'warn' : ''}
              sub={avg > TYPICAL * 1.5 ? `${(avg / TYPICAL).toFixed(1)}× the ${TYPICAL}-credit planning figure` : `≈ ${usd(avg / 1000, 3)} each`}
              subTone={avg > TYPICAL * 1.5 ? 'warn' : ''} />
            <Stat label="Not fully answered" value={num(q.not_ok)} unit={q.events ? `of ${q.events} · ${Math.round((100 * q.not_ok) / q.events)}%` : ''}
              tone={q.not_ok ? 'bad' : ''} sub="Timed out, failed or partial" subTone={q.not_ok ? 'bad' : ''} />
          </section>

          <div className="uz-row">
            <section aria-labelledby="uz-attn" className="uz-card is-flush" style={{ flex: '1 1 340px' }}>
              <div className="uz-card-head"><h3 id="uz-attn" className="uz-h-sm">Needs attention</h3><span className="uz-muted uz-small">{attention.length} open</span></div>
              <ul className="uz-attn">
                {attention.map((a, i) => (
                  <li key={i}>
                    {a.icon}
                    <div className="uz-stack" style={{ gap: 6, minWidth: 0 }}>
                      <div><b>{a.title}</b><div className="uz-muted uz-small">{a.text}</div></div>
                      {a.action && <button type="button" className="uz-link uz-small" style={{ alignSelf: 'flex-start' }} onClick={a.action[1]}>{a.action[0]}</button>}
                    </div>
                  </li>
                ))}
                {!attention.length && <li className="uz-muted">Nothing needs attention.</li>}
              </ul>
            </section>

            <section aria-labelledby="uz-spend" className="uz-card" style={{ flex: '999 1 540px' }}>
              <div className="uz-section-head" style={{ alignItems: 'center' }}>
                <h3 id="uz-spend" className="uz-h-sm">Spend per day · last 14 days</h3>
                <div className="uz-legend">
                  <span><i className="uz-key" style={{ background: 'var(--accent)' }} />Owners</span>
                  <span><i className="uz-key" style={{ background: 'var(--warning)' }} />No owner</span>
                </div>
              </div>
              <SpendChart days={v.by_day} timezone={data.timezone} />
              <div className="uz-stack" style={{ gap: 10, borderTop: '1px solid var(--border)', paddingTop: 14 }}>
                <div className="uz-section-head">
                  <h4 className="uz-h-sm" style={{ fontSize: 14 }}>Spend by model</h4>
                  <span className="uz-muted uz-small">{num(v.models.reduce((a, m) => a + m.calls, 0))} calls · {usd(modelTotal)}</span>
                </div>
                <div className="uz-mix" role="img" aria-label={v.models.map((m) => `${modelName(m.model)} ${Math.round((100 * m.cost_usd) / modelTotal)}%`).join(', ')}>
                  {v.models.map((m, i) => <div key={m.model} style={{ flex: Math.max(m.cost_usd, modelTotal * 0.01), background: MODEL_COLORS[i % 4], minWidth: 3 }} />)}
                </div>
                <div className="uz-grid uz-small" style={{ '--min': '170px', gap: 8 }}>
                  {v.models.map((m, i) => (
                    <div key={m.model} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <i className="uz-key" style={{ background: MODEL_COLORS[i % 4], flex: 'none' }} />
                      {modelName(m.model)} · {num(m.calls)} calls · <b>{usd(m.cost_usd)}</b>
                    </div>
                  ))}
                </div>
                <div className="uz-legend" style={{ marginTop: 2 }}>
                  {v.kinds.map((k) => <span key={k.kind}>{KIND_PLURAL[k.kind] || k.kind}: <b style={{ color: 'var(--ink)' }}>&nbsp;{num(k.events)}</b>&nbsp;· {num(k.credits)} credits</span>)}
                </div>
              </div>
            </section>
          </div>

          <OwnersTable owners={data.owners} onOpenOwner={onOpenOwner} />

          {!!v.top_people.length && (
            <section aria-labelledby="uz-top" className="uz-stack" style={{ gap: 12 }}>
              <h3 id="uz-top" className="uz-h">Most active people</h3>
              <div className="uz-card is-flush">
                <div className="uz-table-wrap">
                  <table className="uz-table">
                    <thead><tr><th>Person</th><th>Owner</th><th className="is-right">Questions</th><th className="is-right">Credits</th></tr></thead>
                    <tbody>
                      {v.top_people.map((t) => (
                        <tr key={`${t.user_id}-${t.owner_user_id}`} className="is-click" onClick={() => t.owner_user_id && onOpenOwner(t.owner_user_id, t.user_id)}>
                          <td>{t.email || `User ${t.user_id}`}</td>
                          <td className="uz-muted">{t.owner_email || `Owner ${t.owner_user_id}`}</td>
                          <td className="is-right">{num(t.events)}</td>
                          <td className="is-right">{num(t.credits, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          )}
          <p className="uz-muted uz-small" style={{ margin: 0 }}>Limits are {data.enforce === 'hard' ? 'enforced' : `in ${data.enforce} mode`}. Accutax internal use this month: {num(data.internal_month_credits)} credits.</p>
        </>
      )}
      {tab === 'owners' && <OwnersTable owners={data.owners} onOpenOwner={onOpenOwner} />}
      {tab === 'alerts' && <AlertsList alerts={data.alerts} owners={data.owners} onOpenOwner={onOpenOwner} />}
    </div>
  );
};

