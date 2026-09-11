import React, { useEffect, useRef, useState } from 'react';

/**
 * PDF.js preview of a short-lived artifact. The worker and fetch are loaded
 * only when this tab is opened.
 */
export function PdfPreview({ artifact, token }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');

  useEffect(() => {
    if (!artifact?.id) {
      setStatus('No PDF to preview.');
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const pdfjs = await import('pdfjs-dist');
        const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
        pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
        const res = await fetch(`/api/v1/artifacts/${artifact.id}`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (res.status === 410) {
          if (!cancelled) setStatus('This file expired. Ask again to regenerate it.');
          return;
        }
        if (!res.ok) {
          if (!cancelled) setStatus('Preview is not available yet.');
          return;
        }
        const buf = await res.arrayBuffer();
        const pdf = await pdfjs.getDocument({ data: buf }).promise;
        const page = await pdf.getPage(1);
        const viewport = page.getViewport({ scale: 1.2 });
        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.style.width = '100%';
        canvas.style.height = 'auto';
        await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
        if (!cancelled && hostRef.current) {
          hostRef.current.innerHTML = '';
          hostRef.current.appendChild(canvas);
          setStatus('');
        }
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'Could not render PDF.');
      }
    })();
    return () => { cancelled = true; };
  }, [artifact?.id, token]);

  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      <div ref={hostRef} />
    </div>
  );
}
