"""Reads a PDF or an image with Claude into a `DocumentExtract` (docs/DOCUMENT_UPLOAD_PLAN.md, phase 0 choices).

One Converse call per file. The model must call `record_document`, so its answer is always JSON in the
tool's schema. PDFs go in as a document block (the model reads the page pictures too, so scans work and no
PDF renderer is needed); images go in as an image block after `text.prepare_image`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.config.settings import settings
from gemini_brain.uploads.extract import EXTRACT_SYSTEM, RECORD_TOOL, DocumentExtract
from gemini_brain.uploads.text import prepare_image

logger = logging.getLogger("gemini_brain.uploads.claude")


class ReadFailed(Exception):
    """The file could not be read. `message` is safe to show the user."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class ClaudeRead:
    extract: DocumentExtract
    model: str
    usage: Dict[str, Any]       # input_tokens, output_tokens, cost_usd, seconds
    truncated: bool = False     # the model ran out of room before it finished the record


def _adapter() -> Any:
    from gemini_brain.reasoning.bedrock_client import BedrockAdapter, get_long_read_client

    return BedrockAdapter(settings.upload_extract_model, label="upload-reader", client_factory=get_long_read_client)


def blocks_for(content: bytes, kind: str) -> List[Dict[str, Any]]:
    if kind == "pdf":
        return [{"document": {"format": "pdf", "name": "uploaded document", "source": {"bytes": content}}}]
    data, fmt = prepare_image(content)
    return [{"image": {"format": fmt, "source": {"bytes": data}}}]


def _instruction(part: Optional[Tuple[int, int, int]]) -> str:
    if not part:
        return "Record this document."
    first, last, total = part
    return (f"Record this document. It has {total} pages and you are given pages {first} to {last} only. Record only what is "
            f"on these pages: use null for header fields that are not on them, and do not repeat lines from other pages.")


def read(content: bytes, kind: str, *, adapter: Optional[Any] = None, part: Optional[Tuple[int, int, int]] = None) -> ClaudeRead:
    """Read one PDF or image. `part`: (first page, last page, pages in all) when `content` is a piece of a longer PDF.
    Raises ReadFailed with a message for the user."""
    if settings.upload_extractor != "claude":
        raise ReadFailed("Reading photos and scanned pages is turned off.")
    adapter = adapter or _adapter()
    messages = [{"role": "user", "content": blocks_for(content, kind) + [{"text": _instruction(part)}]}]
    t0 = time.perf_counter()
    try:
        resp = adapter.converse_with_tools(
            EXTRACT_SYSTEM, messages, [RECORD_TOOL], max_tokens=settings.upload_read_max_tokens,
            purpose="upload_extract", tool_choice="record_document")
    except Exception as e:
        logger.warning("Upload read failed: %s", e)
        raise ReadFailed(_user_message(e)) from e
    seconds = round(time.perf_counter() - t0, 1)

    recorded: Optional[Dict[str, Any]] = None
    for block in resp.get("output", {}).get("message", {}).get("content", []):
        if "toolUse" in block and block["toolUse"].get("name") == "record_document":
            recorded = block["toolUse"].get("input")
    stop = resp.get("stopReason")
    if not isinstance(recorded, dict) or not recorded:
        if stop == "max_tokens":
            raise ReadFailed("This document is too long to read in one go. Attach fewer pages.")
        raise ReadFailed("We could not read this file. Attach a clearer copy.")
    usage = adapter.get_token_usage()
    usage["seconds"] = seconds
    return ClaudeRead(DocumentExtract.from_dict(recorded), settings.upload_extract_model, usage, truncated=stop == "max_tokens")


def _user_message(e: Exception) -> str:
    text = str(e).lower()
    if "timeout" in text or "timed out" in text:
        return "Reading this file took too long. Try again, or attach fewer pages."
    if "throttl" in text or "too many requests" in text:
        return "The reader is busy. Try again in a minute."
    if "validationexception" in text and ("document" in text or "image" in text):
        return "This file could not be read. It may be damaged or in an unusual format."
    return "We could not read this file. Try again, or attach a clearer copy."
