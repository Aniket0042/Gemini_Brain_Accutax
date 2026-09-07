import React, { useState, useRef, useEffect } from 'react';
import { Send, Sparkles, Radio, Paperclip } from 'lucide-react';

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

  return (
    <div style={isHero ? styles.heroWrapper : styles.compactWrapper}>
      <form onSubmit={handleSubmit} style={styles.form}>
        <div
          style={{
            ...styles.inputPill,
            ...(isHero ? styles.heroPill : {}),
            borderColor: isFocused ? 'rgba(14, 138, 117, 0.5)' : '#e2e8f0',
            boxShadow: isFocused ? '0 0 15px rgba(14, 138, 117, 0.15)' : '0 2px 8px rgba(0,0,0,0.05)',
          }}
        >
          <button type="button" style={styles.attachBtn} title="Attach file">
            <Paperclip size={18} color="#64748b" />
          </button>
          
          <textarea
            ref={textareaRef}
            className="query-textarea"
            rows={1}
            placeholder="Ask AccuTax AI anything... or describe invoice details to create one"
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
            <button
              type="submit"
              style={{
                ...styles.sendCircle,
                backgroundColor: prompt.trim() ? 'rgba(10, 92, 82, 0.1)' : '#f1f5f9',
                cursor: prompt.trim() && !isLoading ? 'pointer' : 'not-allowed',
              }}
              disabled={isLoading || !prompt.trim()}
              title={prompt.trim() ? 'Send Message (Enter)' : 'Enter your question'}
            >
              {isLoading ? (
                <Sparkles size={16} color="#0A5C52" className="pulse-animation" />
              ) : (
                <Send size={15} color={prompt.trim() ? '#0A5C52' : '#94a3b8'} />
              )}
            </button>
          </div>
        </div>
      </form>
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
    padding: '8px 16px',
    borderRadius: '32px',
    backgroundColor: '#ffffff',
    border: '1px solid #cbd5e1',
    transition: 'border-color 0.2s ease, box-shadow 0.2s ease',
  },
  heroPill: {
    padding: '12px 20px',
    borderRadius: '36px',
  },
  attachBtn: {
    background: 'transparent',
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    padding: '4px',
    color: '#94a3b8',
    borderRadius: '50%',
    transition: 'color 0.2s',
  },
  textarea: {
    flex: '1',
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: '#1e293b',
    fontSize: '0.95rem',
    fontFamily: 'inherit',
    resize: 'none',
    lineHeight: '1.5',
    padding: '8px 0',
    overflowY: 'hidden',
    boxSizing: 'border-box',
    transition: 'height 0.08s ease-out',
  },
  rightActions: {
    display: 'flex',
    alignItems: 'center',
    flexShrink: 0,
  },
  sendCircle: {
    width: '36px',
    height: '36px',
    borderRadius: '50%',
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'all 0.2s ease',
    flexShrink: 0,
  }
};

