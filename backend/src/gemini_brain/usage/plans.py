"""
plans.py — The AI plans an owner account can be on (docs/ORG_TOKEN_LIMITS_STRATEGY.md 4.3). AI is included in the
Accutax plan: each seat (the owner and each active collaborator) adds credits to the owner's monthly bucket.
Seats are counted as they were when the month began, up to the plan's max_seats, so inviting people (or fake
accounts) does not grow this month's bucket; a bigger bucket is a plan change or a top-up by Accutax.

1 credit = $0.001. A typical question costs about 20 credits, the worst about 90.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    #: Credits each seat adds to the monthly bucket. None: no bucket.
    per_seat: Optional[float]
    #: A person's default daily cap inside the bucket. None: no cap.
    user_daily: Optional[float]
    #: Most seats that add credits. More people may still use AI; they share the bucket. None: no bucket anyway.
    max_seats: Optional[int] = None


PLANS = {
    "trial": Plan("trial", "Trial", 2_000, 300, max_seats=2),
    "standard": Plan("standard", "Standard", 10_000, 1_000, max_seats=10),
    "pro": Plan("pro", "Pro", 25_000, 2_000, max_seats=50),
    "unlimited": Plan("unlimited", "Unlimited (internal, demo)", None, None),
}
DEFAULT = "standard"
#: After the bucket is used up, answers continue on the lite model until this much more is used; then they stop.
LITE_GRACE = 0.10
#: Share of the bucket at which the owner is warned (and, at the higher one, every user sees a banner).
WARN_AT = (0.5, 0.8)


def get(key: Optional[str]) -> Plan:
    return PLANS.get((key or "").strip().lower(), PLANS[DEFAULT])
