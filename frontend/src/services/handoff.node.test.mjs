// Run: node --test src/services/handoff.node.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { USED_CODES_KEY, claimCode, handoffCodeFrom, toWidgetHandoff, urlWithoutCode } from './handoff.js';

const CODE = 'XdL6rgujnLk_2-HpPMdVJfVgvjWtLvntRKq3nILsN10';

test('the code is read only with handoff=1 and a well-formed value', () => {
  assert.equal(handoffCodeFrom(`?handoff=1&code=${CODE}`), CODE);
  assert.equal(handoffCodeFrom(`?code=${CODE}`), null); // an Auth0 callback also carries ?code
  assert.equal(handoffCodeFrom('?handoff=1'), null); // the VM dashboard: postMessage path
  assert.equal(handoffCodeFrom('?handoff=1&code=short'), null);
  assert.equal(handoffCodeFrom('?handoff=1&code=has%20space0123456789abcdef'), null);
  assert.equal(handoffCodeFrom(''), null);
});

test('the code is stripped from the URL, the rest kept', () => {
  assert.equal(urlWithoutCode(`https://chat.example/?handoff=1&code=${CODE}#x`), '/?handoff=1#x');
  assert.equal(urlWithoutCode(`https://chat.example/p?code=${CODE}`), '/p');
  assert.equal(urlWithoutCode('https://chat.example/?handoff=1'), null);
});

function memoryStorage() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)) };
}

test('a code is spent once per tab', () => {
  const s = memoryStorage();
  assert.equal(claimCode(CODE, s), true);
  assert.equal(claimCode(CODE, s), false);
  assert.equal(claimCode(`${CODE}x`, s), true);
  assert.deepEqual(JSON.parse(s.getItem(USED_CODES_KEY)), [CODE, `${CODE}x`]);
});

test('broken storage does not block the first use', () => {
  const broken = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } };
  assert.equal(claimCode(CODE, broken), true);
});

test('the redeemed session takes the postMessage handoff shape', () => {
  const out = toWidgetHandoff({
    access_token: 'ours', email: 'u@example.com', name: 'U', organization_id: 2,
    session_id: '77199b54-7d5d-454d-b75a-42ff08f2492a', current_page: '/invoices',
    conversation: [{ role: 'user', content: 'hi' }],
  });
  assert.deepEqual(out, {
    type: 'accutax-widget-handoff', token: 'ours', email: 'u@example.com', name: 'U', orgId: 2,
    sessionId: '77199b54-7d5d-454d-b75a-42ff08f2492a', currentPage: '/invoices',
    conversation: [{ role: 'user', content: 'hi' }],
  });
  assert.deepEqual(toWidgetHandoff({ access_token: 't', email: 'e' }), {
    type: 'accutax-widget-handoff', token: 't', email: 'e', name: 'e', orgId: null, sessionId: null,
    currentPage: null, conversation: [],
  });
});
