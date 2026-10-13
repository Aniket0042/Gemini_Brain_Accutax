"""Reads an uploaded file in the background and records what it says (docs/DOCUMENT_UPLOAD_PLAN.md, phase 2).

    start(rec)       marks the upload `reading` and hands it to a worker; the API returns at once
    read_now(id)     does the reading (also what the worker runs; tests call it directly)

Routing, by file kind:
  docx, xlsx, csv       text out of the file with libraries; no model call
  image                 Claude, as an image block
  PDF with no text      Claude, as a document block (the pages are pictures)
  PDF with text         Claude when it looks like a bill or statement (the same cost as reading the text, and
                        the layout is kept); otherwise its text only, no model call

The same file uploaded again by the same user, while an earlier copy is still in a thread, reuses that read
(no model call). The cache lives and dies with the threads, so it never keeps a file past its thread.
Result, per upload: `<id>.extract.json` (extract, checks, model, tokens, cost) and the upload's status.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Sequence, Tuple

from gemini_brain.config.settings import settings
from gemini_brain.uploads import checks as checks_mod
from gemini_brain.uploads import claude as claude_reader
from gemini_brain.uploads import store
from gemini_brain.uploads import text as text_reader
from gemini_brain.uploads.kind import guess_type
from gemini_brain.uploads.claude import ReadFailed
from gemini_brain.uploads.extract import DocumentExtract

logger = logging.getLogger("gemini_brain.uploads.reader")

#: Bump when the prompt, the schema or the checks change: a cached read of an older version is then ignored.
EXTRACT_VERSION = "3"
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=settings.upload_read_workers, thread_name_prefix="upload-read")
        return _executor


def start(rec: store.UploadRecord) -> store.UploadRecord:
    """Mark the upload as being read and queue it. Returns the updated record."""
    updated = store.update(rec.id, status="reading", read_started_at=time.time()) or rec
    _pool().submit(read_now, rec.id)
    return updated


def recover_interrupted(now: Optional[float] = None) -> int:
    """At startup: fail reads that were lost (still `reading` long after they began). Safe with several workers:
    a read another worker is running is younger than the threshold and is left alone."""
    return store.fail_stale_reads(now)


def read_now(upload_id: str, *, adapter: Optional[Any] = None) -> None:
    """Read one upload and record the outcome. Never raises: every failure becomes a failed status."""
    rec = store.get(upload_id)
    if rec is None:
        return
    t0 = time.perf_counter()
    try:
        cached = store.find_cached_extract(rec.user_id, rec.sha256, EXTRACT_VERSION, exclude_id=rec.id)
        if cached is not None:
            payload = {**cached, "source": "cache", "usage": {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}}
        else:
            payload = _read(rec, store.read_bytes(rec), adapter)
    except ReadFailed as e:
        _finish_failed(rec, e.message, t0)
        return
    except Exception as e:  # a bug or a damaged file: log it, tell the user plainly
        logger.exception("Reading upload %s failed", upload_id)
        _finish_failed(rec, "We could not read this file. Try again, or attach a clearer copy.", t0)
        return
    payload["created_at"] = time.time()
    store.save_extract(rec.id, payload)
    ex = DocumentExtract.from_dict(payload["extract"])
    failed = [c for c in payload["checks"] if c["status"] == "fail"]
    problems = checks_mod.problem_sentences(payload["checks"])
    store.update(rec.id, status="ready", failure_reason=payload.get("note", ""), summary=ex.summary(),
                 checks_failed=len(failed), document_type=ex.document_type, problems=problems)
    store.record_event(rec.id, "read", user_id=rec.user_id, detail={
        "source": payload["source"], "model": payload.get("model"), "document_type": ex.document_type,
        "checks_failed": len(failed), "cost_usd": payload["usage"].get("cost_usd", 0),
        "input_tokens": payload["usage"].get("input_tokens", 0), "output_tokens": payload["usage"].get("output_tokens", 0),
        "seconds": round(time.perf_counter() - t0, 1)})


def _finish_failed(rec: store.UploadRecord, reason: str, t0: float) -> None:
    store.update(rec.id, status="failed", failure_reason=reason)
    store.record_event(rec.id, "read_failed", user_id=rec.user_id,
                       detail={"reason": reason, "seconds": round(time.perf_counter() - t0, 1)})


def _payload(ex: DocumentExtract, source: str, *, model: Optional[str] = None, usage: Optional[Dict[str, Any]] = None,
             note: str = "") -> Dict[str, Any]:
    return {
        "version": EXTRACT_VERSION, "source": source, "model": model, "usage": usage or {"cost_usd": 0.0},
        "extract": ex.to_dict(), "checks": [c.to_dict() for c in checks_mod.run_checks(ex)], "note": note,
    }


def _read(rec: store.UploadRecord, content: bytes, adapter: Optional[Any]) -> Dict[str, Any]:
    kind = rec.kind
    if kind in ("docx", "xlsx", "csv"):
        text = _text_of(kind, content)
        return _payload(DocumentExtract(text=text, document_type=guess_type(text, rec.filename, kind)), "text")
    if kind in ("png", "jpeg", "webp"):
        done = claude_reader.read(content, kind, adapter=adapter)
        return _payload(done.extract, "claude", model=done.model, usage=done.usage, note=_note(done))
    return _read_pdf(content, adapter, rec.filename)


def _text_of(kind: str, content: bytes) -> str:
    try:
        return {"docx": text_reader.docx_text, "xlsx": text_reader.xlsx_text, "csv": text_reader.csv_text}[kind](content)
    except Exception as e:
        logger.info("Text of a %s file could not be read: %s", kind, e)
        raise ReadFailed("This file could not be read. It may be damaged.") from e


def _read_pdf(content: bytes, adapter: Optional[Any], filename: str = "") -> Dict[str, Any]:
    pages = text_reader.pdf_pages(content)
    scanned = text_reader.is_scanned(pages)
    text = text_reader.join_pages(pages)
    if not scanned and not (text_reader.looks_like_bill(text) and settings.upload_extractor == "claude"):
        return _payload(DocumentExtract(text=text, document_type=guess_type(text, filename, "pdf")), "text")  # a contract or report with a text layer
    try:
        done = _read_pdf_parts(content, len(pages), adapter)
    except ReadFailed as e:
        if text:  # the details could not be read, but the words are there
            return _payload(DocumentExtract(text=text, document_type=guess_type(text, filename, "pdf")), "text",
                            note="The details could not be read. Only the text of the file is available.")
        raise
    if not done.extract.text and not done.extract.is_bill and text:
        done.extract.text = text
    return _payload(done.extract, "claude", model=done.model, usage=done.usage, note=_note(done))


def _read_pdf_parts(content: bytes, page_count: int, adapter: Optional[Any]) -> claude_reader.ClaudeRead:
    """One model call for a short PDF; a long one is cut into parts read at the same time and put back together.
    A part that fails fails the file: a statement with a missing stretch must not look complete."""
    size = settings.upload_chunk_pages
    if page_count <= size:
        return _checked(claude_reader.read(content, "pdf", adapter=adapter))
    parts = text_reader.split_pdf(content, size)
    total = parts[-1][1]

    def one(part: Tuple[int, int, bytes]) -> claude_reader.ClaudeRead:
        return claude_reader.read(part[2], "pdf", adapter=adapter, part=(part[0], part[1], total))

    if adapter is None:  # each part has its own adapter, so its tokens and cost count once
        with ThreadPoolExecutor(max_workers=min(len(parts), 4), thread_name_prefix="upload-part") as pool:
            reads = list(pool.map(one, parts))
    else:                # one shared adapter (tests): its usage is already the sum
        reads = [one(p) for p in parts]
    return _checked(merge_reads(reads, shared_usage=adapter is not None))


def _checked(done: claude_reader.ClaudeRead) -> claude_reader.ClaudeRead:
    """A record cut short with nothing in the part that matters is no read at all: say so, do not show it as empty."""
    ex = done.extract
    if done.truncated and ((ex.document_type == "statement" and not ex.statement_lines) or (ex.is_bill and not ex.items)):
        raise ReadFailed("This document is too long to read in one go. Attach fewer pages.")
    return done


def merge_reads(reads: Sequence[claude_reader.ClaudeRead], *, shared_usage: bool = False) -> claude_reader.ClaudeRead:
    """The reads of the parts of one document, as one. Header fields come from the first part that has them, the
    totals and the closing balance from the last, lines and items in page order."""
    exs = [r.extract for r in reads]

    def first(attr: str) -> Any:
        return next((getattr(e, attr) for e in exs if getattr(e, attr) not in (None, "")), None)

    def last(attr: str) -> Any:
        return next((getattr(e, attr) for e in reversed(exs) if getattr(e, attr) is not None), None)

    merged = DocumentExtract(
        document_type=next((e.document_type for e in exs if e.document_type != "other"), "other"),
        vendor_name=first("vendor_name"), vendor_address=first("vendor_address"), vendor_tax_id=first("vendor_tax_id"),
        buyer_name=first("buyer_name"), buyer_tax_id=first("buyer_tax_id"), document_number=first("document_number"),
        date=first("date"), currency=first("currency"), tax_rate_percent=first("tax_rate_percent"),
        subtotal=last("subtotal"), tax_total=last("tax_total"), grand_total=last("grand_total"),
        opening_balance=first("opening_balance"), closing_balance=last("closing_balance"),
        items=[i for e in exs for i in e.items], statement_lines=[ln for e in exs for ln in e.statement_lines],
        text="\n".join(e.text for e in exs if e.text),
        unreadable_fields=sorted({f for e in exs for f in e.unreadable_fields}),
    )
    usage: Dict[str, Any] = {}
    for r in ([reads[-1]] if shared_usage else reads):
        for key, value in r.usage.items():
            if key in ("seconds", "context_tokens"):
                usage[key] = max(usage.get(key, 0), value)
            elif isinstance(value, (int, float)):
                usage[key] = round(usage.get(key, 0) + value, 6)
    return claude_reader.ClaudeRead(merged, reads[0].model, usage, truncated=any(r.truncated for r in reads))


_FIELD_WORDS = {
    "vendor_name": "the supplier's name", "vendor_address": "the supplier's address", "vendor_tax_id": "the supplier's TRN",
    "buyer_name": "the buyer's name", "buyer_tax_id": "the buyer's TRN", "document_number": "the number", "date": "the date",
    "currency": "the currency", "subtotal": "the subtotal", "tax_total": "the tax", "tax_rate_percent": "the tax rate",
    "grand_total": "the total", "items": "some lines", "opening_balance": "the opening balance",
    "closing_balance": "the closing balance", "statement_lines": "some lines",
}


def _plain_field(name: str) -> str:
    """A field the model could not read, in words a person uses. Anything it names that is not a field is kept as it is."""
    return _FIELD_WORDS.get(name) or str(name).replace("_", " ")


def _note(done: Any) -> str:
    if done.truncated:
        return "This document is long. The end of it may be missing."
    if done.extract.unreadable_fields:
        return "Could not read: " + ", ".join(_plain_field(f) for f in done.extract.unreadable_fields[:5]) + "."
    return ""
