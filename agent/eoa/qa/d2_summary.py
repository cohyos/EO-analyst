"""D2 -- summary / "so what" deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D2)."""

from __future__ import annotations

import re
from typing import Any

from eoa.pipeline.triage import _META_PHRASES
from eoa.qa.types import Check, DomainScore, weighted_score

#: Generic LLM disclaimer/refusal chatter that must never leak into an analyst-facing summary --
#: supplements ``eoa.pipeline.triage._META_PHRASES`` (which catches "the article already covers
#: this" meta-commentary, a narrower failure mode) with the more classic "I am a language model"
#: shape of chatter.
_MODEL_CHATTER_PHRASES: tuple[str, ...] = (
    "as an ai",
    "as a language model",
    "i cannot",
    "i'm sorry",
    "i am sorry",
    "אינני יכול",
    "כמודל שפה",
    "בתור מודל שפה",
    "אני מודל בינה מלאכותית",
    "לא אוכל לספק",
)

_MIN_SUMMARY_CHARS = 40
_MAX_SUMMARY_CHARS = 900
_HEBREW_RE = re.compile(r"[֐-׿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
#: A technical/English term kept in parentheses, per docs/CONVENTIONS.md rule 11 ("Technical
#: terms keep English in parentheses") -- at least one Latin letter inside "(...)".
_PAREN_TERM_RE = re.compile(r"\([^()]*[A-Za-z][^()]*\)")


def _has_chatter(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _MODEL_CHATTER_PHRASES) or any(p in text for p in _META_PHRASES)


def _hebrew_dominant(text: str) -> bool:
    he = len(_HEBREW_RE.findall(text))
    la = len(_LATIN_RE.findall(text))
    if he + la == 0:
        return True  # numbers/punctuation only -- nothing to judge
    return he >= la


def score_D2(sample: list[dict[str, Any]], conn: Any = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D2: summary/so-what deterministic checks over ``sample`` (``items`` rows), restricted to
    in-scope items (``domain != out_of_scope``) that have actually reached the analyze stage
    (``summary_he`` populated) -- an out-of-scope or not-yet-analyzed item has no summary to grade.
    """
    in_scope = [
        it
        for it in sample
        if (it.get("domain") or "out_of_scope") != "out_of_scope" and (it.get("summary_he") or "").strip()
    ]
    n = len(in_scope)
    if n == 0:
        return DomainScore(domain="D2", score_0_100=None, checks=[], n=0, note="no in-scope analyzed items")

    length_bad = []
    hebrew_bad = []
    chatter_bad = []
    terminology_missing = []
    for it in in_scope:
        summary = it.get("summary_he") or ""
        so_what = it.get("so_what_he") or ""
        combined = f"{summary} {so_what}".strip()
        if not (_MIN_SUMMARY_CHARS <= len(summary) <= _MAX_SUMMARY_CHARS):
            length_bad.append(it["id"])
        if not _hebrew_dominant(combined):
            hebrew_bad.append(it["id"])
        if _has_chatter(combined):
            chatter_bad.append(it["id"])
        # terminology check only applies when the combined text is long enough to plausibly need
        # a parenthesised technical term at all (short summaries of a purely narrative item are
        # not penalised for lacking one).
        if len(combined) >= 120 and not _PAREN_TERM_RE.search(combined):
            terminology_missing.append(it["id"])

    checks = [
        Check(
            "summary_length_bounds",
            passed=len(length_bad) / n <= 0.15,
            weight=1.5,
            evidence=f"{n - len(length_bad)}/{n} within [{_MIN_SUMMARY_CHARS},{_MAX_SUMMARY_CHARS}] chars; bad: {length_bad[:10]}",
        ),
        Check(
            "hebrew_dominant",
            passed=len(hebrew_bad) == 0,
            weight=2.0,
            evidence=f"{n - len(hebrew_bad)}/{n} Hebrew-dominant; bad ids: {hebrew_bad[:10]}",
        ),
        Check(
            "no_model_chatter",
            passed=len(chatter_bad) == 0,
            weight=2.5,
            evidence=f"{n - len(chatter_bad)}/{n} clean; bad ids: {chatter_bad[:10]}",
        ),
        Check(
            "terminology_in_parens_when_relevant",
            passed=len(terminology_missing) / n <= 0.4,
            weight=1.0,
            evidence=(
                f"{n - len(terminology_missing)}/{n} carry a parenthesised term; "
                f"missing ids (informational, high false-positive rate): {terminology_missing[:10]}"
            ),
        ),
    ]
    return DomainScore(domain="D2", score_0_100=weighted_score(checks), checks=checks, n=n)
