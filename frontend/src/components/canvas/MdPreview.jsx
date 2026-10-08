import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { linkComponents } from '../markdownLinks';

export function MdPreview({ artifact, token }) {
  const [text, setText] = useState('');
  const [status, setStatus] = useState('Loading preview…');

  useEffect(() => {
    if (!artifact?.id) {
      setStatus('No document to preview.');
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
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
        const body = await res.text();
        if (!cancelled) {
          setText(body);
          setStatus('');
        }
      } catch (err) {
        if (!cancelled) setStatus(err.message || 'Could not load this file.');
      }
    })();
    return () => { cancelled = true; };
  }, [artifact?.id, token]);

  if (status) return <p className="answer-canvas-empty">{status}</p>;
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={linkComponents}>{text}</ReactMarkdown>
    </div>
  );
}
