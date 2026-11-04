"""Missing-Header 401 (AC2 / TC2) and request-level ContextVar isolation
(AC12 / TC12). The isolation test exercises `_access_token` directly with two
concurrent fake requests, the same shape the tech design's test plan
describes -- this is what would catch a regression to a module-level global
(SOP SS3.3), which a full HTTP round-trip through TestClient's blocking
portal cannot reliably force into an interleaved schedule.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from canva_mcp.middleware import CREDENTIAL_HEADER, _access_token, get_access_token
from tests.conftest import make_client, rpc


def test_missing_header_returns_401_listing_required_header():
    client = make_client(lambda request: httpx.Response(500))
    with client:
        resp = rpc(client, "tools/list", token=None)

    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert CREDENTIAL_HEADER in body["missing_headers"]
    assert CREDENTIAL_HEADER in body["error"]["message"]


@pytest.mark.asyncio
async def test_contextvar_token_is_isolated_across_concurrent_requests():
    seen: dict[str, tuple[str | None, str | None]] = {}

    async def fake_request(name: str, token: str, before_read_delay: float, after_read_delay: float) -> None:
        reset = _access_token.set(token)
        try:
            await asyncio.sleep(before_read_delay)
            mid = get_access_token()
            await asyncio.sleep(after_read_delay)
            seen[name] = (mid, get_access_token())
        finally:
            _access_token.reset(reset)

    # Interleaved delays: A reads while B is mid-sleep and vice versa, so a
    # shared global (instead of a ContextVar) would have A observe B's token.
    await asyncio.gather(
        fake_request("A", "token-A", 0.01, 0.02),
        fake_request("B", "token-B", 0.02, 0.01),
    )

    assert seen["A"] == ("token-A", "token-A")
    assert seen["B"] == ("token-B", "token-B")
    # Each coroutine reset its own token; nothing leaks to the caller's context.
    assert get_access_token() is None
