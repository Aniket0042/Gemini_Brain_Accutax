"""Files users attach in chat: checks (validate), storage and retention (store). See docs/DOCUMENT_UPLOAD_PLAN.md."""
from __future__ import annotations

from typing import Any, Optional

from gemini_brain.config.settings import settings


def enabled_for(user_id: Any = None, email: Optional[str] = None) -> bool:
    """Whether this user may attach files: UPLOADS_ENABLED is on and, when UPLOAD_USERS lists people, the
    user's email (any case) or id is one of them."""
    if not settings.uploads_enabled:
        return False
    people = {p.strip().lower() for p in (settings.upload_users or "").split(",") if p.strip()}
    if not people:
        return True
    return str(user_id).lower() in people or (email or "").strip().lower() in people
