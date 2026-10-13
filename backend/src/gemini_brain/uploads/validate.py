"""Checks on an uploaded file, made on its bytes, never on its name or the type the browser claims.

`inspect()` decides the kind (PDF, image, DOCX, XLSX, CSV), counts pages, and refuses what the feature must
not handle: wrong or mixed-up types, files over the limits, password-protected PDFs, files with macros, and
decompression bombs. Every refusal is an `UploadRejected` with a code the API turns into an HTTP status and
a plain message for the user.
"""
from __future__ import annotations

import io
import logging
import math
import re
import zipfile
from dataclasses import dataclass
from typing import Dict, Tuple

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.uploads.validate")

#: kind -> (mime type served for it, extension stored with it)
KINDS: Dict[str, Tuple[str, str]] = {
    "pdf": ("application/pdf", ".pdf"),
    "png": ("image/png", ".png"),
    "jpeg": ("image/jpeg", ".jpg"),
    "webp": ("image/webp", ".webp"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "csv": ("text/csv", ".csv"),
}
#: Kinds a model reads page by page; only these count against the daily page cap.
MODEL_READ_KINDS = ("pdf", "png", "jpeg", "webp")
INLINE_KINDS = ("pdf", "png", "jpeg", "webp")  # safe to show in the browser; the rest download

MAX_PIXELS = 60_000_000
MAX_ZIP_ENTRIES = 2_000
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 200_000          # DOCX
MAX_SHEETS = 10                   # XLSX
MAX_ROWS = 5_000                  # per XLSX sheet, and CSV
CHARS_PER_PAGE = 3_000            # a DOCX counts as this many characters per page

_STATUS = {"too_large": 413, "unsupported_type": 415}  # every other code is 422


class UploadRejected(ValueError):
    """The file cannot be accepted. `code` is stable for clients; `message` is for the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self) -> int:
        return _STATUS.get(self.code, 422)


@dataclass(frozen=True)
class Inspected:
    kind: str
    mime: str
    ext: str
    pages: int            # real pages for PDFs and images; an estimate for DOCX; 1 for sheets and CSV
    counted_pages: int    # what counts against the daily cap

    @property
    def model_read(self) -> bool:
        return self.kind in MODEL_READ_KINDS


def accepted_types() -> Dict[str, str]:
    """Extension -> mime for the file picker's accept list."""
    out = {ext: mime for mime, ext in KINDS.values()}
    out[".jpeg"] = "image/jpeg"
    return out


def clean_filename(name: str, kind: str) -> str:
    """The name shown to the user: no path, no control characters, at most 120 characters, right extension."""
    base = re.split(r"[\\/]", str(name or ""))[-1]
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", base).strip(" .")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = (stem or "document")[:110].strip(" .") or "document"
    return stem + KINDS[kind][1]


def inspect(content: bytes, filename: str = "") -> Inspected:
    """Decide what the bytes are and check them against every limit. Raises UploadRejected."""
    if not content:
        raise UploadRejected("empty", "This file is empty.")
    if len(content) > settings.upload_max_bytes:
        raise UploadRejected(
            "too_large",
            f"This file is {len(content) / 1_048_576:.1f} MB. The limit is {settings.upload_max_bytes // 1_048_576} MB.",
        )
    head = content[:1024]
    if b"%PDF-" in head:
        return _pdf(content)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return _image(content, "png", "PNG")
    if head.startswith(b"\xff\xd8\xff"):
        return _image(content, "jpeg", "JPEG")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return _image(content, "webp", "WEBP")
    if head.startswith(b"PK\x03\x04"):
        return _zip(content)
    if str(filename or "").lower().endswith(".csv"):
        return _csv(content)
    raise UploadRejected(
        "unsupported_type",
        "This file type is not supported. Attach a PDF, a photo (JPEG, PNG or WebP), a Word file (.docx), "
        "an Excel file (.xlsx) or a CSV file.",
    )


def _build(kind: str, pages: int) -> Inspected:
    mime, ext = KINDS[kind]
    return Inspected(kind, mime, ext, pages, pages if kind in MODEL_READ_KINDS else 0)


def _pdf(content: bytes) -> Inspected:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise UploadRejected("encrypted", "This PDF is password-protected. Remove the password and attach it again.")
        pages = len(reader.pages)
    except UploadRejected:
        raise
    except Exception as e:  # pypdf raises many types for a damaged file
        logger.info("Unreadable PDF upload: %s", e)
        raise UploadRejected("unreadable", "This PDF could not be opened. It may be damaged.") from e
    if pages < 1:
        raise UploadRejected("unreadable", "This PDF has no pages.")
    if pages > settings.upload_max_pages:
        raise UploadRejected(
            "too_many_pages", f"This PDF has {pages} pages. The limit is {settings.upload_max_pages}.")
    return _build("pdf", pages)


def _image(content: bytes, kind: str, pil_format: str) -> Inspected:
    from PIL import Image

    try:
        with Image.open(io.BytesIO(content)) as im:
            if im.format != pil_format:
                raise UploadRejected("unreadable", "This image could not be read.")
            width, height = im.size
            if width * height > MAX_PIXELS:  # header only: nothing is decoded yet
                raise UploadRejected(
                    "image_too_large", f"This image is {width}x{height} pixels, which is too large. Use a smaller photo.")
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
    except UploadRejected:
        raise
    except Exception as e:  # damaged data, truncated file, decompression bomb
        logger.info("Unreadable image upload: %s", e)
        raise UploadRejected("unreadable", "This image could not be read. It may be damaged.") from e
    return _build(kind, 1)


def _zip(content: bytes) -> Inspected:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            infos = z.infolist()
            names = {i.filename for i in infos}
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise UploadRejected("too_large", "This file unpacks to far more data than its size suggests, so it was refused.")
            if any(n.lower().endswith("vbaproject.bin") for n in names):
                raise UploadRejected("macros", "Files with macros are not accepted. Save a copy without macros.")
            if "word/document.xml" in names:
                return _docx(content)
            if "xl/workbook.xml" in names:
                return _xlsx(content)
    except UploadRejected:
        raise
    except zipfile.BadZipFile as e:
        raise UploadRejected("unreadable", "This file could not be opened. It may be damaged.") from e
    raise UploadRejected(
        "unsupported_type",
        "This file type is not supported. Attach a PDF, a photo (JPEG, PNG or WebP), a Word file (.docx), "
        "an Excel file (.xlsx) or a CSV file.",
    )


def _docx(content: bytes) -> Inspected:
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
        chars = 0
        for paragraph in document.paragraphs:
            chars += len(paragraph.text)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    chars += len(cell.text)
            if chars > MAX_TEXT_CHARS:
                break
    except Exception as e:
        logger.info("Unreadable DOCX upload: %s", e)
        raise UploadRejected("unreadable", "This Word file could not be opened. It may be damaged.") from e
    if chars > MAX_TEXT_CHARS:
        raise UploadRejected("too_much_text", f"This document has more than {MAX_TEXT_CHARS:,} characters. Attach a shorter one.")
    return _build("docx", max(1, math.ceil(chars / CHARS_PER_PAGE)))


def _xlsx(content: bytes) -> Inspected:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        try:
            if len(workbook.sheetnames) > MAX_SHEETS:
                raise UploadRejected("too_much_text", f"This workbook has more than {MAX_SHEETS} sheets.")
            for sheet in workbook.worksheets:
                for count, _ in enumerate(sheet.iter_rows(values_only=True), start=1):
                    if count > MAX_ROWS:
                        raise UploadRejected(
                            "too_much_text", f"Sheet '{sheet.title}' has more than {MAX_ROWS:,} rows. Attach fewer rows.")
        finally:
            workbook.close()
    except UploadRejected:
        raise
    except Exception as e:
        logger.info("Unreadable XLSX upload: %s", e)
        raise UploadRejected("unreadable", "This Excel file could not be opened. It may be damaged.") from e
    return _build("xlsx", 1)


def _csv(content: bytes) -> Inspected:
    if b"\x00" in content:
        raise UploadRejected("unsupported_type", "This does not look like a CSV text file.")
    try:
        content.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise UploadRejected("unreadable", "This CSV is not UTF-8 text. Save it as CSV UTF-8 and attach it again.") from e
    if content.count(b"\n") + 1 > MAX_ROWS:
        raise UploadRejected("too_much_text", f"This CSV has more than {MAX_ROWS:,} rows. Attach fewer rows.")
    return _build("csv", 1)
