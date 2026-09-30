# Accutax App Guide

Source of truth for LEFT-path "how do I..." / "where is..." answers (intent types
1 FAQ/How-to and 2 App Guidance). Injected into the direct-answer system prompt
with route paths stripped — see `knowledge/guide_loader.py`. Verified against the
live app (accutax-bk-testing.netlify.app, same build as localhost:5173) on
2026-09-22 and 2026-09-23 — menu names, routes, field names and labels are exact,
not guessed. Sidebar labels come from the app's own menu definition.

Sidebar layout (for orientation): **Sales** (Quote(s), Proforma Invoice(s),
Invoice(s), Cash Invoice(s), Recurring Invoice(s), Delivery Note, Credit Note,
Customer Payment), **Purchases** (Expense(s), Vendor Credit(s), Purchase
Order(s), Cash Expense(s), Supplier Payment(s)), **Banking**, **Reports**,
**Items**, **Taxes** (Tax Dashboard, Tax Rates, Tax Rules, VAT Configuration, VAT
Summary), **Chart of Accounts**, **Accounting Records** (System Logs, Audit
Trails, Journal Entries, Manual Journal, General Ledger), **Contacts**, **Business
Setup** (Projects, Branches, Cost Centers, Categories, Currency Management),
**Inventory Dashboard**, **Adjustments**, **Configuration** (Organization
Configuration, Roles & Permissions, User Management, Document Templates),
**Inbox**, **Documents**.

## Creating an Invoice

1. Go to **Sales > Invoice(s)** and click **Create Invoice** (top right), or
   click **Create Invoice** on the dashboard.
2. Route: `/create-new/invoice`.
3. Fill **Customer Details**:
   - **Select Customer** (required)
   - **Invoice Number** — auto-filled from the org's invoice number template
     (Settings > Organization Configuration > Invoice Settings)
   - **Select Currency** (defaults to AED)
   - **Select Payment Type** (Bank Transfer, Paypal, Stripe, Credit Card, Cash,
     Cheque, etc.)
   - Invoice date (defaults to today) and **Select Due Date**
   - Purchase Order Number, Reference, Select Project — all optional
4. Under **Add Line Items**, click into the row and fill:
   - **Product/Service** — searches the Items catalog; if nothing matches, type
     free text (no catalog match still lets you fill Description/Account/Price
     manually)
   - **Description** (required)
   - **Account** (required) — search the chart of accounts, e.g. type "Sales"
     to find "4000 - Sales Revenue"
   - **Qty** and **Price** (required) — Item Amount calculates automatically
   - Optional per-line: **+ Cost center**, **+ Tax rate**, **+ Discount**
   - Click **+ Add New Item** for another line
5. **Summary** panel shows Subtotal, Total VAT, Total — updates live as you add
   tax rates to line items.
6. Toggle **Is this a recurring invoice?** if needed.
7. Fill **Additional Notes / Payment Instructions** and **Terms & Conditions**
   (optional) — a live invoice preview renders on the right as you fill the form.
8. Click **Save As Draft** or **Save & Send** (top right).

## Creating a Quote

1. Go to **Sales > Quote(s)** (page "Quotes") and click **Create Quote &
   Proforma** (top right).
2. Route: `/create-new/quote`.
3. The **Create New Quote** form uses the same layout as an invoice:
   - **Customer Details**: **Select Customer**, **Quote Number**, currency
     (defaults to AED), **Select Payment Type**, date, **Select Due Date**,
     plus optional Purchase Order Number, Reference and Project
   - **Add Line Items**: Product/Service, Description, Account, Qty and Price
     (all required), with optional **+ Cost center**, **+ Tax rate**,
     **+ Discount** per line
   - **Summary** (Subtotal, Total VAT, Total), Additional Notes, Terms &
     Conditions
4. Click **Save As Draft** or **Save**.

Quote statuses in the list include **Pending** and **Accepted**.

**Converting a quote to an invoice:** in **Sales > Quote(s)**, open the quote's
⋮ row menu and choose **Convert to Invoice** (or **Convert to Delivery
Note**). After conversion the menu shows **Converted** with the new
document's number instead of the convert options.

## Creating a Proforma Invoice

A proforma is a preliminary invoice (e.g. for advance payment or customs)
before the final tax invoice.

1. Go to **Sales > Proforma Invoice(s)** (page "Proformas") and click
   **Create Proforma Invoice** (top right).
2. Route: `/create-new/proforma`.
3. Same form as an invoice: Select Customer, **Proforma Number**, currency,
   payment type, dates, optional PO number/reference/project, then line items
   (Product/Service, Description, Account, Qty, Price, optional tax rate,
   discount, cost center). The page heading reads "Create New proforma".
4. Click **Save As Draft** or **Save**.
5. To turn it into a real invoice later, use the row's ⋮ menu > **Convert to
   Invoice**, same as a quote.

## Creating a Cash Invoice

For a sale paid on the spot — the invoice and its payment are recorded
together, so no separate payment step is needed.

1. Go to **Sales > Cash Invoice(s)** (page "Cash Invoices") and click
   **Create Cash Invoice** (top right).
2. Route: `/create-new/cash_invoice`.
3. Same form as an invoice (Select Customer, number, currency, payment type,
   line items, tax, discount), with one extra field: **Payment Account
   (Bank/Cash)** — the bank or cash account the money was received into. The
   page heading reads "Create New Cash_invoice".
4. Click **Save As Draft** or **Save**.

## Setting Up a Recurring Invoice

1. Go to **Sales > Recurring Invoice(s)** (page "Recurring Invoices") to see
   existing ones (numbered REC-..., tagged **Recurring**). To create one,
   click **Create Invoice** there, or start a normal invoice from **Sales >
   Invoice(s) > Create Invoice**.
2. Route: `/create-new/invoice`.
3. Fill the invoice as usual (customer, line items, tax).
4. Turn on **Is this a recurring invoice?** near the bottom of the form. A
   **Recurring Invoice** panel opens ("Automatically generate this invoice
   on a schedule"):
   - **Starts On***
   - **Repeat Every*** — Daily, Weekly, Monthly, Quarterly or Yearly
   - **At Time*** (shown with the time zone)
   - **Due Date*** — number of days after the invoice date
   - **Ends*** — Never, After (a number of occurrences) or On (a date)
5. Click **Save** in the panel. A **Configure** button then appears next to
   the toggle to change the schedule.
6. Save the invoice (**Save As Draft** or **Save & Send**).

## Creating a Credit Note

A credit note reduces what a customer owes (returns, overcharges, discounts
after invoicing).

1. Go to **Sales > Credit Note** and click **Create Credit Note** (top
   right). Note: this list page is titled "Vendor Notes" in the app, even
   though it is under Sales and holds customer credit notes.
2. Route: `/create-new/credit_note`.
3. Same form as an invoice: **Select Customer**, **Credit_note Number**,
   currency, payment type, optional project, then line items (Product/Service,
   Description, Account, Qty, Price, optional tax rate) for the amounts being
   credited.
4. Click **Save As Draft** or **Save**.

(For a credit you receive from a supplier, see "Recording a Vendor Credit".)

## Adding a Customer

Customers and suppliers share one list: **Contacts**.

1. Go to **Contacts** in the sidebar. Tabs: **All**, **Clients**,
   **Suppliers**. Click **Add New** (top right) > **Create New Customer**.
2. Route: `/create-new-customer`.
3. **Create New Customer** form:
   - **Customer Type**: Business or Individual
   - **Salutation**, **First Name***, **Last Name***, **Organization
     Name***, **Display Name**
   - **Email**, **Work Phone**, **Mobile** (with country code picker)
4. Tabs below the main fields:
   - **Other Details**: **Tax Treatment** (VAT Registered, Non VAT
     Registered, VAT Registered - Designated Zone, Non VAT Registered -
     Designated Zone), **Place of Supply**, **Currency**, **Opening
     Balance**, **Payment Terms** (Due on Receipt, Net 15, Net 30, Net 45,
     Net 60, Due end of the month, Due end of the next month), Website URL,
     Department, Designation, social links
   - **Banking Information**, **Address**, **Contact Persons**
5. Click **Save**.

## Adding / Applying Tax (VAT)

Tax rates are a separate one-time setup, then applied per invoice line.

**Setting up a tax rate (one-time, per organization):**
1. Go to **Taxes > Tax Rates** in the sidebar. (Also reachable from **Taxes >
   Tax Dashboard** — the "Tax Management" hub — via the **Tax Rates** tile, or
   from **Configuration > Organization Configuration > Tax Configuration**.)
2. Route: `/tax-rates`.
3. Click **+ Add Tax Rate** (top right).
4. Fill the panel:
   - **Name*** (e.g. "Standard VAT")
   - **Tax Code** (optional — auto-generated if left blank, e.g. "TX-VAT-5")
   - **Tax Rate (%)*** (e.g. 5 for UAE standard VAT)
   - **Tax Authority** (optional, e.g. "FTA")
   - **Rate Type*** — Sales / Purchase / Exempt / Out of Scope (default Sales)
   - **Tax Type*** — Standard / Zero Rated / Exempt / No VAT / Out of Scope
     (default Standard)
   - **Effective From / Effective To** (optional date range)
   - **Description** (optional notes)
   - Settings toggles: **Compound Tax**, **Recoverable** (on by default),
     **Active** (on by default), **Set as Default**
5. Click **Create Rate**.

**Applying tax to an invoice line:**
1. On the invoice line item, fill Account and Price first.
2. Click **+ Tax rate** under the line — a dropdown lists configured rates,
   e.g. "Standard VAT (5% - Standard)". If it shows "No options", no tax rate
   has been created yet — do the setup above first.
3. Selecting a rate recalculates **Total VAT** and **Total** in the Summary
   panel immediately (e.g. Subtotal AED 1,000 → VAT AED 50 → Total AED 1,050
   for 5%).

**Known gotcha:** the Tax Rates module above (`/tax-rates`) is for *invoice
line* tax. A *separate* per-item default tax field exists on the item creation
form (see below) backed by a different table ("surcharge_type") — on a fresh
org this can show "No tax types available. Please seed tax data first. Contact
admin to run: POST /seed_surcharge_type" even after invoice tax rates are set
up. These are two independent things; don't assume setting up one fixes the
other.

## Creating a Tax Rule

Tax rules apply tax automatically based on conditions (e.g. apply standard VAT
to every sales transaction).

1. Go to **Taxes > Tax Rules** (page "Tax Rules" — "Configure automated rules
   and conditions for tax application") and click **Add Tax Rule**. The Tax
   Dashboard's **Create Tax Rule** quick action opens the same page.
2. Route: `/tax-rules`.
3. **New Tax Rule** form:
   - **General Information**: **Rule Name**, **Description**
   - **Rule Logic**: **Condition Type** (Transaction Type, Customer Type,
     Vendor Type, Product Type, Account Type, Geography, Amount Range, Date
     Range, Custom Field), **Action Type** (Apply Tax Rate, Exempt, Zero
     Rate, Use Reverse Charge, Custom Calculation), **Select Tax Rate**
   - **Settings & Validity**: **Priority**, **Status** (Active/Inactive),
     **Effective From**, **Effective To**
4. Click **Create Rule**.

The rules list shows each rule's Condition, Action, Priority (High, Medium,
Low) and Status. Rules need tax rates to exist first — see "Adding / Applying
Tax (VAT)".

## Using the Tax Calculator

1. Go to **Taxes > Tax Dashboard** and click the **Tax Calculator** tile or
   the **Calculate Tax** quick action. The page is titled "Tax Engine" ("Run
   live tax calculations or review previous transaction history").
2. Route: `/tax-calculator`.
3. Click **New Calculation**. Under **Calculation Parameters** set the
   **Transaction Type** (Invoice, Bill, Quote, Credit Note), **Transaction
   Number**, **Transaction Date**, **Currency**, then add items (**Item
   Description**, **Qty**, **Unit Price**, and a tax rate or No Tax).
4. **Calculation Results** show Subtotal, Total Tax, Total Amount and a Tax
   Breakdown. Past calculations are listed with Date, Transaction, Subtotal,
   Tax Amount, Total and Status.

## Setting Up VAT Registration (VAT Configuration)

1. Go to **Taxes > VAT Configuration** ("Manage value-added tax schemes,
   filing frequencies and authorities") and click **Add VAT Config**.
2. Route: `/vat-configuration`.
3. **New VAT Configuration** form:
   - **Basic Information**: including **VAT Type**, **Default VAT Rate
     (%)**, **Reg. Date**, **Effective Date**
   - **Scheme & Filing**: **Scheme Type** (Standard, Flat Rate, Cash Basis,
     Accrual), **Filing Frequency** (Monthly, Quarterly, Annually), and
     **Flat Rate (%)** for the flat rate scheme
   - **Location & Authority**: Country Name, Country Code, Currency Code,
     **Tax Authority**, Authority URL
   - **Settings** toggles: **Main VAT Config**, **Compound VAT**,
     **Recoverable VAT**, **Partial Recovery**
4. Save the configuration. The list shows VAT Name, Country, Reg. Number,
   Scheme, Filing frequency and Status.

## Viewing the VAT Summary

1. Go to **Taxes > VAT Summary** (page "VAT Summary Report" — "Comprehensive
   overview of VAT output and input tax"). The Tax Dashboard's **VAT
   Reports** quick action opens the same page.
2. Route: `/tax-reports/vat-summary`.
3. Set **Start Date**, **End Date** and **Group By** (Monthly, Quarterly,
   Yearly), then click **Apply**.
4. Cards show **Output Sales**, **Output VAT** (collected on sales), **Input
   Purchases**, **Input VAT** (paid on purchases) and **Net VAT Due** (amount
   payable to the tax authority). Tables below break output and input tax
   down by rate.
5. Click **Export CSV** to download it.

## Filing a VAT Return (FTA Form 201)

Accutax prepares the VAT return figures, but the return itself is submitted
by the user on the FTA e-Services portal — Accutax does not file it with the
FTA.

1. Open the **UAE VAT Report (Form 201)** page ("Standard VAT return format
   for Federal Tax Authority (FTA) submission"). It is not currently listed
   under the Reports page's categories (the Tax Reports category shows "No
   reports available"), so open it directly.
2. Route: `/reports/vat-report`.
3. Set **Start Date** and **End Date**, then click **Generate Report**. The
   form follows the FTA layout:
   - **1a–1g**: Standard Rated Supplies (5% VAT) by Emirate — Abu Dhabi,
     Dubai, Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah
   - **2–7**: Tax Refunds provided to Tourists, Supplies Subject to Reverse
     Charge, Zero Rated Supplies, Exempt Supplies, Goods Imported into UAE,
     Adjustments to Goods Imported
   - **8**: Total Output VAT
   - **9–11**: Standard Rated Expenses, Supplies Subject to Reverse Charge,
     Total Input VAT
   - **12–14**: Total Due Tax for Period, Total Recoverable Tax, **Net VAT
     Payable** (payable to FTA)
4. Use **Print Report** or **Export CSV**.
5. A second page, **VAT Return Export (UAE)** ("Review and export your VAT
   return formatting for FTA submission"), shows the Net VAT Position and
   Output/Input tax boxes with a **Download CSV** button and a **Submission
   Checklist**: download the CSV and review all figures, make sure the
   reporting period matches the FTA portal, log in to the **FTA e-Services
   portal** to submit, and keep the CSV for compliance records.
   Route: `/tax-reports/vat-export`.

## Viewing the Profit & Loss Report

1. Go to **Reports** in the sidebar (page "Reports" — "Generate and view
   financial, sales, purchase and tax reports"). In the **Financial
   Statement** category, find the **Profit & Loss** card (it previews Net
   Profit, Total Revenue, Profit Margin) and click **View Report**.
2. Route: `/reports/profit-loss`.
3. Change the period with the date-range button (defaults to **Last 3
   Months**).
4. Use **Print** or **Export** (top right).

## Viewing the Balance Sheet

1. Go to **Reports** > **Financial Statement** > **Balance Sheet** > **View
   Report**.
2. Route: `/reports/balance-sheet`.
3. Shows assets, liabilities and equity **As of** the end of the selected
   period; change it with the date-range button (defaults to Last 3 Months).
4. Click **Export Report as PDF** to download it.

## Viewing the Cash Flow Statement

1. Go to **Reports** > **Financial Statement** > **Cash Flow Statement** >
   **View Report**.
2. Route: `/reports/cash-flow`.
3. Sections: **Cash Flow from Operating Activities**, **Cash Flow from
   Investing Activities**, **Cash Flow from Financing Activities**. Change the
   period with the date-range button (defaults to Last 3 Months).
4. Click **Export Report as PDF** to download it.

## Viewing the Aged Receivables (AR Aging) Report

1. The **Aged Receivables** report is not currently listed in the Reports
   page's categories (the Sales category shows "No reports available"), so
   open it directly.
2. Route: `/reports/aged-receivables`.
3. It groups unpaid customer invoices by age: **Current (0-30)**, **31-60
   Days**, **61-90 Days**, **91+ Days**. Change the period with the
   date-range button.
4. Click **Export Report as PDF** to download it.

## Viewing Other Financial Reports

The **Reports** page's **Financial Statement** category also has **Trial
Balance** (debit and credit balances of all accounts), **General Ledger**
(detailed record of all journal entries) and **Journal Report** (all journal
entries by date) — click **View Report** on the card. The page's categories
are Financial Statement, Sales, Purchases, Consolidated Financial Statements,
Tax Reports, Payroll, Forecasting and Starred. The top bar has a search box,
organization and period selectors, **Filters** and **Export All**.

## Recording a Customer Payment

Money received from a customer. (Paying a vendor/supplier is a different flow —
see "Recording a Supplier Payment".) Two flows depending on intent:

All customer payments are listed under **Sales > Customer Payment**, which
has a **Record Customer Payment** button (top right).

**Payment against a specific unpaid invoice (most common):**
1. Open the invoice from **Sales > Invoice(s)**, or click **Record Payment**
   directly next to it on the dashboard's "Outstanding Invoices" list, or
   inside the invoice detail panel under the **Payments** tab, click
   **Record First Payment** (or **Record Payment** if payments already exist).
2. This opens `/create-customer-payment?invoice_id=...&customer_id=...`
   ("Record Invoice Payment") with the customer and invoice pre-linked.
3. Fill: **Customer*** (pre-filled), **Paid Through**, **Payment Currency**,
   **Date***, **Amount***, optional Branch/Reference/Project/Cost Center.
4. Under **Select Unpaid Invoice(s)**, click **+ Add Unpaid Invoices** to load
   and pick which invoice(s) the payment applies to.
5. Optional: **Adjustment** (rounding/bank charges/write-offs against a
   chosen account) and **Overpayment** (excess recorded as customer credit).
6. Review **Summary** (Amount Paid / Remaining), add Notes, optionally attach
   a Stamp & Signature image, click **Save**.

**Advance payment with no invoice yet (customer credit):**
1. Route: `/create-customer-payment` (no query params) — labeled "Record
   Advance Payment" instead of "Record Invoice Payment".
2. Same fields as above, minus the invoice-linking step; the full amount goes
   to **Amount to Credit** as available customer credit for future invoices.

## Recording an Expense or Vendor Bill

In Accutax a vendor bill and an expense are the same record: both live under
**Purchases > Expense(s)** and are numbered BILL-.... There is no separate
"Bills" menu.

1. Go to **Purchases > Expense(s)** (page "Expenses") and click **Create
   Expense** (top right). A "Create or scan expense" menu offers:
   - **Scan receipt** — auto-fill the form with AI OCR from an uploaded
     receipt or bill
   - **Record manually** — fill the fields by hand
   - **Send to Accutax** — ask suppliers to email their invoices to your
     Accutax inbox
   The dashboard's **Add Expense** button also opens the form.
2. Route: `/create-expense`.
3. **Create New Expense** form, **Supplier Details**:
   - **Select Supplier** (or **+ New Supplier** to add one)
   - **Enter Receipt Number**
   - **Select Category** (e.g. Rent, Utilities, Salaries & Wages, Travel &
     Lodging, Office Supplies, Software & SaaS, Professional Services,
     Repairs & Maintenance, Miscellaneous)
   - currency, **Select Payment Type**, **Select Date**, **Select Due Date**
   - optional **Project** and **Branch**
   - **Scan Receipt / Bill** — **Upload & Scan** to auto-fill with AI OCR
4. **Add Line Items**: Product/Service, Description, Account, Qty and Price
   (required), optional cost center, tax rate, discount. The Summary shows
   Subtotal, Total VAT, Total.
5. Click **Save As Draft** or **Save**.

To pay it, see "Recording a Supplier Payment".

## Creating a Purchase Order

1. Go to **Purchases > Purchase Order(s)** and click **Create Purchase
   Order** (top right).
2. Route: `/create-expense/purchase-order`.
3. Same layout as an expense: **Select Supplier**, **Enter Receipt Number**,
   **Purchase Order Reference*** (required), category, currency, payment
   type, dates, optional project/branch, then line items.
4. Click **Save As Draft** or **Save**.
5. When the goods or services arrive, open the PO's ⋮ row menu in
   **Purchases > Purchase Order(s)** and choose **Convert to Expense** — it
   opens a new expense pre-filled from the purchase order.

## Recording a Cash Expense

For a purchase paid on the spot — the expense and its payment are recorded
together.

1. Go to **Purchases > Cash Expense(s)** and click **Create Cash Expense**
   (top right).
2. Route: `/create-expense/cash-expense`.
3. Same form as an expense (supplier, receipt number, category, dates, line
   items) plus **Payment Account (Bank/Cash)** — the account the money was
   paid from.
4. Click **Save As Draft** or **Save**.

## Recording a Vendor Credit (Debit Note)

A vendor credit reduces what you owe a supplier on an existing bill (returns,
price corrections, discounts). Accutax numbers them DN-..., so this is also
where debit notes are recorded.

1. Go to **Purchases > Vendor Credit(s)** and click **Create Vendor Credit**
   (top right).
2. Route: `/create-vendor-credit`.
3. **Basic Information**: **Bill** (Select Bill — a vendor credit must be
   linked to an existing bill), **Issued Date**, **Description**.
4. Two summary panels appear: **Bill Summary** (bill number, vendor, total,
   status) and **Vendor Credit Summary** (Total Bill Amount, Available
   Credit, Current Credit Amount, and any existing vendor credits on that
   bill).
5. **Vendor Credit Items**: for each bill line, enter the **New Quantity**,
   **New Unit Price** and/or **New Discount**. Accutax calculates the credit
   as the difference from the currently available amounts and shows the
   **Total Credit Amount**.
6. Click **Create Vendor Credit**.

## Recording a Supplier Payment

Paying a vendor or supplier bill.

1. Go to **Purchases > Supplier Payment(s)** and click **Create Supplier
   Payment** (top right), or click **Make Payment** next to a bill in the
   dashboard's "Outstanding Bills" list.
2. Route: `/create-supplier-payment`.
3. **Payment Details**: **Date***, **Supplier***, **Amount***, Currency,
   Payment Method, Reference, Additional Notes.
4. **Chart of Accounts** — **Select Account for Auto Journal Entry**: the
   Cash/Bank account the payment is made from. Accutax creates the journal
   entry automatically.
5. **Unpaid Bills**: select the supplier first, then click **Add Unpaid
   Bills** to choose which bill(s) this payment settles.
6. Click **Create Payment**.

## Adding a Vendor / Supplier

1. Go to **Contacts**, click **Add New** > **Create New Vendor**. On the
   expense form you can also click **+ New Supplier** next to Select
   Supplier.
2. Route: `/create-new-vendor`.
3. **Create New Vendor** form: **Vendor Type** (Business or Individual),
   **First Name**, **Last Name**, **Organization Name**, **Display Name**,
   **Email**, **Work Phone**, **Mobile**.
4. Tabs below: **Other Details**, **Banking Information**, **Address**,
   **Contact Persons** (similar to the customer form).
5. Click **Save**.

## Bank Reconciliation

1. Go to **Banking** (`/banking`) — dashboard also has a "Reconcile N
   transactions" quick action under Next Actions that jumps here.
2. The Banking hub lists institutions/accounts with columns: Account,
   Currency, IBAN, Balance, Inflow, Outflow, **Recon** (shows "N to review" or
   a % done badge). Click **Expand All** or the row chevron to see individual
   accounts under an institution.
3. Click an account name to open its detail page (`/bank/accounts/:id`), which
   has tabs: **Reconcile (N)**, **Import Statement**, **View Ledger**,
   **Reconciliation Report**, **Auto-Match Rules**, plus **+ Add Transaction**.
4. Click **Reconcile** (`/bank/accounts/:id/reconcile`) to open the matching
   screen: left pane is the **Bank Statement** (filterable: All / Unmatched /
   Matched / Excluded / Auto), right pane is **Accounting Entries** — select an
   unmatched bank transaction on the left to see suggested matches on the
   right.
5. A progress bar at the bottom shows "X% · N/M matched". Use **Reconcile
   All** to auto-match everything possible, or match line by line, then
   **Save & Close**.
6. No bank account yet? From the Banking hub, **+ Add Account** lets you
   manually add one: Account Name*, Account Code, Account Type (Bank / Credit
   Card / Cash), Currency*, Bank Name, Account/IBAN*, SWIFT code, Opening
   Balance, Description.

## Creating an Item / Product

1. Go to **Items** (`/items`), click **+ Add New**, then choose **Create New
   Goods** or **Create New Service** from the dropdown.
2. Route: `/items/create-new-item`.
3. Fill:
   - **Item Type** — Goods or Services toggle
   - **Item Name*** and **Item Number*** (required)
   - **SKU** (optional), **Unit** (defaults to "pcs")
   - Toggle **Is it an excise product?** if applicable
4. **Sales Information**: Selling Price + currency, Account, Description, Tax
   (see the tax-seeding gotcha above — this dropdown can be empty on a fresh
   org even after invoice tax rates exist).
5. **Purchase Information**: Cost Price + currency, Additional/Landing Cost
   (adds to COGS per-unit — freight, duties), Account, Description, Preferred
   Vendor (searchable list), toggle **Track Inventory for this Item**.
6. **Additional Settings**: **Active Status** toggle, **Tax Inclusive
   Pricing** toggle (prices include tax), **Friendly Name** (optional display
   alias).
7. Click **Save**.

## Creating a Manual Journal Entry

For adjustments that don't come from an invoice, expense or payment (accruals,
corrections, reclassifications). Transactions like invoices and expenses post
their own journal entries automatically — no manual entry needed for those.

1. Go to **Accounting Records > Manual Journal** in the sidebar (page title
   "Manual Journal Entries").
2. Route: `/manual-journal`.
3. The page shows cards (**Total Manual Entries**, **Posted** — finalized,
   **Pending Review** — awaiting approval, **Total Debits**, **Total
   Credits**) and a table: Serial number, Status (**Draft** / **Posted**),
   Created On, Reference, Notes, Journal lines (each Dr/Cr account and
   amount), Modified.
4. Click **New Journal Entry** (top right) to open the **Add New Journal
   Entry** form.
5. **Journal Details**: date (**Select Date**), **Reference (Optional)**,
   **Notes**.
6. **Add Line Items** — one row per account:
   - **Account** (Select Account)
   - **Description**
   - **Project** and **Contact** (optional)
   - **Debit** or **Credit** amount
   - **+ Add New Item** adds a row; the remove icon deletes one
7. Total debits must equal total credits. Until they do, the form shows
   "Out of balance by ..." with the difference.
8. Click **Save As Draft** (entry stays Draft / Pending Review) or **Add
   Journal Entry**. To change a draft later, open it from the table — the
   form reopens as **Edit Journal Entry** with an **Update Journal Entry**
   button.

## Viewing & Reversing Journal Entries

Every transaction (invoice, expense/bill, payment, cash sale, credit note)
posts its journal entry automatically; manual entries (above) appear here too.

1. Go to **Accounting Records > Journal Entries** in the sidebar — a ledger
   view of all posted entries.
2. Top cards show **Accounts** (active count), **Total Debits**, **Total
   Credits**, **Net Movement** for the period, plus a **Select Account**
   filter, period toggle (Month/Quarter/Year/Pre Fiscal/Cur. Fiscal), and a
   consolidated Opening/Closing Balance + Balance Trend chart.
3. The table lists every entry: Journal #, Date, Reference, Description,
   **Source** (tag: Payment, Expense, Income, etc.), Total Debit, Total
   Credit, Total Balance — always balanced (debit = credit per entry).
4. Click a Journal # (e.g. `JE-5-2026-00033410`) to open its detail page
   (`/journal-entries/:id`): Journal Number, Transaction Date, Reference
   Number, Source Type, Description, Total Debit/Credit, and the line-item
   table (Account, Description, Debit, Credit, with a **GL** link per line
   to that account's general ledger). Actions at the top: **Reverse Entry**
   (posts an offsetting correction — the original entry is never edited
   in place), **View Source** (jumps to the original invoice/bill/payment),
   **General Ledger**.

## Setting Up the Company Profile

1. Go to **Configuration > Organization Configuration** and click the
   **Company Profile** tile (shows "Setup Pending" / "First setup your
   company profile" until done). It opens **My Profile** on the
   **Organization Info** tab.
2. Route: `/userprofile?tab=organization`.
3. Fill **Organization Name**, **Industry**, **Is your business registered
   for VAT?**, **Tax Registration Number (TRN)**, **VAT Registered On**,
   **Organization Location** (country) and **Emirate**, and use **+ Add
   Organization Address** for the address.
4. Click **Save**.

The **Personal Details** tab on the same page holds your own user profile.
Organization Configuration also has tiles for Payment Settings, Tax
Configuration, Email Preferences, User Preferences, Regional Configuration,
and invoice / credit note / debit note / purchase order settings.

## Inviting Users and Managing Roles

1. Go to **Configuration > User Management** (page "User Roles" — "Manage
   team members, roles, and access permissions"). Cards show Total Members,
   Active Members, Pending Invites and 2FA Enabled; tabs are **Users** and
   **Roles**.
2. Route: `/usermanagement`.
3. To invite someone, click **Invite New Member**: choose **Invite By Email**
   (or **Bulk Import**), enter the **Email Address***, **Select Role**,
   optionally **+ Add Another Email**, then click **Send Invite**.
4. To create a role, use the **Add New Role** form: Role Profile (name,
   description, **Role Colour**) and a **Role Permissions Matrix** — for each
   module tick **Full**, **View**, **Create** or **Edit**. Modules: Sales
   (Customers, Estimates, Invoices, Recurring Invoices, Payments Received),
   Purchases (Vendors, Expenses, Purchase Orders, Bills, Payments Made),
   Items (Items, Inventory Adjustments), Banking, Accountant (Manual
   Journals, Chart of Accounts), Settings & Reports (Taxes, Reports,
   Settings). Click **Save Role**.
5. **Configuration > Roles & Permissions** shows **My Role** (only super
   admins can change their own role — ask the organization owner otherwise)
   and links to **Manage Role Definitions** (create, edit or clone roles)
   and **Team Management**.

## Customizing Document Templates

Controls how invoices, quotes and other documents look when printed or sent.

1. Go to **Configuration > Document Templates** ("Manage your document
   layouts and styles"). Filter by type: Invoice, Quote, Credit Note,
   Purchase Order, Delivery Note, Proforma Invoice, Debit Note, Bill,
   Receipt. One template per type is marked **Default**.
2. Route: `/settings/document-templates`.
3. Click **Create Template**. The editor has these steps:
   - **Layout** — choose a design: Modern, Classic, Standard, Boxed,
     Professional, Premium, Minimal, Creative, Edge, Split, Bold, or industry
     styles Marketing, Freelance, Agency, Article
   - **Columns** — table columns (Description, Quantity, Unit, Unit Price,
     Discount, Amount, Final Amount)
   - **Payments** — payment methods printed on the document; set a default
   - **Branding** — primary and text color, font, signature line, DRAFT
     watermark
   - **Logo** — upload, size, alignment
   - **Locale** — language (English, Arabic, Hindi, Portuguese, French,
     German), date format, currency
   - **Numbering** — next document number, prefix, suffix, digits
   - **Terms** — terms & conditions, footer notes
   - **Template Details** — template name, document type, set as default
4. Click **Save Template**.

While creating an invoice, **Change Template** (top of the form) switches the
template for that document.

## Adding Custom Fields

1. Open the **Settings** page (gear icon at top right, or **Configuration**
   in the sidebar) and click **Custom Fields** ("Add fields to invoices and
   records").
2. Route: `/settings/custom-fields`.
3. The page ("Define custom fields for customers and vendors") has:
   **Entity scope**, **Field key**, **Label** (display label), **Data type**
   (Text, Number, Date, Boolean) and a **Required** option. Click **Add
   field**. Existing fields are listed below.

If this page shows "Component Crashed!", the feature is temporarily
unavailable — contact Accutax support.

## Managing Currencies and Exchange Rates

1. Go to **Business Setup > Currency Management** (page "Currencies"). The
   Settings page also has a **Currency** tile ("Manage currencies and
   exchange rates").
2. Route: `/currency-management`.
3. The base currency is shown at the top (e.g. AED). The **Active Exchange
   Rates** table lists Currency, Rate (1 AED = ...), Last Updated and Status.
4. Click **New Rate**: pick the **Target Currency** (ISO code), enter the
   rate and **Effective Date**, tick **Set as active rate**, then **Save**.
   Row actions: **Edit Rate**, **Delete Rate**.
5. **Converter** converts an amount between currencies.

## Importing Data

1. Open the **Settings** page and click **Data Import** ("Import contacts,
   chart of accounts, items, and more from QuickBooks, Tally, Zoho, or
   files").
2. Route: `/settings/data-import`.
3. Under **Select Entity to Import**, choose one: Contacts (Customers /
   Vendors), Chart of Accounts, Items / Products, Opening Balances, Journal
   Entries, Invoices, Bills, Payments, Bank Accounts.
4. Upload the file, then click **Continue to Column Mapping** to match your
   columns to Accutax fields.
5. Review the validation: Total Rows Found, **Valid & Ready**, **Needs
   Correction**. Fix rows (**Save Row Edits**) or **Edit Column Mapping**,
   and pick a **Duplicate Matching Strategy**.
6. Start the import. It runs in the background (Pending, then Processing)
   and ends **Completed Successfully** or **Completed with Some Errors** —
   **Download Error Report (CSV)** lists the failed rows.
7. **Recent Imports** shows past imports (file, module, status, progress,
   date) with their actions, including rollback.

## Adding a Branch

1. Go to **Business Setup > Branches** ("Manage your business branches and
   locations") and click **Add Branch**.
2. Route: `/branches`.
3. Fill **Branch Name***, **Branch Code**, **Phone**, **Email**, **Address**,
   **City**, **State/Province**, **Country**, **Postal Code**, then **Save**.

Branches can then be picked on documents — **+ Add Branch** under Company
Information on the invoice form, **Select Branch** on the expense form.

## Adding a Project

1. Go to **Business Setup > Projects** ("Manage your projects") and click
   **Add Project**.
2. Route: `/projects`.
3. Enter **Project Name*** and click **Create**.

Projects can then be selected on invoices, expenses and manual journal lines
(**Select Project**) to track income and costs per project.

## Adding a Cost Center

1. Go to **Business Setup > Cost Centers** ("Manage your cost center
   structure") and click **Add Cost Center**.
2. Route: `/cost-centers`.
3. Enter the **Name*** and click **Save**.

On invoice and expense lines, **+ Cost center** tags a line to a cost center.

## Connecting Google Drive, Dropbox or SharePoint

1. Open the **Settings** page (gear icon at top right, or **Configuration**
   in the sidebar). In **Integrations** ("Connect Google Drive, Dropbox, and
   SharePoint to import files into Documents"), click **Connect** next to
   **Google Drive**, **Dropbox** or **SharePoint** (SharePoint also covers
   OneDrive for Business).
2. Route: `/settings`.
3. Follow the provider's sign-in prompt. Imported files appear under
   **Documents** in the sidebar.

## Creating a Delivery Note

A delivery note goes with goods shipped to a customer; it can later be turned
into an invoice.

1. Go to **Sales > Delivery Note** (page "Delivery Notes") and click **Create
   Delivery Note** (top right).
2. Route: `/income/delivery-notes`.
3. The form is the same as an invoice: **Select Customer**, **Delivery Note
   Number**, dates, optional reference and project, then line items
   (Product/Service, Description, Account, Qty, Price).
4. Click **Save As Draft** or **Save**.
5. To bill it, open the delivery note's ⋮ row menu in **Sales > Delivery
   Note** and choose **Convert to Invoice**. A quote can also be converted
   into a delivery note (**Convert to Delivery Note** on the quote's menu).

## Managing the Chart of Accounts

1. Go to **Chart of Accounts** in the sidebar ("Organize and manage your
   account structure").
2. Route: `/chart-of-accounts`.
3. Accounts are grouped by type — **Assets**, **Liabilities**, **Equity**,
   **Revenue**, **Expenses** — with columns Code, Account Name, Cash Flow/Sub
   Type, Currency and Amount. Search by name or code, filter by type, use
   **Expand all** / **Collapse all**, or **Export CSV**.
4. Click **Add Account**. Under **Account Information** fill **Account
   Name***, **Account Code*** (e.g. 4000), the parent account (or **None (Top
   Level)**), and **Cash Flow Type** (Operating, Investing, Financing).
5. Optional usage settings: **Allow posting to this account**, **Enable
   account selection for payments**, **Show account for employee expense
   claims**. Save.

## Adding Expense Categories

1. Go to **Business Setup > Categories** ("Manage transaction categories for
   expenses and reporting") and click **Add Category**.
2. Route: `/categories`.
3. Enter the **Category Name*** and an optional **Description**, then
   **Create**. Existing categories can be edited or deleted from the table
   (Name, Description, Created, Modified).

Categories appear in **Select Category** on the expense form.

## Recording an Inventory Adjustment

For correcting stock quantities (physical count differences, damage, theft,
expiry).

1. Go to **Adjustments** in the sidebar, under Inventory (page "Inventory
   Adjustments" — "Manage and track your inventory corrections"). The
   **Inventory Dashboard** above it shows overall stock.
2. Route: `/inventory/adjustments`.
3. Click **New Adjustment**. Adjustment types: **Physical Count**,
   **Damage**, **Theft**, **Expiry**, **Other**. Add a reason and the line
   items — for each item, the **System Qty**, the **Physical Qty** you
   counted, and the resulting **Difference**, **Unit Cost** and **Total
   Value**.
4. An adjustment moves through **Draft**, **Approved** and **Posted** (or
   **Voided**). Use the row actions **Edit**, **Approve**, **Post** or
   **Void** (a void asks for a reason). Filter the list by type, status or
   date, or search by reference number or reason.

## Processing Supplier Invoices in the Email Inbox

Suppliers can email their invoices straight to your Accutax account.

1. Go to **Inbox** in the sidebar (page "Email Inbox" — "Review and process
   supplier invoices sent directly to your AccuTax address").
2. Route: `/inbox`.
3. Your organization's unique Accutax email address is shown at the top —
   give it to suppliers or forward invoices to it.
4. Received emails are listed under **All**, **To do** and **Done**; use
   **Refresh** to check for new ones, and process each invoice into an
   expense.

The **Send to Accutax** option under **Purchases > Expense(s) > Create
Expense** points to this same inbox.

## Uploading and Organizing Documents

1. Go to **Documents** in the sidebar ("Store, organize, and manage all your
   business documents").
2. Route: `/documents`.
3. Click **New** to **Upload File**, create a **New Folder**, or import from
   **Google Drive**, **Dropbox** or **SharePoint** (once connected — see
   "Connecting Google Drive, Dropbox or SharePoint").
4. Folders include Home, Bank Statements, File Request, Inbox, Starred and
   Trash.
5. **New File Request** asks someone (e.g. a client or accountant) to send
   you files.

## Setting Up Bank Auto-Match Rules

Rules that categorize bank transactions automatically during reconciliation.

1. Go to **Banking**, open a bank account, and click **Auto-Match Rules**
   (page "Transaction Rules" — "Automate transaction categorization with
   custom rules").
2. Route: `/transaction-rules`.
3. Click **New Rule**. Under **Basic Information** enter the **Rule Name**,
   **Bank Name** (leave empty for all banks) and **Apply To** (Deposits
   Only, Withdrawals Only, or Both). Set the **Match Criteria**.
4. Under **Accounting** choose how matched transactions are recorded (**Record
   As**) and the **Chart of Account** to post to, plus an optional Reference
   Number. Keep **Rule is active** ticked and click **Create Rule**.

The rules list shows Rule Name, Bank, Criteria, Account and Status.

## Setting Invoice Numbering

Controls the invoice number that is filled in automatically on new invoices.

1. Go to **Configuration > Organization Configuration** and open the
   **Invoice Settings** tile (page "Invoice Template Settings").
2. Route: `/invoice-settings`.
3. Under **Invoice Numbering** set the **Prefix** (e.g. INV-), **Suffix**
   (e.g. -2026) and **Next Invoice Number**. **Default Due Date Days** sets
   the default due date on new invoices.
4. Click **Save**.

Each document template also has its own **Numbering** step (see
"Customizing Document Templates").

## Viewing Sales by Customer and Statements of Account

1. These reports are not listed on the Reports page's categories, so open
   them directly.
2. Route: `/reports/sales-by-contact`.
3. **Sales by Customer** lists Customer Name and Total Sales for the period.
4. **Customer Statement of Account** shows, per customer, each transaction's
   Date, Activity, Amount and Running balance.
   Route: `/reports/customer-statement`.
5. Change the period with the date-range button (defaults to Last 3 Months)
   and use **Export Report as PDF**.

Other reports that exist the same way include Supplier Statement of Account,
Sales by Item and Aged Payables.

## Changing Your Password

1. Open the **Settings** page (gear icon at top right). Under **Account &
   Security** click **Change Password**.
2. Route: `/changepassword`.
3. Enter the **Current password**, a **New password**, and **Confirm new
   password**, then click **Change password**.

## Enabling Two-Factor Authentication (MFA)

1. Open the **Settings** page (gear icon at top right). Under **Account &
   Security**, turn on **Multi-factor authentication** ("Add an extra layer
   of security to your account").
2. Route: `/settings`.
3. In **Set up two-factor authentication**: install the **Google
   Authenticator** app, scan the **QR Code** with it, enter the
   **Verification Code** the app shows, and click **Activate** (or **Skip for
   now**).
