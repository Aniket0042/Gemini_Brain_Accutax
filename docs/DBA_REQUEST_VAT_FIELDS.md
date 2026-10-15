# Request to the DBA: VAT fields for the AccuTax AI assistant

To: the database administrator of `accutax_bk_1_5`
From: the AccuTax AI team
Date: 2026-10-15
Status: approved by the product owner on 2026-10-15; not yet sent (this file is the text to send).

## What we are asking, in one paragraph

The AI assistant reviews bills and sales invoices for VAT problems. It reads the database only through the read-only Cube role (`cube_reader`). Four facts that a VAT review needs are not in the Cube views today: the supplier's or customer's tax registration number (TRN), the VAT category of each line, the VAT rate or code of each line, and whether a bill line is subject to the reverse charge. We do not know whether the application already stores them. Please tell us where each one lives, and give `cube_reader` read access to it. If a fact is not stored at all, please tell us that too; we will then plan the change with you.

We are not asking you to change any data, and we will not write to the database.

## What the Cube views read today

| View | Tables read |
|---|---|
| Purchases | `expense`, `expense_items`, `status_type`, `contacts`, `expense_category_type`, `projects`, `cost_centers`, `items` |
| Sales | `income`, `income_items`, and the same lookups |
| VAT | the same documents, with `tax_amount` on each line |

Today a line has only its amount and `tax_amount`. A bill has a document number, a date, a status and a supplier name.

## The four questions

For each, the answer we need is "table and column", or "not stored".

1. **Supplier TRN (purchases) and customer TRN (sales).** Is there a column on `contacts` (or another table) that holds the tax registration number? Is it filled in for most contacts? Is it free text or validated as 15 digits?
2. **VAT rate or code on each line.** Does `expense_items` / `income_items` hold a tax code, a rate, or a link to a `taxes` table? Which values exist (for example standard 5%, zero rate, exempt, out of scope)?
3. **VAT category of each line.** If question 2 does not already give it, is there a field that says whether a line is standard rated, zero rated, exempt or out of scope?
4. **Reverse charge.** Is a bill line or bill marked as subject to the reverse charge (for example a purchase from a supplier outside the UAE)? Where, and is the reverse-charge VAT amount stored separately from the ordinary `tax_amount`?

## What we will do with the answers

- Add the fields to the Cube model (`semantic/model`) in our own repository. We need `SELECT` on any table that holds them that `cube_reader` cannot read now (for example a `taxes` table). The current grants are listed in `semantic/dba/cube_reader.sql`.
- Use them to check supplier TRNs (15 digits), split zero-rated from exempt purchases, report reverse-charge VAT, and compare a VAT return with the ledger.

## What we need back

1. For each of the four questions: table, column, example values, and the share of rows where it is filled in.
2. The exact `GRANT SELECT` statements for `cube_reader`, if any table needs one.
3. A date when the grants will be in place on the production database.

## Rollback

`REVOKE SELECT ON <table> FROM cube_reader;` removes any grant added for this request. Nothing else changes.
