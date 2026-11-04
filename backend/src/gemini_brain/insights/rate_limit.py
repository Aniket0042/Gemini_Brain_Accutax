import threading
import time
from collections import defaultdict, deque

from gemini_brain.insights.config import get_settings

_settings = get_settings()
_hits: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()

_WINDOW_SECONDS = 3600


def check_and_record(organization_id: int) -> bool:
    """
    In-memory sliding-window limiter, per organization. Returns True if the
    request is allowed. Note: this state is per-process -- if this service
    is ever run with multiple workers/instances, swap this for a shared
    store (e.g. Redis) so limits are enforced across all of them.
    """
    now = time.monotonic()
    limit = _settings.rate_limit_per_org_per_hour

    with _lock:
        bucket = _hits[organization_id]
        while bucket and now - bucket[0] > _WINDOW_SECONDS:
            bucket.popleft()

        if len(bucket) >= limit:
            return False

        bucket.append(now)
        return True
