#!/usr/bin/env python
"""Round-14 patent-assignee misattribution audit/repair (2026-09-07,
docs/qa/content_review/CR-patents.md; user report: patent id 64, CN112074705A, was attributed to
Anduril purely because its Google-Patents search snippet named an unrelated FPGA component,
"(Lattice Semiconductor Corporation, USA)" -- "Lattice" is Anduril's own *product* alias in
``config/watchlist.yaml``, not a company-name alias).

Root cause and the code-level fix (``agent/eoa/pipeline/entity_normalize.py``'s
``find_watchlist_company_names_in_text`` / ``product_aliases``; ``agent/eoa/patents/scan.py``'s
``_assignee_candidates_in_text``/``_backfill_patent_fields``/``enrich_stored_patents_missing_
assignee``) live in those modules' own docstrings and docs/qa/content_review/CR-patents.md -- this
script is the one-off audit/backfill over the *existing* rows that predate that fix.

For every patent whose current ``assignees`` did **not** come from a structured source (EPO
OPS/USPTO ODP -- ``source in ('epo_ops', 'uspto_odp')``, always trusted) -- i.e. every
``source = 'google_patents_search'`` row, since a keyless search-result assignee is only ever a
best-effort text-match, never independently confirmed -- this fetches that patent's own individual
Google Patents *detail page* (reusing ``eoa.patents.scan``'s existing SSRF-guarded fetch/pacing:
``_fetch_patent_detail_html``/``_parse_google_patent_detail_html``, ``_ENRICH_SLEEP_S`` seconds
apart, never in parallel) and compares the page's real ``DC.contributor`` assignee(s) against what
is currently stored. A genuine detail-page assignee always wins (rule c): it corrects a wrong
value, fills a gap, or (when the two already agree) simply stamps ``raw.assignee_source =
'detail_page'`` so the routine enrichment pass never re-fetches this row again. ``entity_ids`` is
always re-derived from the corrected ``assignees`` (rule d, reusing
``eoa.patents.analyze._resolve_entity_ids`` -- a read-only import, this script does not edit that
module). When the detail page itself carries no assignee data at all (dead link, page changed
shape, ...) but the row's current *only* support for its stored assignee is a watchlist
*product/system* alias with no genuine company-name evidence in the same text (exactly id 64's
bug shape), the now-fixed matcher (``find_watchlist_company_names_in_text``) is used as a
same-process fallback to blank the disproven value outright -- never leaving a known-wrong
assignee in place just because the live page could not be reached this run.

Defaults to a dry run (list every candidate + what would change, apply nothing). Pass ``--apply``
to write. ``--apply`` backs up the pre-change state of every row it is about to touch to
``runtime/backups/repair_round14_patents_<UTC timestamp>.json`` first, and re-verifies its own
writes from a *separate* new connection before exiting. Prints the DB target (host:port/db, never
the password) and refuses port 5433 (docs/qa/loop/round_1_fixes.md's lesson).

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_patents.py                # dry run, all candidates
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_patents.py --pub-number CN112074705A --apply   # id 64 only
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round14_patents.py --apply         # full sweep, write
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.rows import dict_row

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from eoa.db import connection  # noqa: E402
from eoa.patents.scan import (  # noqa: E402
    _ENRICH_SLEEP_S,
    _fetch_patent_detail_html,
    _parse_google_patent_detail_html,
)
from eoa.pipeline.entity_normalize import (  # noqa: E402
    find_watchlist_company_names_in_text,
    resolve_canonical,
)

_BACKUP_DIR = Path(__file__).resolve().parents[1] / "runtime" / "backups"

#: Sources whose assignee is always considered already-confirmed (a structured API record, not a
#: best-effort text match) -- never audited/rewritten by this script.
_TRUSTED_SOURCES = frozenset({"epo_ops", "uspto_odp"})


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
        f"[repair_round14_patents] target {u.hostname}:{u.port}{u.path} "
        f"alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair_round14_patents] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def _candidate_rows(pub_number: str | None, limit: int | None) -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if pub_number:
            cur.execute(
                "SELECT id, pub_number, title, abstract, assignees, entity_ids, raw, source "
                "FROM patents WHERE pub_number = %(p)s",
                {"p": pub_number},
            )
        else:
            cur.execute(
                "SELECT id, pub_number, title, abstract, assignees, entity_ids, raw, source "
                "FROM patents WHERE source <> ALL(%(trusted)s) ORDER BY id"
                + (" LIMIT %(limit)s" if limit else ""),
                {"trusted": list(_TRUSTED_SOURCES), "limit": limit},
            )
        return cur.fetchall()


def _snippet_text(row: dict) -> str:
    raw = row.get("raw") or {}
    return f"{row.get('title') or ''}\n{raw.get('snippet') or ''}"


def _culprit_aliases(stored_assignees: list[str], text: str) -> list[tuple[str, str]]:
    """``[(assignee_name, product_alias_that_matched), ...]`` -- for each currently-stored
    assignee that resolves to a watchlist company, every one of that company's ``product_aliases``
    literally found in ``text`` (title+snippet). Diagnostic only, for the audit table's "which
    watchlist alias caused it" column -- never used to decide the correction itself (the live
    detail-page fetch is)."""
    out: list[tuple[str, str]] = []
    for name in stored_assignees:
        canonical = resolve_canonical(name)
        if not canonical or canonical.get("kind") != "company":
            continue
        for alias in canonical.get("product_aliases") or []:
            if re.search(r"\b" + re.escape(alias) + r"\b", text, re.IGNORECASE):
                out.append((name, alias))
    return out


def _names_equal_ci(a: list[str], b: list[str]) -> bool:
    return {n.strip().casefold() for n in a} == {n.strip().casefold() for n in b}


def _entity_ids_for(assignees: list[str]) -> list[int]:
    """Rule (d): entity_ids always derived from the *final* assignees -- reuses
    ``eoa.patents.analyze``'s own resolution (a read-only import; this script does not edit that
    module, which other agents are concurrently editing)."""
    from eoa.patents.analyze import _resolve_entity_ids

    return _resolve_entity_ids(assignees)


def audit(pub_number: str | None, limit: int | None) -> list[dict]:
    """Dry-run/gather pass: for every candidate row, fetch its detail page and decide what (if
    anything) would change. Never writes. Returns one report entry per candidate."""
    rows = _candidate_rows(pub_number, limit)
    report: list[dict] = []
    for i, row in enumerate(rows):
        if i:
            time.sleep(_ENRICH_SLEEP_S)
        entry: dict = {
            "id": row["id"],
            "pub_number": row["pub_number"],
            "stored_assignees": row["assignees"] or [],
        }
        html = _fetch_patent_detail_html(row["pub_number"])
        if not html:
            entry["outcome"] = "fetch_failed"
        else:
            try:
                detail = _parse_google_patent_detail_html(html)
            except Exception as exc:
                entry["outcome"] = "parse_failed"
                entry["error"] = str(exc)[:200]
                report.append(entry)
                continue
            entry["detail_assignees"] = detail["assignees"]
            entry["culprit_aliases"] = _culprit_aliases(entry["stored_assignees"], _snippet_text(row))
            if detail["assignees"]:
                if not entry["stored_assignees"]:
                    entry["outcome"] = "gap_filled"
                elif not _names_equal_ci(entry["stored_assignees"], detail["assignees"]):
                    entry["outcome"] = "mismatch_corrected"
                else:
                    entry["outcome"] = "confirmed"
                entry["final_assignees"] = detail["assignees"]
            else:
                # No detail-page confirmation available this run. Fall back to the now-fixed
                # matcher against the row's own text: if it no longer supports the stored
                # assignee at all (id 64's exact bug shape -- a product-alias-only match), the
                # value is proven wrong regardless of live-page availability and is blanked.
                fixed = find_watchlist_company_names_in_text(_snippet_text(row))
                if entry["stored_assignees"] and not any(a in fixed for a in entry["stored_assignees"]):
                    entry["outcome"] = "blanked_unsupported"
                    entry["final_assignees"] = []
                else:
                    entry["outcome"] = "unconfirmed_unchanged"
        report.append(entry)
    return report


def _backup_rows(ids: list[int]) -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _BACKUP_DIR / f"repair_round14_patents_{ts}.json"
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, pub_number, assignees, entity_ids, raw FROM patents WHERE id = ANY(%(ids)s) ORDER BY id",
            {"ids": ids},
        )
        rows = cur.fetchall()
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def apply_corrections(report: list[dict]) -> list[dict]:
    """Writes every entry whose ``outcome`` implies a change (``gap_filled``,
    ``mismatch_corrected``, ``blanked_unsupported``) or that needs its confidence marker stamped
    (``confirmed``) -- backs up the pre-change state of every touched row first."""
    to_touch = [
        e for e in report if e["outcome"] in ("gap_filled", "mismatch_corrected", "blanked_unsupported", "confirmed")
    ]
    if not to_touch:
        return []
    backup_path = _backup_rows([e["id"] for e in to_touch])
    print(f"[repair_round14_patents] backed up {len(to_touch)} row(s) to {backup_path}", file=sys.stderr)

    applied: list[dict] = []
    for entry in to_touch:
        final_assignees = entry.get("final_assignees", entry["stored_assignees"])
        entity_ids = _entity_ids_for(final_assignees) if final_assignees else []
        source_tag = "detail_page" if entry["outcome"] != "blanked_unsupported" else "corrected_unsupported"
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE patents SET
                    assignees = %(assignees)s::text[],
                    entity_ids = %(entity_ids)s::bigint[],
                    raw = COALESCE(raw, '{}'::jsonb) || jsonb_build_object('assignee_source', %(src)s::text)
                WHERE id = %(id)s
                """,
                {
                    "assignees": final_assignees or None,
                    "entity_ids": entity_ids or None,
                    "src": source_tag,
                    "id": entry["id"],
                },
            )
        applied.append({**entry, "written_assignees": final_assignees, "written_entity_ids": entity_ids})
    return applied


def _verify(ids: list[int]) -> None:
    """Re-reads every touched row from a *fresh* connection (docs/qa/loop/round_1_fixes.md's own
    lesson: never trust the connection that just wrote)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, pub_number, assignees, entity_ids, raw->>'assignee_source' AS assignee_source "
            "FROM patents WHERE id = ANY(%(ids)s) ORDER BY id",
            {"ids": ids},
        )
        for row in cur.fetchall():
            print(f"  verified id={row['id']} {row['pub_number']}: "
                  f"assignees={row['assignees']} entity_ids={row['entity_ids']} "
                  f"assignee_source={row['assignee_source']}", file=sys.stderr)


def _print_report(report: list[dict]) -> None:
    by_outcome: dict[str, int] = {}
    for e in report:
        by_outcome[e["outcome"]] = by_outcome.get(e["outcome"], 0) + 1
    print(f"[repair_round14_patents] audited {len(report)} candidate row(s)", file=sys.stderr)
    for outcome, count in sorted(by_outcome.items()):
        print(f"  {outcome}: {count}", file=sys.stderr)
    print(file=sys.stderr)
    for e in report:
        if e["outcome"] in ("mismatch_corrected", "blanked_unsupported", "gap_filled"):
            culprits = ", ".join(f"{name} <- {alias}" for name, alias in e.get("culprit_aliases", []))
            print(
                f"  [{e['outcome']}] id={e['id']} {e['pub_number']}: "
                f"stored={e['stored_assignees']!r} -> final={e.get('final_assignees')!r}"
                + (f"  (culprit alias: {culprits})" if culprits else ""),
                file=sys.stderr,
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the corrections (default: dry run)")
    ap.add_argument("--pub-number", default=None, help="audit/repair a single pub_number only (e.g. CN112074705A)")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of candidate rows audited")
    args = ap.parse_args()

    _load_env()
    _print_target()

    report = audit(args.pub_number, args.limit)
    _print_report(report)

    cn_ids = {e["id"] for e in report if e["pub_number"].upper().startswith("CN")}
    raw_assignee_less_ids = {e["id"] for e in report if not e["stored_assignees"]}

    if not args.apply:
        print("\n[repair_round14_patents] dry run -- pass --apply to write", file=sys.stderr)
        return

    applied = apply_corrections(report)
    if applied:
        _verify([e["id"] for e in applied])
    changed_cn = sum(1 for e in applied if e["id"] in cn_ids)
    changed_raw_assignee_less = sum(1 for e in applied if e["id"] in raw_assignee_less_ids)
    print(
        f"\n[repair_round14_patents] applied {len(applied)} correction(s); "
        f"{changed_cn} of {len(cn_ids)} CN patent(s) changed; "
        f"{changed_raw_assignee_less} of {len(raw_assignee_less_ids)} previously-assignee-less patent(s) changed",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
