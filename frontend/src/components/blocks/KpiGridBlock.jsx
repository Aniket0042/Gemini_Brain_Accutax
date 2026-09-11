import React from 'react';
import { ShieldAlert } from 'lucide-react';

/**
 * A figure is flagged only when it appears in the grounding check's
 * `unmatched` list — the same number showed up in prose and could not be
 * traced back to the retrieved data.
 */
function isFlagged(item, verification) {
  if (!item.numeric || !verification?.unmatched?.length) return false;
  const bare = String(item.value).replace(/[^0-9.]/g, '');
  if (!bare) return false;
  return verification.unmatched.some((u) => String(u).replace(/[^0-9.]/g, '') === bare);
}

function formatIsoDate(iso) {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "2026-01-01 to 2026-12-31" → "1 Jan 2026 – 31 Dec 2026" */
function formatPeriod(text) {
  if (!text) return null;
  const m = String(text).match(/^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$/i);
  if (!m) return text;
  return `${formatIsoDate(m[1])} – ${formatIsoDate(m[2])}`;
}

function isPeriodItem(item) {
  if (!item) return false;
  if (String(item.label || '').toLowerCase() === 'period') return true;
  return /^\d{4}-\d{2}-\d{2}\s+to\s+\d{4}-\d{2}-\d{2}$/i.test(String(item.value || ''));
}

/** Duplicate "formatted_*" fields the API sometimes ships next to the real number. */
function isNoiseItem(item) {
  const k = String(item?.label || '').toLowerCase().replace(/\s+/g, '_');
  return k.startsWith('formatted_') || k.endsWith('_formatted');
}

function Figure({ item, verification }) {
  const flagged = isFlagged(item, verification);
  return (
    <>
      <span className="kpi-figure">{item.value}</span>
      {flagged && (
        <ShieldAlert
          size={12}
          color="var(--warning-ink, var(--warn))"
          style={{ marginLeft: '4px', verticalAlign: 'middle' }}
          aria-label="Could not be traced to the retrieved data"
        />
      )}
    </>
  );
}

function MetricList({ items, verification }) {
  if (!items.length) return null;
  return (
    <dl className="kpi-list">
      {items.map((item, i) => (
        <div key={`${item.label}-${i}`} className="kpi-row">
          <dt>{item.label}</dt>
          <dd>
            <Figure item={item} verification={verification} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * KpiGridBlock — compact labelled rows, not a spoken run-on sentence.
 * A handful of figures become a two-column list; grouped statements keep a
 * section heading above each cluster.
 */
export function KpiGridBlock({ block, verification }) {
  const rawItems = (block.items || []).filter((item) => !isNoiseItem(item));
  const periodItem = rawItems.find(isPeriodItem);
  const metrics = rawItems.filter((item) => !isPeriodItem(item));
  const period = formatPeriod(block.period || periodItem?.value);

  const ungrouped = metrics.filter((i) => !i.section);
  const sections = [];
  const seen = new Set();
  for (const item of metrics) {
    if (item.section && !seen.has(item.section)) {
      seen.add(item.section);
      sections.push(item.section);
    }
  }

  if (metrics.length === 0 && !period) {
    return <p className="kpi-empty">No records found.</p>;
  }

  return (
    <div className="kpi-grid">
      {period && <p className="kpi-caption">{period}</p>}
      <MetricList items={ungrouped} verification={verification} />
      {sections.map((section) => (
        <div key={section} className="kpi-section">
          <h4 className="kpi-section-title">{section}</h4>
          <MetricList
            items={metrics.filter((i) => i.section === section)}
            verification={verification}
          />
        </div>
      ))}
    </div>
  );
}
