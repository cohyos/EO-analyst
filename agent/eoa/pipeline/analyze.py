"""Stage: analyze — Hebrew summary, So-What, events, and graph edges with provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import AnalyzeOut
from eoa.memory.relational import get_items_for_stage, insert_event, mark_stage, update_item_fields, upsert_entity

log = structlog.get_logger(__name__)

STAGE = "analyze"
MAX_CHARS = 12000


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
    except Exception as exc:  # noqa: BLE001
        log.debug("context_unavailable", error=str(exc)[:120])
        return "אין."


def analyze_item(item: dict, *, role: str = "resident", interactive: bool = False) -> AnalyzeOut:
    """Produce the AnalyzeOut for one item (does not persist)."""
    prompt = render(
        "analyze",
        context=_context_for(item),
        title=item.get("title") or "",
        source=item.get("source_name") or item.get("url") or "",
        report_kind=item.get("report_kind") or "?",
        published_at=item.get("published_at") or "לא ידוע",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )
    return chat_structured(role, AnalyzeOut, [
        {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
        {"role": "user", "content": prompt},
    ], task="summarize", interactive=interactive, options={"temperature": 0.3})


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def persist_analysis(item: dict, out: AnalyzeOut) -> tuple[int, int]:
    """Write summary/so-what/events/edges. Returns (events_written, edges_written)."""
    update_item_fields(item["id"], summary_he=out.summary_he, so_what_he=out.so_what_he,
                       key_facts=list(out.key_facts), uncertainty_he=out.uncertainty_he or None)
    n_events = 0
    for ev in out.events:
        try:
            insert_event(item_id=item["id"], kind=ev.kind, title=ev.title, date=_parse_date(ev.date),
                         amount_usd=ev.amount_usd, currency=ev.currency, parties=ev.parties, customer=ev.customer,
                         program=ev.program, summary_he=ev.summary_he, confidence=ev.confidence)
            n_events += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("event_insert_failed", item_id=item["id"], error=str(exc)[:160])
    n_edges = 0
    if out.edges:
        try:
            from eoa.memory.graph import add_edge, merge_entity

            for e in out.edges:
                src_id = upsert_entity(name=e.src, kind="company", first_seen_item=item["id"])
                dst_id = upsert_entity(name=e.dst, kind="company", first_seen_item=item["id"])
                merge_entity(src_id, e.src, "company", None)
                merge_entity(dst_id, e.dst, "company", None)
                add_edge(src_id, dst_id, e.label, item["id"], {"evidence": e.evidence_he[:300]})
                n_edges += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("edge_write_failed", item_id=item["id"], error=str(exc)[:160])
    return n_events, n_edges


def run_analyze(limit: int = 120, role: str = "resident", min_level: str = "yellow") -> AnalyzeStats:
    """Analyze triaged items at or above ``min_level`` (red > orange > yellow)."""
    order = {"red": 0, "orange": 1, "yellow": 2, "archive": 3}
    stats = AnalyzeStats()
    items = [it for it in get_items_for_stage(STAGE, limit)
             if it.get("security_status") != "quarantined" and not it.get("dedup_of")]
    items.sort(key=lambda it: order.get(it.get("level") or "archive", 3))
    for it in items:
        if order.get(it.get("level") or "archive", 3) > order[min_level]:
            mark_stage(it["id"], STAGE)
            continue
        try:
            out = analyze_item(it, role=role)
            ne, ng = persist_analysis(it, out)
            mark_stage(it["id"], STAGE)
            stats.done += 1
            stats.events += ne
            stats.edges += ng
        except ResourceUnavailable:
            log.warning("analyze_deferred_resources", item_id=it["id"])
            break
        except LLMOutputError as exc:
            log.error("analyze_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except Exception as exc:  # noqa: BLE001
            log.error("analyze_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("analyze_done", **stats.__dict__)
    return stats
