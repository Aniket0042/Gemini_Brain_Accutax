"""FastAPI REST API sub-package for Gemini Brain."""
from __future__ import annotations

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    if name in ("app", "create_app"):
        from gemini_brain.api.app import app, create_app
        if name == "app":
            return app
        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
