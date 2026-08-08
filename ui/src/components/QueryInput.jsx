import React, { useState } from 'react';
import { Send, Sparkles, Radio, HelpCircle, FileText, BarChart3, BookOpen, TrendingUp, Plus } from 'lucide-react';

const PRESET_QUERIES = [
  { label: '📊 Revenue 2026', query: 'What is our total revenue this year?' },
  { label: '📄 P&L Statement', query: 'Show me the P&L statement for 2026' },
  { label: '❓ Create Invoice', query: 'How do I create a recurring invoice in Accutax?' },
  { label: '💡 AR Aging', query: 'What is accounts receivable aging?' },
  { label: '📈 Financial Health', query: 'Give me a general financial health summary of the company' },
];

export const QueryInput = ({ onSubmitQuery, isLoading, isStreaming, setIsStreaming, variant = 'compact' }) => {
  const [prompt, setPrompt] = useState('');

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!prompt.trim() || isLoading) return;
    onSubmitQuery(prompt);
    setPrompt('');
  };

  const handleChipClick = (presetQuery) => {
    if (isLoading) return;
    onSubmitQuery(presetQuery);
    setPrompt('');
  };

  const isHero = variant === 'hero';

  return (
    <div style={isHero ? styles.heroWrapper : styles.compactWrapper}>
      <form onSubmit={handleSubmit} style={styles.form}>
        <div style={{ ...styles.inputPill, ...(isHero ? styles.heroPill : {}) }}>
          <textarea
            rows={isHero ? 2 : 1}
            placeholder={isHero ? "Ask any financial question (e.g. 'What is our total revenue this year?')..." : "Write a message..."}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            style={styles.textarea}
          />

          <div style={styles.rightActions}>
            <div style={styles.streamBadge} onClick={() => setIsStreaming(!isStreaming)} title="Toggle live SSE streaming">
              <Radio size={13} color={isStreaming ? '#10b981' : '#9ca3af'} />
              <span style={styles.modelName}>{isStreaming ? 'Gemini 2.5 • Stream' : 'Sync Mode'}</span>
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
    alignItems: 'center',
    gap: '12px',
    padding: '8px 12px 8px 14px',
    borderRadius: '20px',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    backdropFilter: 'blur(16px)',
    transition: 'all 0.2s ease',
  },
  heroPill: {
    padding: '14px 16px',
    borderRadius: '24px',
    boxShadow: '0 12px 36px rgba(0, 0, 0, 0.4), 0 0 20px rgba(99, 102, 241, 0.1)',
  },
  plusBtn: {
    background: 'none',
    border: 'none',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '4px',
    borderRadius: '50%',
    transition: 'background 0.2s ease',
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
    lineHeight: '1.4',
    padding: '4px 0',
  },
  rightActions: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    flexShrink: 0,
  },
  streamBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    padding: '4px 10px',
    borderRadius: '12px',
    backgroundColor: 'rgba(255, 255, 255, 0.05)',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    cursor: 'pointer',
    userSelect: 'none',
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
