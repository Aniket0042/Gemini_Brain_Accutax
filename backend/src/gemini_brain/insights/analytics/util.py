from typing import Literal

Trend = Literal["up", "down", "flat"]


def pct_change(current: float, previous: float) -> float | None:
    if previous in (0, None):
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


def trend(current: float, previous: float, flat_epsilon: float = 0.5) -> Trend:
    change = pct_change(current, previous)
    if change is None:
        return "flat"
    if change > flat_epsilon:
        return "up"
    if change < -flat_epsilon:
        return "down"
    return "flat"


def safe_ratio(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round((numerator / denominator) * 100, 1)


def fmt_money(value: float) -> str:
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1_000_000:
        return f"{sign}${abs_v / 1_000_000:.1f}M"
    if abs_v >= 1_000:
        return f"{sign}${abs_v / 1_000:.1f}K"
    return f"{sign}${abs_v:.0f}"


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def ensure_labels(labels: list[str] | None, length: int) -> list[str]:
    """Generic "P1", "P2", ... fallback when a caller sends time-series data
    without period labels (or fewer labels than data points) -- keeps the
    widget usable instead of rejecting an otherwise valid payload."""
    if labels and len(labels) >= length:
        return labels[:length]
    return [f"P{i + 1}" for i in range(length)]
