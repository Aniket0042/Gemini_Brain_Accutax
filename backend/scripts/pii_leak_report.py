"""
pii_leak_report.py — Summarise the PII sentinel log (PII_SENTINEL=report): what personal data reaches the models.

    python scripts/pii_leak_report.py logs/pii_sentinel.jsonl                 # everything in the file
    python scripts/pii_leak_report.py logs/pii_sentinel.jsonl --days 1        # the last day
    python scripts/pii_leak_report.py logs/pii_sentinel.jsonl --out leak.md --json leak.json

Prints, as Markdown: how many model calls carried personal data, by call site, by kind and by where in the
payload (system prompt, user text, earlier answers, tool results, tool inputs), the free-text fields and files
that could not be checked, masked samples, and what the scan cost. The log holds no raw values, and neither
does this report. Distinct counts hold within one server process (the fingerprint salt is per process).
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Kinds that are personal or customer data. FREE_TEXT is counted apart: it may hold names, not always.
KIND_ORDER = ["PARTY_NAME", "ORG_NAME", "TRN", "EMAIL", "PHONE", "EMIRATES_ID", "IBAN", "CARD", "ADDRESS"]
WHERE_ORDER = ["system", "user_text", "assistant_text", "tool_result", "tool_input"]
WHERE_LABEL = {
    "system": "System prompt",
    "user_text": "User text (question, history, file block)",
    "assistant_text": "Earlier AI answers",
    "tool_result": "Tool results (books)",
    "tool_input": "Tool inputs",
}


def load(path: Path, days: Optional[float] = None) -> List[Dict[str, Any]]:
    since = None
    if days:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    out = []
    for name in sorted(path.parent.glob(path.name + "*"), reverse=True):  # rotated files first, oldest first
        if name != path and not name.name[len(path.name):].lstrip(".").isdigit():
            continue
        for line in name.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if since and datetime.datetime.fromisoformat(entry["ts"]) < since:
                continue
            out.append(entry)
    out.sort(key=lambda e: e.get("ts", ""))
    return out


def pct(values: List[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0


def summarise(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_site: Dict[str, Dict[str, Any]] = {}
    by_kind = {k: {"occurrences": 0, "calls": 0, "distinct": set()} for k in KIND_ORDER}
    by_where: Dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    samples: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    sessions = set()
    for e in entries:
        site = e.get("purpose") or e.get("label") or "unknown"
        s = by_site.setdefault(site, {"calls": 0, "with_pii": 0, "values": 0, "files": 0, "file_bytes": 0,
                                      "free_text_fields": 0})
        found = {k: v for k, v in (e.get("found") or {}).items() if k in by_kind}
        s["calls"] += 1
        s["with_pii"] += 1 if found else 0
        s["values"] += sum(found.values())
        s["files"] += int(e.get("files") or 0)
        s["file_bytes"] += int(e.get("file_bytes") or 0)
        s["free_text_fields"] += int(e.get("free_text_fields") or 0)
        if e.get("session_id"):
            sessions.add(e["session_id"])
        for k, n in found.items():
            by_kind[k]["occurrences"] += n
            by_kind[k]["calls"] += 1
            by_kind[k]["distinct"].update((e.get("fingerprints") or {}).get(k) or [])
        for where, counts in (e.get("where") or {}).items():
            for k, n in counts.items():
                if k in by_kind:
                    by_where[where][k] += n
        for sample in e.get("samples") or []:
            k = sample.get("kind")
            if k in by_kind and len(samples[k]) < 5 and all(x["fp"] != sample.get("fp") for x in samples[k]):
                samples[k].append({**sample, "site": site})
    scan_ms = [float(e.get("scan_ms") or 0) for e in entries]
    return {
        "calls": len(entries),
        "calls_with_pii": sum(1 for e in entries if any(k in by_kind for k in (e.get("found") or {}))),
        "sessions": len(sessions),
        "from": entries[0]["ts"] if entries else None,
        "to": entries[-1]["ts"] if entries else None,
        "by_site": by_site,
        "by_kind": {k: {"occurrences": v["occurrences"], "calls": v["calls"], "distinct": len(v["distinct"])}
                    for k, v in by_kind.items() if v["occurrences"]},
        "by_where": {w: dict(c) for w, c in by_where.items()},
        "samples": dict(samples),
        "scan_ms": {"median": pct(scan_ms, 0.5), "p95": pct(scan_ms, 0.95), "max": max(scan_ms) if scan_ms else 0},
    }


def markdown(s: Dict[str, Any]) -> str:
    if not s["calls"]:
        return "No model calls in the sentinel log for this period."
    out = ["# PII leak report", ""]
    share = 100.0 * s["calls_with_pii"] / s["calls"]
    out.append(f"**{s['calls_with_pii']} of {s['calls']} model calls ({share:.0f}%) carried personal or customer data.** "
               f"Chats: {s['sessions']}. Period: {s['from']} to {s['to']}.")
    out.append("")
    out.append("Counts are occurrences in what was sent. The agent re-sends the conversation on every step, so one "
               "value is counted once per call it rides in. *Distinct* counts each value once.")
    out += ["", "## By call site", "",
            "| Call site | Calls | With PII | Values sent | Free-text fields | Files (size) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for site, v in sorted(s["by_site"].items(), key=lambda kv: -kv[1]["values"]):
        files = f"{v['files']} ({v['file_bytes'] / 1024:.0f} KB)" if v["files"] else "0"
        out.append(f"| {site} | {v['calls']} | {v['with_pii']} | {v['values']} | {v['free_text_fields']} | {files} |")
    out += ["", "## By kind", "", "| Kind | Occurrences | Distinct values | Calls |", "| --- | ---: | ---: | ---: |"]
    for k in KIND_ORDER:
        v = s["by_kind"].get(k)
        if v:
            out.append(f"| {k} | {v['occurrences']} | {v['distinct']} | {v['calls']} |")
    kinds = [k for k in KIND_ORDER if k in s["by_kind"]]
    if kinds:
        out += ["", "## Where in the payload", "", "| Where | " + " | ".join(kinds) + " |",
                "| --- | " + " | ".join("---:" for _ in kinds) + " |"]
        for w in WHERE_ORDER + sorted(set(s["by_where"]) - set(WHERE_ORDER)):
            if w in s["by_where"]:
                out.append(f"| {WHERE_LABEL.get(w, w)} | " + " | ".join(str(s["by_where"][w].get(k, 0)) for k in kinds) + " |")
    if s["samples"]:
        out += ["", "## Masked samples", "", "| Kind | Where | Field | Preview | Call site |", "| --- | --- | --- | --- | --- |"]
        for k in KIND_ORDER:
            for x in s["samples"].get(k, []):
                out.append(f"| {k} | {WHERE_LABEL.get(x.get('where'), x.get('where'))} | {x.get('key', '')} | "
                           f"`{x.get('preview', '')}` | {x.get('site', '')} |")
    m = s["scan_ms"]
    out += ["", f"Scan cost per call: median {m['median']:.1f} ms, p95 {m['p95']:.1f} ms, max {m['max']:.1f} ms."]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("--days", type=float, default=None)
    parser.add_argument("--out", type=Path, default=None, help="Write the Markdown here instead of printing it")
    parser.add_argument("--json", type=Path, default=None, help="Also write the summary as JSON")
    args = parser.parse_args()
    summary = summarise(load(args.log, args.days))
    text = markdown(summary)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
