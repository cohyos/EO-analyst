"""Golden + rotating sample selection for the QA continuous loop (docs/QA_CONTINUOUS_LOOP.md sec 2).

The **golden** sample (40 items, 6 investigations) is chosen ONCE (this module's ``select_*``
functions) and then frozen into ``docs/qa/loop/golden_items.json`` -- every subsequent round just
loads those same ids back (``load_golden_ids``) so the fixed part of the score is comparable
round over round. The **rotating** sample (20 items from the last 48h) is re-picked every round,
seeded by the round number for reproducibility within a single re-run of the same round.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ResolvedSample:
    golden_items: list[dict[str, Any]]
    rotating_items: list[dict[str, Any]]
    investigation_job_ids: list[int]
    golden_item_ids: list[int] = field(default_factory=list)
    rotating_item_ids: list[int] = field(default_factory=list)


def _fetch_items_by_id(conn: Any, ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM items WHERE id = ANY(%s)", (ids,))
        rows = {r["id"]: r for r in cur.fetchall()}
    # preserve the caller's order, drop any id no longer present (deleted/purged since selection)
    return [rows[i] for i in ids if i in rows]


def select_golden_items(conn: Any, n: int = 40) -> list[int]:
    """Stratified selection by ``level`` and ``domain`` (which naturally sweeps in Israeli-focused
    and ``tech_dev`` items, both being real domain/level values), deterministic by ascending id so
    re-running this exact function reproduces the same set. Meant to be called once; the result is
    persisted to ``golden_items.json`` and reloaded thereafter -- see :func:`load_golden_ids`."""
    query = """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (PARTITION BY COALESCE(level, 'untriaged') ORDER BY id) AS rn_level,
                   row_number() OVER (PARTITION BY COALESCE(domain, 'unknown') ORDER BY id) AS rn_domain,
                   row_number() OVER (ORDER BY israel_relevance DESC NULLS LAST, id) AS rn_israel
            FROM items
        )
        SELECT DISTINCT id FROM ranked
        WHERE rn_level <= 8 OR rn_domain <= 5 OR rn_israel <= 6
        ORDER BY id
    """
    with conn.cursor() as cur:
        cur.execute(query)
        ids = [r["id"] for r in cur.fetchall()]
    if len(ids) >= n:
        return ids[:n]
    # backfill to exactly n with the most recently created items not already selected
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM items WHERE NOT (id = ANY(%s)) ORDER BY created_at DESC LIMIT %s",
            (ids, n - len(ids)),
        )
        ids.extend(r["id"] for r in cur.fetchall())
    return sorted(ids)[:n]


def select_golden_investigation_jobs(conn: Any, n: int = 6) -> list[int]:
    """``n`` most recent completed deep-search jobs -- these become the fixed investigation
    sample; see :func:`load_golden_ids` for the persisted form."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM jobs WHERE kind = 'deep_search' AND state = 'done' ORDER BY id DESC LIMIT %s",
            (n,),
        )
        return [r["id"] for r in cur.fetchall()]


def select_rotating_items(conn: Any, n: int = 20, *, seed: int = 0, window_hours: int = 48) -> list[int]:
    """``n`` items from the last ``window_hours`` hours, pseudo-randomly ordered by a
    seed-dependent hash so the same ``seed`` (the round number) reproduces the same rotating
    sample if a round is re-run, but a new round's sample differs from the last."""
    query = """
        SELECT id FROM items
        WHERE created_at > now() - (%(hours)s || ' hours')::interval
        ORDER BY md5(id::text || %(seed)s::text)
        LIMIT %(n)s
    """
    with conn.cursor() as cur:
        cur.execute(query, {"hours": window_hours, "seed": seed, "n": n})
        return [r["id"] for r in cur.fetchall()]


def save_golden_ids(path: Path, *, item_ids: list[int], investigation_job_ids: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"item_ids": item_ids, "investigation_job_ids": investigation_job_ids},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_golden_ids(path: Path) -> tuple[list[int], list[int]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("item_ids", []), data.get("investigation_job_ids", [])


def load_golden_questions(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("questions", [])


def resolve_sample(
    conn: Any,
    *,
    round_no: int,
    golden_items_path: Path,
    rotating_n: int = 20,
    rotating_window_hours: int = 48,
) -> ResolvedSample:
    """Load the frozen golden sample from ``golden_items_path`` (generating and persisting it on
    first use if the file doesn't exist yet) and pick a fresh rotating sample seeded by
    ``round_no``."""
    if golden_items_path.exists():
        golden_item_ids, investigation_job_ids = load_golden_ids(golden_items_path)
    else:
        golden_item_ids = select_golden_items(conn)
        investigation_job_ids = select_golden_investigation_jobs(conn)
        save_golden_ids(golden_items_path, item_ids=golden_item_ids, investigation_job_ids=investigation_job_ids)

    rotating_item_ids = select_rotating_items(conn, n=rotating_n, seed=round_no, window_hours=rotating_window_hours)

    return ResolvedSample(
        golden_items=_fetch_items_by_id(conn, golden_item_ids),
        rotating_items=_fetch_items_by_id(conn, rotating_item_ids),
        investigation_job_ids=investigation_job_ids,
        golden_item_ids=golden_item_ids,
        rotating_item_ids=rotating_item_ids,
    )
