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

#: Q3-6 (docs/qa/findings_Q3_r1.md): an event title opening with one of these is an assessment/
#: forecast sentence about implications or trends -- not a report of something that happened --
#: and should never be persisted as an `events` row (the model sometimes emits its "so what"
#: reasoning a second time, framed as an event).
_NARRATIVE_TITLE_PREFIXES_HE = ("השלכות", "משמעות", "מגמה", "צפוי", "ייתכן")
#: Hebrew verbs of *occurrence* -- something concrete happening -- as opposed to a verb of
#: assessment/expectation. A title with none of these AND no party/customer/amount/date to anchor
#: it to a concrete fact is treated as narrative, not an event (Q3-6).
_OCCURRENCE_VERBS_HE = (
    "זכ",  # זכה/זכתה/זכייה (won)
    "חתמ",  # חתם/חתמה (signed)
    "רכש",  # רכש/רכשה (acquired/purchased)
    "השיק",  # השיק/השיקה (launched)
    "פרסמ",  # פרסם/פרסמה (published/announced)
    "מינ",  # מינה/מינתה (appointed)
    "אישר",  # אישר/אישרה (approved)
    "העניק",  # העניקה (granted/awarded)
    "גייס",  # גייסה (raised — investment)
    "השלימ",  # השלימה (completed)
    "מסר",  # מסרה (delivered)
    "סיפק",  # סיפקה (supplied)
    "בדק",  # בדק/בדקה/נבדק (tested)
    "פיתח",  # פיתח/פיתחה (developed)
    "השתלט",  # השתלטה (acquired/took over)
    "מיזג",  # מיזגה/התמזגה (merged)
)


def _is_narrative_event_title(title: str | None, ev: EventOut) -> bool:
    """Q3-6: true if `title` reads like an assessment/forecast sentence rather than the report of
    a concrete, dated, attributable event -- and should be rejected before ``insert_event``.

    Two signals: (1) the title opens with one of :data:`_NARRATIVE_TITLE_PREFIXES_HE`; or (2) it
    contains no :data:`_OCCURRENCE_VERBS_HE` AND the event carries no party/customer/amount/date
    to anchor it to a concrete fact -- a title-only, factless "event" is exactly the shape of a
    stray analytical sentence the model emitted a second time.
    """
    t = (title or "").strip()
    if not t:
        return False
    if t.startswith(_NARRATIVE_TITLE_PREFIXES_HE):
        return True
    has_anchor = bool(ev.parties) or bool(ev.customer) or ev.amount_usd is not None or bool(ev.date)
    has_occurrence_verb = any(v in t for v in _OCCURRENCE_VERBS_HE)
    return not has_occurrence_verb and not has_anchor


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


#: Q3-10 (docs/qa/findings_Q3_r1.md): prepended to the prompt's `{context}` block for a
#: 'partial'-content item, so the model is told up front that it may be reasoning over a
#: paywall/RFI-portal teaser rather than the full article -- it cannot see `content_status`
#: otherwise, since that's a DB column, not part of the item text handed to it.
_PARTIAL_CONTENT_CONTEXT_NOTE_HE = (
    "הערה: ייתכן שתוכן הפריט חלקי בלבד (חסם תוכן/מנוי או תקציר בלבד) — חלק מהעובדות עשויות "
    "להיות חסרות. ציין זאת במפורש ב-so_what_he וב-uncertainty_he אם רלוונטי."
)
#: Persisted into `items.uncertainty_he` for a 'partial'-content item, per Q3-10.
PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE = "טקסט חלקי (paywall)"


def _analyze_prompt(item: dict) -> str:
    context = _context_for(item)
    if item.get("content_status") == "partial":
        context = f"{_PARTIAL_CONTENT_CONTEXT_NOTE_HE}\n\n{context}"
    return render(
        "analyze",
        context=context,
        title=item.get("title") or "",
        source=item.get("source_name") or item.get("url") or "",
        report_kind=item.get("report_kind") or "?",
        published_at=item.get("published_at") or "לא ידוע",
        domain=item.get("domain") or "?",
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


def _key_facts_dedupe_key(fact: str) -> str:
    return " ".join(fact.strip().casefold().split())


def _dedupe_key_facts(facts: list[str]) -> list[str]:
    """Q3-9 (docs/qa/findings_Q3_r1.md): drop word-for-word (modulo whitespace/case) duplicate
    ``key_facts`` entries, keeping the first occurrence's original text and order."""
    seen: set[str] = set()
    result: list[str] = []
    for fact in facts:
        if not fact or not fact.strip():
            continue
        key = _key_facts_dedupe_key(fact)
        if key in seen:
            continue
        seen.add(key)
        result.append(fact)
    return result


def _backfill_entities_from_watchlist(item: dict) -> list[str] | None:
    """Q3-8: when the item already reached the analyze stage with an empty
    ``entities_mentioned`` (classify extracted none) but its own title/text plainly names a
    watchlist company or program, deterministically fill it from a watchlist alias match instead
    of leaving it empty forever. Returns ``None`` (nothing to backfill) when
    ``entities_mentioned`` is already non-empty or no watchlist alias is found in the text."""
    if item.get("entities_mentioned"):
        return None
    from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text

    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    matched = find_watchlist_aliases_in_text(text)
    return matched or None


def _with_partial_content_note(item: dict, uncertainty_he: str | None) -> str | None:
    """Q3-10: for a 'partial'-content item, guarantee `uncertainty_he` names the paywall/partial
    condition -- even when the model's own `uncertainty_he` said nothing about it (the model only
    sees the note in its prompt context, best-effort; this makes the persisted field authoritative
    regardless)."""
    if item.get("content_status") != "partial":
        return uncertainty_he
    if uncertainty_he and PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE in uncertainty_he:
        return uncertainty_he
    return f"{PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE}. {uncertainty_he}" if uncertainty_he else PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE


def persist_analysis(item: dict, out: AnalyzeOut) -> tuple[int, int]:
    """Write summary/so-what/events/edges. Returns (events_written, edges_written)."""
    entities_backfill = _backfill_entities_from_watchlist(item)
    extra_fields: dict[str, list[str]] = {}
    if entities_backfill:
        log.info("analyze_entities_backfilled", item_id=item["id"], entities=entities_backfill)
        extra_fields["entities_mentioned"] = entities_backfill
    update_item_fields(
        item["id"],
        summary_he=out.summary_he,
        so_what_he=out.so_what_he,
        key_facts=_dedupe_key_facts(list(out.key_facts)),
        uncertainty_he=_with_partial_content_note(item, out.uncertainty_he or None),
        # A12 (מעקב טכנולוגי): additive, only ever non-null for domain == "tech_dev" -- the LLM
        # is instructed (prompts/analyze.md) to leave these null/empty for every other domain.
        tech_maturity=out.tech_maturity,
        tech_actor_kind=out.tech_actor_kind,
        tech_readiness_note_he=out.tech_readiness_note_he or None,
        **extra_fields,
    )
    n_events = 0
    for ev in _dedup_events(out.events):
        if _is_narrative_event_title(ev.title, ev):
            # Q3-6: an assessment/forecast sentence dressed up as an event -- never persisted.
            log.info("event_rejected_narrative_title", item_id=item["id"], title=(ev.title or "")[:160])
            continue
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
                if src_id is None or dst_id is None:
                    # Q3-13: one (or both) endpoints was rejected by upsert_entity as a
                    # technique-like non-entity name -- the edge itself is meaningless then.
                    log.info(
                        "edge_skipped_rejected_entity", item_id=item["id"], src=e.src, dst=e.dst
                    )
                    continue
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


def _content_status_precheck(it: dict) -> str:
    """Q3-10 pre-check: classify `it`'s clean_text via `eoa.fetch.content_quality.assess` and
    persist `items.content_status` when it changed. Returns the (possibly unchanged) status --
    callers use it to decide whether to skip full analysis ('stub') or just flag it ('partial').
    """
    from eoa.fetch.content_quality import assess

    status = assess(
        it.get("clean_text"),
        html_len=len(it.get("raw_text") or ""),
        status=it.get("security_status"),
    )
    if it.get("content_status") != status:
        try:
            update_item_fields(it["id"], content_status=status)
        except Exception as exc:
            log.debug("content_status_update_failed", item_id=it["id"], error=str(exc)[:120])
    it["content_status"] = status
    return status


def run_analyze(limit: int = 120, role: str = "resident", min_level: str = "yellow") -> AnalyzeStats:
    """Analyze triaged items at or above ``min_level`` (red > orange > yellow)."""
    order = {"red": 0, "orange": 1, "yellow": 2, "archive": 3}
    stats = AnalyzeStats()
    items = [
        it
        for it in get_items_for_stage(STAGE, limit)
        if it.get("security_status") not in ("quarantined", "blocked") and not it.get("dedup_of")
    ]
    items.sort(key=lambda it: order.get(it.get("level") or "archive", 3))
    eligible: list[dict] = []
    for it in items:
        if it.get("level") is None:
            continue  # not triaged yet — leave for the next pass, do not mark
        if order.get(it.get("level") or "archive", 3) > order[min_level]:
            mark_stage(it["id"], STAGE)
            continue
        # Q3-10: 'stub' content (paywall/blocked/near-empty) is never worth a full analysis pass
        # -- mark the stage done (so it isn't retried forever) and move on. 'partial' content is
        # still analyzed, just flagged (see _analyze_prompt/_with_partial_content_note).
        if _content_status_precheck(it) == "stub":
            log.info("analyze_skipped_stub_content", item_id=it["id"])
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
