import React, { useEffect, useState } from 'react';
import { Download, FileText } from 'lucide-react';
import { downloadArtifact } from './ReportActions';

function remainingSeconds(expiresAt, fallback = 120) {
  if (expiresAt) {
    const t = Date.parse(expiresAt);
    if (!Number.isNaN(t)) return Math.max(0, Math.ceil((t - Date.now()) / 1000));
  }
  return fallback;
}

function formatClock(secs, expiresAt) {
  // Links now live for hours: show the expiry time, not a long countdown.
  if (secs >= 3600) {
    const t = expiresAt ? new Date(expiresAt) : new Date(Date.now() + secs * 1000);
    return `until ${t.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`;
  }
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function ArtifactBlock({ block, token }) {
  // `current` becomes the regenerated file after an expired one is rebuilt.
  const [current, setCurrent] = useState(block);
  const [left, setLeft] = useState(() => remainingSeconds(block?.expires_at, block?.expires_in || 120));
  const expired = left <= 0;
  const filename = current?.filename || 'download';

  useEffect(() => {
    if (expired) return undefined;
    const id = window.setInterval(() => {
      setLeft((prev) => Math.max(0, prev - 1));
    }, 1000);
    return () => window.clearInterval(id);
  }, [expired]);

  const handleDownload = async () => {
    // An expired file is rebuilt from its report and downloaded (see downloadArtifact).
    const got = await downloadArtifact(current, token);
    if (got && got.id !== current?.id) {
      setCurrent(got);
      setLeft(remainingSeconds(got.expires_at, got.expires_in || 120));
    }
  };

  return (
    <div className="artifact-card">
      <FileText size={16} />
      <div className="artifact-card-meta">
        <div className="artifact-card-name">{filename}</div>
        <div className="artifact-card-status">
          {expired ? 'Expired — download to rebuild it' : `Download available · ${formatClock(left, current?.expires_at)}`}
        </div>
      </div>
      <button type="button" className="artifact-card-btn" disabled={!current?.id} onClick={handleDownload}>
        <Download size={14} />
        {expired ? 'Rebuild & download' : 'Download'}
      </button>
    </div>
  );
}
