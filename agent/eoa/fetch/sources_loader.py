"""Load `config/sources.yaml` into validated pydantic models and upsert into the DB.

Kept separate from `service.py` so `run_ingest` and anything else that just
needs the configured source list doesn't have to import DB code, and so the
loader itself is trivially unit-testable against an arbitrary YAML path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

# agent/eoa/fetch/sources_loader.py -> parents[3] is the repo root.
_REPO_ROOT = Path(os.environ.get("EOA_ROOT", Path(__file__).resolve().parents[3]))
_DEFAULT_SOURCES_PATH = _REPO_ROOT / "config" / "sources.yaml"


class Source(BaseModel):
    """One `config/sources.yaml` entry, validated."""

    id: str
    name: str
    url: str
    kind: Literal["rss", "html"]
    lang: str
    reliability: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    schedule: Literal["daily", "weekly"] = "daily"
    # Q4-2/Q4-3 (docs/qa/findings_Q4_r1.md): a source with no working fetch path at all (feed
    # dead with no replacement, robots.txt blocks the only feed that exists) is disabled here
    # rather than left in the config to fail every run -- `run_ingest` filters these out before
    # upserting/fetching. Defaults to True so every pre-existing entry (no `enabled:` key) is
    # unaffected.
    enabled: bool = True
    notes: str | None = None
    verified: bool = False
    verified_at: str | None = None
    list_selector: str | None = None
    link_selector: str | None = None
    # A12 (מעקב טכנולוגי, 2026-09-06): additive. `category` is free-text metadata (e.g.
    # "science") carried through to the DB row for downstream reporting; `keywords_any`, when
    # set, restricts a broad `rss` feed (site-wide, journal TOC, etc.) to entries whose title or
    # summary contains at least one of these substrings (case-insensitive) -- applied in
    # `eoa.fetch.service._ingest_rss_source` before the article is even fetched, so an
    # off-topic issue of e.g. an IEEE journal TOC doesn't burn a fetch + LLM classify call.
    category: str | None = None
    keywords_any: list[str] = Field(default_factory=list)


def load_sources(path: str | Path | None = None) -> list[Source]:
    """Load and validate every entry in `config/sources.yaml` (or an alternate `path`)."""
    file_path = Path(path) if path is not None else _DEFAULT_SOURCES_PATH
    with file_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    entries = raw.get("sources", [])
    sources = [Source.model_validate(entry) for entry in entries]
    log.debug("fetch.sources_loaded", count=len(sources), path=str(file_path))
    return sources


def upsert_sources_to_db(sources: list[Source] | None = None) -> dict[str, int]:
    """Upsert every configured source into the `sources` table.

    Returns a `{source.id (yaml slug): db_row_id}` map — the DB table keys
    sources by `name`, not by our yaml slug, so callers that need to resolve
    a slug (e.g. `run_ingest(source_ids=...)`) go through this map.

    Imported lazily: unit tests for `sources_loader.load_sources()` never
    touch the DB.
    """
    from eoa.memory import relational

    resolved = sources if sources is not None else load_sources()
    id_map: dict[str, int] = {}
    for source in resolved:
        db_id = relational.upsert_source(
            name=source.name,
            url=source.url,
            kind=source.kind,
            lang=source.lang,
            reliability=source.reliability,
        )
        id_map[source.id] = db_id
    log.info("fetch.sources_upserted", count=len(id_map))
    return id_map
