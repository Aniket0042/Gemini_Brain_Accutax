export const PREVIEW_ROWS = 100;
export const PREVIEW_COLUMNS = 30;

// File-derived strings become text nodes. Hyperlinks, rich HTML and formulas are never interpreted as markup.
export function renderSheetText(host, XLSX, sheet) {
  host.replaceChildren();
  if (!sheet?.['!ref']) return;
  const range = XLSX.utils.decode_range(sheet['!ref']);
  range.e.r = Math.min(range.e.r, range.s.r + PREVIEW_ROWS - 1);
  range.e.c = Math.min(range.e.c, range.s.c + PREVIEW_COLUMNS - 1);
  const rows = XLSX.utils.sheet_to_json(sheet, { header: 1, raw: false, defval: '', range });
  const table = host.ownerDocument.createElement('table');
  for (const row of rows) {
    const tr = host.ownerDocument.createElement('tr');
    for (const value of row.slice(0, PREVIEW_COLUMNS)) {
      const td = host.ownerDocument.createElement('td');
      td.textContent = String(value ?? '').slice(0, 10_000);
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
  host.appendChild(table);
}
