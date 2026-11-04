"""Exports resource group -- 2 tools: create+poll, and a one-shot status read."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Callable, Literal

from pydantic import Field

from canva_mcp import errors
from canva_mcp.client import CanvaClient
from canva_mcp.errors import CanvaAPIError
from canva_mcp.serialize import to_json

ClientFactory = Callable[[], "CanvaClient | None"]

# Internal poll loop budget: must leave room under the client's own ~110s
# call budget (SOP SS5 <120s), so an export that is genuinely still running
# comes back as "in_progress" with an id instead of the call itself timing out.
POLL_BUDGET_SECONDS = 90.0
POLL_INTERVAL_SECONDS = 2.0

_EXPORT_DESC = (
    "Export a Canva design to a file and return download URL(s).\n"
    "If still running when the internal wait budget runs out, returns an export_id "
    "with status in_progress; follow up with canva_get_export_status."
)
_STATUS_DESC = (
    "Check the status of an export job started by canva_export_design.\n"
    "Returns in_progress, success (with download URLs), or failed."
)


def _export_result(job: dict) -> dict:
    export = job.get("job", job)
    status = export.get("status")
    result: dict = {"export_id": export.get("id"), "status": status}
    if status == "success":
        result["urls"] = export.get("urls") or []
    return result


def register(mcp, client_factory: ClientFactory) -> None:
    @mcp.tool(
        description=_EXPORT_DESC,
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    async def canva_export_design(
        design_id: str,
        format: Literal["pdf", "png", "jpg", "pptx", "gif", "mp4", "csv"] = "pdf",
        pages: Annotated[
            list[int] | None, Field(description="1-based page numbers to export; omit for all pages.")
        ] = None,
    ) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                created = await client.post(
                    "/exports",
                    json_body={"design_id": design_id, "format": {"type": format}, "pages": pages},
                )
            except CanvaAPIError as e:
                return errors.envelope_for(e)

            result = _export_result(created)
            deadline = time.monotonic() + POLL_BUDGET_SECONDS
            while result["status"] not in ("success", "failed") and time.monotonic() < deadline:
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                try:
                    polled = await client.get(f"/exports/{result['export_id']}")
                except CanvaAPIError as e:
                    return errors.envelope_for(e)
                result = _export_result(polled)

        if result["status"] == "failed":
            return errors.envelope(errors.UPSTREAM_ERROR, f"Canva export {result['export_id']} failed", retryable=False)
        return to_json(result)

    @mcp.tool(
        description=_STATUS_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_get_export_status(export_id: str) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                job = await client.get(f"/exports/{export_id}")
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        return to_json(_export_result(job))
