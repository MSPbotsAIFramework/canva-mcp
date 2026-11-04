"""Shared test helpers: drive the real ASGI app with a mocked Canva transport
instead of calling tool functions directly, so every test exercises the same
middleware -> ContextVar -> CanvaClient path a real request would."""

from __future__ import annotations

import json
from typing import Callable

import httpx
from starlette.testclient import TestClient

from canva_mcp.server import create_app

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def make_client(handler: Callable[[httpx.Request], httpx.Response]) -> TestClient:
    app = create_app(transport=httpx.MockTransport(handler))
    return TestClient(app)


def _sse_payload(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise AssertionError(f"no 'data:' line found in response body: {text!r}")


def rpc(client: TestClient, method: str, params: dict | None = None, token: str | None = "test-token"):
    headers = dict(MCP_HEADERS)
    if token is not None:
        headers["X-Canva-Access-Token"] = token
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers=headers,
    )
    return resp


def call_tool(client: TestClient, name: str, arguments: dict | None = None, token: str | None = "test-token"):
    return rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, token=token)


def tool_text(resp: httpx.Response) -> str:
    envelope = _sse_payload(resp.text)
    assert "result" in envelope, f"expected a tool result, got: {envelope}"
    return envelope["result"]["content"][0]["text"]


def tool_json(resp: httpx.Response) -> dict:
    return json.loads(tool_text(resp))


def rpc_result(resp: httpx.Response) -> dict:
    return _sse_payload(resp.text)["result"]
