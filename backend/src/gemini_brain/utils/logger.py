"""
logger.py — Structured logging setup for the gemini_brain package.
"""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the ``gemini_brain`` logger hierarchy once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger("gemini_brain")
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    fmt = logging.Formatter(
        "[%(asctime)s] %(name)s %(levelname)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(fmt)
    root.addHandler(handler)

    _CONFIGURED = True
