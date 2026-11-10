"""
replay_mcp.py — Replay the real questions through the MCP connector, as ChatGPT or Claude would ask them.

Each case goes through the real MCP server (gemini_brain.mcp_server) over Streamable HTTP with the MCP SDK
client: one ask_accutax call with the case's organizations in organization_ids, and the tool's text is the
answer, exactly what the host model reads. The server runs in this process on 127.0.0.1 (MCP_EVAL_PORT,
8013), against the live AI API (MCP_UPSTREAM_URL), so /query, its org checks, usage limits and the
external view all run as they do for the connector.

The bearer is an Accutax AI token for --user (pasted-token mode, on for this server only). The cases,
the Cube reference figures, the figure screen and the Markdown sheet are replay_real_questions.py's, so
the result compares with the agent's own replay. Answers are graded by hand from the sheet.

Costs real Bedrock calls (the same as asking in the app). Cost is read back from the usage ledger.

Usage (from backend/, on the host that runs the AI API):
  python scripts/eval/replay_mcp.py --user 5 --out /tmp/mcp_eval
  python scripts/eval/replay_mcp.py --user 5 --only r006,r063
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
for path in (_BACKEND_ROOT / "src", _BACKEND_ROOT, Path(__file__).resolve().parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import replay_real_questions as harness  # noqa: E402

from gemini_brain.config.settings import settings  # noqa: E402
from gemini_brain.semantic import periods  # noqa: E402

PORT = int(os.environ.get("MCP_EVAL_PORT", "8013"))
#: The API allows a few questions a minute per user (USAGE_RATE_PER_MINUTE); a refused case waits and asks again.
RATE_WAIT_SECONDS = 30
RATE_RETRIES = 4
_TOKEN = ""


def start_server(state_dir: str) -> None:
    """The MCP server on 127.0.0.1:PORT in a background thread, accepting a pasted bearer."""
    import uvicorn
    from gemini_brain.mcp_server.server import build_server

    settings.mcp_accept_api_tokens = True
    settings.mcp_public_url = f"http://127.0.0.1:{PORT}/mcp"
    settings.mcp_issuer_url = f"http://127.0.0.1:{PORT}"
    settings.mcp_state_dir = state_dir
    settings.mcp_audit_log = str(Path(state_dir) / "mcp_audit.jsonl")
    settings.mcp_tool_calls_per_minute = 1000  # the API's own limits still apply
    app = build_server().streamable_http_app(stateless_http=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            return
        time.sleep(0.1)
    raise RuntimeError("MCP server did not start")


async def _call(question: str, orgs: List[int]) -> Dict[str, Any]:
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {_TOKEN}"},
                                timeout=httpx2.Timeout(harness.CASE_TIMEOUT_SECONDS, connect=10.0))
    async with client, streamable_http_client(f"http://127.0.0.1:{PORT}/mcp", http_client=client) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("ask_accutax", {"question": question, "organization_ids": orgs})
    text = "\n".join(getattr(c, "text", "") for c in result.content or [])
    return {"text": text, "is_error": bool(result.is_error)}


def ask_mcp(question: str, orgs: List[int], meta: Dict[int, Dict[str, Any]], session_id: Optional[str]) -> Dict[str, Any]:
    """The harness's ask(): one ask_accutax call. A per-minute refusal waits and asks again."""
    for attempt in range(RATE_RETRIES + 1):
        out = asyncio.run(_call(question, orgs))
        if out["is_error"] and "asking very quickly" in out["text"] and attempt < RATE_RETRIES:
            time.sleep(RATE_WAIT_SECONDS)
            continue
        break
    return {"answer": out["text"], "status": "error" if out["is_error"] else "ok", "token_usage": {},
            "routing_info": {"path": "mcp"}, "tool_calls": [], "rate_retries": attempt}


def ledger_cost(user_id: int, since: datetime.datetime) -> Dict[str, Any]:
    """Questions and spend the usage ledger recorded for the user since the run started."""
    path = Path(settings.usage_ledger or "")
    path = path if path.is_absolute() else _BACKEND_ROOT / path
    if not path.is_file():
        return {}
    credits, answers = 0.0, 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("user_id") != user_id or datetime.datetime.fromisoformat(entry["ts"]) < since:
            continue
        credits += sum((entry.get("charge") or {}).values())
        answers += entry.get("kind") == "agent_answer"
    return {"answers": answers, "credits": round(credits, 1), "cost_usd": round(credits / 1000, 3)}


def main() -> int:
    global _TOKEN
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", default=str(harness.CASES_FILE))
    parser.add_argument("--only", default="", help="Comma-separated case ids")
    parser.add_argument("--user", type=int, required=True, help="Accutax user id to ask as")
    parser.add_argument("--out", default=str(Path(__file__).parent), help="Directory for the report")
    args = parser.parse_args()

    spec = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in spec["cases"]}
    wanted = {i for i in args.only.split(",") if i}
    cases = [c for c in spec["cases"] if not wanted or c["id"] in wanted]
    all_orgs = sorted({o for c in cases for o in c["orgs"]})

    from gemini_brain.api.auth import create_access_token
    _TOKEN = create_access_token(args.user, "", all_orgs, expires_delta=datetime.timedelta(hours=6))
    harness.ask = ask_mcp
    start_server(tempfile.mkdtemp(prefix="mcp_eval_state_"))

    today = periods.today_in(settings.report_timezone)
    started = datetime.datetime.now(datetime.timezone.utc)
    print(f"{len(cases)} cases · MCP ask_accutax via 127.0.0.1:{PORT} -> {settings.mcp_upstream_url} · user {args.user} "
          f"· today {today}", flush=True)
    sessions: Dict[str, str] = {}
    report = []
    for case in cases:
        outcome = harness.run_case(case, {}, sessions, by_id, today)
        outcome["path"] = "mcp"
        report.append(outcome)
        c = outcome["checks"]
        print(f"{case['id']} {case['kind']:12} {outcome['seconds']:>6}s orgs={len(case['orgs'])} "
              f"status={c['status']} ledger={(c['figures_ledger'] or {}).get('share')} "
              f"docs={(c['figures_documents'] or {}).get('share')}{' ERR ' + c['error'] if c['error'] else ''}",
              flush=True)

    spend = ledger_cost(args.user, started)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"replay_mcp_{stamp}.json"
    out.write_text(json.dumps({"today": str(today), "spend": spend, "cases": report}, indent=1, default=str,
                              ensure_ascii=False), encoding="utf-8")
    out.with_suffix(".md").write_text(harness.sheet(stamp, report), encoding="utf-8")
    errors = sum(1 for r in report if r["checks"]["status"] != "ok")
    print(f"\nReport: {out}\nSheet:  {out.with_suffix('.md')}\nErrors: {errors}\nSpend: {spend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
