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


def test_list_designs_forwards_capped_limit_to_canva():
    # PRD-21299 review round 1, must-fix 1: `limit` must be forwarded to Canva so
    # Canva's own page boundary -- and therefore its `continuation` token -- matches
    # what this service hands back. Without this, items between the capped count and
    # the end of Canva's actual page are silently unreachable via `continuation`.
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [], "continuation": "next-page-token"})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_list_designs", {"limit": 10})
        result = tool_json(resp)

        resp_default = call_tool(client, "canva_list_designs", {})
        result_default = tool_json(resp_default)

        resp_over_cap = call_tool(client, "canva_list_designs", {"limit": 999})
        result_over_cap = tool_json(resp_over_cap)

    assert requests[0].url.params["limit"] == "10"
    assert requests[1].url.params["limit"] == "20", "default limit (clamp_limit's DEFAULT_LIMIT) must be forwarded"
    assert requests[2].url.params["limit"] == "50", "limit above the hard cap must be clamped before forwarding"
    assert result["continuation"] == "next-page-token"
    assert result_default["continuation"] == "next-page-token"
    assert result_over_cap["continuation"] == "next-page-token"


def test_list_designs_returns_every_item_canva_sent_for_the_forwarded_limit():
    # Regression for the silent-drop bug: once Canva's page is bounded by the
    # forwarded `limit`, every item in that (correctly-sized) page must survive
    # to the caller -- not just a subset -- and the continuation must pass through
    # unchanged, since it is now valid for the next call.
    items = [{"id": f"d{i}", "title": f"Design {i}"} for i in range(10)]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["limit"] == "10"
        return httpx.Response(200, json={"items": items, "continuation": "page-2"})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_list_designs", {"limit": 10})
        result = tool_json(resp)

    assert len(result["designs"]) == 10
    assert [d["id"] for d in result["designs"]] == [f"d{i}" for i in range(10)]
    assert result["continuation"] == "page-2"


def test_list_folder_items_forwards_capped_limit_to_canva():
    # Same bug and same fix as canva_list_designs (PRD-21299 review round 1, must-fix 1).
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"items": [], "continuation": "next-page-token"})

    client = make_client(handler)
    with client:
        resp = call_tool(client, "canva_list_folder_items", {"limit": 5})
        result = tool_json(resp)

        resp_over_cap = call_tool(client, "canva_list_folder_items", {"limit": 999})
        result_over_cap = tool_json(resp_over_cap)

    assert requests[0].url.params["limit"] == "5"
    assert requests[1].url.params["limit"] == "50", "limit above the hard cap must be clamped before forwarding"
    assert result["continuation"] == "next-page-token"
    assert result_over_cap["continuation"] == "next-page-token"


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
