"""Designs resource group -- 3 tools: list, get, create."""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from pydantic import Field

from canva_mcp import errors
from canva_mcp.client import CanvaClient
from canva_mcp.errors import CanvaAPIError
from canva_mcp.serialize import clamp_limit, to_json

ClientFactory = Callable[[], "CanvaClient | None"]

_LIST_DESC = (
    "List the caller's Canva designs, optionally filtered by a search query.\n"
    "Supports pagination via the continuation token; results are capped at 50 per call."
)
_GET_DESC = (
    "Get a Canva design's metadata: title, page count, thumbnail, and edit/view URLs.\n"
    "Use the id returned by canva_list_designs or canva_create_design."
)
_CREATE_DESC = (
    "Create a new, empty Canva design of the given type.\n"
    "Calling this twice creates two separate designs; it is not idempotent."
)


def _design_summary(d: dict) -> dict:
    urls = d.get("urls") or {}
    thumbnail = d.get("thumbnail") or {}
    return {
        "id": d.get("id"),
        "title": d.get("title"),
        "thumbnail_url": thumbnail.get("url"),
        "edit_url": urls.get("edit_url"),
        "view_url": urls.get("view_url"),
        "updated_at": d.get("updated_at"),
    }


def register(mcp, client_factory: ClientFactory) -> None:
    @mcp.tool(
        description=_LIST_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_list_designs(
        query: Annotated[str | None, Field(description="Free-text search over design titles; omit to list all.")] = None,
        ownership: Literal["owned", "shared", "any"] = "any",
        limit: Annotated[int | None, Field(description="Max designs to return, default 20, hard cap 50.")] = 20,
        continuation: Annotated[
            str | None, Field(description="Continuation token from a previous response, to fetch the next page.")
        ] = None,
    ) -> str:
        if ownership not in ("owned", "shared", "any"):
            return errors.invalid_argument(f"ownership must be one of owned, shared, any; got {ownership!r}")

        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        capped = clamp_limit(limit)
        async with client:
            try:
                data = await client.get(
                    "/designs",
                    params={"query": query, "ownership": ownership, "continuation": continuation},
                )
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        items = data.get("items", [])[:capped]
        return to_json(
            {
                "designs": [_design_summary(d) for d in items],
                "continuation": data.get("continuation"),
            }
        )

    @mcp.tool(
        description=_GET_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_get_design(design_id: str) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                design = await client.get(f"/designs/{design_id}")
                pages = await client.get(f"/designs/{design_id}/pages")
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        d = design.get("design", design)
        summary = _design_summary(d)
        page_items = pages.get("items", [])
        summary["page_count"] = len(page_items) if page_items else pages.get("total")
        return to_json(summary)

    @mcp.tool(
        description=_CREATE_DESC,
        annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    )
    async def canva_create_design(
        design_type: Annotated[
            str, Field(description='A Canva design-type preset, e.g. "presentation", "doc", "instagram_post_square".')
        ],
        title: str | None = None,
    ) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                data = await client.post(
                    "/designs",
                    json_body={"design_type": {"type": "preset", "name": design_type}, "title": title},
                )
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        d = data.get("design", data)
        urls = d.get("urls") or {}
        return to_json({"id": d.get("id"), "edit_url": urls.get("edit_url")})
