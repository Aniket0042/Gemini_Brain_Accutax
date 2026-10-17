"""
pii_redteam.py — Push names and identifiers through the agent and check none reaches the model (PII plan).

    PII_MODE=on PII_SNAPSHOTS=on PII_SNAPSHOT_KEY=... PII_SNAPSHOT_DIR=/tmp/rt/snaps ARTIFACT_DIR=/tmp/rt/art \\
        python scripts/eval/pii_redteam.py --out /tmp/rt/out
    python scripts/eval/pii_redteam.py --only pr01,pr21 --out /tmp/rt/out

Runs tests/data/pii_redteam.json against the configured Cube and Bedrock (one to four model calls per case;
report cases also build the file, so the narrator runs). Every request is kept as a privacy snapshot, and the
check reads what was really sent, call by call:

  leaks     any of these found in what the model received: a value the request hid (the snapshot's vault), a
            contact name of the selected organizations (the dictionary), a value the user typed (the case's
            "pii"), or an identifier by pattern. "gap" cases (names not in the books) are expected to leak and
            are counted apart.
  answer    status ok, no placeholder left in the answer, none unresolved, the names the case expects
            ("answer_has"), and at least "min_names" known names in the answer or the file.

Writes a JSON report and a Markdown sheet to --out. Costs real Bedrock calls: about $2 for the whole set.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for path in (_BACKEND_ROOT / "src", _BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gemini_brain.privacy import policy, recognizers  # noqa: E402
from gemini_brain.privacy.dictionary import Dictionary  # noqa: E402
from gemini_brain.privacy.vault import TOKEN_RX  # noqa: E402

CASES_FILE = _BACKEND_ROOT / "tests" / "data" / "pii_redteam.json"


# ── Checks (pure: unit-tested) ───────────────────────────────────────────────

def flatten(system: Any, messages: Any) -> str:
    """Everything one model call carried, as text: the system prompt and every message, tool calls and results."""
    sys_text = system if isinstance(system, str) else json.dumps(system, ensure_ascii=False)
    return sys_text + "\n" + json.dumps(messages, ensure_ascii=False)


def _value_rx(value: str) -> re.Pattern:
    """A value in any spacing: '+971 50 123 4567' also matches '+971501234567' and '+971-50-123-4567'."""
    compact = re.sub(r"[\s\-]", "", value)
    body = r"[\s\-]?".join(re.escape(c) for c in compact)
    return re.compile(r"(?<![\w])" + body + r"(?![\w])", re.IGNORECASE)


def leaks(texts: Iterable[str], *, known: Optional[Dictionary] = None, values: Iterable[str] = (),
          typed: Iterable[str] = ()) -> List[Dict[str, str]]:
    """Personal data found in what was sent. Each finding: kind, the value (as found), and why it counts."""
    found: Dict[tuple, Dict[str, str]] = {}
    checks = [(v, "hidden value") for v in values if v and len(str(v)) >= 3] + [(v, "typed by user") for v in typed if v]
    rxs = [(_value_rx(str(v)), why) for v, why in checks]
    for text in texts:
        if not text:
            continue
        for rx, why in rxs:
            for m in rx.finditer(text):
                found.setdefault(("VALUE", m.group(0).lower()), {"kind": "VALUE", "value": m.group(0), "why": why})
        for span in (known.find(text) if known is not None else []):
            found.setdefault(("NAME", span.key), {"kind": span.kind, "value": span.text, "why": "known contact name"})
        for span in recognizers.find(text):
            if not policy.public_contact(span.kind, span.text):
                found.setdefault((span.kind, span.text.lower()), {"kind": span.kind, "value": span.text,
                                                                  "why": "identifier pattern"})
    return list(found.values())


def answer_checks(answer: str, case: Dict[str, Any], *, known: Optional[Dictionary] = None, status: str = "ok",
                  unresolved: int = 0, extra_text: str = "") -> Dict[str, Any]:
    """What the user got: answered, real values back in place, the expected names there."""
    shown = (answer or "") + "\n" + (extra_text or "")
    out: Dict[str, Any] = {"status_ok": status == "ok", "placeholders_left": TOKEN_RX.findall(shown),
                           "unresolved": unresolved}
    expected = case.get("answer_has") or []
    if expected:
        out["missing"] = [s for s in expected if not _value_rx(s).search(shown)]
    if case.get("min_names") is not None:
        names = {s.key for s in known.find(shown)} if known is not None else set()
        out["names"] = len(names)
        out["names_ok"] = len(names) >= int(case["min_names"])
    out["ok"] = (out["status_ok"] and not out["placeholders_left"] and not unresolved
                 and not out.get("missing") and out.get("names_ok", True))
    return out


# ── Running ─────────────────────────────────────────────────────────────────

def run_case(case: Dict[str, Any], history: List[Dict[str, str]]) -> Dict[str, Any]:
    from gemini_brain.agent import preview
    from gemini_brain.agent.loop import run_agent
    from gemini_brain.privacy import dictionary, snapshots
    from gemini_brain.semantic import org_directory

    orgs = case["orgs"]
    meta = org_directory.lookup(orgs, subject="pii-redteam")
    delivery = preview.delivery_for(case["question"], preview.Thread()) if case.get("file") else None
    t0 = time.time()
    result = run_agent(case["question"], orgs, meta, history=history, subject="pii-redteam", user_id=0,
                       session_id=f"redteam-{case['id']}",
                       attachment=preview.attachment(case["question"], delivery) if delivery else None)
    blocks = []
    if case.get("file"):
        blocks = preview._files(case["question"], result, orgs, meta, user_id=0, session_id=None,
                                answer_text=result.answer, delivery=delivery)
    seconds = round(time.time() - t0, 1)
    known = dictionary.for_orgs(orgs, subject="pii-redteam", fetch=True,
                                org_names=[(meta.get(o) or {}).get("name") for o in orgs])
    snap = snapshots.load(result.privacy.get("snapshot", "")) if result.privacy.get("snapshot") else None
    calls = (snap or {}).get("calls") or []
    sent = [flatten(c["sent"].get("system", ""), c["sent"].get("messages", [])) + "\n" + str(c.get("reply_model") or "")
            for c in calls]
    found = leaks(sent, known=known, values=((snap or {}).get("vault") or {}).values(), typed=case.get("pii") or [])
    checks = answer_checks(result.answer, case, known=known, status=result.status,
                           unresolved=int(result.privacy.get("unresolved") or 0),
                           extra_text=json.dumps(blocks, ensure_ascii=False, default=str) if blocks else "")
    return {
        "id": case["id"], "group": case["group"], "gap": bool(case.get("gap")), "orgs": orgs,
        "question": case["question"], "answer": result.answer, "status": result.status, "seconds": seconds,
        "cost_usd": (result.usage or {}).get("cost_usd"), "privacy": result.privacy,
        "calls": [c.get("purpose") for c in calls], "snapshot_found": snap is not None,
        "leaks": found, "checks": checks, "tools": [c.get("name") for c in result.tool_calls],
        "verification": {k: (result.verification or {}).get(k) for k in ("grounded", "checked", "matched")},
    }


def sheet(stamp: str, rows: List[Dict[str, Any]]) -> str:
    real = [r for r in rows if not r["gap"]]
    leaked = [r for r in real if r["leaks"] or not r["snapshot_found"]]
    gap_leaked = [r for r in rows if r["gap"] and r["leaks"]]
    bad_answers = [r for r in rows if not r["checks"]["ok"]]
    cost = sum(float(r.get("cost_usd") or 0) for r in rows)
    out = [f"# PII red-team run {stamp}", "",
           f"**Leaks: {len(leaked)} of {len(real)} cases sent personal data to the model** "
           f"(known-gap cases leaking as expected: {len(gap_leaked)} of {sum(r['gap'] for r in rows)}). "
           f"Answer checks failed: {len(bad_answers)} of {len(rows)}. Cost ${cost:.2f}.", "",
           "| Case | Group | Calls | Hidden | Leaks | Answer | Seconds |", "| --- | --- | --- | ---: | --- | --- | ---: |"]
    for r in rows:
        hidden = sum((r["privacy"].get("hidden") or {}).values())
        leak_text = "none" if not r["leaks"] else "; ".join(f"{x['kind']} ({x['why']})" for x in r["leaks"][:4])
        if not r["snapshot_found"]:
            leak_text = "NO SNAPSHOT"
        c = r["checks"]
        problems = [p for p, bad in (("not ok", not c["status_ok"]), (f"placeholders {c['placeholders_left']}",
                    c["placeholders_left"]), (f"missing {c.get('missing')}", c.get("missing")),
                    (f"names {c.get('names')}", c.get("names_ok") is False)) if bad]
        out.append(f"| {r['id']} | {r['group']}{' (gap)' if r['gap'] else ''} | {'+'.join(r['calls'])} | {hidden} | "
                   f"{leak_text} | {'ok' if c['ok'] else ', '.join(problems)} | {r['seconds']} |")
    out += ["", "## Answers", ""]
    for r in rows:
        out += [f"### {r['id']} · {r['question']}", "", (r["answer"] or "")[:1500], ""]
        if r["leaks"]:
            out += ["Leaks: " + json.dumps(r["leaks"], ensure_ascii=False), ""]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=str(CASES_FILE))
    parser.add_argument("--only", default="")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()
    from gemini_brain.config.settings import settings
    if settings.pii_mode != "on" or settings.pii_snapshots != "on" or not settings.pii_snapshot_key:
        print("Set PII_MODE=on, PII_SNAPSHOTS=on and PII_SNAPSHOT_KEY: the check reads each request's snapshot.")
        return 2
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))["cases"]
    by_id = {c["id"]: c for c in cases}
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    answers: Dict[str, str] = {}
    rows = []
    for case in cases:
        if only and case["id"] not in only:
            continue
        history: List[Dict[str, str]] = []
        for cid in case.get("context") or []:
            if cid not in answers:  # earlier turn not run yet: run it into this thread first
                answers[cid] = run_case(by_id[cid], list(history))["answer"]
            history += [{"role": "user", "content": by_id[cid]["question"]}, {"role": "assistant", "content": answers[cid]}]
        try:
            row = run_case(case, history)
        except Exception as e:  # noqa: BLE001 - one failing case is a finding, not the end of the run
            row = {"id": case["id"], "group": case["group"], "gap": bool(case.get("gap")), "orgs": case["orgs"],
                   "question": case["question"], "answer": f"ERROR {type(e).__name__}: {e}", "status": "error",
                   "seconds": 0, "cost_usd": 0, "privacy": {}, "calls": [], "snapshot_found": False, "leaks": [],
                   "checks": {"ok": False, "status_ok": False, "placeholders_left": [], "unresolved": 0},
                   "tools": [], "verification": {}}
        answers[case["id"]] = row["answer"]
        rows.append(row)
        print(f"{row['id']:5} {row['group']:13} leaks={len(row['leaks'])} answer={'ok' if row['checks']['ok'] else 'CHECK'} "
              f"{row['seconds']}s", flush=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"pii_redteam_{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str),
                                                   encoding="utf-8")
    (out / f"pii_redteam_{stamp}.md").write_text(sheet(stamp, rows), encoding="utf-8")
    print(f"Report: {out / f'pii_redteam_{stamp}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
