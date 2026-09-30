import React, { useState } from 'react';
import { Brain, ChevronDown, ChevronUp, Copy, Check, Terminal, Cpu } from 'lucide-react';

// Purpose codes recorded by the backend (see gemini_brain.observability.llm_tracer)
// mapped to a short, human label for the chip.
const PURPOSE_LABELS = {
  classify_intent: 'Classify intent',
  endpoint_select: 'Select endpoint',
  endpoint_select_retry: 'Select endpoint (retry)',
  direct_answer: 'Direct answer',
  narration: 'Narrate data',
  sql_fallback_narration: 'Narrate SQL result',
  auto_title: 'Auto-title chat',
  conversation_summary: 'Summarize conversation',
  sql_agent: 'SQL agent',
  unspecified: 'Unspecified',
};

const purposeLabel = (purpose) => PURPOSE_LABELS[purpose] || purpose || 'Unspecified';

export const LlmTraceCard = ({ llmTraces = [] }) => {
  // Feature flag check: controlled via VITE_SHOW_LLM_TRACES in .env
  const isFeatureEnabled =
    import.meta.env.VITE_SHOW_LLM_TRACES === 'true' ||
    import.meta.env.VITE_SHOW_LLM_TRACES === true ||
    import.meta.env.VITE_SHOW_LLM_TRACES === '1';

  if (!isFeatureEnabled) return null;

  const traces = Array.isArray(llmTraces) ? llmTraces : [];
  if (traces.length === 0) return null;

  const [isExpanded, setIsExpanded] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState(null);

  const totalDuration = traces.reduce((acc, t) => acc + (Number(t.duration_ms) || 0), 0);
  const totalInputTokens = traces.reduce((acc, t) => acc + (Number(t.input_tokens) || 0), 0);
  const totalOutputTokens = traces.reduce((acc, t) => acc + (Number(t.output_tokens) || 0), 0);

  const handleCopy = (trace, idx) => {
    const text = JSON.stringify(
      { model_id: trace.model_id, purpose: trace.purpose, input_tokens: trace.input_tokens, output_tokens: trace.output_tokens },
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
        <span style={styles.chip} title="Bedrock/Claude LLM calls made for this response">
          <Brain size={11} color="var(--accent, #0e8a75)" />
          <span style={{ color: 'var(--accent-ink, #0a5c52)', fontWeight: 600 }}>
            {traces.length} LLM {traces.length === 1 ? 'call' : 'calls'}
          </span>
        </span>

        {totalDuration > 0 && (
          <span style={styles.chip} title="Total LLM call duration">
            <Cpu size={11} />
            <span>{totalDuration.toFixed(1)}ms</span>
          </span>
        )}

        <span style={styles.chip} title="Total input + output tokens across all LLM calls">
          <span>{totalInputTokens} in / {totalOutputTokens} out tokens</span>
        </span>

        <button
          type="button"
          style={{
            ...styles.toggleChip,
            ...(isExpanded ? styles.toggleChipActive : {}),
          }}
          onClick={() => setIsExpanded((prev) => !prev)}
          title={isExpanded ? 'Hide LLM calls' : 'View LLM calls'}
        >
          <span>{isExpanded ? 'Hide LLM' : 'View LLM'}</span>
          {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
        </button>
      </div>

      {isExpanded && (
        <div style={styles.expandedWrapper}>
          {traces.map((trace, idx) => {
            const duration = trace.duration_ms ? `${Number(trace.duration_ms).toFixed(1)}ms` : null;
            const tokens = `${trace.input_tokens ?? 0} in / ${trace.output_tokens ?? 0} out`;

            return (
              <div key={`llm-trace-${idx}`} style={styles.codeBlock}>
                <div style={styles.codeHeader}>
                  <div style={styles.headerMeta}>
                    <Terminal size={11} color="var(--ink-faint, #8a968e)" />
                    <span style={styles.queryTitle}>Call #{idx + 1}</span>
                    <span style={styles.purposeTag}>{purposeLabel(trace.purpose)}</span>
                    {duration && <span style={styles.durationTag}>{duration}</span>}
                    <span style={styles.tokensTag}>{tokens}</span>
                  </div>
                  <button
                    type="button"
                    style={styles.copyBtn}
                    onClick={() => handleCopy(trace, idx)}
                    title="Copy LLM call details"
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
                    <code>{trace.label || trace.model_id}</code>
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
  purposeTag: {
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
  tokensTag: {
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
