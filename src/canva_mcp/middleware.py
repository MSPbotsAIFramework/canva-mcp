"""Request-level credential isolation (SOP SS3.3/SS3.4).

The Canva access token lives only in a ContextVar, set at the top of the
request and reset in a `finally` block. Nothing in this service ever stores a
token at module scope or in a global -- that is how a concurrent request from
a different tenant would end up using someone else's token (SOP SS3.3: this
is a cross-tenant data leak, not a correctness nitpick).
"""

from __future__ import annotations

from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

CREDENTIAL_HEADER = "X-Canva-Access-Token"

_access_token: ContextVar[str | None] = ContextVar("canva_access_token", default=None)


def get_access_token() -> str | None:
    """Read the current request's token. Only valid while the request that
    set it is still being handled -- never cache this value anywhere."""
    return _access_token.get()


class CredentialMiddleware(BaseHTTPMiddleware):
    """Extracts `X-Canva-Access-Token` for every /mcp request. Missing header
    -> 401 naming the header (SOP SS3.2), not a 500 and not a silent pass-through
    with an empty token."""

    def __init__(self, app: ASGIApp, protected_path: str = "/mcp"):
        super().__init__(app)
        self._protected_path = protected_path

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(self._protected_path):
            return await call_next(request)

        token = request.headers.get(CREDENTIAL_HEADER)
        if not token:
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": f"Missing required header: {CREDENTIAL_HEADER}",
                        "retryable": False,
                    },
                    "missing_headers": [CREDENTIAL_HEADER],
                },
            )

        reset_token = _access_token.set(token)
        try:
            return await call_next(request)
        finally:
            _access_token.reset(reset_token)
