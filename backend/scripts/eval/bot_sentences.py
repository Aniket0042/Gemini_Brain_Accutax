"""bot_sentences.py — Evaluate 'one sentence to a draft' parser accuracy.

docs/VAT_BOT_IMPLEMENTATION_PLAN.md, Task 1.12.
Runs from_sentence on tests/data/bot_sentences.json, compares field by field,
prints a comparison table and total cost.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from gemini_brain.bots.draft import from_sentence
from gemini_brain.bots.schema import BotDraft


def _mock_call_model(system_prompt: str, messages: list, tools: list, **kwargs) -> Dict[str, Any]:
    """Deterministic mock for local evaluation dry-runs."""
    text = (messages[0]["content"][0]["text"] or "").lower()
    tool_input: Dict[str, Any] = {}

    if "what is my vat" in text or "how do i file" in text or "top 5 suppliers" in text:
        # Non-bot queries
        tool_input = {"unclear": ["This looks like an accounting question rather than a scheduled bot request."]}
    elif "every monday" in text:
        time_str = "09:00" if "9am" in text or "09:00" in text else "08:00"
        tool_input = {
            "name": "VAT Watch",
            "schedules": [{"kind": "weekly", "day": "mon", "time": time_str}],
        }
        if "al futtaim" in text:
            tool_input["org_names"] = ["Al Futtaim LLC"]
    elif "1st of every month" in text:
        tool_input = {
            "schedules": [{"kind": "monthly", "day_of_month": 1, "time": "10:00"}],
        }
    elif "before each vat return deadline" in text:
        tool_input = {
            "schedules": [{"kind": "before_deadline", "days_before": [7]}],
        }
    elif "after each quarter ends" in text:
        days = 1 if "1 day" in text else 3
        time_str = "09:00" if "09:00" in text else "08:00"
        tool_input = {
            "schedules": [{"kind": "after_period_end", "days_after": days, "time": time_str}],
        }
    elif "friday at 17:30" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "fri", "time": "17:30"}],
            "watches": ["duplicate_bill"],
        }
    elif "email digest" in text or "every wednesday" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "wed", "time": "08:00"}],
            "deliver": ["email"],
        }
    elif "alert me in app notifications" in text:
        tool_input = {
            "deliver": ["bell"],
            "watches": ["vat_rate_wrong"],
        }
    elif "sales invoices" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "mon"}],
            "watches": ["sales_vat_wrong"],
        }
    elif "duplicate bills and missing bill details" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "tue"}],
            "watches": ["duplicate_bill", "bill_missing_details"],
        }
    elif "unknown enterprises" in text:
        tool_input = {
            "org_names": ["Unknown Enterprises LLC"],
            "schedules": [{"kind": "monthly", "day_of_month": 1}],
        }
    elif "high sensitivity" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "tue"}],
            "sensitivity": "more",
            "watches": ["unusual_bill"],
        }
    elif "14 days and 2 days before" in text:
        tool_input = {
            "schedules": [{"kind": "before_deadline", "days_before": [14, 2]}],
        }
    elif "15th of each month" in text:
        tool_input = {
            "schedules": [{"kind": "monthly", "day_of_month": 15}],
            "watches": ["vat_rate_wrong"],
        }
    elif "email and in-app alert" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "thu"}],
            "deliver": ["email", "bell"],
            "watches": ["bill_missing_details"],
        }
    elif "apex holdings" in text:
        tool_input = {
            "org_names": ["Apex Holdings"],
            "schedules": [{"kind": "weekly", "day": "mon"}],
            "watches": ["duplicate_bill"],
        }
    elif "full monthly vat review" in text:
        tool_input = {
            "schedules": [{"kind": "monthly", "day_of_month": 1}],
            "watches": ["duplicate_bill", "vat_rate_wrong", "bill_missing_details", "unusual_bill", "sales_vat_wrong"],
        }
    elif "1 day after each quarter" in text:
        tool_input = {
            "schedules": [{"kind": "after_period_end", "days_after": 1, "time": "09:00"}],
        }
    elif "every sunday evening" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "sun", "time": "18:00"}],
            "watches": ["duplicate_bill"],
        }
    elif "low sensitivity" in text:
        tool_input = {
            "schedules": [{"kind": "weekly", "day": "fri"}],
            "sensitivity": "fewer",
        }
    elif "5 days before each vat deadline" in text:
        tool_input = {
            "schedules": [{"kind": "before_deadline", "days_before": [5]}],
            "deliver": ["email"],
        }
    else:
        # Default / vague
        tool_input = {
            "name": "VAT Watch",
            "schedules": [{"kind": "weekly", "day": "mon", "time": "08:00"}],
            "unclear": ["How often should I run this check?"],
        }

    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "name": "make_bot_draft",
                            "input": tool_input,
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": 280, "outputTokens": 85},
    }


def evaluate(mock: bool = False) -> Dict[str, Any]:
    dataset_path = ROOT / "tests" / "data" / "bot_sentences.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    call_model = _mock_call_model if mock else None
    results = []
    total_tokens = 0
    passed_count = 0
    invented_orgs_count = 0

    print(f"Running evaluation on {len(cases)} cases (mock={mock})...\n")

    for case in cases:
        allowed_orgs = {int(k): v for k, v in case["allowed_orgs"].items()}
        current_org_ids = case["current_org_ids"]
        expected = case["expected"]

        t0 = time.time()
        kwargs = {
            "text": case["sentence"],
            "allowed_orgs": allowed_orgs,
            "current_org_ids": current_org_ids,
        }
        if call_model is not None:
            kwargs["call_model"] = call_model

        draft, issues = from_sentence(**kwargs)
        elapsed = time.time() - t0

        # Safety check: Verify NO invented org
        for oid in draft.org_ids:
            if oid not in allowed_orgs:
                invented_orgs_count += 1

        # Accuracy checks
        reasons = []
        sched_ok = True
        watch_ok = True
        org_ok = True

        if expected.get("is_bot") is False:
            # Expected non-bot query
            if not issues and not draft.questions:
                reasons.append("Expected question or issue flagging non-bot prompt")
                sched_ok = False
        else:
            # Schedule check
            if "schedule_kind" in expected:
                if not draft.schedules or draft.schedules[0].kind != expected["schedule_kind"]:
                    sched_ok = False
                    got_kind = draft.schedules[0].kind if draft.schedules else "none"
                    reasons.append(f"schedule kind {got_kind} != {expected['schedule_kind']}")

            if "day" in expected and draft.schedules:
                if draft.schedules[0].day != expected["day"]:
                    sched_ok = False
                    reasons.append(f"day {draft.schedules[0].day} != {expected['day']}")

            if "time" in expected and draft.schedules:
                if draft.schedules[0].time != expected["time"]:
                    sched_ok = False
                    reasons.append(f"time {draft.schedules[0].time} != {expected['time']}")

            # Watches check
            if "watches" in expected:
                for w in expected["watches"]:
                    if w not in draft.watches:
                        watch_ok = False
                        reasons.append(f"missing watch {w}")

            # Delivery check
            if "deliver_contains" in expected:
                target_delivers = (
                    expected["deliver_contains"]
                    if isinstance(expected["deliver_contains"], list)
                    else [expected["deliver_contains"]]
                )
                for d in target_delivers:
                    if d not in draft.deliver:
                        reasons.append(f"missing delivery {d}")

            # Org check
            if "org_ids" in expected:
                if draft.org_ids != expected["org_ids"]:
                    org_ok = False
                    reasons.append(f"org_ids {draft.org_ids} != {expected['org_ids']}")

            if expected.get("has_question_or_issue_for_org"):
                has_q = any("organization" in q.lower() or "enterprise" in q.lower() for q in draft.questions)
                if not has_q:
                    org_ok = False
                    reasons.append("expected clarifying question for unknown organization")

        passed = sched_ok and watch_ok and org_ok and (len(reasons) == 0)
        if passed:
            passed_count += 1

        results.append({
            "id": case["id"],
            "sentence": case["sentence"],
            "passed": passed,
            "reasons": reasons,
            "elapsed_ms": int(elapsed * 1000),
            "schedules": [s.model_dump() for s in draft.schedules],
            "watches": draft.watches,
            "org_ids": draft.org_ids,
        })

    # Summary table
    table_lines = [
        "| ID | Sentence | Schedule Match | Watches Match | Orgs Match | Result | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        res_str = "PASS" if r["passed"] else "FAIL"
        notes = "; ".join(r["reasons"]) if r["reasons"] else "OK"
        table_lines.append(
            f"| {r['id']} | {r['sentence'][:40]}... | {'Yes' if 'schedule' not in notes else 'No'} | "
            f"{'Yes' if 'watch' not in notes else 'No'} | {'Yes' if 'org' not in notes else 'No'} | {res_str} | {notes} |"
        )

    summary_table = "\n".join(table_lines)
    print(summary_table)
    print("\n" + "=" * 50)
    print(f"Total: {passed_count}/{len(cases)} passed (target >= 22/25)")
    print(f"Invented organizations: {invented_orgs_count} (target: 0)")
    print("=" * 50)

    return {
        "passed": passed_count,
        "total": len(cases),
        "invented_orgs": invented_orgs_count,
        "results": results,
        "summary_table": summary_table,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate bot sentence parser")
    parser.add_argument("--mock", action="store_true", help="Use deterministic mock model")
    args = parser.parse_args()
    evaluate(mock=args.mock)


if __name__ == "__main__":
    main()
