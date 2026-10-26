import React from 'react';
import { Gauge } from 'lucide-react';

// The header's AI usage button: how much of this month's AI the chosen organizations' bucket has used, and the
// person's own day. Percentages only; the owner and admin pages show credits. Hidden when usage tracking is off.

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

  const pct = owner?.percent;
  const width = pct == null ? 0 : Math.min(100, pct);
  const color = STATE_COLOR[owner?.state] || STATE_COLOR.ok;
  const mine = owner?.user?.percent;
  const title = usage.internal
    ? 'AI usage: Accutax internal, not limited'
    : owner
      ? `AI this month: ${pct == null ? 'no limit' : `${pct}% used`} · renews ${shortDate(owner.resets_at)}`
        + (mine != null ? ` · you today: ${mine}% of your daily limit` : '')
        + ' · click for details'
      : 'AI usage';

  return (
    <button
      className="icon-btn"
      style={styles.btn}
      onClick={onOpen}
      title={title}
      aria-label={title}
    >
      <Gauge size={14} color={color} />
      {owner && pct != null && (
        <>
          <span style={styles.track}><span style={{ ...styles.fill, width: `${width}%`, backgroundColor: color }} /></span>
          <span style={styles.label}>{pct}%</span>
        </>
      )}
      {owner && pct == null && <span style={styles.label}>No limit</span>}
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
