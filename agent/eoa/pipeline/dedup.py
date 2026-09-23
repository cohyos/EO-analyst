"""Stage: embed + cross-language de-duplication (numpy cosine, see `eoa.memory.vector`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable
from eoa.execution import checkpoint
from eoa.llm.ollama_client import embed
from eoa.memory.relational import get_items_for_stage
from eoa.memory.vector import commit_dedup_result, find_duplicate_in_memory, load_candidate_vectors

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
    ``eoa.memory.relational.get_items_for_stage``.

    Efficiency (audit "Load dedup candidate vectors once per stage"): the candidate-vector pool is
    loaded ONCE for the whole run (not once per item -- a 300-item run used to reload every embedded
    item's vector 300 times) and kept in memory, appending each processed item's own vector right
    after it's scored so later items in the same run still see it -- the same sequential-matching
    behavior the old per-item DB reload gave for free (an earlier item's freshly committed vector
    was already visible to the next item's fresh query).

    F08: each item's embedding, ``dedup_of`` link, and stage marker are written atomically
    (:func:`eoa.memory.vector.commit_dedup_result`), and the item is excluded from its own candidate
    pool (:func:`eoa.memory.vector.find_duplicate_in_memory`'s ``exclude_id``) -- together these
    close the retry self-dedup window the finding describes (a crash between a partial write and a
    retry could otherwise match an item against its own just-committed vector).

    E01/N06 (SOL-REVIEW-2026-09-24 round 2): the in-memory candidate pool is now keyed by item id
    (``dict[int, vector]``, not a plain append-only list) and every addition to it enforces the
    SAME ``cfg.dedup.lookback_days`` cutoff :func:`eoa.memory.vector.load_candidate_vectors` used
    for the initial DB load -- ``get_items_for_stage`` (the ``items`` source above) has NO date
    filter of its own (oldest-first, whatever hasn't completed this stage yet), so an old item
    processed in this run could otherwise be appended to the pool unconditionally and wrongly match
    a later, genuinely recent item against it (N06's "appends out-of-lookback items"). Keying by id
    also means a re-embed (F09's quality-upgrade reprocessing resets ``processed_stages``, so an
    already-embedded item can be re-selected here) REPLACES that item's stale entry instead of
    leaving both the old and the new vector in the pool simultaneously (N06's "appends a new vector
    without replacing that item's old loaded vector")."""
    checkpoint()
    cfg = settings().dedup
    stats = DedupStats()
    items = [
        it
        for it in get_items_for_stage(STAGE, limit, item_ids=item_ids)
        if it.get("security_status") not in ("quarantined", "blocked")
    ]
    cutoff = (
        datetime.now(UTC) - timedelta(days=cfg.lookback_days)
        if cfg.lookback_days is not None
        else None
    )
    candidates: dict[int, list[float]] = {
        row["id"]: row.get("embedding") for row in load_candidate_vectors(cfg.lookback_days)
    }
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
                dup = find_duplicate_in_memory(
                    vec, list(candidates.items()), cfg.cosine_threshold, exclude_id=it["id"]
                )
                commit_dedup_result(it["id"], vec, dup[0] if dup is not None else None, STAGE)
                # N06: always drop any stale entry for this id first (replace-on-re-embed), then
                # re-add only if this item's own date is within the same cutoff the DB load used --
                # `get_items_for_stage` has no date filter, so a re-selected item can be old.
                candidates.pop(it["id"], None)
                item_date = _coalesced_date(it)
                if cutoff is None or (item_date is not None and item_date >= cutoff):
                    candidates[it["id"]] = list(vec)
                if dup is not None:
                    stats.duplicates += 1
                    log.info("dedup_linked", item_id=it["id"], dup_of=dup[0], sim=round(dup[1], 3))
                stats.embedded += 1
            except (DeadlineExceeded, LeaseLost):
                raise
            except Exception as exc:
                log.error("dedup_item_failed", item_id=it["id"], error=str(exc)[:200])
                stats.failed += 1
    log.info("dedup_done", **stats.__dict__)
    return stats


def _coalesced_date(item: dict) -> datetime | None:
    """Mirror :func:`eoa.memory.vector._load_candidates`'s own date expression
    (``COALESCE(published_at, created_at)``) so the in-memory pool's own lookback cutoff matches
    the DB query's exactly (E01) -- naive datetimes (should not occur from `timestamptz` columns,
    but defended anyway) are treated as UTC, consistent with this module's other date handling."""
    date = item.get("published_at") or item.get("created_at")
    if date is not None and date.tzinfo is None:
        date = date.replace(tzinfo=UTC)
    return date


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
             AND abs(extract(epoch FROM (coalesce(b.published_at, b.created_at) - coalesce(a.published_at, a.created_at)))) <= 86400 * 1.5
             AND cardinality(ARRAY(SELECT unnest(a.entities_mentioned) INTERSECT SELECT unnest(b.entities_mentioned))) >= 2
            WHERE a.dedup_of IS NULL AND b.dedup_of IS NULL
              AND a.security_status = 'clean' AND b.security_status = 'clean'
              AND coalesce(a.published_at, a.created_at) > now() - make_interval(days => %s)
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
