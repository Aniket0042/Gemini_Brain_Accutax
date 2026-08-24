import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { 
  Sparkles, ShieldAlert, Code2, Layers, 
  ChevronDown, ChevronUp, Copy, Check, 
  RotateCw, Clock, DollarSign, Database, Server
} from 'lucide-react';
import { NoticeCard } from './NoticeCard';

/**
 * Custom components for ReactMarkdown to ensure wide tables and elements are cleanly scrollable.
 */
const customMarkdownComponents = {
  table: ({ node, ...props }) => (
    <div className="markdown-table-wrapper">
      <table {...props} />
    </div>
  ),
};

/**
 * PacedMarkdownStream — Streams text smoothly word-by-word at natural reading speed
 * with an active glowing cursor at the leading edge.
 */
const PacedMarkdownStream = ({ text, isStreaming }) => {
  const [revealedLength, setRevealedLength] = useState(0);
  const targetTextRef = useRef(text || '');

  useEffect(() => {
    targetTextRef.current = text || '';
  }, [text]);

  useEffect(() => {
    if (!text) {
      setRevealedLength(0);
      return;
    }

    const timer = setInterval(() => {
      const target = targetTextRef.current;
      setRevealedLength((current) => {
        if (current >= target.length) {
          return current;
        }

        // Find next word/newline boundary (relaxed single-word pacing)
        const remaining = target.length - current;
        const wordsToAdvance = remaining > 350 ? 2 : 1;
        let nextIdx = current;

        for (let i = 0; i < wordsToAdvance; i++) {
          const nextSpace = target.indexOf(' ', nextIdx + 1);
          const nextNewline = target.indexOf('\n', nextIdx + 1);
          let candidate = -1;
          if (nextSpace !== -1 && nextNewline !== -1) {
            candidate = Math.min(nextSpace, nextNewline);
          } else {
            candidate = Math.max(nextSpace, nextNewline);
          }

          if (candidate !== -1 && candidate < target.length) {
            nextIdx = candidate + 1;
          } else {
            nextIdx = target.length;
            break;
          }
        }

        return nextIdx;
      });
    }, 55); // 55ms per word: relaxed, comfortable, smooth reading pace

    return () => clearInterval(timer);
  }, [text]);

  const isStillPacing = revealedLength < (text || '').length;
  const showCursor = isStreaming || isStillPacing;
  const currentSlice = text ? text.slice(0, revealedLength) : '';

  return (
    <div className="markdown-body" style={styles.markdownWrapper}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
        {currentSlice || text}
      </ReactMarkdown>
      {showCursor && <span className="streaming-cursor" />}
    </div>
  );
};

/**
 * AssistantResponseCard — Renders individual assistant response in clean unboxed ChatGPT/Gemini style.
 */
const AssistantResponseCard = ({ 
  msg, 
  userQuery, 
  onRegenerate, 
  activeTenant 
}) => {
  const [copied, setCopied] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const [showTrace, setShowTrace] = useState(false);

  const responseData = msg.responseData;
  const isStreaming = msg.isStreaming;
  const notice = responseData?.notice || msg.notice;
  const rawContent = responseData?.answer || msg.streamingText || '';
  const content = rawContent.trim();
  const tableMarkdown = msg.tableData || responseData?.table_markdown;
  const isSecurityError = Boolean(!notice && (responseData?.error || (responseData?.answer && responseData.answer.startsWith('Error:'))));
  const isError = Boolean(msg.isError || isSecurityError || responseData?.status === 'failed');

  const tokenUsage = responseData?.token_usage;
  const totalTokens = tokenUsage ? (tokenUsage.input_tokens || 0) + (tokenUsage.output_tokens || 0) : 0;
  const elapsedSeconds = tokenUsage?.elapsed_seconds ?? responseData?.elapsed_seconds;
  const costUsd = tokenUsage?.cost_usd;

  const handleCopy = () => {
    if (!content) return;
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={styles.assistantRow}>
      {/* Notice Card for Empty / Partial / Degraded / Denied / Error states */}
      {notice && (
        <NoticeCard
          notice={notice}
          onRetry={() => onRegenerate && onRegenerate(userQuery)}
          onSuggestionClick={(sug) => onRegenerate && onRegenerate(sug)}
        />
      )}

      {/* Data Source Indicator Pill */}
      {!isStreaming && responseData?.data_source && (
        <div className="data-source-pill">
          <span className="source-label">Source:</span>
          <span className={`source-tag tag-${responseData.data_source.tier}`}>
            {responseData.data_source.tier === 'live_api' ? '⚡ Live API' :
             responseData.data_source.tier === 'cache' ? '💾 Cache' :
             responseData.data_source.tier === 'sql_function' ? '📊 Stored Function' :
             '🔍 SQL Fallback'}
          </span>
          {responseData.data_source.endpoint && (
            <span className="source-endpoint">{responseData.data_source.endpoint}</span>
          )}
        </div>
      )}

      {/* Immediate Data Table (rendered before or alongside narration) */}
      {tableMarkdown && (
        <div className="data-table-container">
          <div className="markdown-body" style={styles.markdownWrapper}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
              {tableMarkdown}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Security Error Banner or Paced Streaming Markdown Content */}
      {isSecurityError ? (
        <div style={styles.errorBox}>
          <div style={styles.errorHeader}>
            <ShieldAlert size={20} color="#f43f5e" />
            <div>
              <h4 style={styles.errorTitle}>Security Isolation Boundary Notice</h4>
              <p style={styles.errorSub}>{responseData.error || responseData.answer}</p>
            </div>
          </div>
        </div>
      ) : content ? (
        <PacedMarkdownStream text={content} isStreaming={isStreaming} />
      ) : (
        !notice && !tableMarkdown && (
          <div style={styles.emptyNotice}>
            <span>No response generated. Please try again.</span>
          </div>
        )
      )}

      {/* Technical Metadata & SQL Accordions (Subtle & Non-Intrusive) */}
      {!isStreaming && (responseData?.sql || (responseData?.agent_trace && responseData.agent_trace.length > 0)) && (
        <div style={styles.inspectorsRow}>
          {responseData.sql && (
            <div style={styles.sqlInspector}>
              <button style={styles.inspectorToggle} onClick={() => setShowSql(!showSql)}>
                <div style={styles.inspectorTitle}>
                  <Code2 size={13} color="#10b981" />
                  <span>Executed SQL Query</span>
                </div>
                {showSql ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>
              {showSql && (
                <pre style={styles.sqlCodeBlock}>
                  <code>{responseData.sql}</code>
                </pre>
              )}
            </div>
          )}

          {responseData.agent_trace && responseData.agent_trace.length > 0 && (
            <div style={styles.traceInspector}>
              <button style={styles.inspectorToggle} onClick={() => setShowTrace(!showTrace)}>
                <div style={styles.inspectorTitle}>
                  <Layers size={13} color="#06b6d4" />
                  <span>Trace ({responseData.agent_trace.length} steps)</span>
                </div>
                {showTrace ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
              </button>
              {showTrace && (
                <div style={styles.traceContent}>
                  {responseData.agent_trace.map((step, idx) => (
                    <div key={idx} style={styles.traceStepItem}>
                      <span className="badge badge-cyan">{step.agent || step.task || step.step || 'Step'}</span>
                      <span style={styles.traceStepText}>{JSON.stringify(step)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ChatGPT / Gemini Style Bottom Action Buttons & Metrics */}
      {!isStreaming && content && !isError && (
        <div style={styles.actionToolbar}>
          <div style={styles.actionLeft}>
            <button 
              style={{ ...styles.actionBtn, ...(copied ? styles.actionBtnActive : {}) }} 
              onClick={handleCopy} 
              title={copied ? "Copied to clipboard!" : "Copy response"}
            >
              {copied ? <Check size={15} color="#10b981" /> : <Copy size={15} />}
            </button>

            {onRegenerate && userQuery && (
              <button 
                style={styles.actionBtn} 
                onClick={() => onRegenerate(userQuery)} 
                title="Regenerate response"
              >
                <RotateCw size={15} />
              </button>
            )}
          </div>

          {/* Clean Subtle Token Count & Latency Metrics */}
          {tokenUsage && (
            <div style={styles.metricsBar}>
              {totalTokens > 0 && (
                <span style={styles.metricPill} title="Total tokens processed">
                  <Layers size={11} color="#818cf8" />
                  <span>{totalTokens.toLocaleString()} tokens</span>
                </span>
              )}
              {elapsedSeconds !== undefined && elapsedSeconds !== null && (
                <span style={styles.metricPill} title="Response generation latency">
                  <Clock size={11} color="#f59e0b" />
                  <span>{elapsedSeconds}s</span>
                </span>
              )}
              {costUsd !== undefined && costUsd !== null && costUsd > 0 && (
                <span style={styles.metricPill} title="Estimated cost">
                  <DollarSign size={11} color="#10b981" />
                  <span>${costUsd.toFixed(5)}</span>
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/**
 * ResponseView — Renders full conversation list sequentially in clean ChatGPT/Gemini layout.
 */
export const ResponseView = ({ conversation, isLoading, streamLogs, activeTenant, onRegenerate }) => {
  if (!conversation || conversation.length === 0) return null;

  return (
    <div style={styles.conversationList}>
      {conversation.map((msg, idx) => {
        const isUser = msg.role === 'user';

        if (isUser) {
          return (
            <div key={idx} style={styles.userMsgWrapper}>
              <div style={styles.userBubble}>
                {msg.content}
              </div>
            </div>
          );
        }

        // Assistant Turn
        const isStreaming = msg.isStreaming;
        const responseData = msg.responseData;
        const streamingText = msg.streamingText;
        const hasContent = Boolean((responseData?.answer || streamingText || '').trim());

        // Prior user prompt for regeneration
        const prevUserTurn = idx > 0 && conversation[idx - 1].role === 'user' ? conversation[idx - 1].content : '';

        // If loading and no text accumulated yet: Show ONLY Icon + Dynamic Text + 3-dots animation
        if (isStreaming && !hasContent) {
          const latestLog = streamLogs && streamLogs.length > 0 ? streamLogs[streamLogs.length - 1] : null;
          const statusText = msg.latestStatus || latestLog?.status || 'Understanding request';

          return (
            <div key={idx} style={styles.minimalLoaderContainer}>
              <div style={styles.loaderIconBox}>
                <Sparkles size={18} color="#818cf8" className="gemini-sparkle-spin" />
              </div>
              <div style={styles.loaderTextGroup}>
                <span style={styles.loaderStatusText}>{statusText}</span>
                <div className="gemini-dots">
                  <span className="gemini-dot" />
                  <span className="gemini-dot" />
                  <span className="gemini-dot" />
                </div>
              </div>
            </div>
          );
        }

        return (
          <AssistantResponseCard 
            key={idx}
            msg={msg}
            userQuery={prevUserTurn}
            onRegenerate={onRegenerate}
            activeTenant={activeTenant}
          />
        );
      })}
    </div>
  );
};

const styles = {
  conversationList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '28px',
    paddingBottom: '40px',
    width: '100%',
  },
  userMsgWrapper: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    marginBottom: '4px',
    maxWidth: '100%',
  },
  userBubble: {
    padding: '11px 18px',
    borderRadius: '20px 20px 4px 20px',
    backgroundColor: 'rgba(255, 255, 255, 0.07)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#ffffff',
    fontSize: '0.95rem',
    fontWeight: 500,
    lineHeight: '1.5',
    textAlign: 'left',
    maxWidth: '75%',
    wordBreak: 'break-word',
    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.2)',
  },
  assistantRow: {
    width: '100%',
    maxWidth: '100%',
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    textAlign: 'left',
    padding: '4px 0 16px',
  },
  markdownWrapper: {
    width: '100%',
    maxWidth: '100%',
    color: '#f3f4f6',
    fontSize: '0.975rem',
    lineHeight: '1.75',
  },
  minimalLoaderContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '8px 0',
    width: '100%',
  },
  loaderIconBox: {
    width: '32px',
    height: '32px',
    borderRadius: '10px',
    backgroundColor: 'rgba(99, 102, 241, 0.12)',
    border: '1px solid rgba(99, 102, 241, 0.25)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
  },
  loaderTextGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
  },
  loaderStatusText: {
    fontSize: '0.95rem',
    fontWeight: 600,
    color: '#f3f4f6',
    letterSpacing: '-0.01em',
  },
  actionToolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '12px',
    marginTop: '6px',
    flexWrap: 'wrap',
  },
  actionLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  actionBtn: {
    background: 'transparent',
    border: 'none',
    color: '#9ca3af',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '6px 8px',
    borderRadius: '6px',
    transition: 'all 0.15s ease',
  },
  actionBtnActive: {
    color: '#818cf8',
    backgroundColor: 'rgba(99, 102, 241, 0.1)',
  },
  metricsBar: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
  },
  metricPill: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '4px',
    fontSize: '0.75rem',
    color: '#6b7280',
    fontWeight: 500,
  },
  inspectorsRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    marginTop: '8px',
  },
  sqlInspector: {
    borderRadius: '8px',
    backgroundColor: 'rgba(0, 0, 0, 0.25)',
    border: '1px solid rgba(16, 185, 129, 0.15)',
    overflow: 'hidden',
  },
  traceInspector: {
    borderRadius: '8px',
    backgroundColor: 'rgba(0, 0, 0, 0.25)',
    border: '1px solid rgba(6, 182, 212, 0.15)',
    overflow: 'hidden',
  },
  inspectorToggle: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '8px 12px',
    background: 'none',
    border: 'none',
    color: '#9ca3af',
    cursor: 'pointer',
    fontSize: '0.78rem',
    fontWeight: 600,
  },
  inspectorTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  sqlCodeBlock: {
    padding: '12px 14px',
    backgroundColor: '#0a0d14',
    color: '#10b981',
    fontFamily: 'var(--font-mono)',
    fontSize: '0.8rem',
    overflowX: 'auto',
    borderTop: '1px solid rgba(255, 255, 255, 0.06)',
  },
  traceContent: {
    padding: '10px 14px',
    borderTop: '1px solid rgba(255, 255, 255, 0.06)',
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
  },
  traceStepItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '0.75rem',
  },
  traceStepText: {
    fontFamily: 'var(--font-mono)',
    color: '#9ca3af',
  },
  errorBox: {
    padding: '14px 16px',
    borderRadius: '10px',
    backgroundColor: 'rgba(244, 63, 94, 0.08)',
    border: '1px solid rgba(244, 63, 94, 0.25)',
  },
  errorHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '12px',
  },
  errorTitle: {
    color: '#f43f5e',
    fontSize: '0.95rem',
    marginBottom: '2px',
  },
  errorSub: {
    color: '#e5e7eb',
    fontSize: '0.85rem',
    fontFamily: 'var(--font-mono)',
  },
  emptyNotice: {
    color: '#6b7280',
    fontSize: '0.875rem',
    fontStyle: 'italic',
    padding: '4px 0',
  },
};
