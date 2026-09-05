"""`GET /api/llm/providers`, `PUT /api/llm/settings` -- U8 cloud LLM provider routing.

Read-only listing (availability + models per provider, current default/kill-switch) plus a
narrow, additive write endpoint for the Settings "מודלים" card. The full config.yaml is still
reachable (and still the source of truth) through the generic `GET/PUT /api/settings/config`
editor -- this just spares that one card from round-tripping raw YAML for two booleans/strings.
See docs/adr/005-cloud-llm-cli.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Header
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import APIError

router = APIRouter(tags=["llm"])


class LlmSettingsPayload(BaseModel):
    interactive_default: str | None = None
    allow_cloud: bool | None = None
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
