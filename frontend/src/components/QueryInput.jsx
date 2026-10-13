import React, { useState, useRef, useLayoutEffect } from 'react';
import { ArrowUp, Square, Paperclip } from 'lucide-react';
import { ModelPicker } from './ModelPicker';
import { AttachmentChips, StarterPrompts } from './AttachmentChips';
import { useAttachments } from '../hooks/useAttachments';
import { attachmentsToSend, starterPrompts, DEFAULT_ACCEPT } from '../services/attachments';

/**
 * QueryInput — the chat composer.
 *
 * Two shapes, like ChatGPT's composer:
 * - collapsed: one pill row — attach, text, controls — while the question fits
 *   on one line;
 * - expanded: a rounded rectangle with the text on top and the controls in a
 *   row below, once the text wraps or has a line break (Shift+Enter).
 * It collapses again when the text fits on one line or is cleared.
 *
 * Both shapes are one CSS grid with the same three children; only the grid
 * areas change. The textarea is never re-mounted, so switching shape keeps
 * the cursor position and focus.
 *
 * Files: the paperclip, drag and drop onto the composer, and pasting an image attach files to the
 * question (docs/DOCUMENT_UPLOAD_PLAN.md). Each is uploaded at once and read in the background; the
 * question can be sent when every file is ready. The paperclip is hidden when uploads are off.
 */
export const QueryInput = ({
  onSubmitQuery,
  onStop = () => {},
  isLoading,
  variant = 'compact',
  brief = false,
  onBriefChange = () => {},
  catalog = null,
  model = '',
  onModelChange = () => {},
  uploadConfig = null,
  sessionId = null,
  orgIds = [],
  token = '',
  onPreviewFile = () => {},
}) => {
  const [prompt, setPrompt] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);
  const attach = useAttachments({ config: uploadConfig, sessionId, orgIds, token });
  const [isFocused, setIsFocused] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const textareaRef = useRef(null);
  // Width the text has in the collapsed row, measured while collapsed: the
  // expanded text area is wider, so fitting is always judged against the row.
  const rowWidthRef = useRef(0);
  const canvasRef = useRef(null);

  const isHero = variant === 'hero';
  const maxHeight = isHero ? 260 : 220;

  const measureText = (text, el) => {
    if (!canvasRef.current) canvasRef.current = document.createElement('canvas');
    const ctx = canvasRef.current.getContext('2d');
    ctx.font = window.getComputedStyle(el).font;
    return ctx.measureText(text).width;
  };

  useLayoutEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const lineHeight = parseFloat(window.getComputedStyle(textarea).lineHeight) || 24;

    if (!expanded) {
      rowWidthRef.current = textarea.clientWidth;
      textarea.style.height = `${lineHeight}px`;
      textarea.style.overflowY = 'hidden';
      // Expand on a line break, or once the text no longer fits the row.
      if (prompt && (prompt.includes('\n') || textarea.scrollHeight > lineHeight + 2)) {
        setExpanded(true);
      }
      return;
    }

    const fitsRow = !prompt.includes('\n') && measureText(prompt, textarea) <= rowWidthRef.current - 6;
    if (!prompt || fitsRow) {
      setExpanded(false);
      return;
    }
    textarea.style.height = 'auto';
    const scrollHeight = textarea.scrollHeight;
    textarea.style.height = `${Math.max(lineHeight, Math.min(scrollHeight, maxHeight))}px`;
    textarea.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
  }, [prompt, expanded, maxHeight]);

  const handleSubmit = (e) => {
    if (e) e.preventDefault();
    if (!prompt.trim() || isLoading || attach.blocked) return;
    const files = attachmentsToSend(attach.items);
    onSubmitQuery(prompt, files.ids.length ? { brief, attachments: files.ids, attachmentChips: files.chips } : { brief });
    setPrompt('');
    attach.clear();
  };

  const hasText = Boolean(prompt.trim());
  const hasTiles = attach.items.length > 0;   // files in the box: its shape changes
  const canSend = hasText && !attach.blocked;
  const starters = hasText ? [] : starterPrompts(attach.items);

  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
  const dragHandlers = attach.enabled ? {
    onDragEnter: (e) => { if (hasFiles(e)) { e.preventDefault(); setIsDragging(true); } },
    onDragOver: (e) => { if (hasFiles(e)) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; } },
    onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setIsDragging(false); },
    onDrop: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      setIsDragging(false);
      attach.addFiles(e.dataTransfer.files);
    },
  } : {};

  // A pasted screenshot is a file; pasted text (even with a picture beside it, as from Excel) stays text.
  const handlePaste = (e) => {
    if (!attach.enabled) return;
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length === 0 || e.clipboardData.getData('text/plain')) return;
    e.preventDefault();
    attach.addFiles(files);
  };

  return (
    <div style={isHero ? styles.heroWrapper : styles.compactWrapper}>
      <form onSubmit={handleSubmit} style={styles.form} {...dragHandlers}>
        <StarterPrompts prompts={starters} onPick={(text) => { setPrompt(text); textareaRef.current?.focus(); }} />
        {attach.message && (
          <div className="attach-message" role="alert">
            <span>{attach.message}</span>
            <button type="button" onClick={attach.dismissMessage} aria-label="Dismiss">×</button>
          </div>
        )}
        <div
          className={`composer${expanded ? ' is-expanded' : ''}${isDragging ? ' is-dragging' : ''}`}
          style={{
            ...styles.composer,
            ...(expanded ? styles.composerExpanded : styles.composerCollapsed),
            ...(isHero && !expanded ? styles.heroCollapsed : {}),
            ...(hasTiles ? withFilesStyle(expanded) : {}),
            borderColor: isFocused ? 'var(--border-strong)' : 'var(--border)',
            boxShadow: isFocused ? '0 0 0 3px rgba(var(--ink-rgb), 0.06)' : '0 2px 8px rgba(0,0,0,0.05)',
          }}
          onClick={(e) => {
            // Clicking the empty part of the box focuses the text, as in chat apps.
            if (e.target === e.currentTarget) textareaRef.current?.focus();
          }}
        >
          {hasTiles && (
            <div style={{ gridArea: 'files', minWidth: 0 }}>
              <AttachmentChips items={attach.items} token={token} onRemove={attach.remove} onOpen={onPreviewFile} />
            </div>
          )}
          {attach.enabled && (
            <>
              <button
                type="button"
                style={{ ...styles.attachBtn, gridArea: 'attach' }}
                title={`Attach a file (PDF, photo, Word, Excel or CSV; up to ${uploadConfig.max_files || 3} files)`}
                aria-label="Attach a file"
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip size={17} color="var(--ink-soft)" />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                hidden
                accept={(uploadConfig.accept && uploadConfig.accept.length ? uploadConfig.accept : DEFAULT_ACCEPT).join(',')}
                onChange={(e) => {
                  attach.addFiles(e.target.files);
                  e.target.value = ''; // the same file can be chosen again after it was removed
                }}
              />
            </>
          )}

          <textarea
            ref={textareaRef}
            className="query-textarea"
            rows={1}
            placeholder="Ask AccuTax AI…"
            value={prompt}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            onChange={(e) => setPrompt(e.target.value)}
            onPaste={handlePaste}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            style={{
              ...styles.textarea,
              gridArea: 'text',
              maxHeight: `${maxHeight}px`,
              ...(expanded ? styles.textareaExpanded : {}),
            }}
          />

          {/* Effort is not user-selectable: every query runs at the highest
              tier its model supports. */}
          <div className="pp-inline" style={{ ...styles.rightActions, gridArea: 'actions' }}>
            <ModelPicker catalog={catalog} model={model} onModelChange={onModelChange} disabled={isLoading} />
            <button
              type="button"
              className={`brief-pill${brief ? ' is-on' : ''}`}
              aria-pressed={brief}
              title={brief ? 'Brief answers on — click for full answers' : 'Keep answers brief'}
              onClick={() => onBriefChange(!brief)}
            >
              Brief
            </button>
            <span style={styles.divider} />
            {isLoading ? (
              // While a query runs, the send button becomes Stop.
              <button
                type="button"
                style={{ ...styles.sendCircle, ...styles.stopCircle }}
                onClick={onStop}
                title="Stop Answering"
                aria-label="Stop Answering"
              >
                <Square size={11} fill="currentColor" strokeWidth={0} />
              </button>
            ) : (
              <button
                type="submit"
                style={{
                  ...styles.sendCircle,
                  backgroundColor: canSend ? 'var(--accent)' : 'var(--surface-2)',
                  color: canSend ? '#ffffff' : 'var(--ink-faint)',
                  cursor: canSend ? 'pointer' : 'default',
                  boxShadow: canSend ? '0 2px 8px var(--accent-glow)' : 'none',
                  opacity: canSend ? 1 : 0.6,
                }}
                disabled={!canSend}
                title={attach.blocked && hasText ? attach.blockedReason : hasText ? 'Send Message (Enter)' : 'Enter your question'}
              >
                <ArrowUp size={16} strokeWidth={2.5} color={canSend ? '#ffffff' : 'var(--ink-faint)'} />
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
};

/** With files in it the box is a rounded rectangle with the tiles in a row on top, as in a chat app. */
const withFilesStyle = (expanded) => ({
  gridTemplateAreas: expanded
    ? '"files files files" "text text text" "attach . actions"'
    : '"files files files" "attach text actions"',
  borderRadius: '24px',
  padding: '12px 8px 8px 10px',
});

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
  composer: {
    display: 'grid',
    gridTemplateColumns: 'auto 1fr auto',
    alignItems: 'center',
    columnGap: '8px',
    rowGap: '6px',
    backgroundColor: 'var(--surface)',
    border: '1px solid var(--border-strong)',
    cursor: 'text',
    transition: 'border-color var(--dur-fast) var(--ease), box-shadow var(--dur-fast) var(--ease), border-radius var(--dur-fast) var(--ease)',
  },
  // One line: a pill with everything in one row.
  composerCollapsed: {
    gridTemplateAreas: '"attach text actions"',
    padding: '8px 8px 8px 10px',
    borderRadius: '9999px',
  },
  heroCollapsed: {
    padding: '10px 10px 10px 12px',
  },
  // Long text or a line break: text on top, controls in a row below.
  composerExpanded: {
    gridTemplateAreas: '"text text text" "attach . actions"',
    padding: '12px 8px 8px 10px',
    borderRadius: '24px',
  },
  attachBtn: {
    background: 'transparent',
    border: 'none',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
    padding: '6px',
    color: 'var(--ink-faint)',
    borderRadius: '50%',
    transition: 'color var(--dur-fast) var(--ease)',
  },
  textarea: {
    width: '100%',
    minWidth: 0,
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
  },
  textareaExpanded: {
    padding: '0 6px 0 6px',
  },
  rightActions: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    flexShrink: 0,
    cursor: 'default',
  },
  divider: {
    width: '1px',
    height: '20px',
    backgroundColor: 'var(--border)',
    margin: '0 2px',
    flexShrink: 0,
  },
  sendCircle: {
    width: '32px',
    height: '32px',
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
  },
  stopCircle: {
    backgroundColor: 'var(--ink)',
    color: 'var(--surface)',
    cursor: 'pointer',
  },
};
