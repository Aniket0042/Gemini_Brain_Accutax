import React, { useState } from 'react';
import { 
  Inbox, BarChart3, Lock, AlertTriangle, Zap, 
  Copy, Check, Lightbulb, RotateCw 
} from 'lucide-react';

export const NoticeCard = ({ notice, onRetry, onSuggestionClick }) => {
  const [copied, setCopied] = useState(false);

  if (!notice) return null;

  const { kind = 'degraded', title, message, suggestions = [], retryable = false, request_id } = notice;

  const getKindConfig = () => {
    switch (kind) {
      case 'empty':
        return {
          icon: <Inbox size={16} color="#64748b" />,
          badge: 'Confirmed Zero Records',
          borderColor: '#e2e8f0',
          bgColor: '#f8fafc',
        };
      case 'partial':
        return {
          icon: <BarChart3 size={16} color="#3b82f6" />,
          badge: 'Partial View',
          borderColor: '#bfdbfe',
          bgColor: '#eff6ff',
        };
      case 'denied':
        return {
          icon: <Lock size={16} color="#ef4444" />,
          badge: 'Access Restricted',
          borderColor: '#fecaca',
          bgColor: '#fef2f2',
        };
      case 'failed':
        return {
          icon: <AlertTriangle size={16} color="#f59e0b" />,
          badge: 'Request Interrupted',
          borderColor: '#fed7aa',
          bgColor: '#fffbeb',
        };
      case 'degraded':
      default:
        return {
          icon: <Zap size={16} color="#f59e0b" />,
          badge: 'Degraded Mode',
          borderColor: '#fed7aa',
          bgColor: '#fffbeb',
        };
    }
  };

  const config = getKindConfig();

  const handleCopyRequestId = () => {
    if (request_id) {
      navigator.clipboard.writeText(request_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  return (
    <div style={{ ...styles.card, borderColor: config.borderColor, backgroundColor: config.bgColor }}>
      <div style={styles.header}>
        <div style={styles.titleRow}>
          <span style={styles.badge}>
            {config.icon}
            <span>{config.badge}</span>
          </span>
        </div>
        
        {title && <h4 style={styles.title}>{title}</h4>}
      </div>

      {message && <div style={styles.message}>{message}</div>}

      {suggestions && suggestions.length > 0 && (
        <div style={styles.suggestionsWrapper}>
          <span style={styles.suggestionsLabel}>Suggestions:</span>
          <div style={styles.pillsContainer}>
            {suggestions.map((suggestion, idx) => (
              <button
                key={idx}
                style={styles.suggestionPill}
                onClick={() => onSuggestionClick && onSuggestionClick(suggestion)}
                title="Click to run this query"
              >
                <Lightbulb size={13} color="#f59e0b" />
                <span>{suggestion}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {retryable && onRetry && (
        <div style={styles.actionsWrapper}>
          <button style={styles.retryBtn} onClick={onRetry}>
            <RotateCw size={13} color="#475569" />
            <span>Retry Query</span>
          </button>
        </div>
      )}
    </div>
  );
};

const styles = {
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
    padding: '16px 20px',
    borderRadius: '12px',
    border: '1px solid #e2e8f0',
    backgroundColor: '#f8fafc',
  },
  header: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  badge: {
    fontSize: '0.85rem',
    color: '#475569',
    fontWeight: 500,
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  title: {
    margin: 0,
    fontSize: '1rem',
    fontWeight: 700,
    color: '#0f172a',
    lineHeight: 1.3,
  },
  refRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '0.8rem',
    color: '#64748b',
  },
  refLabel: {
    color: '#64748b',
    fontWeight: 500,
  },
  refCode: {
    fontFamily: 'var(--font-mono)',
    cursor: 'pointer',
    color: '#334155',
    fontSize: '0.8rem',
    letterSpacing: '0.02em',
  },
  copyBtn: {
    background: 'none',
    border: '1px solid #cbd5e1',
    borderRadius: '4px',
    padding: '3px 5px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'background-color 0.2s',
  },
  message: {
    fontSize: '0.9rem',
    color: '#334155',
    lineHeight: 1.6,
  },
  suggestionsWrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    marginTop: '4px',
  },
  suggestionsLabel: {
    fontSize: '0.85rem',
    color: '#475569',
    fontWeight: 500,
  },
  pillsContainer: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: '8px',
  },
  suggestionPill: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 12px',
    backgroundColor: '#ffffff',
    border: '1px solid #cbd5e1',
    borderRadius: '20px',
    fontSize: '0.82rem',
    color: '#1e293b',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
    fontWeight: 500,
  },
  actionsWrapper: {
    marginTop: '8px',
  },
  retryBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '8px 14px',
    backgroundColor: '#ffffff',
    border: '1px solid #cbd5e1',
    borderRadius: '8px',
    fontSize: '0.82rem',
    color: '#334155',
    cursor: 'pointer',
    fontWeight: 500,
    transition: 'all 0.2s ease',
  }
};
