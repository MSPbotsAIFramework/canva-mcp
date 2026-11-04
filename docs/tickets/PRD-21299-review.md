# PRD-21299 · Code review

Status: frozen @ a4f9d63

## Round 2 · 2026-11-04 · reviewed dev/PRD-21299 @ a4f9d63 (base main @ e828ad9)

Verdict: approved

### Round 1 must-fix reconciliation

1. **Must-fix 1 (pagination/continuation mismatch)** — CONFIRMED RESOLVED.
   `src/canva_mcp/tools/designs.py:66-77` (`canva_list_designs`) and
   `src/canva_mcp/tools/folders.py:57-72` (`canva_list_folder_items`) now forward the
   already-clamped `capped` value to Canva as the `limit` query param, in addition to
   the (now-defensive, commented as such) local slice. Independently fetched Canva's
   live OpenAPI spec (`https://www.canva.dev/sources/connect/api/latest/api.yml`,
   downloaded fresh during this review, not taken on the dev's word) and confirmed:
   `GET /v1/designs.limit` — `integer, minimum 1, maximum 100, default 25`;
   `GET /v1/folders/{folderId}/items.limit` — `integer, minimum 1, maximum 100, default 50`.
   The service's hard cap is 50, inside both endpoints' accepted range, so `capped` is
   always a valid value to forward. With Canva's own page now bounded by the same
   number returned to the caller, the unmodified `continuation` token it hands back
   always advances past exactly the items the caller saw — the silent-drop window
   between `capped` and the end of Canva's page (round 1's finding) no longer exists.
   This restores AC3/AC11's "a continuation token that gets the next page" contract.
2. **Suggestion 2 (`canva_get_design` page_count`)** — correctly left unchanged; it was
   non-blocking in round 1 and out of scope for a must-fix-only rework round.

### New diff this round (`6d66df3...a4f9d63`)

- `src/canva_mcp/tools/designs.py`, `src/canva_mcp/tools/folders.py`: the `limit`
  forwarding fix above, each with an inline comment explaining why (good — the next
  reader doesn't have to re-derive the OpenAPI constraint).
- `tests/test_tools.py`: three new regression tests —
  `test_list_designs_forwards_capped_limit_to_canva` (asserts the outgoing `limit`
  query param for a sub-cap value, the unclamped default, and a value above the hard
  cap — all three clamp branches), `test_list_designs_returns_every_item_canva_sent_for_the_forwarded_limit`
  (asserts every item in a Canva page sized to the forwarded limit survives, and
  `continuation` passes through unchanged), `test_list_folder_items_forwards_capped_limit_to_canva`
  (same coverage for the folder-items tool). These actually exercise the request the
  mock receives (`request.url.params["limit"]`), not just the tool's return value —
  the kind of test that would have caught round 1's bug directly.
- `docs/tickets/PRD-21299.ac.json`: `TC15` (AC3) and `TC16` (AC11) added with
  `"source": "dev"`, `"result": "pass"`, evidence pointing at the new tests — correctly
  not touching the design-owned AC3/AC11 acceptance text itself.
- `docs/tickets/PRD-21299-review.md`: round 1's own "Fixes" section (written by the
  dev leg under the review-rework protocol) — read and reconciled above, not altered.

### Verification performed (independent of what the Fixes section claims)

- `uv sync --frozen && uv run pytest -v` re-run on `a4f9d63`: **11 passed, 0 failed**
  (8 pre-existing + 3 new) — matches the claim.
- Fetched Canva's OpenAPI spec live and parsed the `limit` parameter schema for both
  endpoints (see above) — this is the load-bearing fact for the fix and was checked
  against the vendor's actual spec, not re-derived from the dev's description of it.
- `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`: no output.
- README `## MSPbots Connector` section re-diffed against
  `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md`:
  same pre-existing blank-line/`<div align="center">` extraction-boundary difference
  noted in round 1, untouched by this round's change — not a new finding.
- Did not re-run the container build/smoke or `gitleaks`: this round's diff is
  confined to a query-param value and test code, no new network/vendor/credential
  surface, and round 1's "Fixes" section already re-ran the local smoke (`/health`,
  missing-token 401, `tools/list` contract) against the fixed code with an unchanged
  tool contract.

### Findings

None (0 must-fix, 0 new suggestions).

## Round 1 · 2026-11-04 · reviewed dev/PRD-21299 @ 6d66df3 (base main @ e828ad9)

Verdict: changes requested

### Findings

1. [must-fix] `src/canva_mcp/tools/designs.py:66-77` and `src/canva_mcp/tools/folders.py:57-72` — pagination/continuation mismatch causes silent data loss.

   `canva_list_designs` calls Canva's `GET /designs` (line 66-69) without forwarding any page-size parameter, then truncates the response locally: `items = data.get("items", [])[:capped]` (line 73, `capped` defaults to 20, hard cap 50), but returns Canva's own, unmodified `continuation` token (line 77: `"continuation": data.get("continuation")`). Canva's `continuation` advances past its *entire* returned page, not past the `capped`-th item. If Canva's own page for this endpoint ever contains more than `capped` items (its default page size was never confirmed against the OpenAPI spec — this was flagged as a non-blocking "confirm at implementation time" open question in the tech design, §"Open questions", second bullet, but the implementation does not resolve it; the code is simply wrong regardless of what that page size turns out to be), the items between index `capped` and the end of Canva's page are silently dropped: a caller that follows the documented pagination contract (re-call with the returned `continuation`) will never see them. This directly breaks AC3's "if more exist, a continuation token the caller can pass back to get the next page" and AC11's identical contract for `canva_list_folder_items` — "get the next page" is not what the returned token does once local truncation has happened. `tests/test_tools.py::test_list_designs_returns_empty_list_when_canva_has_no_matches` does not exercise this path (the mocked Canva response has 0 items), so the gate green does not cover it.

   `src/canva_mcp/tools/folders.py:57-72` has the identical pattern (`canva_list_folder_items`): truncate locally at line 67, pass through Canva's raw `continuation` at line 71.

   This is a correctness bug per `references/REVIEW.md`'s "pagination miscalculated" must-fix category, not a style nit — it means a tenant with more designs/folder items than the (sub-Canva-page-size) `limit` they asked for will have some of them permanently unreachable through the documented pagination flow.

   Fix: either (a) confirm against Canva's OpenAPI spec (`https://www.canva.dev/sources/connect/api/latest/api.yml`) whether `GET /designs` and `GET /folders/{id}/items` accept a page-size query param, and forward `capped` as that param so Canva's own page matches what is returned to the caller, or (b) if no such param exists, implement real client-side pagination that only ever advances the cursor by what was actually handed back to the caller (e.g. buffer the untruncated page server-side-free, by encoding an offset into a locally-minted continuation token) instead of passing Canva's raw cursor through unchanged.

2. [suggestion] `src/canva_mcp/tools/designs.py:92-100` (`canva_get_design`) — `page_count` is computed as `len(page_items) if page_items else pages.get("total")` from `GET /designs/{id}/pages`. If that endpoint itself paginates for designs with many pages (unconfirmed — TC5, the manual case that would exercise this against a real design, was not run per the "needs a real vendor account" note), this would report only the first page's item count rather than the design's true total page count. Worth checking in the same real-account verification pass already planned for TC3/TC5/TC7-TC11; not blocking since it is unconfirmed and in the same "needs a real vendor account" bucket as the rest of the design's acknowledged gaps.

### Verification performed

- `uv sync --frozen && uv run pytest -v` re-run on `6d66df3`: 8 passed, 0 failed (matches the Self-test section's claim).
- `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`: no output (matches the "no CJK" claim).
- README `## MSPbots Connector` section diffed byte-for-byte against `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md`: identical (only diff was the blank-line boundary from the extraction, not content).
- Credential header name matches end-to-end: `X-Canva-Access-Token` in `middleware.py:19`, README's Authentication headers table, and the `[DEV]` stub's proposed `credential_fields`.
- Did not re-run the container build/smoke or `gitleaks` (no new network/vendor risk from a review read, and the Self-test section's reported commands and results are consistent with the diff).

### What passed review

- Request-level credential isolation (`ContextVar`, `finally: reset()`, no module/global state) — matches SOP §3.3/§3.4, and `tests/test_middleware.py::test_contextvar_token_is_isolated_across_concurrent_requests` is a real interleaved-concurrency test, not a sequential one dressed up as concurrent.
- Error envelope and status-code mapping (`errors.py`) is a fixed, finite vocabulary; Canva's raw 4xx bodies are never echoed back (`client.py:114-117`, verified by `test_get_design_maps_canva_404_to_structured_not_found`).
- Retry/backoff respects `Retry-After`, is bounded by a real wall-clock deadline (`client.py:37,53-54,62-63`), and does not retry non-429 4xx (`client.py:78-80`) — matches AC13/AC14 and is unit-tested for both branches.
- `/health` never calls Canva (`server.py:37-40`); the MCP sub-app's lifespan is explicitly wired into the outer Starlette app (`server.py:85-96`) with a comment explaining why that step is easy to silently skip — this exact class of bug (health green, `/mcp` broken) was caught live in the Self-test container smoke.
- Scope is in line with the tech design's non-goals (no OAuth flow in this repo, no raw-binary upload, no destructive tools); `Decisions made by DEV01` reads as genuine trade-offs with alternatives stated, not post-hoc justification.
- No duplicated/home-grown HTTP, retry, or MCP-protocol plumbing — `fastmcp`/`mcp[cli]`, `httpx`, `pydantic-settings` are exactly the SOP's named stack (reuse & cleanup checked, nothing to clean up in a from-scratch repo).

### Fixes

Fixed in `dev/PRD-21299 @ 4e47345` (synced with `main @ e828ad9`, unchanged since the base — no new commits to merge).

1. **Must-fix 1 (pagination/continuation mismatch)** — `src/canva_mcp/tools/designs.py` (`canva_list_designs`) and `src/canva_mcp/tools/folders.py` (`canva_list_folder_items`): both now forward the clamped `capped` value to Canva as the `limit` query param, in addition to the existing local slice (kept as a defensive no-op, commented as such).

   Resolved the review's option (a) rather than (b): fetched Canva's published OpenAPI spec (`https://www.canva.dev/sources/connect/api/latest/api.yml`) and confirmed `GET /v1/designs` and `GET /v1/folders/{folderId}/items` both declare an optional `limit` query parameter, integer, `minimum: 1`, `maximum: 100` (defaults 25 and 50 respectively in Canva's own API, irrelevant once we always send ours). Our hard cap (50) is below Canva's maximum (100), so `capped` is always a valid value to forward. With `limit=capped` on the request, Canva's own page is bounded by the same number we return, so its `continuation` token — passed through unchanged, as before — now always advances past exactly the items the caller saw. No items between `capped` and the end of Canva's page can exist anymore, because Canva's page ends at `capped`.

   New regression coverage, since the review noted the existing gate did not exercise this path: `tests/test_tools.py::test_list_designs_forwards_capped_limit_to_canva`, `tests/test_tools.py::test_list_designs_returns_every_item_canva_sent_for_the_forwarded_limit`, `tests/test_tools.py::test_list_folder_items_forwards_capped_limit_to_canva` (asserts the outgoing `limit` query param for a sub-cap value, the default, and a value above the hard cap; asserts every item in a Canva page sized exactly to the forwarded limit survives to the caller; asserts `continuation` passes through unchanged in all cases). Added to the `.ac.json` ledger as `TC15` (AC3) and `TC16` (AC11), `source: dev` per the pipeline's "dev-added test cases" rule — these are dev-added regression tests, not a change to the design-owned AC3/AC11 acceptance text itself.

2. **Suggestion 2 (`canva_get_design` page_count)** — not changed. The reviewer flagged this as non-blocking and in the same "needs a real vendor account" bucket as the rest of the design's acknowledged gaps (TC5); it is not a must-fix and this round only addresses must-fix 1 per the review-rework rule (only listed must-fix items are in scope for a review-rework round).

**Full gate re-run on `4e47345`** (same commands as Self-test, repeated per the review-rework rule):
- `uv sync --frozen && uv run pytest -v`: **11 passed, 0 failed** (8 pre-existing + 3 new; the 3 new all exercise the fixed code path).
- `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`: no output (still clean; this round touched no text).
- README `## MSPbots Connector` section (lines 20-89) diffed against `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md`: identical except the same pre-existing blank-line/`<div align="center">` extraction boundary noted in round 1 — untouched by this round's change.
- Local smoke: `uv run python -m canva_mcp` on port 8099 — `GET /health` → 200; `POST /mcp tools/list` with no `X-Canva-Access-Token` → 401; with a placeholder token → 200 with all 9 tools listed, same names/schemas as round 1 (the `limit` field's description and default are unchanged — only the outgoing Canva request gained a `limit` param, nothing in the tool's public contract changed). Server stopped after the check.
- `gitleaks`: not installed in this environment (same as round 1's note); no new network/vendor risk from this change (two added `dict` keys, no new secret handling).

Sha for review: `4e47345` (pushed to `dev/PRD-21299`).
