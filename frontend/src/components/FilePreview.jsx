import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Download, File, FileImage, FileSpreadsheet, FileText, X } from 'lucide-react';
import { CanvasResizer } from './canvas/AnswerCanvas';
import { useFileBlob } from '../hooks/useFileBlob';
import { downloadBlob } from '../services/uploadBlobs';
import { humanSize, kindGroup } from '../services/attachments';
import { openPdf, renderPage } from '../services/pdfRender';

const ICONS = { pdf: FileText, docx: FileText, image: FileImage, xlsx: FileSpreadsheet, csv: FileSpreadsheet };
const NOTE = 'Kept with this chat only';
const MAX_PDF_PAGES = 30;

function Icon({ kind, size }) {
  const Component = ICONS[kindGroup(kind)] || File;
  return <Component size={size} aria-hidden="true" />;
}

// ── the body of the side panel, by kind of file ─────────────────────────────

function PdfPages({ blob }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');

  useEffect(() => {
    let cancelled = false;
    let pdf = null;
    (async () => {
      try {
        pdf = await openPdf(blob);
        const host = hostRef.current;
        if (cancelled || !host) return;
        host.innerHTML = '';
        const width = Math.max(240, host.clientWidth - 2);
        const pages = Math.min(pdf.numPages, MAX_PDF_PAGES);
        for (let n = 1; n <= pages; n += 1) {
          const canvas = await renderPage(pdf, n, width);
          if (cancelled) return;
          canvas.className = 'file-preview-page';
          canvas.setAttribute('aria-label', `Page ${n} of ${pdf.numPages}`);
          host.appendChild(canvas);
          if (n === 1) setStatus('');
        }
        if (pdf.numPages > pages) setStatus(`Showing the first ${pages} of ${pdf.numPages} pages.`);
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'This PDF could not be shown.');
      }
    })();
    return () => {
      cancelled = true;
      pdf?.destroy?.();
    };
  }, [blob]);

  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      <div ref={hostRef} className="file-preview-pages" />
    </div>
  );
}

function DocxBody({ blob }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mod = await import('mammoth');
        const convert = mod.convertToHtml || mod.default?.convertToHtml;
        const { value } = await convert({ arrayBuffer: await blob.arrayBuffer() });
        if (!cancelled && hostRef.current) {
          hostRef.current.innerHTML = value;
          setStatus('');
        }
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'This document could not be shown.');
      }
    })();
    return () => { cancelled = true; };
  }, [blob]);

  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      <div ref={hostRef} className="answer-canvas-docx markdown-body" />
    </div>
  );
}

function SheetBody({ blob, csv }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');
  const [book, setBook] = useState(null);
  const [sheet, setSheet] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const XLSX = await import('xlsx');
        const workbook = csv
          ? XLSX.read(await blob.text(), { type: 'string' })
          : XLSX.read(await blob.arrayBuffer(), { type: 'array' });
        if (!cancelled) {
          setBook({ XLSX, workbook });
          setStatus('');
        }
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'This file could not be shown.');
      }
    })();
    return () => { cancelled = true; };
  }, [blob, csv]);

  useEffect(() => {
    if (!book || !hostRef.current) return;
    const name = book.workbook.SheetNames[sheet];
    hostRef.current.innerHTML = book.XLSX.utils.sheet_to_html(book.workbook.Sheets[name], { editable: false });
  }, [book, sheet]);

  const names = book?.workbook.SheetNames || [];
  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      {names.length > 1 && (
        <div className="file-preview-tabs" role="tablist" aria-label="Sheets">
          {names.map((n, i) => (
            <button key={n} type="button" role="tab" aria-selected={i === sheet} className={i === sheet ? 'is-active' : ''}
                    onClick={() => setSheet(i)}>{n}</button>
          ))}
        </div>
      )}
      <div ref={hostRef} className="answer-canvas-sheet" />
    </div>
  );
}

/**
 * The side panel for a document: a PDF page by page, a Word file as text, a spreadsheet as a grid. It takes the
 * place of the report canvas beside the chat, so the conversation stays in view.
 */
export function FilePreviewPanel({ item, token, width, onWidthChange, onClose }) {
  const group = kindGroup(item.kind);
  const { blob, error, loading } = useFileBlob(item, token);
  const closeRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      {onWidthChange && <CanvasResizer width={width} onWidthChange={onWidthChange} />}
      <aside className="answer-canvas file-preview" style={{ width }} aria-label={`Preview of ${item.name}`}>
        <header className="answer-canvas-toolbar">
          <div className="answer-canvas-toolbar-title">
            <Icon kind={item.kind} size={16} />
            <div>
              <div className="answer-canvas-doc-name" title={item.name}>{item.name}</div>
              <div className="answer-canvas-kicker">{[item.size ? humanSize(item.size) : '', NOTE].filter(Boolean).join(' · ')}</div>
            </div>
          </div>
          <div className="answer-canvas-toolbar-actions">
            <button type="button" className="answer-canvas-icon-btn" aria-label="Download" title="Download"
                    disabled={!blob} onClick={() => blob && downloadBlob(blob, item.name)}>
              <Download size={15} />
            </button>
            <button ref={closeRef} type="button" className="answer-canvas-icon-btn" onClick={onClose} aria-label="Close preview" title="Close">
              <X size={16} />
            </button>
          </div>
        </header>
        <div className="answer-canvas-body">
          {loading && <p className="answer-canvas-empty">Loading preview…</p>}
          {error && <p className="answer-canvas-empty" role="alert">{error}</p>}
          {blob && group === 'pdf' && <PdfPages blob={blob} />}
          {blob && group === 'docx' && <DocxBody blob={blob} />}
          {blob && group === 'xlsx' && <SheetBody blob={blob} csv={false} />}
          {blob && group === 'csv' && <SheetBody blob={blob} csv />}
        </div>
      </aside>
    </>
  );
}

/**
 * An image full size over the page, with the page behind it blurred. A click outside the picture, the close
 * button or Escape closes it.
 */
export function ImageLightbox({ item, token, onClose }) {
  const { blob, error, loading } = useFileBlob(item, token);
  const [url, setUrl] = useState(null);
  const closeRef = useRef(null);

  useEffect(() => {
    if (!blob) return undefined;
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  useEffect(() => {
    const previous = document.activeElement;
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (previous && previous.focus) previous.focus();
    };
  }, [onClose]);

  return createPortal(
    <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={`Preview of ${item.name}`} onClick={onClose}>
      <div className="image-lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <div className="image-lightbox-title">
          <span>{item.name}</span>
          <small>{NOTE}</small>
        </div>
        <div className="image-lightbox-actions">
          <button type="button" aria-label="Download" title="Download" disabled={!blob} onClick={() => blob && downloadBlob(blob, item.name)}>
            <Download size={18} />
          </button>
          <button ref={closeRef} type="button" aria-label="Close preview" title="Close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
      </div>
      <div className="image-lightbox-stage">
        {url && <img src={url} alt={item.name} className="image-lightbox-img" onClick={(e) => e.stopPropagation()} />}
        {loading && <p className="image-lightbox-note">Loading…</p>}
        {error && <p className="image-lightbox-note" role="alert" onClick={(e) => e.stopPropagation()}>{error}</p>}
      </div>
    </div>,
    document.body,
  );
}
