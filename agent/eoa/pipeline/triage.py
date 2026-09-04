"""Stage: triage (importance 1-10 → level) with user-feedback calibration."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from eoa.config import settings
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import TriageOut
from eoa.memory.relational import get_items_for_stage, get_lessons, mark_stage, recent_feedback, update_item_fields

log = structlog.get_logger(__name__)

STAGE = "triage"
MAX_CHARS = 6000


@dataclass
class TriageStats:
    done: int = 0
    red: int = 0
    orange: int = 0
    failed: int = 0


def level_for(score: int) -> str:
    lv = settings().triage.levels
    if score >= lv["red"]:
        return "red"
    if score >= lv["orange"]:
        return "orange"
    if score >= lv["yellow"]:
        return "yellow"
    return "archive"


def _watchlist_hits(entities: list[str] | None) -> str:
    wl = settings().watchlist
    names: dict[str, list[str]] = {}
    for c in wl.get("companies", []) + wl.get("programs", []):
        names[c["name"].lower()] = [a.lower() for a in c.get("aliases", [])]
    hits = []
    for e in entities or []:
        el = e.lower()
        for n, aliases in names.items():
            if el == n or el in aliases or n in el:
                hits.append(e)
                break
    return ", ".join(sorted(set(hits))) or "אין"


def _lessons_text() -> str:
    parts = []
    try:
        for ls in get_lessons("calibration")[:12]:
            parts.append(f"- {ls['text']}")
        fb = recent_feedback(30)
        if fb:
            down = sum(1 for f in fb if f.get("user_level") in {"yellow", "archive"} and f.get("agent_level") in {"red", "orange"})
            up = sum(1 for f in fb if f.get("user_level") in {"red", "orange"} and f.get("agent_level") in {"yellow", "archive"})
            if down > up + 2:
                parts.append("- המשתמש הוריד לאחרונה דירוגים רבים: היה שמרני יותר עם red/orange.")
            elif up > down + 2:
                parts.append("- המשתמש העלה לאחרונה דירוגים רבים: אל תחמיץ אירועים עסקיים בינוניים.")
    except Exception as exc:  # noqa: BLE001
        log.debug("lessons_unavailable", error=str(exc)[:120])
    return "\n".join(parts) or "אין לקחים קודמים."


def triage_item(item: dict, *, role: str = "resident", interactive: bool = False) -> TriageOut:
    """Score one classified item (does not persist). Level is recomputed from config thresholds."""
    lv = settings().triage.levels
    prompt = render(
        "triage",
        red_min=lv["red"], orange_min=lv["orange"], yellow_min=lv["yellow"],
        lessons=_lessons_text(),
        watchlist_hits=_watchlist_hits(item.get("entities_mentioned")),
        title=item.get("title") or "",
        domain=item.get("domain") or "?", subdomain=item.get("subdomain") or "",
        report_kind=item.get("report_kind") or "?", trl=item.get("trl") or "unknown",
        entities=", ".join(item.get("entities_mentioned") or []) or "—",
        one_line_he=item.get("summary_he") or "",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )
    out = chat_structured(role, TriageOut, [
        {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
        {"role": "user", "content": prompt},
    ], task="triage", interactive=interactive)
    out.level = level_for(out.score)  # type: ignore[assignment]
    return out


def run_triage(limit: int = 300, role: str = "resident") -> TriageStats:
    """Triage all classified, in-scope items."""
    stats = TriageStats()
    for it in get_items_for_stage(STAGE, limit):
        if it.get("security_status") == "quarantined" or it.get("dedup_of") or it.get("domain") in (None, "out_of_scope"):
            mark_stage(it["id"], STAGE)
            continue
        try:
            out = triage_item(it, role=role)
            update_item_fields(it["id"], score=out.score, level=out.level, triage_reason=out.reason_he[:600])
            if out.needs_deep_search or out.level == "red":
                _enqueue_deep_search(it, out)
            mark_stage(it["id"], STAGE)
            stats.done += 1
            stats.red += out.level == "red"
            stats.orange += out.level == "orange"
        except ResourceUnavailable:
            log.warning("triage_deferred_resources", item_id=it["id"])
            break
        except LLMOutputError as exc:
            log.error("triage_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except Exception as exc:  # noqa: BLE001
            log.error("triage_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("triage_done", **stats.__dict__)
    return stats


def _enqueue_deep_search(item: dict, out: TriageOut) -> None:
    from eoa.memory.relational import enqueue_job

    question = out.deep_search_question or f"Verify and expand: {item.get('title')}"
    enqueue_job("deep_search", {"item_id": item["id"], "question": question, "level": out.level},
                priority=1 if out.level == "red" else 3)
    log.info("deep_search_enqueued", item_id=item["id"], level=out.level)
