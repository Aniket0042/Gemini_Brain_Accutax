import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { NoticeCard } from './NoticeCard';
import { AnswerProvenance } from './AnswerProvenance';
import { BlockRenderer, hasPortedBlocks, chatFacingBlocks } from './blocks/BlockRenderer';
import { ReportActions } from './blocks/ReportActions';
import { CodeChrome } from './blocks/CodeBlock';
import { 
  Sparkles, ShieldAlert, Code2, Layers, 
  ChevronDown, ChevronUp, Copy, Check, 
  RotateCw, Clock, DollarSign, Database, Server,
  Sparkles as AssistantMark, Share, Edit2, FileText, CheckCircle2,
  Zap, HardDrive, BarChart3, Search
} from 'lucide-react';

/** Formats a stored timestamp for display; older saved sessions may not have one. */
function formatTime(ts) {
  if (!ts) return null;
  try {
    return new Date(ts).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  } catch {
    return null;
  }
}

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
  // Messages loaded from saved history (or any already-finished answer) arrive
  // with isStreaming false/undefined from the start — those should render as
  // static text immediately, not replay the live-typing animation meant only
  // for an answer that is actually streaming in right now.
  const [revealedLength, setRevealedLength] = useState(isStreaming ? 0 : (text || '').length);
  const targetTextRef = useRef(text || '');

  useEffect(() => {
    targetTextRef.current = text || '';
  }, [text]);

  useEffect(() => {
    if (!isStreaming) {
      setRevealedLength((text || '').length);
      return;
    }

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
  }, [text, isStreaming]);

  const fullLength = (text || '').length;
  // Don't wait for the effect: once the stream is done, render the finished
  // answer with no caret. The caret used to stay on for the whole
  // "Finalizing response" window because showCursor keyed off isStreaming.
  const displayLength = isStreaming ? Math.min(revealedLength, fullLength) : fullLength;
  const isStillPacing = isStreaming && displayLength < fullLength;
  const currentSlice = text ? text.slice(0, displayLength) : '';

  return (
    <div className="markdown-body" style={styles.markdownWrapper}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
        {currentSlice}
      </ReactMarkdown>
      {isStillPacing && <span className="streaming-cursor" />}
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
  activeTenant,
  onOpenCanvas,
  token,
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
  // Once a formatter is ported (kv_summary, row_table so far — see the UI
  // rebuild plan), its structured block replaces the pre-rendered markdown
  // table below. Anything not yet ported keeps using tableMarkdown exactly
  // as before — this is additive, not a behaviour change for those.
  const blocks = responseData?.blocks;
  const showBlocks = !isStreaming && hasPortedBlocks(blocks);
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
    <div className="turn-wrapper" style={styles.assistantRow}>
      <div style={styles.avatarBox}>
        <AssistantMark size={16} color="var(--surface)" />
      </div>
      <div style={styles.assistantContentCol}>
        
        {/* Notice Card for Empty / Partial / Degraded / Denied / Error states */}
        {notice && (
          <NoticeCard
            notice={notice}
            onRetry={() => onRegenerate && onRegenerate(userQuery)}
            onSuggestionClick={(sug) => onRegenerate && onRegenerate(sug)}
          />
        )}

        {(() => {
          const steps = (msg.agentSteps || []).filter(Boolean);
          if (!steps.length) return null;
          return (
            <div style={styles.agentStepsWrapper}>
              <div style={styles.agentStepsHeader}>
                <Layers size={12} />
                AGENT STEPS
              </div>
              <div style={styles.agentStepsList}>
                {steps.map((step, i) => (
                  <div key={`${step}-${i}`} style={styles.agentStepItem}>
                    <CheckCircle2 size={14} color="var(--accent)" />
                    <span style={styles.stepTitle}>{step}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })()}

        {/* Assistant Main Content Card */}
        <div style={styles.assistantCard}>
          {isSecurityError ? (
            <div style={styles.errorBox}>
              <div style={styles.errorHeader}>
                <ShieldAlert size={20} color="var(--danger)" />
                <div>
                  <h4 style={styles.errorTitle}>Security Isolation Boundary Notice</h4>
                  <p style={styles.errorSub}>{responseData.error || responseData.answer}</p>
                </div>
              </div>
            </div>
          ) : content ? (
            <PacedMarkdownStream text={content} isStreaming={isStreaming} />
          ) : null}

          {showBlocks ? (
            <div className="data-table-container" style={styles.markdownWrapper}>
              <BlockRenderer
                blocks={chatFacingBlocks(blocks)}
                verification={responseData?.verification}
                onOpenCanvas={onOpenCanvas}
                token={token}
              />
            </div>
          ) : (
            tableMarkdown && (
              <div className="data-table-container">
                <div className="markdown-body" style={styles.markdownWrapper}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
                    {tableMarkdown}
                  </ReactMarkdown>
                </div>
              </div>
            )
          )}

          {!isStreaming && (
            <ReportActions blocks={blocks} onOpenCanvas={onOpenCanvas} token={token} />
          )}

          {!content && !notice && !tableMarkdown && !showBlocks && (
            <div style={styles.emptyNotice}>
              <span>No response generated. Please try again.</span>
            </div>
          )}
        </div>

        {showSql && responseData?.sql && (
          <CodeChrome language="sql" content={responseData.sql} title="Executed SQL" />
        )}

        {!isStreaming && content && !isError && (responseData?.policy || responseData?.verification) && (
          <AnswerProvenance
            policy={responseData.policy}
            verification={responseData.verification}
          />
        )}

        {/* ChatGPT / Gemini Style Bottom Action Buttons & Metrics */}
        {!isStreaming && content && !isError && (
          <div className="turn-actions" style={styles.actionToolbar}>
            <div style={styles.actionLeft}>
              <button 
                style={{ ...styles.actionBtn, ...(copied ? styles.actionBtnActive : {}) }} 
                onClick={handleCopy} 
                title={copied ? "Copied to clipboard!" : "Copy response"}
              >
                {copied ? <Check size={14} color="var(--success)" /> : <Copy size={14} />}
              </button>

              <button style={styles.actionBtn} title="Share response">
                <Share size={14} />
              </button>

              {onRegenerate && userQuery && (
                <button 
                  style={styles.actionBtn} 
                  onClick={() => onRegenerate(userQuery)} 
                  title="Regenerate response"
                >
                  <RotateCw size={14} />
                </button>
              )}

              {responseData?.sql && (
                <button
                  style={{ ...styles.actionBtn, ...(showSql ? styles.actionBtnActive : {}) }}
                  onClick={() => setShowSql((v) => !v)}
                  title={showSql ? 'Hide SQL' : 'Show SQL'}
                >
                  <Code2 size={14} color={showSql ? 'var(--accent-ink)' : undefined} />
                </button>
              )}
            </div>

            {/* Clean Subtle Token Count & Latency Metrics */}
            <div style={styles.metricsBar}>
              {tokenUsage && elapsedSeconds !== undefined && elapsedSeconds !== null && (
                <span style={styles.metricPill} title="Response generation latency">
                  <Clock size={11} color="var(--warning)" />
                  <span>{elapsedSeconds}s</span>
                </span>
              )}
              {formatTime(msg.timestamp) && (
                <span style={styles.timestamp}>{formatTime(msg.timestamp)}</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

/**
 * ModelAnswerCard — one model's answer within a multi-model ("All Models")
 * response. A trimmed-down version of AssistantResponseCard's content area:
 * same block/markdown/notice rendering, no per-message action toolbar (copy/
 * regenerate/etc. don't make sense per-card in a comparison view) — those
 * live once on the MultiModelResponseCard wrapper instead.
 */
const ModelAnswerCard = ({ response }) => {
  const notice = response?.notice;
  const content = (response?.answer || '').trim();
  const blocks = response?.blocks;
  const showBlocks = hasPortedBlocks(blocks);
  const tableMarkdown = response?.table_markdown;
  const isError = Boolean(response?.error || response?.status === 'failed');
  const modelLabel = response?.policy?.model_label || response?.policy?.model || 'Model';
  const elapsedSeconds = response?.token_usage?.elapsed_seconds;

  return (
    <div style={styles.modelCard}>
      <div style={styles.modelCardHeader}>
        <span style={styles.modelCardLabel}>{modelLabel}</span>
        {elapsedSeconds !== undefined && elapsedSeconds !== null && (
          <span style={styles.metricPill} title="Response generation latency">
            <Clock size={11} color="var(--warning)" />
            <span>{elapsedSeconds}s</span>
          </span>
        )}
      </div>

      {notice && <NoticeCard notice={notice} />}

      {showBlocks ? (
        <div className="data-table-container" style={styles.markdownWrapper}>
          <BlockRenderer blocks={blocks} verification={response?.verification} />
        </div>
      ) : (
        tableMarkdown && (
          <div className="data-table-container">
            <div className="markdown-body" style={styles.markdownWrapper}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
                {tableMarkdown}
              </ReactMarkdown>
            </div>
          </div>
        )
      )}

      {content ? (
        <div className="markdown-body" style={styles.markdownWrapper}>
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={customMarkdownComponents}>
            {content}
          </ReactMarkdown>
        </div>
      ) : (
        !notice && !tableMarkdown && (
          <div style={styles.emptyNotice}>
            <span>{isError ? 'This model failed to respond.' : 'No response generated.'}</span>
          </div>
        )
      )}
    </div>
  );
};

/**
 * MultiModelResponseCard — "All Models" dev comparison result: one
 * ModelAnswerCard per model that was queried, stacked vertically.
 */
const MultiModelResponseCard = ({ msg }) => {
  const responses = msg.responses || [];
  return (
    <div className="turn-wrapper" style={styles.assistantRow}>
      <div style={styles.avatarBox}>
        <AssistantMark size={16} color="var(--surface)" />
      </div>
      <div style={styles.assistantContentCol}>
        <div style={styles.multiModelHeader}>
          <Layers size={13} />
          <span>All Models — {responses.length} response{responses.length === 1 ? '' : 's'}</span>
        </div>
        {responses.map((response, i) => (
          <ModelAnswerCard key={i} response={response} />
        ))}
      </div>
    </div>
  );
};

/**
 * ResponseView — Renders full conversation list sequentially in clean ChatGPT/Gemini layout.
 */
export const ResponseView = ({ conversation, isLoading, streamLogs, activeTenant, onRegenerate, onOpenCanvas, token }) => {
  if (!conversation || conversation.length === 0) return null;

  return (
    <div style={styles.conversationList}>
      {conversation.map((msg, idx) => {
        const isUser = msg.role === 'user';

        if (isUser) {
          return (
            <div key={idx} className="turn-wrapper" style={styles.userTurnWrapper}>
              <div style={styles.userMsgWrapper}>
                <div style={styles.userBubble}>
                  {msg.content}
                </div>
                <div className="turn-actions" style={styles.userFooter}>
                  <div style={styles.userActionLeft}>
                    <button style={styles.userActionBtn} title="Copy"><Copy size={13} /></button>
                    <button style={styles.userActionBtn} title="Share"><Share size={13} /></button>
                    <button style={styles.userActionBtn} title="Edit"><Edit2 size={13} /></button>
                  </div>
                  {formatTime(msg.timestamp) && (
                    <span style={styles.timestamp}>{formatTime(msg.timestamp)}</span>
                  )}
                </div>
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
                <Sparkles size={18} color="var(--accent)" className="gemini-sparkle-spin" />
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

        if (msg.isMultiModel) {
          return <MultiModelResponseCard key={idx} msg={msg} />;
        }

        return (
          <AssistantResponseCard
            key={idx}
            msg={msg}
            userQuery={prevUserTurn}
            onRegenerate={onRegenerate}
            activeTenant={activeTenant}
            onOpenCanvas={onOpenCanvas}
            token={token}
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
    gap: '20px',
    paddingBottom: '40px',
    width: '100%',
  },
  multiModelHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: 'var(--text-xs)',
    fontWeight: 600,
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
    color: 'var(--ink-faint)',
  },
  modelCard: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    backgroundColor: 'var(--surface)',
    borderRadius: 'var(--radius-md)',
    padding: '16px',
    border: '1px solid var(--border-soft)',
  },
  modelCardHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '8px',
  },
  modelCardLabel: {
    fontSize: 'var(--text-ui)',
    fontWeight: 600,
    color: 'var(--ink)',
  },
  userTurnWrapper: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    marginBottom: '16px',
    maxWidth: '100%',
    animation: 'messageIn var(--dur-base) var(--ease)',
  },
  userMsgWrapper: {
    display: 'flex',
    flexDirection: 'column',
    maxWidth: '75%',
  },
  userBubble: {
    padding: '8px 16px',
    borderRadius: '16px 16px 0 16px',
    backgroundColor: '#109383',
    color: '#FFFFFF',
    fontSize: '0.875rem',
    lineHeight: '1.5',
    textAlign: 'left',
    wordBreak: 'break-word',
    boxShadow: '0 1px 4px rgba(16, 147, 131, 0.15)',
  },
  userFooter: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginTop: '6px',
    padding: '0 4px',
  },
  userActionLeft: {
    display: 'flex',
    gap: '6px',
  },
  userActionBtn: {
    background: 'none',
    border: 'none',
    color: 'var(--ink-faint)',
    cursor: 'pointer',
    padding: '2px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'color var(--dur-fast) var(--ease)',
  },
  timestamp: {
    fontSize: '0.75rem',
    color: 'var(--ink-faint)',
  },
  assistantRow: {
    width: '100%',
    maxWidth: '100%',
    display: 'flex',
    flexDirection: 'row',
    gap: '16px',
    animation: 'messageIn var(--dur-base) var(--ease)',
    textAlign: 'left',
    padding: '0',
    backgroundColor: 'transparent',
    border: 'none',
  },
  // Matches the brand mark used everywhere else — header, sidebar, composer
  // empty state — instead of a generic robot icon on an unrelated "success"
  // green. One consistent mark for the assistant, like every reference
  // product uses, rather than a decorative color pulled from a semantic
  // status token that had nothing to do with success/failure here.
  avatarBox: {
    width: '28px',
    height: '28px',
    borderRadius: '50%',
    backgroundColor: 'var(--accent-ink)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    marginTop: '4px',
  },
  assistantContentCol: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    gap: '8px',
    minWidth: 0,
  },
  // --surface, not --bg: this is a card sitting on the page canvas, not the
  // canvas itself — using the page's own color here was why content inside
  // it (the KPI tiles) needed a border just to be visible at all; against
  // --surface, --surface-2 tiles read as a clear sunken step down with no
  // border needed anywhere in the chain. Elevation comes from the shadow.
  assistantCard: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
    backgroundColor: 'var(--surface)',
    borderRadius: 'var(--radius-md)',
    padding: '16px',
    boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
  },
  agentStepsWrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
    marginBottom: '4px',
    paddingLeft: '4px',
  },
  agentStepsHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    fontSize: '0.75rem',
    fontWeight: 700,
    color: 'var(--accent-ink)',
    letterSpacing: '0.05em',
  },
  agentStepsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  agentStepItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  stepTitle: {
    fontSize: '0.85rem',
    color: 'var(--ink)',
    fontWeight: 500,
  },
  stepDesc: {
    fontSize: '0.85rem',
    color: 'var(--ink-soft)',
  },
  markdownWrapper: {
    width: '100%',
    maxWidth: '100%',
    color: 'var(--ink)',
    fontSize: 'var(--text-body)',
    lineHeight: '1.7',
    wordBreak: 'break-word',
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
    borderRadius: 'var(--radius-sm)',
    backgroundColor: 'rgba(var(--accent-rgb), 0.15)',
    border: '1px solid rgba(var(--accent-rgb), 0.3)',
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
    color: 'var(--ink)',
    letterSpacing: '-0.01em',
  },
  // No marginTop here — assistantContentCol's own flex `gap` already spaces
  // every child consistently. Adding a second margin on top of that gap was
  // stacking (12px gap + 6px margin = 18px) and reading as an oversized gap
  // between the answer and its action row.
  actionToolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '12px',
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
    color: 'var(--ink-faint)',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '6px 8px',
    borderRadius: 'var(--radius-sm)',
    transition: 'background-color var(--dur-fast) var(--ease)',
  },
  actionBtnActive: {
    color: 'var(--violet)',
    backgroundColor: 'rgba(var(--violet-rgb), 0.1)',
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
    color: 'var(--ink-faint)',
    fontWeight: 500,
  },
  errorBox: {
    padding: '14px 16px',
    borderRadius: 'var(--radius-md)',
    backgroundColor: 'rgba(var(--danger-rgb), 0.08)',
    border: '1px solid rgba(var(--danger-rgb), 0.25)',
  },
  errorHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    gap: '12px',
  },
  errorTitle: {
    color: 'var(--danger)',
    fontSize: '0.95rem',
    marginBottom: '2px',
  },
  errorSub: {
    color: 'var(--border-soft)',
    fontSize: '0.85rem',
    fontFamily: 'var(--font-mono)',
  },
  emptyNotice: {
    color: 'var(--ink-faint)',
    fontSize: '0.875rem',
    fontStyle: 'italic',
    padding: '4px 0',
  },
};
