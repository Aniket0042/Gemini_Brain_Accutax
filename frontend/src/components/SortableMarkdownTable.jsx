import React, { Children, cloneElement, isValidElement, useState } from 'react';
import { ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';

/**
 * ReactMarkdown `table` renderer: every table in an answer gets the behaviour of the structured
 * TableBlock — click a header to sort (again to reverse, a third time to reset) and a header that
 * stays pinned while a long table scrolls in its own box.
 *
 * Rows are reordered as rendered, so links inside cells (an invoice number) keep working. Sort keys
 * come from each cell's text in the Markdown syntax tree. A "Total" row stays at the bottom.
 */

function textOf(node) {
  if (!node) return '';
  if (node.type === 'text') return node.value || '';
  return (node.children || []).map(textOf).join('');
}

function elementsOf(node, tagName) {
  return (node?.children || []).filter((c) => c.type === 'element' && (!tagName || c.tagName === tagName));
}

// "AED 1,234.50", "-832,224.00", "12.5%", "(1,200)" are numbers; "INV-0012", "2026-01-01", "n/m" are not.
const NUMBER = /^\(?\s*-?\s*(?:[A-Z]{3}\s*)?-?\s*\d[\d,]*(?:\.\d+)?\s*%?\s*\)?$/;

export function sortValue(text) {
  const t = String(text ?? '').trim();
  if (NUMBER.test(t)) {
    const n = Number(t.replace(/[^0-9.]/g, ''));
    const negative = /^\(|-/.test(t);
    if (!Number.isNaN(n)) return negative ? -n : n;
  }
  return t.toLowerCase();
}

export function compareValues(a, b) {
  const an = typeof a === 'number';
  const bn = typeof b === 'number';
  if (an && bn) return a - b;
  if (an !== bn) return an ? -1 : 1; // numbers before text ("n/m", "—")
  return a < b ? -1 : a > b ? 1 : 0;
}

const isTotalRow = (cells) => /^\s*(grand\s+)?total\b/i.test(cells[0] || '');

export function SortableMarkdownTable({ node, children, ...props }) {
  const [sort, setSort] = useState(null); // { col, dir: 1 | -1 }

  const kids = Children.toArray(children).filter(isValidElement);
  const thead = kids.find((c) => c.type === 'thead');
  const tbody = kids.find((c) => c.type === 'tbody');
  const rowNodes = elementsOf(elementsOf(node, 'tbody')[0], 'tr');
  const rowEls = tbody ? Children.toArray(tbody.props.children).filter(isValidElement) : [];
  const sortable = Boolean(thead) && rowEls.length > 1 && rowEls.length === rowNodes.length;

  if (!sortable) {
    return (
      <div className="markdown-table-wrapper sortable-table">
        <table {...props}>{children}</table>
      </div>
    );
  }

  const rows = rowEls.map((el, i) => {
    const cells = elementsOf(rowNodes[i]).map(textOf);
    return { el, cells, total: isTotalRow(cells) };
  });
  let bodyRows = rows;
  if (sort) {
    const data = rows.filter((r) => !r.total);
    data.sort((a, b) => sort.dir * compareValues(sortValue(a.cells[sort.col]), sortValue(b.cells[sort.col])));
    bodyRows = [...data, ...rows.filter((r) => r.total)];
  }

  const toggle = (col) => setSort((prev) => {
    if (!prev || prev.col !== col) return { col, dir: 1 };
    if (prev.dir === 1) return { col, dir: -1 };
    return null;
  });

  const headRow = Children.toArray(thead.props.children).filter(isValidElement)[0];
  const headCells = headRow ? Children.toArray(headRow.props.children).filter(isValidElement) : [];
  const header = cloneElement(thead, {}, headRow && cloneElement(headRow, {}, headCells.map((th, col) => {
    const active = sort?.col === col;
    const Icon = active ? (sort.dir === 1 ? ArrowUp : ArrowDown) : ArrowUpDown;
    return cloneElement(
      th,
      {
        key: th.key ?? col,
        className: 'sortable-th',
        onClick: () => toggle(col),
        title: 'Sort',
        'aria-sort': active ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none',
      },
      <span className="sortable-th-inner">
        <span>{th.props.children}</span>
        <Icon size={11} style={{ opacity: active ? 1 : 0.35, flexShrink: 0 }} />
      </span>,
    );
  })));

  return (
    <div className="markdown-table-wrapper sortable-table">
      <table {...props}>
        {header}
        <tbody>{bodyRows.map((r) => r.el)}</tbody>
      </table>
    </div>
  );
}
