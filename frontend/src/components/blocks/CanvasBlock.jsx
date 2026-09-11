import React from 'react';
import { LayoutPanelLeft } from 'lucide-react';

export function CanvasBlock({ block, onOpenCanvas }) {
  const title = block?.title || block?.spec?.title || 'Report';
  return (
    <button
      type="button"
      className="canvas-chip"
      onClick={() => onOpenCanvas && onOpenCanvas(block)}
      title="Open in canvas"
    >
      <LayoutPanelLeft size={14} />
      <span>Opened in canvas</span>
      <span className="canvas-chip-title">{title}</span>
    </button>
  );
}
