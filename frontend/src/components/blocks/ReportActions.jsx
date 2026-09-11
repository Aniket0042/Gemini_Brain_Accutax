import React from 'react';
import { Eye, Download } from 'lucide-react';

function remainingSeconds(expiresAt, fallback = 120) {
  if (expiresAt) {
    const t = Date.parse(expiresAt);
    if (!Number.isNaN(t)) return Math.max(0, Math.ceil((t - Date.now()) / 1000));
  }
  return fallback;
}

export async function downloadArtifact(block, token) {
  if (!block?.id) return false;
  const res = await fetch(`/api/v1/artifacts/${block.id}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) return false;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = block.filename || 'download';
  a.click();
  URL.revokeObjectURL(url);
  return true;
}

function artifactKind(block) {
  if (block?.kind) return String(block.kind).toLowerCase();
  const name = String(block?.filename || block?.mime || '').toLowerCase();
  if (name.includes('pdf')) return 'pdf';
  if (name.includes('csv')) return 'csv';
  if (name.includes('xlsx') || name.includes('excel') || name.includes('spreadsheet')) return 'xlsx';
  return '';
}

/**
 * Preview / Download PDF / Export CSV row from the P&L mock.
 */
export function ReportActions({ blocks, onOpenCanvas, token }) {
  if (!Array.isArray(blocks)) return null;
  const canvas = blocks.find((b) => b?.type === 'canvas');
  const artifacts = blocks.filter((b) => b?.type === 'artifact');
  if (!canvas && artifacts.length === 0) return null;

  const pdf = artifacts.find((a) => artifactKind(a) === 'pdf');
  const csv = artifacts.find((a) => artifactKind(a) === 'csv' || artifactKind(a) === 'xlsx') || artifacts.find((a) => artifactKind(a) !== 'pdf');

  const expired = (block) => remainingSeconds(block?.expires_at, block?.expires_in || 120) <= 0;

  return (
    <div className="report-actions">
      {canvas && (
        <button
          type="button"
          className="report-action report-action-preview"
          onClick={() => onOpenCanvas && onOpenCanvas(canvas)}
        >
          <Eye size={14} />
          Preview Full Report
        </button>
      )}
      {pdf && (
        <button
          type="button"
          className="report-action"
          disabled={expired(pdf)}
          onClick={() => downloadArtifact(pdf, token)}
        >
          <Download size={14} />
          Download PDF
        </button>
      )}
      {csv && (
        <button
          type="button"
          className="report-action"
          disabled={expired(csv)}
          onClick={() => downloadArtifact(csv, token)}
        >
          <Download size={14} />
          Export CSV
        </button>
      )}
    </div>
  );
}
