import React from 'react';

/**
 * Card — the one card shape used everywhere: same padding, radius and border
 * weight regardless of what's inside. What varies is which slots are filled
 * and the `tone` accent, not the underlying container.
 *
 * Slots: eyebrow (small caps label), icon, title, value (a big figure —
 * pairs with a kpi-style card), delta (a trend line under value), footer.
 * Anything else goes in `children`, rendered below the slots.
 *
 * `tone` picks the left accent + eyebrow color: 'neutral' (default),
 * 'accent', 'warn', 'danger'. It does not change the card's own background —
 * see the design-system plan's "spend the accent on the domain, not the
 * chrome" note: a tone is a thin signal, not a wash.
 */
const TONE_COLOR = {
  neutral: 'var(--ink-faint)',
  accent: 'var(--accent-ink)',
  info: 'var(--info)',
  warn: 'var(--warn, #9C6B1F)',
  danger: 'var(--danger)',
};

export function Card({
  eyebrow,
  icon,
  title,
  value,
  delta,
  footer,
  tone = 'neutral',
  interactive = false,
  onClick,
  children,
  style,
}) {
  const toneColor = TONE_COLOR[tone] || TONE_COLOR.neutral;
  const Tag = interactive ? 'button' : 'div';

  return (
    <Tag
      className={interactive ? 'card-surface card-interactive' : 'card-surface'}
      onClick={onClick}
      style={{
        ...styles.base,
        ...(tone !== 'neutral' ? { borderLeft: `3px solid ${toneColor}` } : {}),
        ...(interactive ? styles.interactive : {}),
        ...style,
      }}
    >
      {(eyebrow || icon) && (
        <div style={styles.head}>
          {icon && <span style={styles.icon}>{icon}</span>}
          {eyebrow && <span style={{ ...styles.eyebrow, color: toneColor }}>{eyebrow}</span>}
        </div>
      )}
      {title && <div style={styles.title}>{title}</div>}
      {value !== undefined && <div style={styles.value}>{value}</div>}
      {delta && <div style={{ ...styles.delta, color: toneColor }}>{delta}</div>}
      {children}
      {footer && <div style={styles.footer}>{footer}</div>}
    </Tag>
  );
}

const styles = {
  // No border: the card reads as a surface sitting on the page (--surface on
  // --bg is already visible contrast in both themes) plus a soft shadow for
  // lift, not a boxed outline. A toned card's left accent stripe is the only
  // border-like mark it carries, and it means something (the tone), rather
  // than being decoration repeated on every card regardless of content.
  base: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    padding: '14px 16px',
    borderRadius: 'var(--radius-md)',
    // Explicit 'none', not just omitted: the interactive variant renders a
    // native <button>, which carries its own default border the browser
    // will show unless something explicitly overrides it — leaving this key
    // out entirely (rather than set to 'none') was why the border kept
    // showing on every suggestion card even after "removing" it here.
    border: 'none',
    backgroundColor: 'var(--surface)',
    boxShadow: 'var(--shadow-sm)',
    textAlign: 'left',
    width: '100%',
    boxSizing: 'border-box',
    font: 'inherit',
  },
  interactive: {
    cursor: 'pointer',
    font: 'inherit',
    color: 'inherit',
    transition: 'border-color var(--dur-fast) var(--ease), background-color var(--dur-fast) var(--ease)',
  },
  head: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    marginBottom: '2px',
  },
  icon: { display: 'flex', alignItems: 'center', flexShrink: 0 },
  eyebrow: {
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: 'var(--text-xs)',
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
    fontWeight: 600,
  },
  title: {
    fontSize: 'var(--text-ui)',
    fontWeight: 600,
    color: 'var(--ink)',
    lineHeight: 1.3,
  },
  value: {
    fontSize: '19px',
    fontWeight: 700,
    color: 'var(--ink)',
    lineHeight: 1.2,
  },
  delta: {
    fontSize: 'var(--text-xs)',
    fontWeight: 500,
  },
  footer: {
    marginTop: '4px',
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-soft)',
  },
};
