import React from 'react';
import { Gauge } from 'lucide-react';

// The header's AI usage button. An owner (of the chosen organizations' bucket) and an Accutax admin see how much of
// the month's AI the bucket has used; a collaborator sees their own day against the daily limit the owner set, coloured
// by the bucket too, so a month that runs low still shows. Percentages only. Hidden when usage tracking is off.

const STATE_COLOR = {
  ok: 'var(--success)',
  warn: 'var(--warning, #C8932E)',
  lite: 'var(--warning, #C8932E)',
  blocked: 'var(--danger)',
};

export const worstOwner = (usage) => {
  const rank = { ok: 0, warn: 1, lite: 2, blocked: 3 };
  return (usage?.owners || []).reduce(
    (worst, o) => (!worst || (rank[o.state] ?? 0) > (rank[worst.state] ?? 0) || (o.percent ?? 0) > (worst.percent ?? 0) ? o : worst),
    null,
  );
};

export const shortDate = (iso) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
};

export const UsageMeter = ({ usage, onOpen }) => {
  if (!usage || usage.enforce === 'off' || usage.enforce === 'log') return null;
  const owner = worstOwner(usage);
  // Everyone can open their own usage ("My usage"); owners and admins also get their own tabs there.
  if (!owner && !usage.is_owner && !usage.is_admin) return null;

  // A collaborator: someone who is neither an admin nor the owner of the bucket shown.
  const personal = !!owner && !usage.is_admin && !usage.internal && owner.owner_user_id !== usage.user_id;
  const mine = owner?.user?.percent;
  const off = personal && owner?.user?.disabled;
  const pct = personal ? mine : owner?.percent;
  const width = pct == null ? 0 : Math.min(100, pct);
  const personalState = off || (mine ?? 0) >= 100 ? 'blocked' : (mine ?? 0) >= 80 ? 'warn' : 'ok';
  const rank = { ok: 0, warn: 1, lite: 2, blocked: 3 };
  const state = personal && (rank[personalState] ?? 0) > (rank[owner.state] ?? 0) ? personalState : owner?.state;
  const color = STATE_COLOR[state] || STATE_COLOR.ok;
  const month = owner ? `${owner.percent == null ? 'no limit' : `${owner.percent}% used`}, renews ${shortDate(owner.resets_at)}` : '';
  const title = usage.internal
    ? 'AI usage: Accutax internal, not limited'
    : !owner
      ? 'AI usage'
      : personal
        ? (off ? 'The account owner has turned AI off for you'
          : `Your AI today: ${mine == null ? 'no daily limit' : `${mine}% of your daily limit`}, resets at midnight`)
          + ` · organizations' AI this month: ${month} · click for details`
        : `AI this month: ${month}${mine != null ? ` · you today: ${mine}% of your daily limit` : ''} · click for details`;

  return (
    <button
      className="icon-btn"
      style={styles.btn}
      onClick={onOpen}
      title={title}
      aria-label={title}
    >
      <Gauge size={14} color={color} />
      {owner && off && <span style={styles.label}>AI off</span>}
      {owner && !off && pct != null && (
        <>
          <span style={styles.track}><span style={{ ...styles.fill, width: `${width}%`, backgroundColor: color }} /></span>
          <span style={styles.label}>{pct}%</span>
        </>
      )}
      {owner && !off && pct == null && <span style={styles.label}>No limit</span>}
    </button>
  );
};

const styles = {
  btn: {
    display: 'flex', alignItems: 'center', gap: 6, height: 'var(--control-h-sm)', padding: '0 8px',
    background: 'none', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', flexShrink: 0, cursor: 'pointer',
  },
  track: { width: 46, height: 5, borderRadius: 99, background: 'var(--surface-2, var(--border-soft))', overflow: 'hidden', display: 'inline-block' },
  fill: { display: 'block', height: '100%', borderRadius: 99 },
  label: { fontSize: 11.5, fontWeight: 600, color: 'var(--ink-soft)', fontVariantNumeric: 'tabular-nums' },
};
