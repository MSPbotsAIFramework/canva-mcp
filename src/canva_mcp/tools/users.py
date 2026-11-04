"""Users resource group -- 1 tool."""

from __future__ import annotations

from typing import Callable

from canva_mcp import errors
from canva_mcp.client import CanvaClient
from canva_mcp.errors import CanvaAPIError
from canva_mcp.serialize import to_json

ClientFactory = Callable[[], "CanvaClient | None"]

_DESC = (
    "Get the display name of the connected Canva account.\n"
    "Call this first to confirm the access token works before using other tools."
)


def register(mcp, client_factory: ClientFactory) -> None:
    @mcp.tool(
        description=_DESC,
        annotations={"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    )
    async def canva_get_user_profile() -> str:
        client = client_factory()
        if client is None:
            return errors.not_configured("Missing Canva access token")

        async with client:
            try:
                data = await client.get("/users/me/profile")
            except CanvaAPIError as profile_exc:
                if profile_exc.status_code == 403:
                    # profile:read was not granted to this token -- /users/me
                    # needs no extra scope and still answers "am I connected".
                    try:
                        data = await client.get("/users/me")
                    except CanvaAPIError:
                        return errors.envelope_for(profile_exc)
                else:
                    return errors.envelope_for(profile_exc)

        profile = data.get("profile") if isinstance(data.get("profile"), dict) else data
        team = data.get("team") if isinstance(data.get("team"), dict) else None
        result = {"display_name": profile.get("display_name")}
        if team and team.get("display_name"):
            result["team_name"] = team["display_name"]
        return to_json(result)
