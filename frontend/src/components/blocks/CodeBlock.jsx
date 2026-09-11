import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';

/**
 * CodeBlock — language label + copy button header over a code body. Used both
 * as a registered block type (`{type:'code', language, content}`) and directly
 * by ResponseView for the executed SQL trace, which the backend still returns
 * as a plain `sql` string field rather than a block — wrapping it here is
 * what the UI rebuild plan calls "replacing the separate Show SQL toggle".
 */
export function CodeBlock({ block }) {
  return <CodeChrome language={block.language || 'sql'} content={block.content || ''} />;
}

export function CodeChrome({ language, content, title }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (!content) return;
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (!content) return null;

  return (
    <div style={styles.wrap}>
      <div style={styles.header}>
        <span style={styles.lang}>{title || language}</span>
        <button type="button" style={styles.copyBtn} onClick={handleCopy} title="Copy">
          {copied ? (
            <>
              <Check size={12} color="var(--success)" />
              <span>Copied</span>
            </>
          ) : (
            <>
              <Copy size={12} />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre style={styles.body}>
        <code>{content}</code>
      </pre>
    </div>
  );
}

const styles = {
  wrap: {
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius-sm)',
    overflow: 'hidden',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '7px 12px',
    backgroundColor: 'var(--surface-2)',
    borderBottom: '1px solid var(--border)',
  },
  lang: {
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: 'var(--text-xs)',
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
    color: 'var(--ink-faint)',
    fontWeight: 600,
  },
  copyBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    background: 'none',
    border: 'none',
    color: 'var(--ink-faint)',
    fontSize: 'var(--text-xs)',
    cursor: 'pointer',
    padding: '2px 4px',
  },
  body: {
    margin: 0,
    padding: '12px 14px',
    overflowX: 'auto',
    fontFamily: 'var(--font-mono, monospace)',
    fontSize: 'var(--text-sm)',
    lineHeight: 1.6,
    color: 'var(--ink-soft)',
    backgroundColor: 'var(--bg)',
  },
};
