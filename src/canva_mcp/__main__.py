"""Entry point: run the Canva MCP server over Streamable HTTP."""

from __future__ import annotations

import uvicorn

from canva_mcp.config import get_settings
from canva_mcp.server import create_app


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port, log_level="info")


if __name__ == "__main__":
    main()
