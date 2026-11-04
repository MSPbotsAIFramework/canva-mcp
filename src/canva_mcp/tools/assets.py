"""Assets resource group -- 2 tools: upload-from-URL (create+poll), and get."""

from __future__ import annotations

import asyncio
import time
from typing import Callable

from canva_mcp import errors
from canva_mcp.client import CanvaClient
from canva_mcp.errors import CanvaAPIError
from canva_mcp.serialize import to_json

ClientFactory = Callable[[], "CanvaClient | None"]

POLL_BUDGET_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 2.0

_UPLOAD_DESC = (
    "Upload a publicly reachable image or video URL as a new Canva asset.\n"
    "Only URL-based uploads are supported, not raw file bytes. If still running "
    "when the wait budget runs out, returns a job_id with status in_progress."
)
_GET_DESC = (
    "Get a Canva asset's metadata: name, thumbnail, and type.\n"
    "Use the asset_id returned by canva_upload_asset_from_url."
)


def _asset_summary(a: dict) -> dict:
    thumbnail = a.get("thumbnail") or {}
    return {"id": a.get("id"), "name": a.get("name"), "thumbnail_url": thumbnail.get("url"), "type": a.get("type")}


def _job_result(job: dict) -> dict:
    inner = job.get("job", job)
    status = inner.get("status")
    if status == "success" and inner.get("asset"):
        asset = _asset_summary(inner["asset"])
        asset["status"] = "success"
        return asset
    return {"job_id": inner.get("id"), "status": status}


def register(mcp, client_factory: ClientFactory) -> None:
    @mcp.tool(
        description=_UPLOAD_DESC,
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    async def canva_upload_asset_from_url(url: str, name: str | None = None) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                created = await client.post("/asset-uploads", json_body={"url": url, "name": name})
            except CanvaAPIError as e:
                return errors.envelope_for(e)

            result = _job_result(created)
            job_id = created.get("job", created).get("id")
            deadline = time.monotonic() + POLL_BUDGET_SECONDS
            while result.get("status") not in ("success", "failed") and time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    polled = await client.get(f"/asset-uploads/{job_id}")
                except CanvaAPIError as e:
                    return errors.envelope_for(e)
                result = _job_result(polled)

        if result.get("status") == "failed":
            return errors.envelope(errors.UPSTREAM_ERROR, "Canva asset upload job failed", retryable=False)
        return to_json(result)

    @mcp.tool(
        description=_GET_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_get_asset(asset_id: str) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                data = await client.get(f"/assets/{asset_id}")
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        return to_json(_asset_summary(data.get("asset", data)))
