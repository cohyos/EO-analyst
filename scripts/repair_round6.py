#!/usr/bin/env python
"""Round-6 data repair (docs/qa/loop/round_5_judge.md D1/D2/D3/D9) -- the recurring data defects
the judge has flagged for 3-4 rounds running, plus the pipeline gaps that produced them.

R6-entities (docs/qa/loop/round_6_fixes.md's "R6-entities status", follow-up to R6-data) adds the
`entities_cleanup` subcommand below for the D1/D3 follow-up finding: entities mentioned only by
out-of-scope/archived items (e.g. entity 1208 "Western Burrowing Owl", kind=company) pollute both
the `entities` table and the monthly's "ישויות חדשות החודש" list.

Six independent, individually-runnable subcommands:

    so_what   -- items.so_what_he matching a banned generic-formula phrase
                 (eoa.report.qa_citations.SO_WHAT_TEMPLATE_PHRASES_HE), queried DB-wide (not just
                 the 16 ids the judge originally listed). Re-generated through
                 eoa.pipeline.analyze.repair_so_what_text on the cloud chain, validated (no banned
                 phrase left, 1-3 Hebrew sentences) before being written.
    entities  -- item 22 (entities_mentioned empty for 4 rounds) plus every other in-scope item
                 (level in red/orange/yellow, domain != out_of_scope) with an empty
                 entities_mentioned, capped at 30 -- re-analyzed via
                 eoa.pipeline.analyze.analyze_item + persist_analysis (the pipeline's own per-item
                 entry point, cloud chain).
    triage    -- item 5604's score/level/reason inconsistency, re-triaged via
                 eoa.pipeline.triage.triage_item (cloud chain).
    events    -- the 51 events belonging to items with domain='out_of_scope' or level='archive'
                 (the "owl leak", event 257 on item 2463) -- deleted. Pure SQL, no LLM calls.
    tenders   -- the duplicate 'unknown'/'candidate' tender rows (5x the same Northrop Grumman
                 EO/IR marketing page, 2x the same Counter-UAS category listing, surfaced by
                 different per-country search sources) -- merged (delete all but the oldest id per
                 duplicate group). Pure SQL, no LLM calls. Never touches 'accepted'/'archived' rows
                 or tender_feedback.
    entities_cleanup -- entities mentioned ONLY by items with domain='out_of_scope' or
                 level='archive' (never by any in-scope item) -- deleted, along with every
                 dependent row (graph_edges, discovered live via information_schema; patents'
                 soft-reference entity_ids array). Never deletes a name on config/watchlist.yaml
                 (companies/aliases/strict_aliases, programs, agencies, acquisition_watch +
                 peers_of) or config/payloads_seed.yaml's vendor_entity_name, a company/org/
                 system/program with a country AND at least one in-scope mention, or a name
                 referenced in an existing reports.report_state. Also reports (informational only,
                 never a deletion trigger on its own) which existing entities DB-wide the new
                 junk-name-shape filter (eoa.pipeline.analyze.is_junk_candidate_entity_name) would
                 now block. Pure SQL, no LLM calls.

Every subcommand (and "all") defaults to a dry run (report only); pass --apply to write. so_what/
entities/triage additionally cost real LLM calls on the cloud chain (EOA_PIPELINE=1, forced by this
script) -- a shared --llm-budget (default 25, per the round-6 brief's "keep total LLM calls under
~25") caps how many of those this single invocation will spend, processed in the fixed priority
order triage -> entities -> so_what (ascending id) so the two single-item, explicitly-named
defects (item 5604, item 22) are never starved by the larger so_what batch; anything left over is
reported under "skipped_budget" for a follow-up run. events/tenders/entities_cleanup are pure SQL
and never touch the LLM budget.

Prints the DB target (host:port/db, never the password) and refuses port 5433, per
docs/qa/loop/round_1_fixes.md's lesson. Every --apply run re-verifies its own writes from a
*separate* new connection before exiting.

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round6.py <subcommand>            # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round6.py <subcommand> --apply     # write
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round6.py all --apply --llm-budget 25
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from psycopg import sql
from psycopg.rows import dict_row

_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from eoa.config import CONFIG_DIR, settings  # noqa: E402
from eoa.db import connection  # noqa: E402
from eoa.memory.relational import update_item_fields  # noqa: E402
from eoa.pipeline.analyze import (  # noqa: E402
    _backfill_entities_from_watchlist,
    analyze_item,
    is_junk_candidate_entity_name,
    persist_analysis,
    repair_so_what_text,
)
from eoa.pipeline.entity_normalize import normalize_name_key  # noqa: E402
from eoa.pipeline.triage import (  # noqa: E402
    _reason_conflicting_level,
    level_for,
    triage_item,
    validate_triage_consistency,
)
from eoa.report.qa_citations import SO_WHAT_TEMPLATE_PHRASES_HE, split_sentences  # noqa: E402

_SO_WHAT_RE = re.compile(
    "|".join(re.escape(p) for p in sorted(set(SO_WHAT_TEMPLATE_PHRASES_HE), key=len, reverse=True))
)

#: Task 2 (docs brief): item 22's entities_mentioned has been empty for 4 rounds -- always in
#: scope for repair regardless of its own level/domain (unlike the rest of the "in-scope, empty
#: entities_mentioned" sweep, which only looks at level in red/orange/yellow, domain != out_of_scope).
MANDATORY_ENTITY_REPAIR_ITEM_ID = 22
#: Task 3 (docs brief): the one item with a confirmed reason/score/level inconsistency.
TRIAGE_REPAIR_ITEM_ID = 5604


# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


class LLMBudget:
    """A shared counter every LLM-invoking repair function checks/decrements before calling the
    cloud chain -- enforces this run's total-call cap (brief: "keep total LLM calls under ~25")
    across every subcommand invoked in one process, regardless of order."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit

    def use(self) -> None:
        self.used += 1


def _load_env() -> None:
    env = Path(__file__).resolve().parents[1] / "runtime" / "eoa.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    # Every LLM-invoking repair below must run on the cloud chain, never silently fall back to the
    # local model (round-6 brief, "LLM calls" rule).
    os.environ["EOA_PIPELINE"] = "1"


def _print_target() -> None:
    u = urlsplit(os.environ.get("DATABASE_URL", ""))
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        head = cur.fetchone()
    print(
        f"[repair_round6] target {u.hostname}:{u.port}{u.path} alembic={head['version_num'] if head else '?'}",
        file=sys.stderr,
    )
    if u.port == 5433:
        print("[repair_round6] refusing to run against port 5433 (retired Docker DB)", file=sys.stderr)
        sys.exit(2)


def _fetch_item(item_id: int) -> dict | None:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM items WHERE id = %(id)s", {"id": item_id})
        return cur.fetchone()


# --------------------------------------------------------------------------
# task 1 -- so_what template-phrase repair (D2)
# --------------------------------------------------------------------------


def find_banned_so_what_items() -> list[dict]:
    """Every item (DB-wide, not just the judge's original 16 ids) whose so_what_he matches a
    SO_WHAT_TEMPLATE_PHRASES_HE phrase."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, so_what_he, summary_he FROM items WHERE so_what_he IS NOT NULL ORDER BY id")
        rows = cur.fetchall()
    return [r for r in rows if _SO_WHAT_RE.search(r["so_what_he"] or "")]


def repair_so_what(apply: bool, budget: LLMBudget) -> dict:
    matches = find_banned_so_what_items()
    report: dict = {
        "matched_ids": [r["id"] for r in matches],
        "matched_count": len(matches),
        "repaired": [],
        "rejected": [],
        "skipped_budget": [],
    }
    if not apply:
        return report

    for row in matches:
        if budget.exhausted:
            report["skipped_budget"].append(row["id"])
            continue
        item = _fetch_item(row["id"])
        if item is None:
            report["rejected"].append({"id": row["id"], "reason": "item_vanished"})
            continue
        phrase_m = _SO_WHAT_RE.search(row["so_what_he"] or "")
        phrase = phrase_m.group(0) if phrase_m else ""
        budget.use()
        new_text = repair_so_what_text(
            item, so_what_he=row["so_what_he"] or "", summary_he=row["summary_he"] or "", phrase=phrase
        )
        if not new_text:
            report["rejected"].append({"id": row["id"], "reason": "llm_repair_failed_or_rejected"})
            continue
        if _SO_WHAT_RE.search(new_text):
            report["rejected"].append({"id": row["id"], "reason": "still_generic", "text": new_text})
            continue
        n_sentences = len(split_sentences(new_text))
        if not (1 <= n_sentences <= 3):
            report["rejected"].append(
                {"id": row["id"], "reason": f"sentence_count={n_sentences}", "text": new_text}
            )
            continue
        update_item_fields(row["id"], so_what_he=new_text)
        report["repaired"].append({"id": row["id"], "phrase": phrase, "so_what_he": new_text})
    return report


# --------------------------------------------------------------------------
# task 2 -- entities_mentioned backfill (D3)
# --------------------------------------------------------------------------


def find_empty_entities_items(cap: int = 30) -> dict:
    """Item 22 (mandatory) plus up to `cap` other in-scope (level in red/orange/yellow,
    domain != out_of_scope) items with an empty entities_mentioned."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id FROM items
            WHERE level IN ('red', 'orange', 'yellow')
              AND COALESCE(domain, 'out_of_scope') <> 'out_of_scope'
              AND (entities_mentioned IS NULL OR entities_mentioned = '{}')
            ORDER BY id
            """
        )
        other_ids = [r["id"] for r in cur.fetchall() if r["id"] != MANDATORY_ENTITY_REPAIR_ITEM_ID]
    return {
        "mandatory": MANDATORY_ENTITY_REPAIR_ITEM_ID,
        "in_scope_empty_count": len(other_ids),
        "in_scope_empty_ids": other_ids,
        "other_targets": other_ids[:cap],
        "capped": len(other_ids) > cap,
    }


def _entities_from_events(item_id: int) -> list[str]:
    """Union of every ``events.parties`` name already recorded for ``item_id`` (old events plus
    anything ``persist_analysis`` just inserted), plus every ``graph_edges`` endpoint name
    (src/dst) stamped with this ``item_id`` -- order-preserving/deduped. Discovered live during
    this round's apply run: items 153/290 already had `events` rows naming real parties (TC-Next/
    WeatherNext/GraphCast/Pangu-Weather/IFS HRES for 153; UK MoD/UKDI for 290) from earlier analyze
    passes, and item 22's fresh analyze pass added a graph_edges row (AIM-120 AMRAAM
    INTEGRATES_WITH NASAMS) -- but nothing had ever unioned any of it back into
    `items.entities_mentioned`, which is only ever written by classify.py's own LLM extraction or
    the watchlist-alias backfill (`_backfill_entities_from_watchlist`, which only fires for a
    *watchlist* company/program name, not any named party/entity in general). Pure DB read, no LLM
    cost."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT parties FROM events WHERE item_id = %(id)s", {"id": item_id})
        event_rows = cur.fetchall()
        cur.execute(
            """
            SELECT es.name AS src_name, ed.name AS dst_name
            FROM graph_edges ge
            JOIN entities es ON es.id = ge.src_entity_id
            JOIN entities ed ON ed.id = ge.dst_entity_id
            WHERE ge.item_id = %(id)s
            """,
            {"id": item_id},
        )
        edge_rows = cur.fetchall()
    names: list[str] = []
    for r in event_rows:
        for name in r.get("parties") or []:
            if name and name not in names:
                names.append(name)
    for r in edge_rows:
        for name in (r.get("src_name"), r.get("dst_name")):
            if name and name not in names:
                names.append(name)
    return names


def repair_entities(apply: bool, budget: LLMBudget, cap: int = 30) -> dict:
    found = find_empty_entities_items(cap)
    target_ids = [found["mandatory"], *found["other_targets"]]
    report: dict = {**found, "target_ids": target_ids, "repaired": [], "rejected": [], "skipped_budget": []}

    for item_id in target_ids:
        item = _fetch_item(item_id)
        if item is None:
            report["rejected"].append({"id": item_id, "reason": "item_not_found"})
            continue
        if not apply:
            preview = _backfill_entities_from_watchlist(item)
            report.setdefault("dry_run_preview", []).append(
                {"id": item_id, "watchlist_backfill_preview": preview}
            )
            continue
        if budget.exhausted:
            report["skipped_budget"].append(item_id)
            continue
        budget.use()
        out = analyze_item(item, role="resident", interactive=False)
        persist_analysis(item, out)
        after = _fetch_item(item_id)
        entities_after = (after or {}).get("entities_mentioned") or []
        if not entities_after:
            # The LLM's own extraction (and the watchlist backfill inside persist_analysis) found
            # nothing new -- fall back to whatever named parties this item's events already carry
            # (see _entities_from_events) before giving up on it.
            from_events = _entities_from_events(item_id)
            if from_events:
                update_item_fields(item_id, entities_mentioned=from_events)
                entities_after = from_events
        if entities_after:
            report["repaired"].append({"id": item_id, "entities_mentioned": entities_after})
        else:
            report["rejected"].append({"id": item_id, "reason": "still_empty_after_reanalysis"})
    return report


# --------------------------------------------------------------------------
# task 3 -- triage score/level/reason consistency (D1)
# --------------------------------------------------------------------------


def _consistency_snapshot(item: dict) -> dict:
    score = item.get("score")
    level = item.get("level")
    expected_from_score = level_for(score) if score is not None else None
    conflict = (
        _reason_conflicting_level(item.get("triage_reason") or "", expected_from_score or "")
        if expected_from_score
        else None
    )
    return {
        "score": score,
        "level": level,
        "triage_reason": item.get("triage_reason"),
        "expected_level_from_score": expected_from_score,
        "reason_conflicting_level": conflict,
        "consistent": bool(
            score is not None and level is not None and level == expected_from_score and conflict is None
        ),
    }


def repair_triage(apply: bool, budget: LLMBudget, item_id: int = TRIAGE_REPAIR_ITEM_ID) -> dict:
    item = _fetch_item(item_id)
    if item is None:
        return {"item_id": item_id, "error": "item_not_found"}
    report: dict = {"item_id": item_id, "before": _consistency_snapshot(item)}
    if not apply:
        return report
    if budget.exhausted:
        report["skipped_budget"] = True
        return report
    budget.use()
    out = triage_item(item, role="resident", interactive=False)
    validate_triage_consistency(item_id, level=out.level, score=out.score)
    update_item_fields(item_id, score=out.score, level=out.level, triage_reason=out.reason_he[:600])
    after = _fetch_item(item_id)
    report["after"] = _consistency_snapshot(after)
    return report


# --------------------------------------------------------------------------
# task 4 -- analyze-stage scope gap: stray events on out-of-scope/archived items (D9)
# --------------------------------------------------------------------------


def find_out_of_scope_events() -> list[dict]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT e.id, e.item_id, e.kind, e.title, i.domain, i.level
            FROM events e JOIN items i ON i.id = e.item_id
            WHERE COALESCE(i.domain, 'out_of_scope') = 'out_of_scope' OR i.level = 'archive'
            ORDER BY e.id
            """
        )
        return cur.fetchall()


def repair_events(apply: bool) -> dict:
    rows = find_out_of_scope_events()
    report = {
        "found_count": len(rows),
        "events": [
            {"id": r["id"], "item_id": r["item_id"], "kind": r["kind"], "title": (r["title"] or "")[:120]}
            for r in rows
        ],
    }
    if not apply:
        return report
    ids = [r["id"] for r in rows]
    if ids:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id = ANY(%(ids)s::bigint[])", {"ids": ids})
    report["deleted_count"] = len(ids)
    return report


# --------------------------------------------------------------------------
# task 5 -- junk tender candidate dedupe (D9)
# --------------------------------------------------------------------------

_TITLE_WS_RE = re.compile(r"\s+")


def _normalize_title(title: str | None) -> str:
    return _TITLE_WS_RE.sub(" ", (title or "").strip()).casefold()


def _portal(url: str | None) -> str:
    if not url:
        return ""
    return (urlsplit(url).netloc or "").lower()


def find_duplicate_tenders() -> list[dict]:
    """'candidate'/'unknown'-status tenders rows that share a (normalized title, URL portal) with
    an earlier (lower-id) 'candidate' row -- never considers 'accepted'/'archived' rows, and never
    proposes deleting the oldest row in a group."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, title, url, status, intake, created_at FROM tenders "
            "WHERE intake = 'candidate' ORDER BY id"
        )
        rows = cur.fetchall()
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        key = (_normalize_title(r["title"]), _portal(r["url"]))
        groups.setdefault(key, []).append(r)
    duplicates: list[dict] = []
    for (_norm_title, portal), members in groups.items():
        if len(members) < 2 or not portal:
            continue
        members_sorted = sorted(members, key=lambda r: r["id"])
        keep = members_sorted[0]
        for dup in members_sorted[1:]:
            duplicates.append(
                {
                    "keep_id": keep["id"],
                    "delete_id": dup["id"],
                    "title": dup["title"],
                    "portal": portal,
                    "status": dup["status"],
                }
            )
    return duplicates


def repair_tenders(apply: bool) -> dict:
    duplicates = find_duplicate_tenders()
    report = {"found_count": len(duplicates), "duplicates": duplicates}
    if not apply:
        return report
    delete_ids = [d["delete_id"] for d in duplicates]
    if delete_ids:
        with connection() as conn, conn.cursor() as cur:
            # Belt-and-braces re-assertion of the "never touch accepted/archived" contract even
            # though find_duplicate_tenders() only ever looks at intake='candidate' rows.
            cur.execute(
                "DELETE FROM tenders WHERE id = ANY(%(ids)s::bigint[]) AND intake = 'candidate'",
                {"ids": delete_ids},
            )
    report["deleted_count"] = len(delete_ids)
    return report


# --------------------------------------------------------------------------
# task 6 -- entities_cleanup: entities mentioned ONLY by out-of-scope/archived items (R6-entities)
# --------------------------------------------------------------------------

#: kind values eligible for the "has its own country and an in-scope mention" protection rule
#: (docs brief task 1(b)). Verified live 2026-09-07: given how ``find_out_of_scope_only_entities``
#: is constructed (its own WHERE clause already requires zero in-scope mentions), this rule can
#: never actually protect a row returned by that query -- kept anyway, exactly as the brief
#: specifies, as an explicit documented safety net against a future loosening of that population
#: query (e.g. if it is ever widened to include a *partial*-in-scope entity).
_PROTECTED_KINDS_WITH_COUNTRY = frozenset({"company", "org", "system", "program"})


def _watchlist_protected_names() -> set[str]:
    """Every name/alias this repair must never delete (docs brief task 1): normalized via
    ``eoa.pipeline.entity_normalize.normalize_name_key`` --

    - ``config/watchlist.yaml``: ``companies`` (name + aliases + strict_aliases), ``programs``
      (name + aliases), ``agencies`` (name + aliases), ``acquisition_watch`` (name + peers_of).
    - ``config/payloads_seed.yaml``: every ``payloads[].vendor_entity_name``.

    Pure config/yaml reads -- no DB call, safe to call from a dry run."""
    wl = settings().watchlist
    names: set[str] = set()
    for rec in wl.get("companies", []) or []:
        names.add(rec.get("name", ""))
        names.update(rec.get("aliases") or [])
        names.update(rec.get("strict_aliases") or [])
    for rec in wl.get("programs", []) or []:
        names.add(rec.get("name", ""))
        names.update(rec.get("aliases") or [])
    for rec in wl.get("agencies", []) or []:
        names.add(rec.get("name", ""))
        names.update(rec.get("aliases") or [])
    for rec in wl.get("acquisition_watch", []) or []:
        names.add(rec.get("name", ""))
        names.update(rec.get("peers_of") or [])
    payloads_path = CONFIG_DIR / "payloads_seed.yaml"
    if payloads_path.exists():
        data = yaml.safe_load(payloads_path.read_text(encoding="utf-8")) or {}
        for rec in data.get("payloads", []) or []:
            vendor = rec.get("vendor_entity_name")
            if vendor:
                names.add(vendor)
    return {normalize_name_key(n) for n in names if n}


def find_out_of_scope_only_entities() -> list[dict]:
    """Entities mentioned by at least one item, but NEVER by any item outside
    ``domain='out_of_scope'``/``level='archive'`` -- the docs-brief finding query, confirmed live
    against ``items.entities_mentioned`` (``TEXT[]``). ``has_in_scope_mention`` is always ``False``
    for every row this returns (see :data:`_PROTECTED_KINDS_WITH_COUNTRY`'s note) -- included for
    transparency/documentation rather than because it can vary here."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT e.id, e.name, e.kind, e.country, e.created_at,
              (SELECT count(*) FROM items i WHERE e.name = ANY(i.entities_mentioned)
                 AND (COALESCE(i.domain, '') = 'out_of_scope' OR COALESCE(i.level, '') = 'archive')
              ) AS n_items_out_of_scope,
              EXISTS (
                SELECT 1 FROM items i WHERE e.name = ANY(i.entities_mentioned)
                  AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
              ) AS has_in_scope_mention
            FROM entities e
            WHERE NOT EXISTS (
                SELECT 1 FROM items i WHERE e.name = ANY(i.entities_mentioned)
                  AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
              )
              AND EXISTS (SELECT 1 FROM items i WHERE e.name = ANY(i.entities_mentioned))
            ORDER BY e.id
            """
        )
        return cur.fetchall()


def _reports_report_state_text() -> str:
    """The concatenated text of every non-null ``reports.report_state`` (cast to text in SQL) --
    used to check whether a candidate entity name is visibly referenced in an already-published
    report's ``trend_titles`` (docs brief task 1(c): "referenced by a report_state/reports row").
    ``item_ids``/``item_levels``/``indicator_ids`` in that JSON are item/indicator ids, never
    entity names or ids -- only ``trend_titles[].title_he`` free text can name an entity."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT report_state::text AS txt FROM reports WHERE report_state IS NOT NULL")
        rows = cur.fetchall()
    return "\n".join(r["txt"] for r in rows if r.get("txt"))


def _protection_reason(row: dict, protected_names: set[str], reports_blob: str) -> str | None:
    """``None`` when `row` (a :func:`find_out_of_scope_only_entities` row) is safe to delete;
    otherwise the reason it must be kept -- docs brief task 1's three exemptions."""
    name = row.get("name") or ""
    if normalize_name_key(name) in protected_names:
        return "watchlist_or_payloads_vendor"
    if (
        row.get("kind") in _PROTECTED_KINDS_WITH_COUNTRY
        and row.get("country")
        and row.get("has_in_scope_mention")
    ):
        return "kind_with_country_and_in_scope_mention"
    if name and re.search(r"\b" + re.escape(name) + r"\b", reports_blob, re.IGNORECASE):
        return "referenced_by_report_state"
    return None


def find_junk_shaped_entities() -> list[dict]:
    """Every *existing* entity DB-wide (not just the out-of-scope-only population) that the new
    persistence-time junk-name-shape filter (``eoa.pipeline.analyze.is_junk_candidate_entity_name``,
    docs brief task 3) would now reject -- reported purely for visibility. Cross-referenced against
    the out-of-scope-only population by the caller; never itself a deletion trigger."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, name, kind FROM entities ORDER BY id")
        rows = cur.fetchall()
    return [r for r in rows if is_junk_candidate_entity_name(r["name"], r.get("kind"))]


def _entity_fk_columns() -> list[tuple[str, str]]:
    """``(table_name, column_name)`` for every FK column in the live schema that references
    ``entities.id`` -- discovered via ``information_schema`` (docs brief task 1: "inspect
    information_schema for every table with a FK to entities.id") so a future migration adding a
    new FK-to-entities table is covered automatically, without a code change here. Verified live
    2026-09-07: only ``graph_edges.src_entity_id``/``graph_edges.dst_entity_id`` (both
    ``ON DELETE CASCADE`` already, per ``db/migrations/versions/0006_drop_extensions.py`` -- this
    repair still deletes them explicitly, for an accurate "what did this touch" count rather than
    relying on an implicit cascade)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND ccu.table_name = 'entities' AND ccu.column_name = 'id'
            ORDER BY tc.table_name, kcu.column_name
            """
        )
        return [(r["table_name"], r["column_name"]) for r in cur.fetchall()]


def _table_has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %(t)s AND column_name = %(c)s",
        {"t": table, "c": column},
    )
    return cur.fetchone() is not None


def repair_entities_cleanup(apply: bool) -> dict:
    candidates = find_out_of_scope_only_entities()
    protected_names = _watchlist_protected_names()
    reports_blob = _reports_report_state_text()

    annotated = []
    to_delete_ids: list[int] = []
    for row in candidates:
        reason = _protection_reason(row, protected_names, reports_blob)
        annotated.append({**row, "protected_reason": reason})
        if reason is None:
            to_delete_ids.append(row["id"])

    candidate_id_set = {r["id"] for r in candidates}
    junk_flagged = [
        {**r, "in_out_of_scope_population": r["id"] in candidate_id_set} for r in find_junk_shaped_entities()
    ]

    report: dict = {
        "candidate_count": len(annotated),
        "candidates_preview": annotated[:40],
        "protected_count": len(annotated) - len(to_delete_ids),
        "to_delete_ids": to_delete_ids,
        "to_delete_count": len(to_delete_ids),
        "junk_filter_flagged": junk_flagged,
        "junk_filter_flagged_count": len(junk_flagged),
    }
    if not apply or not to_delete_ids:
        report["deleted_count"] = 0
        return report

    # Metadata discovery (read-only, no need to share the write transaction below) --
    # _entity_fk_columns() opens its own connection, so it must run BEFORE the single write
    # transaction opens, not inside it (that would silently split the delete across two
    # connections/transactions instead of one).
    fk_columns = _entity_fk_columns()

    # One transaction: dependent-row cleanup, then the entities themselves.
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        fk_deleted: dict[str, int] = {}
        for table, column in fk_columns:
            cur.execute(
                sql.SQL("DELETE FROM {table} WHERE {column} = ANY(%(ids)s::bigint[])").format(
                    table=sql.Identifier(table), column=sql.Identifier(column)
                ),
                {"ids": to_delete_ids},
            )
            fk_deleted[f"{table}.{column}"] = cur.rowcount

        patents_updated = 0
        # patents.entity_ids (BIGINT[]) is a soft reference to entities.id -- not a declared FK
        # (an array column can't carry one), found by inspecting every migration for a column
        # naming entities by id (db/migrations/versions/0018_patents.py). Strips the deleted ids
        # out of the array; never deletes the patents row itself.
        if _table_has_column(cur, "patents", "entity_ids"):
            cur.execute(
                """
                UPDATE patents SET entity_ids = (
                    SELECT COALESCE(array_agg(x), '{}') FROM unnest(entity_ids) AS x
                    WHERE x <> ALL(%(ids)s::bigint[])
                )
                WHERE entity_ids && %(ids)s::bigint[]
                """,
                {"ids": to_delete_ids},
            )
            patents_updated = cur.rowcount

        cur.execute("DELETE FROM entities WHERE id = ANY(%(ids)s::bigint[])", {"ids": to_delete_ids})
        deleted_count = cur.rowcount

    report["fk_dependents_deleted"] = fk_deleted
    report["patents_rows_updated"] = patents_updated
    report["deleted_count"] = deleted_count
    return report


# --------------------------------------------------------------------------
# verification (separate connection, per standing rule)
# --------------------------------------------------------------------------


def verify_all() -> dict:
    """Re-checks every task's success criterion from a fresh connection."""
    remaining_so_what = len(find_banned_so_what_items())

    item22 = _fetch_item(MANDATORY_ENTITY_REPAIR_ITEM_ID)
    item5604 = _fetch_item(TRIAGE_REPAIR_ITEM_ID)
    remaining_events = len(find_out_of_scope_events())
    remaining_tender_dupes = len(find_duplicate_tenders())
    remaining_out_of_scope_entities = len(find_out_of_scope_only_entities())

    return {
        "so_what_remaining_matches": remaining_so_what,
        "item_22_entities_mentioned": (item22 or {}).get("entities_mentioned"),
        "item_5604_consistency": _consistency_snapshot(item5604) if item5604 else None,
        "events_remaining_out_of_scope": remaining_events,
        "tenders_remaining_duplicates": remaining_tender_dupes,
        "entities_remaining_out_of_scope_only": remaining_out_of_scope_entities,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "subcommand",
        choices=["so_what", "entities", "triage", "events", "tenders", "entities_cleanup", "all"],
        help="which repair to run",
    )
    ap.add_argument("--apply", action="store_true", help="write the repairs (default: dry run)")
    ap.add_argument(
        "--llm-budget",
        type=int,
        default=25,
        help="max LLM calls this invocation will spend across so_what/entities/triage (default 25)",
    )
    ap.add_argument(
        "--entities-cap", type=int, default=30, help="max non-mandatory items for the entities repair"
    )
    args = ap.parse_args()

    _load_env()
    _print_target()

    budget = LLMBudget(args.llm_budget)
    report: dict = {"mode": "apply" if args.apply else "dry_run", "llm_budget": args.llm_budget}

    if args.subcommand in ("triage", "all"):
        report["triage"] = repair_triage(args.apply, budget, TRIAGE_REPAIR_ITEM_ID)
    if args.subcommand in ("entities", "all"):
        report["entities"] = repair_entities(args.apply, budget, args.entities_cap)
    if args.subcommand in ("so_what", "all"):
        report["so_what"] = repair_so_what(args.apply, budget)
    if args.subcommand in ("events", "all"):
        report["events"] = repair_events(args.apply)
    if args.subcommand in ("tenders", "all"):
        report["tenders"] = repair_tenders(args.apply)
    if args.subcommand in ("entities_cleanup", "all"):
        report["entities_cleanup"] = repair_entities_cleanup(args.apply)

    report["llm_calls_used"] = budget.used

    if args.apply:
        report["verify"] = verify_all()

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
