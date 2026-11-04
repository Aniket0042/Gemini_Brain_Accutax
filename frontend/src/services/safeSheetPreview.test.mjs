import assert from 'node:assert/strict';
import { test } from 'node:test';
import * as XLSX from 'xlsx';
import { renderSheetText } from './safeSheetPreview.mjs';

test('spreadsheet HTML and JavaScript links are rendered only as text', () => {
  const created = [];
  const ownerDocument = { createElement(tag) {
    const node = { tag, children: [], ownerDocument, appendChild(child) { this.children.push(child); } };
    Object.defineProperty(node, 'innerHTML', { set() { throw new Error('Unsafe HTML assignment'); } });
    created.push(node);
    return node;
  } };
  const host = { ownerDocument, replaceChildren() {}, appendChild() {} };
  const sheet = XLSX.utils.aoa_to_sheet([['<img src=x onerror=alert(1)>', 'Open details']]);
  sheet.B1.l = { Target: 'javascript:alert(1)' };
  renderSheetText(host, XLSX, sheet);
  assert.equal(created.filter((node) => node.tag === 'td')[0].textContent, '<img src=x onerror=alert(1)>');
  assert.ok(created.every((node) => ['table', 'tr', 'td'].includes(node.tag)));
});
