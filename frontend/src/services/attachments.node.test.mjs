// Run: node --test src/services/attachments.node.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  extensionOf, fileKind, kindGroup, humanSize, checkFile, nameForPasted, composerState,
  starterPrompts, attachmentsToSend, chipsFromBlocks, uploadErrorMessage,
} from './attachments.js';

const CONFIG = { max_bytes: 10 * 1024 * 1024, max_files: 3, accept: ['.pdf', '.png', '.jpg', '.docx', '.xlsx', '.csv'] };
const file = (name, size = 1000) => ({ name, size });

test('extension and kind come from the name, case blind', () => {
  assert.equal(extensionOf('Bill.PDF'), '.pdf');
  assert.equal(extensionOf('archive.tar.gz'), '.gz');
  assert.equal(extensionOf('noext'), '');
  assert.deepEqual(['a.pdf', 'a.JPG', 'a.webp', 'a.docx', 'a.xlsx', 'a.csv', 'a.exe'].map(fileKind),
    ['pdf', 'image', 'image', 'docx', 'xlsx', 'csv', 'file']);
  assert.equal(kindGroup('jpeg'), 'image');
  assert.equal(kindGroup('pdf'), 'pdf');
});

test('sizes are written plainly', () => {
  assert.equal(humanSize(512), '512 B');
  assert.equal(humanSize(2048), '2 KB');
  assert.equal(humanSize(5.5 * 1024 * 1024), '5.5 MB');
  assert.equal(humanSize(12 * 1024 * 1024), '12 MB');
});

test('a good file passes', () => {
  assert.deepEqual(checkFile(file('bill.pdf'), CONFIG, 0), { ok: true });
});

test('instant refusals say why', () => {
  assert.match(checkFile(file('setup.exe'), CONFIG).message, /not supported/);
  assert.match(checkFile(file('big.pdf', 11 * 1024 * 1024), CONFIG).message, /11 MB.*limit is 10 MB/);
  assert.match(checkFile(file('empty.pdf', 0), CONFIG).message, /empty/);
  assert.match(checkFile(file('a.pdf'), CONFIG, 3).message, /up to 3 files/);
});

test('exactly at the size limit is allowed, one byte over is not', () => {
  assert.equal(checkFile(file('a.pdf', CONFIG.max_bytes), CONFIG).ok, true);
  assert.equal(checkFile(file('a.pdf', CONFIG.max_bytes + 1), CONFIG).ok, false);
});

test('without a config the defaults apply', () => {
  assert.equal(checkFile(file('a.webp'), null).ok, true);
  assert.equal(checkFile(file('a.zip'), null).ok, false);
});

test('a pasted screenshot gets a dated name', () => {
  const when = new Date(2026, 9, 12, 8, 5, 9);
  assert.equal(nameForPasted({ name: 'image.png', type: 'image/png' }, when), 'pasted-image-20261012-080509.png');
  assert.equal(nameForPasted({ name: '', type: 'image/jpeg' }, when), 'pasted-image-20261012-080509.jpg');
  assert.equal(nameForPasted({ name: 'receipt.jpg', type: 'image/jpeg' }, when), 'receipt.jpg');
});

test('the composer blocks sending until every file is ready, and says why', () => {
  assert.equal(composerState([]).blocked, false);
  assert.equal(composerState([{ status: 'ready' }]).blocked, false);
  assert.match(composerState([{ status: 'uploading' }, { status: 'ready' }]).blockedReason, /uploading/);
  assert.match(composerState([{ status: 'reading' }]).blockedReason, /still being read/);
  assert.match(composerState([{ status: 'failed' }, { status: 'ready' }]).blockedReason, /Remove the file/);
  assert.equal(composerState([{ status: 'reading' }]).busy, true);
  assert.equal(composerState([{ status: 'failed' }]).busy, false);
});

test('starter questions follow what was read', () => {
  assert.deepEqual(starterPrompts([]), []);
  assert.deepEqual(starterPrompts([{ status: 'reading', documentType: 'tax_invoice' }]), []);
  assert.equal(starterPrompts([{ status: 'ready', documentType: 'tax_invoice' }])[0], 'Read this bill');
  assert.ok(starterPrompts([{ status: 'ready', documentType: 'statement' }]).includes('Does this statement match our books?'));
  assert.deepEqual(starterPrompts([{ status: 'ready', documentType: 'other' }]), ['Summarize this file']);
  assert.equal(starterPrompts([{ status: 'ready', documentType: 'receipt' }, { status: 'ready', documentType: 'statement' }]).length, 4);
});

test('contracts and budgets get their own questions', () => {
  const contract = starterPrompts([{ status: 'ready', documentType: 'contract' }]);
  assert.equal(contract[0], 'What are the payment terms?');
  assert.ok(contract.length >= 2 && !contract.includes('Summarize this file'));
  const budget = starterPrompts([{ status: 'ready', documentType: 'budget' }]);
  assert.deepEqual(budget, ['Compare this budget with our actuals', 'Where are we over or under budget?']);
  assert.deepEqual(starterPrompts([{ status: 'ready', documentType: 'other' }]), ['Summarize this file']);
});

test('only ready files are sent', () => {
  const items = [
    { id: 'a', status: 'ready', name: 'a.pdf', size: 1, kind: 'pdf', pages: 2, extra: 'x' },
    { id: 'b', status: 'failed', name: 'b.pdf' },
    { id: 'c', status: 'reading', name: 'c.pdf' },
  ];
  assert.deepEqual(attachmentsToSend(items), { ids: ['a'], chips: [{ id: 'a', name: 'a.pdf', size: 1, kind: 'pdf', pages: 2 }] });
  assert.deepEqual(attachmentsToSend([]), { ids: [], chips: [] });
});

test('chips come back from a saved message', () => {
  assert.deepEqual(chipsFromBlocks([{ type: 'kpi_grid' }, { type: 'attachments', items: [{ id: 'a', name: 'a.pdf' }] }]), [{ id: 'a', name: 'a.pdf' }]);
  assert.deepEqual(chipsFromBlocks(null), []);
  assert.deepEqual(chipsFromBlocks([{ type: 'attachments' }]), []);
});

test('errors use the server message when there is one', () => {
  assert.equal(uploadErrorMessage(422, { detail: { code: 'encrypted', message: 'This PDF is password-protected.' } }), 'This PDF is password-protected.');
  assert.equal(uploadErrorMessage(403, { detail: 'File upload is not available for this account.' }), 'File upload is not available for this account.');
  assert.match(uploadErrorMessage(401, null), /session has expired/);
  assert.match(uploadErrorMessage(0, null), /Could not reach/);
  assert.match(uploadErrorMessage(500, {}), /could not be uploaded/);
});
