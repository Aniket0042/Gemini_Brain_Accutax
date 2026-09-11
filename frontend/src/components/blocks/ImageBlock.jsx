import React, { useEffect, useState } from 'react';
import { X, ZoomIn } from 'lucide-react';

/**
 * ImageBlock — {url, alt, caption?}. No producer emits this block yet (the
 * plan names it as a slot for something like an exported report thumbnail),
 * so this is a renderer only — it exists so the day a backend path does
 * return one, nothing on the frontend needs to change.
 */
export function ImageBlock({ block }) {
  const [open, setOpen] = useState(false);
  // Tracked per block.url so a different image in a later message starts
  // from a clean slate instead of inheriting a previous failure.
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [block.url]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  // A failed image renders nothing rather than a placeholder — no visible
  // gap, no broken-icon, no box calling attention to the failure.
  if (!block.url || failed) return null;

  return (
    <>
      <button type="button" style={styles.thumbWrap} onClick={() => setOpen(true)} title="Click to enlarge">
        <img
          src={block.url}
          alt={block.alt || ''}
          style={styles.thumb}
          onError={() => setFailed(true)}
        />
        <span style={styles.zoomHint}><ZoomIn size={14} color="#fff" /></span>
      </button>
      {block.caption && <div style={styles.caption}>{block.caption}</div>}

      {open && (
        <div style={styles.overlay} onClick={() => setOpen(false)} role="dialog" aria-modal="true">
          <button type="button" style={styles.closeBtn} onClick={() => setOpen(false)} title="Close">
            <X size={20} color="#fff" />
          </button>
          <img
            src={block.url}
            alt={block.alt || ''}
            style={styles.fullImg}
            onClick={(e) => e.stopPropagation()}
            onError={() => {
              setFailed(true);
              setOpen(false);
            }}
          />
        </div>
      )}
    </>
  );
}

const styles = {
  thumbWrap: {
    position: 'relative',
    display: 'inline-block',
    padding: 0,
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    overflow: 'hidden',
    cursor: 'zoom-in',
    background: 'none',
    maxWidth: '100%',
  },
  thumb: {
    display: 'block',
    maxWidth: '100%',
    maxHeight: '320px',
  },
  zoomHint: {
    position: 'absolute',
    bottom: '8px',
    right: '8px',
    backgroundColor: 'rgba(0,0,0,0.55)',
    borderRadius: 'var(--radius-sm)',
    padding: '4px 6px',
    display: 'flex',
  },
  caption: {
    fontSize: 'var(--text-xs)',
    color: 'var(--ink-faint)',
    marginTop: '6px',
    fontStyle: 'italic',
  },
  overlay: {
    position: 'fixed',
    inset: 0,
    backgroundColor: 'rgba(0,0,0,0.8)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 200,
    cursor: 'zoom-out',
  },
  fullImg: {
    maxWidth: '90vw',
    maxHeight: '90vh',
    borderRadius: 'var(--radius-sm)',
    cursor: 'default',
  },
  closeBtn: {
    position: 'absolute',
    top: '20px',
    right: '20px',
    background: 'rgba(255,255,255,0.1)',
    border: 'none',
    borderRadius: '50%',
    width: '36px',
    height: '36px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    cursor: 'pointer',
  },
};
