/**
 * Chart palette — mirror of backend/src/gemini_brain/artifacts/theme.py.
 *
 * The backend resolves colors per chart (`series_colors`, `point_colors`) and
 * ships them in every chart block, so the canvas, chat card, and exported
 * files paint each entity the same color. These rules are only the fallback
 * for chart blocks that arrive without resolved colors. A backend unit test
 * (test_frontend_palette_mirror_matches_backend) keeps the hex lists equal.
 */

export const PRIMARY = '#0E8A75';

// Categorical hues in fixed order, never cycled. Past slot 7 a category folds
// into "Other" (OTHER_COLOR) instead of repeating a hue.
export const CATEGORICAL = [
  '#0E8A75', // 1 teal (brand)
  '#2A78D6', // 2 blue
  '#EB6834', // 3 orange
  '#4A3AA7', // 4 violet
  '#E87BA4', // 5 magenta
  '#EDA100', // 6 yellow
  '#E34948', // 7 red
];

export const OTHER_COLOR = '#94A3B8';

const SEMANTIC_SLOTS = [
  [['revenue', 'income', 'sales', 'receipts', 'inflow'], 0],
  [['expense', 'expenses', 'cost', 'costs', 'spend', 'outflow', 'purchases'], 2],
  [['profit', 'net', 'cashflow', 'cash', 'margin'], 1],
];

function tokens(name) {
  return String(name ?? '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

function semanticSlot(name) {
  const toks = tokens(name);
  const joined = toks.join('');
  for (const [words, slot] of SEMANTIC_SLOTS) {
    if (words.some((w) => toks.includes(w)) || words.includes(joined)) return slot;
  }
  return null;
}

const isOther = (name) => String(name ?? '').trim().toLowerCase() === 'other';

export function seriesColor(name, index) {
  if (isOther(name)) return OTHER_COLOR;
  const slot = semanticSlot(name);
  if (slot !== null) return CATEGORICAL[slot];
  return index < CATEGORICAL.length ? CATEGORICAL[index] : OTHER_COLOR;
}

function categoryColors(categories) {
  const slots = categories.map(semanticSlot);
  const taken = new Set(slots.filter((s) => s !== null));
  const free = CATEGORICAL.map((_, i) => i).filter((i) => !taken.has(i));
  return categories.map((cat, i) => {
    if (isOther(cat)) return OTHER_COLOR;
    if (slots[i] !== null) return CATEGORICAL[slots[i]];
    return free.length ? CATEGORICAL[free.shift()] : OTHER_COLOR;
  });
}

/** Same rules as theme.resolve_chart_colors; used only when the block has no colors. */
export function resolveChartColors(chart) {
  if (Array.isArray(chart.series_colors) && chart.series_colors.length) {
    return { seriesColors: chart.series_colors, pointColors: chart.point_colors || null };
  }
  const type = String(chart.chart_type || 'bar').toLowerCase();
  const series = chart.series || [];
  const cats = chart.categories || [];
  const seriesColors = series.length === 1
    ? [semanticSlot(series[0].name) === null ? PRIMARY : seriesColor(series[0].name, 0)]
    : series.map((s, i) => seriesColor(s.name, i));
  let pointColors = null;
  if (type === 'pie' || type === 'donut') {
    pointColors = categoryColors(cats);
  } else if (type === 'waterfall') {
    const totals = new Set(chart.total_indices || []);
    const values = series[0]?.data || [];
    pointColors = cats.map((_, i) => {
      if (totals.has(i)) return PRIMARY;
      return Number(values[i]) < 0 ? CATEGORICAL[2] : CATEGORICAL[1];
    });
  } else if (
    (type === 'bar' || type === 'hbar') && series.length === 1 && cats.length > 1
    && cats.every((c) => semanticSlot(c) !== null)
    && new Set(cats.map(semanticSlot)).size === cats.length
  ) {
    pointColors = categoryColors(cats);
  }
  return { seriesColors, pointColors };
}
