import React, { useState } from 'react';
import { Database, ChevronDown, ChevronUp, Copy, Check, Terminal, Cpu } from 'lucide-react';

export const SqlTraceCard = ({ sqlTraces = [], fallbackSql = null }) => {
  // Feature flag check: controlled via VITE_SHOW_SQL_TRACES in .env
  const isFeatureEnabled =
    import.meta.env.VITE_SHOW_SQL_TRACES === 'true' ||
    import.meta.env.VITE_SHOW_SQL_TRACES === true ||
    import.meta.env.VITE_SHOW_SQL_TRACES === '1';

  if (!isFeatureEnabled) return null;

  // Normalize traces
  let traces = Array.isArray(sqlTraces) && sqlTraces.length > 0 ? sqlTraces : [];
  if (traces.length === 0 && fallbackSql && typeof fallbackSql === 'string' && fallbackSql.trim()) {
    traces = [{ sql: fallbackSql.trim(), duration_ms: 0, row_count: 0, source: 'database' }];
  }

  if (traces.length === 0) return null;

  const [isExpanded, setIsExpanded] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState(null);

  const totalDuration = traces.reduce((acc, t) => acc + (Number(t.duration_ms) || 0), 0);
  const totalRows = traces.reduce((acc, t) => acc + (Number(t.row_count) || 0), 0);

  const handleCopy = (sqlText, idx) => {
    if (!sqlText) return;
    navigator.clipboard.writeText(sqlText);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  return (
    <div style={styles.container}>
      {/* Sleek Provenance-Style Chip Strip */}
      <div style={styles.stripRow}>
        <span style={styles.chip} title="Executed PostgreSQL Database Queries">
          <Database size={11} color="var(--accent, #0e8a75)" />
          <span style={{ color: 'var(--accent-ink, #0a5c52)', fontWeight: 600 }}>
            {traces.length} SQL {traces.length === 1 ? 'query' : 'queries'}
          </span>
        </span>

        {totalDuration > 0 && (
          <span style={styles.chip} title="Total database execution duration">
            <Cpu size={11} />
            <span>{totalDuration.toFixed(1)}ms</span>
          </span>
        )}

        <span style={styles.chip} title="Number of records returned by PostgreSQL to the AI">
          <span>{totalRows} {totalRows === 1 ? 'row returned' : 'rows returned'}</span>
        </span>

        <button
          type="button"
          style={{
            ...styles.toggleChip,
            ...(isExpanded ? styles.toggleChipActive : {}),
          }}
          onClick={() => setIsExpanded((prev) => !prev)}
          title={isExpanded ? 'Hide SQL query' : 'View executed SQL query'}
        >
          <span>{isExpanded ? 'Hide SQL' : 'View SQL'}</span>
          {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
      </div>

      {/* Compact Code Block when Expanded */}
      {isExpanded && (
        <div style={styles.expandedWrapper}>
          {traces.map((trace, idx) => {
            const querySql = trace.sql || '';
            const duration = trace.duration_ms ? `${Number(trace.duration_ms).toFixed(1)}ms` : null;
            const rows = trace.row_count != null ? `${trace.row_count} ${trace.row_count === 1 ? 'row' : 'rows'}` : null;
            const source = trace.source || 'database';

            return (
              <div key={`trace-${idx}`} style={styles.codeBlock}>
                <div style={styles.codeHeader}>
                  <div style={styles.headerMeta}>
                    <Terminal size={11} color="var(--ink-faint, #8a968e)" />
                    <span style={styles.queryTitle}>Query #{idx + 1}</span>
                    <span style={styles.sourceTag}>{source}</span>
                    {duration && <span style={styles.durationTag}>{duration}</span>}
                    {rows && <span style={styles.rowsTag}>{rows}</span>}
                  </div>
                  <button
                    type="button"
                    style={styles.copyBtn}
                    onClick={() => handleCopy(querySql, idx)}
                    title="Copy SQL Query"
                  >
                    {copiedIdx === idx ? (
                      <>
                        <Check size={11} color="var(--success, #10b981)" />
                        <span style={{ color: 'var(--success-ink, #047857)' }}>Copied</span>
                      </>
                    ) : (
                      <>
                        <Copy size={11} />
                        <span>Copy</span>
                      </>
                    )}
                  </button>
                </div>
                <div style={styles.codeBody}>
                  <pre style={styles.preCode}>
                    <code>{querySql}</code>
                  </pre>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

const styles = {
  container: {
    margin: '4px 0 2px 0',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  stripRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    flexWrap: 'wrap',
  },
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.6875rem', /* matches .prov-chip */
    fontWeight: 500,
    padding: '2px 6px',
    borderRadius: 'var(--radius-pill, 9999px)',
    background: 'transparent',
    color: 'var(--ink-faint, #8a968e)',
  },
  toggleChip: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '3px',
    fontSize: '0.6875rem',
    fontWeight: 600,
    padding: '2px 8px',
    borderRadius: 'var(--radius-pill, 9999px)',
    background: 'var(--surface-2, #f3f4f6)',
    border: '1px solid var(--border, rgba(0,0,0,0.08))',
    color: 'var(--ink-soft, #526059)',
    cursor: 'pointer',
    transition: 'all 0.15s ease',
  },
  toggleChipActive: {
    background: 'var(--accent-soft, #e1f1ec)',
    color: 'var(--accent-ink, #0a5c52)',
    borderColor: 'rgba(14, 138, 117, 0.25)',
  },
  expandedWrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    marginTop: '2px',
  },
  codeBlock: {
    border: '1px solid var(--border, rgba(0,0,0,0.1))',
    borderRadius: 'var(--radius-sm, 6px)',
    overflow: 'hidden',
    backgroundColor: 'var(--surface, #ffffff)',
    boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
  },
  codeHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '4px 8px',
    backgroundColor: 'var(--surface-2, #f3f4f6)',
    borderBottom: '1px solid var(--border-soft, rgba(0,0,0,0.06))',
  },
  headerMeta: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    flexWrap: 'wrap',
  },
  queryTitle: {
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: '0.6875rem',
    fontWeight: 600,
    color: 'var(--ink, #14201c)',
  },
  sourceTag: {
    fontSize: '0.625rem',
    fontWeight: 600,
    textTransform: 'uppercase',
    letterSpacing: '0.03em',
    color: 'var(--ink-faint, #8a968e)',
    backgroundColor: 'rgba(0, 0, 0, 0.05)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  durationTag: {
    fontSize: '0.625rem',
    fontWeight: 600,
    color: 'var(--warning-ink, #92400e)',
    backgroundColor: 'var(--warning-soft, #fdf1dd)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  rowsTag: {
    fontSize: '0.625rem',
    fontWeight: 600,
    color: 'var(--success-ink, #047857)',
    backgroundColor: 'var(--success-soft, #e4f7ef)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  copyBtn: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '3px',
    padding: '2px 6px',
    background: 'none',
    border: 'none',
    borderRadius: '3px',
    color: 'var(--ink-faint, #8a968e)',
    fontSize: '0.6875rem',
    cursor: 'pointer',
    transition: 'color 0.15s ease',
  },
  codeBody: {
    padding: '8px 10px',
    overflowX: 'auto',
    backgroundColor: 'var(--bg, #f3f4f6)',
  },
  preCode: {
    margin: 0,
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: '0.72rem',
    lineHeight: '1.45',
    color: 'var(--ink-soft, #526059)',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
  },
};


