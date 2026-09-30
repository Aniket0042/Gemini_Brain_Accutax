"""Report quality scorecard.

    python scripts/eval/report_eval.py            # offline: goldens, chart intent, grounding
    python scripts/eval/report_eval.py --live     # also narrate every golden fixture with the real model

Offline sections need no network. --live calls Bedrock once per fixture (the
report narrator) and reports, per fixture, how many figures the model wrote,
how many were verified, how many sentences grounding removed, and latency —
the numbers to watch when changing the narrator prompt or model.

Exit code is non-zero when any offline gate fails, so this can run in CI.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def _line(label: str, passed: int, total: int) -> str:
    rate = passed / total if total else 1.0
    return f"  {label:<34} {passed:>3}/{total:<3} {rate:6.1%}"


def offline() -> bool:
    from tests.report_golden.evals import run_chart_intent, run_narrative
    from tests.report_golden.fixtures import fixture_names
    from tests.report_golden.harness import build, load_snapshot, normalised, snapshot

    ok = True
    print("Golden reports")
    changed = [n for n in fixture_names() if normalised(snapshot(build(n))) != load_snapshot(n)]
    print(_line("unchanged vs reviewed snapshot", len(fixture_names()) - len(changed), len(fixture_names())))
    for name in changed:
        print(f"    changed: {name}")
    ok &= not changed

    print("Chart intent")
    intent = run_chart_intent()
    print(_line("form honoured or explained", intent.passed, intent.total))
    for f in intent.failures:
        print(f"    {f}")
    ok &= intent.rate == 1.0

    print("Narrative grounding")
    for key, label in (("wrong_removed", "wrong statements removed"), ("true_kept", "true statements kept")):
        scored = run_narrative()[key]
        print(_line(label, scored.passed, scored.total))
        for f in scored.failures:
            print(f"    {f}")
        ok &= scored.rate == 1.0
    return ok


def live() -> None:
    from gemini_brain.artifacts.attach import attach_delivery
    from tests.report_golden.fixtures import FIXTURES

    print("Live narration (Bedrock)")
    print(f"  {'fixture':<28} {'origin':<9} {'figures':>8} {'removed':>8} {'secs':>6}  headline")
    checked = verified = removed = 0
    for name, fixture in FIXTURES.items():
        t0 = time.perf_counter()
        out = attach_delivery(fixture["query"], fixture["data"], [], user_id=1, organization_id=1)
        secs = time.perf_counter() - t0
        spec = next((b["spec"] for b in out if b.get("type") == "canvas"), None)
        if spec is None:
            print(f"  {name:<28} (no report)")
            continue
        integrity = spec.get("integrity") or {}
        parts = spec.get("narrative_parts") or {}
        checked += integrity.get("figures_checked", 0)
        verified += integrity.get("figures_verified", 0)
        removed += integrity.get("sentences_removed", 0)
        figures = f"{integrity.get('figures_verified', 0)}/{integrity.get('figures_checked', 0)}"
        print(f"  {name:<28} {parts.get('origin', '-'):<9} {figures:>8} {integrity.get('sentences_removed', 0):>8} "
              f"{secs:6.1f}  {(parts.get('headline') or '')[:70]}")
    rate = verified / checked if checked else 1.0
    print(f"  grounding pass rate {verified}/{checked} = {rate:.1%}; sentences removed: {removed}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Arabic names on a Windows console
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also narrate every fixture with the real model")
    args = parser.parse_args()
    ok = offline()
    if args.live:
        live()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
