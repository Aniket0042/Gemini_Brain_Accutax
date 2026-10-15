"""VAT review evaluation (docs/VAT_REVIEW_PLAN.md, phase 4).

    python scripts/eval/vat_review_eval.py rules                       # the rules on the planted book: no Cube, no model, free
    python scripts/eval/vat_review_eval.py agent --out DIR [--only id,id]   # the questions, through the whole answer path

`rules` runs the duplicate and anomaly rules on PLANTED_BOOK (scripts/eval/vat_review_cases.py) and reports, for each
rule, the planted bills it found, the ones it missed, and any clean bill it flagged. `agent` asks each case's question
through the same function the API uses (agent.preview.answer), with the review's scan of organization 25 replaced by the
planted book for the planted cases, and judges the answer and the card by rule. It needs Cube (the questions on real
data, and the other tools) and Bedrock, so run it on the VM; it costs about $1 to $2.
Results go to DIR/results_agent.json and DIR/report_agent.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(Path(__file__).resolve().parent)]

import vat_review_cases as vc  # noqa: E402


def run_rules(today: dt.date | None = None) -> Dict[str, Any]:
    """The rules on the planted book. Returns {rule: {expected, found, missed}} and the clean bills that were flagged."""
    from gemini_brain.vat_review import anomalies, duplicates

    today = today or dt.date(2026, 10, 15)
    book = vc.planted_book()
    bills, _ = duplicates.usable(book)
    groups = duplicates.find_duplicates(bills)
    findings, patterns = anomalies.find_anomalies(bills, today)
    findings += anomalies.find_future([b for b in bills if b["document_date"] > today.isoformat()], today)
    found: Dict[str, set] = {}
    for g in groups:
        found.setdefault(g["rule"], set()).update(vc.key_of(b) for b in g["bills"])
    for f in findings:
        found.setdefault(f["rule"], set()).add(vc.key_of(f["bill"]))
    result: Dict[str, Any] = {}
    for rule, expected in vc.EXPECTED.items():
        got = found.get(rule, set())
        result[rule] = {"expected": expected, "found": sorted(got), "missed": sorted(set(expected) - got),
                        "extra": sorted(got - set(expected))}
    flagged_clean = sorted({k for keys in found.values() for k in keys} & set(vc.CLEAN))
    return {"rules": result, "clean_flagged": flagged_clean, "patterns": [p["rule"] for p in patterns],
            "unplanned_rules": sorted(set(found) - set(vc.EXPECTED))}


def rules_report(r: Dict[str, Any]) -> str:
    lines = ["# VAT review: rules on the planted book", "", "| Rule | Planted | Found | Missed | Also flagged |", "|---|---|---|---|---|"]
    for rule, v in r["rules"].items():
        lines.append(f"| {rule} | {', '.join(v['expected'])} | {len(v['found'])} | {', '.join(v['missed']) or '-'} | "
                     f"{', '.join(v['extra']) or '-'} |")
    lines += ["", f"Clean bills flagged: {', '.join(r['clean_flagged']) or 'none'}.",
              f"Rules that fired without a planted bill: {', '.join(r['unplanned_rules']) or 'none'}.",
              f"Patterns: {', '.join(r['patterns']) or 'none'}."]
    return "\n".join(lines)


def run_agent(only: List[str] | None, out: Path) -> List[Dict[str, Any]]:
    from gemini_brain.agent import preview

    results: List[Dict[str, Any]] = []
    for case in vc.CASES:
        if only and case["id"] not in only:
            continue
        org = case["org"]
        meta = {org: {"name": vc.ORG_NAME if case["planted"] else f"Organization {org}", "currency": "AED"}}
        t0 = time.perf_counter()
        try:
            if case["planted"]:
                with vc.planted_cube(org):
                    resp = preview.answer(case["question"], [org], lambda: meta, session_id=None, user_id=0)
            else:
                resp = preview.answer(case["question"], [org], lambda: meta, session_id=None, user_id=0)
        except Exception as e:  # noqa: BLE001 - one broken case must not lose the others
            results.append({"id": case["id"], "question": case["question"], "error": f"{type(e).__name__}: {e}"})
            print(f"  ERROR {case['id']}: {e}", flush=True)
            continue
        tools = [c.get("name") for c in resp.get("agent_trace") or []]
        verdict = vc.judge(case, resp.get("answer") or "", tools, resp.get("blocks") or [])
        usage = resp.get("token_usage") or {}
        results.append({"id": case["id"], "question": case["question"], "org": org, "planted": case["planted"], "tools": tools,
                        "verdict": verdict, "answer": resp.get("answer"),
                        "blocks": [b.get("type") for b in resp.get("blocks") or []],
                        "grounded": (resp.get("verification") or {}).get("grounded"),
                        "seconds": round(time.perf_counter() - t0, 1), "cost": usage.get("cost_usd", 0)})
        print(f"  {'PASS' if verdict['ok'] else 'FAIL'}  {case['id']:<16} {case['question'][:50]!r} {results[-1]['seconds']}s "
              f"${results[-1]['cost'] or 0:.3f}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results_agent.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "report_agent.md").write_text(agent_report(results), encoding="utf-8")
    return results


def agent_report(results: List[Dict[str, Any]]) -> str:
    done = [r for r in results if "verdict" in r]
    passed = sum(1 for r in done if r["verdict"]["ok"])
    cost = sum(r.get("cost") or 0 for r in done)
    times = sorted(r["seconds"] for r in done)
    lines = ["# VAT review: questions through the whole answer path", "",
             f"{passed}/{len(results)} pass by rule. Cost ${cost:.3f}. Median {times[len(times) // 2] if times else '-'} s, "
             f"slowest {times[-1] if times else '-'} s.", ""]
    for r in results:
        if "error" in r:
            lines += [f"## {r['id']}: ERROR", r["error"], ""]
            continue
        lines += [f"## {r['id']}: {'PASS' if r['verdict']['ok'] else 'FAIL'}", f"Question: {r['question']}",
                  f"Tools: {', '.join(r['tools']) or 'none'}. Blocks: {', '.join(r['blocks']) or 'none'}. "
                  f"Verifier grounded: {r['grounded']}. {r['seconds']} s, ${r['cost'] or 0:.3f}."]
        for name, ok in r["verdict"]["checks"].items():
            lines.append(f"- {'ok' if ok else 'FAILED'}: {name}")
        lines += ["", "Answer:", "", r["answer"] or "", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    a = sub.add_parser("agent")
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()
    if args.cmd == "rules":
        report = run_rules()
        print(rules_report(report))
        bad = [k for k, v in report["rules"].items() if v["missed"]] + report["clean_flagged"]
        sys.exit(1 if bad else 0)
    results = run_agent(args.only.split(",") if args.only else None, args.out)
    print(f"\n{sum(1 for r in results if r.get('verdict', {}).get('ok'))}/{len(results)} pass by rule; "
          f"cost ${sum(r.get('cost') or 0 for r in results):.3f}")


if __name__ == "__main__":
    main()
