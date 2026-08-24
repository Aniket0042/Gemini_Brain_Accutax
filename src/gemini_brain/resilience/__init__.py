"""resilience — outcome classification, user-safe copy, and response envelopes."""
from .outcomes import Outcome, Retrieved, classify_payload
from .errors import ErrorCode, AppError, HTTP_FOR_CODE, RETRYABLE, classify_exception
from .messages import notice_for, NOTICES
from .envelope import (
    build_notice, build_success, build_empty, build_degraded, normalize_envelope, new_request_id,
)
from .output_guard import sanitize_answer, looks_like_backend_leak

__all__ = [
    "Outcome", "Retrieved", "classify_payload",
    "ErrorCode", "AppError", "HTTP_FOR_CODE", "RETRYABLE", "classify_exception",
    "notice_for", "NOTICES",
    "build_notice", "build_success", "build_empty", "build_degraded", "normalize_envelope",
    "new_request_id",
    "sanitize_answer", "looks_like_backend_leak",
]
