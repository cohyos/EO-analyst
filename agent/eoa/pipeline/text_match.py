"""Shared word-boundary-safe text matching (PD-vocab-extract, docs/PLAN_SPEC_VOCABULARY.md section
3.2): promotes ``eoa.dossier.corpus._word_present`` to a non-private, cross-package location so
``eoa.dossier.vocabulary`` (and anything else that needs the same "MWS must not match inside
MWSXYZ" precision ``config/product_lines.yaml``'s own ``keywords_en``/aliases matching already
relies on) can reuse it instead of reimplementing it. ``eoa.dossier.corpus._word_present`` itself
now delegates here (see that module) -- this is the one real implementation.

:func:`synonym_present` adds the Hebrew/Latin dispatch ``config/spec_vocabulary.yaml``'s own header
comment documents for its ``synonyms`` field: a Hebrew-scripted synonym matches as a plain
case-insensitive substring (Hebrew has no word-boundary ambiguity the way a short Latin acronym
does); a Latin synonym matches word-boundary-safe, checked against both the Latin and Hebrew text
(an English acronym may legitimately appear embedded in Hebrew prose).
"""

from __future__ import annotations

import re

_HEBREW_RE = re.compile(r"[֐-׿]")


def word_present(text: str, term: str) -> bool:
    """``True`` iff ``term`` appears in ``text`` as a whole word (``\\b``-delimited,
    case-insensitive) -- the exact rule that keeps a short acronym like "MWS" from false-matching
    inside an unrelated longer word."""
    term = (term or "").strip()
    if not term:
        return False
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text or "", re.IGNORECASE) is not None


def is_hebrew_term(term: str) -> bool:
    return bool(_HEBREW_RE.search(term or ""))


def synonym_present(text_he: str, text_en: str, synonym: str) -> bool:
    """A vocabulary ``synonyms`` entry counts as present in ``text_he``/``text_en`` -- Hebrew as a
    plain case-insensitive substring, Latin as a word-boundary match checked against both texts
    (a Latin acronym may appear embedded in a Hebrew sentence)."""
    synonym = (synonym or "").strip()
    if not synonym:
        return False
    if is_hebrew_term(synonym):
        return synonym.casefold() in (text_he or "").casefold()
    return word_present(text_en or "", synonym) or word_present(text_he or "", synonym)


__all__ = ["is_hebrew_term", "synonym_present", "word_present"]
