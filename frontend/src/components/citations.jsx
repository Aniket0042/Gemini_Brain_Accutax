import React from 'react';
import { ExternalLink } from 'lucide-react';

/**
 * Inline citations for answers backed by the UAE VAT knowledge base.
 *
 * The backend sends an `fta_sources` block listing the cited FTA documents. The answer text
 * marks citations as [1], [2][3] or [1, 2]. linkCitations() turns each marker that matches a
 * listed source into a markdown link, and the markdown renderer's `a` override
 * (citationLinkRenderer) draws that link as a chip: the first citation of a source shows its
 * title, later ones only its number. A chip opens the document; hovering it shows a summary.
 */

export const CITE_PREFIX = '#fta-cite-';
const REPEAT = '.r';

/** The cited-source list of a response, or null when the answer has none. */
export function citationSources(blocks) {
  if (!Array.isArray(blocks)) return null;
  const block = blocks.find((b) => b?.type === 'fta_sources');
  return block && Array.isArray(block.sources) && block.sources.length ? block.sources : null;
}

const MARKER = /\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\](?!\()|【(\d{1,2})】/g;

/**
 * Replace citation markers with "[n](#fta-cite-n)" links. A source cited again later gets a
 * compact chip, and the same source repeated within one marker group ("[1][1]") is shown once.
 * Markers with no matching source stay as text.
 */
export function linkCitations(text, sources) {
  if (!text || !sources) return text;
  const known = new Set(sources.map((s) => String(s.n)));
  const seen = new Set();
  const linked = text.replace(MARKER, (whole, list, single) => {
    const numbers = (list || single).split(',').map((n) => n.trim());
    if (!numbers.every((n) => known.has(n))) return whole;
    return numbers
      .map((n) => {
        const href = `${CITE_PREFIX}${n}${seen.has(n) ? REPEAT : ''}`;
        seen.add(n);
        return `[${n}](${href})`;
      })
      .join('');
  });
  // "[1](#fta-cite-1)[1](#fta-cite-1.r)" -> keep the first
  return linked.replace(/(\[(\d{1,2})\]\(#fta-cite-\2(?:\.r)?\))(?:\[\2\]\(#fta-cite-\2\.r\))+/g, '$1');
}

function shortTitle(title, max = 30) {
  if (!title || title.length <= max) return title || '';
  return `${title.slice(0, max).trimEnd()}…`;
}

function dateLine(source) {
  const parts = [];
  if (source.issued) parts.push(`Issued ${source.issued}`);
  if (source.effective) parts.push(`effective ${source.effective}`);
  return parts.join(' · ');
}

const CARD_WIDTH = 340;
const CARD_HEIGHT = 240;

/**
 * One citation chip: a link to the FTA document (new tab). The summary card shows on hover and on
 * keyboard focus. `compact` draws only the number, for a source already cited earlier in the answer.
 */
export function CitationChip({ source, compact = false }) {
  const [place, setPlace] = React.useState('');
  const ref = React.useRef(null);
  const cardId = `fta-cite-card-${React.useId()}`;

  // Open the card on whichever side has room, so it never runs off the screen.
  const position = () => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const sides = [];
    if (box.left + CARD_WIDTH > window.innerWidth - 16) sides.push('is-right');
    if (box.bottom + CARD_HEIGHT > window.innerHeight && box.top > CARD_HEIGHT) sides.push('is-up');
    setPlace(sides.join(' '));
  };

  return (
    <span ref={ref} className={`fta-cite ${place}`} onMouseEnter={position} onFocus={position}>
      <a
        className={`fta-cite-chip${compact ? ' is-compact' : ''}`}
        href={source.url}
        target="_blank"
        rel="noopener noreferrer"
        aria-describedby={cardId}
        aria-label={`Source ${source.n}: ${source.title} (opens the document)`}
        onKeyDown={(e) => { if (e.key === 'Escape') e.currentTarget.blur(); }}
      >
        <span className="fta-cite-n">{source.n}</span>
        {!compact && <span className="fta-cite-label">{shortTitle(source.title)}</span>}
      </a>
      <span className="fta-cite-card" role="tooltip" id={cardId}>
        <span className="fta-cite-card-title">{source.title}</span>
        <span className="fta-cite-card-meta">
          UAE · {source.kind}
          {dateLine(source) ? ` · ${dateLine(source)}` : ''}
        </span>
        {source.excerpt && <span className="fta-cite-card-excerpt">{source.excerpt}</span>}
        <a className="fta-cite-card-link" href={source.url} target="_blank" rel="noopener noreferrer">
          Open source document <ExternalLink size={12} aria-hidden="true" />
        </a>
      </span>
    </span>
  );
}

/** ReactMarkdown `a` renderer: citation links become chips, every other link renders as before. */
export function citationLinkRenderer(sources) {
  const byNumber = new Map((sources || []).map((s) => [String(s.n), s]));
  return function CitationAwareLink({ node, href, children, ...props }) {
    if (href && href.startsWith(CITE_PREFIX)) {
      const ref = href.slice(CITE_PREFIX.length);
      const compact = ref.endsWith(REPEAT);
      const source = byNumber.get(compact ? ref.slice(0, -REPEAT.length) : ref);
      if (source) return <CitationChip source={source} compact={compact} />;
      return <>{children}</>;
    }
    return <a href={href} {...props}>{children}</a>;
  };
}
