import React, { useEffect, useState } from 'react';
import { Download, FileText } from 'lucide-react';

function remainingSeconds(expiresAt, fallback = 120) {
  if (expiresAt) {
    const t = Date.parse(expiresAt);
    if (!Number.isNaN(t)) return Math.max(0, Math.ceil((t - Date.now()) / 1000));
  }
  return fallback;
}

function formatClock(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function ArtifactBlock({ block, token }) {
  const [left, setLeft] = useState(() => remainingSeconds(block?.expires_at, block?.expires_in || 120));
  const expired = left <= 0;
  const filename = block?.filename || 'download';

  useEffect(() => {
    if (expired) return undefined;
    const id = window.setInterval(() => {
      setLeft((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [expired]);

  const handleDownload = async () => {
    if (!block?.id || expired) return;
    const res = await fetch(`/api/v1/artifacts/${block.id}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (res.status === 410) {
      setLeft(0);
      return;
    }
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="artifact-card">
      <FileText size={16} />
      <div className="artifact-card-meta">
        <div className="artifact-card-name">{filename}</div>
        <div className="artifact-card-status">
          {expired ? 'Expired — ask again to regenerate' : `Download available · ${formatClock(left)}`}
        </div>
      </div>
      <button type="button" className="artifact-card-btn" disabled={expired || !block?.id} onClick={handleDownload}>
        <Download size={14} />
        Download
      </button>
    </div>
  );
}
