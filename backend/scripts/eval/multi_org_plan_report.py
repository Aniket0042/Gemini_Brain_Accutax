"""
multi_org_plan_report.py — What real multi-organization questions the rules miss.

Reads the plan log written by gemini_brain/orchestrator/multi_org_log.py
(MULTI_ORG_PLAN_LOG, default backend/logs/multi_org_plans.jsonl) and prints:
how questions were planned and laid out, how many the rules matched, the
slowest and most expensive ones, and every unmatched question (grouped when
the same wording repeats). Unmatched questions are the next rules to write.

Usage (from backend/):
  .venv/Scripts/python scripts/eval/multi_org_plan_report.py
  .venv/Scripts/python scripts/eval/multi_org_plan_report.py --since 2026-09-01 --top 30
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for path in (_BACKEND_ROOT / "src", _BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gemini_brain.orchestrator.multi_org_log import log_path, read_records  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", default="", help="Only records on or after this date (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=20, help="How many unmatched questions to list")
    args = parser.parse_args()

    records = [r for r in read_records() if not args.since or str(r.get("ts", "")) >= args.since]
    print(f"Log: {log_path()}")
    if not records:
        print("No records yet.")
        return 0

    matched = sum(1 for r in records if r.get("matched"))
    print(f"\n{len(records)} questions · {matched} matched by rules ({100 * matched // len(records)}%)")
    for title, key in (("By plan source", "source"), ("By layout", "layout"), ("By status", "status")):
        print(f"\n{title}:")
        for value, count in Counter(str(r.get(key)) for r in records).most_common():
            print(f"  {value:20} {count}")

    metrics = Counter(m for r in records for m in (r.get("metrics") or []))
    if metrics:
        print("\nMetrics asked for:")
        for value, count in metrics.most_common():
            print(f"  {value:20} {count}")

    print("\nSlowest:")
    for r in sorted(records, key=lambda r: -(r.get("elapsed_seconds") or 0))[:5]:
        print(f"  {r.get('elapsed_seconds')}s  llm={r.get('llm_calls')}  [{r.get('layout')}] {r.get('question')}")

    unmatched = Counter(str(r.get("question", "")).strip().lower() for r in records if not r.get("matched"))
    print(f"\nUnmatched questions ({sum(unmatched.values())}):")
    for question, count in unmatched.most_common(args.top):
        example = next(r for r in records if str(r.get("question", "")).strip().lower() == question)
        print(f"  x{count}  [{example.get('source')}/{example.get('layout')}] {example.get('question')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
