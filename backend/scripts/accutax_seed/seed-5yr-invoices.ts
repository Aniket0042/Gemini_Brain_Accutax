/**
 * Copy of accutax_bk_backend/scripts/seed-5yr-invoices.ts with the seed-defect fixes
 * (see README.md in this folder):
 *   - Statuses are read by name from status_type. The old hardcoded [1, 3, 5]
 *     ("PENDING, PAID, PARTIAL_PAID") are ACCEPTED, CANCELLED, RECEIVED in
 *     accutax_bk_1_5.
 *   - Invoices are created PENDING with nothing paid: this script records no
 *     payments, so an invoice marked paid here would have no cash receipt behind
 *     it. Use seed-org-5yr-data.ts for paid and partly paid invoices.
 *   - Login and database credentials come only from the environment; the
 *     hardcoded login and database password were removed.
 */
import axios from 'axios';
import { Client } from 'pg';
import * as dotenv from 'dotenv';
import * as path from 'path';
import * as fs from 'fs';

// Settings come from ACCUTAX_SEED_ENV (a path) or a .env next to this script.
dotenv.config({ path: process.env.ACCUTAX_SEED_ENV || path.join(__dirname, '.env') });

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set. Put it in the .env next to this script (see README.md).`);
  }
  return value;
}

if (
  process.env.ALLOW_LARGE_SEED !== 'true' &&
  !process.argv.includes('--confirm-seed') &&
  !process.argv.includes('--help')
) {
  throw new Error(
    'Refusing to run large invoice seed without explicit confirmation. Set ALLOW_LARGE_SEED=true or pass --confirm-seed.',
  );
}

const BASE_URL = process.env.BASE_URL || `http://localhost:${process.env.PORT || 8081}`;
const LOGIN_URL = `${BASE_URL}/auth/login`;
const INCOME_URL = `${BASE_URL}/income/create`;
const COA_URL = `${BASE_URL}/chart-of-accounts`;
const PROJECT_URL = `${BASE_URL}/projects/list`;
const BRANCH_URL = `${BASE_URL}/branches/list`;
const INVOICE_SETTING_URL = `${BASE_URL}/income/invoice_setting`;

const EMAIL = requiredEnv('SEED_EMAIL');
const PASSWORD = requiredEnv('SEED_PASSWORD');

const dbConfig = {
  host: requiredEnv('DB_HOST'),
  port: parseInt(process.env.DB_PORT || '5432'),
  database: requiredEnv('DB_NAME'),
  user: requiredEnv('DB_USER'),
  password: requiredEnv('DB_PASS'),
};

// -------- USER CONTROLLED YEAR RANGE ----------
const START_DATE_ENV = process.env.INVOICE_START_DATE;
const END_DATE_ENV = process.env.INVOICE_END_DATE;

const END_DATE = END_DATE_ENV ? new Date(END_DATE_ENV) : new Date();
const START_DATE = START_DATE_ENV ? new Date(START_DATE_ENV) : new Date(new Date().setFullYear(END_DATE.getFullYear() - 5));
// ------------------------------------------------

const MAX_RETRIES = 3;

function log(message: string) {
  const ts = new Date().toISOString().replace(/T/, ' ').replace(/\..+/, '');
  console.log(`${ts} ${message}`);
  fs.appendFileSync('script_log.txt', `${ts} ${message}\n`);
}

function generateYearPattern() {
  const base = [1.0, 0.8, 1.7, 0.9, 1.6, 0.7, 1.5, 0.8, 1.8, 0.6, 1.4, 0.9];
  const pattern: { [key: number]: number } = {};
  for (let i = 0; i < 12; i++) {
    pattern[i + 1] = parseFloat((base[i] + (Math.random() * 0.3 - 0.15)).toFixed(2));
  }
  return pattern;
}

async function fetchList(query: string, params: any[] = []) {
  const client = new Client(dbConfig);
  try {
    await client.connect();
    const res = await client.query(query, params);
    return res.rows;
  } catch (e) {
    log(`[DB ERROR] ${e}`);
    return [];
  } finally {
    await client.end();
  }
}

async function run() {
  log(`[START] Seeding data from ${START_DATE.toISOString().split('T')[0]} to ${END_DATE.toISOString().split('T')[0]}`);

  // --- Login ---
  let token = '';
  let userId: number | null = null;
  try {
    const loginResp = await axios.post(LOGIN_URL, { email: EMAIL, password: PASSWORD });
    if (loginResp.data.statusCode === 200 && loginResp.data.data) {
      userId = loginResp.data.data.id;
      token = loginResp.data.data.accessToken || loginResp.data.data.token;
      log(`[LOGIN] Success. User ID: ${userId}`);
    } else {
      log(`[LOGIN] Failed: ${JSON.stringify(loginResp.data)}`);
      process.exit(1);
    }
  } catch (e: any) {
    log(`[LOGIN] Exception: ${e.message}`);
    process.exit(1);
  }

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  };

  // --- Status ids by name (never hardcoded: they differ between databases) ---
  const statusRows = await fetchList('SELECT id, value FROM status_type');
  const pendingStatus = statusRows.find((s: any) => String(s.value).toUpperCase() === 'PENDING');
  if (!pendingStatus) {
    log(`[STATUS ERROR] No PENDING status in status_type (got: ${JSON.stringify(statusRows)})`);
    process.exit(1);
  }
  const PENDING_STATUS_ID = Number(pendingStatus.id);
  log(`[STATUS] PENDING = ${PENDING_STATUS_ID}`);

  // --- Fetch Organizations ---
  const TARGET_ORG_ID = process.env.ORGANIZATION_ID;
  let organizationIds: number[] = [];

  if (TARGET_ORG_ID) {
    const orgId = parseInt(TARGET_ORG_ID);
    const orgCheck = await fetchList('SELECT id FROM organizations WHERE id=$1 AND user_id=$2', [orgId, userId]);
    if (orgCheck.length > 0) {
      organizationIds = [orgId];
      log(`[ORG] Targeting specific Organization ID: ${orgId}`);

    } else {
      log(`[ORG ERROR] Organization ID ${orgId} not found for User ${userId}`);
      process.exit(1);
    }
  } else {
    const orgRows = await fetchList('SELECT id FROM organizations WHERE user_id=$1', [userId]);
    organizationIds = orgRows.map(o => o.id);
    log(`[ORG] Found ${organizationIds.length} organizations`);
  }

  for (const organizationId of organizationIds) {
    log(`\n==============================`);
    log(`[START] Organization ${organizationId}`);
    log(`==============================`);

    // --- Cleanup if requested ---
    if (process.env.CLEANUP === 'true') {
      log(`[CLEANUP] Removing existing invoices for Org ${organizationId}...`);
      const incomes = await fetchList('SELECT id, journal_entry_id FROM income WHERE organization_id = $1', [organizationId]);
      const incomeIds = incomes.map(i => i.id);
      const jeIds = incomes.map(i => i.journal_entry_id).filter(id => id != null);

      if (incomeIds.length > 0) {
        await fetchList('DELETE FROM income_items WHERE income_id = ANY($1)', [incomeIds]);
        await fetchList('DELETE FROM income_attachments WHERE income_id = ANY($1)', [incomeIds]);
        await fetchList('DELETE FROM income WHERE id = ANY($1)', [incomeIds]);

        if (jeIds.length > 0) {
          await fetchList('DELETE FROM journal_entry_lines WHERE journal_entry_id = ANY($1)', [jeIds]);
          await fetchList('DELETE FROM journal_entries WHERE id = ANY($1)', [jeIds]);
        }
        log(`[CLEANUP] Deleted ${incomeIds.length} invoices and ${jeIds.length} journal entries.`);
      } else {
        log(`[CLEANUP] No invoices found to clean.`);
      }
    }

    // --- Chart of Accounts ---
    let revenueAccountIds: number[] = [];
    let arAccountId: number | null = null;
    try {
      const coaRows = await fetchList('SELECT id, account_name, account_type FROM chart_of_accounts WHERE organization_id=$1', [organizationId]);

      revenueAccountIds = coaRows.filter(r => r.account_type === 'Revenue').map(r => r.id);
      const arAccount = coaRows.find(r => r.account_name === 'Accounts Receivable' && r.account_type === 'Asset');
      arAccountId = arAccount ? arAccount.id : (coaRows.find(r => r.account_type === 'Asset')?.id || null);

      log(`[COA] Found ${revenueAccountIds.length} Revenue accounts and AR account ID: ${arAccountId}`);
    } catch (e: any) {
      log(`[COA ERROR] ${e.message}`);
    }

    if (revenueAccountIds.length === 0 || !arAccountId) {
      log(`[COA] Missing Revenue or AR accounts, skip org.`);
      continue;
    }

    // --- Projects ---
    let projectIds: (number | null)[] = [null];
    try {
      const projResp = await axios.get(`${PROJECT_URL}?user_id=${userId}&organization_id=${organizationId}`, { headers });
      const pj = projResp.data.data || [];
      if (pj.length > 0) projectIds = pj.map((x: any) => x.id);
    } catch (e: any) {
      log(`[PROJECT ERROR] ${e.message}`);
    }

    // --- Branches ---
    let branchIds: (number | null)[] = [null];
    try {
      const branchResp = await axios.get(`${BRANCH_URL}?user_id=${userId}&organization_id=${organizationId}`, { headers });
      const br = branchResp.data.data || [];
      if (br.length > 0) branchIds = br.map((x: any) => x.id);
    } catch (e: any) {
      log(`[BRANCH ERROR] ${e.message}`);
    }

    // --- Tax Rates ---
    const trRows = await fetchList(`
      SELECT id, tax_rate FROM tax_rates 
      WHERE user_id=$1 AND organization_id=$2 AND is_active=TRUE
    `, [userId, organizationId]);
    const taxRates = trRows.map(r => ({ id: r.id, rate: parseFloat(r.tax_rate) }));

    if (taxRates.length === 0) {
      log(`[TAX] No tax rates, skip org.`);
      continue;
    }

    // --- Invoice Settings ---
    let invoiceSettings: any = {};
    try {
      const setResp = await axios.get(`${INVOICE_SETTING_URL}/${userId}`, { headers });
      invoiceSettings = setResp.data.data || {};
    } catch (e: any) {
      log(`[SETTING ERROR] ${e.message}`);
    }

    // --- Contacts ---
    const contactRows = await fetchList(`
      SELECT id FROM contacts 
      WHERE user_id=$1 AND organization_id=$2 AND contact_type_id=4
    `, [userId, organizationId]);
    const contactIds = contactRows.map(c => c.id);

    // --- Items ---
    const itemRows = await fetchList(`
      SELECT id, description, unit_price 
      FROM items 
      WHERE user_id=$1 AND organization_id=$2 AND item_type_id=1 AND is_tracked_inventory=FALSE
    `, [userId, organizationId]);
    const itemsData = itemRows
      .filter(r => r.unit_price && parseFloat(r.unit_price) > 0)
      .map(r => ({ id: r.id, description: r.description, unit_price: parseFloat(r.unit_price) }));

    if (contactIds.length === 0 || itemsData.length === 0) {
      log(`[SKIP] No contacts or items.`);
      continue;
    }

    // --- Generate Payloads ---
    let invoiceCounter = 1;
    let currentDate = new Date(START_DATE);
    const yearlyPatternCache: { [key: number]: { [key: number]: number } } = {};
    const allPayloads: any[] = [];

    while (currentDate <= END_DATE) {
      const year = currentDate.getFullYear();
      const month = currentDate.getMonth() + 1;

      if (!yearlyPatternCache[year]) {
        yearlyPatternCache[year] = generateYearPattern();
      }

      const multiplier = yearlyPatternCache[year][month];
      const baseInvoices = Math.floor(Math.random() * 5) + 3; // 3 to 7
      const invoicesToday = Math.max(1, Math.floor(baseInvoices * multiplier));

      for (let i = 0; i < invoicesToday; i++) {
        const invoiceDate = new Date(currentDate);
        invoiceDate.setHours(Math.floor(Math.random() * 11) + 8); // 8 to 18
        invoiceDate.setMinutes(Math.floor(Math.random() * 60));
        invoiceDate.setSeconds(Math.floor(Math.random() * 60));
        if (invoiceDate > END_DATE || invoiceDate > new Date()) continue; // never date an invoice in the future

        const dueDate = new Date(invoiceDate);
        dueDate.setDate(dueDate.getDate() + 14);

        const branchId = branchIds[Math.floor(Math.random() * branchIds.length)];
        const projectId = projectIds[Math.floor(Math.random() * projectIds.length)];
        const runId = Math.random().toString(36).substring(2, 6).toUpperCase();
        const invoiceNumber = `INV-${organizationId}-${year}-${runId}-${String(invoiceCounter).padStart(6, '0')}`;

        const numItems = Math.floor(Math.random() * 3) + 1;
        const shuffledItems = [...itemsData].sort(() => 0.5 - Math.random());
        const chosenItems = shuffledItems.slice(0, Math.min(numItems, itemsData.length));

        let subtotal = 0;
        let totalTax = 0;
        // Always PENDING: payments are recorded by seed-org-5yr-data.ts, not here.
        const statusTypeId = PENDING_STATUS_ID;

        const lines = chosenItems.map(item => {
          const qty = Math.floor(Math.random() * 3) + 1;
          const price = item.unit_price;
          const taxRate = taxRates[Math.floor(Math.random() * taxRates.length)];
          const lineAmount = qty * price;
          const taxAmount = (lineAmount * taxRate.rate) / 100;

          subtotal += lineAmount;
          totalTax += Math.round(taxAmount * 100) / 100;

          return {
            items_id: item.id,
            description: item.description,
            account_id: revenueAccountIds[Math.floor(Math.random() * revenueAccountIds.length)],
            quantity: qty,
            unit_price: price,
            discount_percent: 0,
            discount_amount: 0,
            tax_rate_id: taxRate.id,
            tax_amount: Math.round(taxAmount * 100) / 100,
            line_amount: Math.round(lineAmount * 100) / 100,
            custom_fields: {}
          };
        });

        const totalAmount = Math.round((subtotal + totalTax) * 100) / 100;
        const amountPaid = 0;

        const amountDue = Math.round((totalAmount - amountPaid) * 100) / 100;

        const payload = {
          user_id: userId,
          organization_id: organizationId,
          contact_id: contactIds[Math.floor(Math.random() * contactIds.length)],
          status_type_id: statusTypeId,
          is_recurring: false,
          repeat_frequency_type_id: 3,
          invoice_date: invoiceDate.toISOString(),
          due_date: dueDate.toISOString(),
          never_expires: false,
          start_date: "",
          end_date: "",
          branch_id: branchId,
          reference_id: 0,
          additional_notes: `Invoice #${invoiceCounter}`,
          terms_and_conditions: "Terms & Conditions",
          payment_type_id: Math.floor(Math.random() * 3) + 1,
          invoice_number: invoiceNumber,
          created_date: invoiceDate.toISOString(),
          is_draft: false,
          attachments: [],
          income_type: "INVOICE",
          amount_paid: parseFloat(amountPaid.toFixed(2)),
          amount_due: parseFloat(amountDue.toFixed(2)),
          project_id: projectId,
          reference: `REF-${Math.floor(Math.random() * 90000) + 10000}`,
          purchase_order: `PO-${Math.floor(Math.random() * 90000) + 10000}`,
          account_id: arAccountId,
          bank_name: invoiceSettings.bank_name,
          fullname: invoiceSettings.fullname,
          iban_number: invoiceSettings.iban_number,
          account_number: invoiceSettings.account_number,
          invoice_language: invoiceSettings.invoice_language,
          invoice_logo: invoiceSettings.invoice_logo,
          layout: invoiceSettings.layout || "classic",
          theme_color: invoiceSettings.theme_color || "#000",
          custom_columns: invoiceSettings.custom_columns || [],
          line_items: lines
        };

        allPayloads.push({ invoiceNumber, payload });
        invoiceCounter++;
      }

      currentDate.setDate(currentDate.getDate() + 1);
    }

    log(`[EXEC] Sending ${allPayloads.length} invoices for Org ${organizationId}...`);

    let successCount = 0;
    let postedCount = 0;
    const CONCURRENCY = 1; // Must be 1 to avoid Journal Number collisions in backend

    for (let i = 0; i < allPayloads.length; i += CONCURRENCY) {
      const batch = allPayloads.slice(i, i + CONCURRENCY);
      await Promise.all(batch.map(async ({ invoiceNumber, payload }) => {
        let attempts = 0;
        while (attempts < 3) {
          try {
            const resp = await axios.post(INCOME_URL, payload, { headers, timeout: 30000 });
            successCount++;

            // If the request succeeded, it was successfully sent and (with CONCURRENCY=1) 
            // will be posted to accounting by the backend.
            if (resp.data && resp.data.error === false) {
              postedCount++;
            }
            break;
          } catch (e: any) {
            attempts++;
            const errorBody = e.response?.data ? JSON.stringify(e.response.data) : e.message;
            log(`[ERROR] ${invoiceNumber} attempt ${attempts}: ${errorBody}`);
            if (attempts === 3) break;
            await new Promise(r => setTimeout(r, 1000));
          }
        }
      }));
      if (i % 10 === 0 || i === allPayloads.length - 1) {
        log(`[PROGRESS] Org ${organizationId}: ${Math.min(i + CONCURRENCY, allPayloads.length)}/${allPayloads.length} processed... (Posted: ${postedCount})`);
      }
    }
    log(`[FINISH] Org ${organizationId}: ${successCount}/${allPayloads.length} created, ${postedCount} posted to accounting.`);
  }

  log(`[COMPLETED] Seeding process finished.`);
}

run().catch(e => {
  log(`[FATAL ERROR] ${e.message}`);
  process.exit(1);
});
