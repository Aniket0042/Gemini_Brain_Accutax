/**
 * Stress test for async POST /income/create (202 + write-behind).
 *
 * Usage:
 *   npx ts-node -r tsconfig-paths/register scripts/stress-income-create.ts \
 *     --email user@example.com --password 'secret'
 *
 *   npx ts-node -r tsconfig-paths/register scripts/stress-income-create.ts \
 *     --email user@example.com --password 'secret' --org-id 2 --total 5000 --concurrency 100
 *
 * Environment (optional):
 *   BASE_URL, PORT, STRESS_EMAIL, STRESS_PASSWORD, STRESS_ORG_ID
 */

import axios, { AxiosError, AxiosInstance } from 'axios';
import * as dotenv from 'dotenv';
import * as path from 'path';

// Copy of accutax_bk_backend/scripts/stress-income-create.ts: statuses by name
// only, and documents are created PENDING with nothing paid (see README.md).
// Settings come from ACCUTAX_SEED_ENV (a path) or a .env next to this script.
dotenv.config({ path: process.env.ACCUTAX_SEED_ENV || path.join(__dirname, '.env') });

if (
  process.env.ALLOW_LARGE_SEED !== 'true' &&
  !process.argv.includes('--confirm-seed') &&
  !process.argv.includes('--help')
) {
  throw new Error(
    'Refusing to run income stress seed without explicit confirmation. Set ALLOW_LARGE_SEED=true or pass --confirm-seed.',
  );
}

/** Accutax APIs use `code`, `statusCode`, or `status` depending on endpoint. */
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

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

interface CliOptions {
  email: string;
  password: string;
  baseUrl: string;
  orgId?: number;
  total: number;
  concurrency: number;
  incomeType: string;
  /** When set, each request picks a random type from this list. */
  incomeTypes: string[];
  draftPct: number;
  verifyStatus: boolean;
  pollSamplePct: number;
  minimalPayload: boolean;
  help: boolean;
}

function printHelp(): void {
  console.log(`
Stress test: async POST /income/create

Required:
  --email <email>           Login email (or STRESS_EMAIL)
  --password <password>     Login password (or STRESS_PASSWORD)

Optional:
  --org-id <id>             Organization ID (must be one you OWN).
                            If omitted, uses first owned org from login/API.
  --base-url <url>          API base URL (default http://localhost:\${PORT})
  --total <n>               Number of create requests (default 1000)
  --concurrency <n>         Parallel in-flight requests (default 50)
  --income-type <type>      INVOICE | QUOTE | CASH_INVOICE | ... (default INVOICE)
  --income-types <a,b,c>    Rotate random types per request (overrides --income-type)
  --draft-pct <0-100>       % of requests with is_draft=true (default 25)
  --minimal                 Header-only payload (no contacts/lines; max ingest RPS)
  --verify-status           Poll /income/status until every request completes
  --poll-sample <pct>       When not verifying all, poll this % for persist latency (default 10)
  --help                    Show this message

Examples:
  npm run stress:income-create -- --email owner@test.com --password 'Test@123' --total 2000 --concurrency 100
  npm run stress:income-create -- --email owner@test.com --password 'Test@123' --org-id 2 --verify-status --total 200
`);
}

function parseArgs(argv: string[]): CliOptions {
  const opts: CliOptions = {
    email: process.env.STRESS_EMAIL || '',
    password: process.env.STRESS_PASSWORD || '',
    baseUrl:
      process.env.BASE_URL ||
      `http://localhost:${process.env.PORT || 8081}`,
    total: parseInt(process.env.STRESS_TOTAL || '1000', 10),
    concurrency: parseInt(process.env.STRESS_CONCURRENCY || '50', 10),
    incomeType: process.env.STRESS_INCOME_TYPE || 'INVOICE',
    incomeTypes: process.env.STRESS_INCOME_TYPES
      ? process.env.STRESS_INCOME_TYPES.split(',').map((s) => s.trim()).filter(Boolean)
      : [],
    draftPct: parseInt(process.env.STRESS_DRAFT_PCT || '25', 10),
    verifyStatus: process.env.STRESS_VERIFY_STATUS === 'true',
    pollSamplePct: parseInt(process.env.STRESS_POLL_SAMPLE || '10', 10),
    minimalPayload: process.env.STRESS_MINIMAL === 'true',
    help: false,
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
      case '--org-id':
        opts.orgId = parseInt(next(), 10);
        break;
      case '--base-url':
        opts.baseUrl = next().replace(/\/$/, '');
        break;
      case '--total':
        opts.total = parseInt(next(), 10);
        break;
      case '--concurrency':
        opts.concurrency = parseInt(next(), 10);
        break;
      case '--income-type':
        opts.incomeType = next();
        break;
      case '--income-types':
        opts.incomeTypes = next()
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean);
        break;
      case '--draft-pct':
        opts.draftPct = Math.min(100, Math.max(0, parseInt(next(), 10)));
        break;
      case '--verify-status':
        opts.verifyStatus = true;
        break;
      case '--poll-sample':
        opts.pollSamplePct = parseInt(next(), 10);
        break;
      case '--minimal':
        opts.minimalPayload = true;
        break;
      default:
        if (arg.startsWith('-')) {
          console.warn(`Unknown flag: ${arg}`);
        }
    }
  }
  return opts;
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

interface LoginData {
  id: number;
  token: string;
  organizations?: Array<{
    id: number;
    user_id?: number;
    is_collaborator?: boolean;
  }>;
}

interface TaxRateOption {
  id: number;
  rate: number;
}

interface ProductOption {
  id: number;
  description: string;
  unit_price: number;
  revenue_account_id?: number;
}

interface StatusTypeOption {
  id: number;
  value?: string;
  code?: string;
}

interface RealisticOrgContext {
  organizationId: number;
  userId: number;
  contactIds: number[];
  revenueAccountIds: number[];
  arAccountId: number | null;
  projectIds: Array<number | null>;
  branchIds: Array<number | null>;
  taxRates: TaxRateOption[];
  products: ProductOption[];
  statusTypes: StatusTypeOption[];
}

function extractList(body: any): any[] {
  if (!body) return [];
  if (Array.isArray(body.data)) return body.data;
  if (Array.isArray(body.data?.data)) return body.data.data;
  if (Array.isArray(body.flat_data)) return body.flat_data;
  return [];
}

function pickRandom<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function roundMoney(n: number): number {
  return Math.round(n * 100) / 100;
}

async function login(
  baseUrl: string,
  email: string,
  password: string,
): Promise<LoginData> {
  const res = await axios.post(`${baseUrl}/auth/login`, { email, password });
  const body = res.data;
  if (!isApiSuccess(body) || !body.data?.token) {
    throw new Error(
      `Login failed: ${body.message || JSON.stringify(body)}`,
    );
  }
  return {
    id: body.data.id,
    token: body.data.token || body.data.accessToken,
    organizations: body.data.organizations,
  };
}

async function fetchOwnedOrganizations(
  baseUrl: string,
  token: string,
  userId: number,
): Promise<Array<{ id: number; name?: string }>> {
  const res = await axios.get(`${baseUrl}/auth/all_organizations`, {
    params: { user_id: String(userId), owned_only: 'true' },
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
    .filter(
      (o: { is_collaborator?: boolean; user_id?: number }) =>
        o.is_collaborator !== true && Number(o.user_id) === userId,
    )
    .map((o: { id: number; organization_name?: string; name?: string }) => ({
      id: o.id,
      name: o.organization_name || o.name,
    }));
}

function pickOwnedOrg(
  loginData: LoginData,
  ownedFromApi: Array<{ id: number; name?: string }>,
  explicitOrgId?: number,
): { id: number; name?: string } {
  const ownedFromLogin = (loginData.organizations || []).filter(
    (o) =>
      o.is_collaborator !== true &&
      Number(o.user_id) === loginData.id,
  );

  const merged: Array<{ id: number; name?: string }> = [...ownedFromApi];
  for (const o of ownedFromLogin) {
    if (!merged.some((m) => m.id === o.id)) {
      merged.push({ id: o.id });
    }
  }

  if (explicitOrgId != null) {
    const match = merged.find((o) => o.id === explicitOrgId);
    if (!match) {
      throw new Error(
        `Organization ${explicitOrgId} is not owned by user ${loginData.id}. ` +
          `Owned org IDs: ${merged.map((o) => o.id).join(', ') || '(none)'}`,
      );
    }
    return match;
  }

  if (merged.length > 0) {
    return merged[0];
  }

  throw new Error(
    'No owned organization found. Create an org or pass --org-id for an org you own.',
  );
}

/**
 * Load contacts, products, COA, tax rates, projects, and branches via the same APIs the UI uses.
 */
async function loadRealisticOrgContext(
  baseUrl: string,
  token: string,
  userId: number,
  organizationId: number,
): Promise<RealisticOrgContext> {
  const api: AxiosInstance = axios.create({
    baseURL: baseUrl,
    headers: { Authorization: `Bearer ${token}` },
    validateStatus: () => true,
  });

  const orgParams = { user_id: String(userId), organization_id: organizationId };

  // Chart of accounts (flat_data has account_type for revenue lines)
  const coaRes = await api.get('/chart-of-accounts/list', {
    params: { userId: String(userId), organizationId: String(organizationId) },
  });
  if (!isApiSuccess(coaRes.data)) {
    throw new Error(
      `chart-of-accounts/list failed: ${coaRes.data?.message || coaRes.status}`,
    );
  }
  const accounts =
    coaRes.data?.flat_data ??
    extractList(coaRes.data);
  const revenueAccountIds = accounts
    .filter((a: { account_type?: string }) => a.account_type === 'Revenue')
    .map((a: { id: number }) => a.id);
  const arAccount =
    accounts.find(
      (a: { account_name?: string; account_type?: string }) =>
        a.account_name === 'Accounts Receivable' && a.account_type === 'Asset',
    ) ??
    accounts.find((a: { account_type?: string }) => a.account_type === 'Asset');
  const arAccountId = arAccount?.id ?? null;

  // Customer contacts (type 4 — same as CreateNew.jsx)
  const contactRes = await api.get('/contact/list_filter', {
    params: {
      user_id: String(userId),
      organization_id: organizationId,
      contact_type_id: 4,
      pageSize: 1000,
    },
  });
  if (!isApiSuccess(contactRes.data)) {
    throw new Error(
      `contact/list_filter failed: ${contactRes.data?.message || contactRes.status}`,
    );
  }
  const contacts = extractList(contactRes.data);
  const contactIds = contacts
    .map((c: { id?: number }) => c.id)
    .filter((id): id is number => typeof id === 'number');

  // Products / services (non-tracked preferred to reduce stock admission failures)
  const itemRes = await api.get('/item/list', {
    params: {
      ...orgParams,
      page: 1,
      pageSize: 500,
      is_active: true,
      is_tracked_inventory: false,
    },
  });
  if (!isApiSuccess(itemRes.data)) {
    throw new Error(
      `item/list failed: ${itemRes.data?.message || itemRes.status}`,
    );
  }
  const mapProducts = (rows: Record<string, unknown>[]) =>
    rows
      .map((row) => ({
        id: Number(row.id),
        description: String(row.description || row.name || 'Line item'),
        unit_price: parseFloat(String(row.unit_price ?? row.cost ?? 10)) || 10,
        revenue_account_id: row.revenue_account_id
          ? Number(row.revenue_account_id)
          : undefined,
      }))
      .filter((p) => p.id > 0 && p.unit_price > 0);

  let products: ProductOption[] = mapProducts(extractList(itemRes.data));

  if (products.length === 0) {
    const itemResAny = await api.get('/item/list', {
      params: { ...orgParams, page: 1, pageSize: 500, is_active: true },
    });
    products = mapProducts(extractList(itemResAny.data));
  }

  if (products.length === 0) {
    throw new Error('No active items found for this org. Add products in the UI first.');
  }

  // Tax rates
  const taxRes = await api.get('/tax-rate/list', {
    params: {
      user_id: userId,
      page: 1,
      pageSize: 100,
      is_active: true,
    },
  });
  if (!isApiSuccess(taxRes.data)) {
    throw new Error(
      `tax-rate/list failed: ${taxRes.data?.message || taxRes.status}`,
    );
  }
  const taxRates: TaxRateOption[] = extractList(taxRes.data)
    .map((t: { id: number; tax_rate?: string | number }) => ({
      id: Number(t.id),
      rate: parseFloat(String(t.tax_rate ?? 0)) || 0,
    }))
    .filter((t) => t.id > 0);

  // Projects (optional)
  let projectIds: Array<number | null> = [null];
  const projRes = await api.get('/projects/list', { params: orgParams });
  if (isApiSuccess(projRes.data)) {
    const ids = extractList(projRes.data)
      .map((p: { id?: number }) => p.id)
      .filter((id): id is number => typeof id === 'number');
    if (ids.length > 0) projectIds = ids;
  }

  // Branches (optional)
  let branchIds: Array<number | null> = [null];
  const branchRes = await api.get('/branches/list', { params: orgParams });
  if (isApiSuccess(branchRes.data)) {
    const ids = extractList(branchRes.data)
      .map((b: { id?: number }) => b.id)
      .filter((id): id is number => typeof id === 'number');
    if (ids.length > 0) branchIds = ids;
  }

  if (contactIds.length === 0) {
    throw new Error(
      'No customer contacts (contact_type_id=4) found for this org. Add contacts in the UI first.',
    );
  }
  if (products.length === 0) {
    throw new Error(
      'No active items/products found for this org. Add items in the UI first.',
    );
  }
  if (revenueAccountIds.length === 0) {
    throw new Error(
      'No Revenue accounts in chart of accounts for this org.',
    );
  }
  if (taxRates.length === 0) {
    throw new Error('No active tax rates found for this user/org.');
  }

  // Statuses by name only: the old fallback ids do not match accutax_bk_1_5
  // (there 1 = ACCEPTED, 2 = PAID, 3 = CANCELLED, 4 = PENDING).
  const statusRes = await api.get('/seed/list_status_type');
  if (!isApiSuccess(statusRes.data)) {
    throw new Error(`seed/list_status_type failed: ${JSON.stringify(statusRes.data).slice(0, 200)}`);
  }
  const statusTypes: StatusTypeOption[] = extractList(statusRes.data)
    .map((s: { id: number; value?: string; code?: string }) => ({
      id: Number(s.id),
      value: s.value ?? s.code,
      code: s.code,
    }))
    .filter((s) => s.id > 0);
  statusIdByValue(statusTypes, 'PENDING'); // throws when missing

  return {
    organizationId,
    userId,
    contactIds,
    revenueAccountIds,
    arAccountId,
    projectIds,
    branchIds,
    taxRates,
    products,
    statusTypes,
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

/**
 * Draft or PENDING, nothing paid. The original also wrote PAID / PARTIALLY_PAID
 * with amount_paid but no payment (so no cash receipt), and a random status for
 * a quarter of documents (CANCELLED, VOIDED...). This is a load test of
 * income/create; seed-org-5yr-data.ts records real payments.
 */
function pickDocumentState(
  index: number,
  ctx: RealisticOrgContext,
  draftPct: number,
  totalAmount: number,
): {
  is_draft: boolean;
  status_type_id: number;
  amount_paid: number;
  amount_due: number;
} {
  const roll = (index * 17 + 31) % 100;
  return {
    is_draft: roll < draftPct,
    status_type_id: statusIdByValue(ctx.statusTypes, 'PENDING'),
    amount_paid: 0,
    amount_due: totalAmount,
  };
}

function resolveIncomeType(opts: CliOptions, index: number): string {
  if (opts.incomeTypes.length > 0) {
    return opts.incomeTypes[index % opts.incomeTypes.length];
  }
  return opts.incomeType;
}

// ---------------------------------------------------------------------------
// Stress run
// ---------------------------------------------------------------------------

interface RequestResult {
  index: number;
  httpStatus: number;
  ingestMs: number;
  accepted: boolean;
  correlationId?: string;
  error?: string;
  persistMs?: number;
  finalStatus?: string;
  incomeId?: number;
  persistError?: string;
}

function buildMinimalPayload(
  ctx: RealisticOrgContext,
  incomeType: string,
  invoiceNumber: string,
  index: number,
  draftPct: number,
): Record<string, unknown> {
  const doc = pickDocumentState(index, ctx, draftPct, 0);
  return {
    income_type: incomeType,
    user_id: ctx.userId,
    organization_id: ctx.organizationId,
    status_type_id: doc.status_type_id,
    is_recurring: false,
    repeat_frequency_type_id: 3,
    invoice_date: new Date().toISOString(),
    due_date: new Date(Date.now() + 14 * 86400000).toISOString(),
    never_expires: false,
    reference_id: 0,
    additional_notes: 'stress-test',
    terms_and_conditions: '',
    payment_type_id: 1,
    invoice_number: invoiceNumber,
    is_draft: doc.is_draft,
    amount_paid: doc.amount_paid,
    amount_due: doc.amount_due,
  };
}

/** UI-style invoice body: contact, branch, project, line_items with product + COA + tax. */
function buildRealisticPayload(
  ctx: RealisticOrgContext,
  incomeType: string,
  invoiceNumber: string,
  index: number,
  draftPct: number,
): Record<string, unknown> {
  const numLines = Math.min(
    Math.floor(Math.random() * 3) + 1,
    ctx.products.length,
  );
  const shuffled = [...ctx.products].sort(() => Math.random() - 0.5);
  const chosen = shuffled.slice(0, numLines);

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

    const accountId =
      product.revenue_account_id &&
      ctx.revenueAccountIds.includes(product.revenue_account_id)
        ? product.revenue_account_id
        : pickRandom(ctx.revenueAccountIds);

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
  const invoiceDate = new Date();
  const dueDate = new Date(invoiceDate);
  dueDate.setDate(dueDate.getDate() + 14);

  const docState = pickDocumentState(index, ctx, draftPct, totalAmount);

  const branchId = pickRandom(ctx.branchIds);
  const projectId = pickRandom(ctx.projectIds);

  const line_items_with_dims = line_items.map((line) => ({
    ...line,
    ...(branchId != null ? { branch_id: branchId } : {}),
    ...(projectId != null ? { project_id: projectId } : {}),
  }));

  return {
    income_type: incomeType,
    user_id: ctx.userId,
    organization_id: ctx.organizationId,
    contact_id: pickRandom(ctx.contactIds),
    status_type_id: docState.status_type_id,
    is_recurring: false,
    repeat_frequency_type_id: 3,
    invoice_date: invoiceDate.toISOString(),
    due_date: dueDate.toISOString(),
    never_expires: false,
    start_date: '',
    end_date: '',
    branch_id: branchId,
    project_id: projectId,
    reference_id: 0,
    reference: `REF-STRESS-${index}`,
    additional_notes: `Stress invoice #${index}`,
    terms_and_conditions: 'Terms & Conditions',
    payment_type_id: Math.floor(Math.random() * 3) + 1,
    invoice_number: invoiceNumber,
    created_date: invoiceDate.toISOString(),
    is_draft: docState.is_draft,
    attachments: [],
    account_id: ctx.arAccountId,
    amount_paid: docState.amount_paid,
    amount_due: docState.amount_due,
    line_items: line_items_with_dims,
  };
}

function buildPayload(
  ctx: RealisticOrgContext,
  incomeType: string,
  invoiceNumber: string,
  index: number,
  minimal: boolean,
  draftPct: number,
): Record<string, unknown> {
  if (minimal) {
    return buildMinimalPayload(ctx, incomeType, invoiceNumber, index, draftPct);
  }
  return buildRealisticPayload(
    ctx,
    incomeType,
    invoiceNumber,
    index,
    draftPct,
  );
}

async function pollStatus(
  baseUrl: string,
  token: string,
  correlationId: string,
  timeoutMs: number,
): Promise<{ status: string; incomeId?: number; ms: number; error?: string }> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await axios.get(
        `${baseUrl}/income/status/${encodeURIComponent(correlationId)}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          validateStatus: () => true,
        },
      );
      if (res.status === 403 || res.status === 404) {
        return {
          status: 'failed',
          ms: Date.now() - start,
          error: res.data?.message || `HTTP ${res.status}`,
        };
      }
      const record = res.data?.data;
      if (record?.status === 'success') {
        return {
          status: 'success',
          incomeId: record.income_id,
          ms: Date.now() - start,
        };
      }
      if (record?.status === 'failed') {
        return {
          status: 'failed',
          ms: Date.now() - start,
          error: record.error,
        };
      }
    } catch (err) {
      return {
        status: 'failed',
        ms: Date.now() - start,
        error: (err as Error).message,
      };
    }
    await sleep(100);
  }
  return { status: 'timeout', ms: Date.now() - start };
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(idx, sorted.length - 1))];
}

async function runStress(
  opts: CliOptions,
  ctx: RealisticOrgContext,
  token: string,
): Promise<void> {
  const results: RequestResult[] = new Array(opts.total);
  let completed = 0;
  let inFlight = 0;
  let nextIndex = 0;
  const runId = Date.now().toString(36);

  const startWall = Date.now();

  await new Promise<void>((resolve) => {
    const launch = () => {
      while (inFlight < opts.concurrency && nextIndex < opts.total) {
        const index = nextIndex++;
        inFlight++;
        void (async () => {
          const invoiceNumber = `STRESS-${ctx.organizationId}-${runId}-${index}`;
          const incomeType = resolveIncomeType(opts, index);
          const payload = buildPayload(
            ctx,
            incomeType,
            invoiceNumber,
            index,
            opts.minimalPayload,
            opts.draftPct,
          );
          const t0 = Date.now();
          try {
            const res = await axios.post(
              `${opts.baseUrl}/income/create`,
              payload,
              {
                headers: {
                  'Content-Type': 'application/json',
                  Authorization: `Bearer ${token}`,
                },
                validateStatus: () => true,
              },
            );
            const ingestMs = Date.now() - t0;
            const accepted =
              res.status === 202 ||
              res.data?.code === 202 ||
              res.data?.status === 202 ||
              res.data?.statusCode === 202;
            const correlationId = res.data?.data?.correlation_id as
              | string
              | undefined;

            results[index] = {
              index,
              httpStatus: res.status,
              ingestMs,
              accepted,
              correlationId,
              error: accepted
                ? undefined
                : res.data?.message || `HTTP ${res.status}`,
            };
          } catch (err) {
            const ax = err as AxiosError;
            results[index] = {
              index,
              httpStatus: ax.response?.status || 0,
              ingestMs: Date.now() - t0,
              accepted: false,
              error: ax.message,
            };
          } finally {
            inFlight--;
            completed++;
            if (completed % 500 === 0 || completed === opts.total) {
              process.stdout.write(
                `\r  Progress: ${completed}/${opts.total} requests`,
              );
            }
            if (nextIndex < opts.total || inFlight > 0) {
              launch();
            } else if (completed === opts.total) {
              resolve();
            }
          }
        })();
      }
    };
    launch();
  });

  const wallMs = Date.now() - startWall;
  console.log('\n');

  const accepted = results.filter((r) => r?.accepted);
  const failed = results.filter((r) => r && !r.accepted);

  const toPoll = opts.verifyStatus
    ? accepted.filter((r) => r.correlationId)
    : accepted.filter((r) => {
        if (!r.correlationId) return false;
        return Math.random() * 100 < opts.pollSamplePct;
      });

  if (toPoll.length > 0) {
    console.log(`Polling status for ${toPoll.length} request(s)...`);
    let pollDone = 0;
    const pollConcurrency = Math.min(20, opts.concurrency);
    const queue = [...toPoll];

    await new Promise<void>((resolve) => {
      let active = 0;
      const next = () => {
        while (active < pollConcurrency && queue.length > 0) {
          const r = queue.shift()!;
          active++;
          void pollStatus(opts.baseUrl, token, r.correlationId!, 120000).then(
            (p) => {
              r.persistMs = p.ms;
              r.finalStatus = p.status;
              r.incomeId = p.incomeId;
              r.persistError = p.error;
              pollDone++;
              if (pollDone % 100 === 0) {
                process.stdout.write(`\r  Polled: ${pollDone}/${toPoll.length}`);
              }
              active--;
              if (queue.length > 0 || active > 0) next();
              else resolve();
            },
          );
        }
      };
      next();
    });
    console.log('\n');
  }

  const ingestTimes = accepted.map((r) => r.ingestMs).sort((a, b) => a - b);
  const persistTimes = accepted
    .filter((r) => r.persistMs != null)
    .map((r) => r.persistMs!)
    .sort((a, b) => a - b);

  const statusCounts = { success: 0, failed: 0, timeout: 0, pending: 0 };
  for (const r of accepted) {
    if (r.finalStatus === 'success') statusCounts.success++;
    else if (r.finalStatus === 'failed') statusCounts.failed++;
    else if (r.finalStatus === 'timeout') statusCounts.timeout++;
    else if (r.finalStatus) statusCounts.pending++;
  }

  console.log('========== Stress test summary ==========');
  console.log(`Base URL:        ${opts.baseUrl}`);
  console.log(`Organization:    ${ctx.organizationId}`);
  console.log(`User ID:         ${ctx.userId}`);
  console.log(
    `Income type(s):  ${
      opts.incomeTypes.length > 0
        ? opts.incomeTypes.join(', ')
        : opts.incomeType
    }`,
  );
  console.log(`Draft %:         ${opts.draftPct}% (remainder: pending/paid/partial/other)`);
  console.log(`Total requests:  ${opts.total}`);
  console.log(`HTTP concurrency:${opts.concurrency} (parallel POST /income/create)`);
  const workerConcurrency = parseInt(
    process.env.INCOME_PERSIST_CONCURRENCY || '20',
    10,
  );
  console.log(
    `BullMQ workers:  ${workerConcurrency} parallel (income-persist, env INCOME_PERSIST_CONCURRENCY)`,
  );
  console.log(
    `                 + micro-batches up to 500 jobs / 50ms flush`,
  );
  console.log(`Wall time:       ${(wallMs / 1000).toFixed(2)}s`);
  console.log(
    `Ingest RPS:      ${((accepted.length / wallMs) * 1000).toFixed(1)} (202 only)`,
  );
  console.log(`Accepted (202):  ${accepted.length}`);
  console.log(`Rejected:        ${failed.length}`);

  if (failed.length > 0 && failed.length <= 5) {
    for (const f of failed) {
      console.log(`  [${f.index}] ${f.httpStatus} ${f.error}`);
    }
  } else if (failed.length > 5) {
    const byStatus = new Map<number, number>();
    for (const f of failed) {
      byStatus.set(f.httpStatus, (byStatus.get(f.httpStatus) || 0) + 1);
    }
    console.log('  Failure HTTP codes:', Object.fromEntries(byStatus));
    console.log(`  First error: ${failed[0].error}`);
  }

  if (ingestTimes.length > 0) {
    console.log('Ingest latency (ms):');
    console.log(`  p50:  ${percentile(ingestTimes, 50)}`);
    console.log(`  p95:  ${percentile(ingestTimes, 95)}`);
    console.log(`  p99:  ${percentile(ingestTimes, 99)}`);
    console.log(`  max:  ${ingestTimes[ingestTimes.length - 1]}`);
  }

  if (persistTimes.length > 0) {
    console.log('Persist latency (ms, sampled/verified):');
    console.log(`  p50:  ${percentile(persistTimes, 50)}`);
    console.log(`  p95:  ${percentile(persistTimes, 95)}`);
    console.log(`  p99:  ${percentile(persistTimes, 99)}`);
    console.log(
      `  outcomes: success=${statusCounts.success} failed=${statusCounts.failed} timeout=${statusCounts.timeout}`,
    );
    const sampleFail = accepted.find(
      (r) => r.finalStatus === 'failed' && r.persistError,
    );
    if (sampleFail?.persistError) {
      console.log(`  sample worker error: ${sampleFail.persistError}`);
    }
  }

  console.log('');
  console.log('Note: Ingest RPS measures 202 responses only.');
  console.log(
    'Workers (income-persist) may still be draining the queue after the script exits.',
  );
  if (statusCounts.failed > 0 && statusCounts.success === 0) {
    console.log(
      'If all polled jobs failed, restart API after pulling latest fixes, then re-run with --poll-sample 5.',
    );
  }

  console.log('=========================================');

  if (accepted.length === 0) {
    process.exit(1);
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main(): Promise<void> {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    printHelp();
    return;
  }
  if (!opts.email || !opts.password) {
    printHelp();
    console.error('\nError: --email and --password are required.');
    process.exit(1);
  }

  console.log(`[1/4] Login as ${opts.email}`);
  const loginData = await login(opts.baseUrl, opts.email, opts.password);
  console.log(`      User ID: ${loginData.id}`);

  console.log('[2/4] Resolve owned organization (collaborator orgs excluded)');
  const ownedOrgs = await fetchOwnedOrganizations(
    opts.baseUrl,
    loginData.token,
    loginData.id,
  );
  const org = pickOwnedOrg(loginData, ownedOrgs, opts.orgId);
  console.log(
    `      Using org ${org.id}${org.name ? ` (${org.name})` : ''}. Owned orgs: [${ownedOrgs.map((o) => o.id).join(', ') || 'see login'}]`,
  );

  let ctx: RealisticOrgContext;
  if (opts.minimalPayload) {
    console.log('[3/4] Minimal payload mode (no contacts/lines)');
    ctx = {
      organizationId: org.id,
      userId: loginData.id,
      contactIds: [],
      revenueAccountIds: [],
      arAccountId: null,
      projectIds: [null],
      branchIds: [null],
      taxRates: [],
      products: [],
      statusTypes: [
        { id: 1, value: 'PENDING' },
        { id: 3, value: 'PAID' },
      ],
    };
  } else {
    console.log(
      '[3/4] Load UI context via API (contacts, items, COA, tax, projects, branches)',
    );
    ctx = await loadRealisticOrgContext(
      opts.baseUrl,
      loginData.token,
      loginData.id,
      org.id,
    );
    console.log(
      `      contacts=${ctx.contactIds.length} products=${ctx.products.length} ` +
        `revenue_accounts=${ctx.revenueAccountIds.length} tax_rates=${ctx.taxRates.length} ` +
        `projects=${ctx.projectIds.filter((id) => id != null).length} ` +
        `branches=${ctx.branchIds.filter((id) => id != null).length} ` +
        `status_types=${ctx.statusTypes.length}`,
    );
  }

  console.log(
    `[4/4] Stress test: ${opts.total} requests @ concurrency ${opts.concurrency}`,
  );
  await runStress(opts, ctx, loginData.token);
}

main().catch((err) => {
  console.error('Fatal:', err.message || err);
  process.exit(1);
});
