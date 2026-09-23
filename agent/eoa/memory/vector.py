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


def _load_candidates(days: int | None) -> list[dict[str, Any]]:
    """Fetch `(id, embedding)` for every embedded item, optionally windowed by `days`."""
    days_clause = ""
    params: dict[str, Any] = {}
    if days is not None:
        days_clause = (
            "AND COALESCE(published_at, created_at) >= now() - (%(days)s || ' days')::interval"
        )
        params["days"] = days
    query = f"""
        SELECT id, embedding
        FROM items
        WHERE embedding IS NOT NULL
        {days_clause}
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


def nearest(vec: Sequence[float], limit: int = 10, days: int | None = None) -> list[tuple[int, float]]:
    """Return `(item_id, cosine_similarity)` for the closest embedded items.

    `similarity` is the cosine similarity in `[-1, 1]` (1.0 is identical, closer
    to 0 is dissimilar) -- the same semantics `1 - cosine_distance` gave under
    pgvector's `<=>` operator. Results are windowed to the last `days` days (by
    published_at, falling back to fetched_at/created_at) when `days` is given.
    """
    query_vec = np.asarray(vec, dtype=np.float64)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        return []

    scored: list[tuple[int, float]] = []
    for row in _load_candidates(days):
        similarity = _cosine_similarity(query_vec, query_norm, row.get("embedding"))
        if similarity is not None:
            scored.append((row["id"], similarity))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


def find_duplicate(vec: Sequence[float], threshold: float, days: int) -> tuple[int, float] | None:
    """Return the closest prior item `(id, similarity)` within `days` if it clears `threshold`, else None."""
    candidates = nearest(vec, limit=1, days=days)
    if not candidates:
        return None
    item_id, similarity = candidates[0]
    if similarity >= threshold:
        log.info("vector.duplicate_found", item_id=item_id, similarity=similarity, threshold=threshold)
        return item_id, similarity
    return None
