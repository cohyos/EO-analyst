"""Stage: analyze — Hebrew summary, So-What, events, and graph edges with provenance."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost, LLMOutputError, ResourceUnavailable
from eoa.execution import checkpoint
from eoa.llm.ollama_client import (
    DATA_GUARD_SYSTEM,
    chat_structured,
    chat_structured_batch,
    is_cloud_batch_mode,
    wrap_data,
)
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import AnalyzeOut, EventOut, SoWhatRepairOut
from eoa.memory.relational import (
    get_items_for_stage,
    insert_event,
    mark_stage,
    update_item_fields,
    upsert_entity,
)
from eoa.pipeline.analysis_grounding import ground_analysis_fields, is_too_thin
from eoa.pipeline.event_grounding import ground_event
from eoa.pipeline.opportunity_signals import TAG as PLATFORM_OPPORTUNITY_TAG

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
#: Hebrew VERB-form stems and NOUN-form business-event words, plus their common English
#: equivalents -- a title can legitimately be in either language (this codebase's `events.title`
#: is not Hebrew-only), and Hebrew business-event phrasing is often a noun construct ("רכישת",
#: "פתיחת", "שיתוף פעולה") rather than the conjugated verb its triliteral root would suggest
#: ("רכש"), which a plain substring match against only the verb stem misses entirely. The initial
#: verb-stems-only list under-matched real events lacking a populated party/customer/amount/date
#: (Elbit-Serbia UAV partnership, satellite launches, an arms-embargo partial lift -- all
#: real events, all flagged as narrative before this widening), which would have made
#: ``_is_narrative_event_title`` reject genuine future events, not just clean up assessment prose.
_OCCURRENCE_VERBS_HE = (
    "זכ",  # זכה/זכתה/זכייה (won)
    "חתמ",  # חתם/חתמה/חתימה/חתימת (signed/signing)
    "רכש",  # רכש/רכשה (acquired/purchased, verb form)
    "רכיש",  # רכישה/רכישת (acquisition, noun form)
    "השיק",  # השיק/השיקה (launched, verb form)
    "השק",  # השקה/השקת (launch, noun form)
    "פרסמ",  # פרסם/פרסמה (published/announced)
    "מינ",  # מינה/מינתה/מינוי (appointed/appointment)
    "אישר",  # אישר/אישרה/אישור (approved/approval)
    "העניק",  # העניקה (granted/awarded)
    "גייס",  # גייסה (raised — investment)
    "השלימ",  # השלימה (completed)
    "מסר",  # מסרה (delivered)
    "סיפק",  # סיפקה (supplied)
    "בדק",  # בדק/בדקה/נבדק (tested)
    "פיתח",  # פיתח/פיתחה (developed)
    "השתלט",  # השתלטה (acquired/took over)
    "מיזג",  # מיזגה/התמזגה (merged)
    "שיתוף פעולה",  # cooperation/partnership (noun form)
    "פתיח",  # פתיחה/פתיחת (opening, e.g. an exhibition/plant)
    "הסר",  # הסרה/הסרת (removal, e.g. a sanction/embargo)
    "הקמ",  # הקמה/הקמת (establishment, e.g. a plant/factory)
    "launch",
    "signs",
    "signed",
    "wins",
    "won",
    "awarded",
    "acquires",
    "acquired",
    "acquisition",
    "announces",
    "announced",
    "opens",
    "opened",
    "opening",
    "partners",
    "partnership",
    "invests",
    "invested",
    "raises",
    "raised",
    "completes",
    "completed",
    "delivers",
    "delivered",
    "tests",
    "tested",
    "develops",
    "developed",
    "merges",
    "merged",
    "orders",
    "order",
    "prioritizes",
    "establishes",
    "establishment",
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
    except (DeadlineExceeded, LeaseLost):
        raise
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


#: CR-platform-opportunity (2026-09-16): prepended to `context` (same mechanism as the partial-
#: content note below) when classify.py's apply_platform_opportunity_gate tagged this item
#: `platform_integration_opportunity` -- tells the model to phrase `so_what_he` for a BD reader
#: (which Israeli product line could bid, the platform's own stated timeline, hedged) even though
#: the item's own domain/subdomain may carry little EO/IR technical depth of its own -- without
#: this note the model's default ASSESSMENT-mode instructions (analyze.md) have no reason to frame
#: the so-what around a bid opportunity rather than a generic competitive read.
_PLATFORM_OPPORTUNITY_CONTEXT_NOTE_HE = (
    "הערה: פריט זה תויג ב-classify כ'הזדמנות אינטגרציה בפלטפורמה' (platform_integration_opportunity) "
    "-- הפלטפורמה המתוארת עשויה לפתוח חריץ אינטגרציה חיצוני לפוד/חיישן EO/IR, גם אם אין בפריט עצמו "
    "תוכן טכני EO/IR ממשי. נסח את so_what_he מנקודת מבט פיתוח עסקי (BD): איזה קו מוצר ישראלי רלוונטי "
    "(אם ניתן לזהות לפי הפלטפורמה/הצורך המתואר), מה לוח הזמנים המוצהר של הפלטפורמה (אם נמסר במקור), "
    "בניסוח מסויג (\"עשוי להוות\", \"פוטנציאל ל...\") ולא כעובדה ודאית -- ותמיד לפי הכמות/הפרטים "
    "שנמסרו במקור קודם לכל ניסוח איכותני."
)


def _analyze_prompt(item: dict) -> str:
    context = _context_for(item)
    if item.get("content_status") == "partial":
        context = f"{_PARTIAL_CONTENT_CONTEXT_NOTE_HE}\n\n{context}"
    if PLATFORM_OPPORTUNITY_TAG in (item.get("tags") or []):
        context = f"{_PLATFORM_OPPORTUNITY_CONTEXT_NOTE_HE}\n\n{context}"
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


#: Round-3 D2 (docs/qa/loop/round_2_judge.md): the formulaic assessments the judge counted on 32
#: items DB-wide. The prompt now forbids them, but a 12B model still reaches for them ("מחזקת את
#: מעמדה של תעשייה אווירית כמובילה טכנולוגית" on item 39 after the prompt change), so a
#: ``so_what_he`` matching any of these gets ONE targeted corrective pass that rewrites just that
#: field around a concrete beneficiary / loser / change. Morphology-tolerant on purpose.
_GENERIC_SO_WHAT_RES = (
    re.compile(r"מחזק(?:ת|ים|ות)?\s+את\s+מעמד"),
    re.compile(r"מהוו(?:ה|ים|ות)\s+צעד\s+משמעותי"),
    re.compile(r"מעיד(?:ה|ים|ות)?\s+על\s+מגמה"),
    re.compile(r"צפוי(?:ה|ים|ות)?\s+לחזק\s+את\s+מעמד"),
)

_SO_WHAT_REPAIR_INSTRUCTION_HE = (
    "ה-so_what_he שכתבת משתמש בנוסחה גנרית ({phrase}). כתוב אותו מחדש (1-3 משפטים, מתחיל ב'להערכתנו') "
    "כך שיאמר במפורש: מי מרוויח ומי נפגע (שם חברה/תוכנית/לקוח), מה משתנה בפועל (יכולת, עלות, לוח "
    "זמנים, נתח שוק) ולמה זה נובע מהעובדות שבמקור. אסור לחזור על הביטוי הגנרי או על מקבילה שלו. "
    "החזר JSON עם השדה so_what_he בלבד.\n\nהתקציר: {summary}\n\nה-so_what המקורי: {so_what}"
)


def generic_so_what_phrase(text: str | None) -> str | None:
    """The first generic formula found in ``text`` (or ``None``)."""
    for rx in _GENERIC_SO_WHAT_RES:
        m = rx.search(text or "")
        if m:
            return m.group(0)
    return None


def _repair_generic_so_what(item: dict, out: AnalyzeOut, *, role: str, interactive: bool) -> AnalyzeOut:
    phrase = generic_so_what_phrase(out.so_what_he)
    if not phrase:
        return out
    try:
        fixed = chat_structured(
            role,
            SoWhatRepairOut,
            [
                {"role": "system", "content": _analyze_system()},
                {"role": "user", "content": _analyze_prompt(item)},
                {"role": "assistant", "content": out.model_dump_json(exclude_none=True)[:6000]},
                {
                    "role": "user",
                    "content": _SO_WHAT_REPAIR_INSTRUCTION_HE.format(
                        phrase=phrase, summary=out.summary_he, so_what=out.so_what_he
                    ),
                },
            ],
            task="summarize",
            interactive=interactive,
            options={"temperature": 0.4},
        )
    except (LLMOutputError, ResourceUnavailable) as exc:
        log.warning("so_what_repair_failed", item_id=item.get("id"), error=str(exc)[:120])
        return out
    text = (fixed.so_what_he or "").strip()
    if not text or generic_so_what_phrase(text) or not text.startswith("להערכתנו"):
        log.info(
            "so_what_repair_rejected",
            item_id=item.get("id"),
            still_generic=bool(generic_so_what_phrase(text)),
        )
        return out
    log.info("so_what_repaired", item_id=item.get("id"), phrase=phrase)
    return out.model_copy(update={"so_what_he": text})


def repair_so_what_text(
    item: dict,
    *,
    so_what_he: str,
    summary_he: str,
    phrase: str,
    role: str = "resident",
    interactive: bool = False,
) -> str | None:
    """Round-6 data-repair entry point (``scripts/repair_round6.py``): re-generate an *already
    persisted* ``items.so_what_he`` that a QA pass flagged against
    ``eoa.report.qa_citations.SO_WHAT_TEMPLATE_PHRASES_HE`` -- a broader, QA-owned banned-phrase
    list than this module's own :data:`_GENERIC_SO_WHAT_RES` (which only gates the corrective pass
    :func:`_repair_generic_so_what` runs during a *fresh* analyze call, and does not cover every
    phrase in the QA list, e.g. "מהווה צעד נוסף"/"מהווה צעד חשוב"). Calling
    :func:`_repair_generic_so_what` directly would silently no-op on such a phrase (its own gate
    wouldn't fire), so this function shares its exact LLM-call shape (same system/user/assistant/
    repair-instruction messages) but is driven by the caller's already-matched ``phrase`` and
    persisted text instead of re-deriving them from a freshly-generated :class:`AnalyzeOut`.

    Returns the repaired ``so_what_he`` (guaranteed non-empty and starting with ``"להערכתנו"`` --
    the same acceptance bar :func:`_repair_generic_so_what` applies), or ``None`` if the LLM call
    failed or its output didn't clear that bar. The caller (the repair script) is responsible for
    the round-6-specific validation that the *result* also avoids every
    ``SO_WHAT_TEMPLATE_PHRASES_HE`` phrase and reads as 1-3 Hebrew sentences before persisting it."""
    try:
        fixed = chat_structured(
            role,
            SoWhatRepairOut,
            [
                {"role": "system", "content": _analyze_system()},
                {"role": "user", "content": _analyze_prompt(item)},
                {
                    "role": "assistant",
                    "content": AnalyzeOut(summary_he=summary_he, so_what_he=so_what_he).model_dump_json(
                        exclude_none=True
                    )[:6000],
                },
                {
                    "role": "user",
                    "content": _SO_WHAT_REPAIR_INSTRUCTION_HE.format(
                        phrase=phrase, summary=summary_he, so_what=so_what_he
                    ),
                },
            ],
            task="summarize",
            interactive=interactive,
            options={"temperature": 0.4},
        )
    except (LLMOutputError, ResourceUnavailable) as exc:
        log.warning("so_what_round6_repair_failed", item_id=item.get("id"), error=str(exc)[:120])
        return None
    text = (fixed.so_what_he or "").strip()
    if not text or not text.startswith("להערכתנו"):
        log.info("so_what_round6_repair_rejected", item_id=item.get("id"), text=text[:160])
        return None
    # Round-14 (2026-09-07): this repaired so_what_he is a fresh LLM output about to be persisted
    # directly by the caller (it does not itself go through persist_analysis's own grounding call)
    # -- ground it here so a round-6/13/14-style repair pass can never reintroduce an ungrounded
    # organisation/affiliation/competitor claim.
    text = ground_analysis_fields(item, so_what_he=text).so_what_he
    if is_too_thin(text, require_prefix="להערכתנו"):
        log.info("so_what_round6_repair_rejected_ungrounded", item_id=item.get("id"), text=text[:160])
        return None
    return text


def analyze_item(item: dict, *, role: str = "resident", interactive: bool = False) -> AnalyzeOut:
    """Produce the AnalyzeOut for one item (does not persist). A generic-formula ``so_what_he``
    gets one targeted corrective pass (:func:`_repair_generic_so_what`)."""
    out = chat_structured(
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
    return _repair_generic_so_what(item, out, role=role, interactive=interactive)


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
    except (DeadlineExceeded, LeaseLost):
        raise
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


# R6-forecast (round 6 judge D3, docs/qa/loop/round_5_judge.md finding W5-followup): the model
# sometimes extracts one facet of a multi-party military exercise as its own ``kind='test'`` event
# (e.g. "ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים" -- literally containing "ניסוי" -- extracted
# alongside sibling ``deployment``/``partnership`` events from the same item, all sharing the
# ``program`` "Operation Atlantic City"). Read on its own, kind='test' misleadingly reads as a
# standalone weapon test; the source is actually describing one NATO Arctic exercise deployment.
# ``events.kind`` carries a DB CHECK constraint to the 9 ``EventOut`` literals (db/migrations/
# versions/0001_core.py) -- there is no 'exercise' value to write -- so this reclassifies onto the
# closest existing literal, 'deployment' ("פריסה"), rather than inventing a value the insert would
# reject. Conservative: only ever touches 'test' events whose own title/summary/program names an
# exercise/deployment/named operation; every other kind, and a genuine test with no such vocabulary
# (e.g. plain "ירי ניסיוני של הטיל בוצע בהצלחה"), is left untouched.
_EXERCISE_VOCAB_RE = re.compile(
    r"תרגיל|\bexercise(?:s)?\b|\bdeployment\b|\bOperation\s+[A-Z][A-Za-z]+",
)


def _looks_like_exercise(ev: EventOut) -> bool:
    text = " ".join(str(x) for x in (ev.title, ev.summary_he, ev.program) if x)
    return bool(_EXERCISE_VOCAB_RE.search(text))


def _reclassify_exercise_kind(ev: EventOut) -> EventOut:
    """R6-forecast: a ``kind='test'`` event whose text also names an exercise/deployment/named
    operation is reclassified to ``'deployment'`` -- see the module note above :data:`_EXERCISE_VOCAB_RE`.
    A no-op for every other kind, and for a 'test' event with no exercise vocabulary at all."""
    if ev.kind != "test" or not _looks_like_exercise(ev):
        return ev
    log.info("event_kind_test_reclassified_deployment", title=(ev.title or "")[:160], program=ev.program)
    return ev.model_copy(update={"kind": "deployment"})


_KEY_FACTS_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
#: D1 round-1 fix (docs/qa/loop/round_1_fixes.md, ``key_facts_no_duplicates``, items 5/10/51):
#: threshold above which two key_facts entries are treated as the same fact restated with minor
#: wording differences (e.g. "בחו״ל לפני מעבר לייצור בארה״ב" vs. "בחו״ל לפני מעבר לייצור בארה״ס")
#: rather than two distinct facts. Chosen per the task brief; ``difflib.SequenceMatcher.ratio()``
#: on the already case/punctuation-normalized strings.
_KEY_FACTS_NEAR_DUP_RATIO = 0.9


def _key_facts_dedupe_key(fact: str) -> str:
    """Case- and punctuation-insensitive normalisation for exact-duplicate comparison. Q3-9
    originally only stripped whitespace/case; round-1 (docs/qa/loop/round_1_fixes.md) adds
    punctuation, since two entries differing only in a trailing period or an added comma were
    still slipping through as "different" facts."""
    stripped = _KEY_FACTS_PUNCT_RE.sub("", fact.casefold())
    return " ".join(stripped.split())


def _dedupe_key_facts(facts: list[str]) -> list[str]:
    """Q3-9 (docs/qa/findings_Q3_r1.md) + D1 round-1 fix (docs/qa/loop/round_1_fixes.md): drop
    ``key_facts`` entries that are either an exact duplicate (modulo whitespace/case/punctuation)
    of an earlier one, or a *near*-duplicate of one -- the same fact restated with small wording
    differences (``difflib.SequenceMatcher.ratio() >= _KEY_FACTS_NEAR_DUP_RATIO`` against every
    fact already kept) -- keeping the first occurrence's original text and order in both cases."""
    seen_keys: set[str] = set()
    kept_keys: list[str] = []
    result: list[str] = []
    for fact in facts:
        if not fact or not fact.strip():
            continue
        key = _key_facts_dedupe_key(fact)
        if not key:
            continue
        if key in seen_keys:
            continue
        if any(
            difflib.SequenceMatcher(None, key, other).ratio() >= _KEY_FACTS_NEAR_DUP_RATIO
            for other in kept_keys
        ):
            continue
        seen_keys.add(key)
        kept_keys.append(key)
        result.append(fact)
    return result


def _backfill_entities_from_watchlist(item: dict) -> list[str] | None:
    """Q3-8, extended round-2 (2026-09-06, judge D3, docs/qa/loop/round_0_judge.md item 2): fill
    ``entities_mentioned`` with any watchlist/curated-org name plainly present in the item's own
    title/text that isn't already there -- deterministically, via
    ``eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text``.

    Originally (Q3-8) this only ever ran when ``entities_mentioned`` was completely empty (classify
    extracted nothing at all). Round-2 found the same gap on a *partially*-filled list: item 50's
    TITAN award ($192M to Palantir + Anduril) had ``entities_mentioned = [US Army, Anduril,
    BlueHalo]`` -- Palantir, a lead named party in the item's own summary/key_facts, was silently
    dropped by the LLM's own extraction and never recovered because the old guard bailed out the
    moment the list was non-empty. Now always unions in any additional match rather than skipping
    the check once the model found *something*. Returns ``None`` only when there is truly nothing
    new to add (already-complete list, or no watchlist alias found in the text at all) --
    preserves the original "nothing to persist" contract for callers that skip a no-op write."""
    from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text

    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    matched = find_watchlist_aliases_in_text(text)
    if not matched:
        return None
    existing = item.get("entities_mentioned") or []
    combined = list(dict.fromkeys([*existing, *matched]))
    return combined if combined != existing else None


#: Round-3 (docs/qa/loop/round_2_judge.md D3, events 55/84/89/121): the analyze prompt tells the
#: model to copy every number "as it appears in the source", so "$464.8 million" was persisted as
#: ``amount_usd = 464.8``. The prompt now asks for full units, and this deterministic pass anchors
#: the stored figure back to the source text regardless: when the number the model wrote appears
#: in the item's title/text immediately followed by a magnitude word, the amount is scaled by it.
_AMOUNT_SCALE_RE_TEMPLATE = (
    r"(?<![\d.,])(?:US\$|USD|\$|€|£)?\s*{num}\s*(?:-|to|–)?\s*"
    r"(?P<mag>billion|bn|b|million|mn|mm|m|thousand|k|מיליארד|מיליון|אלף|מיליארדי|מיליוני)\b"
)
_AMOUNT_MAGNITUDE = {
    "billion": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "מיליארד": 1e9,
    "מיליארדי": 1e9,
    "million": 1e6,
    "mn": 1e6,
    "mm": 1e6,
    "m": 1e6,
    "מיליון": 1e6,
    "מיליוני": 1e6,
    "thousand": 1e3,
    "k": 1e3,
    "אלף": 1e3,
}
#: Amounts at or above this are already in full units; a smaller figure is checked against the
#: source for a magnitude word (a real four-figure price such as "$1,595" simply finds none).
_AMOUNT_SUSPICIOUS_BELOW = 100_000.0


def normalize_amount_from_source(amount: float | None, text: str | None) -> tuple[float | None, str | None]:
    """Return ``(amount, magnitude_word)``: the amount scaled to full units when ``text`` shows the
    same number followed by a magnitude word ("464.8 million" -> 464800000.0, "million"); otherwise
    ``(amount, None)`` unchanged. Only amounts below :data:`_AMOUNT_SUSPICIOUS_BELOW` are examined."""
    if amount is None or not text or amount <= 0 or amount >= _AMOUNT_SUSPICIOUS_BELOW:
        return amount, None
    # the number as the model most likely saw it: "464.8", "464,8", "10", "1,595"
    if float(amount).is_integer():
        base = str(int(amount))
        variants = [base, f"{int(amount):,}"]
    else:
        base = f"{amount:.4f}".rstrip("0").rstrip(".")
        variants = [base, base.replace(".", ",")]
    for v in dict.fromkeys(variants):
        pat = _AMOUNT_SCALE_RE_TEMPLATE.format(num=re.escape(v))
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            mag = m.group("mag").casefold()
            return round(amount * _AMOUNT_MAGNITUDE[mag], 2), mag
    return amount, None


def _source_text_for_amounts(item: dict) -> str:
    return " ".join(filter(None, [item.get("title"), item.get("clean_text"), item.get("raw_text")]))


def _recall_event_parties(ev: EventOut) -> list[str]:
    """Round-2 (2026-09-06, judge D3 item 2): a watchlist/curated-org name plainly present in an
    event's own ``summary_he`` (the LLM's own extracted event narrative) but missing from that same
    event's ``parties`` list -- e.g. item 50's TITAN event named Palantir + Anduril in its
    ``summary_he`` while ``parties`` only carried a subset. Unions any such name in, preserving the
    model's own party ordering first. A no-op (returns ``ev.parties`` unchanged) when
    ``summary_he`` is empty or names nothing new."""
    if not ev.summary_he:
        return ev.parties
    from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text

    matched = find_watchlist_aliases_in_text(ev.summary_he)
    if not matched:
        return ev.parties
    return list(dict.fromkeys([*ev.parties, *matched]))


def _with_partial_content_note(item: dict, uncertainty_he: str | None) -> str | None:
    """Q3-10: for a 'partial'-content item, guarantee `uncertainty_he` names the paywall/partial
    condition -- even when the model's own `uncertainty_he` said nothing about it (the model only
    sees the note in its prompt context, best-effort; this makes the persisted field authoritative
    regardless)."""
    if item.get("content_status") != "partial":
        return uncertainty_he
    if uncertainty_he and PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE in uncertainty_he:
        return uncertainty_he
    return (
        f"{PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE}. {uncertainty_he}"
        if uncertainty_he
        else PARTIAL_CONTENT_UNCERTAINTY_NOTE_HE
    )


#: Round-6 D3/D9 continuation (docs/qa/loop/round_6_fixes.md, "R6-entities" task 2): entities
#: extracted from an out-of-scope/archived item pollute both `items.entities_mentioned` and the
#: `entities` table itself (the live finding: item 22, domain='air_defense' but level='archive',
#: had AIM-120 AMRAAM/NASAMS/F-16s/Western Partners/Ukraine/Russia written into
#: entities_mentioned by a *direct* analyze_item/persist_analysis call in the R6-data round --
#: get_items_for_stage's own analyze-stage scope filter (_ANALYZE_STAGE_SCOPE_FILTER,
#: eoa.memory.relational) only protects the normal run_analyze sweep, not a targeted per-item
#: call like scripts/repair_round6.py's). This is that defense-in-depth guard.
def _entity_persistence_allowed(item: dict) -> bool:
    """False when `item`'s domain == 'out_of_scope' or level == 'archive' -- persist_analysis
    must then skip `items.entities_mentioned` and the `entities`/`graph_edges` upserts entirely
    (every other field it writes is unaffected). A missing/unset domain or level (not yet
    classified/triaged) is never blocked -- only an explicit 'out_of_scope'/'archive' value is."""
    return item.get("domain") != "out_of_scope" and item.get("level") != "archive"


# --------------------------------------------------------------------------
# Round-6 D9 continuation ("R6-entities" task 3): a small, conservative, deterministic net for
# junk entity-name *shapes* observed live in the out-of-scope-entity population
# (scripts/repair_round6.py's entities_cleanup subcommand), applied at persistence time. Narrower
# and more conservative than eoa.pipeline.entity_normalize.is_junk_entity (not owned by this
# round's file list) -- this exists to catch specific shapes that module's technique/generic-
# concept checks miss: a bare plural of a platform/weapon designation ("F-16s", "M1s"), a generic
# "who talked" two-word phrase ("Western Partners"), or a wildlife/nature word in a name typed
# kind='company' ("Western Burrowing Owl", entity 1208). Mirrored as a small local duplicate in
# eoa.memory.relational (`_is_junk_shaped_entity_name`) rather than imported, per this codebase's
# convention for a cross-module-boundary helper (e.g. this module's own `_event_dedup_key`
# mirroring `eoa.report.daily._normalize_event_key`).
# --------------------------------------------------------------------------
_PLATFORM_DESIGNATION_PLURAL_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Za-z]?s$")
_GENERIC_TWO_WORD_STOPLIST = frozenset(
    {
        "western partners",
        "local partners",
        "industry partners",
        "defense officials",
        "government officials",
    }
)
_WILDLIFE_NATURE_WORDS = ("owl", "eagle", "habitat", "wildlife", "conservation")


def is_junk_candidate_entity_name(name: str | None, kind: str | None = None) -> bool:
    """True when `name` should never be persisted as an entity, or into
    `items.entities_mentioned`, regardless of what upstream extraction produced it -- see the
    module note above. Deliberately narrow: callers only ever use this to *drop* a fresh candidate
    before writing, never to reject something already on record."""
    if not name or not name.strip():
        return False
    n = name.strip()
    if _PLATFORM_DESIGNATION_PLURAL_RE.match(n):
        return True
    if n.casefold() in _GENERIC_TWO_WORD_STOPLIST:
        return True
    return kind == "company" and any(w in n.casefold() for w in _WILDLIFE_NATURE_WORDS)


def _entities_from_persisted_events(event_party_names: list[str], edge_entity_names: list[str]) -> list[str]:
    """Round-6 D3 continuation ("R6-entities" task 2): order-preserving, deduped union of every
    event-party name and edge-endpoint name captured during *this* `persist_analysis` call --
    moved in from `scripts/repair_round6.py`'s original `_entities_from_events` (R6-data round),
    which discovered live (items 153/290) that `events`/`graph_edges` could already carry real
    named parties that were never unioned back into `items.entities_mentioned` at all. Unlike the
    original script-only version, this reads the in-memory events/edges just processed in this
    same call rather than re-querying the DB. Junk-shaped names (see
    :func:`is_junk_candidate_entity_name`) are the caller's responsibility to filter."""
    names: list[str] = []
    for name in [*event_party_names, *edge_entity_names]:
        if name and name not in names:
            names.append(name)
    return names


def persist_analysis(item: dict, out: AnalyzeOut) -> tuple[int, int]:
    """Write summary/so-what/events/edges. Returns (events_written, edges_written).

    Round-6 D3/D9: entity persistence -- `items.entities_mentioned` and the `entities`/
    `graph_edges` upserts below -- is skipped entirely when `item` is out of scope or archived
    (see :func:`_entity_persistence_allowed`); every other field this function writes is
    unaffected."""
    entity_persistence_ok = _entity_persistence_allowed(item)
    entities_backfill = _backfill_entities_from_watchlist(item) if entity_persistence_ok else None
    extra_fields: dict[str, Any] = {}
    if entities_backfill:
        log.info("analyze_entities_backfilled", item_id=item["id"], entities=entities_backfill)
        extra_fields["entities_mentioned"] = entities_backfill
    elif not entity_persistence_ok and item.get("entities_mentioned"):
        log.info(
            "analyze_entity_persistence_skipped_out_of_scope",
            item_id=item["id"],
            domain=item.get("domain"),
            level=item.get("level"),
        )

    # --- A13 (מיקוד תעשייה ישראלית, 2026-09-06) -- BEGIN ------------------------------------
    # Refresh eoa.pipeline.israel_focus.israel_relevance() now that entities_mentioned may have
    # just been backfilled above (classify.py's own "# --- A13" block already scored the item
    # once, before any watchlist-alias backfill existed) -- never *lowers* a score classify
    # already set higher (analyze sees strictly more signal: the backfilled entities plus the
    # full analyzed text), matching triage.py's "never lowering" contract for its own score bump.
    try:
        from eoa.pipeline.israel_focus import israel_relevance, score_and_persist_entity_israeli

        entities_for_scoring = extra_fields.get("entities_mentioned") or item.get("entities_mentioned") or []
        text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
        refreshed = israel_relevance(
            text, entities_for_scoring, lang=item.get("lang"), geography=item.get("geography")
        )
        if refreshed["score"] > (item.get("israel_relevance") or 0):
            extra_fields["israel_relevance"] = refreshed["score"]
            extra_fields["israel_reasons"] = refreshed["reasons"]
        for name in entities_for_scoring:
            score_and_persist_entity_israeli(name)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("israel_relevance_refresh_failed", item_id=item["id"], error=str(exc)[:120])
    # --- A13 -- END --------------------------------------------------------------------------

    # Round-14 (2026-09-07, item-39 fabrication root-cause fix): the analysis-stage grounding
    # guard -- summary_he/so_what_he/key_facts/entities_mentioned must never assert an
    # organisation/affiliation/competitor/number the item's own source text (or the watchlist, for
    # a company it actually mentions) doesn't support. Every removal is logged by
    # analysis_grounding itself (analysis.ungrounded_<category>_removed); a field left too thin by
    # stripping is persisted as-is here (never blocks the pipeline) and picked up for a fresh
    # LLM re-analysis by scripts/repair_round14_grounding.py's own sweep.
    # entities_mentioned is only ever ground-checked (and possibly rewritten) when entity
    # persistence is allowed at all -- an out-of-scope/archived item's entities_mentioned must
    # never be touched, matching _entity_persistence_allowed's own contract elsewhere in this
    # function (round-6 D3/D9).
    entities_for_grounding = (
        extra_fields.get("entities_mentioned") or item.get("entities_mentioned")
        if entity_persistence_ok
        else None
    )
    grounding = ground_analysis_fields(
        item,
        summary_he=out.summary_he,
        so_what_he=out.so_what_he,
        key_facts=_dedupe_key_facts(list(out.key_facts)),
        entities_mentioned=entities_for_grounding,
    )
    if grounding.removed:
        log.info(
            "analyze_grounding_stripped",
            item_id=item["id"],
            n_removed=len(grounding.removed),
            categories=sorted({r["category"] for r in grounding.removed}),
        )
    if is_too_thin(grounding.summary_he) or is_too_thin(grounding.so_what_he, require_prefix="להערכתנו"):
        log.warning(
            "analyze_grounding_left_too_thin",
            item_id=item["id"],
            summary_he=grounding.summary_he[:160],
            so_what_he=grounding.so_what_he[:160],
        )
    if entity_persistence_ok and grounding.entities_mentioned != (entities_for_grounding or []):
        extra_fields["entities_mentioned"] = grounding.entities_mentioned

    update_item_fields(
        item["id"],
        summary_he=grounding.summary_he,
        so_what_he=grounding.so_what_he,
        key_facts=grounding.key_facts,
        uncertainty_he=_with_partial_content_note(item, out.uncertainty_he or None),
        # A12 (מעקב טכנולוגי): additive, only ever non-null for domain == "tech_dev" -- the LLM
        # is instructed (prompts/analyze.md) to leave these null/empty for every other domain.
        tech_maturity=out.tech_maturity,
        tech_actor_kind=out.tech_actor_kind,
        tech_readiness_note_he=out.tech_readiness_note_he or None,
        **extra_fields,
    )
    n_events = 0
    persisted_event_party_names: list[str] = []
    persisted_event_ids: list[int] = []
    for ev in _dedup_events(out.events):
        ev = _reclassify_exercise_kind(ev)
        if _is_narrative_event_title(ev.title, ev):
            # Q3-6: an assessment/forecast sentence dressed up as an event -- never persisted.
            log.info("event_rejected_narrative_title", item_id=item["id"], title=(ev.title or "")[:160])
            continue
        event_parties = _recall_event_parties(ev)
        amount_usd, magnitude = normalize_amount_from_source(ev.amount_usd, _source_text_for_amounts(item))
        if magnitude:
            log.info(
                "event_amount_scaled",
                item_id=item["id"],
                raw=ev.amount_usd,
                scaled=amount_usd,
                magnitude=magnitude,
            )
        # CR-events (2026-09-07, docs/qa/content_review/CR-events.md): ground every extracted event
        # against the item's own source text before it is persisted -- amount magnitude, customer,
        # parties and kind vocabulary must be traceable to the source (events 22/23: an Anduril
        # appointment article became a fabricated $10B m_and_a against the Israeli MoD).
        grounded = ground_event(
            item,
            {
                "kind": ev.kind, "title": ev.title, "date": ev.date, "amount_usd": amount_usd,
                "currency": ev.currency, "parties": event_parties, "customer": ev.customer,
                "program": ev.program, "summary_he": ev.summary_he, "confidence": ev.confidence,
            },
        )
        if grounded is None:
            log.info("event.dropped", item_id=item["id"], title=(ev.title or "")[:160])
            continue
        if grounded.dropped_fields:
            log.info(
                "event.ungrounded_field_dropped",
                item_id=item["id"], title=(ev.title or "")[:160], fields=grounded.dropped_fields,
            )
        try:
            event_id = insert_event(
                item_id=item["id"],
                kind=grounded.kind,
                title=grounded.title,
                date=_parse_date(grounded.date),
                amount_usd=grounded.amount_usd,
                currency=grounded.currency,
                parties=grounded.parties,
                customer=grounded.customer,
                program=grounded.program,
                summary_he=grounded.summary_he,
                confidence=grounded.confidence,
            )
            n_events += 1
            persisted_event_ids.append(event_id)
            persisted_event_party_names.extend(p for p in (grounded.parties or []) if p)
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.warning("event_insert_failed", item_id=item["id"], error=str(exc)[:160])
    n_edges = 0
    persisted_edge_entity_names: list[str] = []
    if out.edges and not entity_persistence_ok:
        # Round-6 D3/D9: the analyzer's own entity/graph-edge upserts are entity persistence --
        # skipped for an out-of-scope/archived item exactly like items.entities_mentioned above.
        log.info(
            "analyze_edges_skipped_out_of_scope",
            item_id=item["id"],
            n_edges_candidate=len(out.edges),
            domain=item.get("domain"),
            level=item.get("level"),
        )
    elif out.edges:
        try:
            from eoa.memory.graph import add_edge, merge_entity
            from eoa.pipeline.entity_normalize import canonical_name_and_kind

            names = sorted({e.src for e in out.edges} | {e.dst for e in out.edges})
            kind_by_name = _resolve_edge_kinds(names)
            for e in out.edges:
                # Q3-13 r3 (docs/qa/findings_Q3_r2.md): resolve each endpoint's *canonical*
                # name/kind once, up front, and use that canonical form consistently for both
                # `upsert_entity` and `merge_entity` below. Previously `merge_entity` was called
                # with the raw, as-extracted name (e.g. "USAF") even though `upsert_entity` had
                # just canonicalised it onto an existing row (e.g. "US Air Force", id 685) --
                # `merge_entity` does a raw `UPDATE entities SET name = ...`, so it was silently
                # renaming the row *back* to the raw spelling on every edge write, undoing Q3-13's
                # de-duplication and eventually recreating a same-name-different-case/spelling
                # duplicate the moment another item's extraction used the row's canonical name.
                src_name, src_kind = canonical_name_and_kind(e.src, kind_by_name.get(e.src, "company"))
                dst_name, dst_kind = canonical_name_and_kind(e.dst, kind_by_name.get(e.dst, "company"))
                src_id = upsert_entity(name=src_name, kind=src_kind, first_seen_item=item["id"])
                dst_id = upsert_entity(name=dst_name, kind=dst_kind, first_seen_item=item["id"])
                if src_id is None or dst_id is None:
                    # Q3-13: one (or both) endpoints was rejected by upsert_entity as junk (a
                    # technique-like or generic-non-entity name) -- the edge itself is meaningless.
                    log.info("edge_skipped_rejected_entity", item_id=item["id"], src=e.src, dst=e.dst)
                    continue
                merge_entity(src_id, src_name, src_kind, None)
                merge_entity(dst_id, dst_name, dst_kind, None)
                add_edge(src_id, dst_id, e.label, item["id"], {"evidence": e.evidence_he[:300]})
                n_edges += 1
                persisted_edge_entity_names.extend([src_name, dst_name])
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.warning("edge_write_failed", item_id=item["id"], error=str(exc)[:160])

    # Round-6 D3 continuation ("R6-entities" task 2): when neither the LLM's own extraction nor
    # the watchlist backfill above populated entities_mentioned for an in-scope item, fall back to
    # the party/edge-endpoint names captured in THIS SAME analyze pass (see
    # _entities_from_persisted_events) -- moved in from scripts/repair_round6.py's original
    # _entities_from_events (R6-data). Never runs when entity persistence is blocked (out-of-scope/
    # archived item), and drops any junk-shaped candidate (_is_junk_candidate_entity_name) so a
    # bare "F-16s"/"Western Partners" in an event's own parties list never backfills the field.
    if entity_persistence_ok:
        existing_entities = extra_fields.get("entities_mentioned") or item.get("entities_mentioned") or []
        if not existing_entities:
            fallback_names = [
                n
                for n in _entities_from_persisted_events(
                    persisted_event_party_names, persisted_edge_entity_names
                )
                if not is_junk_candidate_entity_name(n)
            ]
            if fallback_names:
                update_item_fields(item["id"], entities_mentioned=fallback_names)
                log.info(
                    "analyze_entities_backfilled_from_events", item_id=item["id"], entities=fallback_names
                )

    # --- PL-backend (user request 2026-09-07) -- BEGIN --------------------------------------
    # Deterministic (no LLM) product-line tagging, run last so it sees every field this function
    # may have just refreshed (summary/so_what, entities_mentioned incl. any watchlist/event
    # backfill above, subdomain from classify). Tags this item's own `product_lines` column plus
    # every event just persisted for it (an event has no domain/subdomain of its own -- it inherits
    # its parent item's tags, since it was extracted from the same text). Never breaks the pipeline
    # on failure (same defensive convention as the A13 israel_relevance block above).
    try:
        from eoa.db import connection
        from eoa.product_lines.tagging import tag_product_lines

        entities_for_tagging = extra_fields.get("entities_mentioned") or item.get("entities_mentioned") or []
        text_he = " ".join(filter(None, [grounding.summary_he, grounding.so_what_he]))
        product_lines = tag_product_lines(
            text_he=text_he,
            text_en=item.get("title"),
            entities=entities_for_tagging,
            subdomain=item.get("subdomain"),
        )
        tag_method = "deterministic"
        # R8-tagging (2026-09-07): the deterministic pass above is precise but incomplete -- a
        # genuinely relevant item that never uses one of the configured keyword strings verbatim
        # gets no tag at all. When it found nothing, `product_lines.yaml`'s `llm_tagging: true`
        # switch is on, and this item is in scope (same domain/level gate `entity_persistence_ok`
        # above already computed), give the LLM-assisted fallback one shot at this single item (a
        # batch of 1 is still a valid `chat_structured_batch` call -- the ~15-per-batch case is
        # `scripts/backfill_product_lines.py --llm`'s own bulk sweep, not this per-item hook).
        # Never breaks the pipeline on failure -- `llm_tag_batch` itself never raises.
        if not product_lines and entity_persistence_ok:
            from eoa.product_lines.registry import llm_tagging_enabled

            if llm_tagging_enabled():
                from eoa.product_lines.llm_tagging import llm_tag_batch

                llm_result = llm_tag_batch(
                    [{"id": item["id"], "title": item.get("title"), "summary_he": text_he}]
                )
                llm_lines = llm_result.get(item["id"])
                if llm_lines:
                    product_lines = llm_lines
                    tag_method = "llm"
        # CR-platform-opportunity (2026-09-16): UNION with whatever eoa.pipeline.classify's
        # persist_classification already wrote to items.product_lines (the deterministic platform-
        # integration-opportunity pre-check, eoa.pipeline.opportunity_signals) rather than
        # overwrite it -- this stage's own tag_product_lines/llm_tag_batch match against
        # summary_he/so_what_he/subdomain and may legitimately find nothing (or a different,
        # non-overlapping set) for an item whose classify-stage tag was set from title/clean_text
        # alone; overwriting would silently drop a classify-stage tag this function never re-derives.
        existing_product_lines = item.get("product_lines") or []
        if existing_product_lines:
            product_lines = list(dict.fromkeys([*existing_product_lines, *product_lines]))
        if product_lines:
            update_item_fields(item["id"], product_lines=product_lines)
            if persisted_event_ids:
                with connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE events SET product_lines = %(lines)s WHERE id = ANY(%(ids)s)",
                        {"lines": product_lines, "ids": persisted_event_ids},
                    )
            log.info(
                "product_lines_tagged", item_id=item["id"], product_lines=product_lines, method=tag_method
            )
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("product_lines_tagging_failed", item_id=item["id"], error=str(exc)[:160])
    # --- PL-backend -- END ----------------------------------------------------------------

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
    except (DeadlineExceeded, LeaseLost):
        raise
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
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.debug("content_status_update_failed", item_id=it["id"], error=str(exc)[:120])
    it["content_status"] = status
    return status


def run_analyze(limit: int = 120, role: str = "resident", min_level: str = "yellow") -> AnalyzeStats:
    """Analyze triaged items at or above ``min_level`` (red > orange > yellow)."""
    checkpoint()
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
        checkpoint()
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
        batch_size = min(BATCH_SIZE, settings().llm_providers.cloud_batch_size)
        for i in range(0, len(eligible), batch_size):
            checkpoint()
            chunk = eligible[i : i + batch_size]
            try:
                results = analyze_batch(chunk, role=role)
            except ResourceUnavailable:
                log.warning("analyze_batch_deferred_resources", n=len(chunk))
                raise
            except LLMOutputError as exc:
                log.error("analyze_batch_bad_output", n=len(chunk), error=str(exc)[:200])
                stats.failed += len(chunk)
                continue
            for it in chunk:
                checkpoint()
                out = results.get(it["id"])
                if out is None:
                    log.error("analyze_batch_missing_item", item_id=it["id"])
                    stats.failed += 1
                    continue
                try:
                    _persist_analysis_and_score(it, out, stats)
                except (DeadlineExceeded, LeaseLost):
                    raise
                except Exception as exc:
                    log.error("analyze_persist_failed", item_id=it["id"], error=str(exc)[:200])
                    stats.failed += 1
        log.info("analyze_done", **stats.__dict__)
        return stats
    # --- end U8-6 batch mode -------------------------------------------------------------------

    for it in eligible:
        checkpoint()
        try:
            out = analyze_item(it, role=role)
            _persist_analysis_and_score(it, out, stats)
        except ResourceUnavailable:
            log.warning("analyze_deferred_resources", item_id=it["id"])
            raise
        except LLMOutputError as exc:
            log.error("analyze_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.error("analyze_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("analyze_done", **stats.__dict__)
    return stats
