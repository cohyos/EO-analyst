"""Stage: triage (importance 1-10 → level) with user-feedback calibration."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from eoa.config import settings
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import (
    DATA_GUARD_SYSTEM,
    chat_structured,
    chat_structured_batch,
    is_cloud_batch_mode,
    wrap_data,
)
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import TriageOut
from eoa.memory.relational import (
    get_items_for_stage,
    get_lessons,
    mark_stage,
    recent_feedback,
    update_item_fields,
)

log = structlog.get_logger(__name__)

STAGE = "triage"
MAX_CHARS = 6000
BATCH_SIZE = 25  # U8-6 (Revision 2026-09-06): "triage ... run in batches of up to 25 items"

# U11: phrases that indicate the model produced a non-question / meta-commentary about the
# article instead of a self-contained research question (docs/REVIEW_2026-09-05.md U11/F17/F18).
_META_PHRASES = (
    "הכתבה מספקת",
    "המאמר מספק",
    "כל המידע הנדרש",
    "אין מידע נוסף",
    "הכתבה כוללת",
    "המאמר כולל",
    "לא נדרש מידע נוסף",
    "הפריט מספק",
)
_MIN_QUESTION_WORDS = 12


def _bare_article_reference(question: str) -> bool:
    """True if the question mentions "the article/piece" without carrying any other identifying
    context (title tokens / entity names) alongside it -- exactly U11's "no article attached" bug."""
    bare_markers = ("הכתבה", "המאמר", "הידיעה")
    return any(m in question for m in bare_markers)


def _question_is_valid(question: str, item: dict) -> bool:
    if not question or not question.strip():
        return False
    words = question.split()
    if len(words) < _MIN_QUESTION_WORDS:
        return False
    if any(phrase in question for phrase in _META_PHRASES):
        return False
    title = (item.get("title") or "").strip()
    entities = item.get("entities_mentioned") or []
    title_tokens = {t for t in title.split() if len(t) >= 4}
    carries_context = any(e and e.lower() in question.lower() for e in entities) or any(
        t in question for t in title_tokens
    )
    return not (_bare_article_reference(question) and not carries_context)


def _hint_is_usable(hint: str) -> bool:
    """A hint is only worth carrying into the fallback if it isn't itself the meta-noise/bare
    reference we're repairing away from -- otherwise appending it just reintroduces the bug."""
    if not hint or not hint.strip():
        return False
    if any(phrase in hint for phrase in _META_PHRASES):
        return False
    return not _bare_article_reference(hint)


def _fallback_question(item: dict, hint: str) -> str:
    """Deterministic, self-contained replacement when the model's question fails validation --
    always carries the item's title/entities so it never degenerates into a bare "the article" reference."""
    title = item.get("title") or "הפריט הנוכחי"
    entities = ", ".join((item.get("entities_mentioned") or [])[:4])
    entities_part = f" (ישויות מעורבות: {entities})" if entities else ""
    hint_part = f" בפרט לגבי: {hint.strip()}" if _hint_is_usable(hint) else ""
    return (
        f"מהם הפרטים המלאים, המספרים והתאריכים המדויקים העומדים מאחורי האירוע המתואר בכתבה "
        f'"{title}"{entities_part}, ומה עדיין אינו ידוע ודורש אימות ממקורות נוספים?{hint_part}'
    )


def _fallback_seed_en(item: dict) -> str:
    title = (item.get("title") or "").strip()
    entities = item.get("entities_mentioned") or []
    seed = " ".join(entities[:3]) if entities else title
    words = seed.split()[:8]
    return " ".join(words) or "defense contract announcement details"


def _ensure_valid_investigation_question(item: dict, question: str, seed_en: str) -> tuple[str, str]:
    """U11/F17/F18: never enqueue a malformed/meta/context-free deep-search question. Repairs
    deterministically instead of round-tripping the LLM again -- see docs/REVIEW_2026-09-05.md U11."""
    fixed_question = question if _question_is_valid(question, item) else _fallback_question(item, question)
    fixed_seed = seed_en.strip() if seed_en and len(seed_en.split()) >= 2 else _fallback_seed_en(item)
    if fixed_question != question or fixed_seed != seed_en:
        log.info(
            "deep_search_question_repaired",
            item_id=item.get("id"),
            original=question[:200],
            repaired=fixed_question[:200],
        )
    return fixed_question, fixed_seed


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
            down = sum(
                1
                for f in fb
                if f.get("user_level") in {"yellow", "archive"} and f.get("agent_level") in {"red", "orange"}
            )
            up = sum(
                1
                for f in fb
                if f.get("user_level") in {"red", "orange"} and f.get("agent_level") in {"yellow", "archive"}
            )
            if down > up + 2:
                parts.append("- המשתמש הוריד לאחרונה דירוגים רבים: היה שמרני יותר עם red/orange.")
            elif up > down + 2:
                parts.append("- המשתמש העלה לאחרונה דירוגים רבים: אל תחמיץ אירועים עסקיים בינוניים.")
    except Exception as exc:
        log.debug("lessons_unavailable", error=str(exc)[:120])
    return "\n".join(parts) or "אין לקחים קודמים."


def _triage_prompt(item: dict) -> str:
    lv = settings().triage.levels
    return render(
        "triage",
        red_min=lv["red"],
        orange_min=lv["orange"],
        yellow_min=lv["yellow"],
        lessons=_lessons_text(),
        watchlist_hits=_watchlist_hits(item.get("entities_mentioned")),
        title=item.get("title") or "",
        domain=item.get("domain") or "?",
        subdomain=item.get("subdomain") or "",
        report_kind=item.get("report_kind") or "?",
        trl=item.get("trl") or "unknown",
        entities=", ".join(item.get("entities_mentioned") or []) or "—",
        one_line_he=item.get("summary_he") or "",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )


def _triage_system() -> str:
    return render("system_analyst", data_guard=DATA_GUARD_SYSTEM)


def triage_item(item: dict, *, role: str = "resident", interactive: bool = False) -> TriageOut:
    """Score one classified item (does not persist). Level is recomputed from config thresholds."""
    out = chat_structured(
        role,
        TriageOut,
        [
            {"role": "system", "content": _triage_system()},
            {"role": "user", "content": _triage_prompt(item)},
        ],
        task="triage",
        interactive=interactive,
    )
    out.level = level_for(out.score)  # type: ignore[assignment]
    return out


def triage_batch(items: list[dict], *, role: str = "resident") -> dict[int, TriageOut]:
    """U8-6 batch mode (Revision 2026-09-06): triage up to ``BATCH_SIZE`` items in one cloud call
    instead of one call per item; returns ``{item_id: TriageOut}`` with ``level`` already
    recomputed from config thresholds, same as ``triage_item``. Only used by ``run_triage`` when
    ``is_cloud_batch_mode()`` is true."""
    prompts = [(it["id"], _triage_prompt(it)) for it in items]
    results = chat_structured_batch(role, TriageOut, prompts, system=_triage_system(), task="triage")
    for out in results.values():
        out.level = level_for(out.score)  # type: ignore[assignment]
    return results


def run_triage(limit: int = 300, role: str = "resident") -> TriageStats:
    """Triage all classified, in-scope items."""
    stats = TriageStats()
    eligible: list[dict] = []
    for it in get_items_for_stage(STAGE, limit):
        if it.get("domain") is None:
            continue  # not classified yet — leave for the next pass, do not mark
        if (
            it.get("security_status") == "quarantined"
            or it.get("dedup_of")
            or it.get("domain") == "out_of_scope"
        ):
            mark_stage(it["id"], STAGE)
            continue
        eligible.append(it)

    # --- U8-6 batch mode (Revision 2026-09-06): cloud mode triages BATCH_SIZE items per call.
    # Persistence/side-effects below are identical to the per-item path; only how TriageOut is
    # obtained differs. Local mode (default) never enters this branch. -----------------------
    if is_cloud_batch_mode():
        for i in range(0, len(eligible), BATCH_SIZE):
            chunk = eligible[i : i + BATCH_SIZE]
            try:
                results = triage_batch(chunk, role=role)
            except ResourceUnavailable:
                log.warning("triage_batch_deferred_resources", n=len(chunk))
                break
            except LLMOutputError as exc:
                log.error("triage_batch_bad_output", n=len(chunk), error=str(exc)[:200])
                stats.failed += len(chunk)
                continue
            for it in chunk:
                out = results.get(it["id"])
                if out is None:
                    log.error("triage_batch_missing_item", item_id=it["id"])
                    stats.failed += 1
                    continue
                try:
                    update_item_fields(it["id"], score=out.score, level=out.level, triage_reason=out.reason_he[:600])
                    if out.needs_deep_search or out.level == "red":
                        _enqueue_deep_search(it, out)
                    mark_stage(it["id"], STAGE)
                    stats.done += 1
                    stats.red += out.level == "red"
                    stats.orange += out.level == "orange"
                except Exception as exc:
                    log.error("triage_persist_failed", item_id=it["id"], error=str(exc)[:200])
                    stats.failed += 1
        log.info("triage_done", **stats.__dict__)
        return stats
    # --- end U8-6 batch mode -------------------------------------------------------------------

    for it in eligible:
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
        except Exception as exc:
            log.error("triage_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("triage_done", **stats.__dict__)
    return stats


def _enqueue_deep_search(item: dict, out: TriageOut) -> None:
    from eoa.memory.relational import enqueue_job

    question, seed_en = _ensure_valid_investigation_question(
        item, out.deep_search_question, out.deep_search_seed_en
    )
    entities = ", ".join(item.get("entities_mentioned") or []) or "—"
    context_he = (
        f"כותרת הפריט: {item.get('title') or ''}\n"
        f"ישויות: {entities}\n"
        f"תקציר: {item.get('summary_he') or ''}\n"
        f"זרע חיפוש באנגלית מוצע: {seed_en}"
    )
    enqueue_job(
        "deep_search",
        {"item_id": item["id"], "question": question, "level": out.level, "context_he": context_he},
        priority=1 if out.level == "red" else 3,
    )
    log.info("deep_search_enqueued", item_id=item["id"], level=out.level)
