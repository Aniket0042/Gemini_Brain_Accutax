/**
 * Seed ~5 years of random invoices per organization (owned + collaborator), via API.
 *
 * Copy of accutax_bk_backend/scripts/seed-org-5yr-data.ts, fixed for the seed
 * defects found in test orgs 24-33 (see README.md in this folder):
 *   - Statuses are looked up by name only. The old fallback ids 1/3/5 meant
 *     ACCEPTED/CANCELLED/RECEIVED in this database, not PENDING/PAID/PARTIALLY_PAID,
 *     so a third of "paid" invoices were seeded as CANCELLED. A missing status now
 *     stops the run.
 *   - Invoices and bills are created PENDING with nothing paid, then settled
 *     through the payment endpoints (customer-payment / supplier-payment), so the
 *     app sets PAID / PARTIALLY_PAID itself and posts the cash journal. The old
 *     script wrote amount_paid on the document and never posted a receipt.
 *   - Every rejected create is logged with its reason, and the run stops when
 *     rejections exceed --max-reject-pct.
 *   - Document numbers use --number-prefix (default SEEDV2) so a new run can be
 *     told apart from the old SEED5Y data.
 * The Accutax backend itself is not changed; this script only calls its API.
 *
 * Usage:
 *   npm run seed:org-5yr-data -- --email user@test.com --password 'secret'
 *   npm run seed:org-5yr-data -- --email user@test.com --password 'secret' --years 5 --concurrency 25
 *   npm run seed:org-5yr-data -- --email user@test.com --password 'secret' --owned-only
 *   npm run seed:org-5yr-data -- --email user@test.com --password 'secret' --org-id 2
 *
 *   # All seeded test users (testuser1@gmail.com … testuser1000@gmail.com)
 *   npm run seed:testusers-loop -- --password 'Test@123' --user-from 1 --user-to 1000
 *
 * Requires: API + Redis (async POST /income/create). Processes each org one-by-one.
 */

import axios from 'axios';
import * as dotenv from 'dotenv';
import * as path from 'path';
import Redis from 'ioredis';
import * as readline from 'readline';

// Settings come from ACCUTAX_SEED_ENV (a path) or a .env next to this script.
dotenv.config({ path: process.env.ACCUTAX_SEED_ENV || path.join(__dirname, '.env') });

if (
  process.env.ALLOW_LARGE_SEED !== 'true' &&
  !process.argv.includes('--confirm-seed') &&
  !process.argv.includes('--help')
) {
  throw new Error(
    'Refusing to run large invoice seed without explicit confirmation. Set ALLOW_LARGE_SEED=true or pass --confirm-seed.',
  );
}

const BULL_QUEUE_INCOME_PERSIST = 'income-persist';

function bullQueueKeys(queueName: string) {
  const prefix = process.env.BULLMQ_PREFIX || 'bull';
  return {
    wait: `${prefix}:${queueName}:wait`,
    active: `${prefix}:${queueName}:active`,
    delayed: `${prefix}:${queueName}:delayed`,
  };
}

async function getIncomePersistQueueDepth(client: Redis): Promise<number> {
  const keys = bullQueueKeys(BULL_QUEUE_INCOME_PERSIST);
  const [wait, active, delayed] = await Promise.all([
    client.llen(keys.wait),
    client.llen(keys.active),
    client.zcard(keys.delayed),
  ]);
  return wait + active + delayed;
}

async function waitForQueueBelow(
  client: Redis,
  maxDepth: number,
  pollMs: number,
  label?: string,
): Promise<void> {
  let lastLogged = 0;
  while (true) {
    const depth = await getIncomePersistQueueDepth(client);
    if (depth <= maxDepth) return;
    const now = Date.now();
    if (now - lastLogged > 10_000) {
      const tag = label ? ` (${label})` : '';
      process.stdout.write(
        `\r    Queue backpressure${tag}: waiting (depth ${depth}, max ${maxDepth})...`,
      );
      lastLogged = now;
    }
    await sleep(pollMs);
  }
}

function createSeedRedisClient(): Redis | null {
  const host = process.env.REDIS_HOST;
  if (!host) return null;
  return new Redis({
    host,
    port: parseInt(process.env.REDIS_PORT || '6379', 10),
    password: process.env.REDIS_PASSWORD || undefined,
    maxRetriesPerRequest: null,
    lazyConnect: true,
  });
}

function isApiSuccess(body: {
  error?: boolean;
  code?: number;
  statusCode?: number;
  status?: number;
}): boolean {
  if (body?.error === true) return false;
  const c = body?.code ?? body?.statusCode ?? body?.status;
  return c === 200 || c === 202;
}

interface CliOptions {
  email: string;
  password: string;
  baseUrl: string;
  years: number;
  concurrency: number;
  orgId?: number;
  ownedOnly: boolean;
  collaboratorOnly: boolean;
  dryRun: boolean;
  minPerDay: number;
  maxPerDay: number;
  delayBetweenOrgsMs: number;
  delayBetweenUsersMs: number;
  maxQueueDepth: number;
  queuePollMs: number;
  drainQueueBetweenOrgs: boolean;
  draftPct: number;
  userFrom?: number;
  userTo?: number;
  emailTemplate: string;
  stopOnLoginFail: boolean;
  help: boolean;
  seedingOrder?: 'invoices-first' | 'expenses-first';
  /** Document number prefix; tells a run's documents apart from older seeds. */
  numberPrefix: string;
  /** Stop the run when more than this share of creates or payments is rejected. */
  maxRejectPct: number;
  /** Record payments after creating documents (off: documents stay PENDING). */
  settle: boolean;
  /** Longest wait for queued invoices to be persisted before paying them. */
  settleWaitMs: number;
}

interface LoginData {
  id: number;
  token: string;
}

interface AccessibleOrg {
  id: number;
  name?: string;
  is_collaborator: boolean;
  collaborator_role?: string | null;
}

interface StatusTypeOption {
  id: number;
  value?: string;
}

interface ProductOption {
  id: number;
  description: string;
  unit_price: number;
  revenue_account_id?: number;
}

interface CoaAccountRow {
  id: number;
  account_name: string;
  account_type: string;
  allow_posting: boolean;
}

interface OrgSeedContext {
  organizationId: number;
  userId: number;
  contactIds: number[];
  vendorIds: number[];
  revenueAccountIds: number[];
  expenseAccountIds: number[];
  arAccountId: number | null;
  apAccountId: number | null;
  cashAccountId: number | null;
  coaAccounts: CoaAccountRow[];
  projectIds: Array<number | null>;
  branchIds: Array<number | null>;
  taxRates: Array<{ id: number; rate: number }>;
  products: ProductOption[];
  statusTypes: StatusTypeOption[];
  expenseCategories: Array<{ id: number }>;
}

/** What happens to a document after it is created: paid in full, half paid, or left open. */
type Settlement = 'paid' | 'partial' | 'open';

interface PlannedInvoice {
  invoiceNumber: string;
  payload: Record<string, unknown>;
  settle: Settlement;
  total: number;
  contactId: number;
  date: Date;
}

function printHelp(): void {
  console.log(`
Seed 5-year invoice history (all accessible orgs, one org at a time)

Single user (required: --email and --password):
  --email <email>
  --password <password>

Batch test users (required: --password, --user-from, --user-to):
  --user-from <n>               First index (e.g. 1 → testuser1@gmail.com)
  --user-to <n>                 Last index (e.g. 1000)
  --email-template <pattern>    Default testuser{index}@gmail.com
  --delay-between-users-ms <n>  Pause between users (default 10000)

Optional:
  --base-url <url>              Default http://localhost:\${PORT}
  --years <n>                   Lookback years (default 5)
  --concurrency <n>             Parallel POST /income/create per org (default 20)
  --org-id <id>                 Only this organization
  --owned-only                  Skip collaborator orgs
  --collaborator-only           Skip owned orgs
  --min-per-day <n>             Min invoices per day (default 3)
  --max-per-day <n>             Max invoices per day (default 7)
  --draft-pct <0-100>           Draft share (default 10; lower = more posted journals)
  --delay-between-orgs-ms <n>   Pause between orgs (default 5000)
  --max-queue-depth <n>         Pause POSTs when income-persist depth exceeds this (default 3000; 0=off)
  --queue-poll-ms <n>           How often to recheck queue depth (default 1500)
  --drain-queue-between-orgs    Wait until queue depth is 0 before next org
  --dry-run                     Build payloads only, no API creates
  --number-prefix <text>        Document number prefix (default SEEDV2)
  --max-reject-pct <n>          Stop when more than n% of creates/payments fail (default 2)
  --no-settle                   Skip payments (documents stay PENDING)
  --settle-wait-ms <n>          Longest wait for queued invoices before paying (default 600000)
  --help

Bulk seed (.env on API server recommended):
  INCOME_PERSIST_REMOVE_ON_COMPLETE=true
  INCOME_PERSIST_CONCURRENCY=40
  INCOME_PERSIST_PARALLEL=15
  DB_POOL_MAX=30

Examples:
  npm run seed:org-5yr-data -- --email genthird555@gmail.com --password password
  npm run seed:testusers-loop -- --password 'Test@123' --user-from 1 --user-to 1000 --concurrency 15
  npm run seed:testusers-loop -- --password 'Test@123' --user-from 1 --user-to 5 --dry-run
`);
}

function buildEmailFromTemplate(template: string, index: number): string {
  return template.replace(/\{index\}/g, String(index));
}

function parseArgs(argv: string[]): CliOptions {
  const opts: CliOptions = {
    email: process.env.SEED_EMAIL || '',
    password: process.env.SEED_PASSWORD || '',
    baseUrl:
      process.env.BASE_URL ||
      `http://localhost:${process.env.PORT || 8081}`,
    years: parseInt(process.env.SEED_YEARS || '5', 10),
    concurrency: parseInt(process.env.SEED_CONCURRENCY || '20', 10),
    ownedOnly: false,
    collaboratorOnly: false,
    dryRun: false,
    minPerDay: parseInt(process.env.SEED_MIN_PER_DAY || '3', 10),
    maxPerDay: parseInt(process.env.SEED_MAX_PER_DAY || '7', 10),
    delayBetweenOrgsMs: parseInt(
      process.env.SEED_DELAY_BETWEEN_ORGS_MS || '5000',
      10,
    ),
    delayBetweenUsersMs: parseInt(
      process.env.SEED_DELAY_BETWEEN_USERS_MS || '10000',
      10,
    ),
    maxQueueDepth: parseInt(process.env.SEED_MAX_QUEUE_DEPTH || '3000', 10),
    queuePollMs: parseInt(process.env.SEED_QUEUE_POLL_MS || '1500', 10),
    drainQueueBetweenOrgs: false,
    draftPct: parseInt(process.env.SEED_DRAFT_PCT || '10', 10),
    emailTemplate:
      process.env.SEED_EMAIL_TEMPLATE || 'testuser{index}@gmail.com',
    stopOnLoginFail: false,
    help: false,
    numberPrefix: process.env.SEED_NUMBER_PREFIX || 'SEEDV2',
    maxRejectPct: parseFloat(process.env.SEED_MAX_REJECT_PCT || '2'),
    settle: true,
    settleWaitMs: parseInt(process.env.SEED_SETTLE_WAIT_MS || '600000', 10),
  };

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => argv[++i];
    switch (arg) {
      case '--help':
      case '-h':
        opts.help = true;
        break;
      case '--email':
        opts.email = next();
        break;
      case '--password':
        opts.password = next();
        break;
      case '--base-url':
        opts.baseUrl = next().replace(/\/$/, '');
        break;
      case '--years':
        opts.years = parseInt(next(), 10);
        break;
      case '--concurrency':
        opts.concurrency = parseInt(next(), 10);
        break;
      case '--org-id':
        opts.orgId = parseInt(next(), 10);
        break;
      case '--owned-only':
        opts.ownedOnly = true;
        break;
      case '--collaborator-only':
        opts.collaboratorOnly = true;
        break;
      case '--min-per-day':
        opts.minPerDay = parseInt(next(), 10);
        break;
      case '--max-per-day':
        opts.maxPerDay = parseInt(next(), 10);
        break;
      case '--draft-pct':
        opts.draftPct = Math.min(100, Math.max(0, parseInt(next(), 10)));
        break;
      case '--delay-between-orgs-ms':
        opts.delayBetweenOrgsMs = parseInt(next(), 10);
        break;
      case '--delay-between-users-ms':
        opts.delayBetweenUsersMs = parseInt(next(), 10);
        break;
      case '--max-queue-depth':
        opts.maxQueueDepth = parseInt(next(), 10);
        break;
      case '--queue-poll-ms':
        opts.queuePollMs = parseInt(next(), 10);
        break;
      case '--drain-queue-between-orgs':
        opts.drainQueueBetweenOrgs = true;
        break;
      case '--user-from':
        opts.userFrom = parseInt(next(), 10);
        break;
      case '--user-to':
        opts.userTo = parseInt(next(), 10);
        break;
      case '--email-template':
        opts.emailTemplate = next();
        break;
      case '--stop-on-login-fail':
        opts.stopOnLoginFail = true;
        break;
      case '--order':
        const orderVal = next();
        if (orderVal === 'invoices-first' || orderVal === 'expenses-first') {
          opts.seedingOrder = orderVal;
        } else {
          console.warn(`Invalid order: ${orderVal}. Will prompt instead.`);
        }
        break;
      case '--dry-run':
        opts.dryRun = true;
        break;
      case '--number-prefix':
        opts.numberPrefix = next();
        break;
      case '--max-reject-pct':
        opts.maxRejectPct = parseFloat(next());
        break;
      case '--no-settle':
        opts.settle = false;
        break;
      case '--settle-wait-ms':
        opts.settleWaitMs = parseInt(next(), 10);
        break;
      default:
        if (arg.startsWith('-')) {
          console.warn(`Unknown flag: ${arg}`);
        }
    }
  }
  return opts;
}

function extractList(body: any): any[] {
  if (!body) return [];
  if (Array.isArray(body.data)) return body.data;
  if (Array.isArray(body.data?.data)) return body.data.data;
  if (Array.isArray(body.flat_data)) return body.flat_data;
  return [];
}

/** Flatten hierarchical or flat chart-of-accounts/list payloads. */
function flattenCoaTree(nodes: unknown[]): CoaAccountRow[] {
  const out: CoaAccountRow[] = [];
  for (const raw of nodes) {
    if (!raw || typeof raw !== 'object') continue;
    const node = raw as Record<string, unknown>;
    const id = Number(node.id);
    if (Number.isFinite(id) && id > 0) {
      out.push({
        id,
        account_name: String(node.account_name ?? ''),
        account_type: String(node.account_type ?? ''),
        allow_posting: node.allow_posting !== false,
      });
    }
    const children = node.children;
    if (Array.isArray(children) && children.length > 0) {
      out.push(...flattenCoaTree(children));
    }
  }
  return out;
}

function dedupeCoaAccounts(rows: CoaAccountRow[]): CoaAccountRow[] {
  const byId = new Map<number, CoaAccountRow>();
  for (const row of rows) {
    byId.set(row.id, row);
  }
  return Array.from(byId.values());
}

function extractCoaAccounts(body: unknown): CoaAccountRow[] {
  if (!body || typeof body !== 'object') return [];
  const b = body as Record<string, unknown>;
  const nested = b.data as Record<string, unknown> | undefined;
  const flat = b.flat_data ?? nested?.flat_data;
  if (Array.isArray(flat) && flat.length > 0) {
    return dedupeCoaAccounts(flattenCoaTree(flat));
  }
  if (Array.isArray(b.data)) {
    return dedupeCoaAccounts(flattenCoaTree(b.data));
  }
  return [];
}

/** Pick invoice AR + line revenue accounts only from fetched COA rows. */
function resolveInvoiceCoaFromList(accounts: CoaAccountRow[]): {
  revenueAccountIds: number[];
  arAccountId: number | null;
} {
  const posting = accounts.filter((a) => a.allow_posting);
  let revenueAccountIds = posting
    .filter((a) => a.account_type === 'Revenue')
    .map((a) => a.id);
  if (revenueAccountIds.length === 0) {
    revenueAccountIds = accounts
      .filter((a) => a.account_type === 'Revenue')
      .map((a) => a.id);
  }

  const postingAssets = posting.filter((a) => a.account_type === 'Asset');
  const allAssets = accounts.filter((a) => a.account_type === 'Asset');
  const arCandidate =
    postingAssets.find((a) => /receivable/i.test(a.account_name)) ??
    postingAssets[0] ??
    allAssets.find((a) => /receivable/i.test(a.account_name)) ??
    allAssets[0];

  return {
    revenueAccountIds,
    arAccountId: arCandidate?.id ?? null,
  };
}

function resolveExpenseCoaFromList(accounts: CoaAccountRow[]): {
  expenseAccountIds: number[];
  apAccountId: number | null;
} {
  const posting = accounts.filter((a) => a.allow_posting);
  let expenseAccountIds = posting
    .filter((a) => a.account_type === 'Expense' || a.account_type === 'Cost of Goods Sold')
    .map((a) => a.id);
  if (expenseAccountIds.length === 0) {
    expenseAccountIds = accounts
      .filter((a) => a.account_type === 'Expense' || a.account_type === 'Cost of Goods Sold')
      .map((a) => a.id);
  }

  const postingLiabilities = posting.filter((a) => a.account_type === 'Liability');
  const allLiabilities = accounts.filter((a) => a.account_type === 'Liability');
  const apCandidate =
    postingLiabilities.find((a) => /payable/i.test(a.account_name)) ??
    postingLiabilities[0] ??
    allLiabilities.find((a) => /payable/i.test(a.account_name)) ??
    allLiabilities[0];

  return {
    expenseAccountIds,
    apAccountId: apCandidate?.id ?? null,
  };
}

function resolveCashCoaFromList(accounts: CoaAccountRow[]): number | null {
  const posting = accounts.filter((a) => a.allow_posting);
  const assets = posting.filter((a) => a.account_type === 'Asset');
  const allAssets = accounts.filter((a) => a.account_type === 'Asset');
  
  const candidate =
    assets.find((a) => /cash/i.test(a.account_name)) ??
    assets.find((a) => /bank/i.test(a.account_name)) ??
    assets.find((a) => /1110/.test(a.account_name)) ??
    assets[0] ??
    allAssets.find((a) => /cash/i.test(a.account_name)) ??
    allAssets.find((a) => /bank/i.test(a.account_name)) ??
    allAssets[0];
    
  return candidate?.id ?? null;
}

function pickRandom<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function roundMoney(n: number): number {
  return Math.round(n * 100) / 100;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function login(
  baseUrl: string,
  email: string,
  password: string,
): Promise<LoginData> {
  const res = await axios.post(`${baseUrl}/auth/login`, { email, password });
  const body = res.data;
  if (!isApiSuccess(body) || !body.data?.token) {
    throw new Error(`Login failed: ${body.message || JSON.stringify(body)}`);
  }
  return {
    id: body.data.id,
    token: body.data.token || body.data.accessToken,
  };
}

/** Owned + active collaborator orgs (same as org switcher / all_organizations without owned_only). */
async function fetchAccessibleOrganizations(
  baseUrl: string,
  token: string,
  userId: number,
): Promise<AccessibleOrg[]> {
  const res = await axios.get(`${baseUrl}/auth/all_organizations`, {
    params: { user_id: String(userId) },
    headers: { Authorization: `Bearer ${token}` },
  });
  const body = res.data;
  if (!isApiSuccess(body)) {
    throw new Error(
      `all_organizations failed: ${body.message || JSON.stringify(body)}`,
    );
  }
  const list = Array.isArray(body.data) ? body.data : [];
  return list
    .filter((o: { id?: number }) => o.id != null)
    .map(
      (o: {
        id: number;
        organization_name?: string;
        name?: string;
        is_collaborator?: boolean;
        collaborator_role?: string | null;
      }) => ({
        id: Number(o.id),
        name: o.organization_name || o.name,
        is_collaborator: o.is_collaborator === true,
        collaborator_role: o.collaborator_role ?? null,
      }),
    );
}

function filterOrganizations(
  orgs: AccessibleOrg[],
  opts: CliOptions,
): AccessibleOrg[] {
  if (opts.ownedOnly && opts.collaboratorOnly) {
    throw new Error('Use only one of --owned-only or --collaborator-only');
  }
  let list = orgs;
  if (opts.orgId != null) {
    list = list.filter((o) => o.id === opts.orgId);
    if (list.length === 0) {
      throw new Error(
        `Organization ${opts.orgId} not in accessible list for this user`,
      );
    }
    return list;
  }
  if (opts.ownedOnly) {
    list = list.filter((o) => !o.is_collaborator);
  }
  if (opts.collaboratorOnly) {
    list = list.filter((o) => o.is_collaborator);
  }
  return list;
}

async function loadOrgSeedContext(
  baseUrl: string,
  token: string,
  userId: number,
  organizationId: number,
): Promise<OrgSeedContext> {
  const api = axios.create({
    baseURL: baseUrl,
    headers: { Authorization: `Bearer ${token}` },
    validateStatus: () => true,
  });
  const orgParams = { user_id: String(userId), organization_id: organizationId };

  const coaRes = await api.get('/chart-of-accounts/list', {
    params: { userId: String(userId), organizationId: String(organizationId) },
  });
  if (!isApiSuccess(coaRes.data)) {
    throw new Error(`chart-of-accounts/list failed for org ${organizationId}`);
  }
  const coaAccounts = extractCoaAccounts(coaRes.data);
  const { revenueAccountIds, arAccountId } =
    resolveInvoiceCoaFromList(coaAccounts);
  const { expenseAccountIds, apAccountId } =
    resolveExpenseCoaFromList(coaAccounts);
  const cashAccountId = resolveCashCoaFromList(coaAccounts);

  const contactRes = await api.get('/contact/list_filter', {
    params: {
      user_id: String(userId),
      organization_id: organizationId,
      contact_type_id: 4,
      pageSize: 1000,
    },
  });
  if (!isApiSuccess(contactRes.data)) {
    throw new Error(`contact/list_filter failed for org ${organizationId}`);
  }
  const contactIds = extractList(contactRes.data)
    .map((c: { id?: number }) => c.id)
    .filter((id): id is number => typeof id === 'number');

  const vendorRes = await api.get('/contact/list_filter', {
    params: {
      user_id: String(userId),
      organization_id: organizationId,
      contact_type_id: 5,
      pageSize: 1000,
    },
  });
  if (!isApiSuccess(vendorRes.data)) {
    throw new Error(`contact/list_filter (vendors) failed for org ${organizationId}`);
  }
  const vendorIds = extractList(vendorRes.data)
    .map((c: { id?: number }) => c.id)
    .filter((id): id is number => typeof id === 'number');

  const itemRes = await api.get('/item/list', {
    params: { ...orgParams, page: 1, pageSize: 500, is_active: true },
  });
  if (!isApiSuccess(itemRes.data)) {
    throw new Error(`item/list failed for org ${organizationId}`);
  }
  const mapProducts = (rows: Record<string, unknown>[]) =>
    rows
      .map((row) => ({
        id: Number(row.id),
        description: String(row.description || row.name || 'Line item'),
        unit_price:
          parseFloat(String(row.unit_price ?? row.cost ?? 10)) || 10,
        revenue_account_id: row.revenue_account_id
          ? Number(row.revenue_account_id)
          : undefined,
      }))
      .filter((p) => p.id > 0 && p.unit_price > 0);

  let products = mapProducts(extractList(itemRes.data));

  const taxRes = await api.get('/tax-rate/list', {
    params: { user_id: userId, page: 1, pageSize: 100, is_active: true },
  });
  if (!isApiSuccess(taxRes.data)) {
    throw new Error(`tax-rate/list failed for org ${organizationId}`);
  }
  const taxRates = extractList(taxRes.data)
    .map((t: { id: number; tax_rate?: string | number }) => ({
      id: Number(t.id),
      rate: parseFloat(String(t.tax_rate ?? 0)) || 0,
    }))
    .filter((t) => t.id > 0);

  let projectIds: Array<number | null> = [null];
  const projRes = await api.get('/projects/list', { params: orgParams });
  if (isApiSuccess(projRes.data)) {
    const ids = extractList(projRes.data)
      .map((p: { id?: number }) => p.id)
      .filter((id): id is number => typeof id === 'number');
    if (ids.length > 0) projectIds = ids;
  }

  let branchIds: Array<number | null> = [null];
  const branchRes = await api.get('/branches/list', { params: orgParams });
  if (isApiSuccess(branchRes.data)) {
    const ids = extractList(branchRes.data)
      .map((b: { id?: number }) => b.id)
      .filter((id): id is number => typeof id === 'number');
    if (ids.length > 0) branchIds = ids;
  }

  // Statuses by name only. The ids differ between databases: the old fallback
  // [1 PENDING, 3 PAID, 5 PARTIALLY_PAID] is [ACCEPTED, CANCELLED, RECEIVED] in
  // accutax_bk_1_5, which seeded a third of "paid" invoices as CANCELLED.
  const statusRes = await api.get('/seed/list_status_type');
  if (!isApiSuccess(statusRes.data)) {
    throw new Error(
      `seed/list_status_type failed for org ${organizationId}: ${JSON.stringify(statusRes.data).slice(0, 200)}`,
    );
  }
  const statusTypes: StatusTypeOption[] = extractList(statusRes.data)
    .map((s: { id: number; value?: string; code?: string }) => ({
      id: Number(s.id),
      value: s.value ?? s.code,
    }))
    .filter((s) => s.id > 0);
  for (const required of ['PENDING', 'PAID', 'PARTIALLY_PAID']) {
    statusIdByValue(statusTypes, required); // throws when missing
  }

  let expenseCategories: Array<{ id: number }> = [];
  const catRes = await api.get('/seed/list_expense_category');
  if (isApiSuccess(catRes.data)) {
    expenseCategories = extractList(catRes.data)
      .map((c: { id: number }) => ({ id: Number(c.id) }))
      .filter((c) => c.id > 0);
  }

  if (contactIds.length === 0 || products.length === 0) {
    throw new Error(
      `Org ${organizationId}: need at least one customer contact and one active item`,
    );
  }
  if (vendorIds.length === 0) {
    throw new Error(
      `Org ${organizationId}: need at least one vendor contact`,
    );
  }
  if (coaAccounts.length === 0) {
    throw new Error(
      `Org ${organizationId}: chart-of-accounts/list returned no accounts`,
    );
  }
  if (revenueAccountIds.length === 0 || !arAccountId) {
    const types = Array.from(
      new Set(coaAccounts.map((a) => a.account_type)),
    ).join(', ');
    throw new Error(
      `Org ${organizationId}: need at least one Revenue and one Asset COA from list (types seen: ${types})`,
    );
  }
  if (expenseAccountIds.length === 0 || !apAccountId) {
    const types = Array.from(
      new Set(coaAccounts.map((a) => a.account_type)),
    ).join(', ');
    throw new Error(
      `Org ${organizationId}: need at least one Expense/COGS and one Liability (AP) COA from list (types seen: ${types})`,
    );
  }
  if (!cashAccountId) {
    throw new Error(`Org ${organizationId}: need at least one Asset COA representing cash or bank`);
  }
  if (taxRates.length === 0) {
    throw new Error(`Org ${organizationId}: need at least one active tax rate`);
  }

  return {
    organizationId,
    userId,
    contactIds,
    vendorIds,
    revenueAccountIds,
    expenseAccountIds,
    arAccountId,
    apAccountId,
    cashAccountId,
    coaAccounts,
    projectIds,
    branchIds,
    taxRates,
    products,
    statusTypes,
    expenseCategories,
  };
}

/** The id of a status by its name. Throws when the database has no such status. */
function statusIdByValue(
  statusTypes: StatusTypeOption[],
  value: string,
): number {
  const hit = statusTypes.find(
    (s) => s.value?.toUpperCase() === value.toUpperCase(),
  );
  if (!hit) {
    throw new Error(
      `Status "${value}" not found in seed/list_status_type (got: ${statusTypes.map((s) => `${s.id}=${s.value}`).join(', ') || 'none'})`,
    );
  }
  return hit.id;
}

function generateYearPattern(): Record<number, number> {
  const base = [1.0, 0.8, 1.7, 0.9, 1.6, 0.7, 1.5, 0.8, 1.8, 0.6, 1.4, 0.9];
  const pattern: Record<number, number> = {};
  for (let i = 0; i < 12; i++) {
    pattern[i + 1] = parseFloat(
      (base[i] + (Math.random() * 0.3 - 0.15)).toFixed(2),
    );
  }
  return pattern;
}

/**
 * Every invoice is created PENDING with nothing paid; `settle` says which
 * payment it gets afterwards (a third each: paid, half paid, left open).
 * The payment endpoint then sets PAID / PARTIALLY_PAID and posts the cash
 * receipt, instead of the invoice claiming amount_paid with no payment.
 */
function pickPaymentState(
  ctx: OrgSeedContext,
  totalAmount: number,
  seq: number,
  draftPct: number,
): {
  is_draft: boolean;
  status_type_id: number;
  amount_paid: number;
  amount_due: number;
  settle: Settlement;
} {
  const pending = statusIdByValue(ctx.statusTypes, 'PENDING');
  const roll = (seq * 13 + 7) % 100;
  if (roll < draftPct) {
    return { is_draft: true, status_type_id: pending, amount_paid: 0, amount_due: totalAmount, settle: 'open' };
  }
  const settle: Settlement = (['open', 'paid', 'partial'] as const)[seq % 3];
  return { is_draft: false, status_type_id: pending, amount_paid: 0, amount_due: totalAmount, settle };
}

function buildHistoricalInvoice(
  ctx: OrgSeedContext,
  invoiceDate: Date,
  seq: number,
  draftPct: number,
  numberPrefix: string,
): PlannedInvoice {
  const year = invoiceDate.getFullYear();
  const runId = Math.random().toString(36).substring(2, 6);
  const invoiceNumber = `${numberPrefix}-${ctx.organizationId}-${year}-${runId}-${String(seq).padStart(7, '0')}`;

  const numLines = Math.min(
    Math.floor(Math.random() * 3) + 1,
    ctx.products.length,
  );
  const chosen = [...ctx.products]
    .sort(() => Math.random() - 0.5)
    .slice(0, numLines);

  let subtotal = 0;
  let totalTax = 0;
  const line_items = chosen.map((product) => {
    const qty = Math.floor(Math.random() * 3) + 1;
    const price = product.unit_price;
    const tax = pickRandom(ctx.taxRates);
    const lineAmount = qty * price;
    const taxAmount = (lineAmount * tax.rate) / 100;
    subtotal += lineAmount;
    totalTax += taxAmount;
    const accountId = pickRandom(ctx.revenueAccountIds);
    return {
      items_id: product.id,
      description: product.description,
      account_id: accountId,
      quantity: qty,
      unit_price: price,
      discount_percent: 0,
      discount_amount: 0,
      tax_rate_id: tax.id,
      tax_amount: roundMoney(taxAmount),
      line_amount: roundMoney(lineAmount),
    };
  });

  const totalAmount = roundMoney(subtotal + totalTax);
  const dueDate = new Date(invoiceDate);
  dueDate.setDate(dueDate.getDate() + 14);
  const pay = pickPaymentState(ctx, totalAmount, seq, draftPct);
  const branchId = pickRandom(ctx.branchIds);
  const projectId = pickRandom(ctx.projectIds);
  const contactId = pickRandom(ctx.contactIds);

  const payload: Record<string, unknown> = {
    income_type: 'INVOICE',
    user_id: ctx.userId,
    organization_id: ctx.organizationId,
    contact_id: contactId,
    status_type_id: pay.status_type_id,
    is_recurring: false,
    repeat_frequency_type_id: 3,
    invoice_date: invoiceDate.toISOString(),
    due_date: dueDate.toISOString(),
    created_date: invoiceDate.toISOString(),
    never_expires: false,
    start_date: '',
    end_date: '',
    branch_id: branchId,
    project_id: projectId,
    reference_id: 0,
    reference: `REF-${numberPrefix}-${seq}`,
    additional_notes: `Historical invoice ${invoiceNumber}`,
    terms_and_conditions: 'Terms & Conditions',
    payment_type_id: Math.floor(Math.random() * 3) + 1,
    invoice_number: invoiceNumber,
    is_draft: pay.is_draft,
    attachments: [],
    account_id: ctx.arAccountId,
    amount_paid: pay.amount_paid,
    amount_due: pay.amount_due,
    line_items: line_items.map((line) => ({
      ...line,
      ...(branchId != null ? { branch_id: branchId } : {}),
      ...(projectId != null ? { project_id: projectId } : {}),
    })),
  };

  return { invoiceNumber, payload, settle: pay.settle, total: totalAmount, contactId, date: invoiceDate };
}

function planInvoicesForOrg(
  ctx: OrgSeedContext,
  opts: CliOptions,
): PlannedInvoice[] {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setFullYear(endDate.getFullYear() - opts.years);
  startDate.setHours(8, 0, 0, 0);

  const yearlyPatternCache: Record<number, Record<number, number>> = {};
  const planned: PlannedInvoice[] = [];
  let seq = 1;
  const cursor = new Date(startDate);

  while (cursor <= endDate) {
    const year = cursor.getFullYear();
    const month = cursor.getMonth() + 1;
    if (!yearlyPatternCache[year]) {
      yearlyPatternCache[year] = generateYearPattern();
    }
    const multiplier = yearlyPatternCache[year][month];
    const base =
      Math.floor(Math.random() * (opts.maxPerDay - opts.minPerDay + 1)) +
      opts.minPerDay;
    const count = Math.max(1, Math.floor(base * multiplier));

    for (let i = 0; i < count; i++) {
      const invoiceDate = new Date(cursor);
      invoiceDate.setHours(Math.floor(Math.random() * 10) + 8);
      invoiceDate.setMinutes(Math.floor(Math.random() * 60));
      invoiceDate.setSeconds(Math.floor(Math.random() * 60));
      if (invoiceDate > endDate) continue; // today's later hours would be in the future
      planned.push(
        buildHistoricalInvoice(ctx, invoiceDate, seq++, opts.draftPct, opts.numberPrefix),
      );
    }
    cursor.setDate(cursor.getDate() + 1);
  }

  return planned;
}

async function ingestOrgInvoices(
  baseUrl: string,
  token: string,
  planned: PlannedInvoice[],
  concurrency: number,
  redisClient: Redis | null,
  maxQueueDepth: number,
  queuePollMs: number,
): Promise<{ accepted: number; rejected: number; reasons: string[] }> {
  let nextIndex = 0;
  let completed = 0;
  let inFlight = 0;
  let accepted = 0;
  let rejected = 0;
  const reasons: string[] = [];

  const maybeWaitForQueue = async () => {
    if (!redisClient || maxQueueDepth <= 0) return;
    const depth = await getIncomePersistQueueDepth(redisClient);
    if (depth > maxQueueDepth) {
      await waitForQueueBelow(redisClient, maxQueueDepth, queuePollMs);
    }
  };

  await new Promise<void>((resolve) => {
    const launch = () => {
      void (async () => {
        while (inFlight < concurrency && nextIndex < planned.length) {
          await maybeWaitForQueue();
          if (inFlight >= concurrency || nextIndex >= planned.length) break;
          const item = planned[nextIndex++];
          inFlight++;
          void (async () => {
          try {
            const res = await axios.post(
              `${baseUrl}/income/create`,
              item.payload,
              {
                headers: {
                  'Content-Type': 'application/json',
                  Authorization: `Bearer ${token}`,
                  'X-Bulk-Ingest': '1',
                },
                validateStatus: () => true,
              },
            );
            const ok =
              res.status === 202 ||
              res.data?.code === 202 ||
              res.data?.statusCode === 202;
            if (ok) accepted++;
            else {
              rejected++;
              noteReason(reasons, `${item.invoiceNumber}: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 300)}`);
            }
          } catch (err) {
            rejected++;
            noteReason(reasons, `${item.invoiceNumber}: ${(err as Error).message}`);
          } finally {
            inFlight--;
            completed++;
            if (completed % 200 === 0 || completed === planned.length) {
              process.stdout.write(
                `\r    Progress: ${completed}/${planned.length} (202: ${accepted}, rejected: ${rejected})`,
              );
            }
            if (completed === planned.length) {
              resolve();
            } else {
              launch();
            }
          }
        })();
        }
      })();
    };
    launch();
  });

  console.log('');
  return { accepted, rejected, reasons };
}

/** Rejection reasons kept per phase; the first ones are enough to diagnose a run. */
const MAX_REASONS = 20;

function noteReason(reasons: string[], reason: string): void {
  if (reasons.length < MAX_REASONS) reasons.push(reason);
}

/** Stops the run when a phase rejected more than the allowed share. */
function checkRejections(phase: string, attempted: number, rejected: number, reasons: string[], maxPct: number): void {
  if (rejected === 0) return;
  console.log(`  ${phase}: ${rejected} rejected. First reasons:`);
  reasons.slice(0, 5).forEach((r) => console.log(`    - ${r}`));
  const pct = attempted > 0 ? (100 * rejected) / attempted : 100;
  if (pct > maxPct) {
    throw new Error(
      `${phase}: ${rejected}/${attempted} rejected (${pct.toFixed(1)}% > --max-reject-pct ${maxPct}). Stopping; fix the cause before re-running.`,
    );
  }
}

interface PlannedExpense {
  receiptNumber: string;
  payload: Record<string, unknown>;
  settle: Settlement;
  total: number;
  vendorId: number;
  date: Date;
  expenseType: string;
  /** Set once the create call returns the new bill's id. */
  id?: number;
}

function pickExpensePaymentState(
  ctx: OrgSeedContext,
  seq: number,
  draftPct: number,
  expenseType: string,
): {
  is_draft: boolean;
  status_type_id: number;
  settle: Settlement;
} {
  if (expenseType === 'CASH_EXPENSE') {
    // The server records the supplier payment (and its cash journal) for a
    // cash expense itself; paying it again here would pay it twice.
    return { is_draft: false, status_type_id: statusIdByValue(ctx.statusTypes, 'PAID'), settle: 'open' };
  }

  const pending = statusIdByValue(ctx.statusTypes, 'PENDING');
  const roll = (seq * 17 + 11) % 100;
  if (roll < draftPct) {
    return { is_draft: true, status_type_id: pending, settle: 'open' };
  }
  // Bills are created PENDING; one in five is then paid through
  // supplier-payment/create, which sets PAID and posts the payment journal.
  return { is_draft: false, status_type_id: pending, settle: roll < 80 ? 'open' : 'paid' };
}

function buildHistoricalExpense(
  ctx: OrgSeedContext,
  expenseDate: Date,
  seq: number,
  draftPct: number,
  numberPrefix: string,
): PlannedExpense {
  const year = expenseDate.getFullYear();
  const runId = Math.random().toString(36).substring(2, 6);
  const receiptNumber = `${numberPrefix}-EXP-${ctx.organizationId}-${year}-${runId}-${String(seq).padStart(7, '0')}`;

  const expenseType = seq % 10 < 6 ? 'BILL' : 'CASH_EXPENSE';

  const numLines = Math.min(
    Math.floor(Math.random() * 3) + 1,
    ctx.products.length,
  );
  const chosen = [...ctx.products]
    .sort(() => Math.random() - 0.5)
    .slice(0, numLines);

  let subtotal = 0;
  let totalTax = 0;

  const items = chosen.map((product) => {
    const qty = Math.floor(Math.random() * 3) + 1;
    const price = product.unit_price;
    const tax = pickRandom(ctx.taxRates);
    const lineAmount = qty * price;
    const taxAmount = (lineAmount * tax.rate) / 100;
    subtotal += lineAmount;
    totalTax += taxAmount;
    const accountId = pickRandom(ctx.expenseAccountIds);
    return {
      items_id: product.id,
      description: product.description,
      account_id: accountId,
      quantity: qty,
      unit_cost: price,
      discount_percent: 0,
      discount_amount: 0,
      tax_rate_id: tax.id,
      tax_amount: roundMoney(taxAmount),
      line_amount: roundMoney(lineAmount),
    };
  });

  const pay = pickExpensePaymentState(ctx, seq, draftPct, expenseType);
  const branchId = pickRandom(ctx.branchIds);
  const projectId = pickRandom(ctx.projectIds);
  const vendorId = pickRandom(ctx.vendorIds);

  const expenseCategoryId = ctx.expenseCategories.length > 0
    ? pickRandom(ctx.expenseCategories).id
    : 1;

  const payload: Record<string, unknown> = {
    expense_type: expenseType,
    user_id: ctx.userId,
    organization_id: ctx.organizationId,
    contact_id: vendorId,
    status_type_id: pay.status_type_id,
    reception_date: expenseDate.toISOString(),
    created_date: expenseDate.toISOString(),
    due_date: expenseType === 'BILL'
      ? new Date(expenseDate.getTime() + 30 * 24 * 60 * 60 * 1000).toISOString()
      : expenseDate.toISOString(),
    branch_id: branchId,
    project_id: projectId,
    additional_notes: `Historical expense ${receiptNumber}`,
    terms_and_conditions: 'Terms & Conditions',
    payment_type_id: Math.floor(Math.random() * 3) + 1,
    receipt_number: receiptNumber,
    is_draft: pay.is_draft,
    attachments: [],
    account_id: expenseType === 'CASH_EXPENSE' ? ctx.cashAccountId : null,
    expense_category_type_id: expenseCategoryId,
    items: items.map((line) => ({
      ...line,
      ...(branchId != null ? { branch_id: branchId } : {}),
      ...(projectId != null ? { project_id: projectId } : {}),
    })),
  };

  return {
    receiptNumber,
    payload,
    settle: pay.settle,
    total: roundMoney(subtotal + totalTax),
    vendorId,
    date: expenseDate,
    expenseType,
  };
}

function planExpensesForOrg(
  ctx: OrgSeedContext,
  opts: CliOptions,
): PlannedExpense[] {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setFullYear(endDate.getFullYear() - opts.years);
  startDate.setHours(8, 0, 0, 0);

  const yearlyPatternCache: Record<number, Record<number, number>> = {};
  const planned: PlannedExpense[] = [];
  let seq = 1;
  const cursor = new Date(startDate);

  while (cursor <= endDate) {
    const year = cursor.getFullYear();
    const month = cursor.getMonth() + 1;
    if (!yearlyPatternCache[year]) {
      yearlyPatternCache[year] = generateYearPattern();
    }
    const multiplier = yearlyPatternCache[year][month];

    const base =
      Math.floor(Math.random() * (opts.maxPerDay - opts.minPerDay + 1)) +
      opts.minPerDay;
    const count = Math.max(1, Math.floor(base * multiplier * 0.5));

    for (let i = 0; i < count; i++) {
      const expenseDate = new Date(cursor);
      expenseDate.setHours(Math.floor(Math.random() * 10) + 8);
      expenseDate.setMinutes(Math.floor(Math.random() * 60));
      expenseDate.setSeconds(Math.floor(Math.random() * 60));
      if (expenseDate > endDate) continue; // today's later hours would be in the future
      planned.push(
        buildHistoricalExpense(ctx, expenseDate, seq++, opts.draftPct, opts.numberPrefix),
      );
    }
    cursor.setDate(cursor.getDate() + 1);
  }

  return planned;
}

async function ingestOrgExpenses(
  baseUrl: string,
  token: string,
  planned: PlannedExpense[],
  concurrency: number,
): Promise<{ accepted: number; rejected: number; reasons: string[] }> {
  let nextIndex = 0;
  let completed = 0;
  let inFlight = 0;
  let accepted = 0;
  let rejected = 0;
  const reasons: string[] = [];

  await new Promise<void>((resolve) => {
    const launch = () => {
      void (async () => {
        while (inFlight < concurrency && nextIndex < planned.length) {
          if (inFlight >= concurrency || nextIndex >= planned.length) break;
          const item = planned[nextIndex++];
          inFlight++;
          void (async () => {
            try {
              const res = await axios.post(
                `${baseUrl}/expense/create`,
                item.payload,
                {
                  headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${token}`,
                  },
                  validateStatus: () => true,
                },
              );
              const ok =
                res.status === 200 ||
                res.status === 201 ||
                res.data?.code === 200 ||
                res.data?.code === 201 ||
                res.data?.statusCode === 200 ||
                res.data?.statusCode === 201;
              const id = Number(res.data?.data?.id);
              if (ok && id > 0) {
                accepted++;
                item.id = id; // needed to pay the bill afterwards
              } else {
                rejected++;
                noteReason(reasons, `${item.receiptNumber}: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 300)}`);
              }
            } catch (err) {
              rejected++;
              noteReason(reasons, `${item.receiptNumber}: ${(err as Error).message}`);
            } finally {
              inFlight--;
              completed++;
              if (completed % 200 === 0 || completed === planned.length) {
                process.stdout.write(
                  `\r    Progress (Expenses): ${completed}/${planned.length} (Success: ${accepted}, Rejected: ${rejected})`,
                );
              }
              if (completed === planned.length) {
                resolve();
              } else {
                launch();
              }
            }
          })();
        }
      })();
    };
    launch();
  });

  console.log('');
  return { accepted, rejected, reasons };
}

// ── Payments ─────────────────────────────────────────────────────────────────

/** A payment date between 1 and 30 days after the document, never after today. */
function paymentDate(documentDate: Date): string {
  const d = new Date(documentDate.getTime() + (1 + Math.floor(Math.random() * 30)) * 24 * 60 * 60 * 1000);
  const now = new Date();
  return (d > now ? now : d).toISOString();
}

/** Runs `work` over `items` with at most `concurrency` calls in flight. */
async function runPool<T>(items: T[], concurrency: number, work: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const workers = Array.from({ length: Math.max(1, Math.min(concurrency, items.length)) }, async () => {
    while (next < items.length) {
      const item = items[next++];
      await work(item);
    }
  });
  await Promise.all(workers);
}

/**
 * Invoices are persisted by a queue worker after the 202, so their ids are
 * not known at create time. Once the queue is empty (or the wait runs out),
 * each planned customer's open invoices are read back and matched by number.
 */
async function waitForInvoicesPersisted(
  redisClient: Redis | null,
  opts: CliOptions,
): Promise<void> {
  if (!redisClient) {
    console.log('  No Redis: cannot see the invoice queue; waiting 60s before reading invoices back.');
    await sleep(60_000);
    return;
  }
  const deadline = Date.now() + opts.settleWaitMs;
  while (Date.now() < deadline) {
    if ((await getIncomePersistQueueDepth(redisClient)) === 0) return;
    await sleep(opts.queuePollMs);
  }
  console.log(`  Invoice queue not empty after ${opts.settleWaitMs}ms; paying the invoices persisted so far.`);
}

async function settleInvoices(
  ctx: OrgSeedContext,
  planned: PlannedInvoice[],
  opts: CliOptions,
  token: string,
): Promise<{ attempted: number; paid: number; rejected: number; notFound: number; reasons: string[] }> {
  const api = axios.create({
    baseURL: opts.baseUrl,
    headers: { Authorization: `Bearer ${token}`, 'X-Organization-ID': String(ctx.organizationId) },
    validateStatus: () => true,
  });
  const toSettle = planned.filter((p) => p.settle !== 'open' && p.payload.is_draft !== true);
  const byNumber = new Map(toSettle.map((p) => [p.invoiceNumber, p]));
  // The server's own figures: paying its amount_due leaves no rounding cents open.
  const found = new Map<string, { id: number; total: number; due: number }>();
  const reasons: string[] = [];

  // Read back each customer's open invoices to learn the ids of this run's invoices.
  const customers = Array.from(new Set(toSettle.map((p) => p.contactId)));
  await runPool(customers, opts.concurrency, async (customerId) => {
    const res = await api.get(`/income/customer-payment/unpaid-invoices/${customerId}`, {
      params: { userId: String(ctx.userId), organization_id: String(ctx.organizationId) },
    });
    if (!isApiSuccess(res.data)) {
      noteReason(reasons, `unpaid-invoices ${customerId}: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 200)}`);
      return;
    }
    // Response shape: { data: { data: [{ id, invoice_number, total, amount_due, ... }] } }
    for (const row of extractList(res.data) as Array<{ id: number; invoice_number?: string; total?: number; amount_due?: number }>) {
      const number = String(row.invoice_number || '');
      if (byNumber.has(number)) {
        found.set(number, { id: Number(row.id), total: Number(row.total || 0), due: Number(row.amount_due || 0) });
      }
    }
  });

  let paid = 0;
  let rejected = 0;
  let notFound = 0;
  await runPool(toSettle, opts.concurrency, async (p) => {
    const invoice = found.get(p.invoiceNumber);
    if (!invoice) {
      notFound++;
      return;
    }
    const invoiceId = invoice.id;
    const amount = p.settle === 'paid' ? invoice.due : roundMoney((invoice.total || p.total) * 0.5);
    const res = await api.post('/income/customer-payment/create', {
      date: paymentDate(p.date),
      customer_id: p.contactId,
      payment_type: 'INVOICE_PAYMENT',
      invoice_ids: [invoiceId],
      items: [{ invoice_id: invoiceId, amount_applied: amount }],
      amount,
      currency: 'AED',
      payment_method: 'Bank Transfer',
      reference: `PAY-${p.invoiceNumber}`,
      account_id: ctx.cashAccountId,
      organization_id: ctx.organizationId,
    });
    if (isApiSuccess(res.data) || res.status === 201) paid++;
    else {
      rejected++;
      noteReason(reasons, `${p.invoiceNumber}: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 300)}`);
    }
  });
  return { attempted: toSettle.length, paid, rejected, notFound, reasons };
}

async function settleBills(
  ctx: OrgSeedContext,
  planned: PlannedExpense[],
  opts: CliOptions,
  token: string,
): Promise<{ attempted: number; paid: number; rejected: number; reasons: string[] }> {
  const api = axios.create({
    baseURL: opts.baseUrl,
    headers: { Authorization: `Bearer ${token}`, 'X-Organization-ID': String(ctx.organizationId) },
    validateStatus: () => true,
  });
  const toSettle = planned.filter((p) => p.settle === 'paid' && p.expenseType === 'BILL' && p.id);
  const reasons: string[] = [];
  let paid = 0;
  let rejected = 0;
  await runPool(toSettle, opts.concurrency, async (p) => {
    const res = await api.post('/expense/supplier-payment/create', {
      date: paymentDate(p.date),
      supplier_id: p.vendorId,
      bill_ids: [p.id],
      items: [{ bill_id: p.id, amount_applied: p.total }],
      amount: p.total,
      currency: 'AED',
      payment_method: 'Bank Transfer',
      reference: `PAY-${p.receiptNumber}`,
      account_id: ctx.cashAccountId,
      user_id: ctx.userId,
      organization_id: ctx.organizationId,
    });
    if (isApiSuccess(res.data) || res.status === 201) paid++;
    else {
      rejected++;
      noteReason(reasons, `${p.receiptNumber}: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 300)}`);
    }
  });
  return { attempted: toSettle.length, paid, rejected, reasons };
}

async function seedOrganization(
  org: AccessibleOrg,
  opts: CliOptions,
  login: LoginData,
  orgIndex: number,
  orgTotal: number,
  redisClient: Redis | null,
): Promise<'seeded' | 'skipped'> {
  const label = org.is_collaborator
    ? `collaborator${org.collaborator_role ? ` (${org.collaborator_role})` : ''}`
    : 'owned';
  console.log(
    `\n[Org ${orgIndex + 1}/${orgTotal}] ID ${org.id}${org.name ? ` — ${org.name}` : ''} [${label}]`,
  );

  let ctx: OrgSeedContext;
  try {
    ctx = await loadOrgSeedContext(
      opts.baseUrl,
      login.token,
      login.id,
      org.id,
    );
  } catch (err) {
    const message = (err as Error).message;
    // A status problem affects every org alike: stop instead of skipping them all.
    if (/status/i.test(message)) throw err;
    console.log(`  SKIP: ${message}`);
    return 'skipped';
  }

  console.log(
    `  Context: customers=${ctx.contactIds.length} vendors=${ctx.vendorIds.length} items=${ctx.products.length} tax=${ctx.taxRates.length} coa=${ctx.coaAccounts.length}\n` +
    `           revenue=${ctx.revenueAccountIds.length} ar=${ctx.arAccountId} expense=${ctx.expenseAccountIds.length} ap=${ctx.apAccountId} cash=${ctx.cashAccountId}`,
  );

  const plannedInvoices = planInvoicesForOrg(ctx, opts);
  const plannedExpenses = planExpensesForOrg(ctx, opts);
  console.log(
    `  Planned ${plannedInvoices.length} invoices and ${plannedExpenses.length} expenses (${opts.years}y)`,
  );

  if (opts.dryRun) {
    console.log('  Dry run — no POST calls');
    return 'seeded';
  }

  const runInvoices = async () => {
    console.log(`  Ingesting ${plannedInvoices.length} invoices...`);
    const { accepted, rejected, reasons } = await ingestOrgInvoices(
      opts.baseUrl,
      login.token,
      plannedInvoices,
      opts.concurrency,
      redisClient,
      opts.maxQueueDepth,
      opts.queuePollMs,
    );
    console.log(`  Invoices done: ${accepted} accepted (202), ${rejected} rejected.`);
    checkRejections('Invoices', plannedInvoices.length, rejected, reasons, opts.maxRejectPct);
  };

  const runExpenses = async () => {
    console.log(`  Ingesting ${plannedExpenses.length} expenses...`);
    const { accepted, rejected, reasons } = await ingestOrgExpenses(
      opts.baseUrl,
      login.token,
      plannedExpenses,
      opts.concurrency,
    );
    console.log(`  Expenses done: ${accepted} accepted, ${rejected} rejected.`);
    checkRejections('Expenses', plannedExpenses.length, rejected, reasons, opts.maxRejectPct);
  };

  if (opts.seedingOrder === 'expenses-first') {
    await runExpenses();
    await runInvoices();
  } else {
    await runInvoices();
    await runExpenses();
  }

  if (opts.settle) {
    const bills = await settleBills(ctx, plannedExpenses, opts, login.token);
    console.log(`  Bill payments: ${bills.paid}/${bills.attempted} recorded, ${bills.rejected} rejected.`);
    checkRejections('Bill payments', bills.attempted, bills.rejected, bills.reasons, opts.maxRejectPct);

    console.log('  Waiting for queued invoices to be saved before paying them...');
    await waitForInvoicesPersisted(redisClient, opts);
    const inv = await settleInvoices(ctx, plannedInvoices, opts, login.token);
    console.log(
      `  Invoice payments: ${inv.paid}/${inv.attempted} recorded, ${inv.rejected} rejected, ${inv.notFound} not found yet.`,
    );
    checkRejections('Invoice payments', inv.attempted, inv.rejected + inv.notFound, inv.reasons, opts.maxRejectPct);
  } else {
    console.log('  --no-settle: payments skipped; every document stays PENDING.');
  }

  if (redisClient && opts.drainQueueBetweenOrgs) {
    await waitForQueueBelow(redisClient, 0, opts.queuePollMs, 'between orgs');
    console.log('  Queue drained before next org.');
  } else if (redisClient && opts.maxQueueDepth > 0) {
    const depth = await getIncomePersistQueueDepth(redisClient);
    console.log(`  income-persist queue depth: ${depth}`);
  }
  return 'seeded';
}

async function runSeedForOneUser(
  opts: CliOptions,
  email: string,
  userIndex: number,
  userTotal: number,
  redisClient: Redis | null,
): Promise<{
  loginOk: boolean;
  orgsAttempted: number;
  orgsSeeded: number;
  orgsSkipped: number;
}> {
  console.log(
    `\n######## User ${userIndex}/${userTotal}: ${email} ########`,
  );

  let loginData: LoginData;
  try {
    loginData = await login(opts.baseUrl, email, opts.password);
    console.log(`  Logged in. User ID: ${loginData.id}`);
  } catch (err) {
    console.log(`  LOGIN FAILED: ${(err as Error).message}`);
    return {
      loginOk: false,
      orgsAttempted: 0,
      orgsSeeded: 0,
      orgsSkipped: 0,
    };
  }

  const allOrgs = await fetchAccessibleOrganizations(
    opts.baseUrl,
    loginData.token,
    loginData.id,
  );
  const orgs = filterOrganizations(allOrgs, opts);
  const owned = orgs.filter((o) => !o.is_collaborator);
  const collab = orgs.filter((o) => o.is_collaborator);
  console.log(
    `  Orgs: ${orgs.length} to seed (${owned.length} owned, ${collab.length} collaborator) — IDs: ${orgs.map((o) => o.id).join(', ') || '(none)'}`,
  );

  if (orgs.length === 0) {
    console.log('  No organizations for this user — skip.');
    return {
      loginOk: true,
      orgsAttempted: 0,
      orgsSeeded: 0,
      orgsSkipped: 0,
    };
  }

  if (redisClient && opts.maxQueueDepth > 0) {
    const depth = await getIncomePersistQueueDepth(redisClient);
    if (depth > opts.maxQueueDepth) {
      console.log(
        `  Waiting for queue (depth ${depth} > ${opts.maxQueueDepth}) before seeding user...`,
      );
      await waitForQueueBelow(
        redisClient,
        opts.maxQueueDepth,
        opts.queuePollMs,
        'before user',
      );
    }
  }

  let orgsSeeded = 0;
  let orgsSkipped = 0;
  for (let i = 0; i < orgs.length; i++) {
    const result = await seedOrganization(
      orgs[i],
      opts,
      loginData,
      i,
      orgs.length,
      redisClient,
    );
    if (result === 'seeded') orgsSeeded++;
    else orgsSkipped++;
    if (i < orgs.length - 1 && opts.delayBetweenOrgsMs > 0) {
      await sleep(opts.delayBetweenOrgsMs);
    }
  }

  return {
    loginOk: true,
    orgsAttempted: orgs.length,
    orgsSeeded,
    orgsSkipped,
  };
}

async function runBatchTestUsers(
  opts: CliOptions,
  redisClient: Redis | null,
): Promise<void> {
  const from = opts.userFrom!;
  const to = opts.userTo!;
  if (from > to) {
    throw new Error('--user-from must be <= --user-to');
  }

  const totalUsers = to - from + 1;
  let usersOk = 0;
  let usersLoginFail = 0;
  let totalOrgsSeeded = 0;

  console.log(
    `Batch mode: ${totalUsers} users (${buildEmailFromTemplate(opts.emailTemplate, from)} … ${buildEmailFromTemplate(opts.emailTemplate, to)})`,
  );

  for (let i = from; i <= to; i++) {
    const email = buildEmailFromTemplate(opts.emailTemplate, i);
    const userNum = i - from + 1;
    const summary = await runSeedForOneUser(
      opts,
      email,
      userNum,
      totalUsers,
      redisClient,
    );

    if (summary.loginOk) {
      usersOk++;
      totalOrgsSeeded += summary.orgsSeeded;
    } else {
      usersLoginFail++;
      if (opts.stopOnLoginFail) {
        console.error('Stopping (--stop-on-login-fail).');
        break;
      }
    }

    if (i < to && opts.delayBetweenUsersMs > 0) {
      console.log(
        `  Waiting ${opts.delayBetweenUsersMs}ms before next user...`,
      );
      await sleep(opts.delayBetweenUsersMs);
    }
  }

  console.log('\n========== Batch summary ==========');
  console.log(`Users processed:     ${usersOk + usersLoginFail}`);
  console.log(`Login OK:            ${usersOk}`);
  console.log(`Login failed/skipped:${usersLoginFail}`);
  console.log(`Orgs seeded (total): ${totalOrgsSeeded}`);
  console.log('==================================');
}

async function runSingleUser(
  opts: CliOptions,
  redisClient: Redis | null,
): Promise<void> {
  console.log(`\n[1/3] Login as ${opts.email}`);
  const loginData = await login(opts.baseUrl, opts.email, opts.password);
  console.log(`      User ID: ${loginData.id}`);

  console.log('[2/3] Fetch owned + collaborator organizations');
  const allOrgs = await fetchAccessibleOrganizations(
    opts.baseUrl,
    loginData.token,
    loginData.id,
  );
  const orgs = filterOrganizations(allOrgs, opts);
  const owned = orgs.filter((o) => !o.is_collaborator);
  const collab = orgs.filter((o) => o.is_collaborator);
  console.log(
    `      Accessible: ${allOrgs.length} total → seeding ${orgs.length} (${owned.length} owned, ${collab.length} collaborator)`,
  );
  if (orgs.length === 0) {
    console.error('No organizations to seed.');
    process.exit(1);
  }
  console.log(`      IDs: ${orgs.map((o) => o.id).join(', ')}`);

  console.log('[3/3] Seed each organization sequentially');
  for (let i = 0; i < orgs.length; i++) {
    await seedOrganization(
      orgs[i],
      opts,
      loginData,
      i,
      orgs.length,
      redisClient,
    );
    if (i < orgs.length - 1 && opts.delayBetweenOrgsMs > 0) {
      console.log(
        `  Waiting ${opts.delayBetweenOrgsMs}ms before next org (let workers catch up)...`,
      );
      await sleep(opts.delayBetweenOrgsMs);
    }
  }
}

async function askSeedingOrder(): Promise<'invoices-first' | 'expenses-first'> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) => {
    const ask = () => {
      rl.question(
        '\nChoose seeding order:\n' +
        '  1) Seed Invoices first, then Expenses\n' +
        '  2) Seed Expenses first, then Invoices\n' +
        'Enter choice (1 or 2): ',
        (answer) => {
          const val = answer.trim();
          if (val === '1') {
            rl.close();
            resolve('invoices-first');
          } else if (val === '2') {
            rl.close();
            resolve('expenses-first');
          } else {
            console.log('Invalid choice. Please enter 1 or 2.');
            ask();
          }
        }
      );
    };
    ask();
  });
}

async function main(): Promise<void> {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    printHelp();
    return;
  }
  const batchMode =
    opts.userFrom != null &&
    opts.userTo != null &&
    Number.isFinite(opts.userFrom) &&
    Number.isFinite(opts.userTo);

  if (!opts.password) {
    printHelp();
    console.error('\nError: --password is required.');
    process.exit(1);
  }
  if (!batchMode && !opts.email) {
    printHelp();
    console.error('\nError: use --email for one user, or --user-from/--user-to for batch.');
    process.exit(1);
  }
  if (opts.minPerDay > opts.maxPerDay) {
    console.error('min-per-day must be <= max-per-day');
    process.exit(1);
  }

  if (!opts.seedingOrder) {
    opts.seedingOrder = await askSeedingOrder();
  }

  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setFullYear(endDate.getFullYear() - opts.years);

  console.log('========== Org 5-year seed ==========');
  console.log(`Base URL:     ${opts.baseUrl}`);
  console.log(`Date range:   ${startDate.toISOString().split('T')[0]} → ${endDate.toISOString().split('T')[0]}`);
  console.log(`Concurrency:  ${opts.concurrency} (per org, async 202)`);
  console.log(`Max queue:    ${opts.maxQueueDepth <= 0 ? 'off' : opts.maxQueueDepth} (backpressure)`);
  console.log(`Draft %:      ${opts.draftPct}`);
  console.log(`Order:        ${opts.seedingOrder}`);
  console.log(`Prefix:       ${opts.numberPrefix}`);
  console.log(`Payments:     ${opts.settle ? 'recorded via payment endpoints' : 'skipped (--no-settle)'}`);
  console.log(`Max reject %: ${opts.maxRejectPct}`);
  if (batchMode) {
    console.log(`User range:   ${opts.userFrom}–${opts.userTo} (${opts.emailTemplate})`);
  }

  let redisClient = createSeedRedisClient();
  if (redisClient) {
    try {
      await redisClient.connect();
      const depth = await getIncomePersistQueueDepth(redisClient);
      console.log(`Redis OK — income-persist depth at start: ${depth}`);
    } catch (err) {
      console.warn(
        `Redis unavailable (${(err as Error).message}); queue backpressure disabled.`,
      );
      redisClient.disconnect();
      redisClient = null;
    }
  } else {
    console.warn(
      'REDIS_HOST not set — no queue backpressure (risk of Redis OOM on large runs).',
    );
  }

  try {
    if (batchMode) {
      await runBatchTestUsers(opts, redisClient);
    } else {
      await runSingleUser(opts, redisClient);
    }
  } finally {
    redisClient?.disconnect();
  }

  console.log('\n========== Finished ==========');
  console.log(
    'Workers may still drain income-persist after the script exits. Use Bull Board to monitor.',
  );
}

main().catch((err) => {
  console.error('Fatal:', (err as Error).message || err);
  process.exit(1);
});
