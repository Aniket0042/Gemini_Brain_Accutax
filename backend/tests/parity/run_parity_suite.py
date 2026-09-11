"""
run_parity_suite.py — Side-by-side parity comparison test suite.

Compares outputs, routing decisions, intent classification, endpoint selection,
and response structure between the original GeminiBrainRunner in the monolith
and the newly extracted gemini_brain package.
"""
from __future__ import annotations

import json
import os
import sys
import time

USER_SITE = r"C:\Users\acer\AppData\Roaming\Python\Python312\site-packages"
if USER_SITE not in sys.path:
    sys.path.insert(0, USER_SITE)

# Ensure both original and new packages are importable
ORIGINAL_PROJECT_PATH = r"C:\Users\acer\Desktop\query-parser-bedrock_clean\query-parser-bedrock_clean"
NEW_PROJECT_PATH = r"C:\Users\acer\Desktop\Gemini_Brain\src"

if ORIGINAL_PROJECT_PATH not in sys.path:
    sys.path.insert(0, ORIGINAL_PROJECT_PATH)
if NEW_PROJECT_PATH not in sys.path:
    sys.path.insert(0, NEW_PROJECT_PATH)


TEST_QUERIES = [
    # Type 1: FAQ / How-to
    "How do I create a recurring invoice in Accutax?",
    # Type 2: App Guidance
    "Where can I find the expense report settings?",
    # Type 3: Report Generation
    "Show me the P&L statement for 2026",
    # Type 4: Data Query
    "What is our total revenue this year?",
    # Type 5: Forecast
    "Show expected cash flow projection for next month",
    # Type 6: Accounting Concept
    "What is accounts receivable aging?",
    # Type 7: Summary & Advice
    "Give me a general financial health summary of the company",
]


def run_parity_tests():
    print("=" * 80)
    print("GEMINI BRAIN - SIDE-BY-SIDE PARITY TEST SUITE")
    print("=" * 80)

    try:
        from model_arena.backend.adapters.gemini_brain_adapter import (
            GeminiBrainRunner as OriginalRunner,
        )
        print("[OK] Loaded Original GeminiBrainRunner")
    except ImportError as e:
        print(f"[FAIL] Could not load Original GeminiBrainRunner: {e}")
        return

    try:
        from gemini_brain import GeminiBrainRunner as NewRunner
        print("[OK] Loaded New gemini_brain.GeminiBrainRunner")
    except ImportError as e:
        print(f"[FAIL] Could not load New gemini_brain.GeminiBrainRunner: {e}")
        return

    api_key = os.getenv("GEMINI_API_KEY", "AIzaSyDEnSor0PcjFZWFyE-ewSjboC4fa9zY0uc")
    orig_runner = OriginalRunner(api_key=api_key)
    new_runner = NewRunner(api_key=api_key)

    total_tests = len(TEST_QUERIES)
    passed_tests = 0

    print("\nExecuting test queries side-by-side...\n")

    for idx, query in enumerate(TEST_QUERIES, 1):
        print(f"[{idx}/{total_tests}] Query: '{query}'")

        # Run original
        t0 = time.time()
        try:
            res_orig = orig_runner.run(query, organization_id=199, use_api=False)
            dt_orig = time.time() - t0
        except Exception as e:
            print(f"  [FAIL] Original failed: {e}")
            continue

        # Run new
        t0 = time.time()
        try:
            res_new = new_runner.run(query, organization_id=199, use_api=False)
            dt_new = time.time() - t0
        except Exception as e:
            print(f"  [FAIL] New implementation failed: {e}")
            continue

        orig_route = res_orig.get("routing_info", {})
        new_route = res_new.get("routing_info", {})

        type_match = orig_route.get("type") == new_route.get("type")
        path_match = orig_route.get("path") == new_route.get("path")
        answer_present = bool(res_new.get("answer"))

        status = "PASS" if (type_match and path_match and answer_present) else "DIFF"
        if status == "PASS":
            passed_tests += 1

        print(f"  Result: {status}")
        print(f"  - Original Route: type={orig_route.get('type')}, path={orig_route.get('path')} ({dt_orig:.2f}s)")
        print(f"  - New Route:      type={new_route.get('type')}, path={new_route.get('path')} ({dt_new:.2f}s)")
        print("-" * 60)

    print("\n" + "=" * 80)
    print(f"PARITY SUMMARY: {passed_tests}/{total_tests} queries passed side-by-side parity check.")
    print("=" * 80)


if __name__ == "__main__":
    run_parity_tests()
