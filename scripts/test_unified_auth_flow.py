"""
test_unified_auth_flow.py — End-to-end test for Approach 2 (Single login + Dynamic tenant switching).

Usage:
    python scripts/test_unified_auth_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure src layout is available
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain.api.models import LoginRequest
from gemini_brain.api.routes import login_json, list_tenants
from gemini_brain.api.auth import get_current_user


def test_flow():
    print("=" * 80)
    print("TESTING APPROACH 2: UNIFIED LOGIN & DYNAMIC TENANT SWITCHING")
    print("=" * 80)

    # 1. Login with unified user
    print("\n[STEP 1] Logging in as genthird456@gmail.com...")
    req = LoginRequest(username="genthird456@gmail.com", password="Password123$$")
    auth_resp = login_json(req)

    print(f"  Login Status : SUCCESS")
    print(f"  User ID      : {auth_resp.user_id}")
    print(f"  Email        : {auth_resp.email}")
    print(f"  Allowed Orgs : {auth_resp.allowed_org_ids}")
    print(f"  Token Snippet: {auth_resp.access_token[:40]}...")
    print(f"  Tenants Count: {len(auth_resp.tenants)}")

    # 2. Test Tenant Discovery
    print("\n[STEP 2] Fetching Accessible Organization Tenants for User...")
    current_user = get_current_user(token=auth_resp.access_token)
    tenants_resp = list_tenants(current_user=current_user)

    for t in tenants_resp.tenants:
        print(f"  • [Org {t.id:3d}] {t.display_name}")
        print(f"            Specialty: {t.tag} ({t.badge_color})")
        print(f"            Summary  : {t.description[:80]}...")

    # 3. Assertions
    assert len(tenants_resp.tenants) == 4, f"Expected 4 tenants, got {len(tenants_resp.tenants)}"
    org_ids = [t.id for t in tenants_resp.tenants]
    assert 27 in org_ids, "Org 27 missing"
    assert 25 in org_ids, "Org 25 missing"
    assert 154 in org_ids, "Org 154 missing"
    assert 28 in org_ids, "Org 28 missing"

    print("\n" + "=" * 80)
    print("ALL TESTS PASSED SUCCESSFULLY! (Approach 2 Verified)")
    print("=" * 80)


if __name__ == "__main__":
    test_flow()
