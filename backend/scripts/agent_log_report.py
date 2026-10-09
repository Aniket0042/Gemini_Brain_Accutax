"""
agent_log_report.py — Summarise the agent answer log (AGENT_ANSWER_LOG).

    python scripts/agent_log_report.py logs/agent_answers.jsonl            # everything in the file
    python scripts/agent_log_report.py logs/agent_answers.jsonl --days 7   # the last 7 days

Prints volume, status and routes, latency (median and p95), tokens and cost,
the figure check, tool failures, and the answers that need a look: failed or
timed-out answers, unverified figures, and the slowest questions.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List


def load(path: Path, days: float | None) -> List[Dict[str, Any]]:
    since = None
    if days:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if since and datetime.datetime.fromisoformat(entry["ts"]) < since:
            continue
        out.append(entry)
    return out


def pct(values: List[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0


def report(entries: List[Dict[str, Any]], show: int = 10) -> str:
    if not entries:
        return "No answers in the log for this period."
    lines = []
    n = len(entries)
    lines.append(f"Answers: {n}   users: {len({e.get('user_id') for e in entries})}   "
                 f"from {entries[0]['ts']} to {entries[-1]['ts']}")
    lines.append("Status: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(e.get("status") for e in entries).most_common()))
    lines.append("Route: " + ", ".join(f"{k} {v}" for k, v in collections.Counter(e.get("route") for e in entries).most_common()))

    times = [float(e["elapsed_s"]) for e in entries if e.get("elapsed_s") is not None]
    if times:
        lines.append(f"Time: median {statistics.median(times):.1f} s, p95 {pct(times, 0.95):.1f} s, max {max(times):.1f} s")
    cost = sum(float(e.get("cost_usd") or 0) for e in entries)
    lines.append(f"Cost: ${cost:.2f} total, ${cost / n:.4f} per answer; output tokens median "
                 f"{statistics.median([int(e.get('output_tokens') or 0) for e in entries]):.0f}")

    checked = [e for e in entries if e.get("verification")]
    ungrounded = [e for e in checked if e["verification"].get("grounded") is False]
    lines.append(f"Figure check: {len(checked)} answers checked, {len(ungrounded)} with unverified figures")

    calls = [c for e in entries for c in e.get("tool_calls") or []]
    failed = [c for c in calls if not c.get("ok")]
    by_tool = collections.Counter(c.get("name") for c in calls)
    lines.append("Tools: " + ", ".join(f"{k} {v}" for k, v in by_tool.most_common())
                 + f"   failed {len(failed)}")
    for error, count in collections.Counter(str(c.get("error"))[:90] for c in failed).most_common(5):
        lines.append(f"  {count} x {error}")
    views = collections.Counter((c.get("input") or {}).get("view") or (c.get("input") or {}).get("type")
                                for c in calls if c.get("ok"))
    lines.append("Views and list types used: " + ", ".join(f"{k} {v}" for k, v in views.most_common(12)))

    def section(title: str, rows: List[Dict[str, Any]], extra) -> None:
        lines.append("")
        lines.append(f"{title} ({len(rows)})")
        for e in rows[:show]:
            lines.append(f"  {e['ts']}  user {e.get('user_id')}  {extra(e)}  {e.get('question', '')[:110]}")

    section("Failed, timed out or partial", [e for e in entries if e.get("status") != "ok"],
            lambda e: f"{e.get('status')}/{e.get('notice')}")
    section("Unverified figures", ungrounded, lambda e: f"unmatched {e['verification'].get('unmatched')}")
    section("Slowest", sorted(entries, key=lambda e: -(e.get("elapsed_s") or 0)),
            lambda e: f"{e.get('elapsed_s')} s, {len(e.get('tool_calls') or [])} tools")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("--days", type=float, default=None, help="only answers from the last N days")
    parser.add_argument("--show", type=int, default=10, help="questions listed per section")
    args = parser.parse_args()
    print(report(load(args.log, args.days), args.show))


if __name__ == "__main__":
    main()
