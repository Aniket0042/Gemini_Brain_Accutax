"""Multi-org deadline and cancellation (Phase 0.7)."""
import threading
import time
from typing import Any, Dict, List

from gemini_brain.orchestrator import multi_org
from gemini_brain.orchestrator.multi_org import run_multi_org_stream

META = {5: {"name": "Alpha LLC", "currency": "AED"}, 6: {"name": "Beta Inc", "currency": "AED"},
        7: {"name": "Gamma FZE", "currency": "AED"}}


class SlowRunner:
    """Org 6 hangs; the others answer at once."""

    started: List[int] = []
    release = threading.Event()

    def run(self, **kwargs: Any) -> Dict[str, Any]:
        oid = kwargs["organization_id"]
        SlowRunner.started.append(oid)
        if oid == 6:
            SlowRunner.release.wait(10)
        return {"answer": f"Revenue for org {oid}", "status": "ok", "results": [{"revenue": oid}], "blocks": [],
                "token_usage": {}, "routing_info": {"type": 4}}

    def _call_llm(self, system: str, user_text: str, **kwargs: Any):
        return "Summary.", 1, 1


def _reset(monkeypatch, deadline: float, parallel: int):
    SlowRunner.started = []
    SlowRunner.release = threading.Event()
    monkeypatch.setattr(multi_org.settings if hasattr(multi_org, "settings") else
                        __import__("gemini_brain.config.settings", fromlist=["settings"]).settings,
                        "multi_org_fetch_deadline_seconds", deadline)
    monkeypatch.setattr(multi_org, "MAX_PARALLEL_ORGS", parallel)


def test_a_hung_organization_is_reported_not_awaited(monkeypatch):
    _reset(monkeypatch, deadline=1.0, parallel=3)
    t0 = time.monotonic()
    chunks = list(run_multi_org_stream("revenue", [5, 6, 7], META, runner_factory=SlowRunner,
                                       run_kwargs={"allowed_org_ids": [5, 6, 7]}))
    elapsed = time.monotonic() - t0
    SlowRunner.release.set()
    final = chunks[-1]["final_result"]
    assert elapsed < 5, elapsed
    assert any("Stopped waiting" in str(c.get("status", "")) for c in chunks)
    assert "Beta Inc" in final["answer"] or "Beta Inc" in str(final.get("notes") or final)
    statuses = {o["id"]: o["status"] for o in final["organizations"]}
    assert statuses[6] == "failed" and statuses[5] == "ok" and statuses[7] == "ok"


def test_closing_the_stream_cancels_queued_organizations(monkeypatch):
    # One worker: org 5 answers, org 6 then hangs, org 7 waits in the queue.
    _reset(monkeypatch, deadline=30.0, parallel=1)
    stream = run_multi_org_stream("revenue", [5, 6, 7], META, runner_factory=SlowRunner,
                                  run_kwargs={"allowed_org_ids": [5, 6, 7]})
    assert "Querying" in next(stream)["status"]
    assert next(stream).get("organization_id") == 5  # parked at a yield inside the fan-out
    t0 = time.monotonic()
    stream.close()  # what the route does on Stop or a dropped connection
    assert time.monotonic() - t0 < 1  # did not wait for org 6
    SlowRunner.release.set()
    time.sleep(0.5)
    assert 7 not in SlowRunner.started  # queued work was cancelled, never started
