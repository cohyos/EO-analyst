"""Stage: analyze — Hebrew summary, So-What, events, and graph edges with provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import (
    DATA_GUARD_SYSTEM,
    chat_structured,
    chat_structured_batch,
    is_cloud_batch_mode,
    wrap_data,
)
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import AnalyzeOut, EventOut
from eoa.memory.relational import (
    get_items_for_stage,
    insert_event,
    mark_stage,
    update_item_fields,
    upsert_entity,
)

log = structlog.get_logger(__name__)

STAGE = "analyze"
MAX_CHARS = 12000
BATCH_SIZE = 8  # U8-6 (Revision 2026-09-06): "analyze in batches of up to 8"

_ORG_KEYWORDS = (
    "air force",
    "army",
    "navy",
    "ministry",
    "department",
    "command",
    "agency",
    "nato",
    "dod",
)
_PROGRAM_KEYWORDS = ("program", "project", "programme")


@dataclass
class AnalyzeStats:
    done: int = 0
    events: int = 0
    edges: int = 0
    failed: int = 0


def _context_for(item: dict) -> str:
    """Related earlier items (same entities, last 30 days) as short bullet context."""
    try:
        from eoa.db import connection

        ents = item.get("entities_mentioned") or []
        if not ents:
            return "אין."
        with connection() as conn:
            rows = conn.execute(
                "SELECT id, title, summary_he, published_at FROM items WHERE id<>%s AND entities_mentioned && %s "
                "AND published_at > now() - interval '30 days' AND summary_he IS NOT NULL "
                "ORDER BY published_at DESC LIMIT 5",
                (item["id"], ents),
            ).fetchall()
        if not rows:
            return "אין."
        return "\n".join(f"- [item {r['id']}] {r['title']}: {r['summary_he']}" for r in rows)
    except Exception as exc:
        log.debug("context_unavailable", error=str(exc)[:120])
        return "אין."


def _analyze_prompt(item: dict) -> str:
    return render(
        "analyze",
        context=_context_for(item),
        title=item.get("title") or "",
        source=item.get("source_name") or item.get("url") or "",
        report_kind=item.get("report_kind") or "?",
        published_at=item.get("published_at") or "לא ידוע",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )


def _analyze_system() -> str:
    return render("system_analyst", data_guard=DATA_GUARD_SYSTEM)


def analyze_item(item: dict, *, role: str = "resident", interactive: bool = False) -> AnalyzeOut:
    """Produce the AnalyzeOut for one item (does not persist)."""
    return chat_structured(
        role,
        AnalyzeOut,
        [
            {"role": "system", "content": _analyze_system()},
            {"role": "user", "content": _analyze_prompt(item)},
        ],
        task="summarize",
        interactive=interactive,
        options={"temperature": 0.3},
    )


def analyze_batch(items: list[dict], *, role: str = "resident") -> dict[int, AnalyzeOut]:
    """U8-6 batch mode (Revision 2026-09-06): analyze up to ``BATCH_SIZE`` (8) items in one cloud
    call instead of one call per item -- point 6's "cross-item context" is most valuable here,
    since related items (same program/contract from different outlets) benefit from being scored
    together. Returns ``{item_id: AnalyzeOut}``. Only used by ``run_analyze`` when
    ``is_cloud_batch_mode()`` is true."""
    prompts = [(it["id"], _analyze_prompt(it)) for it in items]
    return chat_structured_batch(
        role, AnalyzeOut, prompts, system=_analyze_system(), task="summarize", options={"temperature": 0.3}
    )


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _heuristic_kind(name: str) -> str:
    """Guess an entity `kind` from its name when no better information exists.

    Military/government bodies (Air Force, Ministry, NATO, ...) -> "org";
    named programs/projects -> "program"; everything else defaults to
    "company", the historical (and often wrong) default this replaces.
    """
    lname = name.lower()
    if any(kw in lname for kw in _ORG_KEYWORDS):
        return "org"
    if any(kw in lname for kw in _PROGRAM_KEYWORDS):
        return "program"
    return "company"


def _resolve_edge_kinds(names: list[str]) -> dict[str, str]:
    """Resolve a `name -> kind` map for every edge endpoint about to be upserted.

    Priority: (1) the kind already on record in `entities` -- typically set
    correctly by `classify.persist_classification` from the LLM's
    `EntityMention.kind` -- so a later, cruder guess here never overwrites a
    better-informed one; (2) a keyword heuristic; (3) "company" as the last
    resort. Never raises: a DB lookup failure just falls back to (2)/(3) for
    every name, same as before this function existed.
    """
    if not names:
        return {}
    existing: dict[str, str] = {}
    try:
        from eoa.db import connection

        with connection() as conn:
            rows = conn.execute("SELECT name, kind FROM entities WHERE name = ANY(%s)", (names,)).fetchall()
        existing = {r["name"]: r["kind"] for r in rows if r.get("kind")}
    except Exception as exc:
        log.debug("edge_kind_lookup_failed", error=str(exc)[:120])
    return {name: existing.get(name) or _heuristic_kind(name) for name in names}


def _event_dedup_key(ev: EventOut) -> tuple[str, str, str, str]:
    """Same normalised-identity key as ``eoa.report.daily._normalize_event_key`` (kept as a small,
    local duplicate rather than an import, per this codebase's convention for report/pipeline
    boundary helpers): a small local model frequently emits near-duplicate events for the same
    underlying fact within a single item's ``events`` list (F9/F16)."""
    parties_norm = tuple(sorted(p.strip().casefold() for p in (ev.parties or []) if p and p.strip()))
    customer_norm = (ev.customer or "").strip().casefold()
    program_norm = (ev.program or "").strip().casefold()
    return (ev.kind, "|".join(parties_norm), customer_norm, program_norm)


def _event_richness(ev: EventOut) -> int:
    return sum(
        1 for v in (ev.date, ev.amount_usd, ev.currency, ev.customer, ev.program) if v not in (None, "")
    ) + len(ev.parties or [])


def _dedup_events(events: list[EventOut]) -> list[EventOut]:
    """Collapse events sharing :func:`_event_dedup_key` within a single item's extraction, keeping
    the richest (most fields populated) one per group (F9/F16)."""
    best: dict[tuple[str, str, str, str], EventOut] = {}
    for ev in events:
        key = _event_dedup_key(ev)
        cur = best.get(key)
        if cur is None or _event_richness(ev) > _event_richness(cur):
            best[key] = ev
    return list(best.values())


def persist_analysis(item: dict, out: AnalyzeOut) -> tuple[int, int]:
    """Write summary/so-what/events/edges. Returns (events_written, edges_written)."""
    update_item_fields(
        item["id"],
        summary_he=out.summary_he,
        so_what_he=out.so_what_he,
        key_facts=list(out.key_facts),
        uncertainty_he=out.uncertainty_he or None,
    )
    n_events = 0
    for ev in _dedup_events(out.events):
        try:
            insert_event(
                item_id=item["id"],
                kind=ev.kind,
                title=ev.title,
                date=_parse_date(ev.date),
                amount_usd=ev.amount_usd,
                currency=ev.currency,
                parties=ev.parties,
                customer=ev.customer,
                program=ev.program,
                summary_he=ev.summary_he,
                confidence=ev.confidence,
            )
            n_events += 1
        except Exception as exc:
            log.warning("event_insert_failed", item_id=item["id"], error=str(exc)[:160])
    n_edges = 0
    if out.edges:
        try:
            from eoa.memory.graph import add_edge, merge_entity

            names = sorted({e.src for e in out.edges} | {e.dst for e in out.edges})
            kind_by_name = _resolve_edge_kinds(names)
            for e in out.edges:
                src_kind = kind_by_name.get(e.src, "company")
                dst_kind = kind_by_name.get(e.dst, "company")
                src_id = upsert_entity(name=e.src, kind=src_kind, first_seen_item=item["id"])
                dst_id = upsert_entity(name=e.dst, kind=dst_kind, first_seen_item=item["id"])
                merge_entity(src_id, e.src, src_kind, None)
                merge_entity(dst_id, e.dst, dst_kind, None)
                add_edge(src_id, dst_id, e.label, item["id"], {"evidence": e.evidence_he[:300]})
                n_edges += 1
        except Exception as exc:
            log.warning("edge_write_failed", item_id=item["id"], error=str(exc)[:160])
    return n_events, n_edges


def _persist_analysis_and_score(it: dict, out: AnalyzeOut, stats: AnalyzeStats) -> None:
    """Shared per-item tail of ``run_analyze``'s two loops (plain and U8-6 batch mode): persist,
    mark the stage, and rescore entity relevance (F15). Only DB/side-effect failures are caught
    here -- an LLM-side failure is the caller's problem (it decides whether to fail one item or
    the whole batch chunk)."""
    ne, ng = persist_analysis(it, out)
    mark_stage(it["id"], STAGE)
    stats.done += 1
    stats.events += ne
    stats.edges += ng
    # --- entity relevance scoring (F15) --------------------------------------
    # Rescore every entity this item mentions now that its level/domain (set by
    # earlier stages) and entities_mentioned (set by classify.py) are both final.
    # Guarded, best-effort, one call per name -- never blocks/fails the item.
    try:
        from eoa.pipeline.entity_relevance import score_and_persist_entity

        for name in it.get("entities_mentioned") or []:
            score_and_persist_entity(name)
    except Exception as exc:
        log.debug("entity_relevance_scoring_skipped", item_id=it["id"], error=str(exc)[:120])
    # --- end entity relevance scoring ----------------------------------------


def run_analyze(limit: int = 120, role: str = "resident", min_level: str = "yellow") -> AnalyzeStats:
    """Analyze triaged items at or above ``min_level`` (red > orange > yellow)."""
    order = {"red": 0, "orange": 1, "yellow": 2, "archive": 3}
    stats = AnalyzeStats()
    items = [
        it
        for it in get_items_for_stage(STAGE, limit)
        if it.get("security_status") != "quarantined" and not it.get("dedup_of")
    ]
    items.sort(key=lambda it: order.get(it.get("level") or "archive", 3))
    eligible: list[dict] = []
    for it in items:
        if it.get("level") is None:
            continue  # not triaged yet — leave for the next pass, do not mark
        if order.get(it.get("level") or "archive", 3) > order[min_level]:
            mark_stage(it["id"], STAGE)
            continue
        eligible.append(it)

    # --- U8-6 batch mode (Revision 2026-09-06): cloud mode analyzes BATCH_SIZE (8) items per
    # call. Persistence/side-effects are identical to the per-item path via
    # _persist_analysis_and_score. Local mode (default) never enters this branch. -------------
    if is_cloud_batch_mode():
        for i in range(0, len(eligible), BATCH_SIZE):
            chunk = eligible[i : i + BATCH_SIZE]
            try:
                results = analyze_batch(chunk, role=role)
            except ResourceUnavailable:
                log.warning("analyze_batch_deferred_resources", n=len(chunk))
                break
            except LLMOutputError as exc:
                log.error("analyze_batch_bad_output", n=len(chunk), error=str(exc)[:200])
                stats.failed += len(chunk)
                continue
            for it in chunk:
                out = results.get(it["id"])
                if out is None:
                    log.error("analyze_batch_missing_item", item_id=it["id"])
                    stats.failed += 1
                    continue
                try:
                    _persist_analysis_and_score(it, out, stats)
                except Exception as exc:
                    log.error("analyze_persist_failed", item_id=it["id"], error=str(exc)[:200])
                    stats.failed += 1
        log.info("analyze_done", **stats.__dict__)
        return stats
    # --- end U8-6 batch mode -------------------------------------------------------------------

    for it in eligible:
        try:
            out = analyze_item(it, role=role)
            _persist_analysis_and_score(it, out, stats)
        except ResourceUnavailable:
            log.warning("analyze_deferred_resources", item_id=it["id"])
            break
        except LLMOutputError as exc:
            log.error("analyze_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except Exception as exc:
            log.error("analyze_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("analyze_done", **stats.__dict__)
    return stats
