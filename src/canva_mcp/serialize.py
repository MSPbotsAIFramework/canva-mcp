"""Response shaping shared by every tool (SOP SS2.5 token-economy rules)."""

from __future__ import annotations

import json
from typing import Any

MAX_RESPONSE_CHARS = 20_000
DEFAULT_LIMIT = 20
HARD_CAP_LIMIT = 50  # Tech design SS"Decisions made by DEV01": smaller than the SOP's
# 200 ceiling because every Canva design record already carries a thumbnail
# URL plus two link URLs.


def clamp_limit(limit: int | None) -> int:
    if limit is None or limit <= 0:
        return DEFAULT_LIMIT
    return min(limit, HARD_CAP_LIMIT)


def to_json(payload: Any) -> str:
    """Compact, non-ASCII-escaped JSON -- `indent=2` and `ensure_ascii=True`
    are both pure token waste (SOP SS2.5)."""
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if len(text) <= MAX_RESPONSE_CHARS:
        return text
    # Should not happen given the limits above, but never silently truncate
    # JSON mid-structure -- fail loudly into the upstream_error shape instead
    # of handing the agent invalid JSON.
    from canva_mcp.errors import envelope, UPSTREAM_ERROR

    return envelope(
        UPSTREAM_ERROR,
        f"Response exceeded the {MAX_RESPONSE_CHARS}-character limit before truncation logic "
        "could run; retry with a smaller limit.",
        retryable=True,
    )
