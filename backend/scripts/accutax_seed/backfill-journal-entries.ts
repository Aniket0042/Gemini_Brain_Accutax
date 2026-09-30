/**
 * Backfill Journal Entries for Finalised Invoices and Bills
 *
 * Copy of accutax_bk_backend/scripts/backfill-journal-entries.ts, changed for the
 * seed defects found in test orgs 24-33 (see README.md in this folder):
 *   - Dry run by default. Nothing is written without --apply.
 *   - CANCELLED and VOIDED documents are skipped (status ids looked up by name).
 *     The original posted revenue for them: ~2,900 cancelled invoices in each of
 *     orgs 24 and 25 would have been booked as sales.
 *   - Bills (BILL, EXPENSE) are backfilled too; the original covered invoices only.
 *   - --org-id is required unless --all-orgs is given.
 * Like the original, it only creates entries that are MISSING: a document that
 * already has a journal entry is linked to it, never re-posted. It cannot repair
 * an existing entry that is wrong.
 *
 * The journal entries are created by the Accutax app's own services, so this
 * script loads the Accutax backend. Run it from inside accutax_bk_backend (its
 * node_modules, tsconfig and .env are used; nothing in that repo is changed):
 *
 *   cd accutax_bk_backend
 *   npx ts-node -r tsconfig-paths/register \
 *     ../Gemini_Brain_Accutax/backend/scripts/accutax_seed/backfill-journal-entries.ts --org-id 24
 *
 * Options:
 *   --org-id <number>      Organization to backfill (required unless --all-orgs)
 *   --all-orgs             Every organization
 *   --limit <number>       At most this many documents of each kind
 *   --invoices-only        Skip bills
 *   --bills-only           Skip invoices
 *   --apply                Write the journal entries (default: dry run, report only)
 *   ACCUTAX_BACKEND_DIR    Path of accutax_bk_backend (default: the current directory)
 */

import * as dotenv from 'dotenv';
import * as path from 'path';

const BACKEND_DIR = path.resolve(process.env.ACCUTAX_BACKEND_DIR || process.cwd());
dotenv.config({ path: path.join(BACKEND_DIR, '.env') });

/** A module of the Accutax backend, loaded from BACKEND_DIR (never copied here). */
function backend(relative: string): any {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  return require(path.join(BACKEND_DIR, relative));
}

interface Options {
  orgId?: number;
  allOrgs: boolean;
  limit?: number;
  apply: boolean;
  invoices: boolean;
  bills: boolean;
}

function parseArgs(args: string[]): Options {
  const opts: Options = { allOrgs: false, apply: false, invoices: true, bills: true };
  for (let i = 0; i < args.length; i++) {
    switch (args[i]) {
      case '--org-id':
        opts.orgId = parseInt(args[++i], 10);
        break;
      case '--all-orgs':
        opts.allOrgs = true;
        break;
      case '--limit':
        opts.limit = parseInt(args[++i], 10);
        break;
      case '--apply':
        opts.apply = true;
        break;
      case '--invoices-only':
        opts.bills = false;
        break;
      case '--bills-only':
        opts.invoices = false;
        break;
      case '--dry-run':
        break; // the default; kept so old command lines still work
      case '--help':
      case '-h':
        console.log(`
Backfill journal entries (dry run unless --apply):
  --org-id <number>      Organization to backfill (required unless --all-orgs)
  --all-orgs             Every organization
  --limit <number>       At most this many documents of each kind
  --invoices-only        Skip bills
  --bills-only           Skip invoices
  --apply                Write the journal entries
        `);
        process.exit(0);
    }
  }
  if (!opts.orgId && !opts.allOrgs) {
    throw new Error('Pass --org-id <id>, or --all-orgs to backfill every organization.');
  }
  if (!opts.invoices && !opts.bills) {
    throw new Error('--invoices-only and --bills-only together leave nothing to do.');
  }
  return opts;
}

/** Status ids for CANCELLED and VOIDED, by name. */
async function excludedStatusIds(dataSource: any): Promise<number[]> {
  const rows: Array<{ id: number; value: string }> = await dataSource.query(
    `SELECT id, value FROM status_type WHERE UPPER(value) IN ('CANCELLED', 'VOIDED')`,
  );
  if (rows.length < 2) {
    throw new Error(`Expected CANCELLED and VOIDED in status_type, found: ${JSON.stringify(rows)}`);
  }
  return rows.map((r) => Number(r.id));
}

interface Tally {
  created: number;
  linked: number;
  failed: number;
}

async function backfill(
  label: string,
  documents: any[],
  sourceType: 'INCOME' | 'EXPENSE',
  dataSource: any,
  repo: any,
  post: (doc: any) => Promise<void>,
  numberOf: (doc: any) => string,
): Promise<Tally> {
  const tally: Tally = { created: 0, linked: 0, failed: 0 };
  for (let i = 0; i < documents.length; i++) {
    const doc = documents[i];
    try {
      // Never duplicate: an existing entry is linked to the document instead.
      const existing = await dataSource.query(
        `SELECT id FROM journal_entries WHERE source_type = $1 AND source_id = $2`,
        [sourceType, doc.id],
      );
      if (existing.length > 0) {
        await repo.update(doc.id, {
          journal_entry_id: existing[0].id,
          is_posted_to_accounting: true,
          posted_to_accounting_at: new Date(),
          posted_by: doc.user_id,
        });
        tally.linked++;
      } else {
        await post(doc);
        tally.created++;
      }
    } catch (err) {
      console.error(`Error processing ${label} ${numberOf(doc)} (ID: ${doc.id}):`, err);
      tally.failed++;
    }
    if ((i + 1) % 50 === 0 || i === documents.length - 1) {
      console.log(
        `  ${label}: ${i + 1}/${documents.length} processed. Created ${tally.created}, linked ${tally.linked}, failed ${tally.failed}`,
      );
    }
  }
  return tally;
}

async function runBackfill() {
  const opts = parseArgs(process.argv.slice(2));

  const { NestFactory } = backend('node_modules/@nestjs/core');
  const { DataSource } = backend('node_modules/typeorm');
  const { AppModule } = backend('src/app.module');
  const { IncomeService } = backend('src/modules/income/income.service');
  const { IncomeEntity } = backend('src/modules/income/entity/income.entity');
  const { ExpenseService } = backend('src/modules/expense/expense.service');
  const { ExpenseEntity } = backend('src/modules/expense/entity/expense.entity');

  console.log(`Accutax backend: ${BACKEND_DIR}`);
  console.log(`Mode: ${opts.apply ? 'APPLY (writes journal entries)' : 'dry run (no writes; pass --apply to write)'}`);
  console.log('Initializing NestJS application context (this may take a few seconds)...');
  const app = await NestFactory.createApplicationContext(AppModule);

  try {
    const dataSource = app.get(DataSource);
    const excluded = await excludedStatusIds(dataSource);
    console.log(`Skipping status ids ${excluded.join(', ')} (CANCELLED, VOIDED).`);

    let invoices: any[] = [];
    const incomeRepo = dataSource.getRepository(IncomeEntity);
    if (opts.invoices) {
      const q = incomeRepo
        .createQueryBuilder('income')
        .where('income.is_draft = :isDraft', { isDraft: false })
        .andWhere('income.income_type IN (:...types)', { types: ['INVOICE', 'CASH_INVOICE'] })
        .andWhere('(income.is_posted_to_accounting = :isPosted OR income.journal_entry_id IS NULL)', { isPosted: false })
        .andWhere('(income.status_type_id IS NULL OR income.status_type_id NOT IN (:...excluded))', { excluded });
      if (opts.orgId) q.andWhere('income.organization_id = :orgId', { orgId: opts.orgId });
      if (opts.limit) q.limit(opts.limit);
      invoices = await q.getMany();
    }

    let bills: any[] = [];
    const expenseRepo = dataSource.getRepository(ExpenseEntity);
    if (opts.bills) {
      // CASH_EXPENSE is posted through its supplier payment, PURCHASE_ORDER not at all
      // (the same rule the expense service applies on create).
      const q = expenseRepo
        .createQueryBuilder('expense')
        .where('expense.is_draft = :isDraft', { isDraft: false })
        .andWhere('expense.expense_type IN (:...types)', { types: ['BILL', 'EXPENSE'] })
        .andWhere('(expense.is_posted_to_accounting = :isPosted OR expense.journal_entry_id IS NULL)', { isPosted: false })
        .andWhere('(expense.status_type_id IS NULL OR expense.status_type_id NOT IN (:...excluded))', { excluded });
      if (opts.orgId) q.andWhere('expense.organization_id = :orgId', { orgId: opts.orgId });
      if (opts.limit) q.limit(opts.limit);
      bills = await q.getMany();
    }

    console.log(`Found ${invoices.length} invoices and ${bills.length} bills without a journal entry.`);
    if (!opts.apply) {
      console.log('Dry run: nothing written. Re-run with --apply to create the entries.');
      return;
    }

    const incomeService = app.get(IncomeService);
    const expenseService = app.get(ExpenseService);
    const inv = await backfill(
      'invoice', invoices, 'INCOME', dataSource, incomeRepo,
      (doc) => incomeService.createAutomaticJournalEntry(doc, doc.account_id, doc.organization_id),
      (doc) => doc.invoice_number,
    );
    const bil = await backfill(
      'bill', bills, 'EXPENSE', dataSource, expenseRepo,
      (doc) => expenseService.createAutomaticJournalEntry(doc, doc.account_id, doc.organization_id),
      (doc) => doc.receipt_number,
    );

    console.log('\nBackfill completed.');
    console.log(`- Invoices: created ${inv.created}, linked ${inv.linked}, failed ${inv.failed}`);
    console.log(`- Bills:    created ${bil.created}, linked ${bil.linked}, failed ${bil.failed}`);
  } finally {
    await app.close();
    console.log('Application context closed.');
  }
}

runBackfill()
  .then(() => process.exit(0))
  .catch((err) => {
    console.error('\nScript execution failed:', err);
    process.exit(1);
  });
