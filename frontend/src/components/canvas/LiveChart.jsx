import React, { useMemo } from 'react';
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
} from 'recharts';

const PASTEL_COLORS = [
  '#8EC5D6',
  '#F4B183',
  '#C3B1E1',
  '#9DD9C5',
  '#F3A6B8',
  '#F2D98A',
  '#8FB8E8',
  '#B5D99C',
  '#E8A0C2',
  '#D4C1EC',
];

function pastelAt(index) {
  return PASTEL_COLORS[index % PASTEL_COLORS.length];
}

function namedColor(name, index) {
  const key = String(name || '').toLowerCase();
  if (key.includes('revenue') || key.includes('income') || key.includes('sales')) return pastelAt(0);
  if (key.includes('expense') || key.includes('cost')) return pastelAt(1);
  if (key.includes('profit') || key.includes('net')) return pastelAt(2);
  return pastelAt(index);
}

function categoryColors(count, explicit) {
  if (Array.isArray(explicit) && explicit.length) {
    return Array.from({ length: count }, (_, i) => explicit[i % explicit.length]);
  }
  return Array.from({ length: count }, (_, i) => pastelAt(i));
}

function formatTick(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? '');
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatTooltip(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  return n.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function toRows(categories, series) {
  return (categories || []).map((cat, i) => {
    const row = { category: cat };
    (series || []).forEach((s) => {
      const name = s.name || 'Value';
      const data = s.data || [];
      row[name] = Number(data[i]) || 0;
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
      bar_colors: input.bar_colors,
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
      bar_colors: input.bar_colors || data.bar_colors,
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

  if (!normalized || categories.length === 0 || series.length === 0) {
    return <p style={styles.empty}>No data to chart.</p>;
  }

  const claim = `${chartType} chart of ${series.map((s) => s.name).join(', ')}`;
  const plotHeight = Math.max(180, height);

  let plot;
  if (chartType === 'pie') {
    const primary = series[0];
    const pieRows = categories.map((cat, i) => ({
      name: cat,
      value: Number(primary.data[i]) || 0,
    }));
    plot = (
      <PieChart>
        <Pie data={pieRows} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius="40%" outerRadius="70%" paddingAngle={1}>
          {pieRows.map((_, i) => (
            <Cell key={i} fill={pastelAt(i)} />
          ))}
        </Pie>
        <Tooltip content={<ChartTooltip />} />
        <Legend wrapperStyle={LEGEND_STYLE} iconType="circle" />
      </PieChart>
    );
  } else {
    const ChartTag = chartType === 'area' ? AreaChart : chartType === 'line' ? LineChart : BarChart;
    plot = (
      <ChartTag data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke={GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="category" tick={AXIS_TICK} axisLine={false} tickLine={false} />
        <YAxis tickFormatter={formatTick} tick={AXIS_TICK} axisLine={false} tickLine={false} width={40} />
        <Tooltip
          content={<ChartTooltip />}
          cursor={chartType === 'bar'
            ? { fill: 'rgba(var(--ink-rgb), 0.08)' }
            : { stroke: 'var(--ink-faint)', strokeWidth: 1 }}
        />
        <Legend iconType="circle" wrapperStyle={LEGEND_STYLE} />
        {series.map((s, i) => {
          const color = namedColor(s.name, i);
          const name = s.name || `Series ${i + 1}`;
          if (chartType === 'area') {
            return <Area key={name} type="monotone" dataKey={name} stroke={color} fill={color} fillOpacity={0.22} strokeWidth={2} />;
          }
          if (chartType === 'line') {
            return <Line key={name} type="monotone" dataKey={name} stroke={color} strokeWidth={2} dot={{ r: 3 }} />;
          }
          const perBar = series.length === 1
            ? categoryColors(rows.length, normalized.bar_colors)
            : null;
          return (
            <Bar key={name} dataKey={name} fill={color} radius={[6, 6, 0, 0]} maxBarSize={42}>
              {perBar
                ? rows.map((_, idx) => <Cell key={idx} fill={perBar[idx]} />)
                : null}
            </Bar>
          );
        })}
      </ChartTag>
    );
  }

  return (
    <div className={variant === 'canvas' ? 'live-chart live-chart-canvas' : 'live-chart'} style={styles.wrap} role="img" aria-label={claim}>
      {normalized.title && <div style={styles.title}>{normalized.title}</div>}
      <div style={{ width: '100%', minWidth: 0, height: plotHeight }}>
        <ResponsiveContainer width="100%" height={plotHeight}>
          {plot}
        </ResponsiveContainer>
      </div>
      {normalized.caption && <div style={styles.caption}>{normalized.caption}</div>}
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
