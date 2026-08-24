"""
versions.py — Per-organization data version counter for cache invalidation.
"""
from __future__ import annotations

import threading
from typing import Dict

_version_lock = threading.Lock()
_org_versions: Dict[int, int] = {}


def get_data_version(org_id: int) -> int:
    """Retrieve current data version counter for organization."""
    with _version_lock:
        return _org_versions.get(org_id, 1)


def bump_data_version(org_id: int) -> int:
    """Increment data version counter when writes/mutations occur."""
    with _version_lock:
        current = _org_versions.get(org_id, 1)
        _org_versions[org_id] = current + 1
        return _org_versions[org_id]


def reset_data_versions() -> None:
    """Reset versions (primarily for testing)."""
    with _version_lock:
        _org_versions.clear()
