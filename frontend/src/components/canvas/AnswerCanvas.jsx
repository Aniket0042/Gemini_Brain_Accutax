import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, Download, Share2, Eye, FileSpreadsheet, ChevronDown, Check } from 'lucide-react';
import { LiveChart } from './LiveChart';
import { TableBlock } from '../blocks/TableBlock';
import { downloadArtifact, artifactKind, KIND_LABEL } from '../blocks/ReportActions';
import { DocxPreview } from './DocxPreview';
import { SheetPreview } from './SheetPreview';
import { MdPreview } from './MdPreview';

function shortLabel(kind) {
  if (kind === 'summary') return 'Summary';
  return (KIND_LABEL[kind] || kind.toUpperCase()).replace(/^(Download|Export)\s+/, '');
}

/**
 * Shared header dropdown for the preview-format and download-format pickers.
 * Portaled to <body> with fixed coordinates (same technique as TenantSwitcher)
 * since the canvas toolbar sits inside an overflow-clipped panel — an
 * absolutely-positioned menu here would get cut off at the panel edge.
 */
function ToolbarMenu({ triggerClassName, icon, label, items, onSelect, ariaLabel }) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target)
        && menuRef.current && !menuRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  if (!items.length) return null;

  const toggleOpen = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setMenuPos({ top: rect.bottom + 8, right: window.innerWidth - rect.right });
    }
    setOpen((v) => !v);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={`${triggerClassName} ${open ? 'is-open' : ''}`}
        onClick={toggleOpen}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        title={ariaLabel}
      >
        {icon}
        {label && <span>{label}</span>}
        {label && <ChevronDown size={13} className="pp-caret" />}
      </button>
      {open && menuPos && createPortal(
        <div
          ref={menuRef}
          className="pp-menu pp-menu-down"
          style={{ position: 'fixed', top: menuPos.top, right: menuPos.right, left: 'auto', bottom: 'auto' }}
          role="listbox"
        >
          {items.map((item) => (
            <button
              type="button"
              key={item.key}
              className={`pp-row ${item.selected ? 'is-selected' : ''}`}
              role="option"
              aria-selected={!!item.selected}
              onClick={() => { onSelect(item); setOpen(false); }}
            >
              <span className="pp-row-label">{item.label}</span>
              {item.selected && <Check size={15} className="pp-row-check" />}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </>
  );
}

const CANVAS_MIN = 360;
const CANVAS_MAX = 920;
const CHAT_MIN = 280;

function formatGenerated(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return '';
  }
}

function CanvasResizer({ width, onWidthChange }) {
  const dragRef = useRef(null);

  const onPointerDown = (event) => {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const pane = event.currentTarget.closest('.main-content');
    const available = (pane?.getBoundingClientRect().width || window.innerWidth) - CHAT_MIN;
    dragRef.current = {
      startX: event.clientX,
      startW: width,
      max: Math.min(CANVAS_MAX, Math.max(CANVAS_MIN, available)),
    };
    document.body.classList.add('is-canvas-resizing');
  };

  const onPointerMove = (event) => {
    if (!dragRef.current) return;
    const { startX, startW, max } = dragRef.current;
    const next = Math.min(max, Math.max(CANVAS_MIN, startW + (startX - event.clientX)));
    onWidthChange(next);
  };

  const endDrag = (event) => {
    dragRef.current = null;
    document.body.classList.remove('is-canvas-resizing');
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div
      className="answer-canvas-resizer"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize report preview"
      aria-valuemin={CANVAS_MIN}
      aria-valuemax={CANVAS_MAX}
      aria-valuenow={Math.round(width)}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={(event) => {
        if (event.key === 'ArrowLeft') {
          event.preventDefault();
          onWidthChange(Math.min(CANVAS_MAX, width + 24));
        } else if (event.key === 'ArrowRight') {
          event.preventDefault();
          onWidthChange(Math.max(CANVAS_MIN, width - 24));
        }
      }}
    />
  );
}

/**
 * AnswerCanvas — live document preview matching the P&L sidebar mock:
 * header toolbar, every KPI, the executive summary, every chart and table, callouts.
 */
export function AnswerCanvas({ spec, artifact, artifacts, token, width, onWidthChange, onClose }) {
  const charts = spec?.charts || [];
  const tables = spec?.tables || [];
  const kpis = spec?.kpis || [];
  const notes = spec?.notes || [];
  const allArtifacts = artifacts?.length ? artifacts : (artifact ? [artifact] : []);
  const seenKinds = new Set();
  const distinctArtifacts = allArtifacts.filter((a) => {
    const k = artifactKind(a);
    if (!k || seenKinds.has(k)) return false;
    seenKinds.add(k);
    return true;
  });
  // PPTX has no browser-side renderer (no parser lib for it) — download only.
  const previewableArtifacts = distinctArtifacts.filter((a) => artifactKind(a) !== 'pptx');
  const pdf = distinctArtifacts.find((a) => artifactKind(a) === 'pdf');

  // Callouts come from the validated report document: window caveats,
  // chart-type fallbacks, dropped sections. Older specs only carry `notes`.
  const callouts = spec?.callouts || notes.map((text) => ({ tone: 'note', text }));
  // The report narrator's analysis (headline, summary, drivers, risks,
  // actions). Older specs only carry a one-line insight.
  const analysis = spec?.narrative_parts;
  const insight = analysis ? null : spec?.insight;

  const [activeView, setActiveView] = useState('summary');
  const artifactIdsKey = distinctArtifacts.map((a) => a?.id).join('|');
  useEffect(() => {
    const kinds = new Set(previewableArtifacts.map((a) => artifactKind(a)));
    setActiveView((prev) => (prev !== 'summary' && !kinds.has(prev) ? 'summary' : prev));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifactIdsKey]);

  const previewItems = [
    { key: 'summary', label: 'Summary', selected: activeView === 'summary' },
    ...previewableArtifacts.map((a) => ({
      key: artifactKind(a),
      label: shortLabel(artifactKind(a)),
      selected: activeView === artifactKind(a),
    })),
  ];
  const downloadItems = distinctArtifacts.map((a) => ({
    key: artifactKind(a),
    label: KIND_LABEL[artifactKind(a)] || `Download ${(artifactKind(a) || 'file').toUpperCase()}`,
    artifact: a,
  }));
  const activeArtifact = distinctArtifacts.find((a) => artifactKind(a) === activeView);
  const paneWidth = width || 480;

  return (
    <>
      {onWidthChange && <CanvasResizer width={paneWidth} onWidthChange={onWidthChange} />}
      <aside className="answer-canvas" style={{ width: paneWidth }} aria-label="Live report preview">
      <header className="answer-canvas-toolbar">
        <div className="answer-canvas-toolbar-title">
          <FileSpreadsheet size={16} />
          <div>
            <div className="answer-canvas-doc-name">{spec?.title || 'Report'}</div>
            <div className="answer-canvas-kicker">Live Preview</div>
          </div>
        </div>
        <div className="answer-canvas-toolbar-actions">
          <ToolbarMenu
            triggerClassName="answer-canvas-text-btn pp-trigger"
            ariaLabel="Preview format"
            icon={<Eye size={14} />}
            label={shortLabel(activeView)}
            items={previewItems}
            onSelect={(item) => setActiveView(item.key)}
          />
          <button type="button" className="answer-canvas-icon-btn" aria-label="Share" disabled>
            <Share2 size={15} />
          </button>
          <ToolbarMenu
            triggerClassName="answer-canvas-icon-btn"
            ariaLabel="Download"
            icon={<Download size={15} />}
            items={downloadItems}
            onSelect={(item) => downloadArtifact(item.artifact, token)}
          />
          <button type="button" className="answer-canvas-icon-btn" onClick={onClose} aria-label="Close canvas">
            <X size={16} />
          </button>
        </div>
      </header>

      <div className="answer-canvas-body">
        {activeView === 'pdf' && pdf ? (
          <PdfPreview artifact={pdf} token={token} />
        ) : activeView === 'docx' && activeArtifact ? (
          <DocxPreview artifact={activeArtifact} token={token} />
        ) : activeView === 'xlsx' && activeArtifact ? (
          <SheetPreview artifact={activeArtifact} token={token} format="xlsx" />
        ) : activeView === 'csv' && activeArtifact ? (
          <SheetPreview artifact={activeArtifact} token={token} format="csv" />
        ) : activeView === 'md' && activeArtifact ? (
          <MdPreview artifact={activeArtifact} token={token} />
        ) : (
          <article className="answer-canvas-doc">
            <h2 className="answer-canvas-doc-heading">{spec?.title || 'Profit & Loss Statement'}</h2>
            {(spec?.period || spec?.entity || spec?.subtitle) && (
              <p className="answer-canvas-doc-meta">
                {[spec.period && `Period: ${spec.period}`, (spec.entity || spec.subtitle) && `Entity: ${spec.entity || spec.subtitle}`]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            )}

            {kpis.length > 0 && (
              <div className="answer-canvas-kpis">
                {kpis.map((k) => (
                  <div key={k.label} className="answer-canvas-kpi">
                    <span className="answer-canvas-kpi-label">{k.label}</span>
                    <span className="answer-canvas-kpi-value">{k.formatted || k.value}</span>
                    {k.delta && (
                      <span className={`answer-canvas-kpi-delta ${k.delta_positive === false ? 'is-down' : 'is-up'}`}>
                        {k.delta}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {analysis && (
              <section className="answer-canvas-analysis" aria-label="Executive summary">
                {analysis.headline && <p className="answer-canvas-headline">{analysis.headline}</p>}
                {analysis.summary?.length > 0 && <p className="answer-canvas-summary">{analysis.summary.join(' ')}</p>}
                {[['Key drivers', analysis.drivers], ['Risks and watch-points', analysis.risks], ['Recommended actions', analysis.actions]]
                  .filter(([, items]) => items?.length)
                  .map(([title, items]) => (
                    <div key={title} className="answer-canvas-analysis-group">
                      <h4>{title}</h4>
                      <ul>
                        {items.map((item) => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                  ))}
              </section>
            )}

            {/* Every chart in the report, each with its takeaway — the P&L
                bridge used to be exported but never previewed. */}
            {charts.map((chart, i) => (
              <LiveChart key={`${chart.title || 'chart'}-${i}`} chart={chart} height={220} variant="canvas" />
            ))}

            {/* Every table, as in the exported files (the canvas used to show one). */}
            {tables.map((table, i) => (
              <section key={`${table.title || 'table'}-${i}`} className="answer-canvas-section">
                <h3 className="answer-canvas-section-title">{table.title || 'Details'}</h3>
                <TableBlock block={table} />
              </section>
            ))}

            {insight && (
              <div className="answer-canvas-insight">{insight}</div>
            )}

            {callouts.length > 0 && (
              <ul className="answer-canvas-callouts">
                {callouts.map((c) => (
                  <li key={c.text} className={`answer-canvas-callout is-${c.tone || 'note'}`}>{c.text}</li>
                ))}
              </ul>
            )}

            <p className="answer-canvas-footer">
              Generated by AccuTax AI{formatGenerated(spec?.generated_at) ? ` · ${formatGenerated(spec.generated_at)}` : ''}
            </p>
            {spec?.source && <p className="answer-canvas-footer">{spec.source}</p>}
          </article>
        )}
      </div>
    </aside>
    </>
  );
}

function PdfPreview({ artifact, token }) {
  const [PdfInner, setPdfInner] = useState(null);
  const [error, setError] = useState(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import('./PdfPreview.jsx');
        if (!cancelled) setPdfInner(() => mod.PdfPreview);
      } catch (err) {
        if (!cancelled) setError(err.message || 'PDF preview unavailable');
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (error) return <p className="answer-canvas-empty">{error}</p>;
  if (!PdfInner) return <p className="answer-canvas-empty">Loading preview…</p>;
  return <PdfInner artifact={artifact} token={token} />;
}
