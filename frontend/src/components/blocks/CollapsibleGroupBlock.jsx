import React, { useState } from 'react';
import { ChevronRight } from 'lucide-react';
// Circular with BlockRenderer, which is safe: it is only read at render time.
import { BlockRenderer } from './BlockRenderer';

/**
 * CollapsibleGroupBlock — per-organization details of a multi-org answer.
 *
 * The answer's summary sits above; each organization's own tables sit here,
 * closed by default, so a 10-org answer stays one screen tall. Sections
 * render their blocks with the same BlockRenderer as the answer itself.
 *
 * block: { title, default_open, sections_open, sections: [{ title, subtitle, blocks }] }
 */
export function CollapsibleGroupBlock({ block, verification, onOpenCanvas, token }) {
  const sections = Array.isArray(block.sections) ? block.sections : [];
  const [groupOpen, setGroupOpen] = useState(Boolean(block.default_open));
  const [openSections, setOpenSections] = useState(() => (
    block.sections_open ? Object.fromEntries(sections.map((_, i) => [i, true])) : {}
  ));
  if (sections.length === 0) return null;

  const toggle = (i) => setOpenSections((prev) => ({ ...prev, [i]: !prev[i] }));

  return (
    <div className="cg-wrap">
      <button
        type="button"
        className="cg-head"
        aria-expanded={groupOpen}
        onClick={() => setGroupOpen((v) => !v)}
      >
        <ChevronRight size={14} className={`cg-caret ${groupOpen ? 'is-open' : ''}`} />
        <span className="cg-title">{block.title || 'Details'}</span>
        <span className="cg-count">{sections.length}</span>
      </button>
      {groupOpen && (
        <div className="cg-sections">
          {sections.map((section, i) => {
            const open = Boolean(openSections[i]);
            const hasBlocks = Array.isArray(section.blocks) && section.blocks.length > 0;
            return (
              <div key={i} className="cg-section">
                <button
                  type="button"
                  className="cg-section-head"
                  aria-expanded={open}
                  disabled={!hasBlocks}
                  onClick={() => toggle(i)}
                >
                  <ChevronRight size={13} className={`cg-caret ${open ? 'is-open' : ''}`} />
                  <span className="cg-section-title">{section.title}</span>
                  {section.subtitle && <span className="cg-section-sub">{section.subtitle}</span>}
                </button>
                {open && hasBlocks && (
                  <div className="cg-section-body">
                    <BlockRenderer
                      blocks={section.blocks}
                      verification={verification}
                      onOpenCanvas={onOpenCanvas}
                      token={token}
                    />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
