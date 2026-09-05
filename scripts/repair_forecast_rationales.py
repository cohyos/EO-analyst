"""F1 repair: regenerate ``tender_forecasts.rationale_he`` for every row that fails the new
guard (``eoa.tenders.forecast._rationale_guard_failure``) -- no citation, a leaked-reasoning
phrase, or an implausibly long rationale (all symptoms of the root cause fixed in this pass: an
over-long prompt got its *start*, i.e. the task instructions, truncated by Ollama, so the model
narrated its own confused understanding of the request instead of writing a rationale).

Reconstructs enough of a ``ForecastCandidate`` from each row's own stored columns to reuse the
production code path (``eoa.tenders.forecast._rationale_data_block`` +
``_llm_rationale_guarded``) -- the already-computed ``likelihood``/``window_from``/``window_to``
are kept as-is (this script only ever touches ``rationale_he``, never the deterministic fields).
``trigger_item_ids`` comes from the row's own ``sources`` array (``"item:<id>"`` entries, written
by ``_upsert_forecast``); a row written before ``sources`` existed falls back to the single
``trigger_item_id`` column.

Safe to re-run (idempotent -- a row that now passes the guard is left untouched, unless
``--force`` is given to regenerate every candidate row regardless). Use ``--dry-run`` to see what
would change without writing it.

Run with the same ``DATABASE_URL`` as the app and Ollama reachable, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_forecast_rationales.py [--dry-run] [--force] [--role resident]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Windows consoles default stdout to the cp1252 codepage, which cannot encode the Hebrew
# rationale text this script prints -- reconfigure to UTF-8 (available on Python 3.7+'s
# TextIOWrapper) so `python scripts/repair_forecast_rationales.py` doesn't crash mid-report.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/repair_forecast_rationales.py` without having to set
# PYTHONPATH=agent first (mirrors scripts/purge_tender_junk.py's convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.tenders.forecast import (  # noqa: E402
    ForecastCandidate,
    _llm_rationale_guarded,
    _rationale_data_block,
    _rationale_guard_failure,
)


def _parse_trigger_item_ids(row: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for src in row.get("sources") or []:
        if isinstance(src, str) and src.startswith("item:"):
            try:
                ids.append(int(src.split(":", 1)[1]))
            except ValueError:
                continue
    if ids:
        return ids
    if row.get("trigger_item_id") is not None:
        return [row["trigger_item_id"]]
    return []


def _candidate_from_row(row: dict[str, Any]) -> ForecastCandidate | None:
    trigger_item_ids = _parse_trigger_item_ids(row)
    if not trigger_item_ids:
        return None
    trigger_event_ids = [row["trigger_event_id"]] if row.get("trigger_event_id") is not None else [0]
    return ForecastCandidate(
        platform_key=row.get("platform") or "",
        platform_he=row.get("platform") or "",
        buyer_country=row.get("buyer_country"),
        payload_need_he=row.get("payload_need") or "",
        candidate_vendors=list(row.get("candidate_vendors") or []),
        trigger_event_ids=trigger_event_ids,
        trigger_item_ids=trigger_item_ids,
        trigger_texts=[],  # not needed -- likelihood is reused as-is, never recomputed here
        lag_min=0,
        lag_max=0,
    )


def repair(*, dry_run: bool = False, force: bool = False, role: str = "resident") -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tender_forecasts ORDER BY id")
        rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        before = row.get("rationale_he") or ""
        reason = _rationale_guard_failure(before)
        if reason is None and not force:
            continue

        candidate = _candidate_from_row(row)
        if candidate is None:
            results.append(
                {"id": row["id"], "platform": row.get("platform"), "skipped": "no_trigger_items", "before": before}
            )
            continue

        all_item_ids = sorted(set(candidate.trigger_item_ids))
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, url, clean_text, summary_he FROM items WHERE id = ANY(%(ids)s)",
                {"ids": all_item_ids},
            )
            items_by_id = {r["id"]: r for r in cur.fetchall()}

        data_block = _rationale_data_block(candidate, items_by_id)
        window = (row.get("window_from"), row.get("window_to"))
        likelihood = row.get("likelihood") or 0.0
        try:
            after = _llm_rationale_guarded(candidate, likelihood, window, data_block, role=role)
        except Exception as exc:  # a repair run must not die on one bad row
            results.append(
                {"id": row["id"], "platform": row.get("platform"), "error": str(exc)[:200], "before": before}
            )
            continue

        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE tender_forecasts SET rationale_he = %(rationale)s WHERE id = %(id)s",
                    {"rationale": after, "id": row["id"]},
                )

        results.append(
            {
                "id": row["id"],
                "platform": row.get("platform"),
                "before_guard_reason": reason,
                "before": before,
                "after": after,
                "after_guard_reason": _rationale_guard_failure(after),
            }
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    parser.add_argument("--force", action="store_true", help="regenerate every row, not only guard failures")
    parser.add_argument("--role", default="resident", help="LLM role to use (default: resident)")
    args = parser.parse_args()

    results = repair(dry_run=args.dry_run, force=args.force, role=args.role)
    if not results:
        print("no rows needed repair (all rationales already pass the guard)")
        return

    for r in results:
        print(f"--- id={r['id']} platform={r.get('platform')!r} ---")
        if "skipped" in r:
            print(f"  skipped: {r['skipped']}")
            continue
        if "error" in r:
            print(f"  ERROR regenerating: {r['error']}")
            continue
        print(f"  before ({r.get('before_guard_reason')}): {r['before'][:120]!r}")
        print(f"  after  ({r.get('after_guard_reason') or 'ok'}): {r['after'][:120]!r}")

    if args.dry_run:
        print(f"\n(dry run -- {len(results)} row(s) would be updated, nothing written)")
    else:
        written = sum(1 for r in results if "error" not in r and "skipped" not in r)
        print(f"\nupdated {written} row(s)")


if __name__ == "__main__":
    main()
