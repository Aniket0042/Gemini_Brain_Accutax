import React, { useRef, useState } from 'react';
import { X, Download, Share2, Eye, FileSpreadsheet } from 'lucide-react';
import { LiveChart } from './LiveChart';
import { TableBlock } from '../blocks/TableBlock';
import { downloadArtifact } from '../blocks/ReportActions';

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
 * header toolbar, KPI tiles, optional chart, monthly table, insight callout.
 */
export function AnswerCanvas({ spec, artifact, artifacts, token, width, onWidthChange, onClose }) {
  const charts = spec?.charts || [];
  const tables = spec?.tables || [];
  const kpis = spec?.kpis || [];
  const notes = spec?.notes || [];
  const allArtifacts = artifacts?.length ? artifacts : (artifact ? [artifact] : []);
  const pdf = allArtifacts.find((a) => String(a?.kind || a?.mime || a?.filename || '').toLowerCase().includes('pdf'));
  const downloadable = pdf || allArtifacts[0];

  const monthly = tables.find((t) =>
    String(t.title || '').toLowerCase().includes('month')
    || (t.columns || []).some((c) => c.key === 'month'),
  );
  const bodyTable = monthly || tables[0];
  const insight = spec?.insight || notes[0];

  const [showPdf, setShowPdf] = useState(false);
  const isPdf = Boolean(pdf?.id);
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
          {isPdf && (
            <button type="button" className="answer-canvas-text-btn" onClick={() => setShowPdf((v) => !v)}>
              <Eye size={14} />
              {showPdf ? 'View Summary' : 'View Full Report'}
            </button>
          )}
          <button type="button" className="answer-canvas-icon-btn" aria-label="Share" disabled>
            <Share2 size={15} />
          </button>
          {downloadable && (
            <button
              type="button"
              className="answer-canvas-icon-btn"
              aria-label="Download"
              onClick={() => downloadArtifact(downloadable, token)}
            >
              <Download size={15} />
            </button>
          )}
          <button type="button" className="answer-canvas-icon-btn" onClick={onClose} aria-label="Close canvas">
            <X size={16} />
          </button>
        </div>
      </header>

      <div className="answer-canvas-body">
        {showPdf && isPdf ? (
          <PdfPreview artifact={pdf} token={token} />
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
                {kpis.slice(0, 3).map((k) => (
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

            {charts[0] && (
              <LiveChart chart={charts[0]} height={220} variant="canvas" />
            )}

            {bodyTable && (
              <section className="answer-canvas-section">
                <h3 className="answer-canvas-section-title">{bodyTable.title || 'Details'}</h3>
                <TableBlock block={bodyTable} />
              </section>
            )}

            {insight && (
              <div className="answer-canvas-insight">{insight}</div>
            )}

            <p className="answer-canvas-footer">
              Generated by AccuTax AI{formatGenerated(spec?.generated_at) ? ` · ${formatGenerated(spec.generated_at)}` : ''}
            </p>
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
