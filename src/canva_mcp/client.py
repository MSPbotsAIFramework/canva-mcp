"""Thin async wrapper around the Canva Connect REST API (SOP SS5).

One CanvaClient = one httpx.AsyncClient, created once per tool call and reused
for every outbound Canva request that single tool call makes (e.g.
canva_get_design calls two endpoints) -- never a new client per HTTP call, and
never a client shared across requests (that would share connection state
across tenants).
"""

from __future__ import annotations

import asyncio
import time
from types import TracebackType

import httpx

from canva_mcp.errors import CanvaAPIError

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 30.0
# SOP SS5: single tool call, including retries, must stay under 120s.
CALL_BUDGET_SECONDS = 110.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.0


class CanvaClient:
    def __init__(self, token: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT, write=READ_TIMEOUT, pool=READ_TIMEOUT),
            transport=transport,
        )
        self._deadline = time.monotonic() + CALL_BUDGET_SECONDS

    async def __aenter__(self) -> "CanvaClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _time_left(self) -> float:
        return self._deadline - time.monotonic()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        """Issue one logical Canva call with bounded retry+backoff on 429/5xx.
        Raises CanvaAPIError for any non-2xx response or transport failure."""
        attempt = 0
        last_exc: CanvaAPIError | None = None
        while True:
            if self._time_left() <= 0:
                raise last_exc or CanvaAPIError("Canva call exceeded its time budget", status_code=None)

            retry_after: float | None = None
            try:
                resp = await self._client.request(method, path, headers=self._headers(), **kwargs)
            except httpx.TimeoutException as e:
                last_exc = CanvaAPIError(f"Timed out calling Canva: {e}", status_code=None)
            except httpx.TransportError as e:
                last_exc = CanvaAPIError(f"Network error calling Canva: {e}", status_code=None)
            else:
                if resp.status_code < 400:
                    if not resp.content:
                        return {}
                    return resp.json()
                last_exc = CanvaAPIError(_summarize(resp), status_code=resp.status_code)
                if resp.status_code != 429 and resp.status_code < 500:
                    # 4xx other than 429 is not retryable.
                    raise last_exc
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))

            attempt += 1
            if attempt > MAX_RETRIES:
                raise last_exc
            delay = retry_after if retry_after else BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            delay = min(delay, max(self._time_left(), 0))
            if delay <= 0:
                raise last_exc
            await asyncio.sleep(delay)

    async def get(self, path: str, params: dict | None = None) -> dict:
        return await self.request("GET", path, params=_clean(params))

    async def post(self, path: str, json_body: dict | None = None) -> dict:
        return await self.request("POST", path, json=_clean(json_body))


def _clean(d: dict | None) -> dict | None:
    if d is None:
        return None
    return {k: v for k, v in d.items() if v is not None}


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _summarize(resp: httpx.Response) -> str:
    # Never forward Canva's raw body verbatim (SOP SS4.2: no sensitive data in
    # message) -- just enough to debug which call failed and why.
    return f"Canva {resp.request.method} {resp.request.url.path} returned {resp.status_code}"
