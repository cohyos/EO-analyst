"""F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): re-apply the tightened tenders quality gate
(``eoa.tenders.scan``'s F24 gate -- see its module docstring and ``_gate_reject_reason``) to every
existing ``tenders`` row and delete the ones that no longer pass.

Written after a live audit found 21 rows, 8 closed 2015-2025, 13 undated "unknown" (never actually
verified against a real page), and outright irrelevant hits (an unrelated "Green Tech Projects
Corp. | CanadaBuys" row, a Scribd PDF reupload of an old RFP). This supersedes
``scripts/purge_tender_junk.py`` (kept working, but only re-checks the original three-part gate at
a much looser relevance floor) with the full, current bar:

  1. its ``url``'s host is on ``config/tenders.yaml``'s (now-expanded) ``deny_domains`` list
     (document-hosting/aggregator sites -- Scribd, DocPlayer, Yumpu, SlideShare, ... -- were added
     for this fix);
  2. its title+``summary_he`` no longer carries at least one DOMAIN-signal keyword for its source
     (``eoa.tenders.scan._matches_keywords``);
  3. it also lacks a PROCUREMENT signal (required for anything not from a structured
     procurement-portal API -- ``eoa.tenders.scan._has_procurement_signal``);
  4. its stored ``relevance`` is below the new floor, ``eoa.tenders.scan.RELEVANCE_MIN_ACCEPT``
     (6, raised from the old script's 2);
  5. it is ``status='unknown'`` with NEITHER a ``deadline`` NOR a ``published_at`` -- 'unknown' is
     only valid for a notice whose page was actually verified (F24); a pre-fix row this undated
     carries no stored evidence that ever happened, so it is purged rather than trusted (the
     review's "13 undated unknown rows" finding);
  6. it is still ``status`` in ('open', 'unknown') with a ``published_at`` older than
     ``eoa.tenders.scan.NOTICE_MAX_AGE_DAYS`` (90 days) -- a stale lead nobody flagged closed.

``notice_type`` and whether the page was actually re-fetched (the two remaining F24 gate checks)
are NOT re-derived here -- neither is persisted on the ``tenders`` row, and re-running the LLM
classification / re-fetching every historical notice's page is a materially heavier operation this
purge intentionally does not perform (``scripts/repair_tenders.py`` does that, separately, for
date/agency/country enrichment). ``status`` in ('closed', 'awarded') is exempt from checks 5-6 --
that lifecycle (close -> archive after 30 days) is ``eoa.tenders.scan._transition_closed`` /
``_archive_stale_closed``'s job, not this purge's; deleting a closed/awarded row outright would
throw away a real historical record F1 (docs/CONVENTIONS.md: never destroy a fetched-content row
outright) says to keep.

A tender row that fails re-check is deleted; its linked ``items`` row (``report_kind='tender'``) is
deleted alongside it ONLY if that item has "no other use" -- ``level`` is ``NULL`` (never triaged)
or ``'archive'``. An item promoted to ``red``/``orange``/``yellow`` by the normal triage pipeline is
left in place (just no longer linked from a tenders row) since some other part of the app may
already reference it. Safe to re-run (idempotent -- rows that already pass are left untouched).

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/purge_stale_tenders.py [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Any

# Windows consoles default stdout to the cp1252 codepage, which cannot encode the Hebrew title
# text this script prints -- reconfigure to UTF-8 (same fix as scripts/repair_tenders.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/purge_stale_tenders.py` without having to set PYTHONPATH=agent
# first (mirrors scripts/purge_tender_junk.py's convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.tenders.scan import (  # noqa: E402
    NOTICE_MAX_AGE_DAYS,
    RELEVANCE_MIN_ACCEPT,
    NoticeRaw,
    _has_procurement_signal,
    _is_denylisted_domain,
    _matches_keywords,
    load_deny_domains,
    load_procurement_signals,
    load_tender_sources,
)

# A tender in one of these statuses is exempt from the undated/stale checks (5/6 above) -- its
# lifecycle is owned by eoa.tenders.scan._transition_closed/_archive_stale_closed, not this purge.
_LIFECYCLE_EXEMPT_STATUSES = ("closed", "awarded")


def _to_date(value: Any) -> dt.date | None:
    if value is None:
        return None
    return value.date() if hasattr(value, "date") else value


def fails_gate(
    row: dict[str, Any],
    sources_by_id: dict[str, Any],
    procurement_signals: list[str],
    deny_domains: list[str],
    *,
    today: dt.date | None = None,
) -> str | None:
    """Returns a short machine-readable reason if ``row`` should be purged, else ``None``. Pure
    function of the row + config (no DB access), so it's directly unit-testable."""
    today = today or dt.date.today()
    url = row.get("url") or ""
    if url and _is_denylisted_domain(url, deny_domains):
        return "deny_domain"

    src = sources_by_id.get(row.get("source") or "")
    domain_keywords = src.keywords if src is not None else None
    src_kind = src.kind if src is not None else "search"  # unknown source -> treat as general web

    notice = NoticeRaw(
        source_id=row.get("source") or "",
        external_ref=row.get("external_ref") or "",
        title=row.get("title") or "",
        summary=row.get("summary_he") or "",
        url=url,
    )
    domain_terms = _matches_keywords(notice, domain_keywords or [])
    if not domain_terms:
        return "no_domain_signal"
    if not _has_procurement_signal(notice, src_kind, procurement_signals):
        return "no_procurement_signal"

    relevance = row.get("relevance")
    if relevance is not None and relevance < RELEVANCE_MIN_ACCEPT:
        return "relevance_below_floor"

    status = row.get("status")
    deadline = row.get("deadline")
    published_at = _to_date(row.get("published_at"))

    if status in _LIFECYCLE_EXEMPT_STATUSES:
        return None

    if status == "unknown" and deadline is None and published_at is None:
        return "unverified_undated_unknown"

    if published_at is not None and (today - published_at).days > NOTICE_MAX_AGE_DAYS:
        return "stale_published_still_open_or_unknown"

    return None


def purge(*, dry_run: bool = False) -> dict[str, Any]:
    sources_by_id = {s.id: s for s in load_tender_sources()}
    procurement_signals = load_procurement_signals()
    deny_domains = load_deny_domains()
    today = dt.date.today()

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tenders")
        rows = cur.fetchall()

    before_by_status: dict[str, int] = {}
    for row in rows:
        s = row.get("status") or "?"
        before_by_status[s] = before_by_status.get(s, 0) + 1

    to_delete: list[tuple[int, str, str]] = []  # (tender_id, status, reason)
    for row in rows:
        reason = fails_gate(row, sources_by_id, procurement_signals, deny_domains, today=today)
        if reason is not None:
            to_delete.append((row["id"], row.get("status") or "?", reason))

    reasons: dict[str, int] = {}
    for _id, _status, reason in to_delete:
        reasons[reason] = reasons.get(reason, 0) + 1

    deleted_tenders = 0
    deleted_items = 0
    kept_items = 0
    if to_delete and not dry_run:
        ids = [i for i, _s, _r in to_delete]
        with db.connection() as conn, conn.cursor() as cur:
            # "No other use" = never triaged (level IS NULL) or triaged straight to archive --
            # only those linked items are deleted alongside their tenders row.
            cur.execute(
                """
                SELECT i.id
                FROM tenders t JOIN items i ON i.id = t.item_id
                WHERE t.id = ANY(%s) AND i.report_kind = 'tender'
                  AND (i.level IS NULL OR i.level = 'archive')
                """,
                (ids,),
            )
            deletable_item_ids = [r["id"] for r in cur.fetchall()]

            cur.execute(
                """
                SELECT count(*) AS n
                FROM tenders t JOIN items i ON i.id = t.item_id
                WHERE t.id = ANY(%s) AND i.report_kind = 'tender'
                  AND i.level IS NOT NULL AND i.level != 'archive'
                """,
                (ids,),
            )
            kept_items = cur.fetchone()["n"]

            cur.execute("DELETE FROM tenders WHERE id = ANY(%s)", (ids,))
            deleted_tenders = cur.rowcount

            if deletable_item_ids:
                cur.execute("DELETE FROM items WHERE id = ANY(%s)", (deletable_item_ids,))
                deleted_items = cur.rowcount

    after_by_status: dict[str, int] = dict(before_by_status)
    if not dry_run:
        for _id, status, _reason in to_delete:
            after_by_status[status] = after_by_status.get(status, 0) - 1
        after_by_status = {k: v for k, v in after_by_status.items() if v > 0}

    return {
        "before_total": len(rows),
        "before_by_status": before_by_status,
        "to_delete": len(to_delete),
        "reasons": reasons,
        "deleted_tenders": deleted_tenders,
        "deleted_items": deleted_items,
        "kept_items": kept_items,
        "after_total": len(rows) - (len(to_delete) if not dry_run else 0),
        "after_by_status": after_by_status,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be deleted without deleting it"
    )
    args = parser.parse_args()

    result = purge(dry_run=args.dry_run)
    print(f"before: {result['before_total']} {result['before_by_status']}")
    print(f"failing re-check: {result['to_delete']} {result['reasons']}")
    if args.dry_run:
        print("(dry run -- nothing deleted)")
    else:
        print(
            f"deleted tenders: {result['deleted_tenders']}, "
            f"deleted items: {result['deleted_items']}, kept items (still in other use): {result['kept_items']}"
        )
    print(f"after: {result['after_total']} {result['after_by_status']}")


if __name__ == "__main__":
    main()
