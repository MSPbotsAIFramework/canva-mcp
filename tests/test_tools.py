"""tools/list snapshot + error-mapping unit tests (PRD-21299 .ac.json TC1, TC4,
TC6, TC13, TC14). No real Canva account needed -- the Canva side is an
httpx.MockTransport, so these run offline and fast."""

from __future__ import annotations

import httpx

from canva_mcp import client as client_module
from tests.conftest import call_tool, make_client, rpc, rpc_result, tool_json

EXPECTED_TOOL_NAMES = {
    "canva_get_user_profile",
    "canva_list_designs",
    "canva_get_design",
    "canva_create_design",
    "canva_export_design",
    "canva_get_export_status",
    "canva_upload_asset_from_url",
    "canva_get_asset",
    "canva_list_folder_items",
}


def test_tools_list_matches_the_nine_tool_contract():
    # Transport is never used by tools/list -- no Canva call happens.
    client = make_client(lambda request: httpx.Response(500))
    with client:
        resp = rpc(client, "tools/list")
        tools = rpc_result(resp)["tools"]

    names = {t["name"] for t in tools}
    assert names == EXPECTED_TOOL_NAMES

    for tool in tools:
        description = tool["description"]
        first_line = description.splitlines()[0]
        assert len(first_line) <= 100, f"{tool['name']}: first line too long ({len(first_line)} chars)"
        assert len(description) <= 500, f"{tool['name']}: description too long ({len(description)} chars)"


def test_list_designs_returns_empty_list_when_canva_has_no_matches():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/designs")
        return httpx.Response(200, json={"items": [], "continuation": None})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_list_designs", {"query": "no-such-design-xyz"})
        result = tool_json(resp)

    assert result == {"designs": [], "continuation": None}
    assert "error" not in result


def test_get_design_maps_canva_404_to_structured_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "not_found", "message": "design does not exist"})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_get_design", {"design_id": "does-not-exist"})
        result = tool_json(resp)

    assert result["error"]["code"] == "not_found"
    assert result["error"]["retryable"] is False
    # Canva's raw body must never be forwarded verbatim (SOP SS4.2).
    assert "design does not exist" not in result["error"]["message"]


def test_canva_401_is_surfaced_as_unauthorized_without_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, json={"message": "token expired"})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_get_user_profile")
        result = tool_json(resp)

    assert result["error"]["code"] == "unauthorized"
    assert result["error"]["retryable"] is False
    assert len(calls) == 1, "a 401 must not be retried"


def test_retries_on_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"profile": {"display_name": "Ada"}})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_get_user_profile")
        result = tool_json(resp)

    assert result == {"display_name": "Ada"}
    assert len(calls) == 3


def test_returns_structured_upstream_error_when_5xx_exhausts_budget(monkeypatch):
    monkeypatch.setattr(client_module.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(client_module, "CALL_BUDGET_SECONDS", 0.05)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_get_user_profile")
        result = tool_json(resp)

    assert result["error"]["code"] == "upstream_error"
    assert result["error"]["retryable"] is True
    assert len(calls) >= 1


async def _no_sleep(_seconds: float) -> None:
    return None
