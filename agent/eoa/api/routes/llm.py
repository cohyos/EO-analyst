"""`GET /api/llm/providers`, `PUT /api/llm/settings`, `GET /api/llm/calls` -- U8 cloud LLM
provider routing (docs/adr/005-cloud-llm-cli.md + "Revision 2026-09-06" section).

Read-only listing (availability + models per provider, current default/kill-switch/global mode)
plus a narrow, additive write endpoint for the Settings "מודלים" card, plus the fallback-chain
call-accounting summary (per-provider calls/failures/fallbacks/tokens/cost). The full config.yaml
is still reachable (and still the source of truth) through the generic `GET/PUT /api/settings/config`
editor -- this just spares that one card from round-tripping raw YAML for a few scalars.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import APIError

router = APIRouter(tags=["llm"])


class LlmSettingsPayload(BaseModel):
    interactive_default: str | None = None
    allow_cloud: bool | None = None
    mode: str | None = None  # U8-א (Revision 2026-09-06): "local" | "cloud" global switch
    revision: str | None = None


@router.get("/llm/providers")
def get_llm_providers() -> dict:
    return services.list_llm_providers()


@router.put("/llm/settings")
def put_llm_settings(
    body: LlmSettingsPayload,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    expected_revision = (if_match.strip('"') if if_match else None) or body.revision
    try:
        ok, errors, revision = services.patch_llm_provider_settings(
            interactive_default=body.interactive_default,
            allow_cloud=body.allow_cloud,
            mode=body.mode,
            expected_revision=expected_revision,
        )
    except services.SettingsConflict as exc:
        raise APIError(
            409,
            "conflict",
            "ההגדרות השתנו מאז הקריאה האחרונה — טען מחדש ונסה שוב",
            detail={"current_revision": exc.current_revision},
        ) from exc
    return {"ok": ok, "errors": errors, "revision": revision}


@router.get("/llm/calls")
def get_llm_calls(since: str = Query(default="24h")) -> dict:
    """U8-4: ``?since=24h`` (or a bare ``"<n>h"``/``"<n>"`` string, hours) -> per-provider
    calls/failures/fallbacks/tokens/estimated cost summary from ``llm_calls``, for the Settings
    "מודלים" card."""
    hours_text = since.strip().lower().removesuffix("h") or "24"
    try:
        hours = max(1, int(hours_text))
    except ValueError:
        hours = 24
    return services.summarize_llm_calls(since_hours=hours)
