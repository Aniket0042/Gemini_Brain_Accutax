"""FastAPI REST API sub-package for Gemini Brain."""
from __future__ import annotations

from gemini_brain.api.app import app, create_app

__all__ = ["app", "create_app"]
