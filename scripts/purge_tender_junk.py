"""One-off cleanup: re-apply the tenders two-signal gate + LLM-relevance floor
(``agent/eoa/tenders/scan.py``) to every existing ``tenders`` row and delete the ones that no
longer pass, along with their linked ``items`` row (``report_kind = 'tender'``).

Written after tightening the gate (coordinator feedback, 2026-09-04): live ``/tenders`` results
included generic web content (Wikipedia/NASA articles merely mentioning "infrared", a Reddit
thread, a market-research report) and one real-but-irrelevant UK Contracts Finder notice that only
mentioned a domain term as an incidental line item. A row now fails re-check (and is deleted) if
any of:

  1. its ``url``'s host is on ``config/tenders.yaml``'s ``deny_domains`` list;
  2. its title+``summary_he`` no longer carries at least one DOMAIN-signal keyword
     (``config/tenders.yaml``'s ``keywords`` for the row's own source, per
     ``eoa.tenders.scan._matches_keywords``);
  3. it also lacks a PROCUREMENT signal -- required for anything not sourced from a structured
     procurement-portal API (``eoa.tenders.scan._has_procurement_signal``; TED/Contracts Finder
     rows are exempt from this specific check, same as at ingestion time);
  4. its stored ``relevance`` is ``<= 2`` (the LLM-relevance persistence floor -- a row that was
     inserted before that gate existed, or whose LLM enrichment never ran).

Safe to re-run (idempotent -- rows that already pass are left untouched). Use ``--dry-run`` to see
what would be deleted without deleting it.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/purge_tender_junk.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Allow running as `python scripts/purge_tender_junk.py` without having to set PYTHONPATH=agent
# first (the documented invocation still works too -- this is just a convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.tenders.scan import (  # noqa: E402
    RELEVANCE_REJECT_MAX,
    NoticeRaw,
    _has_procurement_signal,
    _is_denylisted_domain,
    _matches_keywords,
    load_deny_domains,
    load_procurement_signals,
    load_tender_sources,
)


def _fails_gate(row: dict[str, Any], sources_by_id: dict[str, Any], procurement_signals: list[str], deny_domains: list[str]) -> str | None:
    """Returns a short reason string if `row` should be purged, else None."""
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
    if relevance is not None and relevance <= RELEVANCE_REJECT_MAX:
        return "relevance_too_low"

    return None


def purge(*, dry_run: bool = False) -> dict[str, Any]:
    sources_by_id = {s.id: s for s in load_tender_sources()}
    procurement_signals = load_procurement_signals()
    deny_domains = load_deny_domains()

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM tenders")
        rows = cur.fetchall()

    before = len(rows)
    to_delete: list[tuple[int, str]] = []
    for row in rows:
        reason = _fails_gate(row, sources_by_id, procurement_signals, deny_domains)
        if reason is not None:
            to_delete.append((row["id"], reason))

    reasons: dict[str, int] = {}
    for _id, reason in to_delete:
        reasons[reason] = reasons.get(reason, 0) + 1

    deleted_tenders = 0
    deleted_items = 0
    if to_delete and not dry_run:
        ids = [i for i, _ in to_delete]
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT item_id FROM tenders WHERE id = ANY(%s) AND item_id IS NOT NULL", (ids,))
            item_ids = [r["item_id"] for r in cur.fetchall()]
            cur.execute("DELETE FROM tenders WHERE id = ANY(%s)", (ids,))
            deleted_tenders = cur.rowcount
            if item_ids:
                cur.execute(
                    "DELETE FROM items WHERE id = ANY(%s) AND report_kind = 'tender'", (item_ids,)
                )
                deleted_items = cur.rowcount

    # Belt-and-suspenders: an `items` row can end up report_kind='tender' with no *surviving*
    # tenders.item_id pointer even outside the batch just deleted above -- e.g. two different
    # scan_tenders() notices resolving (via insert_item's ON CONFLICT (url)) to the same existing
    # item, where only one of the two owning `tenders` rows got captured by a given purge pass.
    # Never deletes an item some other tenders row still legitimately points to.
    if not dry_run:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM items WHERE report_kind = 'tender' "
                "AND id NOT IN (SELECT item_id FROM tenders WHERE item_id IS NOT NULL)"
            )
            deleted_items += cur.rowcount

    after = before - (len(to_delete) if not dry_run else 0)
    return {
        "before": before,
        "to_delete": len(to_delete),
        "reasons": reasons,
        "deleted_tenders": deleted_tenders,
        "deleted_items": deleted_items,
        "after": after,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be deleted without deleting it"
    )
    args = parser.parse_args()

    result = purge(dry_run=args.dry_run)
    print(f"before: {result['before']}")
    print(f"failing re-check: {result['to_delete']} {result['reasons']}")
    if args.dry_run:
        print("(dry run -- nothing deleted)")
    else:
        print(f"deleted tenders: {result['deleted_tenders']}, deleted items: {result['deleted_items']}")
    print(f"after: {result['after']}")


if __name__ == "__main__":
    main()
