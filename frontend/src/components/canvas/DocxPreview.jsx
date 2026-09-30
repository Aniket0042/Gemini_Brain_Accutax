import React, { useEffect, useRef, useState } from 'react';

/**
 * mammoth converts the DOCX we generated into HTML for a read-only inline
 * preview. Dynamically imported — only paid for when a DOCX preview opens.
 */
export function DocxPreview({ artifact, token }) {
  const hostRef = useRef(null);
  const [status, setStatus] = useState('Loading preview…');

  useEffect(() => {
    if (!artifact?.id) {
      setStatus('No document to preview.');
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const mod = await import('mammoth');
        const convertToHtml = mod.convertToHtml || mod.default?.convertToHtml;
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
        const arrayBuffer = await res.arrayBuffer();
        const { value: html } = await convertToHtml({ arrayBuffer });
        if (!cancelled && hostRef.current) {
          hostRef.current.innerHTML = html;
          setStatus('');
        }
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'Could not render this document.');
      }
    })();
    return () => { cancelled = true; };
  }, [artifact?.id, token]);

  return (
    <div>
      {status && <p className="answer-canvas-empty">{status}</p>}
      <div ref={hostRef} className="answer-canvas-docx markdown-body" />
    </div>
  );
}
