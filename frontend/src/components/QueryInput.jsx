import React, { useState, useRef, useEffect } from 'react';
import { ArrowUp, Sparkles, Radio, Paperclip } from 'lucide-react';
import { ModelMenu } from './PolicyPicker';

export const QueryInput = ({
  onSubmitQuery,
  isLoading,
  isStreaming,
  setIsStreaming,
  variant = 'compact',
  catalog = null,
  catalogState = 'ready',
  onRetryCatalog = () => {},
  model = 'auto',
  onModelChange = () => {},
  brief = false,
  onBriefChange = () => {},
}) => {
  const [prompt, setPrompt] = useState('');
  const [isFocused, setIsFocused] = useState(false);
  const textareaRef = useRef(null);

  const isHero = variant === 'hero';
  const maxHeight = isHero ? 240 : 200;
  // Single source of vertical space is the pill's own padding (below) —
  // these are just the textarea's natural single-line floor, not a second
  // layer of padding stacked on top of it.
  const minHeight = isHero ? 26 : 23;

  // Auto-expand textarea height based on content
  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    // Measuring scrollHeight on an empty textarea also measures its
    // *placeholder* text wrapping, which grew the pill even with nothing
    // typed once the composer got narrower (model/effort chips now share
    // its width). Skip the measurement entirely when there's no value —
    // minHeight already is the correct empty-state height.
    if (!prompt) {
      textarea.style.height = `${minHeight}px`;
      textarea.style.overflowY = 'hidden';
      return;
    }

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
    onSubmitQuery(prompt, { brief });
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
            borderColor: isFocused ? 'var(--border-strong)' : 'var(--border)',
            boxShadow: isFocused ? '0 0 0 3px rgba(var(--ink-rgb), 0.06)' : '0 2px 8px rgba(0,0,0,0.05)',
          }}
        >
          <button type="button" style={styles.attachBtn} title="Attach file">
            <Paperclip size={18} color="var(--ink-soft)" />
          </button>
          
          <textarea
            ref={textareaRef}
            className="query-textarea"
            rows={1}
            placeholder="Ask AccuTax AI…"
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

          {/* Model selection lives inside the same pill as the input, right up
              against the send button — one bordered object, not a control
              row floating below it (see the UI rebuild plan, §3, Fig. 2).
              Effort is no longer user-selectable — every query runs at the
              highest tier its model supports. */}
          <div className="pp-inline" style={styles.rightActions}>
            <button
              type="button"
              className={`brief-pill${brief ? ' is-on' : ''}`}
              aria-pressed={brief}
              title={brief ? 'Brief answers on — click for full answers' : 'Keep answers brief'}
              onClick={() => onBriefChange(!brief)}
            >
              Brief
            </button>
            <ModelMenu
              catalog={catalog}
              catalogState={catalogState}
              onRetryCatalog={onRetryCatalog}
              model={model}
              onModelChange={onModelChange}
            />
            <span style={styles.divider} />
            <button
              type="submit"
              style={{
                ...styles.sendCircle,
                backgroundColor: prompt.trim() ? 'var(--accent)' : 'var(--surface-2)',
                color: prompt.trim() ? '#ffffff' : 'var(--ink-faint)',
                cursor: prompt.trim() && !isLoading ? 'pointer' : 'default',
                boxShadow: prompt.trim() ? '0 2px 8px var(--accent-glow)' : 'none',
                opacity: prompt.trim() || isLoading ? 1 : 0.6,
              }}
              disabled={isLoading || !prompt.trim()}
              title={prompt.trim() ? 'Send Message (Enter)' : 'Enter your question'}
            >
              {isLoading ? (
                <Sparkles size={16} color="#ffffff" className="pulse-animation" />
              ) : (
                <ArrowUp
                  size={16}
                  strokeWidth={2.5}
                  color={prompt.trim() ? '#ffffff' : 'var(--ink-faint)'}
                />
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
    maxWidth: 'var(--content-width)',
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },
  form: {
    width: '100%',
  },
  // A pill's radius should always be half its own height regardless of how
  // tall the textarea grows — var(--radius-pill) (9999px) does that for
  // free, which is why this no longer carries an explicit pixel radius.
  inputPill: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    padding: '9px 18px',
    borderRadius: 'var(--radius-pill)',
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border-strong)',
    transition: 'border-color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease)',
  },
  heroPill: {
    padding: '13px 22px',
  },
  attachBtn: {
    background: 'transparent',
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    padding: '4px',
    color: 'var(--ink-faint)',
    borderRadius: '50%',
    transition: 'color var(--dur-fast) var(--ease)',
  },
  textarea: {
    flex: '1',
    background: 'transparent',
    border: 'none',
    outline: 'none',
    color: 'var(--ink)',
    fontSize: 'var(--text-body)',
    fontFamily: 'inherit',
    resize: 'none',
    lineHeight: '1.5',
    padding: 0,
    overflowY: 'hidden',
    boxSizing: 'border-box',
    transition: 'height var(--dur-fast) var(--ease)',
  },
  rightActions: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    flexShrink: 0,
  },
  divider: {
    width: '1px',
    height: '20px',
    backgroundColor: 'var(--border)',
    margin: '0 2px',
    flexShrink: 0,
  },
  // Deliberately the --control-h tier rather than --control-h-sm: the send
  // Sized to match the pp-trigger dropdown chips (~28px) so all
  // controls in the input pill share a consistent visual height.
  sendCircle: {
    width: '28px',
    height: '28px',
    borderRadius: '50%',
    border: 'none',
    padding: 0,
    margin: 0,
    boxSizing: 'border-box',
    outline: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    transition: 'background-color var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease), opacity var(--dur-fast) var(--ease)',
    flexShrink: 0,
  }
};

