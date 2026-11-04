"""Folders resource group -- 1 tool: list items in a folder."""

from __future__ import annotations

from typing import Annotated, Callable, Literal

from pydantic import Field

from canva_mcp import errors
from canva_mcp.client import CanvaClient
from canva_mcp.errors import CanvaAPIError
from canva_mcp.serialize import clamp_limit, to_json

ClientFactory = Callable[[], "CanvaClient | None"]

_DESC = (
    'List the contents of a Canva folder.\n'
    'Use folder_id "root" for the top-level Projects folder (default) or "uploads" for the Uploads folder.'
)


def _item_summary(item: dict) -> dict:
    thumbnail = item.get("thumbnail") or {}
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "title": item.get("title") or item.get("name"),
        "thumbnail_url": thumbnail.get("url"),
    }


def register(mcp, client_factory: ClientFactory) -> None:
    @mcp.tool(
        description=_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_list_folder_items(
        folder_id: Annotated[
            str, Field(description='Folder id, or the reserved ids "root" or "uploads".')
        ] = "root",
        item_types: Annotated[
            list[Literal["design", "folder", "image"]] | None,
            Field(description="Optional filter: any of design, folder, image."),
        ] = None,
        limit: Annotated[int | None, Field(description="Max items to return, default 20, hard cap 50.")] = 20,
        continuation: Annotated[
            str | None, Field(description="Continuation token from a previous response, to fetch the next page.")
        ] = None,
    ) -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        capped = clamp_limit(limit)
        async with client:
            try:
                data = await client.get(
                    f"/folders/{folder_id}/items",
                    params={
                        "item_types": ",".join(item_types) if item_types else None,
                        "continuation": continuation,
                    },
                )
            except CanvaAPIError as e:
                return errors.envelope_for(e)

        items = data.get("items", [])[:capped]
        return to_json(
            {
                "items": [_item_summary(i) for i in items],
                "continuation": data.get("continuation"),
            }
        )
