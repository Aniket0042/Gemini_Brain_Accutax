import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  BarChart,
  Bar,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  LabelList,
} from 'recharts';
import { OTHER_COLOR, resolveChartColors } from './chartTheme';

function formatTick(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? '');
  const abs = Math.abs(n);
  const trim = (s) => s.replace(/\.0$/, '');
  if (abs >= 1_000_000) return `${trim((n / 1_000_000).toFixed(1))}M`;
  if (abs >= 10_000) return `${(n / 1_000).toFixed(0)}K`;
  // 1,300 must not read as "1K" next to a "2K" tick.
  if (abs >= 1_000) return `${trim((n / 1_000).toFixed(1))}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// Same compact form as the exported files and takeaways (ir.format_compact):
// "220.5K", "1.24M", "2,340".
function compactLabel(v, signed = false) {
  if (v === null || v === undefined) return '';
  const n = Number(v);
  if (!Number.isFinite(n)) return '';
  const abs = Math.abs(n);
  let body;
  if (abs >= 1_000_000) body = `${(abs / 1_000_000).toFixed(2)}M`;
  else if (abs >= 10_000) body = `${(abs / 1_000).toFixed(1)}K`;
  else body = Math.round(abs).toLocaleString('en-US');
  const sign = n < 0 ? '-' : (signed && n > 0 ? '+' : '');
  return `${sign}${body}`;
}

// Bridge geometry: an invisible base plus the visible step, as in the files.
function waterfallRows(categories, values, totals) {
  let running = 0;
  return categories.map((cat, i) => {
    const v = Number(values[i]) || 0;
    if (totals.has(i)) {
      running = v;
      return { category: cat, base: 0, bar: v, value: v, total: true };
    }
    const base = v >= 0 ? running : running + v;
    running += v;
    return { category: cat, base, bar: Math.abs(v), value: v, total: false };
  });
}

const LABEL_STYLE = { fontSize: 11, fill: 'var(--ink-soft)' };
// Recharts 3 sorts legend items by name; series order carries meaning
// (Revenue before Expenses), so keep it.
const KEEP_ORDER = () => 0;

// Category tick that wraps onto two lines instead of running into its neighbour.
function WrappedTick({ x, y, payload }) {
  const words = String(payload?.value ?? '').split(' ');
  const lines = words.length > 1 && String(payload?.value).length > 10
    ? [words.slice(0, Math.ceil(words.length / 2)).join(' '), words.slice(Math.ceil(words.length / 2)).join(' ')]
    : [String(payload?.value ?? '')];
  return (
    <text x={x} y={y + 12} textAnchor="middle" fontSize={11} fill="var(--ink-faint)">
      {lines.map((line, i) => <tspan key={line} x={x} dy={i === 0 ? 0 : 13}>{line}</tspan>)}
    </text>
  );
}

const MAX_LABELLED_BARS = 15;

function formatTooltip(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function toRows(categories, series) {
  return (categories || []).map((cat, i) => {
    const row = { category: cat };
    (series || []).forEach((s) => {
      const name = s.name || 'Value';
      const raw = (s.data || [])[i];
      // A missing value is a gap (null), never a drawn zero.
      const n = raw === null || raw === undefined || raw === '' ? null : Number(raw);
      row[name] = Number.isFinite(n) ? n : null;
    });
    return row;
  });
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="live-chart-tooltip">
      {label != null && label !== '' && (
        <div className="live-chart-tooltip-label">{label}</div>
      )}
      {payload.map((entry) => (
        <div key={entry.dataKey || entry.name} className="live-chart-tooltip-row">
          <span className="live-chart-tooltip-swatch" style={{ backgroundColor: entry.color }} />
          <span className="live-chart-tooltip-name">{entry.name}</span>
          <span className="live-chart-tooltip-value">{formatTooltip(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}

const AXIS_TICK = { fontSize: 12, fill: 'var(--ink-faint)' };
const GRID_STROKE = 'var(--border)';
const LEGEND_STYLE = { fontSize: 12, paddingTop: 8, color: 'var(--ink-soft)' };

/**
 * Fits a long horizontal-bar category label on one line. The ellipsis goes in
 * the middle: names like "Professional & Consulting Services_User1_Org4" and
 * "…_Org6" differ only at the end, so the end must stay visible. The tooltip
 * still shows the full label.
 */
function shortCategory(label, maxChars = 22) {
  const text = String(label ?? '');
  if (text.length <= maxChars) return text;
  const head = Math.max(3, Math.floor(maxChars * 0.45));
  const tail = Math.max(3, maxChars - head - 1);
  return `${text.slice(0, head).trimEnd()}…${text.slice(-tail).trimStart()}`;
}

/** Horizontal-bar label column: up to 172px, never more than 38% of the card. */
const HBAR_AXIS_MAX = 172;
const HBAR_AXIS_SHARE = 0.38;
const AXIS_CHAR_PX = 7.4;

export function normalizeChart(input) {
  if (!input) return null;
  if (Array.isArray(input.categories) && Array.isArray(input.series)) {
    return {
      chart_type: (input.chart_type || 'bar').toLowerCase(),
      title: input.title,
      caption: input.caption,
      x_label: input.x_label,
      y_label: input.y_label,
      categories: input.categories,
      series: input.series,
      series_colors: input.series_colors,
      point_colors: input.point_colors,
      downgrade_reason: input.downgrade_reason,
      requested_type: input.requested_type,
      takeaway: input.takeaway,
      total_indices: input.total_indices,
    };
  }
  const data = input.data && typeof input.data === 'object' ? input.data : input;
  const labels = data.labels || input.labels;
  const datasets = data.datasets || input.datasets;
  if (Array.isArray(labels) && Array.isArray(datasets) && datasets.length) {
    return {
      chart_type: String(data.chartType || input.chartType || input.chart_type || 'bar').toLowerCase().replace('column', 'bar'),
      title: input.title || data.title,
      caption: input.caption || data.caption,
      categories: labels,
      series: datasets.map((ds) => ({
        name: ds.label || ds.name || 'Value',
        data: ds.data || [],
      })),
    };
  }
  return null;
}

/**
 * LiveChart — Recharts renderer. Always given an explicit pixel height so
 * ResponsiveContainer cannot collapse to 0×0 inside a flex sidebar.
 */
export function LiveChart({ chart, height = 260, variant = 'card' }) {
  const normalized = useMemo(() => normalizeChart(chart), [chart]);
  const categories = normalized?.categories || [];
  const series = (normalized?.series || []).filter((s) => Array.isArray(s.data) && s.data.length > 0);
  const chartType = (normalized?.chart_type || 'bar').toLowerCase();
  const rows = useMemo(() => toRows(categories, series), [categories, series]);
  const { seriesColors, pointColors } = useMemo(
    () => (normalized ? resolveChartColors(normalized) : { seriesColors: [], pointColors: null }),
    [normalized],
  );
  // Card width, so the horizontal-bar label column can shrink in narrow
  // layouts instead of leaving no room for the bars.
  const wrapRef = useRef(null);
  const [boxWidth, setBoxWidth] = useState(0);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width || 0;
      setBoxWidth(Math.round(width));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const hbarAxisWidth = boxWidth
    ? Math.max(56, Math.min(HBAR_AXIS_MAX, Math.round(boxWidth * HBAR_AXIS_SHARE)))
    : HBAR_AXIS_MAX;
  const hbarMaxChars = Math.max(6, Math.floor(hbarAxisWidth / AXIS_CHAR_PX));

  if (!normalized || categories.length === 0 || series.length === 0) {
    return <p style={styles.empty}>No data to chart.</p>;
  }

  const claim = `${chartType} chart of ${series.map((s) => s.name).join(', ')}`;
  // Horizontal bars grow with the number of categories so labels never collide.
  const plotHeight = chartType === 'hbar'
    ? Math.max(height, 30 * categories.length + 40)
    : Math.max(180, height);

  let plot;
  if (chartType === 'pie' || chartType === 'donut') {
    const primary = series[0];
    const pieRows = categories
      .map((cat, i) => ({ name: cat, value: Number(primary.data[i]), color: pointColors?.[i] || OTHER_COLOR }))
      .filter((r) => Number.isFinite(r.value) && r.value > 0);
    const total = pieRows.reduce((acc, r) => acc + r.value, 0);
    // Direct % labels: two palette slots sit below 3:1 contrast, so identity
    // never rests on color alone.
    const pctLabel = ({ value }) => (total && value / total >= 0.04 ? `${Math.round((value / total) * 100)}%` : '');
    plot = (
      <PieChart>
        <Pie
          data={pieRows}
          dataKey="value"
          nameKey="name"
          cx="50%"
          cy="50%"
          innerRadius={chartType === 'donut' ? '48%' : 0}
          outerRadius="72%"
          startAngle={90}
          endAngle={-270}
          stroke="var(--surface-2, #FFFFFF)"
          strokeWidth={2}
          label={pctLabel}
          labelLine={false}
          isAnimationActive={false}
        >
          {pieRows.map((r) => (
            <Cell key={r.name} fill={r.color} />
          ))}
        </Pie>
        <Tooltip content={<ChartTooltip />} />
        <Legend wrapperStyle={LEGEND_STYLE} iconType="circle" itemSorter={KEEP_ORDER} />
      </PieChart>
    );
  } else if (chartType === 'waterfall') {
    const totals = new Set(normalized.total_indices || [0, categories.length - 1]);
    const wf = waterfallRows(categories, series[0].data || [], totals);
    plot = (
      <BarChart data={wf} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="category" tick={<WrappedTick />} axisLine={false} tickLine={false} interval={0} height={36} />
        <YAxis tickFormatter={formatTick} tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} />
        <Tooltip
          content={({ active, payload, label }) => (active && payload?.length ? (
            <ChartTooltip
              active
              label={label}
              payload={[{ name: series[0].name, value: payload[0].payload.value, color: pointColors?.[categories.indexOf(label)] }]}
            />
          ) : null)}
          cursor={{ fill: 'rgba(var(--ink-rgb), 0.08)' }}
        />
        <Bar dataKey="base" stackId="wf" fill="transparent" isAnimationActive={false} />
        <Bar dataKey="bar" stackId="wf" isAnimationActive={false} maxBarSize={48}>
          {wf.map((r, idx) => <Cell key={r.category} fill={pointColors?.[idx] || seriesColors[0] || OTHER_COLOR} />)}
          <LabelList
            dataKey="value"
            content={(props) => {
              const row = wf[props.index];
              if (!row) return null;
              return (
                <text x={props.x + props.width / 2} y={props.y - 5} textAnchor="middle" {...LABEL_STYLE}>
                  {compactLabel(row.value, !row.total)}
                </text>
              );
            }}
          />
        </Bar>
      </BarChart>
    );
  } else if (chartType === 'hbar') {
    plot = (
      <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tickFormatter={formatTick} tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <YAxis
          type="category"
          dataKey="category"
          tick={AXIS_TICK}
          axisLine={false}
          tickLine={false}
          width={hbarAxisWidth}
          interval={0}
          tickFormatter={(v) => shortCategory(v, hbarMaxChars)}
        />
        <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(var(--ink-rgb), 0.08)' }} />
        {series.length > 1 && <Legend iconType="circle" wrapperStyle={LEGEND_STYLE} itemSorter={KEEP_ORDER} />}
        {series.map((s, i) => {
          const name = s.name || `Series ${i + 1}`;
          const perBar = series.length === 1 && Array.isArray(pointColors) ? pointColors : null;
          return (
            <Bar key={name} dataKey={name} fill={seriesColors[i] || OTHER_COLOR} radius={[0, 6, 6, 0]} maxBarSize={26} isAnimationActive={false}>
              {perBar ? rows.map((_, idx) => <Cell key={idx} fill={perBar[idx]} />) : null}
              {series.length === 1 && (
                <LabelList dataKey={name} position="right" style={LABEL_STYLE} formatter={(v) => compactLabel(v)} />
              )}
            </Bar>
          );
        })}
      </BarChart>
    );
  } else {
    const ChartTag = chartType === 'area' ? AreaChart : chartType === 'line' ? LineChart : BarChart;
    const stacked = chartType === 'stacked_bar';
    const labelBars = !stacked && rows.length * series.length <= MAX_LABELLED_BARS;
    plot = (
      <ChartTag data={rows} margin={{ top: labelBars ? 18 : 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="category" tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <YAxis tickFormatter={formatTick} tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} />
        <Tooltip
          content={<ChartTooltip />}
          cursor={chartType === 'bar' || stacked
            ? { fill: 'rgba(var(--ink-rgb), 0.08)' }
            : { stroke: 'var(--ink-faint)', strokeWidth: 1 }}
        />
        {series.length > 1 && <Legend iconType="circle" wrapperStyle={LEGEND_STYLE} itemSorter={KEEP_ORDER} />}
        {series.map((s, i) => {
          const color = seriesColors[i] || OTHER_COLOR;
          const name = s.name || `Series ${i + 1}`;
          if (chartType === 'area') {
            return <Area key={name} type="monotone" dataKey={name} stroke={color} fill={color} fillOpacity={0.22} strokeWidth={2} />;
          }
          if (chartType === 'line') {
            return <Line key={name} type="monotone" dataKey={name} stroke={color} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} />;
          }
          const perBar = series.length === 1 && Array.isArray(pointColors) ? pointColors : null;
          const topOfStack = stacked && i === series.length - 1;
          return (
            <Bar
              key={name}
              dataKey={name}
              fill={color}
              stackId={stacked ? 'stack' : undefined}
              radius={stacked && !topOfStack ? [0, 0, 0, 0] : [6, 6, 0, 0]}
              maxBarSize={42}
              stroke={stacked ? 'var(--surface-2, #FFFFFF)' : undefined}
              strokeWidth={stacked ? 1 : 0}
            >
              {perBar
                ? rows.map((_, idx) => <Cell key={idx} fill={perBar[idx]} />)
                : null}
              {labelBars && (
                <LabelList dataKey={name} position="top" style={LABEL_STYLE} formatter={(v) => compactLabel(v)} />
              )}
            </Bar>
          );
        })}
      </ChartTag>
    );
  }

  return (
    <div ref={wrapRef} className={variant === 'canvas' ? 'live-chart live-chart-canvas' : 'live-chart'} style={styles.wrap} role="img" aria-label={claim}>
      {normalized.title && <div style={styles.title}>{normalized.title}</div>}
      <div style={{ width: '100%', minWidth: 0, height: plotHeight }}>
        <ResponsiveContainer width="100%" height={plotHeight}>
          {plot}
        </ResponsiveContainer>
      </div>
      {normalized.takeaway && <div style={styles.takeaway}>{normalized.takeaway}</div>}
      {normalized.caption && <div style={styles.caption}>{normalized.caption}</div>}
      {normalized.downgrade_reason && (
        <div style={styles.caption}>
          {`Shown as a bar chart: ${normalized.downgrade_reason}`}
        </div>
      )}
    </div>
  );
}

const styles = {
  wrap: {
    width: '100%',
    minWidth: 0,
    borderRadius: 12,
    padding: '14px 16px 10px',
    backgroundColor: 'var(--surface-2, #F1F5F4)',
  },
  title: {
    fontSize: 11,
    fontWeight: 700,
    letterSpacing: '0.06em',
    textTransform: 'uppercase',
    color: 'var(--ink-faint, #64748B)',
    marginBottom: 8,
  },
  takeaway: {
    fontSize: 13,
    lineHeight: 1.45,
    color: 'var(--ink, #14201C)',
    paddingTop: 8,
    marginTop: 6,
    borderTop: '1px solid var(--border-soft, #E2E8F0)',
  },
  caption: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 12,
    color: 'var(--ink-soft, #526059)',
    paddingTop: 4,
    borderTop: '1px solid var(--border-soft, #E2E8F0)',
    marginTop: 4,
  },
  empty: {
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-faint)',
    fontStyle: 'italic',
    padding: '8px 0',
  },
};
