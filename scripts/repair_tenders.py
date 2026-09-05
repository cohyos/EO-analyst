"""F2 repair: re-run the tender-notice LLM extraction (with a full page fetch this time, per the
F2 fix in ``eoa.tenders.scan._llm_classify``/``_fetch_notice_text``) for every existing ``tenders``
row, to fill in ``published_at``/``deadline``/``agency``/``country`` that were never captured at
ingestion time (search-hit rows only ever had a title+snippet, never the notice's own text), and
recompute ``status`` with the fixed rubric (``eoa.tenders.scan._initial_status``) -- an undated row
that used to default to ``'open'`` forever now becomes ``'unknown'``/``'closed'`` as appropriate.

Reuses ``eoa.tenders.scan``'s own building blocks (``_llm_classify``, ``_apply_extraction_to_notice``,
``_apply_domain_country_fallback``, ``_initial_status``) rather than reimplementing them, so this
script and the live ``scan_tenders`` path can never drift apart. An LLM/fetch failure on one row
only skips *that* row's date/agency/country enrichment -- the domain-fallback country and the
deadline-based status recompute still run on whatever the row already had (docs/CONVENTIONS.md
rule 9: a failing item never stops the batch).

Safe to re-run (idempotent for rows that already have both published_at and deadline -- pass
``--force`` to re-run the LLM extraction on those too). Use ``--dry-run`` to see what would change
without writing it.

Run with the same ``DATABASE_URL``/Ollama as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_tenders.py [--dry-run] [--force] [--role resident]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

# Windows consoles default stdout to the cp1252 codepage, which cannot encode the Hebrew title
# text this script prints -- reconfigure to UTF-8 so `python scripts/repair_tenders.py` doesn't
# crash mid-report (same fix as scripts/repair_forecast_rationales.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/repair_tenders.py` without having to set PYTHONPATH=agent first
# (mirrors scripts/purge_tender_junk.py's convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.errors import LLMOutputError, ResourceUnavailable  # noqa: E402
from eoa.tenders.scan import (  # noqa: E402
    RELEVANCE_UNKNOWN,
    NoticeRaw,
    _apply_domain_country_fallback,
    _apply_extraction_to_notice,
    _initial_status,
    _llm_classify,
    load_tender_sources,
)


def _to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def _notice_from_row(row: dict[str, Any]) -> NoticeRaw:
    return NoticeRaw(
        source_id=row.get("source") or "",
        external_ref=row.get("external_ref") or "",
        title=row.get("title") or "",
        summary=row.get("summary_he") or "",
        agency=row.get("agency"),
        country=row.get("country"),
        published_at=_to_date(row.get("published_at")),
        deadline=row.get("deadline"),
        url=row.get("url"),
    )


def repair(*, dry_run: bool = False, force: bool = False, role: str = "resident") -> list[dict[str, Any]]:
    sources_by_id = {s.id: s for s in load_tender_sources()}
    today = dt.date.today()

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tenders ORDER BY id")
        rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        already_dated = row.get("published_at") is not None and row.get("deadline") is not None
        if already_dated and not force:
            results.append(
                {
                    "id": row["id"],
                    "title": row.get("title"),
                    "skipped": "already_dated",
                    "status": row.get("status"),
                }
            )
            continue

        notice = _notice_from_row(row)
        src = sources_by_id.get(notice.source_id)
        src_kind = src.kind if src is not None else "search"

        extract = None
        extract_error: str | None = None
        try:
            extract = _llm_classify(notice, role=role, interactive=False, src_kind=src_kind)
        except (ResourceUnavailable, LLMOutputError) as exc:
            extract_error = str(exc)[:200]
        except Exception as exc:  # one row's failure must not stop the batch
            extract_error = str(exc)[:200]

        before = {
            "status": row.get("status"),
            "published_at": row.get("published_at"),
            "deadline": row.get("deadline"),
            "agency": row.get("agency"),
            "country": row.get("country"),
        }

        if extract is not None:
            _apply_extraction_to_notice(notice, extract)
        _apply_domain_country_fallback(notice)

        status_override = "unknown" if extract is not None and extract.relevance == RELEVANCE_UNKNOWN else None
        notice_type = extract.notice_type if extract is not None else None
        new_status = status_override or _initial_status(notice, today, notice_type)

        after = {
            "status": new_status,
            "published_at": notice.published_at,
            "deadline": notice.deadline,
            "agency": notice.agency,
            "country": notice.country,
        }

        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tenders SET
                        published_at = %(published_at)s, deadline = %(deadline)s,
                        agency = %(agency)s, country = %(country)s, status = %(status)s
                    WHERE id = %(id)s
                    """,
                    {
                        "id": row["id"],
                        "published_at": dt.datetime.combine(notice.published_at, dt.time(), tzinfo=dt.UTC)
                        if notice.published_at
                        else None,
                        "deadline": notice.deadline,
                        "agency": notice.agency,
                        "country": notice.country,
                        "status": new_status,
                    },
                )

        results.append(
            {
                "id": row["id"],
                "title": row.get("title"),
                "before": before,
                "after": after,
                "extract_error": extract_error,
            }
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    parser.add_argument(
        "--force", action="store_true", help="re-run extraction even for rows that already have both dates"
    )
    parser.add_argument("--role", default="resident", help="LLM role to use (default: resident)")
    args = parser.parse_args()

    results = repair(dry_run=args.dry_run, force=args.force, role=args.role)
    for r in results:
        print(f"--- id={r['id']} title={(r.get('title') or '')[:70]!r} ---")
        if r.get("skipped"):
            print(f"  skipped: {r['skipped']} (status={r.get('status')})")
            continue
        b, a = r["before"], r["after"]
        print(f"  status:       {b['status']!r} -> {a['status']!r}")
        print(f"  published_at: {b['published_at']} -> {a['published_at']}")
        print(f"  deadline:     {b['deadline']} -> {a['deadline']}")
        print(f"  agency:       {b['agency']!r} -> {a['agency']!r}")
        print(f"  country:      {b['country']!r} -> {a['country']!r}")
        if r.get("extract_error"):
            print(f"  (LLM extraction failed, dates/agency/country from domain-fallback only: {r['extract_error']})")

    if args.dry_run:
        print("\n(dry run -- nothing written)")


if __name__ == "__main__":
    main()
