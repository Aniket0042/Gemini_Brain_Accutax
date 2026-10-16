"""UAE VAT law evaluation (docs/VAT_AGENT_COMPLETION_PLAN.md, package 3).

    python scripts/eval/vat_law_eval.py facts                          # free: does the search find each case's fact?
    python scripts/eval/vat_law_eval.py agent --out DIR [--only id,id]  # the questions through the answer path (paid)

`facts` searches the knowledge base (VAT_KB_DIR) with each English question and reports whether the top passages hold
one of the facts the case expects: a case whose fact the knowledge base does not hold cannot be answered right, and is a
fault of the case or of the knowledge base, not of the model. `agent` asks every question through agent.preview.answer,
the function the API uses, and judges the answer by vat_law_cases.judge: it states the rule, cites a source, the sources
are listed under it, it did not review the books, and an Arabic question is answered in Arabic. About $1 to $2.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(Path(__file__).resolve().parent)]

import vat_law_cases as lc  # noqa: E402

PASSAGES = 6


def run_facts() -> List[Dict[str, Any]]:
    from gemini_brain.config.settings import settings
    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    kb = load_knowledge_base(settings.vat_kb_dir)
    out = []
    for case in lc.CASES:
        if case["arabic"]:
            continue
        result = kb.search(case["question"])
        text = " ".join((h.chunk.text or "") for h in result.hits[:PASSAGES])
        found = lc.states_a_fact(text, case["facts"])
        out.append({"id": case["id"], "confident": bool(result.confident), "fact_in_passages": found,
                    "top": [h.chunk.title for h in result.hits[:3]]})
        print(f"  {'ok  ' if found and result.confident else 'MISS'}  {case['id']:<22} confident={result.confident} "
              f"top={out[-1]['top'][0][:60] if out[-1]['top'] else '-'}", flush=True)
    return out


def run_agent(only: List[str] | None, out_dir: Path, org: int) -> List[Dict[str, Any]]:
    from gemini_brain.agent import preview
    results = []
    for case in lc.CASES:
        if only and case["id"] not in only:
            continue
        t0 = time.perf_counter()
        try:
            resp = preview.answer(case["question"], [org], lambda: {org: {"name": f"Organization {org}", "currency": "AED"}},
                                  session_id=None, user_id=0)
        except Exception as e:  # noqa: BLE001
            results.append({"id": case["id"], "error": f"{type(e).__name__}: {e}"})
            continue
        tools = [c.get("name") for c in resp.get("agent_trace") or []]
        verdict = lc.judge(case, resp.get("answer") or "", tools, resp.get("blocks") or [])
        usage = resp.get("token_usage") or {}
        results.append({"id": case["id"], "question": case["question"], "route": (resp.get("routing_info") or {}).get("path"),
                        "tools": tools, "verdict": verdict, "answer": resp.get("answer"),
                        "seconds": round(time.perf_counter() - t0, 1), "cost": usage.get("cost_usd", 0)})
        print(f"  {'PASS' if verdict['ok'] else 'FAIL'}  {case['id']:<22} {results[-1]['route']:<12} "
              f"{results[-1]['seconds']}s ${results[-1]['cost'] or 0:.3f}", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results_law.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report_law.md").write_text(report(results), encoding="utf-8")
    return results


def report(results: List[Dict[str, Any]]) -> str:
    done = [r for r in results if "verdict" in r]
    passed = sum(1 for r in done if r["verdict"]["ok"])
    lines = ["# VAT law evaluation", "", f"{passed}/{len(results)} pass. Cost ${sum(r.get('cost') or 0 for r in done):.3f}.", ""]
    for r in results:
        if "error" in r:
            lines += [f"## {r['id']}: ERROR", r["error"], ""]
            continue
        lines += [f"## {r['id']}: {'PASS' if r['verdict']['ok'] else 'FAIL'} ({r['route']}, {r['seconds']} s)",
                  f"Question: {r['question']}"]
        lines += [f"- {'ok' if ok else 'FAILED'}: {name}" for name, ok in r["verdict"]["checks"].items()]
        lines += ["", r["answer"] or "", ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("facts")
    a = sub.add_parser("agent")
    a.add_argument("--out", required=True, type=Path)
    a.add_argument("--only")
    a.add_argument("--org", type=int, default=25)
    args = ap.parse_args()
    if args.cmd == "facts":
        rows = run_facts()
        print(f"\n{sum(1 for r in rows if r['fact_in_passages'] and r['confident'])}/{len(rows)} cases have their fact in the passages")
        return
    rows = run_agent(args.only.split(",") if args.only else None, args.out, args.org)
    print(f"\n{sum(1 for r in rows if r.get('verdict', {}).get('ok'))}/{len(rows)} pass; "
          f"cost ${sum(r.get('cost') or 0 for r in rows):.3f}")


if __name__ == "__main__":
    main()
