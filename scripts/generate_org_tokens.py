"""
generate_org_tokens.py — Generate valid JWT bearer tokens for Org 27, 25, 154, 28 users and Admin.

Usage:
    python scripts/generate_org_tokens.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import timedelta

# Ensure src layout is available
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain.api.auth import _SEED_USER_MAP, create_access_token


def main() -> None:
    print("=" * 80)
    print("GEMINI BRAIN — USER CREDENTIALS & JWT TOKEN GENERATOR")
    print("=" * 80)

    target_emails = [
        "user_org27@accutax.com",
        "user_org25@accutax.com",
        "user_org154@accutax.com",
        "user_org28@accutax.com",
        "admin_all@accutax.com",
    ]

    # Generate 7-day token for easy testing
    token_delta = timedelta(days=7)

    for email in target_emails:
        user_info = _SEED_USER_MAP.get(email)
        if not user_info:
            continue

        token = create_access_token(
            user_id=user_info["id"],
            email=user_info["email"],
            allowed_org_ids=user_info["allowed_org_ids"],
            expires_delta=token_delta,
        )

        org_str = ", ".join(str(o) for o in user_info["allowed_org_ids"])
        print(f"\nUser Email      : {user_info['email']}")
        print(f"Password        : {user_info['password']}")
        print(f"User ID         : {user_info['id']}")
        print(f"Allowed Org IDs : [{org_str}]")
        print(f"JWT Token       : {token}")
        print("-" * 80)

    print("\n[TIP] In Swagger UI (http://localhost:8000/docs), click 'Authorize' and log in with any user above,")
    print("      or pass header: Authorization: Bearer <JWT_TOKEN> in your REST client / curl calls.")


if __name__ == "__main__":
    main()
