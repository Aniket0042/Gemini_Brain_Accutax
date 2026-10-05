"""Retrieval check for the VAT knowledge base (Phase 1 exit criterion; no LLM calls, no cost).

For every test question, reports where the first chunk of an expected document lands in the
search results, and whether the search counted as confident. Also runs questions that must NOT
be answered from the knowledge base (app how-to, live data, other topics) to check the
confidence thresholds.

    python scripts/eval/vat_kb_retrieval_check.py --dir E:/vat_kb_data
Exit code 0 when every VAT question finds its document in the top 8 and is confident.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gemini_brain.vat_kb.retrieval import load_knowledge_base  # noqa: E402

# (question, titles that count as the right source — prefix match)
VAT_QUESTIONS = [
    ("What changed in UAE VAT from 1 January 2026 under Federal Decree-Law No. 16 of 2025?", ["Amendments to the VAT Federal Decree-Law No. 8 of 2017"]),
    ("What checks must a business carry out on its suppliers so its input tax is not denied?", ["FTA Decision No. 13 of 2026"]),
    ("How do I value a deemed supply of services?", ["Directive on Tax Transactions No. 5"]),
    ("We receive payment in USDT. How do we convert it to AED for VAT?", ["Directive on Tax Transactions No. 3"]),
    ("A company leaves our VAT tax group mid-year. What output tax and input tax adjustments are needed?", ["Directive on Tax Transactions No. 2"]),
    ("What are the current late payment penalties for VAT?", ["Cabinet Decision 129 of 2025"]),
    ("A mainland company buys goods from another company in a designated zone and has them shipped to Dubai mainland. Who charges VAT, and when?", ["Goods Supplied in a Designated Zone", "Designated Zones VAT Guide"]),
    ("We sell used cars that we bought from individuals. Can we use the profit margin scheme? What if a car was bought from a VAT-registered dealer who charged VAT?", ["Profit Margin Scheme | VATGPM1"]),
    ("A UAE national builds a villa and rents one floor out. Can they still claim the new-residence refund?", ["VAT Refund for UAE Nationals Building New Residences"]),
    ("We bought an entire business, including staff and stock. Do we charge or reclaim VAT on the transfer?", ["Transfer of a Business as a Going Concern"]),
    ("Our employees get company mobile phones with unlimited data, and they also use them personally. Can we reclaim the input tax in full?", ["Mobile Phones, Airtime"]),
    ("Taxable supplies AED 6,000,000, exempt supplies AED 4,000,000, residual input tax AED 100,000. How much can we recover under the standard method, and when is the annual adjustment due?", ["Input Tax Apportionment Special Methods", "Taxable Person Guide", "Financial Services VAT Guide"]),
    ("An invoice for AED 105,000 including VAT; the customer paid 40%, and the invoice is now 8 months old. How much output tax can we adjust under bad debt relief, and what conditions apply?", ["Adjustment on Account of Bad Debt Relief"]),
    ("First sale of a residential building 2 years after completion, then a resale in year 5. What is the VAT treatment of each?", ["Real Estate Guide"]),
    ("Labour accommodation: residential (exempt) or serviced (standard-rated)? What decides it?", ["Labour Accommodation"]),
    ("A board member who is a natural person receives director fees. Who must register, and when is the date of supply?", ["Performing the function of Director on a Board of Directors by a Natural person - VATP037"]),
    ("We export consultancy services to a Saudi client whose manager visits Dubai during the project. Is the supply still zero-rated?", ["Zero-rating of export of services"]),
    ("We re-invoice courier fees to a client at cost. Is this a disbursement, or part of our supply?", ["Disbursements"]),
    ("We sell smartphones to a VAT-registered reseller. Who accounts for the VAT? And what if the buyer is a retail consumer?", ["Cabinet Decision No. 91 of 2023", "Application of the Reverse Charge Mechanism on Electronic Devices"]),
    ("How long must we keep VAT records for general supplies, and for real estate?", ["Federal Decree-Law No. 28 of 2022", "Real Estate Guide", "Taxable Person Guide", "Cabinet Decision No. 74 of 2023"]),
    ("When does e-invoicing become mandatory for a business with AED 60 million revenue, and by what date must it appoint an Accredited Service Provider?", ["Ministerial Decision No. 244"]),
    ("What is the penalty for not issuing an e-invoice?", ["Cabinet Decision No.106 of 2025"]),
    ("What is the criteria for e-invoicing currently?", ["Ministerial Decision No. 244"]),
    ("What is the flow of e-invoicing in UAE?", ["Ministerial Decision No. 243", "Ministerial Decision No. 244"]),
    # 10 additional questions
    ("At what turnover must a business register for VAT, when can it register voluntarily, and how quickly must it apply?", ["Executive Regulation of Federal Decree Law No 8", "Taxable Person Guide", "Federal Decree-Law No. 8 of 2017"]),
    ("How long does a tourist have to claim back VAT, and what happens to refunds that are never claimed?", ["Federal Tax Authority Decision No. 4 of 2022"]),
    ("We buy scrap metal from a registered supplier for recycling. Who pays the VAT, and since when?", ["Cabinet Decision No. 153 of 2025", "Application of the Reverse Charge Mechanism on Metal Scrap"]),
    ("We reported zero-rated sales in the exempt box of our VAT return, but the total tax due is unchanged. Do we need a voluntary disclosure?", ["FTA Decision No. 8 of 2024"]),
    ("Who can claim back the VAT spent on building and running a mosque?", ["Cabinet Decision No. 82 of 2022", "Refund of VAT Incurred on the Construction and Operation of Mosques"]),
    ("When can we issue a simplified tax invoice instead of a full one?", ["Executive Regulation of Federal Decree Law No 8", "Tax Invoices", "Taxable Person Guide"]),
    ("Our invoice is in US dollars. Which exchange rate do we use for the VAT amount?", ["Use of Exchange Rates"]),
    ("Do we charge or report VAT on interest from our bank deposits and dividends from shares we hold?", ["Bank Interest and Dividends"]),
    ("A wholesaler sells gold jewellery to a VAT-registered retailer who will resell it. Who accounts for the VAT?", ["The Application of the Reverse Charge Mechanism on Precious Metals", "Application of the Reverse Charge Mechanism on Precious Metals"]),
    ("Do we have to issue a tax invoice for a supply that is wholly zero-rated, such as an export of goods?", ["Executive Regulation of Federal Decree Law No 8", "Taxable Person Guide", "Tax Invoices"]),
]

# Questions the knowledge base should NOT be confident about.
OFF_TOPIC = [
    "How do I create a new invoice in Accutax?",
    "Where can I find the expense module?",
    "Show me my top 5 customers by revenue",
    "What was our total revenue last quarter?",
    "Forecast cash flow for the next three months",
    "How do I add a new user to my organisation?",
    "What is double-entry bookkeeping?",
    "Explain the difference between accounts payable and accounts receivable",
    "What is the capital of France?",
    "Write me a poem about the sea",
    "How do I reset my password?",
    "Give me a business health check",
    "What is EBITDA?",
    "How do I export the trial balance to Excel?",
    "Which customers have overdue invoices?",
    "What is the GST rate on restaurant services in India?",
    "Summarise this conversation",
    "How do I connect my bank account?",
    "What is depreciation?",
    "Show the balance sheet for March",
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True, type=Path)
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--rerank", action="store_true")
    args = p.parse_args()
    kb = load_knowledge_base(args.dir, rerank=args.rerank)
    if kb is None:
        print("no build found")
        return 1

    print(f"build {kb.build_dir.name}: {len(kb.chunks)} chunks\n")
    print(" #  rank  conf  dense  bm25   ms  question")
    found = confident = 0
    for n, (question, wanted) in enumerate(VAT_QUESTIONS, 1):
        result = kb.search(question)
        rank = next((i + 1 for i, h in enumerate(result.hits) if any(h.chunk.title.startswith(w) for w in wanted)), None)
        ok = rank is not None and rank <= args.top
        found += ok
        confident += result.confident
        ms = sum(result.timings_ms.values())
        print(f"{n:2}  {str(rank or '-'):>4}  {'yes' if result.confident else 'NO ':>4}  "
              f"{result.top_dense:.2f}  {result.top_bm25:5.1f}  {ms:4}  {question[:60]}")
    print(f"\nVAT questions: right document in top {args.top}: {found}/{len(VAT_QUESTIONS)}; "
          f"confident: {confident}/{len(VAT_QUESTIONS)}")

    print("\nOff-topic questions (should NOT be confident):")
    false_positives = 0
    for question in OFF_TOPIC:
        result = kb.search(question)
        false_positives += result.confident
        print(f"  {'CONFIDENT' if result.confident else 'ok       '}  dense {result.top_dense:.2f}  "
              f"bm25 {result.top_bm25:5.1f}  {question}")
    print(f"off-topic confident: {false_positives}/{len(OFF_TOPIC)} (the VAT detector in Phase 2 filters these too)")
    return 0 if found == len(VAT_QUESTIONS) and confident == len(VAT_QUESTIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
