"""pgvector helpers over `items.embedding` (cosine distance/similarity)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)


def _vector_literal(vec: Sequence[float]) -> str:
    """Render a float sequence as a pgvector text literal, e.g. ``[0.1,0.2,0.3]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def upsert_embedding(item_id: int, vec: Sequence[float]) -> None:
    """Set `items.embedding` for `item_id`."""
    query = "UPDATE items SET embedding = %(vec)s::vector WHERE id = %(item_id)s"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"vec": _vector_literal(vec), "item_id": item_id})
    log.debug("vector.embedding_upserted", item_id=item_id, dim=len(vec))


def nearest(vec: Sequence[float], limit: int = 10, days: int | None = None) -> list[tuple[int, float]]:
    """Return `(item_id, cosine_similarity)` for the closest embedded items.

    `similarity` is ``1 - cosine_distance`` (so 1.0 is identical, closer to 0
    is dissimilar). Results are windowed to the last `days` days (by
    published_at, falling back to fetched_at/created_at) when `days` is given.
    """
    literal = _vector_literal(vec)
    days_clause = ""
    params: dict[str, Any] = {"vec": literal, "limit": limit}
    if days is not None:
        days_clause = (
            "AND COALESCE(published_at, fetched_at, created_at) >= now() - (%(days)s || ' days')::interval"
        )
        params["days"] = days
    query = f"""
        SELECT id, 1 - (embedding <=> %(vec)s::vector) AS similarity
        FROM items
        WHERE embedding IS NOT NULL
        {days_clause}
        ORDER BY embedding <=> %(vec)s::vector
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [(row["id"], row["similarity"]) for row in rows]


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
