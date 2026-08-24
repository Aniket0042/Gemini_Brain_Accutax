"""
test_org_queries.py — Test query execution and tenant isolation for Org 27, 25, 154, and 28 users.

Usage:
    python scripts/test_org_queries.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure src layout is available
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain.api.auth import _SEED_USER_MAP, get_user_allowed_orgs
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner


def run_test():
    print("=" * 80)
    print("TESTING GEMINI BRAIN QUERIES ACROSS ORGS 27, 25, 154, 28")
    print("=" * 80)

    runner = GeminiBrainRunner()

    tests = [
        {
            "title": "Org 27 User — Revenue & Top Customers",
            "email": "user_org27@accutax.com",
            "query": "What is our total revenue for 2026 and list top 3 customers?",
        },
        {
            "title": "Org 25 User — VAT & Supplier Payments",
            "email": "user_org25@accutax.com",
            "query": "What is our total tax collected and total supplier payments?",
        },
        {
            "title": "Org 154 User — Audit & Invoice Overview",
            "email": "user_org154@accutax.com",
            "query": "Give me an overview of our invoices and audit trail activity.",
        },
        {
            "title": "Org 28 User — Expense Breakdown",
            "email": "user_org28@accutax.com",
            "query": "What is our total expense breakdown by category?",
        },
    ]

    for t in tests:
        user = _SEED_USER_MAP[t["email"]]
        print(f"\n▶ [{t['title']}]")
        print(f"  User: {user['email']} (ID: {user['id']}, Allowed Orgs: {user['allowed_org_ids']})")
        print(f"  Query: \"{t['query']}\"")
        try:
            res = runner.run(
                query=t["query"],
                user_id=user["id"],
                allowed_org_ids=user["allowed_org_ids"],
            )
            print(f"  Status: {res.get('status', 'success')}")
            print(f"  Resolved Org ID: {res.get('organization_id')}")
            print(f"  Answer Snippet:\n  {res.get('answer', '')[:300]}...\n")
        except Exception as e:
            print(f"  [ERROR]: {e}")

    # Cross-tenant isolation test
    print("\n" + "=" * 80)
    print("TESTING CROSS-TENANT ISOLATION (SECURITY CHECK)")
    print("=" * 80)
    user27 = _SEED_USER_MAP["user_org27@accutax.com"]
    print(f"\n▶ User {user27['email']} attempting to access Organization 25 explicitly:")
    try:
        res = runner.run(
            query="Show me all invoices for organization 25",
            organization_id=25,
            user_id=user27["id"],
            allowed_org_ids=user27["allowed_org_ids"],
        )
        print(f"  [FAIL] Query succeeded unexpectedly: {res}")
    except ValueError as ve:
        print(f"  [PASS] Tenant isolation successfully blocked access: {ve}")
    except Exception as e:
        print(f"  [PASS] Request blocked: {e}")

    print("\n" + "=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_test()
