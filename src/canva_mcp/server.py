"""FastMCP factory: wires the Canva tools, the credential middleware, and the
pure-local /health probe into one ASGI app (PRD-21299 tech design SS"Change list").
"""

from __future__ import annotations

import httpx
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from canva_mcp.client import CanvaClient
from canva_mcp.config import Settings, get_settings
from canva_mcp.middleware import CredentialMiddleware, get_access_token
from canva_mcp.tools import assets, designs, exports, folders, users

INSTRUCTIONS = (
    "Canva connector: browse, create, and export the caller's Canva designs, "
    "upload assets, and list folder contents. All 9 tools act on the single "
    "Canva account tied to the access token supplied on this request.\n\n"
    "Typical flows:\n"
    "- Browse: canva_list_designs -> canva_get_design for details.\n"
    "- Export: canva_export_design (returns URLs, or an export_id if still "
    "running) -> canva_get_export_status to poll a pending export.\n"
    "- Add media: canva_upload_asset_from_url -> canva_get_asset to confirm.\n"
    "- Explore structure: canva_list_folder_items (root or uploads).\n\n"
    "This connector cannot edit design content, cannot delete or update "
    "existing designs/assets, and only accepts asset uploads by URL (not raw "
    "file bytes) -- these are not implemented."
)


async def health(_request) -> JSONResponse:
    # Pure local probe -- never calls Canva (SOP SS1.1): the Canva API being
    # slow or down must not flip this container's health check.
    return JSONResponse({"status": "ok"})


def build_client_factory(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    """`transport` is test-only: it lets tests point every outbound Canva call
    at an in-memory httpx.MockTransport instead of the real network. Production
    callers never pass it, so CanvaClient builds its normal connection pool."""

    def factory() -> CanvaClient | None:
        token = get_access_token()
        if not token:
            return None
        return CanvaClient(token=token, base_url=settings.canva_api_base_url, transport=transport)

    return factory


def create_mcp_server(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastMCP:
    settings = settings or get_settings()
    mcp = FastMCP(
        name="canva-mcp",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        # The gateway sits between the AI client and this service; a DNS-
        # rebinding check on the Host header it forwards would reject every
        # request with 421 (SOP SS7). The service is never exposed directly.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    client_factory = build_client_factory(settings, transport=transport)
    for module in (users, designs, exports, assets, folders):
        module.register(mcp, client_factory)

    return mcp


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> Starlette:
    settings = settings or get_settings()
    mcp = create_mcp_server(settings, transport=transport)
    mcp_app = mcp.streamable_http_app()

    # Mount() does not run a sub-app's lifespan on its own (SOP SS7) -- the
    # MCP session manager's task group would never start, and every /mcp
    # request would fail with "Task group is not initialized" while /health
    # kept returning 200. Passing the sub-app's lifespan to the outer app
    # explicitly is the fix.
    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[Middleware(CredentialMiddleware)],
        lifespan=mcp_app.router.lifespan_context,
    )
