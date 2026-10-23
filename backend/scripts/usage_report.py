"""
usage_report.py — Summarise the usage ledger (USAGE_LEDGER), to size the AI plans and caps.

    python scripts/usage_report.py logs/ai_usage.jsonl                  # everything, rotated files included
    python scripts/usage_report.py logs/ai_usage.jsonl --month 2026-11  # one calendar month (UTC)

Prints spend by kind, the cost of one question (median, p90, p95, p99, max), each owner's month (the bucket a
plan must cover), each user's busiest day (the daily cap) and the biggest owners and users. Credits: 1 = $0.001.
See docs/ORG_TOKEN_LIMITS_STRATEGY.md, rollout step 1.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


def files(path: Path) -> List[Path]:
    """The ledger and its rotated copies, oldest first."""
    rotated = sorted((p for p in path.parent.glob(path.name + ".*") if p.suffix.lstrip(".").isdigit()),
                     key=lambda p: -int(p.suffix.lstrip(".")))
    return rotated + ([path] if path.exists() else [])


def load(path: Path, month: str | None = None) -> List[Dict[str, Any]]:
    out = []
    for f in files(path):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if month and not str(entry.get("ts", "")).startswith(month):
                continue
            out.append(entry)
    return out


def pct(values: Iterable[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0


def spread(values: List[float]) -> str:
    if not values:
        return "none"
    return (f"n={len(values)}  median {pct(values, .5):.1f}  p90 {pct(values, .9):.1f}  p95 {pct(values, .95):.1f}"
            f"  p99 {pct(values, .99):.1f}  max {max(values):.1f}")


def summarise(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, Dict[str, float]] = collections.defaultdict(lambda: {"lines": 0, "credits": 0.0})
    owner_month: Dict[tuple, float] = collections.defaultdict(float)
    owner_users: Dict[str, set] = collections.defaultdict(set)
    user_day: Dict[tuple, float] = collections.defaultdict(float)
    user_total: Dict[str, float] = collections.defaultdict(float)
    questions: List[float] = []
    multi_owner = 0
    for e in entries:
        credits = float(e.get("credits") or 0)
        kind = e.get("kind") or "?"
        by_kind[kind]["lines"] += 1
        by_kind[kind]["credits"] += credits
        if kind == "agent_answer" and e.get("status") != "error":
            questions.append(credits)
        ts = str(e.get("ts", ""))
        charge = e.get("charge") or {}
        if len(charge) > 1:
            multi_owner += 1
        for owner, share in charge.items():
            owner_month[(owner, ts[:7])] += float(share or 0)
            if e.get("user_id") is not None:
                owner_users[owner].add(e["user_id"])
        if e.get("user_id") is not None:
            user_day[(str(e["user_id"]), ts[:10])] += credits
            user_total[str(e["user_id"])] += credits
    return {
        "lines": len(entries),
        "credits": round(sum(float(e.get("credits") or 0) for e in entries), 3),
        "cost_usd": round(sum(float(e.get("cost_usd") or 0) for e in entries), 4),
        "by_kind": {k: {"lines": int(v["lines"]), "credits": round(v["credits"], 3)} for k, v in sorted(by_kind.items())},
        "question": questions,
        "owner_month": dict(owner_month),
        "owner_users": {k: len(v) for k, v in owner_users.items()},
        "user_day": dict(user_day),
        "user_total": dict(user_total),
        "multi_owner_lines": multi_owner,
    }


def report(s: Dict[str, Any]) -> str:
    lines = [f"Ledger lines {s['lines']}   credits {s['credits']:.1f}   cost ${s['cost_usd']:.4f}", "", "By kind:"]
    for kind, v in s["by_kind"].items():
        lines.append(f"  {kind:<16} {v['lines']:>6} lines  {v['credits']:>10.1f} credits")
    lines += ["", "One question (agent_answer), credits:", "  " + spread(s["question"]),
              "", "Owner month (credits charged to an owner in a calendar month):",
              "  " + spread(list(s["owner_month"].values())),
              "", "User day (credits a user spent in one UTC day; sizes the daily cap):",
              "  " + spread(list(s["user_day"].values())),
              "", f"Lines charged to more than one owner: {s['multi_owner_lines']}", "", "Top owners:"]
    for (owner, month), credits in sorted(s["owner_month"].items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  owner {owner:<8} {month}  {credits:>9.1f} credits  {s['owner_users'].get(owner, 0)} users")
    lines += ["", "Top users:"]
    for user, credits in sorted(s["user_total"].items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  user {user:<8} {credits:>9.1f} credits")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--month", help="YYYY-MM, UTC")
    args = ap.parse_args()
    print(report(summarise(load(args.ledger, args.month))))


if __name__ == "__main__":
    main()
