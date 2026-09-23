"""Stage: report/claims_gate -- deterministic guard against unsupported evaluative/intensifier
claims in LLM-authored report prose (BLUF, executive summary, analyst note, trend sections, domain
review sections), for the monthly and weekly reports.

Root cause (docs/qa/content_review/CR-monthly.md, 2026-09-08 user feedback on
``output/reports/monthly_2026-09-30.md``): the drafting model routinely wrote magnitude/novelty/
drama words ("ניכר"/"ניכרת", "זינוק", "דרמטי", "משמעותי", "קפיצת מדרגה", "מסמן/ת", "התעצמות",
"הולכת וגוברת", "מגמה", "יוצא/ת דופן", "היסטורי/ת", "ענק") with **no number anywhere in the same
sentence** to back the claim up -- "ניכרת התעצמות דרמטית ברכש מערכות הגנה אווירית" asserts a
magnitude of change while citing zero items or counts for it. This module is a deterministic (no
LLM) sentence-level gate: a sentence carrying one of those trigger words is allowed to stand ONLY
if it also carries a supporting quantity (a digit, a percentage, a magnitude word, or an explicit
comparison to a stated baseline) in the SAME sentence. Otherwise the trigger phrase is removed; if
nothing evidentiary is left afterwards, the whole sentence is dropped. Citation markers (the
``cites`` field / rendered ``[n]``) are never touched either way.

Applied ONLY to already-drafted, model-authored content (``Sentence.text_he`` values and
``AnalystNote.sentences_he`` strings) -- never to deterministic, code-authored scaffolding text
(trend change-notes like "מגמה חדשה החודש.", table captions, headings). Scaffolding text cannot be
a hallucinated claim in the first place (it is generated straight from already-verified numbers
elsewhere in the same paragraph), so gating it would only risk corrupting report structure for no
safety benefit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------
# quantity detector -- a sentence "supports" its own intensifier when it also contains one of these
# --------------------------------------------------------------------------

_QUANTITY_RE = re.compile(
    r"\d"  # any digit: a count, a percent, a dollar figure, a year, "N פריטים"...
    r"|%"
    r"|\bאחוז(ים)?\b"
    r"|\bמיליון\b"
    r"|\bמיליארד\b"
    r"|\bאלף\b"
    r"|\bפי\s+\d"  # "פי 2" (a stated multiplier)
    r"|\bלעומת\s+(ה)?ממוצע\b"  # explicit comparison to a stated baseline
)


def _has_quantity(text: str) -> bool:
    return bool(_QUANTITY_RE.search(text or ""))


# --------------------------------------------------------------------------
# trigger phrases (verbatim list from CR-monthly.md item 3) -- each maps to a deterministic
# neutral replacement (usually deletion; a couple of multi-word phrases get a specific rewrite so
# the resulting sentence still reads as a sentence, matching the worked example in the task:
# "ניכרת התעצמות דרמטית ברכש" -> "נרשמו דיווחים על רכש").
# --------------------------------------------------------------------------

_PHRASE_RULES: list[tuple[re.Pattern[str], str]] = [
    # specific, higher-priority rewrite (checked before the generic "ניכר" deletion below)
    (re.compile(r"ניכר(ת|ים)?\s+התעצמות(\s+דרמטית)?\s+ב"), "נרשמו דיווחים על "),
    (re.compile(r"ניכר(ת|ים)?\s*"), ""),
    (re.compile(r"זינוק\s*"), ""),
    (re.compile(r"דרמטי(ת|ים)?\s*"), ""),
    (re.compile(r"משמעותי(ת|ים)?\s*"), ""),
    (re.compile(r"תאוצה\s*"), ""),
    (re.compile(r"עלייה\s+חדה\s*"), "עלייה "),
    (re.compile(r"קפיצת\s+מדרגה\s*"), ""),
    (re.compile(r"מצביע(ה)?\s+בבירור\s+על\s*"), "קשור ל"),
    # "מסמן" (denotes) uses the final-form nun (ן) only when word-final; every suffixed form
    # ("מסמנת"/"מסמנים"/"מסמנות") uses the regular nun (נ) instead -- match both.
    (re.compile(r"מסמ[נן](ה|ת|ים|ות)?\s*"), ""),
    (re.compile(r"התעצמות\s*"), ""),
    (re.compile(r"הולכת\s+וגוברת\s*"), ""),
    (re.compile(r"מגמה\s*"), ""),
    (re.compile(r"יוצא(ת)?\s+דופן\s*"), ""),
    (re.compile(r"היסטורי(ת|ים)?\s*"), ""),
    (re.compile(r"ענק(ית|ים)?\s*"), ""),
    # lead additions 2026-09-08 (live monthly 172: "התכנסות בולטת", "בהיקפים גדולים")
    (re.compile(r"בולט(ת|ים|ות)?\s*"), ""),
    (re.compile(r"בהיקפים\s+גדולים\s*"), ""),
    (re.compile(r"חסר(ת|י)?\s+תקדים\s*"), ""),
]

#: Same list, used purely for detection (does the sentence carry ANY trigger word at all) -- kept
#: as a separate compiled alternation so ``d6_daily_report``'s rendered-Markdown backstop check can
#: reuse the exact same definition without re-running every substitution.
TRIGGER_RE = re.compile("|".join(p.pattern for p, _ in _PHRASE_RULES))

_TOKEN_RE = re.compile(r"[א-ת]{2,}|[A-Za-z]{2,}|\d+")
_FILLER_WORDS_HE = {
    "החודש",
    "השבוע",
    "כי",
    "על",
    "עם",
    "גם",
    "עוד",
    "כן",
    "זה",
    "זו",
    "אלה",
    "של",
    "אשר",
    "הוא",
    "היא",
    "אל",
    "ב",
    "ל",
}
_MIN_CONTENT_TOKENS = 3


def _is_vacuous(text: str) -> bool:
    """After stripping the trigger phrase(s), is anything substantive left? Citation markers and
    generic connector words don't count -- "החודש ." or "בשוק ה-EO/IR" (subject with no predicate
    left) reads as noise, not a claim, and must be dropped rather than rendered."""
    cleaned = re.sub(r"\[\d+\]", "", text or "")
    tokens = [t for t in _TOKEN_RE.findall(cleaned) if t not in _FILLER_WORDS_HE]
    return len(tokens) < _MIN_CONTENT_TOKENS


def gate_text(text: str) -> str | None:
    """Core per-sentence rule: returns the (possibly softened) text, or ``None`` if the sentence
    must be dropped entirely. A sentence with no trigger word, or a trigger word already backed by
    a quantity in the same sentence, passes through unchanged."""
    if not text or not text.strip():
        return text
    if _has_quantity(text):
        return text
    if not TRIGGER_RE.search(text):
        return text
    softened = text
    for pattern, repl in _PHRASE_RULES:
        softened = pattern.sub(repl, softened)
    softened = re.sub(r"\s{2,}", " ", softened).strip(" ,.-")
    if _is_vacuous(softened):
        return None
    return softened


@dataclass
class ClaimsGateResult:
    softened: int = 0
    dropped: int = 0
    softened_examples: list[tuple[str, str]] = field(default_factory=list)
    dropped_examples: list[str] = field(default_factory=list)

    def merge(self, other: ClaimsGateResult) -> None:
        self.softened += other.softened
        self.dropped += other.dropped
        self.softened_examples.extend(other.softened_examples)
        self.dropped_examples.extend(other.dropped_examples)


_SentenceT = TypeVar("_SentenceT")


def gate_sentences(
    sentences: list[_SentenceT], *, context: str
) -> tuple[list[_SentenceT], ClaimsGateResult]:
    """Gates a list of ``Sentence``-shaped objects (anything with a ``.text_he`` attribute and a
    pydantic-style ``model_copy(update=...)``) -- used for ``exec_summary``/``bluf``/trend and
    section ``sentences``. Never touches ``cites``."""
    result = ClaimsGateResult()
    out: list[_SentenceT] = []
    for s in sentences:
        text = getattr(s, "text_he", None)
        new_text = gate_text(text)
        if new_text is None:
            result.dropped += 1
            result.dropped_examples.append(text or "")
            log.info("claims_gate.dropped", context=context, text=(text or "")[:200])
            continue
        if new_text != text:
            result.softened += 1
            result.softened_examples.append((text or "", new_text))
            log.info("claims_gate.softened", context=context, before=(text or "")[:200], after=new_text[:200])
            out.append(s.model_copy(update={"text_he": new_text}))
        else:
            out.append(s)
    return out, result


def gate_plain_strings(strings: list[str], *, context: str) -> tuple[list[str], ClaimsGateResult]:
    """Gates a list of plain Hebrew strings with no ``cites`` (``AnalystNote.sentences_he``)."""
    result = ClaimsGateResult()
    out: list[str] = []
    for text in strings:
        new_text = gate_text(text)
        if new_text is None:
            result.dropped += 1
            result.dropped_examples.append(text)
            log.info("claims_gate.dropped", context=context, text=text[:200])
            continue
        if new_text != text:
            result.softened += 1
            result.softened_examples.append((text, new_text))
            log.info("claims_gate.softened", context=context, before=text[:200], after=new_text[:200])
        out.append(new_text)
    return out, result


def apply_claims_gate(draft: Any, report_kind: str) -> tuple[Any, ClaimsGateResult]:
    """Walks a ``WeeklyReportDraft``/``MonthlyReportDraft``-shaped object's model-authored fields
    (``bluf``, ``exec_summary``, ``trends[].sentences``, ``sections[].sentences``,
    ``analyst_note_he.sentences_he``) and gates every one, per this module's own docstring. Uses
    ``getattr``/duck typing throughout (no import of the schema classes) so it works unchanged for
    both draft shapes and any future one with the same field names -- the same convention
    ``eoa.report.qa_citations``/``eoa.report.textnorm`` already use for these drafts."""
    total = ClaimsGateResult()
    updates: dict[str, Any] = {}

    bluf = getattr(draft, "bluf", None)
    if bluf:
        gated, sub = gate_sentences(bluf, context=f"{report_kind}.bluf")
        total.merge(sub)
        updates["bluf"] = gated

    exec_summary = getattr(draft, "exec_summary", None)
    if exec_summary:
        gated, sub = gate_sentences(exec_summary, context=f"{report_kind}.exec_summary")
        total.merge(sub)
        updates["exec_summary"] = gated

    analyst_note = getattr(draft, "analyst_note_he", None)
    note_sentences = getattr(analyst_note, "sentences_he", None) if analyst_note is not None else None
    if note_sentences:
        gated_strings, sub = gate_plain_strings(note_sentences, context=f"{report_kind}.analyst_note")
        total.merge(sub)
        updates["analyst_note_he"] = (
            analyst_note.model_copy(update={"sentences_he": gated_strings}) if gated_strings else None
        )

    trends_list = getattr(draft, "trends", None)
    if trends_list:
        new_trends = []
        for t in trends_list:
            t_sentences = getattr(t, "sentences", None)
            if t_sentences:
                gated, sub = gate_sentences(t_sentences, context=f"{report_kind}.trend[{t.title_he}]")
                total.merge(sub)
                t = t.model_copy(update={"sentences": gated})
            new_trends.append(t)
        updates["trends"] = new_trends

    sections_list = getattr(draft, "sections", None)
    if sections_list:
        new_sections = []
        for sec in sections_list:
            sec_sentences = getattr(sec, "sentences", None)
            if sec_sentences:
                gated, sub = gate_sentences(
                    sec_sentences, context=f"{report_kind}.section[{getattr(sec, 'title_he', '')}]"
                )
                total.merge(sub)
                sec = sec.model_copy(update={"sentences": gated})
            new_sections.append(sec)
        updates["sections"] = new_sections

    if updates:
        draft = draft.model_copy(update=updates)
    return draft, total


__all__ = [
    "TRIGGER_RE",
    "ClaimsGateResult",
    "apply_claims_gate",
    "gate_plain_strings",
    "gate_sentences",
    "gate_text",
]

# --------------------------------------------------------------------------
# Lead additions 2026-09-08: the gate also covers QUOTED content the reports embed verbatim --
# item summary/so-what cells and deep-search answers -- because the user reads those inside the
# report and cannot tell they came from another module ("מעבר היסטורי", "קפיצת מדרגה טכנולוגית",
# "יתרון משמעותי" survived monthly 172 in exactly those places). Display-time softening only:
# the stored rows are never rewritten.
# --------------------------------------------------------------------------


_SENT_END_RE = re.compile(r"(?<=[.!?؟])\s+(?=\S)")


def _split_sentences(text: str) -> list[str]:
    """Sentence split on terminal punctuation followed by whitespace; keeps citation markers with
    their sentence. Newlines are treated as boundaries too (bullets / short lines)."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        out.extend(p for p in _SENT_END_RE.split(line) if p.strip())
    return out


def soften_text(text: str | None) -> str | None:
    """Sentence-wise :func:`gate_text` over a free-text field; drops sentences the gate drops."""
    if not text:
        return text
    parts = _split_sentences(text)
    kept: list[str] = []
    for p in parts:
        g = gate_text(p)
        if not g:
            continue
        g = g.strip()
        if g[-1] not in ".!?:؟" and p.rstrip()[-1] in ".!?:؟":
            g += p.rstrip()[-1]  # keep the sentence-final punctuation the rewrite dropped
        kept.append(g)
    return " ".join(kept).strip() or None


def gate_item_texts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Soften ``summary_he``/``so_what_he`` of collected report items in place (returns the list).

    F17: a fully rejected sentence (:func:`soften_text` returns ``None``/empty) is KEPT as such,
    never silently replaced by the original unsupported text -- the ``or it[key]`` fallback this
    used to have defeated the whole point of the gate (rendering exactly the unsupported claim it
    just rejected). Renderers already treat a falsy ``summary_he``/``so_what_he`` as an empty state
    (``it.get('summary_he') or '—'`` and equivalents throughout ``eoa.report.*``)."""
    for it in items:
        for key in ("summary_he", "so_what_he"):
            if it.get(key):
                it[key] = soften_text(it[key])
    return items


def gate_deep_search_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Soften the deep-search entries' answer/context/contradiction text and key facts.

    F17: same fix as :func:`gate_item_texts` -- a fully rejected (``None``) result is kept, not
    replaced by the original text ``or`` fell back to."""
    for e in entries:
        for key in ("answer_he", "contradictions_he", "what_was_tried_he"):
            if e.get(key):
                e[key] = soften_text(e[key])
        if e.get("key_facts"):
            e["key_facts"] = [g for g in (gate_text(f) for f in e["key_facts"]) if g]
    return entries
