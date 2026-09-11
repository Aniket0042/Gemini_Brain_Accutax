"""Delivery-aware canvas specs and short-lived file artifacts."""

from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.artifacts.delivery import Delivery, detect_delivery
from gemini_brain.artifacts.report_spec import build_report_spec

__all__ = [
    "Delivery",
    "detect_delivery",
    "build_report_spec",
    "attach_delivery",
]
