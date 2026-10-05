import React, { useState } from 'react';
import { BadgeCheck, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react';

/**
 * FtaSourcesBlock — the official Federal Tax Authority documents a UAE VAT answer cites
 * (block type "fta_sources", built by the backend's vat_kb.answer.sources_block).
 *
 * A compact bar under the answer: what kind of sources these are, the jurisdiction, and a
 * toggle that opens one card per document. The numbers match the [n] chips in the answer.
 */
export function FtaSourcesBlock({ block }) {
  const [open, setOpen] = useState(false);
  const listId = `fta-sources-${React.useId()}`;
  const sources = Array.isArray(block?.sources) ? block.sources : [];
  if (!sources.length) return null;

  return (
    <div className="fta-sources">
      <div className="fta-sources-bar">
        <span className="fta-badge fta-badge-official">
          <BadgeCheck size={12} aria-hidden="true" /> Official FTA sources
        </span>
        {block.jurisdiction && <span className="fta-badge">{block.jurisdiction}</span>}
        <button
          type="button"
          className="fta-sources-toggle"
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((v) => !v)}
        >
          {sources.length} {sources.length === 1 ? 'source' : 'sources'}
          {open ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}
        </button>
      </div>

      {open && (
        <ol className="fta-sources-list" id={listId}>
          {sources.map((s) => (
            <li key={s.n} className="fta-source-card">
              <div className="fta-source-head">
                <span className="fta-source-n">[{s.n}]</span>
                <a className="fta-source-title" href={s.url} target="_blank" rel="noopener noreferrer">{s.title}</a>
              </div>
              <div className="fta-source-meta">
                <span className="fta-badge">{s.kind}</span>
                {s.issued && <span>Issued {s.issued}</span>}
                {s.effective && <span>Effective {s.effective}</span>}
              </div>
              {s.excerpt && <p className="fta-source-excerpt">{s.excerpt}</p>}
              <a className="fta-source-link" href={s.url} target="_blank" rel="noopener noreferrer">
                Open source document <ExternalLink size={12} aria-hidden="true" />
              </a>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
