import React from 'react';
import { LiveChart, normalizeChart } from '../canvas/LiveChart';

/**
 * ChartBlock — same `{categories, series, chart_type}` schema as the canvas,
 * plus Chart.js `{labels, datasets}` if an older payload arrives.
 */
export function ChartBlock({ block }) {
  const chart = normalizeChart(block) || block;
  return (
    <div className="chat-chart-card">
      <LiveChart chart={chart} height={240} variant="card" />
    </div>
  );
}
