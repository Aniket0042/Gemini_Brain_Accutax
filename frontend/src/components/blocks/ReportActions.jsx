import React from 'react';
import { Eye, Download } from 'lucide-react';

/**
 * Download a generated file. An expired file (HTTP 410) whose report is still
 * kept is rebuilt from that report on the server and downloaded in the same
 * click, so an old link keeps working instead of dead-ending.
 * Returns the block that was downloaded (the regenerated one, if any) or null.
 */
export async function downloadArtifact(block, token) {
  if (!block?.id) return null;
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  let current = block;
  let res = await fetch(`/api/v1/artifacts/${current.id}`, { headers });
  if (res.status === 410) {
    const body = await res.json().catch(() => ({}));
    if (!body?.detail?.regenerate) return null;
    const regen = await fetch(`/api/v1/artifacts/${current.id}/regenerate`, { method: 'POST', headers });
    if (!regen.ok) return null;
    current = { ...current, ...(await regen.json()) };
    res = await fetch(`/api/v1/artifacts/${current.id}`, { headers });
  }
  if (!res.ok) return null;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = current.filename || 'download';
  a.click();
  URL.revokeObjectURL(url);
  return current;
}

export function artifactKind(block) {
  if (block?.kind) return String(block.kind).toLowerCase();
  const name = String(block?.filename || block?.mime || '').toLowerCase();
  if (name.includes('pdf')) return 'pdf';
  if (name.includes('csv')) return 'csv';
  if (name.includes('xlsx') || name.includes('excel') || name.includes('spreadsheet')) return 'xlsx';
  if (name.includes('docx') || name.includes('word')) return 'docx';
  if (name.includes('pptx') || name.includes('powerpoint')) return 'pptx';
  if (name.includes('md') || name.includes('markdown')) return 'md';
  return '';
}

export const KIND_LABEL = {
  pdf: 'Download PDF',
  csv: 'Export CSV',
  xlsx: 'Download Excel',
  docx: 'Download Word',
  pptx: 'Download PowerPoint',
  md: 'Download Markdown',
};

/**
 * Preview + download button(s) for the chat card. If the user named a format
 * ("as csv", "as markdown"), the backend flags that one artifact `primary`
 * (see attach.py) and this shows only that button — not the pdf/csv bonus
 * files minted alongside it (those stay reachable from the canvas preview).
 * A plain chart request has no primary artifact, so it falls back to one
 * button per kind, same as before.
 */
export function ReportActions({ blocks, onOpenCanvas, token }) {
  if (!Array.isArray(blocks)) return null;
  const canvas = blocks.find((b) => b?.type === 'canvas');
  const artifacts = blocks.filter((b) => b?.type === 'artifact');
  if (!canvas && artifacts.length === 0) return null;

  const primary = artifacts.find((a) => a?.primary === true);
  let ordered;
  if (primary) {
    ordered = [primary];
  } else {
    // One button per kind (first artifact of that kind wins); PDF/CSV lead
    // to keep the familiar order, then whatever else came back.
    const seen = new Set();
    ordered = [
      ...artifacts.filter((a) => artifactKind(a) === 'pdf'),
      ...artifacts.filter((a) => artifactKind(a) === 'csv'),
      ...artifacts.filter((a) => !['pdf', 'csv'].includes(artifactKind(a))),
    ].filter((a) => {
      const k = artifactKind(a);
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }


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
      {ordered.map((artifact) => (
        <button
          key={artifact.id || artifactKind(artifact)}
          type="button"
          className="report-action"
          onClick={() => downloadArtifact(artifact, token)}
        >
          <Download size={14} />
          {KIND_LABEL[artifactKind(artifact)] || `Download ${(artifactKind(artifact) || 'file').toUpperCase()}`}
        </button>
      ))}
    </div>
  );
}
