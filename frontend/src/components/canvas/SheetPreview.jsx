import React, { useEffect, useRef, useState } from 'react';

/**
 * SheetJS reads the XLSX/CSV artifact and renders its first sheet as a real
 * grid. Dynamically imported — only paid for when this preview opens. Same
 * reader for both formats: `XLSX.read` takes CSV text directly.
 */
export function SheetPreview({ artifact, token, format }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');
  const [sheetNames, setSheetNames] = useState([]);

  useEffect(() => {
    if (!artifact?.id) {
      setStatus('No data to preview.');
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const XLSX = await import('xlsx');
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
        const workbook = format === 'csv'
          ? XLSX.read(await res.text(), { type: 'string' })
          : XLSX.read(await res.arrayBuffer(), { type: 'array' });
        if (cancelled) return;
        const firstSheet = workbook.SheetNames[0];
        const html = XLSX.utils.sheet_to_html(workbook.Sheets[firstSheet], { editable: false });
        if (hostRef.current) hostRef.current.innerHTML = html;
        setSheetNames(workbook.SheetNames);
        setStatus('');
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'Could not render this file.');
      }
    })();
    return () => { cancelled = true; };
  }, [artifact?.id, token, format]);

  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      {sheetNames.length > 1 && (
        <p className="answer-canvas-empty">
          Showing sheet "{sheetNames[0]}" — {sheetNames.length} sheets in file, download for the rest.
        </p>
      )}
      <div ref={hostRef} className="answer-canvas-sheet" />
    </div>
  );
}
