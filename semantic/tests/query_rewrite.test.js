// Tests for the tenant guard in cube.js. Runs without Cube or a database:
//   node --test semantic/tests/*.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

process.env.CUBEJS_API_SECRET = 'x'.repeat(40);
delete process.env.CUBEJS_REFRESH_WORKER;
const { queryRewrite } = require(path.join(__dirname, '..', 'cube.js'));

const now = Math.floor(Date.now() / 1000);
const ctx = (extra = {}) => ({ securityContext: { organization_ids: [24, 25], iat: now, exp: now + 60, ...extra } });
const orgFilters = (q) => q.filters.filter((f) => /\.organization_id$/.test(f.member));

test('adds an organization filter for the view', () => {
  const q = queryRewrite({ measures: ['pnl.net_profit'] }, ctx());
  assert.deepEqual(orgFilters(q), [{ member: 'pnl.organization_id', operator: 'equals', values: ['24', '25'] }]);
});

test('an or-filter from the caller stays ANDed with the scope', () => {
  const q = queryRewrite({
    measures: ['pnl.net_profit'],
    filters: [{ or: [{ member: 'pnl.organization_id', operator: 'set' }, { member: 'pnl.net_profit', operator: 'lt', values: ['0'] }] }],
  }, ctx());
  assert.equal(q.filters.length, 2);
  assert.deepEqual(q.filters[1].values, ['24', '25']);
});

test('refuses a raw cube', () => {
  assert.throws(() => queryRewrite({ measures: ['gl_lines.revenue'] }, ctx()), /not queryable/);
});

test('refuses a raw cube hidden in a nested filter', () => {
  assert.throws(() => queryRewrite({
    measures: ['pnl.revenue'],
    filters: [{ and: [{ member: 'organizations.name', operator: 'set' }] }],
  }, ctx()), /not queryable/);
});

test('refuses a token without organizations', () => {
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ organization_ids: [] })), /no organization scope/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ organization_ids: undefined })), /no organization scope/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ organization_ids: ['24'] })), /no organization scope/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ organization_ids: [0] })), /no organization scope/);
});

test('refuses too many organizations', () => {
  const ids = Array.from({ length: 26 }, (_, i) => i + 1);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ organization_ids: ids })), /no organization scope/);
});

test('refuses a long-lived token', () => {
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ exp: now + 3600 })), /short-lived/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, ctx({ exp: undefined })), /short-lived/);
});

test('refuses a missing security context', () => {
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'] }, { securityContext: undefined }), /short-lived/);
});

test('refuses an empty query', () => {
  assert.throws(() => queryRewrite({}, ctx()), /empty query/);
});

test('caps the row limit', () => {
  assert.equal(queryRewrite({ measures: ['pnl.revenue'], limit: 50000 }, ctx()).limit, 500);
  assert.equal(queryRewrite({ measures: ['pnl.revenue'] }, ctx()).limit, 500);
  assert.equal(queryRewrite({ measures: ['pnl.revenue'], limit: 20 }, ctx()).limit, 20);
});

test('reads members from order, time dimensions and segments', () => {
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'], order: { 'gl_lines.revenue': 'desc' } }, ctx()), /not queryable/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'], order: [['gl_lines.revenue', 'desc']] }, ctx()), /not queryable/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'], timeDimensions: [{ dimension: 'gl_lines.transaction_date' }] }, ctx()), /not queryable/);
  assert.throws(() => queryRewrite({ measures: ['pnl.revenue'], segments: ['gl_lines.big'] }, ctx()), /not queryable/);
});

test('cube.js refuses to load without a strong secret', () => {
  const { execFileSync } = require('node:child_process');
  const script = `require(${JSON.stringify(path.join(__dirname, '..', 'cube.js'))})`;
  assert.throws(() => execFileSync(process.execPath, ['-e', script], {
    env: { ...process.env, CUBEJS_API_SECRET: 'short' }, stdio: 'pipe',
  }));
});
