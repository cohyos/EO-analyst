"""Stage: triage (importance 1-10 → level) with user-feedback calibration."""

from __future__ import annotations

import re
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

# Q3-4 (docs/qa/findings_Q3_r1.md): the same fixed sum->score lookup table triage.md gives the
# model ("אל תחשב נוסחה, רק תרגם לפי הטבלה") -- used to deterministically check that the model's
# own `score` actually matches the sum of the `(novelty, magnitude, core_relevance)` components it
# also returned. ids 10/67 in the QA sample had a `reason_he` narrating one level (e.g. "orange")
# while `score` corresponded to another ("red") -- the components/score disagreed with each other.
_SUM_TO_SCORE: dict[int, int] = {
    3: 1, 4: 1,
    5: 2, 6: 2,
    7: 3,
    8: 4,
    9: 5,
    10: 6,
    11: 7,
    12: 8,
    13: 9,
    14: 10, 15: 10,
}  # fmt: skip


def _expected_score(novelty: int, magnitude: int, core_relevance: int) -> int:
    """Deterministic score for a component triple, per triage.md's fixed lookup table."""
    total = max(3, min(15, novelty + magnitude + core_relevance))
    return _SUM_TO_SCORE[total]


def _score_matches_components(out: TriageOut) -> bool:
    return out.score == _expected_score(out.novelty, out.magnitude, out.core_relevance)


# ids 10/67 in the QA sample had a numerically self-consistent score (its components really do
# sum to it per the table) but `reason_he` still narrated a *different* level word in its own
# concluding sentence (e.g. "...רמה orange" for a score of 8, which the config's own thresholds
# put at "red") -- a prose/threshold disagreement `_score_matches_components` alone can't catch,
# since it only looks at the numbers, never the free text's own stated conclusion.
_LEVEL_WORDS_HE: dict[str, tuple[str, ...]] = {
    "red": ("אדום", "red"),
    "orange": ("כתום", "orange"),
    "yellow": ("צהוב", "yellow"),
    "archive": ("ארכיון", "archive"),
}
_REASON_LEVEL_CONCLUSION_RE = re.compile(
    r"(?:רמה|level)\s*[:\-]?\s*(אדום|כתום|צהוב|ארכיון|red|orange|yellow|archive)", re.IGNORECASE
)


def _reason_conflicting_level(reason_he: str, expected_level: str) -> str | None:
    """The level word ``reason_he`` explicitly concludes with (via a "רמה .../level ..." phrase),
    if it names a level other than ``expected_level`` -- else ``None``. Only trusts an explicit
    conclusion phrase (not any incidental level-word mention elsewhere) to avoid false positives."""
    if not reason_he:
        return None
    m = _REASON_LEVEL_CONCLUSION_RE.search(reason_he)
    if not m:
        return None
    stated = m.group(1).lower()
    for level, words in _LEVEL_WORDS_HE.items():
        if stated in (w.lower() for w in words) and level != expected_level:
            return level
    return None


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


def _reconcile_score(
    item: dict, out: TriageOut, *, role: str = "resident", interactive: bool = False
) -> TriageOut:
    """Q3-4: ``score`` must equal the deterministic sum-to-score mapping of the
    ``(novelty, magnitude, core_relevance)`` components the model also returned, AND
    ``reason_he``'s own stated conclusion (if any) must not name a different level -- ids 10/67 in
    the QA sample had a numerically self-consistent score whose own ``reason_he`` still concluded
    with the *wrong* level word (e.g. "...רמה orange" for a score of 8, which the config's red
    threshold puts at "red"). On either mismatch, one corrective retry (mirroring the
    schema-validation "one corrective retry" contract used elsewhere); if the retry still
    disagrees, fall back to the deterministic value computed from whatever components came back
    and log ``triage_score_reconciled`` -- the caller then always has an internally-consistent
    result, never blocking the pipeline on this."""
    expected = _expected_score(out.novelty, out.magnitude, out.core_relevance)
    conflicting_level = _reason_conflicting_level(out.reason_he, level_for(expected))
    if out.score == expected and conflicting_level is None:
        return out
    log.warning(
        "triage_score_mismatch_retry",
        item_id=item.get("id"),
        returned_score=out.score,
        expected_score=expected,
        conflicting_level_in_reason=conflicting_level,
        novelty=out.novelty,
        magnitude=out.magnitude,
        core_relevance=out.core_relevance,
    )
    conflict_note = (
        f' כמו כן, הנימוק שלך (reason_he) מסיק רמה "{conflicting_level}", אך זה סותר את הרמה '
        f'הנכונה ("{level_for(expected)}") לפי score={expected}.'
        if conflicting_level
        else ""
    )
    retry_messages = [
        {"role": "system", "content": _triage_system()},
        {"role": "user", "content": _triage_prompt(item)},
        {"role": "assistant", "content": out.model_dump_json()},
        {
            "role": "user",
            "content": (
                f"שים לב: הרכיבים שנתת (core_relevance={out.core_relevance}, "
                f"magnitude={out.magnitude}, novelty={out.novelty}) מסתכמים ל-"
                f"{out.novelty + out.magnitude + out.core_relevance}, שמתאים לפי הטבלה הקבועה ל-"
                f"score={expected}, אבל החזרת score={out.score} -- הנתונים סותרים זה את זה."
                f"{conflict_note} score הסופי, ומסקנת הרמה בתוך reason_he אם קיימת, חייבים "
                "להתאים בדיוק לתרגום הטבלה של סכום שלושת הרכיבים. החזר JSON מלא ותקין עם score "
                "ו-reason_he מתוקנים (אפשר גם לתקן את הרכיבים עצמם אם טעית בהם)."
            ),
        },
    ]
    try:
        retried = chat_structured(role, TriageOut, retry_messages, task="triage", interactive=interactive)
    except LLMOutputError as exc:
        log.warning("triage_score_reconcile_retry_failed", item_id=item.get("id"), error=str(exc)[:200])
        out.score = expected
        log.warning("triage_score_reconciled", item_id=item.get("id"), score=expected)
        return out
    retried_expected = _expected_score(retried.novelty, retried.magnitude, retried.core_relevance)
    retried_conflict = _reason_conflicting_level(retried.reason_he, level_for(retried_expected))
    if retried.score != retried_expected or retried_conflict is not None:
        retried.score = retried_expected
        log.warning("triage_score_reconciled", item_id=item.get("id"), score=retried_expected)
    return retried


# --- A13 (מיקוד תעשייה ישראלית, 2026-09-06) -- BEGIN --------------------------------------
# Deterministic "מעורבות ישראלית" score component, applied *after* the LLM's own score is
# reconciled against its components (Q3-4, above) -- never lowers `out.score`, only ever raises
# it (and the derived `level` with it), per docs/PLAN_WINDOWS_NATIVE.md row A13: +1 when the
# item's own `israel_relevance` (eoa.pipeline.israel_focus, computed in classify.py) is >= 0.6,
# +2 more when an Israeli watchlist company is itself one of this item's `entities_mentioned`
# (i.e. directly a party to the story, not just a background export-market/competitor signal).
def _apply_israel_focus_boost(item: dict, out: TriageOut) -> TriageOut:
    try:
        from eoa.pipeline.israel_focus import israel_relevance, israeli_watchlist_names

        entities = item.get("entities_mentioned") or []
        israel_score = item.get("israel_relevance")
        if israel_score is None:
            text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
            israel_score = israel_relevance(
                text, entities, lang=item.get("lang"), geography=item.get("geography")
            )["score"]
        boost = 0
        if israel_score >= 0.6:
            boost += 1
        israeli_names = {n.casefold() for n in israeli_watchlist_names()}
        if any((e or "").casefold() in israeli_names for e in entities):
            boost += 2
        if boost:
            new_score = min(10, out.score + boost)
            log.info(
                "israel_focus_triage_boost",
                item_id=item.get("id"),
                original_score=out.score,
                boost=boost,
                new_score=new_score,
            )
            out.score = new_score
    except Exception as exc:
        log.debug("israel_focus_triage_boost_failed", item_id=item.get("id"), error=str(exc)[:120])
    return out
# --- A13 -- END ----------------------------------------------------------------------------


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
    out = _reconcile_score(item, out, role=role, interactive=interactive)
    out = _apply_israel_focus_boost(item, out)
    out.level = level_for(out.score)  # type: ignore[assignment]
    return out


def triage_batch(items: list[dict], *, role: str = "resident") -> dict[int, TriageOut]:
    """U8-6 batch mode (Revision 2026-09-06): triage up to ``BATCH_SIZE`` items in one cloud call
    instead of one call per item; returns ``{item_id: TriageOut}`` with ``level`` already
    recomputed from config thresholds, same as ``triage_item``. Only used by ``run_triage`` when
    ``is_cloud_batch_mode()`` is true. Q3-4's score/component reconciliation still runs per-item
    (a mismatched item gets its own single corrective call, same as the non-batch path -- the
    batch call itself is not retried just for this)."""
    prompts = [(it["id"], _triage_prompt(it)) for it in items]
    results = chat_structured_batch(role, TriageOut, prompts, system=_triage_system(), task="triage")
    items_by_id = {it["id"]: it for it in items}
    for item_id, out in results.items():
        item = items_by_id.get(item_id)
        if item is not None:
            results[item_id] = _reconcile_score(item, out, role=role)
            results[item_id] = _apply_israel_focus_boost(item, results[item_id])
        results[item_id].level = level_for(results[item_id].score)  # type: ignore[assignment]
    return results


def run_triage(limit: int = 300, role: str = "resident", *, item_ids: list[int] | None = None) -> TriageStats:
    """Triage all classified, in-scope items.

    F22: ``item_ids`` (optional, additive) scopes this run to just those ids -- see
    ``eoa.memory.relational.get_items_for_stage``."""
    stats = TriageStats()
    eligible: list[dict] = []
    for it in get_items_for_stage(STAGE, limit, item_ids=item_ids):
        if it.get("domain") is None:
            continue  # not classified yet — leave for the next pass, do not mark
        if (
            it.get("security_status") in ("quarantined", "blocked")
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
                    update_item_fields(
                        it["id"], score=out.score, level=out.level, triage_reason=out.reason_he[:600]
                    )
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


#: A13 (מיקוד תעשייה ישראלית, 2026-09-06): appended to a deep-search question when the item's
#: own israel_relevance (eoa.pipeline.israel_focus, computed in classify.py) is >= 0.6 -- per
#: docs/PLAN_WINDOWS_NATIVE.md row A13 point 6, "מה המשמעות לתעשייה הישראלית / למי מהחברות
#: הישראליות זה נוגע".
_ISRAEL_DEEP_SEARCH_SUBQUESTION_HE = (
    " בנוסף: מה המשמעות לתעשייה הישראלית ולמי מהחברות הישראליות זה נוגע?"
)
_ISRAEL_DEEP_SEARCH_THRESHOLD = 0.6


def _enqueue_deep_search(item: dict, out: TriageOut) -> None:
    from eoa.memory.relational import enqueue_job

    question, seed_en = _ensure_valid_investigation_question(
        item, out.deep_search_question, out.deep_search_seed_en
    )
    # --- A13 -- BEGIN ------------------------------------------------------------------------
    if (item.get("israel_relevance") or 0) >= _ISRAEL_DEEP_SEARCH_THRESHOLD:
        question = f"{question}{_ISRAEL_DEEP_SEARCH_SUBQUESTION_HE}"
    # --- A13 -- END --------------------------------------------------------------------------
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
