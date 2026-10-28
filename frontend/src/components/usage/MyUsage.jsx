import React, { useEffect, useMemo, useState } from 'react';
import { fetchMyUsage } from '../../services/api';
import {
  Bar, ErrorBox, IconBulb, IconInfo, Loading, Pill, Segments, Stat, StatePill, forecastPercent, longDate, modelsLabel,
  num, questions, RESULT, shortDate, sizeOf, timeUntil, toneOfState, whenLabel, notOk, KIND,
} from './ui';

// "My usage": every role's own page. Today against the daily limit, how full the organizations' monthly AI is, this
// month's questions, and the newest questions with their size. No question text: the usage tables never keep it.

const scopeLabel = (ids, orgNames) => {
  if (!ids?.length) return '—';
  if (ids.length === 1) return orgNames[ids[0]] || `Organization ${ids[0]}`;
  const all = Object.keys(orgNames).length;
  return ids.length === all && all > 1 ? `All ${all} organizations` : `${ids.length} organizations`;
};

const TodayCard = ({ b, typical }) => {
  if (b.disabled) {
    return (
      <div className="uz-card is-bad">
        <div className="uz-section-head"><h3 className="uz-eyebrow">Today</h3><Pill tone="bad" dot>AI off</Pill></div>
        <div className="uz-big"><b>Off</b><span>for you</span></div>
        <p className="uz-muted" style={{ margin: 0 }}>The account owner has turned AI off for you in these organizations. Ask them to switch it back on.</p>
      </div>
    );
  }
  const cap = b.daily_cap;
  const used = Number(b.used_today || 0);
  if (cap == null) {
    return (
      <div className="uz-card">
        <div className="uz-section-head"><h3 className="uz-eyebrow">Today</h3><span className="uz-small uz-muted">No daily limit</span></div>
        <div className="uz-big"><b>{num(questions(used, typical))}</b><span>questions so far today</span></div>
        <p className="uz-muted uz-small" style={{ margin: 0 }}>{num(used, 1)} credits used. Only the monthly allowance applies.</p>
      </div>
    );
  }
  const totalQ = Math.max(1, Math.round(cap / typical));
  const usedQ = Math.min(totalQ, Math.ceil(used / typical - 0.0001));
  const leftQ = Math.max(0, questions(cap - used, typical));
  const pct = Math.min(100, (100 * used) / cap);
  const tone = used >= cap ? 'bad' : pct >= 80 ? 'warn' : 'good';
  return (
    <div className={`uz-card${tone === 'bad' ? ' is-bad' : ''}`}>
      <div className="uz-section-head">
        <h3 className="uz-eyebrow">Today</h3>
        <span className="uz-small uz-muted">Resets in {timeUntil(b.day_resets_at)}</span>
      </div>
      <div className="uz-big"><b>{num(leftQ)}</b><span>of ≈ {num(totalQ)} questions left</span></div>
      {totalQ <= 12
        ? <Segments total={totalQ} used={usedQ} tone={tone} label={`${usedQ} of ${totalQ} daily questions used`} />
        : <Bar pct={pct} tone={tone} label={`${Math.round(pct)}% of today's limit used`} />}
      <div className="uz-section-head" style={{ alignItems: 'center' }}>
        <span className="uz-muted uz-small">
          {num(used, 1)} of {num(cap)} credits · {b.you_own_it ? 'your own daily limit' : 'daily limit set by your organization owner'}
        </span>
        {!b.you_own_it && b.owner_email && (
          <a className="uz-btn" href={`mailto:${b.owner_email}?subject=${encodeURIComponent('More AI for today, please')}`}>Ask owner for more</a>
        )}
      </div>
    </div>
  );
};

const AllowanceCard = ({ b }) => {
  const forecast = forecastPercent(b.percent, b.resets_at);
  const runsOut = forecast != null && forecast > 100;
  return (
    <div className="uz-card">
      <div className="uz-section-head">
        <h3 className="uz-eyebrow">{b.you_own_it ? 'Your organizations\' allowance' : 'Organization allowance'}</h3>
        <StatePill state={b.state} />
      </div>
      {b.percent == null ? (
        <div className="uz-big"><b>No limit</b><span>this month</span></div>
      ) : (
        <>
          <div className="uz-big"><b>{b.percent}%</b><span>used this month</span></div>
          <Bar pct={b.percent} tone={toneOfState(b.state)} label={`${b.percent}% of the monthly allowance used`} />
        </>
      )}
      <p className="uz-muted uz-small" style={{ margin: 0 }}>
        {b.percent == null ? 'Shared by everyone in these organizations.'
          : runsOut ? <>At this pace it may run out before it renews on <b style={{ color: 'var(--ink)' }}>{shortDate(b.resets_at)}</b>. </>
            : <>On track to last until it renews on <b style={{ color: 'var(--ink)' }}>{shortDate(b.resets_at)}</b>. </>}
        {b.percent != null && `Shared by everyone in ${b.you_own_it ? 'your' : `${b.owner_email || 'the owner'}'s`} organizations.`}
      </p>
    </div>
  );
};

export const MyUsage = ({ token, orgNames = {} }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [org, setOrg] = useState('all');
  const [shown, setShown] = useState(8);
  useEffect(() => { fetchMyUsage(token).then(setData).catch((e) => setError(e.message)); }, [token]);

  const events = data?.events || [];
  const typical = data?.typical_question_credits || 20;
  const orgsSeen = useMemo(() => [...new Set(events.flatMap((e) => (e.organization_ids?.length === 1 ? e.organization_ids : [])))], [events]);
  const filtered = org === 'all' ? events : events.filter((e) => e.organization_ids?.length === 1 && String(e.organization_ids[0]) === org);
  // What a question over several organizations costs next to one inside a single organization.
  const ratio = useMemo(() => {
    const avg = (list) => (list.length ? list.reduce((a, e) => a + e.credits, 0) / list.length : 0);
    const q = events.filter((e) => e.kind === 'agent_answer' && e.status === 'ok' && e.mode === 'full');
    const one = avg(q.filter((e) => e.organization_ids?.length === 1));
    const many = avg(q.filter((e) => e.organization_ids?.length > 1));
    return one && many ? many / one : null;
  }, [events]);

  if (error) return <ErrorBox message={error} />;
  if (!data) return <Loading />;
  const t = data.totals;
  const questionsAsked = events.filter((e) => e.kind === 'agent_answer').length;
  return (
    <div className="uz-stack">
      {data.internal && (
        <div className="uz-callout"><IconInfo /><span>Your AI use is Accutax's own and has no limit. It is still listed below.</span></div>
      )}
      {data.buckets.map((b) => (
        <section key={b.owner_user_id} aria-label="Allowance" className="uz-stack" style={{ gap: 10 }}>
          {data.buckets.length > 1 && (
            <h3 className="uz-h-sm">{b.you_own_it ? 'Your organizations' : `Organizations of ${b.owner_email || `owner ${b.owner_user_id}`}`}</h3>
          )}
          <div className="uz-grid" style={{ '--min': '300px', gap: 16 }}>
            <TodayCard b={b} typical={typical} />
            <AllowanceCard b={b} />
          </div>
        </section>
      ))}

      <details className="uz-details">
        <summary><IconInfo />What happens when a limit is reached?</summary>
        <div className="uz-details-body">
          <p style={{ margin: 0 }}><b>Your daily limit:</b> the assistant pauses for you until midnight. Others on your team are not affected.</p>
          <p style={{ margin: 0 }}><b>The organization allowance:</b> answers come from a lighter, faster model for a short while, then pause for everyone until it renews.</p>
          <p style={{ margin: 0 }}><b>A question that is stopped by a limit</b> is never sent to the AI and costs nothing.</p>
        </div>
      </details>

      <section aria-labelledby="uz-month" className="uz-stack" style={{ gap: 14 }}>
        <div className="uz-section-head">
          <h3 id="uz-month" className="uz-h">This month</h3>
          <span className="uz-muted uz-small">{data.buckets[0] ? `Renews ${longDate(data.buckets[0].resets_at)}` : ''}</span>
        </div>
        <div className="uz-grid" style={{ '--min': '170px' }}>
          <Stat label="Questions asked" value={num(questionsAsked)} sub={t.events > questionsAsked ? `+ ${t.events - questionsAsked} file reads` : null} />
          <Stat label="Answered by the lighter model" value={num(t.lite_events)} />
          <Stat label="Not fully answered" value={num(t.not_ok)} tone={t.not_ok ? 'warn' : ''} sub="timed out, failed or partial" />
          <Stat label="Credits used" value={num(t.credits, 1)} sub={`≈ ${num(questions(t.credits, typical))} typical questions`} />
        </div>
        {ratio && ratio > 1.4 && (
          <div className="uz-callout">
            <IconBulb />
            <span><b>Tip:</b> your questions across several organizations cost about {ratio.toFixed(1)}× more than one asked
              inside a single organization. Pick one organization first to stretch your daily limit.</span>
          </div>
        )}
      </section>

      <section aria-labelledby="uz-recent" className="uz-stack" style={{ gap: 14 }}>
        <div className="uz-section-head" style={{ alignItems: 'center' }}>
          <h3 id="uz-recent" className="uz-h">Recent questions</h3>
          {orgsSeen.length > 1 && (
            <label className="uz-small uz-muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>Show
              <select className="uz-select" style={{ width: 'auto' }} value={org} onChange={(e) => { setOrg(e.target.value); setShown(8); }}>
                <option value="all">All organizations</option>
                {orgsSeen.map((o) => <option key={o} value={String(o)}>{orgNames[o] || `Organization ${o}`}</option>)}
              </select>
            </label>
          )}
        </div>
        <div className="uz-card is-flush">
          {filtered.length ? (
            <div className="uz-table-wrap">
              <table className="uz-table" style={{ minWidth: 640 }}>
                <thead><tr><th>Asked in</th><th>When</th><th>Model</th><th className="is-right">Size</th></tr></thead>
                <tbody>
                  {filtered.slice(0, shown).map((e) => {
                    const size = sizeOf(e.credits, typical);
                    return (
                      <tr key={e.id}>
                        <td>
                          <div>{scopeLabel(e.organization_ids, orgNames)}</div>
                          <div className="uz-muted uz-small">{KIND[e.kind] || e.kind}
                            {notOk(e.status) && <span style={{ color: 'var(--danger-ink)' }}> · {(RESULT[e.status] || {}).label || e.status}</span>}
                          </div>
                        </td>
                        <td className="uz-muted" style={{ whiteSpace: 'nowrap' }}>{whenLabel(e.at)}</td>
                        <td>{modelsLabel(e)}{e.mode === 'lite' && <span className="uz-muted"> · lite</span>}</td>
                        <td className="is-right"><Pill tone={size.tone}>{size.label}</Pill></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <div className="uz-empty">No questions this month yet.</div>}
          {filtered.length > shown && (
            <div className="uz-table-foot">
              <button type="button" className="uz-btn is-ghost is-wide" onClick={() => setShown(filtered.length)}>
                Show all {filtered.length} {filtered.length >= 50 ? 'newest ' : ''}questions
              </button>
            </div>
          )}
        </div>
      </section>

      <details className="uz-plain-details" style={{ borderTop: '1px solid var(--border)', paddingTop: 6 }}>
        <summary>Technical details (tokens and credits)</summary>
        <div className="uz-grid uz-mono" style={{ '--min': '190px', paddingTop: 8, fontSize: 13 }}>
          <div><div className="uz-muted uz-small">Tokens in / out</div>{num(t.input_tokens)} / {num(t.output_tokens)}</div>
          <div><div className="uz-muted uz-small">Cached tokens</div>{num(t.cached_tokens)}</div>
          <div><div className="uz-muted uz-small">Credits used</div>{num(t.credits, 1)} (${num(t.cost_usd, 4)})</div>
          <div><div className="uz-muted uz-small">1 question ≈</div>{typical} credits</div>
        </div>
      </details>
    </div>
  );
};
