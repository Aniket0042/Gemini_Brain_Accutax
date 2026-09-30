import React, { useState } from 'react';
import { Globe, ChevronDown, ChevronUp, Copy, Check, Terminal, Cpu, AlertTriangle, CircleSlash, Zap } from 'lucide-react';

const FAILED_OUTCOMES = new Set(['unavailable', 'invalid', 'denied']);

// Classify a trace as CACHED / FAILED / EMPTY so the badge always matches what
// actually happened, not just HTTP 200 vs non-200 (a 200 with 0 rows is
// EMPTY, not a failure; a DENIED/UNAVAILABLE outcome is FAILED regardless of
// status code; a result served from result_cache never hit the network at
// all, so it is neither — see source="cache" in api_tracer.record_api_trace).
const outcomeBadge = (trace) => {
  if (trace.source === 'cache') return { label: 'CACHED', style: 'cached' };
  const outcome = String(trace.outcome || '').toLowerCase();
  const status = trace.status_code;
  const isFailed = FAILED_OUTCOMES.has(outcome) || (typeof status === 'number' && status >= 400);
  if (isFailed) return { label: 'FAILED', style: 'failed' };
  const isEmpty = outcome === 'empty' || (Number(trace.row_count) || 0) === 0;
  if (isEmpty) return { label: 'EMPTY', style: 'empty' };
  return null;
};

export const ApiTraceCard = ({ apiTraces = [] }) => {
  // Feature flag check: controlled via VITE_SHOW_API_TRACES in .env
  const isFeatureEnabled =
    import.meta.env.VITE_SHOW_API_TRACES === 'true' ||
    import.meta.env.VITE_SHOW_API_TRACES === true ||
    import.meta.env.VITE_SHOW_API_TRACES === '1';

  if (!isFeatureEnabled) return null;

  const traces = Array.isArray(apiTraces) ? apiTraces : [];
  if (traces.length === 0) return null;

  const [isExpanded, setIsExpanded] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState(null);

  const totalDuration = traces.reduce((acc, t) => acc + (Number(t.duration_ms) || 0), 0);
  const totalRows = traces.reduce((acc, t) => acc + (Number(t.row_count) || 0), 0);
  const badges = traces.map(outcomeBadge);
  const failedCount = badges.filter((b) => b?.style === 'failed').length;
  const emptyCount = badges.filter((b) => b?.style === 'empty').length;
  const cachedCount = badges.filter((b) => b?.style === 'cached').length;

  const formatCall = (trace) => {
    const params = { ...(trace.path_params || {}), ...(trace.query_params || {}) };
    const qs = Object.keys(params).length
      ? '?' + Object.entries(params).map(([k, v]) => `${k}=${v}`).join('&')
      : '';
    return `${trace.method || 'GET'} ${trace.endpoint}${qs}`;
  };

  const handleCopy = (trace, idx) => {
    const text = JSON.stringify(
      { endpoint: trace.endpoint, method: trace.method, path_params: trace.path_params, query_params: trace.query_params },
      null,
      2
    );
    navigator.clipboard.writeText(text);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  return (
    <div style={styles.container}>
      <div style={styles.stripRow}>
        <span style={styles.chip} title="Accutax REST API calls made for this response">
          <Globe size={11} color="var(--accent, #0e8a75)" />
          <span style={{ color: 'var(--accent-ink, #0a5c52)', fontWeight: 600 }}>
            {traces.length} API {traces.length === 1 ? 'call' : 'calls'}
          </span>
        </span>

        {totalDuration > 0 && (
          <span style={styles.chip} title="Total API call duration">
            <Cpu size={11} />
            <span>{totalDuration.toFixed(1)}ms</span>
          </span>
        )}

        <span style={styles.chip} title="Number of records returned by the API">
          <span>{totalRows} {totalRows === 1 ? 'row returned' : 'rows returned'}</span>
        </span>

        {failedCount > 0 && (
          <span style={styles.failedBadge} title="API calls that errored or were denied">
            <AlertTriangle size={11} />
            <span>{failedCount} failed</span>
          </span>
        )}

        {emptyCount > 0 && (
          <span style={styles.emptyBadge} title="API calls that returned zero rows">
            <CircleSlash size={11} />
            <span>{emptyCount} empty</span>
          </span>
        )}

        {cachedCount > 0 && (
          <span style={styles.cachedBadge} title="Served from result cache — no network call was made">
            <Zap size={11} />
            <span>{cachedCount} cached</span>
          </span>
        )}

        <button
          type="button"
          style={{
            ...styles.toggleChip,
            ...(isExpanded ? styles.toggleChipActive : {}),
          }}
          onClick={() => setIsExpanded((prev) => !prev)}
          title={isExpanded ? 'Hide API calls' : 'View API calls'}
        >
          <span>{isExpanded ? 'Hide API' : 'View API'}</span>
          {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
      </div>

      {isExpanded && (
        <div style={styles.expandedWrapper}>
          {traces.map((trace, idx) => {
            const duration = trace.duration_ms ? `${Number(trace.duration_ms).toFixed(1)}ms` : null;
            const rows = trace.row_count != null ? `${trace.row_count} ${trace.row_count === 1 ? 'row' : 'rows'}` : null;
            const status = trace.source === 'cache'
              ? 'CACHE'
              : trace.status_code != null ? `HTTP ${trace.status_code}` : (trace.outcome || '');
            const badge = outcomeBadge(trace);

            return (
              <div key={`api-trace-${idx}`} style={styles.codeBlock}>
                <div style={styles.codeHeader}>
                  <div style={styles.headerMeta}>
                    <Terminal size={11} color="var(--ink-faint, #8a968e)" />
                    <span style={styles.queryTitle}>Call #{idx + 1}</span>
                    <span style={styles.sourceTag}>{status}</span>
                    {duration && <span style={styles.durationTag}>{duration}</span>}
                    {rows && <span style={styles.rowsTag}>{rows}</span>}
                    {badge && (
                      <span style={
                        badge.style === 'failed' ? styles.failedTag
                          : badge.style === 'cached' ? styles.cachedTag
                          : styles.emptyTag
                      }>
                        {badge.label}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    style={styles.copyBtn}
                    onClick={() => handleCopy(trace, idx)}
                    title="Copy API call details"
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
                    <code>{formatCall(trace)}</code>
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
    fontSize: '0.6875rem',
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
  failedTag: {
    fontSize: '0.625rem',
    fontWeight: 700,
    letterSpacing: '0.03em',
    color: 'var(--danger-ink, #991b1b)',
    backgroundColor: 'var(--danger-soft, #fde8e8)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  emptyTag: {
    fontSize: '0.625rem',
    fontWeight: 700,
    letterSpacing: '0.03em',
    color: 'var(--ink-faint, #6b7280)',
    backgroundColor: 'rgba(107, 114, 128, 0.14)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  cachedTag: {
    fontSize: '0.625rem',
    fontWeight: 700,
    letterSpacing: '0.03em',
    color: 'var(--info-ink, #1d4ed8)',
    backgroundColor: 'var(--info-soft, #dbeafe)',
    padding: '1px 4px',
    borderRadius: '3px',
  },
  failedBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.6875rem',
    fontWeight: 600,
    padding: '2px 6px',
    borderRadius: 'var(--radius-pill, 9999px)',
    background: 'var(--danger-soft, #fde8e8)',
    color: 'var(--danger-ink, #991b1b)',
  },
  emptyBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.6875rem',
    fontWeight: 600,
    padding: '2px 6px',
    borderRadius: 'var(--radius-pill, 9999px)',
    background: 'rgba(107, 114, 128, 0.14)',
    color: 'var(--ink-faint, #6b7280)',
  },
  cachedBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.6875rem',
    fontWeight: 600,
    padding: '2px 6px',
    borderRadius: 'var(--radius-pill, 9999px)',
    background: 'var(--info-soft, #dbeafe)',
    color: 'var(--info-ink, #1d4ed8)',
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
