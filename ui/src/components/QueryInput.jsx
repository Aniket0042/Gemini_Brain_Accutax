import React, { useState, useRef, useEffect } from 'react';
import { Send, Sparkles, Radio } from 'lucide-react';

const PRESET_QUERIES = [
  { label: '📊 Revenue 2026', query: 'What is our total revenue this year?' },
  { label: '📄 P&L Statement', query: 'Show me the P&L statement for 2026' },
  { label: '❓ Create Invoice', query: 'How do I create a recurring invoice in Accutax?' },
  { label: '💡 AR Aging', query: 'What is accounts receivable aging?' },
  { label: '📈 Financial Health', query: 'Give me a general financial health summary of the company' },
];

export const QueryInput = ({ onSubmitQuery, isLoading, isStreaming, setIsStreaming, variant = 'compact' }) => {
  const [prompt, setPrompt] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const textareaRef = useRef(null);

  const isHero = variant === 'hero';
  const maxHeight = isHero ? 240 : 200;
  const minHeight = isHero ? 44 : 24;

  // Auto-expand textarea height based on content
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    // Reset height to auto to accurately measure scrollHeight
    textarea.style.height = 'auto';
    const scrollHeight = textarea.scrollHeight;
    const newHeight = Math.max(minHeight, Math.min(scrollHeight, maxHeight));

    textarea.style.height = `${newHeight}px`;
    textarea.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
  }, [prompt, isHero, maxHeight, minHeight]);

  const handleSubmit = (e) => {
    if (e) e.preventDefault();
    if (!prompt.trim() || isLoading) return;
    onSubmitQuery(prompt);
    setPrompt('');
    if (textareaRef.current) {
      textareaRef.current.style.height = `${minHeight}px`;
    }
  };

  const handleChipClick = (presetQuery) => {
    if (isLoading) return;
    setPrompt(presetQuery);
    if (textareaRef.current) {
      textareaRef.current.focus();
    }
  };

  return (
    <div style={isHero ? styles.heroWrapper : styles.compactWrapper}>
      <form onSubmit={handleSubmit} style={styles.form}>
        <div
          style={{
            ...styles.inputPill,
            ...(isHero ? styles.heroPill : {}),
            borderColor: isFocused ? 'rgba(129, 140, 248, 0.5)' : 'rgba(255, 255, 255, 0.12)',
            boxShadow: isFocused
              ? isHero
                ? '0 12px 36px rgba(0, 0, 0, 0.4), 0 0 24px rgba(99, 102, 241, 0.25)'
                : '0 0 16px rgba(99, 102, 241, 0.2)'
              : isHero
              ? '0 12px 36px rgba(0, 0, 0, 0.4), 0 0 20px rgba(99, 102, 241, 0.1)'
              : 'none',
          }}
        >
          <textarea
            ref={textareaRef}
            className="query-textarea"
            rows={1}
            placeholder={
              isHero
                ? "Ask any financial question (e.g. 'What is our total revenue this year?')..."
                : "Ask a financial question... (Shift + Enter for new line)"
            }
            value={prompt}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            style={{
              ...styles.textarea,
              minHeight: `${minHeight}px`,
              maxHeight: `${maxHeight}px`,
            }}
          />

          <div style={styles.rightActions}>
            <div
              style={styles.streamBadge}
              onClick={() => setIsStreaming(!isStreaming)}
              title="Toggle live SSE streaming"
            >
              <Radio size={13} color={isStreaming ? '#10b981' : '#9ca3af'} />
              <span style={styles.modelName}>
                {isStreaming ? 'Gemini 2.5 • Stream' : 'Sync Mode'}
              </span>
            </div>

            <button
              type="submit"
              style={{
                ...styles.sendCircle,
                backgroundColor: prompt.trim() ? '#818cf8' : 'rgba(255, 255, 255, 0.08)',
                boxShadow: prompt.trim() ? '0 0 12px rgba(129, 140, 248, 0.4)' : 'none',
                cursor: prompt.trim() && !isLoading ? 'pointer' : 'not-allowed',
              }}
              disabled={isLoading || !prompt.trim()}
              title={prompt.trim() ? 'Send Message (Enter)' : 'Enter your question'}
            >
              {isLoading ? (
                <Sparkles size={16} color="#ffffff" className="pulse-animation" />
              ) : (
                <Send size={15} color={prompt.trim() ? '#ffffff' : '#6b7280'} />
              )}
            </button>
          </div>
        </div>
      </form>

      {/* Preset Chips */}
      <div style={isHero ? styles.heroChipsRow : styles.compactChipsRow}>
        {PRESET_QUERIES.map((preset, idx) => (
          <button
            key={idx}
            type="button"
            style={styles.chipBtn}
            onClick={() => handleChipClick(preset.query)}
            disabled={isLoading}
          >
            <span>{preset.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
};

const styles = {
  heroWrapper: {
    width: '100%',
    maxWidth: '760px',
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
  },
  compactWrapper: {
    width: '100%',
    maxWidth: '1050px',
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  form: {
    width: '100%',
  },
  inputPill: {
    display: 'flex',
    alignItems: 'flex-end',
    gap: '12px',
    padding: '10px 14px',
    borderRadius: '22px',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    backdropFilter: 'blur(16px)',
    transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
  },
  heroPill: {
    padding: '14px 18px',
    borderRadius: '26px',
  },
  textarea: {
    flex: '1',
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#ffffff',
    fontSize: '0.95rem',
    fontFamily: 'inherit',
    resize: 'none',
    lineHeight: '1.5',
    padding: '4px 0',
    overflowY: 'hidden',
    boxSizing: 'border-box',
    transition: 'height 0.08s ease-out',
  },
  rightActions: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    flexShrink: 0,
    paddingBottom: '2px',
  },
  streamBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '5px 10px',
    borderRadius: '12px',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    cursor: 'pointer',
    userSelect: 'none',
    transition: 'background 0.2s ease, border-color 0.2s ease',
  },
  modelName: {
    fontSize: '0.75rem',
    color: '#9ca3af',
    fontWeight: 500,
  },
  sendCircle: {
    width: '34px',
    height: '34px',
    borderRadius: '50%',
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.2s ease',
    flexShrink: 0,
  },
  heroChipsRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '8px',
    flexWrap: 'wrap',
  },
  compactChipsRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    overflowX: 'auto',
    paddingBottom: '2px',
  },
  chipBtn: {
    padding: '6px 12px',
    borderRadius: '12px',
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#d1d5db',
    fontSize: '0.8rem',
    fontWeight: 500,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    transition: 'all 0.2s ease',
  },
};

