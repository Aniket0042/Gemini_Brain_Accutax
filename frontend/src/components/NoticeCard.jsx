import React from 'react';
import { 
  Inbox, BarChart3, Lock, AlertTriangle, Zap, 
  RotateCw, ArrowRight 
} from 'lucide-react';

export const NoticeCard = ({ notice, onRetry, onSuggestionClick }) => {
  if (!notice) return null;

  const { kind = 'degraded', title, message, suggestions = [], retryable = false } = notice;

  const getKindConfig = () => {
    switch (kind) {
      case 'empty':
        return { icon: <Inbox size={14} />, label: 'No records', color: 'var(--ink-faint)' };
      case 'partial':
        return { icon: <BarChart3 size={14} />, label: 'Partial data', color: 'var(--info)' };
      case 'denied':
        return { icon: <Lock size={14} />, label: 'Restricted', color: 'var(--danger)' };
      case 'failed':
        return { icon: <AlertTriangle size={14} />, label: 'Interrupted', color: 'var(--warning-ink, #9C6B1F)' };
      case 'degraded':
      default:
        return { icon: <Zap size={14} />, label: 'Degraded mode', color: 'var(--warning-ink, #9C6B1F)' };
    }
  };

  const config = getKindConfig();

  return (
    <div style={styles.wrap}>
      <div style={styles.header}>
        <span style={{ ...styles.badge, color: config.color }}>{config.icon} {config.label}</span>
      </div>

      {title && <div style={styles.title}>{title}</div>}
      {message && <div style={styles.message}>{message}</div>}

      {(suggestions.length > 0 || (retryable && onRetry)) && (
        <div style={styles.actions}>
          {suggestions.map((suggestion, idx) => (
            <button
              key={idx}
              style={styles.actionChip}
              onClick={() => onSuggestionClick && onSuggestionClick(suggestion)}
            >
              <span>{suggestion}</span>
              <ArrowRight size={11} style={{ opacity: 0.5 }} />
            </button>
          ))}
          {retryable && onRetry && (
            <button style={styles.actionChip} onClick={onRetry}>
              <RotateCw size={12} />
              <span>Retry</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
};

const styles = {
  wrap: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    padding: '10px 14px',
    borderRadius: 'var(--radius-md)',
    backgroundColor: 'var(--surface-2)',
    fontSize: 'var(--text-sm)',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: 'var(--text-xs)',
    fontWeight: 600,
    letterSpacing: '0.02em',
    textTransform: 'uppercase',
  },
  title: {
    fontSize: 'var(--text-ui)',
    fontWeight: 600,
    color: 'var(--ink)',
    lineHeight: 1.4,
  },
  message: {
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-soft)',
    lineHeight: 1.5,
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '6px',
    marginTop: '4px',
  },
  actionChip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '5px',
    padding: '4px 10px',
    fontSize: 'var(--text-xs)',
    fontWeight: 500,
    color: 'var(--ink-soft)',
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border-soft)',
    borderRadius: 'var(--radius-pill)',
    cursor: 'pointer',
    transition: 'border-color var(--dur-fast) var(--ease), background var(--dur-fast) var(--ease)',
    fontFamily: 'inherit',
  },
};
