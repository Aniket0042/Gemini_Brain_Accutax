import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { 
  Sparkles, ShieldAlert, Cpu, Database, Code2, DollarSign, 
  Clock, Layers, ChevronDown, ChevronUp, Zap, Copy, Edit3 
} from 'lucide-react';

/**
 * StreamingMarkdown — Real-time token/text streaming component (typewriter effect)
 * Renders progressive text word-by-word with a glowing blinking cursor animation.
 */
const StreamingMarkdown = ({ content, speed = 10 }) => {
  const [displayedText, setDisplayedText] = useState('');
  const [isDone, setIsDone] = useState(false);

  useEffect(() => {
    if (!content) return;

    setDisplayedText('');
    setIsDone(false);

    let currentIndex = 0;
    const totalLength = content.length;

    const timer = setInterval(() => {
      currentIndex += Math.min(2, totalLength - currentIndex);
      setDisplayedText(content.slice(0, currentIndex));

      if (currentIndex >= totalLength) {
        setIsDone(true);
        clearInterval(timer);
      }
    }, speed);

    return () => clearInterval(timer);
  }, [content, speed]);

  const handleSkip = () => {
    setDisplayedText(content);
    setIsDone(true);
  };

  return (
    <div style={styles.streamingContainer}>
      {!isDone && (
        <div style={styles.streamingToolbar}>
          <div style={styles.streamingIndicator}>
            <Zap size={13} color="#818cf8" className="pulse-animation" />
            <span style={styles.streamingTextBadge}>Streaming Response Live...</span>
          </div>
          <button type="button" style={styles.skipBtn} onClick={handleSkip}>
            Skip to End ⏩
          </button>
        </div>
      )}
      <div style={styles.answerBox} className="markdown-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>
          {displayedText}
        </ReactMarkdown>
        {!isDone && <span className="streaming-cursor" />}
      </div>
    </div>
  );
};

/**
 * AssistantResponseCard — Renders individual assistant response with its own accordion state.
 */
const AssistantResponseCard = ({ responseData, isStreamingMsg, activeTenant }) => {
  const [showSql, setShowSql] = useState(false);
  const [showTrace, setShowTrace] = useState(false);

  if (!responseData) return null;

  const isError = Boolean(responseData.error || responseData.answer?.startsWith('Error'));
  const routing = responseData.routing_info;
  const tokenUsage = responseData.token_usage;

  const getPathBadge = (path) => {
    switch (path) {
      case 'gemini_direct':
        return { label: 'Gemini 2.5 Flash Direct', badgeClass: 'badge-cyan', icon: Cpu };
      case 'api_then_anthropic':
        return { label: 'REST API + Claude Bedrock', badgeClass: 'badge-amber', icon: Sparkles };
      case 'db_fallback':
        return { label: 'SQL Fallback Engine', badgeClass: 'badge-emerald', icon: Database };
      default:
        return { label: path || 'Orchestrated', badgeClass: 'badge-primary', icon: Cpu };
    }
  };

  const pathConfig = getPathBadge(routing?.path);
  const PathIcon = pathConfig.icon;

  return (
    <div style={styles.assistantCard} className="glass-panel">
      {/* Response Header & Metadata Badges */}
      <div style={styles.metaHeader}>
        <div style={styles.badgeGroup}>
          <span className={`badge ${pathConfig.badgeClass}`}>
            <PathIcon size={13} />
            {pathConfig.label}
          </span>

          {routing?.type_label && (
            <span className="badge badge-primary">
              Type {routing.type}: {routing.type_label}
            </span>
          )}

          {activeTenant?.organization_id && (
            <span className="badge badge-emerald">
              Tenant Org #{activeTenant.organization_id}
            </span>
          )}
        </div>

        {/* Token & Cost Metrics */}
        {tokenUsage && (
          <div style={styles.metricsGroup}>
            <span style={styles.metricItem} title="Total tokens processed">
              <Layers size={13} color="#818cf8" />
              <span>{tokenUsage.input_tokens + tokenUsage.output_tokens} tokens</span>
            </span>
            <span style={styles.metricItem} title="Estimated cost in USD">
              <DollarSign size={13} color="#10b981" />
              <span>${tokenUsage.cost_usd.toFixed(5)}</span>
            </span>
            <span style={styles.metricItem} title="Execution time">
              <Clock size={13} color="#f59e0b" />
              <span>{tokenUsage.elapsed_seconds}s</span>
            </span>
          </div>
        )}
      </div>

      {/* Security Error Banner or Answer content */}
      {isError ? (
        <div style={styles.errorBox}>
          <div style={styles.errorHeader}>
            <ShieldAlert size={20} color="#f43f5e" />
            <div>
              <h4 style={styles.errorTitle}>Tenant Security Isolation Boundary Triggered</h4>
              <p style={styles.errorSub}>{responseData.error || responseData.answer}</p>
            </div>
          </div>
        </div>
      ) : (
        /* If currently streaming, use StreamingMarkdown, otherwise use static Markdown */
        isStreamingMsg ? (
          <StreamingMarkdown content={responseData.answer} />
        ) : (
          <div className="markdown-body" style={styles.answerBox}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {responseData.answer}
            </ReactMarkdown>
          </div>
        )
      )}

      {/* SQL Query Inspector Accordion */}
      {responseData.sql && (
        <div style={styles.sqlInspector}>
          <button style={styles.inspectorToggle} onClick={() => setShowSql(!showSql)}>
            <div style={styles.inspectorTitle}>
              <Code2 size={15} color="#10b981" />
              <span>Executed SQL Query Inspector</span>
            </div>
            {showSql ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
          {showSql && (
            <pre style={styles.sqlCodeBlock}>
              <code>{responseData.sql}</code>
            </pre>
          )}
        </div>
      )}

      {/* Agent Trace Log Inspector */}
      {responseData.agent_trace && responseData.agent_trace.length > 0 && (
        <div style={styles.traceInspector}>
          <button style={styles.inspectorToggle} onClick={() => setShowTrace(!showTrace)}>
            <div style={styles.inspectorTitle}>
              <Layers size={15} color="#06b6d4" />
              <span>Agent Execution Trace ({responseData.agent_trace.length} steps)</span>
            </div>
            {showTrace ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
          </button>
          {showTrace && (
            <div style={styles.traceContent}>
              {responseData.agent_trace.map((step, idx) => (
                <div key={idx} style={styles.traceStepItem}>
                  <span className="badge badge-cyan">{step.agent || step.task || 'Step'}</span>
                  <span style={styles.traceStepText}>{JSON.stringify(step)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

/**
 * ResponseView — Renders full conversation list sequentially.
 */
export const ResponseView = ({ conversation, isLoading, streamLogs, activeTenant }) => {
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
              <div style={styles.bubbleActions}>
                <button 
                  style={styles.bubbleActionBtn} 
                  onClick={() => navigator.clipboard.writeText(msg.content)} 
                  title="Copy prompt"
                >
                  <Copy size={12} />
                </button>
                <button style={styles.bubbleActionBtn} title="Edit prompt">
                  <Edit3 size={12} />
                </button>
              </div>
            </div>
          );
        }

        // Assistant Turn
        const responseData = msg.responseData;
        const isStreamingMsg = msg.isStreaming;

        // If it is the active loading placeholder without data yet, show Gemini Loader
        if (isStreamingMsg && !responseData) {
          const latestLog = streamLogs && streamLogs.length > 0 ? streamLogs[streamLogs.length - 1] : null;
          return (
            <div key={idx} style={styles.loadingCard} className="glass-panel">
              <div style={styles.loadingHeader}>
                <div style={styles.geminiIconBox}>
                  <Sparkles size={24} color="#818cf8" className="gemini-sparkle-spin" />
                </div>

                <div style={styles.loadingTitleGroup}>
                  <div style={styles.titleWithDots}>
                    <h3 style={styles.loadingTitle}>
                      {latestLog?.status || 'Orchestrating AI Financial Query'}
                    </h3>
                    <div className="gemini-dots">
                      <span className="gemini-dot" />
                      <span className="gemini-dot" />
                      <span className="gemini-dot" />
                    </div>
                  </div>
                  <p style={styles.loadingSub}>Routing between Google Gemini 2.5 & Anthropic Claude Bedrock</p>
                </div>
              </div>

              <div className="gemini-shimmer-bar" />

              {streamLogs && streamLogs.length > 0 && (
                <div style={styles.streamLogBox}>
                  <div style={styles.streamLogHeader}>
                    <Layers size={14} color="#06b6d4" />
                    <span>Real-Time SSE Execution Pipeline</span>
                  </div>
                  {streamLogs.map((log, lIdx) => (
                    <div key={lIdx} style={styles.streamItem}>
                      <span className="badge badge-cyan">{log.type || 'status'}</span>
                      <span style={styles.streamText}>{log.status || JSON.stringify(log)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        }

        return (
          <AssistantResponseCard 
            key={idx}
            responseData={responseData}
            isStreamingMsg={isStreamingMsg}
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
    gap: '24px',
    paddingBottom: '40px',
  },
  userMsgWrapper: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-end',
    marginBottom: '8px',
    maxWidth: '100%',
  },
  userBubble: {
    padding: '10px 16px',
    borderRadius: '16px 16px 4px 16px',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#ffffff',
    fontSize: '0.925rem',
    fontWeight: 500,
    lineHeight: '1.4',
    textAlign: 'left',
    maxWidth: '75%',
    wordBreak: 'break-word',
  },
  bubbleActions: {
    display: 'flex',
    gap: '8px',
    marginTop: '6px',
    paddingRight: '4px',
  },
  bubbleActionBtn: {
    background: 'none',
    border: 'none',
    color: '#9ca3af',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '4px',
    borderRadius: '4px',
    transition: 'all 0.2s ease',
    '&:hover': {
      backgroundColor: 'rgba(255, 255, 255, 0.08)',
      color: '#ffffff',
    },
  },
  assistantCard: {
    padding: '24px',
    marginBottom: '8px',
  },
  loadingCard: {
    padding: '24px',
    marginBottom: '8px',
  },
  loadingHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '16px',
  },
  geminiIconBox: {
    width: '46px',
    height: '46px',
    borderRadius: '14px',
    backgroundColor: 'rgba(99, 102, 241, 0.14)',
    border: '1px solid rgba(99, 102, 241, 0.3)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    boxShadow: '0 0 20px rgba(99, 102, 241, 0.25)',
  },
  loadingTitleGroup: {
    display: 'flex',
    flexDirection: 'column',
    textAlign: 'left',
  },
  titleWithDots: {
    display: 'flex',
    alignItems: 'center',
  },
  loadingTitle: {
    fontSize: '1.05rem',
    fontWeight: 700,
    color: '#f3f4f6',
  },
  loadingSub: {
    fontSize: '0.8rem',
    color: '#9ca3af',
    marginTop: '2px',
  },
  streamLogBox: {
    marginTop: '16px',
    padding: '14px',
    borderRadius: '10px',
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
  },
  streamLogHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    fontSize: '0.8rem',
    fontWeight: 600,
    color: '#9ca3af',
    marginBottom: '10px',
  },
  streamItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    fontSize: '0.85rem',
    marginBottom: '6px',
  },
  streamText: {
    color: '#e5e7eb',
  },
  metaHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingBottom: '16px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    marginBottom: '18px',
    flexWrap: 'wrap',
    gap: '12px',
  },
  badgeGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  metricsGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '14px',
  },
  metricItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    fontSize: '0.8rem',
    color: '#9ca3af',
    fontWeight: 500,
  },
  streamingContainer: {
    position: 'relative',
  },
  streamingToolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: '10px',
    padding: '6px 12px',
    borderRadius: '8px',
    backgroundColor: 'rgba(99, 102, 241, 0.08)',
    border: '1px solid rgba(99, 102, 241, 0.2)',
  },
  streamingIndicator: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
  },
  streamingTextBadge: {
    fontSize: '0.78rem',
    fontWeight: 600,
    color: '#a5b4fc',
  },
  skipBtn: {
    background: 'none',
    border: 'none',
    color: '#9ca3af',
    fontSize: '0.75rem',
    cursor: 'pointer',
    fontWeight: 600,
  },
  answerBox: {
    padding: '4px 0',
  },
  errorBox: {
    padding: '16px',
    borderRadius: '12px',
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
    fontSize: '1rem',
    marginBottom: '4px',
  },
  errorSub: {
    color: '#e5e7eb',
    fontSize: '0.875rem',
    fontFamily: 'var(--font-mono)',
  },
  sqlInspector: {
    marginTop: '16px',
    borderRadius: '10px',
    backgroundColor: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(16, 185, 129, 0.2)',
    overflow: 'hidden',
  },
  traceInspector: {
    marginTop: '12px',
    borderRadius: '10px',
    backgroundColor: 'rgba(0, 0, 0, 0.3)',
    border: '1px solid rgba(6, 182, 212, 0.2)',
    overflow: 'hidden',
  },
  inspectorToggle: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '12px 16px',
    background: 'none',
    border: 'none',
    color: '#d1d5db',
    cursor: 'pointer',
    fontSize: '0.85rem',
    fontWeight: 600,
  },
  inspectorTitle: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
  },
  sqlCodeBlock: {
    padding: '14px',
    backgroundColor: '#0a0d14',
    color: '#10b981',
    fontFamily: 'var(--font-mono)',
    fontSize: '0.825rem',
    overflowX: 'auto',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
  },
  traceContent: {
    padding: '12px 16px',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  traceStepItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    fontSize: '0.8rem',
  },
  traceStepText: {
    fontFamily: 'var(--font-mono)',
    color: '#9ca3af',
  },
};
