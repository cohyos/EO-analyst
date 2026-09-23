"""Cosine-similarity helpers over `items.embedding` (plain `REAL[]`, numpy-computed).

2026-09-05 (ADR-004, docs/PLAN_WINDOWS_NATIVE.md step 1a): pgvector is gone --
`items.embedding` is a plain PostgreSQL `REAL[]` column (migration 0006) and
similarity is computed in Python with numpy instead of via a pgvector operator
and HNSW index. Candidate embeddings are pulled into memory (optionally windowed
by `days`) and scored with a normalise + dot-product cosine similarity, which is
trivial at this project's scale (hundreds to low thousands of vectors -- see the
plan doc). The public API and the exact `nearest()`/`find_duplicate()` return
contract are unchanged from the pgvector implementation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)


def upsert_embedding(item_id: int, vec: Sequence[float]) -> None:
    """Set `items.embedding` for `item_id`."""
    query = "UPDATE items SET embedding = %(vec)s WHERE id = %(item_id)s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"vec": [float(x) for x in vec], "item_id": item_id})
    log.debug("vector.embedding_upserted", item_id=item_id, dim=len(vec))


def _load_candidates(days: int | None, exclude_id: int | None = None) -> list[dict[str, Any]]:
    """Fetch `(id, embedding)` for every embedded item, optionally windowed by `days`.

    F08: `exclude_id`, when given, drops that item from its own candidate pool -- a retry that
    re-embeds an item whose embedding was already committed (but whose stage marker was not, e.g.
    after a crash between the two writes) must never be allowed to match against its own
    just-upserted vector, which is trivially cosine 1.0 and would wrongly mark the item as its own
    duplicate (hiding its canonical row from reports)."""
    days_clause = ""
    exclude_clause = ""
    params: dict[str, Any] = {}
    if days is not None:
        days_clause = (
            "AND COALESCE(published_at, created_at) >= now() - (%(days)s || ' days')::interval"
        )
        params["days"] = days
    if exclude_id is not None:
        exclude_clause = "AND id != %(exclude_id)s"
        params["exclude_id"] = exclude_id
    query = f"""
        SELECT id, embedding
        FROM items
        WHERE embedding IS NOT NULL
        {days_clause}
        {exclude_clause}
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _cosine_similarity(query_vec: np.ndarray, query_norm: float, candidate: Sequence[float]) -> float | None:
    """Return cosine similarity of `candidate` against the pre-normed `query_vec`, or None if unscoreable."""
    if not candidate or query_norm == 0.0:
        return None
    cand_vec = np.asarray(candidate, dtype=np.float64)
    if cand_vec.shape != query_vec.shape:
        return None
    cand_norm = float(np.linalg.norm(cand_vec))
    if cand_norm == 0.0:
        return None
    return float(np.dot(query_vec, cand_vec) / (query_norm * cand_norm))


def nearest(
    vec: Sequence[float], limit: int = 10, days: int | None = None, exclude_id: int | None = None
) -> list[tuple[int, float]]:
    """Return `(item_id, cosine_similarity)` for the closest embedded items.

    `similarity` is the cosine similarity in `[-1, 1]` (1.0 is identical, closer
    to 0 is dissimilar) -- the same semantics `1 - cosine_distance` gave under
    pgvector's `<=>` operator. Results are windowed to the last `days` days (by
    published_at, falling back to fetched_at/created_at) when `days` is given.

    F08: `exclude_id`, when given, is never returned as a candidate -- filtered at the SQL level
    (:func:`_load_candidates`) AND again here in Python (belt and suspenders: the self-match this
    closes is exactly the retry-hides-its-own-canonical-row bug, so it must hold even if a future
    change to `_load_candidates` ever loosens the SQL-side filter).
    """
    query_vec = np.asarray(vec, dtype=np.float64)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        return []

    scored: list[tuple[int, float]] = []
    for row in _load_candidates(days, exclude_id=exclude_id):
        if exclude_id is not None and row.get("id") == exclude_id:
            continue
        similarity = _cosine_similarity(query_vec, query_norm, row.get("embedding"))
        if similarity is not None:
            scored.append((row["id"], similarity))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


def find_duplicate(
    vec: Sequence[float], threshold: float, days: int, exclude_id: int | None = None
) -> tuple[int, float] | None:
    """Return the closest prior item `(id, similarity)` within `days` if it clears `threshold`, else None.

    F08: pass the item's own id as `exclude_id` so a retry (embedding already committed, stage
    marker not yet) can never match against its own just-upserted vector."""
    candidates = nearest(vec, limit=1, days=days, exclude_id=exclude_id)
    if not candidates:
        return None
    item_id, similarity = candidates[0]
    if similarity >= threshold:
        log.info("vector.duplicate_found", item_id=item_id, similarity=similarity, threshold=threshold)
        return item_id, similarity
    return None


# --------------------------------------------------------------------------
# F08 (efficiency note, audit "Load dedup candidate vectors once per stage"): in-memory scoring
# over an already-loaded candidate pool, used by ``eoa.pipeline.dedup.run_dedup`` so a run of N
# items issues one candidate-vector query instead of N -- and atomic embedding+dedup_of+stage-marker
# commit so a crash between the three writes can never again leave an embedded-but-unmarked item
# whose retry sees its own vector (the root cause this same finding also flags).
# --------------------------------------------------------------------------


def load_candidate_vectors(days: int | None) -> list[dict[str, Any]]:
    """Public, one-shot version of :func:`_load_candidates` for a caller (``eoa.pipeline.dedup``)
    that wants to hold the whole candidate pool in memory for a run and score many query vectors
    against it, instead of re-querying per item."""
    return _load_candidates(days)


def find_duplicate_in_memory(
    vec: Sequence[float],
    candidates: list[tuple[int, Sequence[float] | None]],
    threshold: float,
    *,
    exclude_id: int | None = None,
) -> tuple[int, float] | None:
    """Same matching semantics as :func:`find_duplicate`, scored against an already-loaded
    in-memory `candidates` list (`(item_id, embedding)` pairs) instead of a fresh DB query."""
    query_vec = np.asarray(vec, dtype=np.float64)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        return None
    best: tuple[int, float] | None = None
    for item_id, embedding in candidates:
        if exclude_id is not None and item_id == exclude_id:
            continue
        similarity = _cosine_similarity(query_vec, query_norm, embedding)
        if similarity is not None and (best is None or similarity > best[1]):
            best = (item_id, similarity)
    if best is not None and best[1] >= threshold:
        log.info("vector.duplicate_found", item_id=best[0], similarity=best[1], threshold=threshold)
        return best
    return None


def commit_dedup_result(item_id: int, vec: Sequence[float], dedup_of: int | None, stage: str) -> None:
    """Atomically write `item_id`'s embedding, `dedup_of` link, and pipeline `stage` marker in one
    transaction (one pooled connection, committed once).

    F08: previously these were three separate auto-committing calls (`upsert_embedding`,
    `update_item_fields`, `mark_stage`); a crash between the first and the last left the embedding
    committed but the stage marker absent, so a retry re-selected the item, re-embedded it, and (pre
    F08) matched against its own already-committed vector. Doing the write in one transaction
    removes that partial-write window; the exclude-self fix above is the second, independent half of
    the same finding (belt and suspenders -- either alone would have prevented the self-match).

    N05 (SOL-REVIEW-2026-09-24 round 2): `dedup_of` is now ALWAYS written (to `dedup_of`, which is
    `None`/NULL when this pass found no match), not only when a match was found. The old
    `if dedup_of is not None:` guard meant a RE-embed (`eoa.memory.relational.insert_item` resets
    `processed_stages` -- and thus re-queues this stage -- when a same-URL refetch's content is
    accepted as a quality upgrade, F09) could never clear a stale `dedup_of` link computed against
    the item's OLD, worse content: a quality-upgraded item that turns out to be unique on its new
    content stayed hidden behind that old link forever. Writing unconditionally means this pass's
    result -- match or no match -- always reflects the item's current content."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE items SET embedding = %(vec)s WHERE id = %(item_id)s",
            {"vec": [float(x) for x in vec], "item_id": item_id},
        )
        cur.execute(
            "UPDATE items SET dedup_of = %(dedup_of)s WHERE id = %(item_id)s",
            {"dedup_of": dedup_of, "item_id": item_id},
        )
        cur.execute(
            """
            UPDATE items
            SET processed_stages = CASE
                WHEN %(stage)s = ANY(COALESCE(processed_stages, '{}')) THEN processed_stages
                ELSE array_append(COALESCE(processed_stages, '{}'), %(stage)s)
            END
            WHERE id = %(item_id)s
            """,
            {"stage": stage, "item_id": item_id},
        )
    log.debug("vector.dedup_committed", item_id=item_id, dedup_of=dedup_of, stage=stage)
