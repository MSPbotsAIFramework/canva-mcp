"""Structured error envelope for every tool (SOP SS4).

Tools never raise. They catch CanvaAPIError (and anything unexpected) and
return the JSON-serialized envelope below instead, so the agent can branch on
`error.code` and `error.retryable` without parsing a stack trace.
"""

from __future__ import annotations

import json

# Fixed, finite vocabulary (SOP SS4.2) -- do not invent new codes.
NOT_CONFIGURED = "not_configured"
UNAUTHORIZED = "unauthorized"
NOT_FOUND = "not_found"
INVALID_ARGUMENT = "invalid_argument"
RATE_LIMITED = "rate_limited"
UPSTREAM_ERROR = "upstream_error"

_RETRYABLE = {RATE_LIMITED, UPSTREAM_ERROR}


class CanvaAPIError(Exception):
    """Raised by client.py when Canva's REST API returns a non-2xx response,
    or when the request fails before a response is received (timeout,
    connection error). `status_code` is None for the latter case."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def code_for_status(status_code: int | None) -> str:
    """Map a Canva HTTP status (or None for a transport failure) to this
    service's fixed error code vocabulary (SOP SS4.2 table)."""
    if status_code is None:
        return UPSTREAM_ERROR
    if status_code == 401 or status_code == 403:
        return UNAUTHORIZED
    if status_code == 404:
        return NOT_FOUND
    if status_code == 429:
        return RATE_LIMITED
    if status_code in (400, 422):
        return INVALID_ARGUMENT
    if status_code >= 500:
        return UPSTREAM_ERROR
    return UPSTREAM_ERROR


def envelope(code: str, message: str, retryable: bool | None = None) -> str:
    """Build the JSON-string error envelope every tool returns on failure."""
    if retryable is None:
        retryable = code in _RETRYABLE
    return json.dumps(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def envelope_for(exc: CanvaAPIError) -> str:
    code = code_for_status(exc.status_code)
    # Never echo Canva's raw response body back -- it could carry data that
    # does not belong to the calling tenant's account; exc.message is already
    # a short, sanitized summary built in client.py.
    return envelope(code, exc.message)


def not_configured(message: str) -> str:
    return envelope(NOT_CONFIGURED, message, retryable=False)


def invalid_argument(message: str) -> str:
    return envelope(INVALID_ARGUMENT, message, retryable=False)
