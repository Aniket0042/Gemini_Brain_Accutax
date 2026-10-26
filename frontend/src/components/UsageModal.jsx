import React, { useCallback, useEffect, useState } from 'react';
import { Gauge, X, RefreshCw } from 'lucide-react';
import {
  fetchOwnerUsage, setMemberCap, fetchUsageAdmin, fetchUsageAdminOwner, setOwnerPlan, addOwnerTopup, searchUsageOwners,
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

// One person's row: their use and an editable daily limit. `onSave(userId, credits|null, disabled)`.
const MemberRow = ({ m, typical, onSave, editable }) => {
  const [cap, setCap] = useState(m.cap_override ?? '');
  const [off, setOff] = useState(m.disabled);
  const [saving, setSaving] = useState(false);
  const dirty = String(cap) !== String(m.cap_override ?? '') || off !== m.disabled;
  const save = async () => {
    setSaving(true);
    try { await onSave(m.user_id, cap === '' ? null : Number(cap), off); } finally { setSaving(false); }
  };
  return (
    <div style={styles.row}>
      <div style={styles.cellMain}>
        <div style={styles.email}>{m.email || `User ${m.user_id}`}</div>
        <div style={styles.muted}>{m.role}{m.invitation && m.invitation !== 'ACTIVE' ? ` · ${m.invitation.toLowerCase()}` : ''}</div>
      </div>
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
  );
};

const MembersTable = ({ members, typical, onSave, editable = true }) => (
  <div style={styles.grid}>
    <div style={{ ...styles.row, ...styles.head }}>
      <div style={styles.cellMain}>Person</div><div style={styles.cell}>This month</div><div style={styles.cell}>Today</div>
      <div style={styles.cell}>Daily limit</div><div style={styles.cell}>Access</div><div style={styles.cell} />
    </div>
    {members.map((m) => <MemberRow key={`${m.user_id}-${m.cap_override}-${m.disabled}`} m={m} typical={typical} onSave={onSave} editable={editable} />)}
    {!members.length && <div style={styles.empty}>Nobody has used AI this month.</div>}
  </div>
);

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
      <MembersTable members={data.members} typical={data.typical_question_credits} onSave={save} />
    </div>
  );
};

const OwnerAdmin = ({ ownerId, plans, token, onChanged }) => {
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
      <MembersTable members={data.members} typical={20} editable={false} onSave={() => {}} />
    </div>
  );
};

const AdminView = ({ token }) => {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(null);
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
        {found && (
          <>
            {found.owners.map((o) => (
              <button key={`o${o.owner_user_id}`} onClick={() => setSelected(o.owner_user_id)}
                style={{ ...styles.item, ...(selected === o.owner_user_id ? styles.itemActive : {}) }}>
                <span style={styles.email}>{o.email || `Owner ${o.owner_user_id}`}</span>
                <span style={styles.muted}>owner · user {o.owner_user_id} · {o.organizations} organization{o.organizations === 1 ? '' : 's'}</span>
              </button>
            ))}
            {found.collaborators.map((c) => (
              <button key={`c${c.email}-${c.owner_user_id}`} onClick={() => setSelected(c.owner_user_id)}
                style={{ ...styles.item, ...(selected === c.owner_user_id ? styles.itemActive : {}) }}>
                <span style={styles.email}>{c.email}</span>
                <span style={styles.muted}>
                  collaborator{c.status === 'INVITED' ? ' (invited)' : ''}{c.user_id ? ` · user ${c.user_id}` : ''} · owner {c.owner_email}
                </span>
              </button>
            ))}
            {!found.owners.length && !found.collaborators.length && <div style={styles.muted}>Nobody matches.</div>}
          </>
        )}
        <div style={styles.section}>Owners this month</div>
        {data.owners.map((o) => (
          <button key={o.owner_user_id} onClick={() => setSelected(o.owner_user_id)}
            style={{ ...styles.item, ...(selected === o.owner_user_id ? styles.itemActive : {}) }}>
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
          ? <OwnerAdmin ownerId={selected} plans={data.plans} token={token} onChanged={load} />
          : <div style={styles.empty}>Pick an owner to change their plan or give a top-up.</div>}
      </div>
    </div>
  );
};

export const UsageModal = ({ token, usage, onClose }) => {
  const [view, setView] = useState(usage?.is_owner ? 'owner' : 'admin');
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
            {usage?.is_owner && usage?.is_admin && (
              <div style={styles.tabs} role="tablist">
                <button role="tab" aria-selected={view === 'owner'} onClick={() => setView('owner')}
                  style={{ ...styles.tab, ...(view === 'owner' ? styles.tabActive : {}) }}>My organizations</button>
                <button role="tab" aria-selected={view === 'admin'} onClick={() => setView('admin')}
                  style={{ ...styles.tab, ...(view === 'admin' ? styles.tabActive : {}) }}>Accutax admin</button>
              </div>
            )}
            <button style={styles.closeBtn} onClick={onClose} aria-label="Close"><X size={18} /></button>
          </div>
        </div>
        {view === 'owner' && usage?.is_owner && <OwnerView token={token} />}
        {view === 'admin' && usage?.is_admin && <AdminView token={token} />}
      </div>
    </div>
  );
};

const styles = {
  modal: { maxWidth: '96vw', width: '1100px', height: '86vh', display: 'flex', flexDirection: 'column' },
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
  body: { display: 'flex', gap: 12, flex: 1, minHeight: 0, flexWrap: 'wrap' },
  list: { flex: '1 1 300px', maxWidth: 380, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 6, paddingRight: 8 },
  detail: { flex: '2 1 420px', minWidth: 0, overflowY: 'auto' },
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
  cell: { minWidth: 0 },
  email: { fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' },
  muted: { color: 'var(--ink-soft)', fontSize: 11.5 },
  input: { width: '100%', boxSizing: 'border-box', padding: '5px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)', font: 'inherit', fontSize: 13 },
  toggle: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 12.5, cursor: 'pointer' },
  btn: { padding: '5px 12px', borderRadius: 99, border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--ink)', cursor: 'pointer', fontSize: 12.5, whiteSpace: 'nowrap' },
  form: { display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap', margin: '10px 0' },
  field: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11.5, color: 'var(--ink-soft)', flex: 1, minWidth: 140 },
};
