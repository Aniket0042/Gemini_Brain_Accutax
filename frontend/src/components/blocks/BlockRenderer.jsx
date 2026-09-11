import React from 'react';
import { MarkdownBlock } from './MarkdownBlock';
import { TableBlock } from './TableBlock';
import { KpiGridBlock } from './KpiGridBlock';
import { CodeBlock } from './CodeBlock';
import { ChartBlock } from './ChartBlock';
import { ImageBlock } from './ImageBlock';
import { CanvasBlock } from './CanvasBlock';
import { ArtifactBlock } from './ArtifactBlock';

/**
 * BLOCK_REGISTRY — the one place a new block type gets wired up.
 */
const BLOCK_REGISTRY = {
  markdown: MarkdownBlock,
  table: TableBlock,
  kpi_grid: KpiGridBlock,
  code: CodeBlock,
  chart: ChartBlock,
  image: ImageBlock,
  canvas: CanvasBlock,
  artifact: ArtifactBlock,
};

/**
 * Renders raw text for a block type this build doesn't recognise yet,
 * instead of a blank gap or a crash — the safety net that makes it fine for
 * the backend to ship new block types ahead of the frontend that renders
 * them properly.
 */
function UnknownBlock({ block }) {
  console.warn(`BlockRenderer: unrecognised block type "${block.type}"`, block);
  const fallbackText = block.text || block.answer || JSON.stringify(block);
  return <p style={{ fontSize: 'var(--text-sm)', color: 'var(--ink-soft)' }}>{fallbackText}</p>;
}

export function BlockRenderer({ blocks, verification, onOpenCanvas, token }) {
  if (!Array.isArray(blocks) || blocks.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {blocks.map((block, i) => {
        const Component = BLOCK_REGISTRY[block?.type] || UnknownBlock;
        return (
          <Component
            key={i}
            block={block}
            verification={verification}
            onOpenCanvas={onOpenCanvas}
            token={token}
          />
        );
      })}
    </div>
  );
}

/** True once `blocks` contains something more than the universal markdown fallback. */
export function hasPortedBlocks(blocks) {
  return Array.isArray(blocks) && blocks.some((b) => b?.type && b.type !== 'markdown');
}

/** Chat card keeps narration, chart, and table; canvas/files become the action row. */
export function chatFacingBlocks(blocks) {
  if (!Array.isArray(blocks)) return blocks;
  const hasCanvas = blocks.some((b) => b?.type === 'canvas' || b?.type === 'artifact');
  if (!hasCanvas) return blocks;
  const hasTable = blocks.some((b) => b?.type === 'table');
  return blocks.filter((b) => {
    if (b?.type === 'canvas' || b?.type === 'artifact') return false;
    if (hasTable && b?.type === 'kpi_grid') return false;
    return ['markdown', 'chart', 'table', 'kpi_grid'].includes(b?.type);
  });
}
