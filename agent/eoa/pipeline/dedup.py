"""Stage: embed + cross-language de-duplication (numpy cosine, see `eoa.memory.vector`)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable
from eoa.execution import checkpoint
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


def run_dedup(limit: int = 500, batch_size: int = 16, *, item_ids: list[int] | None = None) -> DedupStats:
    """Embed new items and link near-duplicates (same story, any language) via ``dedup_of``.

    F22: ``item_ids`` (optional, additive) scopes this run to just those ids -- see
    ``eoa.memory.relational.get_items_for_stage``."""
    checkpoint()
    cfg = settings().dedup
    stats = DedupStats()
    items = [
        it
        for it in get_items_for_stage(STAGE, limit, item_ids=item_ids)
        if it.get("security_status") not in ("quarantined", "blocked")
    ]
    for i in range(0, len(items), batch_size):
        checkpoint()
        batch = items[i : i + batch_size]
        try:
            vecs = embed([_embed_text(it) for it in batch])
        except (DeadlineExceeded, LeaseLost, ResourceUnavailable):
            raise
        except Exception as exc:
            log.error("embed_batch_failed", n=len(batch), error=str(exc)[:200])
            stats.failed += len(batch)
            continue
        for it, vec in zip(batch, vecs, strict=True):
            checkpoint()
            try:
                dup = find_duplicate(vec, cfg.cosine_threshold, cfg.lookback_days)
                upsert_embedding(it["id"], vec)
                if dup is not None:
                    update_item_fields(it["id"], dedup_of=dup[0])
                    stats.duplicates += 1
                    log.info("dedup_linked", item_id=it["id"], dup_of=dup[0], sim=round(dup[1], 3))
                mark_stage(it["id"], STAGE)
                stats.embedded += 1
            except (DeadlineExceeded, LeaseLost):
                raise
            except Exception as exc:
                log.error("dedup_item_failed", item_id=it["id"], error=str(exc)[:200])
                stats.failed += 1
    log.info("dedup_done", **stats.__dict__)
    return stats


def link_cross_language(lookback_days: int | None = None) -> int:
    """Second-pass dedup after classification: the same story in different languages rarely clears the cosine
    threshold, so link items that share ≥ 2 entities and a publication date within ±1.5 days but have different
    languages. The earlier item becomes the canonical one. Returns the number of links made.

    2026-09-17 (story-clustering task, docs/... SPICE-1000 investigation): this used to also require
    ``b.domain = a.domain`` -- dropped after live evidence showed it made the stage a near-total no-op. Six
    items about one real story (Rafael SPICE 1000 on the F-35, ids 22396/22798/23002/24089/25948/26284) came
    back from ``eoa.pipeline.classify`` tagged with FIVE different (taxonomy, not outlet) ``domain`` values --
    ``computer_vision``, ``secondary``, ``airborne_pods``, ``out_of_scope`` and ``NULL`` -- for the exact same
    underlying event. Classification is a per-item LLM judgment call about which taxonomy bucket a story best
    fits, and different outlets' framing of the same news (photo/targeting angle vs. munition-integration
    angle vs. programme angle) routinely lands it in different buckets; requiring an exact match made this
    stage link cross-language pairs only when classification *happened* to agree, which last night's run (and
    almost certainly most nights) it didn't -- hence ``dedup_xlang: linked=0``. The ≥2-shared-distinctive-
    entity check plus the tight time window already do the real "is this the same story" work (the same
    signal ``eoa.pipeline.corroboration.compute_for_item``'s same-event matcher relies on); domain equality
    was redundant on top of them and, in practice, only ever the thing keeping this stage from ever firing.
    ``out_of_scope``/``secondary`` are still excluded on both sides -- that guard was only ever meant to keep
    plainly irrelevant items out of the dedup graph, not to gate matching between two in-scope items."""
    checkpoint()
    from eoa.db import connection

    days = lookback_days or settings().dedup.lookback_days
    linked = 0
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id AS a_id, b.id AS b_id
            FROM items a
            JOIN items b
              ON b.id > a.id
             AND b.lang IS DISTINCT FROM a.lang
             AND a.domain NOT IN ('out_of_scope', 'secondary')
             AND b.domain NOT IN ('out_of_scope', 'secondary')
             AND abs(extract(epoch FROM (coalesce(b.published_at, b.fetched_at) - coalesce(a.published_at, a.fetched_at)))) <= 86400 * 1.5
             AND cardinality(ARRAY(SELECT unnest(a.entities_mentioned) INTERSECT SELECT unnest(b.entities_mentioned))) >= 2
            WHERE a.dedup_of IS NULL AND b.dedup_of IS NULL
              AND a.security_status = 'clean' AND b.security_status = 'clean'
              AND a.fetched_at > now() - make_interval(days => %s)
            ORDER BY a.id
            """,
            (days,),
        ).fetchall()
        seen: set[int] = set()
        for r in rows:
            checkpoint()
            if r["b_id"] in seen:
                continue
            conn.execute(
                "UPDATE items SET dedup_of = %s WHERE id = %s AND dedup_of IS NULL", (r["a_id"], r["b_id"])
            )
            seen.add(r["b_id"])
            linked += 1
    log.info("dedup_cross_language", linked=linked)
    return linked
