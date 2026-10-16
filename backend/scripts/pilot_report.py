"""Daily pilot report: what the pilot users asked, how the answers went, what the guards flagged, what it cost.

    python scripts/pilot_report.py                 # the last day
    python scripts/pilot_report.py --days 7        # the whole pilot week
    python scripts/pilot_report.py --days 1 --users 12,15

Reads the answer log (AGENT_ANSWER_LOG, one line per answer) and the audit log (AGENT_AUDIT_LOG, one line per review,
flagged file or result, unchecked claim, leaked marker, uncited law answer and knowledge-base refresh). Prints counts
only, and the questions that need a person to read them: answers that failed, ran out of time, were not grounded by the
verifier, or set off a guard. It never prints an answer or file text. See docs/VAT_AGENT_PILOT_CHECKLIST.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def read_lines(path: Optional[Path], since: dt.datetime, users: Optional[set]) -> List[Dict[str, Any]]:
    if path is None or not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                when = dt.datetime.fromisoformat(str(row.get("ts")))
            except (ValueError, TypeError):
                continue
            if when < since or (users and row.get("user_id") not in users):
                continue
            out.append(row)
    return out


def _tools(row: Dict[str, Any]) -> List[str]:
    return [c.get("name") for c in row.get("tool_calls") or [] if c.get("name")]


def summary(answers: Iterable[Dict[str, Any]], events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    answers, events = list(answers), list(events)
    costs = [float(a.get("cost_usd") or 0) for a in answers]
    seconds = sorted(float(a.get("elapsed_s") or 0) for a in answers if a.get("elapsed_s") is not None)
    tools = Counter(t for a in answers for t in set(_tools(a)))
    need_reading = []
    for a in answers:
        why = []
        if a.get("status") not in (None, "ok", "success"):
            why.append(f"status {a.get('status')}")
        if (a.get("verification") or {}).get("grounded") is False:
            why.append("figures not grounded")
        if any(c.get("error") for c in a.get("tool_calls") or []):
            why.append("a tool error")
        if why:
            need_reading.append({"ts": a.get("ts"), "user_id": a.get("user_id"), "question": a.get("question"),
                                 "why": ", ".join(why)})
    guard_kinds = ("injection_in_file", "injection_in_results", "unchecked_books_claim", "canary_hit", "law_answer_citations")
    return {
        "answers": len(answers),
        "users": len({a.get("user_id") for a in answers}),
        "with_files": sum(1 for a in answers if a.get("documents")),
        "routes": dict(Counter(a.get("route") or "?" for a in answers)),
        "tools_used": dict(tools.most_common()),
        "cost_usd": {"total": round(sum(costs), 3), "median": round(statistics.median(costs), 4) if costs else 0},
        "seconds": {"median": round(statistics.median(seconds), 1) if seconds else None,
                    "p95": seconds[min(len(seconds) - 1, int(len(seconds) * 0.95))] if seconds else None},
        "not_grounded": sum(1 for a in answers if (a.get("verification") or {}).get("grounded") is False),
        "vat_reviews": sum(1 for e in events if e.get("kind") == "vat_review"),
        "guard_events": dict(Counter(e.get("kind") for e in events if e.get("kind") in guard_kinds)),
        "knowledge_base_refreshes": [{"ts": e.get("ts"), "ok": e.get("ok"), "new_documents": e.get("new_documents")}
                                     for e in events if e.get("kind") == "vat_kb_refresh"],
        "need_reading": need_reading[:50],
    }


def main() -> None:
    from gemini_brain.agent import answer_log, audit
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--users", help="comma-separated user ids (default: everyone)")
    args = ap.parse_args()
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    users = {int(u) for u in args.users.split(",")} if args.users else None
    report = summary(read_lines(answer_log.log_path(), since, users), read_lines(audit.log_path(), since, users))
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
