"""`GET/PUT /api/settings/{name}` (name in config|sources|watchlist|taxonomy|models).

Network exposure: per `docker-compose.yml`/`docs/CONVENTIONS.md`, this API
binds to `127.0.0.1`/Tailscale only -- it is never reachable from the
public internet. That boundary is unchanged here. On top of it, `PUT` also
enforces an optional shared-secret check (finding #17 in
`output/reviews/codex_security_review.md`): if the `EOA_API_TOKEN`
environment variable is set, every `PUT` must carry a matching
`X-EOA-Token` header or it is rejected with 401. `GET` is never gated by
the token -- the network boundary is still the primary control for reads,
and the web UI's settings screen needs to load without a token prompt.
This is a belt-and-suspenders check, not a substitute for the network
boundary.

Writes are additionally guarded by (finding #18/#19, implemented in
`eoa.api.services`): `name` resolves only through a fixed dict of absolute
paths, never joined from user input; a 256 KB request-size cap; `yaml.
safe_load` with a node-count/nesting-depth budget; full typed validation
before anything touches disk; an atomic write-then-replace; and an
optional `If-Match`/`revision` precondition -- `GET` returns the current
file's sha256 as `revision`, and a `PUT` that supplies a stale one (via the
`If-Match` header or the body's `revision` field) is rejected with 409
rather than silently clobbering a concurrent edit.
"""

from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import APIError, not_found

router = APIRouter(tags=["settings"])


class SettingsPayload(BaseModel):
    yaml: str
    revision: str | None = None


def _require_token(x_eoa_token: str | None) -> None:
    required = os.environ.get("EOA_API_TOKEN")
    if not required:
        return
    if not x_eoa_token or not hmac.compare_digest(x_eoa_token, required):
        raise APIError(401, "unauthorized", "טוקן גישה חסר או שגוי (X-EOA-Token)")


@router.get("/settings/{name}")
def get_settings(name: str) -> dict:
    try:
        yaml_text = services.read_settings_yaml(name)
        revision = services.settings_revision(name)
    except KeyError as exc:
        raise not_found(f"קובץ הגדרות לא מוכר: {name}") from exc
    except FileNotFoundError as exc:
        raise not_found("קובץ ההגדרות לא קיים") from exc
    return {"yaml": yaml_text, "revision": revision}


@router.put("/settings/{name}")
def put_settings(
    name: str,
    body: SettingsPayload,
    x_eoa_token: str | None = Header(default=None, alias="X-EOA-Token"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict:
    _require_token(x_eoa_token)

    expected_revision = (if_match.strip('"') if if_match else None) or body.revision

    try:
        errors = services.write_settings_yaml(name, body.yaml, expected_revision=expected_revision)
    except KeyError as exc:
        raise not_found(f"קובץ הגדרות לא מוכר: {name}") from exc
    except services.SettingsConflict as exc:
        raise APIError(
            409,
            "conflict",
            "הקובץ השתנה מאז הקריאה האחרונה — טען מחדש ונסה שוב",
            detail={"current_revision": exc.current_revision},
        ) from exc
    return {"ok": not errors, "errors": errors}
