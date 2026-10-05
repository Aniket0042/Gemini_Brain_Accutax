"""Phase 3 evaluation gate for the VAT knowledge base (docs/product/VAT_KNOWLEDGE_BASE_PLAN.md).

Runs the 24 test questions (+ 11 additional) through the real GeminiBrainRunner with the
knowledge base switched on: live intent classification and live Claude answers on Bedrock
(costs a little). The Accutax data API is off (use_api=False), so nothing touches the Accutax
backend or database; a VAT question routed to the data path shows up as a routing failure.

    python scripts/eval/vat_kb_eval.py --dir E:/vat_kb_data [--set main|extra|all] [--only 4,13,H3]

Writes <dir>/eval/vat_kb_eval_<timestamp>.md and .json. The keyword check is only a hint:
every answer is graded by reading it against the cited FTA documents.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gemini_brain.config.settings import settings  # noqa: E402

# (id, question, facts a correct answer states (regex), statements a correct answer must not make)
# Checked on every answer as well: a filled Action line, an opening sentence (not a bullet), and no
# Accutax screens or leftover markdown.
EVERY_MUST = [r"\*\*Action:\*\*[ \t]*\S"]
EVERY_MUST_NOT = [r"\A\s*([-*•]|\d+\.)\s", r"\bAccutax\b", r"\A\s*\*\*\s", r"\*\*\s*\Z"]
MAIN = [
    ("1", "What changed in UAE VAT from 1 January 2026 under Federal Decree-Law No. 16 of 2025?",
     [r"self|tax invoice", r"evasion|54", r"five|5[- ]year"], [r"do not contain|cannot provide"]),
    ("2", "What checks must a business carry out on its suppliers so its input tax is not denied?",
     [r"Emirates ID|passport", r"375,000", r"10,000"], []),
    ("3", "How do I value a deemed supply of services?",
     [r"open market value", r"profit margin", r"Input Tax"], []),
    ("4", "We receive payment in USDT. How do we convert it to AED for VAT?",
     [r"three|3 ", r"average"], [r"Central Bank", r"return notes|notes (section|field|box)"]),
    ("5", "A company leaves our VAT tax group mid-year. What output tax and input tax adjustments are needed?",
     [r"own|its tax return", r"previously declared|declared in the tax group"], []),
    ("6", "What are the current late payment penalties for VAT?",
     [r"14 ?%"], [r"300 ?%", r"14 ?% (per )?month|14 ?% monthly"]),
    ("7", "A mainland company buys goods from another company in a designated zone and has them shipped to Dubai mainland. Who charges VAT, and when?",
     [r"import", r"customs"], []),
    ("8", "We sell used cars that we bought from individuals. Can we use the profit margin scheme? What if a car was bought from a VAT-registered dealer who charged VAT?",
     [r"applied the (profit margin )?scheme|under the (profit margin )?scheme|used the scheme", r"previously subject|subject to VAT"], [r"only if you did not claim"]),
    ("9", "A UAE national builds a villa and rents one floor out. Can they still claim the new-residence refund?",
     [r"leased|rent", r"solely|not eligible|cannot|may not"], []),
    ("10", "We bought an entire business, including staff and stock. Do we charge or reclaim VAT on the transfer?",
     [r"going concern", r"not (be )?(treated as |considered )?a supply|outside the scope|no VAT"], []),
    ("11", "Our employees get company mobile phones with unlimited data, and they also use them personally. Can we reclaim the input tax in full?",
     [r"business", r"policy|personal"], []),
    ("12", "Taxable supplies AED 6,000,000, exempt supplies AED 4,000,000, residual input tax AED 100,000. How much can we recover under the standard method, and when is the annual adjustment due?",
     [r"60,000", r"first tax period|annual"],
     # The AED 250,000 test compares two recovery amounts; the question gives no actual-use figure.
     [r"difference is AED ?60,000|so no (mandatory )?adjustment"]),
    ("13", "An invoice for AED 105,000 including VAT; the customer paid 40%, and the invoice is now 8 months old. How much output tax can we adjust under bad debt relief, and what conditions apply?",
     [r"3,000", r"6 months|six months", r"5/105"], [r"3,150"]),
    ("14", "First sale of a residential building 2 years after completion, then a resale in year 5. What is the VAT treatment of each?",
     [r"zero", r"exempt"], []),
    ("15", "Labour accommodation: residential (exempt) or serviced (standard-rated)? What decides it?",
     [r"serviced", r"residential"], []),
    ("16", "A board member who is a natural person receives director fees. Who must register, and when is the date of supply?",
     [r"not (be )?(considered|treated as) a supply|outside the scope|not a supply"], [r"must register for VAT if"]),
    ("17", "We export consultancy services to a Saudi client whose manager visits Dubai during the project. Is the supply still zero-rated?",
     [r"month", r"connected"], []),
    ("18", "We re-invoice courier fees to a client at cost. Is this a disbursement, or part of our supply?",
     [r"disbursement", r"on behalf|agent"], []),
    ("19", "We sell smartphones to a VAT-registered reseller. Who accounts for the VAT? And what if the buyer is a retail consumer?",
     [r"reverse charge", r"resale|resell"], []),
    ("20", "How long must we keep VAT records for general supplies, and for real estate?",
     [r"5 years|five years", r"15 years|fifteen"], []),
    ("21", "When does e-invoicing become mandatory for a business with AED 60 million revenue, and by what date must it appoint an Accredited Service Provider?",
     [r"30 Oct\w* 2026|October 30, 2026", r"1 Jan\w* 2027|January 1, 2027"], []),
    ("22", "What is the penalty for not issuing an e-invoice?",
     [r"100", r"5,000"], []),
    ("23", "What is the criteria for e-invoicing currently?",
     [r"50(,000,000| ?million| ?m)", r"31 Mar\w* 2027|March 31, 2027"], []),
    ("24", "What is the flow of e-invoicing in UAE?",
     [r"Accredited Service Provider|ASP"], []),
]
EXTRA = [
    ("H1", "At what turnover must a business register for VAT, when can it register voluntarily, and how quickly must it apply?",
     [r"375,000", r"187,500", r"30 (\(thirty\) )?days"], []),
    ("H2", "How long does a tourist have to claim back VAT, and what happens to refunds that are never claimed?",
     [r"one year|12 months|one-year", r"one month|Authority"], []),
    ("H3", "We buy scrap metal from a registered supplier for recycling. Who pays the VAT, and since when?",
     [r"recipient|buyer|reverse charge", r"14 Jan\w* 2026|January 14, 2026"], [r"4 Nov\w* 2025"]),
    ("H4", "We reported zero-rated sales in the exempt box of our VAT return, but the total tax due is unchanged. Do we need a voluntary disclosure?",
     [r"voluntary disclosure", r"\byes\b|must|required"], []),
    ("H5", "Who can claim back the VAT spent on building and running a mosque?",
     [r"donor", r"operator"], []),
    ("H6", "When can we issue a simplified tax invoice instead of a full one?",
     [r"not (a )?regist", r"10,000"], []),
    ("H7", "Our invoice is in US dollars. Which exchange rate do we use for the VAT amount?",
     [r"Central Bank", r"date of supply"], []),
    ("H8", "Do we charge or report VAT on interest from our bank deposits and dividends from shares we hold?",
     [r"outside the scope", r"report"], []),
    ("H9", "A wholesaler sells gold jewellery to a VAT-registered retailer who will resell it. Who accounts for the VAT?",
     [r"recipient|retailer|buyer", r"reverse charge"], []),
    ("H11", "We found an error in last quarter's return that understated tax by AED 8,000. Voluntary disclosure, or correct it in the next return?",
     [r"10,000", r"20 business days", r"earlier"],
     # Article 10 (as amended by Cabinet Decision No. 17 of 2026): the return is the route and a
     # voluntary disclosure only the fallback; no source says a disclosure reduces a penalty.
     [r"\bamend(ed)? (the |your |that |a )?(next |current |previous )?(VAT |tax )?return|\bamend a return",
      r"reduce (any |the )?(potential )?penalt", r"if you prefer|not mandatory but",
      r"20\d\d (Cabinet Decision|amendment)[^.]{0,60}introduced"]),
    ("H10", "Do we have to issue a tax invoice for a supply that is wholly zero-rated, such as an export of goods?",
     [r"not required|no requirement|need not|do not (need|have) to|don't (need|have) to"], []),
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dir", required=True)
    p.add_argument("--set", choices=["main", "extra", "all"], default="all")
    p.add_argument("--only", help="comma-separated ids, e.g. 4,13,H3")
    p.add_argument("--pause", type=float, default=1.0)
    args = p.parse_args()

    settings.vat_kb_enabled = True
    settings.vat_kb_shadow = False
    settings.vat_kb_dir = args.dir
    settings.vat_kb_timeout_seconds = 20.0   # the laptop is slower than the VM; latency is measured separately

    from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    load_knowledge_base(args.dir).search("warm up")

    cases = (MAIN if args.set in ("main", "all") else []) + (EXTRA if args.set in ("extra", "all") else [])
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c[0] in wanted]

    runner = GeminiBrainRunner()
    out = []
    for cid, question, must, must_not in cases:
        started = time.time()
        res = runner.run(question, organization_id=1, use_api=False)
        seconds = round(time.time() - started, 1)
        answer = res.get("answer") or ""
        trace = res.get("agent_trace") or []
        vat = next((e for e in trace if e.get("step") == "vat_kb"), None)
        model = next((e.get("model") for e in trace if e.get("step") == "gemini_answer"), "")
        routing = res.get("routing_info") or {}
        sources = [b.get("text", "") for b in res.get("blocks") or [] if b.get("type") == "fta_sources"]
        missing = [rx for rx in must + EVERY_MUST if not re.search(rx, answer, re.I)]
        forbidden = [rx for rx in must_not + EVERY_MUST_NOT if re.search(rx, answer, re.I)]
        used_kb = bool(vat and vat.get("status") == "used")
        hint = "PASS" if used_kb and not missing and not forbidden else "CHECK"
        out.append({"id": cid, "question": question, "answer": answer, "sources_block": sources[0] if sources else "",
                    "routing": routing, "vat_kb": vat, "model": model, "seconds": seconds,
                    "hint": hint, "missing": missing, "forbidden": forbidden, "status": res.get("status")})
        print(f"[{cid:>3}] {hint:5} type={routing.get('type')} path={routing.get('path')} "
              f"kb={(vat or {}).get('status', 'not used')} {seconds:5.1f}s  {question[:60]}", flush=True)
        time.sleep(args.pause)

    folder = Path(args.dir) / "eval"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (folder / f"vat_kb_eval_{stamp}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    with open(folder / f"vat_kb_eval_{stamp}.md", "w", encoding="utf-8") as f:
        f.write(f"# AccuTax AI VAT knowledge base eval {stamp}\n\n")
        for r in out:
            kb = r["vat_kb"] or {}
            f.write(f"## {r['id']}. {r['question']}\n\n*{r['hint']}* | type {r['routing'].get('type')} | "
                    f"{r['routing'].get('path')} | kb {kb.get('status', 'not used')} {kb.get('reason', '')} | "
                    f"{r['model']} | {r['seconds']}s\n\n{r['answer']}\n\n{r['sources_block']}\n\n")
    passed = sum(r["hint"] == "PASS" for r in out)
    print(f"\nkeyword hint: {passed}/{len(out)} -> {folder / f'vat_kb_eval_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
