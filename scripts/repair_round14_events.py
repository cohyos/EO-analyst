#!/usr/bin/env python
"""Round-14 events repair (docs/qa/content_review/CR-factcheck.md, independent fact-check pass
2026-09-07): ``eoa.pipeline.event_grounding`` did not exist before this round -- the structured
``events`` extraction had no grounding guard at all (unlike ``summary_he``/``so_what_he``/
``key_facts``, already covered by ``eoa.pipeline.analysis_grounding`` + its own
``scripts/repair_round14_grounding.py``). This script is the one-time (and re-runnable) sweep over
every ``events`` row that was already persisted before the guard existed.

Headline finding: events 22/23 (item 81, a Globes article about Anduril appointing Amikam Norkin
to head its Israel operation) -- event 22 was extracted as ``kind='m_and_a'``,
``amount_usd=$10,000,000,000``, ``customer='Israel Ministry of Defense and IDF'`` from a source
that describes a personnel appointment, no transaction, and no IMOD/IDF counterparty of any kind;
event 23 duplicated the same $10B figure (this one real -- an in-progress financing round the
source does describe) under the same wrong 'm_and_a' kind. Event 22's fabricated row was then
promoted into monthly's own "top 10 events by value" table and into bd_il's procurement pipeline
table as a fictitious "$10B fighter-jet / targeting-pod" opportunity. See
``eoa.pipeline.event_grounding``'s own module docstring for the full rule set and reasoning.

Three passes, run in this order (each is independently idempotent -- a second run with nothing
left to fix reports zero changes):

  1. ``ground``      -- :func:`eoa.pipeline.event_grounding.ground_event` over every row (kind
                        reclassification, amount/customer/party grounding, confidence cap).
  2. ``reconcile``    -- :func:`eoa.pipeline.event_grounding.reconcile_events` over the
                        *already-grounded* rows: merges cross-source duplicates (e.g. events
                        54/107, the same $270M Elbit SPECTRO/ISR contract reported by two outlets a
                        day apart) into one row, recording sibling item ids in the new
                        ``events.source_item_ids`` column (``db/migrations/versions/
                        0030_events_grounding.py``) and, when the cluster's own amounts disagree
                        (the Greece $4B/€3.1B/€3.5B case), appending a reconciliation note to
                        ``summary_he`` instead of silently keeping one figure.
  3. ``apply``        -- writes both passes' results: UPDATEs every grounded/reconciled ``keep``
                        row, DELETEs every merged-away sibling row.

Prints a table of every changed row (id, item, field, before -> after, evidence quote) -- events
22/23 are always printed first, per the task brief, followed by every other change in id order.

Dry-run by default; ``--apply`` writes. Every row about to change is backed up first to
``runtime/backups/repair_round14_events_<UTC timestamp>.json`` (full before-image of every row that
will be updated or deleted). Prints the DB target (host:port/db, never the password) and the
alembic head first, and refuses port 5433, per docs/qa/loop/round_1_fixes.md's lesson. ``--apply``
re-verifies its own writes from a *separate* new connection before exiting.

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_events.py            # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_events.py --apply     # write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from psycopg.rows import dict_row

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from eoa.db import connection  # noqa: E402
from eoa.pipeline.event_grounding import ground_event, reconcile_events  # noqa: E402

_BACKUP_DIR = Path(__file__).resolve().parents[1] / "runtime" / "backups"
_EVENT_COLUMNS = (
    "id", "item_id", "kind", "title", "date", "amount_usd", "currency", "parties", "customer",
    "program", "summary_he", "confidence", "source_item_ids",
)  # fmt: skip
_PRIORITY_EVENT_IDS = (22, 23)  # task brief: "fix events 22/23 first and show them"


def _load_env() -> None:
    env = Path(__file__).resolve().parents[1] / "runtime" / "eoa.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def _print_target() -> None:
    u = urlsplit(os.environ.get("DATABASE_URL", ""))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()
    print(
        f"[repair_round14_events] target {u.hostname}:{u.port}{u.path} "
        f"alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair_round14_events] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def _backup(rows: list[dict]) -> Path | None:
    if not rows:
        return None
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _BACKUP_DIR / f"repair_round14_events_{ts}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[repair_round14_events] backed up {len(rows)} row(s) -> {path}", file=sys.stderr)
    return path


def _fetch_all_events_with_items() -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events ORDER BY id")
        events = cur.fetchall()
        cur.execute("SELECT id, title, clean_text, raw_text FROM items")
        items = {r["id"]: r for r in cur.fetchall()}
    for ev in events:
        ev["_item"] = items.get(ev["item_id"], {})
    return events


# --------------------------------------------------------------------------
# pass 1: per-event grounding
# --------------------------------------------------------------------------


def _as_float(v: Any) -> float | None:
    return float(v) if v is not None else None


def _grounded_differs_from_row(ev: dict, grounded) -> bool:
    """True if any field :func:`ground_event` actually returns differs from what's on the row
    today -- deliberately checked independently of ``grounded.changes`` (which only logs a
    *rejection*, e.g. a dropped/reclassified field): a placeholder customer ("לא צוין") is silently
    normalised to ``None`` with no entry in ``changes`` at all (it was never real information to
    begin with, so it isn't logged as something "lost"), but the row still needs updating -- gating
    on ``changes`` alone missed exactly this for event 107 the first time this script ran."""
    return (
        ev.get("kind") != grounded.kind
        or _as_float(ev.get("amount_usd")) != _as_float(grounded.amount_usd)
        or ev.get("currency") != grounded.currency
        or (ev.get("parties") or []) != (grounded.parties or [])
        or ev.get("customer") != grounded.customer
        or _as_float(ev.get("confidence")) != _as_float(grounded.confidence)
    )


def run_grounding(events: list[dict]) -> dict[int, dict]:
    """Returns ``{event_id: {"grounded": GroundedEvent, "before": {...}}}`` for every event whose
    grounded result differs from what's on the row today (see :func:`_grounded_differs_from_row` --
    not simply every event with a non-empty ``grounded.changes``, which misses a silent
    placeholder-customer normalisation)."""
    out: dict[int, dict] = {}
    for ev in events:
        item = ev["_item"]
        grounded = ground_event(item, ev)
        if not grounded.changes and not _grounded_differs_from_row(ev, grounded):
            continue
        out[ev["id"]] = {
            "item_id": ev["item_id"],
            "item_title": (item.get("title") or "")[:120],
            "grounded": grounded,
            "before": {k: ev.get(k) for k in _EVENT_COLUMNS},
        }
    return out


# --------------------------------------------------------------------------
# pass 2: cross-source reconciliation
# --------------------------------------------------------------------------


def run_reconciliation(events: list[dict], grounded_by_id: dict[int, dict]) -> list[dict]:
    """Applies pass-1's grounded values on top of the raw rows, then runs
    :func:`reconcile_events`. Returns one dict per cluster with >1 member (singleton clusters --
    the overwhelming majority -- are not interesting to this pass and are skipped)."""
    working: list[dict] = []
    for ev in events:
        row = {k: ev.get(k) for k in _EVENT_COLUMNS}
        g = grounded_by_id.get(ev["id"], {}).get("grounded")
        if g is not None:
            row.update(
                kind=g.kind, amount_usd=g.amount_usd, currency=g.currency, parties=g.parties,
                customer=g.customer, summary_he=g.summary_he, confidence=g.confidence,
            )  # fmt: skip
        working.append(row)

    clusters = reconcile_events(working)
    merges: list[dict] = []
    for c in clusters:
        if not c.member_ids:
            continue
        merges.append(
            {
                "keep_id": c.keep["id"],
                "keep_item_id": c.keep["item_id"],
                "merged_item_ids": c.merged_item_ids,
                "member_ids": c.member_ids,  # the events.id rows to delete -- see ReconciledEvent
                "keep_after": c.keep,
                "amounts_reconciled": c.amounts_reconciled,
            }
        )
    return merges


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def _print_changed_row_table(grounded_by_id: dict[int, dict]) -> None:
    ordered_ids = [i for i in _PRIORITY_EVENT_IDS if i in grounded_by_id]
    ordered_ids += sorted(i for i in grounded_by_id if i not in _PRIORITY_EVENT_IDS)
    print(f"\n=== grounding changes: {len(ordered_ids)} event(s) ===")
    header = f"{'id':>6} {'item':>6}  {'field':<12} {'before':<40} {'after':<30} evidence"
    print(header)
    print("-" * len(header))
    for eid in ordered_ids:
        rec = grounded_by_id[eid]
        for c in rec["grounded"].changes:
            before = str(c.before)[:38]
            after = str(c.after)[:28]
            print(f"{eid:>6} {rec['item_id']:>6}  {c.field:<12} {before:<40} {after:<30} {c.evidence[:80]}")


def _print_merges_table(merges: list[dict]) -> None:
    print(f"\n=== reconciliation merges: {len(merges)} cluster(s), {sum(len(m['member_ids']) for m in merges)} row(s) deleted ===")
    for m in merges:
        note = " (amounts reconciled -- range kept in summary_he)" if m["amounts_reconciled"] else ""
        same_item = " [same-item near-duplicate]" if not m["merged_item_ids"] else ""
        print(
            f"keep event {m['keep_id']} (item {m['keep_item_id']}) "
            f"<- deleted event(s) {m['member_ids']} (item(s) {m['merged_item_ids']}){note}{same_item}"
        )


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def _apply(grounded_by_id: dict[int, dict], merges: list[dict]) -> None:
    backup_rows: list[dict] = []
    for eid, rec in grounded_by_id.items():
        backup_rows.append({"table": "events", "op": "update", "id": eid, "before": rec["before"]})
    all_sibling_ids = {sid for m in merges for sid in m["member_ids"]}
    if all_sibling_ids:
        with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events WHERE id = ANY(%(ids)s)", {"ids": list(all_sibling_ids)})
            for row in cur.fetchall():
                backup_rows.append({"table": "events", "op": "delete", "id": row["id"], "before": dict(row)})

    _backup(backup_rows)

    with connection() as conn, conn.cursor() as cur:
        for eid, rec in grounded_by_id.items():
            g = rec["grounded"]
            cur.execute(
                """
                UPDATE events SET kind=%(kind)s, amount_usd=%(amount_usd)s, currency=%(currency)s,
                       parties=%(parties)s, customer=%(customer)s, summary_he=%(summary_he)s,
                       confidence=%(confidence)s, updated_at=now()
                WHERE id=%(id)s
                """,
                {
                    "kind": g.kind, "amount_usd": g.amount_usd, "currency": g.currency,
                    "parties": g.parties, "customer": g.customer, "summary_he": g.summary_he,
                    "confidence": g.confidence, "id": eid,
                },
            )
        for m in merges:
            keep = m["keep_after"]
            cur.execute(
                """
                UPDATE events SET amount_usd=%(amount_usd)s, currency=%(currency)s,
                       parties=%(parties)s, customer=%(customer)s, summary_he=%(summary_he)s,
                       confidence=%(confidence)s, source_item_ids=%(source_item_ids)s, updated_at=now()
                WHERE id=%(id)s
                """,
                {
                    "amount_usd": keep.get("amount_usd"), "currency": keep.get("currency"),
                    "parties": keep.get("parties"), "customer": keep.get("customer"),
                    "summary_he": keep.get("summary_he"), "confidence": keep.get("confidence"),
                    "source_item_ids": m["merged_item_ids"], "id": m["keep_id"],
                },
            )
            if m["member_ids"]:
                cur.execute("DELETE FROM events WHERE id = ANY(%(ids)s)", {"ids": m["member_ids"]})
        # eoa.db.connection() commits on clean exit from its own `with` block -- no explicit
        # commit needed (or wanted: doing it mid-block here wouldn't match that contract).


def _verify(grounded_by_id: dict[int, dict], merges: list[dict]) -> dict:
    """Re-checks, from a fresh connection, that (a) every grounded row's persisted change set is
    now empty (grounding has nothing left to strip) and (b) every merged-away sibling id is gone."""
    still_changing: list[int] = []
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        for eid in grounded_by_id:
            cur.execute(f"SELECT {', '.join(_EVENT_COLUMNS)} FROM events WHERE id=%(id)s", {"id": eid})
            row = cur.fetchone()
            if row is None:
                continue
            cur.execute("SELECT id, title, clean_text, raw_text FROM items WHERE id=%(id)s", {"id": row["item_id"]})
            item = cur.fetchone() or {}
            g2 = ground_event(item, row)
            if g2.changes:
                still_changing.append(eid)

    keep_rows_missing: list[int] = []
    siblings_still_present: list[int] = []
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        for m in merges:
            cur.execute("SELECT id FROM events WHERE id=%(id)s", {"id": m["keep_id"]})
            if cur.fetchone() is None:
                keep_rows_missing.append(m["keep_id"])
            if m["member_ids"]:
                cur.execute("SELECT id FROM events WHERE id = ANY(%(ids)s)", {"ids": m["member_ids"]})
                siblings_still_present.extend(r["id"] for r in cur.fetchall())

    return {
        "events_still_ungrounded": still_changing,
        "keep_rows_missing_after_apply": keep_rows_missing,
        "merged_siblings_still_present": siblings_still_present,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    args = ap.parse_args()

    _load_env()
    _print_target()

    events = _fetch_all_events_with_items()
    grounded_by_id = run_grounding(events)
    merges = run_reconciliation(events, grounded_by_id)

    _print_changed_row_table(grounded_by_id)
    _print_merges_table(merges)

    report: dict = {
        "mode": "apply" if args.apply else "dry_run",
        "events_checked": len(events),
        "events_changed": len(grounded_by_id),
        "clusters_merged": len(merges),
        "events_deleted_by_merge": sum(len(m["member_ids"]) for m in merges),
        "priority_events": {
            str(eid): [asdict(c) for c in grounded_by_id[eid]["grounded"].changes]
            for eid in _PRIORITY_EVENT_IDS
            if eid in grounded_by_id
        },
    }

    if args.apply:
        _apply(grounded_by_id, merges)
        report["verify"] = _verify(grounded_by_id, merges)

    print("\n" + json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
