import React, { useEffect, useRef, useState } from 'react';

// Compact "21.9k / 200k" style meter: how full the model's context window was on
// the latest answer (the agent keeps only the last few messages, so this is what
// the model holds, not a running total), against SESSION_CONTEXT_WINDOW_TOKENS
// on the backend — currently 200K. Deliberately NOT the selected model's real context window:
// that varies per model in this app's catalog (Claude 200K vs Gemini 1M), and
// the whole point of this meter is a number that stays stable across model
// switches, same as the account-level meter it's modeled after.
const formatTokens = (n) => {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(v % 1_000_000 === 0 ? 0 : 1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(v % 1_000 === 0 ? 0 : 1)}k`;
  return String(v);
};

const meterColor = (pct) =>
  pct >= 90 ? 'var(--danger, #dc2626)' : pct >= 70 ? 'var(--warning, #d97706)' : 'var(--accent, #0e8a75)';

// Small ring icon that itself shows the used fraction as an arc — the trigger
// button doubles as an always-visible mini-meter, same idea as the reference
// product's round context indicator next to the model picker.
const RingIcon = ({ percent, size = 20 }) => {
  const stroke = 2.5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const offset = c * (1 - Math.max(0, Math.min(100, percent)) / 100);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ display: 'block' }}>
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke="var(--border, rgba(255,255,255,0.16))" strokeWidth={stroke}
      />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke={meterColor(percent)} strokeWidth={stroke}
        strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: 'stroke-dashoffset 0.2s ease' }}
      />
    </svg>
  );
};

// Round trigger button at the bottom of the composer — click opens a small
// popover with just the context-window row (no plan/5-hour/weekly limits;
// this app has no such tiers, only the fixed per-session token budget).
export const ContextWindowButton = ({ contextWindow }) => {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onOutside = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) setOpen(false);
    };
    const onEscape = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onOutside);
    document.addEventListener('keydown', onEscape);
    return () => {
      document.removeEventListener('mousedown', onOutside);
      document.removeEventListener('keydown', onEscape);
    };
  }, [open]);

  if (!contextWindow || typeof contextWindow.limit !== 'number') return null;

  const { used = 0, limit, percent = 0 } = contextWindow;
  const pct = Math.max(0, Math.min(100, Number(percent) || 0));

  return (
    <div ref={wrapperRef} style={styles.wrapper}>
      <button
        type="button"
        style={styles.trigger}
        onClick={() => setOpen((v) => !v)}
        title={`Context window: ${used.toLocaleString()} / ${limit.toLocaleString()} tokens used (${pct}%)`}
        aria-expanded={open}
      >
        <RingIcon percent={pct} size={16} />
      </button>

      {open && (
        <div style={styles.popover}>
          <div style={styles.row}>
            <span style={styles.label}>Context window</span>
            <span style={styles.fraction}>{formatTokens(used)} / {formatTokens(limit)} ({pct}%)</span>
          </div>
          <div style={styles.track}>
            <div style={{ ...styles.fill, width: `${pct}%`, backgroundColor: meterColor(pct) }} />
          </div>
        </div>
      )}
    </div>
  );
};

const styles = {
  wrapper: {
    position: 'relative',
    display: 'flex',
    alignItems: 'center',
    flexShrink: 0,
  },
  trigger: {
    width: '18px',
    height: '18px',
    borderRadius: '50%',
    border: 'none',
    background: 'transparent',
    padding: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    flexShrink: 0,
  },
  popover: {
    position: 'absolute',
    bottom: 'calc(100% + 8px)',
    right: 0,
    minWidth: '260px',
    padding: '10px 12px',
    borderRadius: 'var(--radius-md, 10px)',
    border: '1px solid var(--border, rgba(0,0,0,0.1))',
    background: 'var(--surface, #ffffff)',
    boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    zIndex: 50,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '10px',
  },
  label: {
    fontSize: '0.75rem',
    fontWeight: 500,
    color: 'var(--ink-faint, #8a968e)',
  },
  fraction: {
    fontSize: '0.75rem',
    fontWeight: 600,
    color: 'var(--ink-soft, #526059)',
    fontVariantNumeric: 'tabular-nums',
  },
  track: {
    width: '100%',
    height: '4px',
    borderRadius: '2px',
    background: 'var(--border, rgba(0,0,0,0.08))',
    overflow: 'hidden',
  },
  fill: {
    height: '100%',
    borderRadius: '2px',
    transition: 'width 0.2s ease, background-color 0.2s ease',
  },
};
