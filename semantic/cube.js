// cube.js: Cube Core security configuration for Accutax.
//
// Tenant isolation for every API query:
//   1. Cube's default checkAuth verifies the HS256 signature, aud, iss and exp
//      (CUBEJS_JWT_* environment variables).
//   2. queryRewrite refuses tokens that live longer than two minutes or carry no
//      organization scope, refuses members outside the allow-listed views, and
//      ANDs an organization_id filter onto every view the query touches.
// The AI never sets organization_id. Gemini Brain mints the token from
// api.auth.authorize_org_scope output only (backend/src/gemini_brain/semantic/cube_client.py).
// Tests: semantic/tests/query_rewrite.test.js.

const secret = process.env.CUBEJS_API_SECRET || '';
if (secret.length < 32) {
  throw new Error('CUBEJS_API_SECRET is missing or shorter than 32 characters');
}

const QUERYABLE_VIEWS = new Set([
  'pnl', 'balance_sheet', 'sales', 'purchases', 'receivables', 'payables', 'vat', 'ledger',
]);
const MAX_TOKEN_LIFETIME_SECONDS = 120;
const MAX_ORGS = 25;
const MAX_ROWS = 500;
// The refresh worker builds rollups and serves no API traffic; its port is never published.
const IS_REFRESH_WORKER = process.env.CUBEJS_REFRESH_WORKER === 'true';

function referencedMembers(query) {
  const out = [];
  const add = (m) => { if (typeof m === 'string' && m) out.push(m); };
  (query.measures || []).forEach(add);
  (query.dimensions || []).forEach(add);
  (query.segments || []).forEach(add);
  (query.timeDimensions || []).forEach((td) => add(td && td.dimension));
  const walk = (filters) => (filters || []).forEach((f) => {
    if (!f) return;
    if (Array.isArray(f.and)) walk(f.and);
    else if (Array.isArray(f.or)) walk(f.or);
    else add(f.member || f.dimension);
  });
  walk(query.filters);
  if (Array.isArray(query.order)) {
    query.order.forEach((o) => add(Array.isArray(o) ? o[0] : o && o.id));
  } else if (query.order && typeof query.order === 'object') {
    Object.keys(query.order).forEach(add);
  }
  return out;
}

function organizationIds(ctx) {
  const ids = ctx && ctx.organization_ids;
  if (!Array.isArray(ids) || ids.length === 0 || ids.length > MAX_ORGS) return null;
  if (!ids.every((v) => Number.isInteger(v) && v > 0)) return null;
  return ids.map(String);
}

function queryRewrite(query, { securityContext }) {
  if (IS_REFRESH_WORKER) return query;

  const ctx = securityContext || {};
  if (typeof ctx.iat !== 'number' || typeof ctx.exp !== 'number'
      || ctx.exp - ctx.iat > MAX_TOKEN_LIFETIME_SECONDS) {
    throw new Error('Forbidden: token must be short-lived');
  }
  const orgIds = organizationIds(ctx);
  if (!orgIds) throw new Error('Forbidden: token carries no organization scope');

  const members = referencedMembers(query);
  if (members.length === 0) throw new Error('Forbidden: empty query');
  const views = new Set(members.map((m) => m.split('.')[0]));
  for (const v of views) {
    if (!QUERYABLE_VIEWS.has(v)) throw new Error(`Forbidden: '${v}' is not queryable`);
  }

  query.filters = Array.isArray(query.filters) ? query.filters : [];
  for (const v of views) {
    query.filters.push({ member: `${v}.organization_id`, operator: 'equals', values: orgIds });
  }
  query.limit = Math.min(Number(query.limit) || MAX_ROWS, MAX_ROWS);
  return query;
}

module.exports = {
  // One compiled model for every tenant. Isolation is by filter, not by model copy.
  contextToAppId: () => 'accutax',
  queryRewrite,
};
