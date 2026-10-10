"""
replay_real_questions.py — Replay real chat questions through the current answer path.

Runs every case in tests/data/real_questions.json through the same entry points
the /query route uses (run_multi_org for several organizations, the runner for
one), against the configured database, Accutax API and Bedrock. With
METRICS_BACKEND=shadow the multi-org metric answers are also compared with Cube
in the shadow log, as for real traffic.

Follow-up cases replay inside the conversation of the case they follow. Thread
memory is kept in this process only: nothing is written to the chat tables.

For every case the reference Cube queries in "expect" are run directly (ledger
basis in "ref", the document basis the current path uses in "alt"), and the
answer is checked for those figures. The figure check is a screen, not the
grade: answers are read and graded by hand from the Markdown sheet.

Costs real Bedrock calls: about one to four per case.

Usage (from backend/, on a host that reaches Cube):
  .venv/bin/python scripts/eval/replay_real_questions.py
  .venv/bin/python scripts/eval/replay_real_questions.py --path agent
  .venv/bin/python scripts/eval/replay_real_questions.py --only r080,r081 --out /tmp/replay
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for path in (_BACKEND_ROOT / "src", _BACKEND_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gemini_brain.config.settings import settings  # noqa: E402
from gemini_brain.memory import conversation_window, session_memory  # noqa: E402
from gemini_brain.orchestrator import gemini_brain_runner, multi_org  # noqa: E402
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner  # noqa: E402
from gemini_brain.orchestrator.multi_org import run_multi_org  # noqa: E402
from gemini_brain.orchestrator.multi_org_plan import plan_query  # noqa: E402
from gemini_brain.semantic import cube_client, periods  # noqa: E402
from gemini_brain.db.connection import get_connection  # noqa: E402

CASES_FILE = _BACKEND_ROOT / "tests" / "data" / "real_questions.json"
CASE_TIMEOUT_SECONDS = 150
REFUSAL_WORDS = ("not available", "isn't available", "is not tracked", "not tracked", "don't have", "do not have",
                 "no data", "not recorded", "cannot", "can't", "unable", "not supported", "no figure", "not hold")


# ── Thread memory in this process only ───────────────────────────────────────

class _MemoryStore:
    """Stands in for the session_memory functions the answer path calls."""

    def __init__(self) -> None:
        self.messages: Dict[str, List[Dict[str, Any]]] = {}
        self.state: Dict[str, Dict[str, Any]] = {}
        self.scope: Dict[str, List[int]] = {}

    def ensure_session(self, session_id, user_id, organization_id=None, db_name="", organization_ids=None):
        self.messages.setdefault(session_id, [])
        self.scope.setdefault(session_id, sorted(organization_ids or ([organization_id] if organization_id else [])))
        return True

    def save_message_by_session(self, session_id, role, content, db_name="", blocks=None):
        self.messages.setdefault(session_id, []).append({"role": role, "content": content})

    def get_history_by_session(self, session_id, limit=10, db_name=""):
        return [dict(m) for m in self.messages.get(session_id, [])[-limit:]]

    def count_messages_by_session(self, session_id, db_name=""):
        return len(self.messages.get(session_id, []))

    def get_first_user_message_by_session(self, session_id, db_name=""):
        return next((m["content"] for m in self.messages.get(session_id, []) if m["role"] == "user"), "")

    def get_state_by_session(self, session_id, db_name=""):
        return dict(self.state.get(session_id, {}))

    def update_state_by_session(self, session_id, new_state, db_name=""):
        self.state[session_id] = dict(new_state or {})

    def get_session_record(self, session_id, db_name=""):
        if session_id not in self.messages:
            return None
        return {"id": session_id, "user_id": 0, "organization_id": None, "name": None,
                "conversation_state": self.state.get(session_id, {}), "organization_ids": self.scope.get(session_id, [])}

    @staticmethod
    def update_last_assistant_blocks(*args, **kwargs):
        return None

    @staticmethod
    def maybe_auto_title(*args, **kwargs):
        return None

    @staticmethod
    def verify_session_ownership(*args, **kwargs):
        return True


def install_memory_store() -> _MemoryStore:
    store = _MemoryStore()
    from gemini_brain.memory import context_window, state_extractor
    modules = (session_memory, conversation_window, state_extractor, context_window, gemini_brain_runner, multi_org)
    names = ("ensure_session", "save_message_by_session", "get_history_by_session", "count_messages_by_session",
             "get_first_user_message_by_session", "get_state_by_session", "update_state_by_session",
             "get_session_record", "update_last_assistant_blocks", "maybe_auto_title", "verify_session_ownership")
    for module in modules:
        for name in names:
            if hasattr(module, name):
                setattr(module, name, getattr(store, name))
    return store


# ── Reference figures from Cube ──────────────────────────────────────────────

_TIME = {"pnl": "pnl.transaction_date", "sales": "sales.document_date", "purchases": "purchases.document_date",
         "vat": "vat.document_date", "balance_sheet": "balance_sheet.transaction_date"}


#: Relative dates in reference specs, resolved on the day of the run: {today}, {today+3},
#: {week_start} (Monday), {week_end} (Sunday), each with an optional +/- day offset.
_DATE_TOKEN = re.compile(r"^\{(today|week_start|week_end)([+-]\d+)?\}$")


def _resolve(value: Any, today: datetime.date) -> str:
    m = _DATE_TOKEN.match(str(value))
    if not m:
        return str(value)
    base = {"today": today,
            "week_start": today - datetime.timedelta(days=today.weekday()),
            "week_end": today + datetime.timedelta(days=6 - today.weekday())}[m.group(1)]
    return (base + datetime.timedelta(days=int(m.group(2) or 0))).isoformat()


def _date(value: str, today: datetime.date) -> datetime.date:
    return today if value == "today" else datetime.date.fromisoformat(_resolve(value, today))


def _range(period: Any, today: datetime.date) -> Tuple[datetime.date, datetime.date]:
    if isinstance(period, list):
        return _date(period[0], today), _date(period[1], today)
    if period == "same_period_last_year":
        return datetime.date(today.year - 1, 1, 1), today.replace(year=today.year - 1)
    return periods.resolve({"preset": period or "ytd"}, today)


def cube_query(spec: Dict[str, Any], today: datetime.date) -> Dict[str, Any]:
    """The Cube query for a reference spec. Written apart from query_metrics on purpose."""
    v = spec["view"]
    dims = [f"{v}.organization_id", f"{v}.organization_name", f"{v}.currency"]
    if v == "balance_sheet":
        dims.append("balance_sheet.account_currency")
    dims += [f"{v}.{d}" for d in spec.get("group_by", []) if f"{v}.{d}" not in dims]
    query: Dict[str, Any] = {
        "measures": [f"{v}.{m}" for m in spec["measures"]],
        "dimensions": dims,
        "filters": [{"member": f"{v}.{m}", "operator": op, "values": [_resolve(x, today) for x in vals]}
                    for m, op, vals in spec.get("filters", [])],
        "limit": spec.get("limit", 500),
    }
    if v == "balance_sheet":
        as_of = _date(spec.get("as_of", "today"), today)
        query["timeDimensions"] = [{"dimension": _TIME[v], "dateRange": ["1900-01-01", as_of.isoformat()]}]
    elif v in _TIME:
        start, end = _range(spec.get("period"), today)
        td: Dict[str, Any] = {"dimension": _TIME[v], "dateRange": [start.isoformat(), end.isoformat()]}
        if spec.get("granularity"):
            td["granularity"] = spec["granularity"]
        query["timeDimensions"] = [td]
    if spec.get("order"):
        query["order"] = {f"{v}.{spec['order'][0]}": spec["order"][1]}
    return query


def reference(spec: Dict[str, Any], orgs: List[int], today: datetime.date) -> Dict[str, Any]:
    query = cube_query(spec, today)
    scope = spec.get("orgs") or orgs
    try:
        result = cube_client.load(query, organization_ids=scope, subject="replay-real-questions",
                                  deadline=time.monotonic() + 60)
        rows = [{k.split(".", 1)[1]: (str(val) if val is not None else None) for k, val in r.items()} for r in result.rows]
        return {"spec": spec, "rows": rows}
    except Exception as e:  # one bad reference must not stop the replay
        return {"spec": spec, "error": f"{type(e).__name__}: {e}"}


# ── Figure screen ────────────────────────────────────────────────────────────

_NUMBER = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*(billion|bn|million|mn|m|thousand|k)?(?![a-z])", re.I)
_SCALE = {"billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def numbers_in(text: str) -> List[Tuple[float, bool]]:
    """(absolute value, abbreviated) for every number in the text."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        try:
            value = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        scale = _SCALE.get((m.group(2) or "").lower())
        out.append((abs(value * scale) if scale else abs(value), bool(scale)))
    return out


def _found(target: float, pct: bool, found: List[Tuple[float, bool]]) -> bool:
    t = abs(target)
    for value, abbreviated in found:
        if pct:
            if abs(value - t) <= 0.15:
                return True
        elif abs(value - t) <= (0.015 * t if abbreviated else max(1.0, 0.002 * t)):
            return True
    return False


def figure_screen(refs: List[Dict[str, Any]], answer: str) -> Optional[Dict[str, Any]]:
    """Share of the reference figures (largest 25 per query, zeros skipped) that appear in the answer."""
    found = numbers_in(answer)
    hits = total = 0
    for ref in refs:
        targets = []
        for row in ref.get("rows") or []:
            for m in ref["spec"]["measures"]:
                raw = row.get(m)
                if raw is None:
                    continue
                value = float(raw)
                if abs(value) >= 0.01:
                    targets.append((value, m.endswith("_pct") or m == "current_ratio"))
        targets.sort(key=lambda t: -abs(t[0]))
        for value, pct in targets[:25]:
            total += 1
            hits += _found(value, pct, found)
    return {"hits": hits, "total": total, "share": round(hits / total, 2)} if total else None


# ── Replay ───────────────────────────────────────────────────────────────────

#: "current" replays the answer path users get today; "agent" the Phase 2 agent.
_PATH = "current"
_STORE: Optional[_MemoryStore] = None


def ask_agent(question: str, orgs: List[int], meta: Dict[int, Dict[str, Any]], session_id: Optional[str]) -> Dict[str, Any]:
    from gemini_brain.agent.loop import run_agent
    history = _STORE.get_history_by_session(session_id, limit=6) if session_id and _STORE else []
    result = run_agent(question, orgs, {o: meta[o] for o in orgs}, history=history, subject="replay-agent")
    if session_id and _STORE:
        _STORE.save_message_by_session(session_id, "user", question)
        _STORE.save_message_by_session(session_id, "assistant", result.answer)
    return {"answer": result.answer, "status": result.status, "token_usage": result.usage,
            "routing_info": {"path": "agent"}, "tool_calls": result.tool_calls, "verification": result.verification}


def ask(question: str, orgs: List[int], meta: Dict[int, Dict[str, Any]], session_id: Optional[str]) -> Dict[str, Any]:
    if _PATH == "agent":
        return ask_agent(question, orgs, meta, session_id)
    if len(orgs) > 1:
        return run_multi_org(
            question, orgs, {o: meta[o] for o in orgs},
            runner_factory=GeminiBrainRunner,
            run_kwargs={"allowed_org_ids": orgs, "user_id": 0, "db_name": "", "use_api": True, "auth_token": "",
                        "model": "auto", "effort": None, "ui_context": None, "brief": False,
                        "selected_model_key": None, "session_id": session_id},
            planner=plan_query,
        )
    return GeminiBrainRunner().run(
        query=question, organization_id=orgs[0], db_name="", use_api=True, user_id=0, session_id=None,
        selected_model_key=None, allowed_org_ids=orgs, auth_token="", model="auto", effort=None,
        ui_context=None, brief=False,
    )


def ask_with_timeout(*args: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str], float]:
    box: Dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = ask(*args)
        except Exception as e:  # noqa: BLE001
            box["error"] = f"{type(e).__name__}: {e}"

    t0 = time.time()
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(CASE_TIMEOUT_SECONDS)
    elapsed = round(time.time() - t0, 1)
    if worker.is_alive():
        return None, f"timeout after {CASE_TIMEOUT_SECONDS}s", elapsed
    return box.get("result"), box.get("error"), elapsed


def _table_rows(result: Dict[str, Any]) -> List[Any]:
    """Rows of the table blocks shown under the answer."""
    rows: List[Any] = []
    for block in result.get("blocks") or []:
        if isinstance(block, dict):
            for key in ("rows", "data", "items"):
                if isinstance(block.get(key), list):
                    rows += block[key]
    return rows


def run_case(case: Dict[str, Any], meta: Dict[int, Dict[str, Any]], sessions: Dict[str, str],
             by_id: Dict[str, Dict[str, Any]], today: datetime.date) -> Dict[str, Any]:
    orgs = case["orgs"]
    session_id = None
    if len(orgs) > 1:
        context = case.get("context") or []
        if context and context[-1] in sessions:
            session_id = sessions[context[-1]]
        else:
            session_id = str(uuid.uuid4())
            for cid in context:  # earlier turns not replayed yet: replay them into this thread first
                ask_with_timeout(by_id[cid]["question"], orgs, meta, session_id)
        sessions[case["id"]] = session_id

    result, error, elapsed = ask_with_timeout(case["question"], orgs, meta, session_id)
    result = result or {}
    answer = result.get("answer") or ""
    usage = result.get("token_usage") or {}
    routing = result.get("routing_info") or {}
    expect = case.get("expect") or {}

    # The UI shows the answer text and its table blocks; the figure screen reads both.
    tables = _table_rows(result)
    shown = answer + "\n" + json.dumps(tables, default=str)
    refs = [reference(s, orgs, today) for s in expect.get("ref", [])]
    alts = [reference(s, orgs, today) for s in expect.get("alt", [])]
    checks: Dict[str, Any] = {
        "status": result.get("status") or ("error" if error else None),
        "error": error,
        "figures_ledger": figure_screen(refs, shown),
        "figures_documents": figure_screen(alts, shown),
    }
    if expect.get("must_contain"):
        flat = answer.replace(" ", "")
        checks["must_contain"] = {s: s.replace(" ", "") in flat for s in expect["must_contain"]}
    if expect.get("must_refuse"):
        checks["says_unavailable"] = any(w in answer.lower() for w in REFUSAL_WORDS)
    if expect.get("kb"):
        checks["cites"] = bool(re.search(r"\[\d+\]", answer))
    return {
        "id": case["id"], "kind": case["kind"], "orgs": orgs, "question": case["question"],
        "context": case.get("context") or [], "note": expect.get("note"),
        "seconds": elapsed, "llm_calls": usage.get("llm_calls"), "cost_usd": usage.get("cost_usd"),
        "path": routing.get("path"), "layout": routing.get("layout"),
        "checks": checks, "answer": answer, "tables": tables, "old_answer": case.get("old_answer"),
        "tool_calls": result.get("tool_calls"), "verification": result.get("verification"),
        "references": refs, "alternatives": alts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=str(CASES_FILE))
    parser.add_argument("--only", default="", help="Comma-separated case ids")
    parser.add_argument("--out", default=str(Path(__file__).parent), help="Directory for the report")
    parser.add_argument("--path", choices=("current", "agent"), default="current",
                        help="Answer path to replay: the current one, or the Phase 2 agent")
    args = parser.parse_args()
    global _PATH, _STORE
    _PATH = args.path

    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in spec["cases"]}
    wanted = {i for i in args.only.split(",") if i}
    cases = [c for c in spec["cases"] if not wanted or c["id"] in wanted]

    _STORE = install_memory_store()
    today = periods.today_in(settings.report_timezone)
    all_orgs = sorted({o for c in cases for o in c["orgs"]})
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, name, currency FROM organizations WHERE id = ANY(%s)", (all_orgs,))
    meta = {int(i): {"name": n, "currency": c or ""} for i, n, c in cur.fetchall()}
    conn.close()

    print(f"{len(cases)} cases · path {_PATH} · today {today} · METRICS_BACKEND={settings.metrics_backend}", flush=True)
    sessions: Dict[str, str] = {}
    report = []
    for case in cases:
        outcome = run_case(case, meta, sessions, by_id, today)
        report.append(outcome)
        c = outcome["checks"]
        fig = c["figures_ledger"] or c["figures_documents"]
        print(f"{case['id']} {case['kind']:12} {outcome['seconds']:>6}s llm={outcome['llm_calls']} "
              f"status={c['status']} ledger={(c['figures_ledger'] or {}).get('share')} "
              f"docs={(c['figures_documents'] or {}).get('share')}{' ERR ' + c['error'] if c['error'] else ''}",
              flush=True)

    for t in threading.enumerate():  # let shadow comparisons finish writing
        if t is not threading.current_thread() and not t.daemon:
            t.join(40)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"replay_real_questions_{_PATH}_{stamp}.json"
    out.write_text(json.dumps({"today": str(today), "cases": report}, indent=1, default=str, ensure_ascii=False),
                   encoding="utf-8")
    out.with_suffix(".md").write_text(sheet(stamp, report), encoding="utf-8")
    cost = round(sum(r.get("cost_usd") or 0 for r in report), 3)
    print(f"\nReport: {out}\nSheet:  {out.with_suffix('.md')}\nEstimated cost ${cost}")
    return 0


def sheet(stamp: str, report: List[Dict[str, Any]]) -> str:
    times = sorted(r["seconds"] for r in report)
    p95 = times[min(len(times) - 1, int(0.95 * len(times)))] if times else None
    lines = [f"# Real-question replay — {stamp}", "", f"{len(report)} cases · p95 {p95}s", ""]
    for r in report:
        c = r["checks"]
        lines += [f"## {r['id']} · {r['kind']} · {r['seconds']}s · {r['path'] or ''}/{r['layout'] or ''}",
                  f"**Q:** {r['question']}" + (f"  (follows {', '.join(r['context'])})" if r["context"] else ""),
                  f"Checks: {json.dumps({k: v for k, v in c.items() if v is not None}, default=str)}"]
        if r.get("note"):
            lines.append(f"Expected: {r['note']}")
        for label, refs in (("Ledger", r["references"]), ("Documents", r["alternatives"])):
            for ref in refs:
                if ref.get("error"):
                    lines.append(f"{label} reference error: {ref['error']}")
                    continue
                shown = [{k: v for k, v in row.items() if k not in ("organization_id", "currency")} for row in ref["rows"][:12]]
                lines.append(f"{label} `{ref['spec']['view']}` {ref['spec']['measures']}: {json.dumps(shown, ensure_ascii=False)}")
        lines += ["", "**Answer:**", "", (r["answer"] or "(empty)").strip()]
        for call in r.get("tool_calls") or []:
            lines.append(f"Tool: {call.get('name')} ok={call.get('ok')} rows={call.get('rows')} {call.get('ms')}ms "
                         f"{json.dumps(call.get('input'), ensure_ascii=False)}" + (f" ERROR {call['error']}" if call.get("error") else ""))
        if r.get("tables"):
            lines += ["", "Table: " + json.dumps(r["tables"][:15], default=str, ensure_ascii=False)]
        lines += ["", "---", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
