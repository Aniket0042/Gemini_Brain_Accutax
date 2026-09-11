import React, { useMemo, useState } from 'react';
import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

/**
 * Strips formatting ("AED 1,234.50" -> 1234.5, "42%" -> 42) so a column of
 * formatted currency/percentage strings still sorts numerically instead of
 * alphabetically ("AED 9.00" would otherwise sort before "AED 10.00").
 */
function sortValue(v) {
  if (typeof v === 'number') return v;
  if (typeof v !== 'string') return v;
  const cleaned = v.replace(/[^0-9.-]/g, '');
  if (cleaned && !Number.isNaN(Number(cleaned)) && /\d/.test(cleaned)) {
    return Number(cleaned);
  }
  return v.toLowerCase();
}

/**
 * TableBlock — renders the structured {columns, rows} shape from
 * render_table_block() (backend). Real columns, not a parsed markdown
 * string — click a header to sort; the header row stays pinned while the
 * body scrolls for anything taller than the card.
 *
 * No outer box: a --surface-2 wash on the header row is what reads as
 * "table", the same way the KPI tiles above it do — one shared visual
 * language for "this is retrieved data" instead of every block drawing its
 * own border around itself.
 */
export function TableBlock({ block }) {
  const columns = block.columns || [];
  const rows = block.rows || [];
  const [sort, setSort] = useState(null); // { key, dir: 1 | -1 }

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const { key, dir } = sort;
    return [...rows].sort((a, b) => {
      const av = sortValue(a[key]);
      const bv = sortValue(b[key]);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }, [rows, sort]);

  if (columns.length === 0 || rows.length === 0) {
    return <p style={styles.empty}>No records found.</p>;
  }

  const toggleSort = (key) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: 1 };
      if (prev.dir === 1) return { key, dir: -1 };
      return null;
    });
  };

  return (
    <div style={styles.wrap}>
      {block.period && <div style={styles.period}>{block.period}</div>}
      <div style={styles.scrollBox}>
        <table style={styles.table}>
          <thead>
            <tr>
              {columns.map((col, i) => {
                const active = sort?.key === col.key;
                const Icon = active ? (sort.dir === 1 ? ArrowUp : ArrowDown) : ArrowUpDown;
                const edgeRadius = {
                  ...(i === 0 ? { borderTopLeftRadius: 'var(--radius-sm)' } : {}),
                  ...(i === columns.length - 1 ? { borderTopRightRadius: 'var(--radius-sm)' } : {}),
                };
                return (
                  <th key={col.key} style={{ ...styles.th, ...edgeRadius, textAlign: col.align || 'left' }}>
                    <button
                      type="button"
                      className="table-sort-btn"
                      style={{
                        ...styles.sortBtn,
                        justifyContent: col.align === 'right' ? 'flex-end' : 'flex-start',
                      }}
                      onClick={() => toggleSort(col.key)}
                      title={`Sort by ${col.label}`}
                    >
                      <span>{col.label}</span>
                      <Icon size={11} style={{ opacity: active ? 1 : 0.35, flexShrink: 0 }} />
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sortedRows.map((row, i) => {
              const isLast = i === sortedRows.length - 1;
              return (
                <tr key={i}>
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      style={{
                        ...styles.td,
                        textAlign: col.align || 'left',
                        ...(isLast ? { borderBottom: 'none' } : {}),
                      }}
                    >
                      {row[col.key]}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {block.truncated && (
        <p style={styles.truncated}>Showing {rows.length} of {block.total_rows} total records.</p>
      )}
    </div>
  );
}

const styles = {
  wrap: {
    display: 'flex',
    flexDirection: 'column',
    gap: '6px',
    minWidth: 0,
    width: '100%',
  },
  period: {
    fontSize: 'var(--text-xs)',
    color: 'var(--ink-faint)',
    fontStyle: 'italic',
  },
  scrollBox: {
    overflowX: 'auto',
    overflowY: 'auto',
    maxHeight: '380px',
    borderRadius: 'var(--radius-sm)',
    border: '1px solid var(--border-soft)',
    minWidth: 0,
  },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: 'var(--text-sm)',
  },
  th: {
    padding: 0,
    fontSize: 'var(--text-xs)',
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
    color: 'var(--ink)',
    whiteSpace: 'nowrap',
    position: 'sticky',
    top: 0,
    backgroundColor: 'var(--surface-2)',
  },
  sortBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    width: '100%',
    padding: '8px 12px',
    background: 'none',
    border: 'none',
    color: 'inherit',
    font: 'inherit',
    letterSpacing: 'inherit',
    textTransform: 'inherit',
    cursor: 'pointer',
  },
  td: {
    padding: '8px 12px',
    borderBottom: '1px solid var(--border-soft)',
    color: 'var(--ink-soft)',
    whiteSpace: 'nowrap',
  },
  empty: {
    fontSize: 'var(--text-sm)',
    color: 'var(--ink-faint)',
    fontStyle: 'italic',
    padding: '8px 0',
  },
  truncated: {
    fontSize: 'var(--text-xs)',
    color: 'var(--ink-faint)',
    padding: '4px 14px 0',
    margin: 0,
  },
};
