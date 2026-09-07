#!/usr/bin/env python
"""Round-14 patent-description text-grounding audit/repair (2026-09-07,
docs/qa/content_review/CR-patents.md / CR-factcheck.md; independent fact-check + editing review,
patent_survey sections): US10506436B1 ("Lattice mesh", a real Anduril patent) has a stored
``abstract`` that is literally a USPTO assignment-transfer notice ("2019-03-07 Assigned to Anduril
Industries Inc...") with **zero technical content**, yet its ``claims_summary_he``/``so_what_he``
(and the Anduril survey's per-patent "התקדמות" column) describe a fully invented optical
lens/mirror/fiber array.

Root cause and the code-level fix (``agent/eoa/patents/analyze.py``'s two new guards --
``_has_sufficient_technical_text``/``analyze_one_patent_row`` guard 1, ``ground_generated_text``
guard 2; ``agent/eoa/patents/scan.py``'s ``_parse_abstract_from_detail_html``/
``_backfill_patent_fields(..., overwrite_abstract=True)``) live in those modules' own docstrings.
This script is the one-off audit/repair over the *existing* rows that predate that fix -- a row
``analyze_patents`` already wrote a ``claims_summary_he`` for, before guard 1/guard 2 existed.

Scope: every ``patents`` row with ``claims_summary_he IS NOT NULL`` (a row never analyzed at all
has nothing to audit -- guard 1 in ``analyze.py`` now handles it correctly the first time
``analyze_patents`` reaches it, no repair needed). For each:

1. **Text-less** (current abstract + any ``raw.claims_text`` under :data:`_MIN_TECHNICAL_WORDS`
   words, and ``claims_summary_he`` is not already the honest placeholder): first tries to recover
   a *real* abstract via the patent's own Google Patents detail page (reusing
   ``eoa.patents.scan``'s existing SSRF-guarded fetch + pacing --
   ``_fetch_patent_detail_html``/``_parse_abstract_from_detail_html``, ``_ENRICH_SLEEP_S`` seconds
   apart, never in parallel; confirmed live 2026-09-07: US10506436B1's own detail page carries a
   genuine ~120-word "lattice mesh" networking/registration abstract in a ``DC.description`` meta
   tag, nothing to do with optics). If that recovers >= :data:`_MIN_TECHNICAL_WORDS` words of real
   text, the abstract is backfilled (overwriting the low-quality search snippet) and the row is
   *re-analyzed* through the exact same guarded pipeline ``analyze_patents`` uses
   (``eoa.patents.analyze.analyze_one_patent_row``) to produce a fresh, grounded description.
   Otherwise (no real abstract recoverable) falls back to the fixed, honest placeholder
   (``eoa.patents.analyze._persist_no_text_analysis``) -- never leaves the old fabricated text in
   place.
2. **Text was sufficient but the stored description still isn't grounded** (a row analyzed before
   guard 2 existed): the stored ``claims_summary_he``/``so_what_he`` are re-checked with the exact
   same ``eoa.patents.analyze.ground_generated_text`` guard 2 uses; any sentence that still isn't
   grounded is stripped and the row rewritten.

Defaults to a dry run (list every candidate + what would change, apply nothing -- fetches ARE made
in a dry run, since that is how the audit *finds out* whether a real abstract is recoverable; only
the DB write is gated by ``--apply``). ``--apply`` backs up the pre-change state of every row it is
about to touch to ``runtime/backups/repair_round14_patent_text_<UTC timestamp>.json`` first, and
re-verifies its own writes from a *separate* new connection before exiting. US10506436B1 is always
sorted to the front of the candidate list (and given its own explicit before/after block) when
present, per the task's "fix it first" instruction. Prints the DB target (host:port/db, never the
password) and refuses port 5433 (docs/qa/loop/round_1_fixes.md's lesson).

    PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe scripts/repair_round14_patent_text.py                               # dry run, all candidates
    PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe scripts/repair_round14_patent_text.py --pub-number US10506436B1 --apply   # the flagship patent only
    PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe scripts/repair_round14_patent_text.py --apply                        # full sweep, write

``EOA_PIPELINE=1`` is only required for ``--apply`` on a row this script decides to *re-analyze*
(a real abstract was recovered) -- that path makes a real LLM call via
``eoa.patents.analyze.analyze_one_patent_row``, same as any other analysis pass in this project.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
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
from eoa.patents.analyze import (  # noqa: E402
    _MIN_TECHNICAL_WORDS,
    _NO_TEXT_SUMMARY_HE,
    _grounding_source_pool,
    _has_sufficient_technical_text,
    _israel_relevance,
    _persist_no_text_analysis,
    _resolve_entity_ids,
    _tech_dev_subdomain_keys,
    analyze_one_patent_row,
    ground_generated_text,
)
from eoa.patents.models import PatentRecord  # noqa: E402
from eoa.patents.scan import (  # noqa: E402
    _ENRICH_SLEEP_S,
    _backfill_patent_fields,
    _fetch_patent_detail_html,
    _parse_abstract_from_detail_html,
)

_BACKUP_DIR = Path(__file__).resolve().parents[1] / "runtime" / "backups"
_FLAGSHIP_PUB_NUMBER = "US10506436B1"


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
        f"[repair_round14_patent_text] target {u.hostname}:{u.port}{u.path} "
        f"alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print(
            "[repair_round14_patent_text] refusing to run against port 5433 (retired Docker DB)",
            file=sys.stderr,
        )
        sys.exit(2)


def _candidate_rows(pub_number: str | None, limit: int | None) -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if pub_number:
            cur.execute(
                "SELECT id, pub_number, title, abstract, assignees, cpc, jurisdictions, url, raw, "
                "claims_summary_he, so_what_he FROM patents "
                "WHERE pub_number = %(p)s AND claims_summary_he IS NOT NULL",
                {"p": pub_number},
            )
        else:
            cur.execute(
                "SELECT id, pub_number, title, abstract, assignees, cpc, jurisdictions, url, raw, "
                "claims_summary_he, so_what_he FROM patents "
                "WHERE claims_summary_he IS NOT NULL ORDER BY id" + (" LIMIT %(limit)s" if limit else ""),
                {"limit": limit},
            )
        rows = cur.fetchall()
    # The flagship patent this round's bug report named always sorts first, per task instruction
    # ("fix US10506436B1 first").
    rows.sort(key=lambda r: (r["pub_number"] != _FLAGSHIP_PUB_NUMBER, r["id"]))
    return rows


def _classify(row: dict) -> str:
    """``"already_placeholder"`` | ``"text_less"`` | ``"grounding_check"`` -- never fetches/mutates,
    pure classification off the row's own current DB fields."""
    if row.get("claims_summary_he") == _NO_TEXT_SUMMARY_HE:
        return "already_placeholder"
    if not _has_sufficient_technical_text(row.get("abstract"), row.get("raw")):
        return "text_less"
    return "grounding_check"


def audit(pub_number: str | None, limit: int | None) -> list[dict]:
    """Dry-run/gather pass: for every candidate row, decide what (if anything) would change.
    ``text_less`` candidates DO make a live detail-page fetch (read-only, politely paced) -- that
    is the only way to know whether a real abstract is recoverable; nothing is written to the DB
    here regardless (writes only happen in :func:`apply_repairs`, gated on ``--apply``)."""
    rows = _candidate_rows(pub_number, limit)
    subdomain_keys = _tech_dev_subdomain_keys()
    report: list[dict] = []
    fetch_count = 0
    for row in rows:
        entry: dict = {
            "id": row["id"],
            "pub_number": row["pub_number"],
            "stored_claims_summary_he": row.get("claims_summary_he"),
            "stored_so_what_he": row.get("so_what_he"),
        }
        kind = _classify(row)
        if kind == "already_placeholder":
            entry["outcome"] = "already_placeholder"
            report.append(entry)
            continue
        if kind == "text_less":
            if fetch_count:
                time.sleep(_ENRICH_SLEEP_S)
            fetch_count += 1
            html = _fetch_patent_detail_html(row["pub_number"])
            new_abstract = _parse_abstract_from_detail_html(html) if html else None
            entry["recovered_abstract"] = new_abstract
            recovered_words = len((new_abstract or "").split())
            entry["recovered_word_count"] = recovered_words
            if new_abstract and recovered_words >= _MIN_TECHNICAL_WORDS:
                entry["outcome"] = "abstract_recovered_needs_reanalysis"
                entry["row"] = row
                entry["subdomain_keys"] = subdomain_keys
            else:
                entry["outcome"] = "fallback_placeholder"
                entry["row"] = row
            report.append(entry)
            continue
        # kind == "grounding_check": the row's text was sufficient at analysis time -- check
        # whether the stored description is still grounded against it.
        source_pool = _grounding_source_pool(row)
        grounded_claims = ground_generated_text(
            row.get("claims_summary_he") or "",
            source_pool,
            pub_number=row["pub_number"],
            field_name="claims_summary_he",
        )
        grounded_so_what = ground_generated_text(
            row.get("so_what_he") or "", source_pool, pub_number=row["pub_number"], field_name="so_what_he"
        )
        changed = grounded_claims != (row.get("claims_summary_he") or "") or grounded_so_what != (
            row.get("so_what_he") or ""
        )
        if changed:
            entry["outcome"] = "ungrounded_sentences_stripped"
            entry["grounded_claims_summary_he"] = grounded_claims
            entry["grounded_so_what_he"] = grounded_so_what
        else:
            entry["outcome"] = "already_grounded"
        report.append(entry)
    return report


def _backup_rows(ids: list[int]) -> Path:
    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = _BACKUP_DIR / f"repair_round14_patent_text_{ts}.json"
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, pub_number, abstract, claims_summary_he, subdomain, so_what_he, "
            "israel_relevance, entity_ids, raw FROM patents WHERE id = ANY(%(ids)s) ORDER BY id",
            {"ids": ids},
        )
        rows = cur.fetchall()
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def apply_repairs(report: list[dict]) -> list[dict]:
    """Writes every entry whose ``outcome`` implies a change. Backs up the pre-change state of
    every touched row first."""
    to_touch = [
        e
        for e in report
        if e["outcome"]
        in ("abstract_recovered_needs_reanalysis", "fallback_placeholder", "ungrounded_sentences_stripped")
    ]
    if not to_touch:
        return []
    backup_path = _backup_rows([e["id"] for e in to_touch])
    print(f"[repair_round14_patent_text] backed up {len(to_touch)} row(s) to {backup_path}", file=sys.stderr)

    applied: list[dict] = []
    for entry in to_touch:
        if entry["outcome"] == "abstract_recovered_needs_reanalysis":
            row = entry["row"]
            rec = PatentRecord(pub_number=row["pub_number"], abstract=entry["recovered_abstract"])
            _backfill_patent_fields(row["pub_number"], rec, overwrite_abstract=True)
            fresh_row = {**row, "abstract": entry["recovered_abstract"], "raw": {**(row.get("raw") or {}), "abstract_source": "detail_page"}}
            result = analyze_one_patent_row(fresh_row, entry["subdomain_keys"], role="resident", interactive=False)
            entry["reanalysis_outcome"] = result.outcome
            entry["new_claims_summary_he"] = result.out.claims_summary_he if result.out else None
            entry["new_so_what_he"] = result.out.so_what_he if result.out else None
        elif entry["outcome"] == "fallback_placeholder":
            row = entry["row"]
            assignees = row.get("assignees") or []
            relevance = _israel_relevance(
                row.get("title") or "", row.get("abstract") or "", assignees, row.get("jurisdictions") or []
            )
            entity_ids = _resolve_entity_ids(assignees)
            _persist_no_text_analysis(entry["id"], relevance, entity_ids)
        elif entry["outcome"] == "ungrounded_sentences_stripped":
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE patents SET claims_summary_he = %(c)s, so_what_he = %(s)s WHERE id = %(id)s",
                    {
                        "c": entry["grounded_claims_summary_he"],
                        "s": entry["grounded_so_what_he"],
                        "id": entry["id"],
                    },
                )
        applied.append(entry)
    return applied


def _verify(ids: list[int]) -> None:
    """Re-reads every touched row from a *fresh* connection (docs/qa/loop/round_1_fixes.md's own
    lesson: never trust the connection that just wrote)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, pub_number, claims_summary_he, raw->>'text_available' AS text_available, "
            "raw->>'abstract_source' AS abstract_source FROM patents WHERE id = ANY(%(ids)s) ORDER BY id",
            {"ids": ids},
        )
        for row in cur.fetchall():
            summary = (row["claims_summary_he"] or "")[:80]
            print(
                f"  verified id={row['id']} {row['pub_number']}: "
                f"text_available={row['text_available']} abstract_source={row['abstract_source']} "
                f"claims_summary_he[:80]={summary!r}",
                file=sys.stderr,
            )


def _print_flagship_before_after(report: list[dict]) -> None:
    entry = next((e for e in report if e["pub_number"] == _FLAGSHIP_PUB_NUMBER), None)
    if entry is None:
        return
    print(f"\n[repair_round14_patent_text] === {_FLAGSHIP_PUB_NUMBER} (fixed first) ===", file=sys.stderr)
    print(f"  outcome: {entry['outcome']}", file=sys.stderr)
    print(f"  before claims_summary_he: {entry.get('stored_claims_summary_he')!r}", file=sys.stderr)
    if entry["outcome"] == "abstract_recovered_needs_reanalysis":
        after = entry.get("new_claims_summary_he") or "(would be regenerated from the recovered abstract -- pass --apply to see it)"
    elif entry["outcome"] == "fallback_placeholder":
        after = _NO_TEXT_SUMMARY_HE
    elif entry["outcome"] == "ungrounded_sentences_stripped":
        after = entry.get("grounded_claims_summary_he")
    else:
        after = entry.get("stored_claims_summary_he")  # already_placeholder / already_grounded -- unchanged
    print(f"  after  claims_summary_he: {after!r}", file=sys.stderr)
    if entry["outcome"] == "abstract_recovered_needs_reanalysis":
        print(f"  recovered real abstract ({entry['recovered_word_count']} words) from the detail page", file=sys.stderr)


def _print_report(report: list[dict]) -> None:
    by_outcome: dict[str, int] = {}
    for e in report:
        by_outcome[e["outcome"]] = by_outcome.get(e["outcome"], 0) + 1
    print(f"[repair_round14_patent_text] audited {len(report)} candidate row(s)", file=sys.stderr)
    for outcome, count in sorted(by_outcome.items()):
        print(f"  {outcome}: {count}", file=sys.stderr)
    print(file=sys.stderr)
    for e in report:
        if e["outcome"] not in ("already_placeholder", "already_grounded"):
            print(f"  [{e['outcome']}] id={e['id']} {e['pub_number']}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the corrections (default: dry run)")
    ap.add_argument("--pub-number", default=None, help="audit/repair a single pub_number only (e.g. US10506436B1)")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of candidate rows audited")
    args = ap.parse_args()

    _load_env()
    _print_target()

    report = audit(args.pub_number, args.limit)
    _print_report(report)
    _print_flagship_before_after(report)

    if not args.apply:
        print("\n[repair_round14_patent_text] dry run -- pass --apply to write", file=sys.stderr)
        return

    applied = apply_repairs(report)
    if applied:
        _verify([e["id"] for e in applied])
    _print_flagship_before_after(report)  # re-print with post-apply fields (new_*) filled in
    print(f"\n[repair_round14_patent_text] applied {len(applied)} correction(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
