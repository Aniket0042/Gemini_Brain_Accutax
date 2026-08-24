"""errors.py — Error taxonomy and exception → code classification."""
from __future__ import annotations

from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    TENANT_REQUIRED = "TENANT_REQUIRED"
    TENANT_FORBIDDEN = "TENANT_FORBIDDEN"
    TENANT_AMBIGUOUS = "TENANT_AMBIGUOUS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    DB_UNAVAILABLE = "DB_UNAVAILABLE"
    QUERY_FAILED = "QUERY_FAILED"
    SQL_FALLBACK_FAILED = "SQL_FALLBACK_FAILED"
    NO_ROWS = "NO_ROWS"
    INTERNAL_ERROR = "INTERNAL_ERROR"


HTTP_FOR_CODE = {
    ErrorCode.AUTH_REQUIRED: 401,
    ErrorCode.AUTH_EXPIRED: 401,
    ErrorCode.TENANT_REQUIRED: 400,
    ErrorCode.TENANT_FORBIDDEN: 403,
    ErrorCode.TENANT_AMBIGUOUS: 400,
    ErrorCode.VALIDATION_FAILED: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}
#: Everything not listed above is a *degraded success* — HTTP 200.
DEGRADED_HTTP = 200

RETRYABLE = frozenset({
    ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE,
    ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_RATE_LIMITED,
    ErrorCode.DB_UNAVAILABLE, ErrorCode.QUERY_FAILED,
    ErrorCode.SQL_FALLBACK_FAILED, ErrorCode.INTERNAL_ERROR,
})


class AppError(Exception):
    """Carries a user-safe code plus operator-only detail."""

    def __init__(self, code: ErrorCode, detail: str = "", *, cause: Optional[BaseException] = None):
        super().__init__(detail or code.value)
        self.code = code
        self.detail = detail
        self.cause = cause

    @property
    def http_status(self) -> int:
        return HTTP_FOR_CODE.get(self.code, DEGRADED_HTTP)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE


def classify_exception(exc: BaseException) -> ErrorCode:
    """Map an arbitrary exception to an ErrorCode. Never raises."""
    if isinstance(exc, AppError):
        return exc.code

    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "timeout" in name or "timeout" in text or "timed out" in text:
        return ErrorCode.UPSTREAM_TIMEOUT
    if "throttl" in text or "429" in text or "rate limit" in text or "quota" in text or "exhausted" in text:
        return ErrorCode.MODEL_RATE_LIMITED
    if "accessdenied" in name or "accessdenied" in text or "denied" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return ErrorCode.TENANT_FORBIDDEN
    if "operationalerror" in name or "psycopg2" in text or "could not connect" in text:
        return ErrorCode.DB_UNAVAILABLE
    if "programmingerror" in name or "undefinedtable" in name or "syntax error" in text:
        return ErrorCode.QUERY_FAILED
    if "botocore" in text or "bedrock" in text or "clienterror" in name:
        return ErrorCode.MODEL_UNAVAILABLE
    if "connect" in text or "503" in text or "502" in text or "504" in text:
        return ErrorCode.UPSTREAM_UNAVAILABLE
    if "validation" in name or "pydantic" in text:
        return ErrorCode.VALIDATION_FAILED
    return ErrorCode.INTERNAL_ERROR
