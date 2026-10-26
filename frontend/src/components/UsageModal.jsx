import React, { useCallback, useEffect, useState } from 'react';
import { Gauge, X, RefreshCw } from 'lucide-react';
import {
  fetchOwnerUsage, setMemberCap, fetchUsageAdmin, fetchUsageAdminOwner, setOwnerPlan, addOwnerTopup, searchUsageOwners,
  fetchMemberUsage, fetchMyUsage,
} from '../services/api';
import { shortDate } from './UsageMeter';

// AI usage pages (docs/ORG_TOKEN_LIMITS_STRATEGY.md).
// "My organizations": an owner's monthly AI bucket, who used it, and each person's daily limit (owners can lower or
// raise it, or turn AI off for one person). "Accutax admin": spend today, every owner's bucket, plans and top-ups.
// 1 credit = $0.001; a typical question is about 20 credits.

const STATE_LABEL = { ok: 'OK', warn: 'Running low', lite: 'Lite model', blocked: 'Used up' };
const num = (v, d = 0) => (v == null ? '—' : Number(v).toLocaleString(undefined, { maximumFractionDigits: d }));

const Bar = ({ percent, state }) => {
  const color = state === 'blocked' ? 'var(--danger)' : state === 'lite' || state === 'warn' ? 'var(--warning, #C8932E)' : 'var(--success)';
  return (
    <div style={styles.track}>
      <div style={{ ...styles.fill, width: `${Math.min(100, percent || 0)}%`, backgroundColor: color }} />
    </div>
  );
};

const BucketSummary = ({ bucket, typical }) => (
  <div style={styles.cards}>
    <div style={styles.card}>
      <div style={styles.cardLabel}>This month</div>
      <div style={styles.cardValue}>{bucket.bucket == null ? 'No limit' : `${bucket.percent}% used`}</div>
      {bucket.bucket != null && <Bar percent={bucket.percent} state={bucket.state} />}
    </div>
    <div style={styles.card}>
      <div style={styles.cardLabel}>Credits used / available</div>
      <div style={styles.cardValue}>{num(bucket.used)} / {bucket.bucket == null ? '∞' : num(bucket.bucket)}</div>
      <div style={styles.cardSub}>about {num(bucket.bucket == null ? null : (bucket.bucket - bucket.used) / typical)} questions left</div>
    </div>
    <div style={styles.card}>
      <div style={styles.cardLabel}>Plan</div>
      <div style={styles.cardValue}>{bucket.plan_label}</div>
      <div style={styles.cardSub}>
        {bucket.seats} seat{bucket.seats === 1 ? '' : 's'}
        {bucket.seats_counted > bucket.seats ? ` (plan max ${bucket.max_seats})` : ''}
        {bucket.topups ? ` · +${num(bucket.topups)} top-up` : ''}
      </div>
    </div>
    <div style={styles.card}>
      <div style={styles.cardLabel}>Status</div>
      <div style={styles.cardValue}>{STATE_LABEL[bucket.state] || bucket.state}</div>
      <div style={styles.cardSub}>renews {shortDate(bucket.resets_at)}</div>
    </div>
  </div>
);

const KIND_LABEL = { agent_answer: 'Question', document_read: 'File read', privacy_demo: 'Privacy demo' };
// An event's status in words; "ok" and "ready" say nothing extra.
const STATUS_LABEL = { degraded: 'timed out', failed: 'failed', partial: 'some figures missing', error: 'error' };
const orgsLabel = (ids) => (ids.length > 3 ? `${ids.length} organizations` : `org ${ids.join(', ')}`);
const modelName = (id) => (/haiku/i.test(id) ? 'Haiku' : /sonnet/i.test(id) ? 'Sonnet' : /opus/i.test(id) ? 'Opus' : id);
const when = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime())
    ? d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
};

// One person's use of the bucket this month: totals and their newest questions and file reads.
// No question text: the usage tables never keep it.
const PAGE = 10;

// Questions and file reads, newest first, 10 at a time. No question text: the usage tables never keep it.
const EventsTable = ({ events }) => {
  const [shown, setShown] = useState(PAGE);
  return (
    <>
      {events.length ? (
        <div style={styles.grid}>
          <div style={{ ...styles.eventRow, ...styles.head }}>
            <div>When</div><div>What</div><div>Model</div><div>Tokens in / out</div><div>Credits</div>
          </div>
          {events.slice(0, shown).map((e) => (
            <div key={e.id} style={styles.eventRow}>
              <div>{when(e.at)}</div>
              <div>{KIND_LABEL[e.kind] || e.kind}{STATUS_LABEL[e.status] ? <span style={styles.muted}> · {STATUS_LABEL[e.status]}</span> : null}
                <div style={styles.muted} title={e.organization_ids.join(', ')}>{orgsLabel(e.organization_ids)}</div></div>
              <div>{e.models.map(modelName).join(', ') || '—'}{e.mode === 'lite' ? <span style={styles.muted}> · lite</span> : null}
                <div style={styles.muted}>{e.calls} call{e.calls === 1 ? '' : 's'}</div></div>
              <div>{num(e.input_tokens)} / {num(e.output_tokens)}{e.cached_tokens ? <div style={styles.muted}>+ {num(e.cached_tokens)} cached</div> : null}</div>
              <div>{num(e.credits, 1)}</div>
            </div>
          ))}
        </div>
      ) : <div style={styles.muted}>No AI use this month.</div>}
      {events.length > PAGE && (
        <div style={styles.moreRow}>
          <span style={styles.muted}>
            Showing {Math.min(shown, events.length)} of {events.length}{events.length >= 50 ? ' newest' : ''}
          </span>
          {shown < events.length && <button style={styles.btn} onClick={() => setShown(shown + PAGE)}>Show {PAGE} more</button>}
          {shown > PAGE && <button style={styles.btn} onClick={() => setShown(PAGE)}>Show fewer</button>}
        </div>
      )}
    </>
  );
};

const TotalsCards = ({ t }) => (
  <div style={styles.cards}>
    <div style={styles.card}><div style={styles.cardLabel}>Questions and files</div><div style={styles.cardValue}>{num(t.events)}</div>
      <div style={styles.cardSub}>{t.lite_events ? `${t.lite_events} on the lite model` : 'all on the full model'}</div></div>
    <div style={styles.card}><div style={styles.cardLabel}>Tokens in / out</div>
      <div style={styles.cardValue}>{num(t.input_tokens)} / {num(t.output_tokens)}</div>
      <div style={styles.cardSub}>+ {num(t.cached_tokens)} cached</div></div>
    <div style={styles.card}><div style={styles.cardLabel}>Credits</div><div style={styles.cardValue}>{num(t.credits, 1)}</div>
      <div style={styles.cardSub}>${num(t.cost_usd, 4)}</div></div>
  </div>
);

const MemberUsage = ({ userId, ownerId, token }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    fetchMemberUsage(userId, ownerId, token).then(setData).catch((e) => setError(e.message));
  }, [userId, ownerId, token]);
  if (error) return <div style={styles.memberDetail}><div style={styles.error}>{error}</div></div>;
  if (!data) return <div style={styles.memberDetail}><div style={styles.muted}>Loading…</div></div>;
  return (
    <div style={styles.memberDetail}>
      <TotalsCards t={data.totals} />
      <EventsTable events={data.events} />
    </div>
  );
};

// Every role's own page: what I used this month and today, my limit in each bucket, how full the bucket is.
const MyUsageView = ({ token }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { fetchMyUsage(token).then(setData).catch((e) => setError(e.message)); }, [token]);
  if (error) return <div style={styles.error}>{error}</div>;
  if (!data) return <div style={styles.empty}>Loading…</div>;
  return (
    <div style={styles.scroll}>
      <p style={styles.note}>
        {data.internal
          ? 'Your AI use is Accutax\'s own and has no limit.'
          : 'The organizations you work in share their owner\'s monthly AI. Your daily limit is set by that owner. '}
        {' '}1 credit = $0.001; a typical question is about {data.typical_question_credits} credits.
      </p>
      {data.buckets.map((b) => (
        <div key={b.owner_user_id}>
          <div style={styles.section}>
            {b.you_own_it ? 'Your organizations' : `Organizations of ${b.owner_email || `owner ${b.owner_user_id}`}`}
          </div>
          <div style={styles.cards}>
            <div style={styles.card}>
              <div style={styles.cardLabel}>Organizations' AI this month</div>
              <div style={styles.cardValue}>{b.percent == null ? 'No limit' : `${b.percent}% used`}</div>
              {b.percent != null && <Bar percent={b.percent} state={b.state} />}
              <div style={styles.cardSub}>{STATE_LABEL[b.state] || b.state} · renews {shortDate(b.resets_at)}</div>
            </div>
            <div style={styles.card}>
              <div style={styles.cardLabel}>You today</div>
              <div style={styles.cardValue}>
                {b.disabled ? 'AI off for you' : b.daily_cap == null ? `${num(b.used_today, 1)} credits` : `${num(b.used_today, 1)} of ${num(b.daily_cap)}`}
              </div>
              {!b.disabled && b.daily_cap != null && <Bar percent={b.today_percent} state={b.today_percent >= 100 ? 'blocked' : b.today_percent >= 80 ? 'warn' : 'ok'} />}
              <div style={styles.cardSub}>
                {b.disabled ? 'the owner turned it off' : b.daily_cap == null ? 'no daily limit' : `≈ ${num(Math.max(0, b.daily_cap - b.used_today) / data.typical_question_credits)} questions left today`}
              </div>
            </div>
            <div style={styles.card}>
              <div style={styles.cardLabel}>You this month</div>
              <div style={styles.cardValue}>{num(b.month_credits, 1)} credits</div>
              <div style={styles.cardSub}>{num(b.questions)} question{b.questions === 1 ? '' : 's'} and files · {b.plan_label}</div>
            </div>
          </div>
        </div>
      ))}
      <div style={styles.section}>All my AI use this month</div>
      <TotalsCards t={data.totals} />
      <EventsTable events={data.events} />
    </div>
  );
};

// One person's row: their use and an editable daily limit. `onSave(userId, credits|null, disabled)`.
// Clicking the person opens their usage this month underneath.
const MemberRow = ({ m, typical, onSave, editable, ownerId, token, open, onToggle }) => {
  const [cap, setCap] = useState(m.cap_override ?? '');
  const [off, setOff] = useState(m.disabled);
  const [saving, setSaving] = useState(false);
  const dirty = String(cap) !== String(m.cap_override ?? '') || off !== m.disabled;
  const save = async () => {
    setSaving(true);
    try { await onSave(m.user_id, cap === '' ? null : Number(cap), off); } finally { setSaving(false); }
  };
  return (
    <>
    <div style={{ ...styles.row, ...(open ? styles.rowOpen : {}) }}>
      <button style={styles.personBtn} onClick={onToggle} aria-expanded={open}
        title={open ? 'Hide usage' : "Show this person's usage this month"}>
        <div style={styles.email}>{open ? '▾' : '▸'} {m.email || `User ${m.user_id}`}</div>
        <div style={styles.muted}>{m.role}{m.invitation && m.invitation !== 'ACTIVE' ? ` · ${m.invitation.toLowerCase()}` : ''}</div>
      </button>
      <div style={styles.cell}>{num(m.month_credits)}<div style={styles.muted}>{m.questions} q</div></div>
      <div style={styles.cell}>{num(m.today_credits)}<div style={styles.muted}>of {m.daily_cap == null ? '∞' : num(m.daily_cap)}</div></div>
      <div style={styles.cell}>
        {editable ? (
          <>
            <input
              type="number" min="0" step="50" value={cap} placeholder={`default ${m.default_cap == null ? '∞' : num(m.default_cap)}`}
              onChange={(e) => setCap(e.target.value)} style={styles.input} aria-label={`Daily limit for ${m.email || m.user_id}`}
            />
            <div style={styles.muted}>{cap === '' ? 'plan default' : `≈ ${num(Number(cap) / typical)} questions a day`}</div>
          </>
        ) : <span>{m.cap_override == null ? 'default' : num(m.cap_override)}</span>}
      </div>
      <div style={styles.cell}>
        {editable ? (
          <label style={styles.toggle}>
            <input type="checkbox" checked={!off} onChange={(e) => setOff(!e.target.checked)} /> AI on
          </label>
        ) : <span>{m.disabled ? 'off' : 'on'}</span>}
      </div>
      <div style={styles.cell}>
        {editable && <button style={styles.btn} disabled={!dirty || saving} onClick={save}>{saving ? 'Saving…' : 'Save'}</button>}
      </div>
    </div>
    {open && <MemberUsage userId={m.user_id} ownerId={ownerId} token={token} />}
    </>
  );
};

// `ownerId`: the bucket's owner for the admin page; null on the owner's own page. `focus`: a person to open at once.
const MembersTable = ({ members, typical, onSave, editable = true, ownerId = null, token = '', focus = null }) => {
  const [open, setOpen] = useState(focus);
  useEffect(() => { setOpen(focus); }, [focus]);
  return (
    <div style={styles.grid}>
      <div style={{ ...styles.row, ...styles.head }}>
        <div style={styles.cellMain}>Person</div><div style={styles.cell}>This month</div><div style={styles.cell}>Today</div>
        <div style={styles.cell}>Daily limit</div><div style={styles.cell}>Access</div><div style={styles.cell} />
      </div>
      {members.map((m) => (
        <MemberRow key={`${m.user_id}-${m.cap_override}-${m.disabled}`} m={m} typical={typical} onSave={onSave}
          editable={editable} ownerId={ownerId} token={token} open={open === m.user_id}
          onToggle={() => setOpen(open === m.user_id ? null : m.user_id)} />
      ))}
      {!members.length && <div style={styles.empty}>Nobody has used AI this month.</div>}
    </div>
  );
};

const OwnerView = ({ token }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    setError('');
    fetchOwnerUsage(token).then(setData).catch((e) => setError(e.message));
  }, [token]);
  useEffect(load, [load]);
  const save = async (userId, credits, disabled) => {
    try { await setMemberCap(userId, credits, disabled, token); load(); } catch (e) { setError(e.message); }
  };
  if (error) return <div style={styles.error}>{error}</div>;
  if (!data) return <div style={styles.empty}>Loading…</div>;
  return (
    <div style={styles.scroll}>
      <p style={styles.note}>
        One monthly AI allowance covers all your organizations. When it is used up, answers come from a lighter model
        for a little longer, then stop until it renews. Each person who was on your team when the month began adds to
        it (people who join count from next month). A daily limit stops one person from using it all; leave it empty
        for your plan's default. 1 credit = $0.001; a typical question is about {data.typical_question_credits} credits.
      </p>
      <BucketSummary bucket={data.bucket} typical={data.typical_question_credits} />
      <div style={styles.section}>People</div>
      <MembersTable members={data.members} typical={data.typical_question_credits} onSave={save} token={token} />
    </div>
  );
};

const OwnerAdmin = ({ ownerId, plans, token, onChanged, focus }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState('standard');
  const [monthly, setMonthly] = useState('');
  const [daily, setDaily] = useState('');
  const [topup, setTopup] = useState('');
  const [reason, setReason] = useState('');
  const load = useCallback(() => {
    setError('');
    fetchUsageAdminOwner(ownerId, token).then((d) => {
      setData(d);
      setPlan(d.plan_row?.plan || 'standard');
      setMonthly(d.plan_row?.monthly_credits ?? '');
      setDaily(d.plan_row?.user_daily_credits ?? '');
    }).catch((e) => setError(e.message));
  }, [ownerId, token]);
  useEffect(load, [load]);
  const savePlan = async () => {
    try {
      await setOwnerPlan(ownerId, plan, monthly === '' ? null : Number(monthly), daily === '' ? null : Number(daily), token);
      load(); onChanged();
    } catch (e) { setError(e.message); }
  };
  const giveTopup = async () => {
    try { await addOwnerTopup(ownerId, Number(topup), reason, 365, token); setTopup(''); setReason(''); load(); onChanged(); }
    catch (e) { setError(e.message); }
  };
  if (error) return <div style={styles.error}>{error}</div>;
  if (!data) return <div style={styles.empty}>Loading…</div>;
  return (
    <div>
      <div style={styles.section}>{data.email || `Owner ${ownerId}`}</div>
      <BucketSummary bucket={data.bucket} typical={20} />
      <div style={styles.form}>
        <label style={styles.field}>Plan
          <select value={plan} onChange={(e) => setPlan(e.target.value)} style={styles.input}>
            {plans.map((p) => <option key={p.key} value={p.key}>{p.label}{p.per_seat ? ` · ${num(p.per_seat)}/seat, max ${p.max_seats}` : ''}</option>)}
          </select>
        </label>
        <label style={styles.field}>Monthly credits (override)
          <input type="number" min="0" value={monthly} placeholder="plan × seats" onChange={(e) => setMonthly(e.target.value)} style={styles.input} />
        </label>
        <label style={styles.field}>Default daily limit
          <input type="number" min="0" value={daily} placeholder="plan default" onChange={(e) => setDaily(e.target.value)} style={styles.input} />
        </label>
        <button style={styles.btn} onClick={savePlan}>Save plan</button>
      </div>
      <div style={styles.form}>
        <label style={styles.field}>Top-up credits
          <input type="number" min="1" value={topup} onChange={(e) => setTopup(e.target.value)} style={styles.input} />
        </label>
        <label style={{ ...styles.field, flex: 2 }}>Reason
          <input value={reason} onChange={(e) => setReason(e.target.value)} style={styles.input} placeholder="e.g. pilot, goodwill" />
        </label>
        <button style={styles.btn} disabled={!(Number(topup) > 0)} onClick={giveTopup}>Add (valid 1 year)</button>
      </div>
      {!!data.topups?.length && (
        <div style={styles.muted}>Top-ups: {data.topups.map((t) => `${num(t.credits)} (${t.reason || '—'}, until ${shortDate(t.expires_at) || 'never'})`).join(' · ')}</div>
      )}
      <div style={styles.section}>People</div>
      <MembersTable members={data.members} typical={20} editable={false} onSave={() => {}} ownerId={ownerId}
        token={token} focus={focus} />
    </div>
  );
};

const modelShort = (id) => (/haiku/i.test(id) ? 'Haiku' : /sonnet/i.test(id) ? 'Sonnet' : /opus/i.test(id) ? 'Opus' : id);
const KIND_PLURAL = { agent_answer: 'Questions', document_read: 'File reads', privacy_demo: 'Privacy demos',
  health_check: 'Health checks', unscoped: 'Other (no owner)' };

// Spend per day for the last 14 days: one series, so one color and no legend; each bar has its own tooltip.
const DailySpend = ({ days, timezone }) => {
  const byDay = Object.fromEntries(days.map((d) => [d.day, d]));
  const end = new Date();
  const series = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(end);
    d.setDate(end.getDate() - (13 - i));
    const key = d.toLocaleDateString('en-CA', { timeZone: timezone });
    return { key, label: d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', timeZone: timezone }),
      cost: byDay[key]?.cost_usd || 0, questions: byDay[key]?.questions || 0 };
  });
  const max = Math.max(...series.map((d) => d.cost), 0.01);
  const total = series.reduce((a, d) => a + d.cost, 0);
  const [hover, setHover] = useState(null);
  return (
    <div>
      <div style={styles.chart} role="group" onMouseLeave={() => setHover(null)}
        aria-label={`AI spend per day, last 14 days: ${series.map((d) => `${d.label} $${d.cost.toFixed(2)}`).join(', ')}`}>
        {series.map((d, i) => (
          <div key={d.key} className="usage-bar-slot" style={styles.barSlot} tabIndex={0}
            aria-label={`${d.label}: $${d.cost.toFixed(2)}, ${d.questions} questions`}
            onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}>
            <div style={{ ...styles.bar, height: `${Math.max(d.cost > 0 ? 3 : 0, (d.cost / max) * 100)}%`,
              opacity: hover == null || hover === i ? 1 : 0.35 }} />
            {hover === i && (
              // The same card as an uploaded bill's (attach-pop): a pill, then the figures; its edge stays inside the chart.
              <span role="tooltip" className={`usage-pop${i < 3 ? ' at-start' : i > 10 ? ' at-end' : ''}`}>
                <span className="usage-pop-pill">{d.label}</span>
                <span className="usage-pop-row"><span>Spend</span><b>${d.cost.toFixed(2)}</b></span>
                <span className="usage-pop-row"><span>Questions</span><b>{d.questions}</b></span>
                <span className="usage-pop-row"><span>Share of 14 days</span><b>{total ? Math.round((100 * d.cost) / total) : 0}%</b></span>
              </span>
            )}
          </div>
        ))}
      </div>
      <div style={styles.chartAxis}><span>{series[0].label}</span><span>max ${max.toFixed(2)} a day</span><span>{series[13].label}</span></div>
    </div>
  );
};

const Overview = ({ data, onPickPerson }) => {
  const v = data.overview;
  const q = v.kinds.find((k) => k.kind === 'agent_answer') || { events: 0, credits: 0, lite: 0, not_ok: 0 };
  const totalModel = v.models.reduce((a, m) => a + m.cost_usd, 0) || 1;
  return (
    <div style={styles.overview}>
      <div style={styles.section}>This month</div>
      <div style={styles.cards}>
        <div style={styles.card}><div style={styles.cardLabel}>Questions</div><div style={styles.cardValue}>{num(q.events)}</div>
          <div style={styles.cardSub}>{num(q.lite)} on the lite model</div></div>
        <div style={styles.card}><div style={styles.cardLabel}>Average question</div>
          <div style={styles.cardValue}>{num(q.events ? q.credits / q.events : 0, 1)} credits</div>
          <div style={styles.cardSub}>${num(q.events ? q.credits / q.events / 1000 : 0, 4)}</div></div>
        <div style={styles.card}><div style={styles.cardLabel}>Not fully answered</div><div style={styles.cardValue}>{num(q.not_ok)}</div>
          <div style={styles.cardSub}>timed out, failed or partial</div></div>
        <div style={styles.card}><div style={styles.cardLabel}>Owners using AI</div><div style={styles.cardValue}>{num(data.owners.length)}</div>
          <div style={styles.cardSub}>{num(data.owners.reduce((a, o) => a + (o.users || 0), 0))} people</div></div>
      </div>

      <div style={styles.section}>Spend per day, last 14 days</div>
      <DailySpend days={v.by_day} timezone={data.timezone} />

      <div style={styles.section}>Most active people</div>
      <div style={styles.grid}>
        <div style={{ ...styles.topRow, ...styles.head }}><div>Person</div><div>Owner</div><div>Questions</div><div>Credits</div></div>
        {v.top_people.map((t) => (
          <button key={`${t.user_id}-${t.owner_user_id}`} style={{ ...styles.topRow, ...styles.rowBtn }}
            onClick={() => onPickPerson(t)} title="Open this person's usage">
            <span style={styles.email}>{t.email || `User ${t.user_id}`}</span>
            <span style={{ ...styles.email, ...styles.muted }}>{t.owner_email || `Owner ${t.owner_user_id}`}</span>
            <span>{num(t.events)}</span><span>{num(t.credits, 1)}</span>
          </button>
        ))}
        {!v.top_people.length && <div style={styles.empty}>No AI use this month yet.</div>}
      </div>

      <div style={styles.twoCol}>
        <div>
          <div style={styles.section}>By model</div>
          {v.models.map((m) => (
            <div key={m.model} style={styles.statRow}>
              <span>{modelShort(m.model)}<span style={styles.muted}> · {num(m.calls)} calls</span></span>
              <span>${num(m.cost_usd, 2)}<span style={styles.muted}> · {Math.round((100 * m.cost_usd) / totalModel)}%</span></span>
            </div>
          ))}
          {!v.models.length && <div style={styles.muted}>None.</div>}
        </div>
        <div>
          <div style={styles.section}>By type</div>
          {v.kinds.map((k) => (
            <div key={k.kind} style={styles.statRow}>
              <span>{KIND_PLURAL[k.kind] || k.kind}<span style={styles.muted}> · {num(k.events)}</span></span>
              <span>{num(k.credits, 0)} credits</span>
            </div>
          ))}
          {!v.kinds.length && <div style={styles.muted}>None.</div>}
        </div>
      </div>
    </div>
  );
};

const AdminView = ({ token }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  // The owner on the right, the card that was clicked, and the person to open there (a collaborator from the search).
  const [selected, setSelected] = useState(null);
  const [picked, setPicked] = useState(null);
  const [focus, setFocus] = useState(null);
  const pick = (key, ownerId, person = null) => { setPicked(key); setSelected(ownerId); setFocus(person); };
  const [query, setQuery] = useState('');
  const [found, setFound] = useState(null);
  const load = useCallback(() => {
    setError('');
    fetchUsageAdmin(token).then(setData).catch((e) => setError(e.message));
  }, [token]);
  useEffect(load, [load]);
  // Any owner, also one who has not used AI yet, by part of the email or the user id.
  useEffect(() => {
    const text = query.trim();
    if (!text) { setFound(null); return undefined; }
    const timer = setTimeout(() => {
      searchUsageOwners(text, token).then(setFound).catch((e) => setError(e.message));
    }, 300);
    return () => clearTimeout(timer);
  }, [query, token]);
  if (error) return <div style={styles.error}>{error}</div>;
  if (!data) return <div style={styles.empty}>Loading…</div>;
  const p = data.platform;
  return (
    <div style={styles.body}>
      <div style={styles.list}>
        <div style={styles.cards}>
          <div style={styles.card}><div style={styles.cardLabel}>Spend today (UTC)</div>
            <div style={styles.cardValue}>${num(p.today_usd, 2)}</div>
            <div style={styles.cardSub}>alert ${num(p.alert_usd)} · pause ${num(p.pause_usd)}</div></div>
          <div style={styles.card}><div style={styles.cardLabel}>This month</div>
            <div style={styles.cardValue}>${num(p.month_usd, 2)}</div>
            <div style={styles.cardSub}>internal {num(data.internal_month_credits)} credits · limits {data.enforce}</div></div>
        </div>
        <input value={query} onChange={(e) => setQuery(e.target.value)} style={styles.input}
          placeholder="Find an owner or a person by email or user id" aria-label="Find an owner or a person" />
        {found && (found.owners.length + found.collaborators.length > 0) && (
          <div style={styles.mutedSmall}>
            {found.owners.length + found.collaborators.length} match{found.owners.length + found.collaborators.length === 1 ? '' : 'es'}
            {found.owners.length + found.collaborators.length > 5 ? ' · scroll for more' : ''}
          </div>
        )}
        {found && (
          <div style={styles.results}>
            {found.owners.map((o) => (
              <button key={`o${o.owner_user_id}`} onClick={() => pick(`o${o.owner_user_id}`, o.owner_user_id)}
                style={{ ...styles.item, ...(picked === `o${o.owner_user_id}` ? styles.itemActive : {}) }}>
                <span style={styles.email}>{o.email || `Owner ${o.owner_user_id}`}</span>
                <span style={styles.muted}>owner · user {o.owner_user_id} · {o.organizations} organization{o.organizations === 1 ? '' : 's'}</span>
              </button>
            ))}
            {found.collaborators.map((c) => (
              <button key={`c${c.email}-${c.owner_user_id}`}
                onClick={() => pick(`c${c.email}-${c.owner_user_id}`, c.owner_user_id, c.user_id)}
                style={{ ...styles.item, ...(picked === `c${c.email}-${c.owner_user_id}` ? styles.itemActive : {}) }}>
                <span style={styles.email}>{c.email}</span>
                <span style={styles.muted}>
                  collaborator{c.status === 'INVITED' ? ' (invited)' : ''}{c.user_id ? ` · user ${c.user_id}` : ''} · owner {c.owner_email}
                </span>
              </button>
            ))}
            {!found.owners.length && !found.collaborators.length && <div style={styles.muted}>Nobody matches.</div>}
          </div>
        )}
        <div style={styles.section}>Owners this month</div>
        {data.owners.map((o) => (
          <button key={o.owner_user_id} onClick={() => pick(`m${o.owner_user_id}`, o.owner_user_id)}
            style={{ ...styles.item, ...(picked === `m${o.owner_user_id}` ? styles.itemActive : {}) }}>
            <div style={styles.itemTop}>
              <span style={styles.email}>{o.email || `Owner ${o.owner_user_id}`}</span>
              <span>{o.percent == null ? '∞' : `${o.percent}%`}</span>
            </div>
            {o.bucket != null && <Bar percent={o.percent} state={o.state} />}
            <div style={styles.muted}>{o.plan_label} · {num(o.used)} / {o.bucket == null ? '∞' : num(o.bucket)} · {o.users} people · {STATE_LABEL[o.state]}</div>
            {!!o.people?.length && <div style={styles.muted}>{o.people.join(', ')}</div>}
          </button>
        ))}
        {!data.owners.length && <div style={styles.empty}>No AI use this month yet.</div>}
        <div style={styles.section}>Alerts</div>
        {data.alerts.map((a, i) => (
          <div key={i} style={styles.muted}>{shortDate(a.created_at)} · owner {a.owner_user_id || 'platform'} · {a.level} · {a.cycle}</div>
        ))}
        {!data.alerts.length && <div style={styles.muted}>None.</div>}
      </div>
      <div style={styles.detail}>
        {selected
          ? <OwnerAdmin ownerId={selected} plans={data.plans} token={token} onChanged={load} focus={focus} />
          : (
            <>
              <div style={styles.hint}>Pick an owner or a person on the left to change a plan, give a top-up or see their usage.</div>
              <Overview data={data} onPickPerson={(t) => pick(`t${t.user_id}-${t.owner_user_id}`, t.owner_user_id, t.user_id)} />
            </>
          )}
      </div>
    </div>
  );
};

const TABS = [
  { key: 'me', label: 'My usage', show: () => true },
  { key: 'owner', label: 'My organizations', show: (u) => u?.is_owner },
  { key: 'admin', label: 'Accutax admin', show: (u) => u?.is_admin },
];

export const UsageModal = ({ token, usage, onClose }) => {
  const tabs = TABS.filter((t) => t.show(usage));
  const [view, setView] = useState(usage?.is_owner ? 'owner' : usage?.is_admin ? 'admin' : 'me');
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.titleGroup}>
            <Gauge size={22} color="var(--accent)" />
            <div>
              <h3 style={{ margin: 0 }}>AI usage</h3>
              <p style={styles.subtitle}>Monthly AI allowance per account owner, and each person's daily limit.</p>
            </div>
          </div>
          <div style={styles.headerRight}>
            {tabs.length > 1 && (
              <div style={styles.tabs} role="tablist">
                {tabs.map((t) => (
                  <button key={t.key} role="tab" aria-selected={view === t.key} onClick={() => setView(t.key)}
                    style={{ ...styles.tab, ...(view === t.key ? styles.tabActive : {}) }}>{t.label}</button>
                ))}
              </div>
            )}
            <button style={styles.closeBtn} onClick={onClose} aria-label="Close"><X size={18} /></button>
          </div>
        </div>
        {view === 'me' && <MyUsageView token={token} />}
        {view === 'owner' && usage?.is_owner && <OwnerView token={token} />}
        {view === 'admin' && usage?.is_admin && <AdminView token={token} />}
      </div>
    </div>
  );
};

const styles = {
  modal: { maxWidth: '96vw', width: '1100px', height: '86vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap', marginBottom: 12 },
  titleGroup: { display: 'flex', gap: 10, alignItems: 'flex-start' },
  subtitle: { margin: '2px 0 0', fontSize: 12, color: 'var(--ink-soft)' },
  headerRight: { display: 'flex', gap: 8, alignItems: 'center' },
  closeBtn: { background: 'none', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--ink-soft)' },
  tabs: { display: 'flex', gap: 4 },
  tab: { borderWidth: 1, borderStyle: 'solid', borderColor: 'var(--border)', background: 'var(--surface)', borderRadius: 99, padding: '4px 12px', cursor: 'pointer', fontSize: 12.5, color: 'var(--ink)' },
  tabActive: { background: 'var(--accent-soft)', borderColor: 'var(--accent)', color: 'var(--accent-ink)', fontWeight: 600 },
  error: { background: 'var(--danger-soft)', color: 'var(--danger-ink)', padding: '8px 12px', borderRadius: 8, fontSize: 13 },
  empty: { color: 'var(--ink-soft)', fontSize: 13, padding: 12 },
  note: { fontSize: 12.5, color: 'var(--ink-soft)', margin: '0 0 10px', lineHeight: 1.5 },
  scroll: { overflowY: 'auto', flex: 1, minHeight: 0 },
  body: { display: 'flex', gap: 16, flex: 1, minHeight: 0 },
  list: { flex: '0 0 320px', minHeight: 0, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 8 },
  detail: { flex: 1, minWidth: 0, minHeight: 0, overflowY: 'auto', paddingRight: 4 },
  item: { textAlign: 'left', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 10px', cursor: 'pointer', color: 'var(--ink)', font: 'inherit', display: 'flex', flexDirection: 'column', gap: 4 },
  itemActive: { borderColor: 'var(--accent)', background: 'var(--accent-soft)' },
  itemTop: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, fontWeight: 600, gap: 8 },
  cards: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, marginBottom: 6 },
  card: { background: 'var(--surface-2)', borderRadius: 8, padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4 },
  cardLabel: { fontSize: 11, color: 'var(--ink-soft)' },
  cardValue: { fontSize: 14, fontWeight: 600, color: 'var(--ink)' },
  cardSub: { fontSize: 11.5, color: 'var(--ink-soft)' },
  track: { height: 6, borderRadius: 99, background: 'var(--border-soft)', overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 99 },
  section: { fontSize: 13, fontWeight: 700, margin: '14px 0 6px', color: 'var(--ink)' },
  grid: { border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' },
  row: { display: 'grid', gridTemplateColumns: 'minmax(0,2fr) minmax(0,.8fr) minmax(0,.8fr) minmax(0,1.3fr) minmax(0,.6fr) minmax(0,.6fr)', gap: 8, padding: '8px 10px', borderTop: '1px solid var(--border-soft)', alignItems: 'center', fontSize: 13 },
  head: { background: 'var(--surface-2)', fontSize: 11, color: 'var(--ink-soft)', textTransform: 'uppercase', letterSpacing: '.04em', borderTop: 'none' },
  cellMain: { minWidth: 0 },
  results: { display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 268, overflowY: 'auto', flexShrink: 0, paddingRight: 2 },
  mutedSmall: { fontSize: 11.5, color: 'var(--ink-soft)' },
  hint: { fontSize: 12.5, color: 'var(--ink-soft)', marginBottom: 4 },
  overview: { display: 'flex', flexDirection: 'column' },
  chart: { display: 'flex', alignItems: 'flex-end', gap: 2, height: 120, padding: '8px 0', borderBottom: '1px solid var(--border)' },
  barSlot: { flex: 1, height: '100%', display: 'flex', alignItems: 'flex-end', cursor: 'default', position: 'relative' },
  bar: { width: '100%', background: 'var(--accent)', borderRadius: '4px 4px 0 0', transition: 'opacity 120ms ease' },
  chartAxis: { display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--ink-soft)', marginTop: 4 },
  topRow: { display: 'grid', gridTemplateColumns: 'minmax(0,1.4fr) minmax(0,1.4fr) minmax(0,.6fr) minmax(0,.6fr)', gap: 8, padding: '7px 10px', borderTop: '1px solid var(--border-soft)', alignItems: 'center', fontSize: 13 },
  rowBtn: { width: '100%', textAlign: 'left', background: 'none', border: 'none', borderTop: '1px solid var(--border-soft)', cursor: 'pointer', color: 'var(--ink)', font: 'inherit' },
  twoCol: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 },
  statRow: { display: 'flex', justifyContent: 'space-between', gap: 8, padding: '5px 0', borderBottom: '1px solid var(--border-soft)', fontSize: 13 },
  moreRow: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  rowOpen: { background: 'var(--accent-soft)' },
  personBtn: { minWidth: 0, textAlign: 'left', background: 'none', border: 'none', padding: 0, cursor: 'pointer', color: 'var(--ink)', font: 'inherit' },
  memberDetail: { padding: '10px 12px 12px', borderTop: '1px solid var(--border-soft)', background: 'var(--surface-2)', display: 'flex', flexDirection: 'column', gap: 8 },
  eventRow: { display: 'grid', gridTemplateColumns: 'minmax(0,1.1fr) minmax(0,1.1fr) minmax(0,1fr) minmax(0,1.2fr) minmax(0,.6fr)', gap: 8, padding: '6px 10px', borderTop: '1px solid var(--border-soft)', fontSize: 12.5, alignItems: 'start' },
  cell: { minWidth: 0 },
  email: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' },
  muted: { color: 'var(--ink-soft)', fontSize: 11.5 },
  input: { width: '100%', boxSizing: 'border-box', padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)', font: 'inherit', fontSize: 13 },
  toggle: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 12.5, cursor: 'pointer' },
  btn: { padding: '5px 12px', borderRadius: 99, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)', cursor: 'pointer', fontSize: 12.5, whiteSpace: 'nowrap' },
  form: { display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', margin: '10px 0' },
  field: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--ink-soft)', flex: 1, minWidth: 140 },
};
