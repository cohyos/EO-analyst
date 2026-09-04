"""Stage: embed + cross-language de-duplication (pgvector cosine)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from eoa.config import settings
from eoa.llm.ollama_client import embed
from eoa.memory.relational import get_items_for_stage, mark_stage, update_item_fields
from eoa.memory.vector import find_duplicate, upsert_embedding

log = structlog.get_logger(__name__)

STAGE = "embed_dedup"


@dataclass
class DedupStats:
    embedded: int = 0
    duplicates: int = 0
    failed: int = 0


def _embed_text(item: dict) -> str:
    title = (item.get("title") or "").strip()
    body = (item.get("clean_text") or "")[:3000]
    return f"{title}\n{body}"


def run_dedup(limit: int = 500, batch_size: int = 16) -> DedupStats:
    """Embed new items and link near-duplicates (same story, any language) via ``dedup_of``."""
    cfg = settings().dedup
    stats = DedupStats()
    items = [it for it in get_items_for_stage(STAGE, limit) if it.get("security_status") != "quarantined"]
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        try:
            vecs = embed([_embed_text(it) for it in batch])
        except Exception as exc:  # noqa: BLE001
            log.error("embed_batch_failed", n=len(batch), error=str(exc)[:200])
            stats.failed += len(batch)
            continue
        for it, vec in zip(batch, vecs, strict=True):
            try:
                dup = find_duplicate(vec, cfg.cosine_threshold, cfg.lookback_days, exclude_item_id=it["id"])
                upsert_embedding(it["id"], vec)
                if dup is not None:
                    update_item_fields(it["id"], dedup_of=dup[0])
                    stats.duplicates += 1
                    log.info("dedup_linked", item_id=it["id"], dup_of=dup[0], sim=round(dup[1], 3))
                mark_stage(it["id"], STAGE)
                stats.embedded += 1
            except Exception as exc:  # noqa: BLE001
                log.error("dedup_item_failed", item_id=it["id"], error=str(exc)[:200])
                stats.failed += 1
    log.info("dedup_done", **stats.__dict__)
    return stats
