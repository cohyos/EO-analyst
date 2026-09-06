"""D1 -- classification & triage deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D1).

Every check below reuses an existing pipeline detector rather than re-implementing its logic --
see the module docstring's file list in the task brief. Nothing here mutates the DB; every
function takes an already-fetched ``items`` row dict (``dict_row`` shape, as returned by
``eoa.db.connection()``).
"""

from __future__ import annotations

from typing import Any

from eoa.config import settings
from eoa.llm.ollama_client import (
    _ASCII_QUOTE_BETWEEN_HEBREW_RE,
    _looks_truncated_mid_hebrew_acronym,
)
from eoa.pipeline.classify import _has_eoir_vocabulary, _watchlist_alias_hit
from eoa.pipeline.triage import _reason_conflicting_level, level_for
from eoa.qa.types import Check, DomainScore, weighted_score

#: Hebrew free-text fields on an ``items`` row worth running the truncation/gershayim detectors
#: over -- (db column, field_name to pass the detector -- "*_he" turns on the broad "ends without
#: terminal punctuation" net, not just the exact acronym-stem match). ``triage_reason`` holds the
#: same free-sentence prose as ``TriageOut.reason_he``, so it is given that synthetic "_he" name
#: here to enable the broad net. ``key_facts``/``israel_reasons`` are deliberately KEPT OFF the
#: broad net (passed under their own literal names, which do not end in "_he") -- verified against
#: the live DB (round 0) that they are short bullet-style noun phrases with no terminal punctuation
#: by design, not truncated sentences; the broad net produced a wall of false positives on them.
#: The exact acronym-stem check (still applied to every field regardless of name) remains active.
_HE_SCALAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("summary_he", "summary_he"),
    ("so_what_he", "so_what_he"),
    ("triage_reason", "reason_he"),
    ("uncertainty_he", "uncertainty_he"),
    ("tech_readiness_note_he", "tech_readiness_note_he"),
)
_HE_LIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("key_facts", "key_facts"),
    ("israel_reasons", "israel_reasons"),
)


def _taxonomy_sub_keys() -> dict[str, set[str]]:
    domains = settings().taxonomy.get("domains", {}) or {}
    return {key: set((d.get("sub") or {}).keys()) for key, d in domains.items()}


def _subdomain_valid(item: dict[str, Any], sub_keys: dict[str, set[str]]) -> bool:
    domain = item.get("domain")
    subdomain = item.get("subdomain") or ""
    if not domain or domain == "out_of_scope":
        return True  # no subdomain required once out of scope
    if domain not in sub_keys:
        return False
    return subdomain in sub_keys[domain]


def _no_eoir_gate_agrees(item: dict[str, Any]) -> bool:
    """True if the item's stored ``domain`` agrees with :func:`apply_no_eoir_gate`'s own rule:
    an in-scope domain with zero extracted entities, zero EO/IR vocabulary, and zero watchlist
    alias hit should never have survived as in-scope."""
    domain = item.get("domain")
    if not domain or domain == "out_of_scope":
        return True
    if item.get("entities_mentioned"):
        return True
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    return _has_eoir_vocabulary(text) or _watchlist_alias_hit(text)


def _reason_score_consistent(item: dict[str, Any]) -> bool:
    """Re-derives the expected level from the stored ``score`` (the components themselves are not
    persisted on ``items``, only the reconciled ``score``/``level`` -- so this checks the weaker
    but still meaningful invariant: ``level`` matches ``level_for(score)``, and ``triage_reason``'s
    own stated conclusion, if any, does not name a different level)."""
    score = item.get("score")
    level = item.get("level")
    if score is None or level is None:
        return True  # not triaged yet -- nothing to check
    expected_level = level_for(int(score))
    if level != expected_level:
        return False
    conflicting = _reason_conflicting_level(item.get("triage_reason") or "", expected_level)
    return conflicting is None


def _truncation_hits(item: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    for column, field_name in _HE_SCALAR_FIELDS:
        value = item.get(column)
        if isinstance(value, str) and _looks_truncated_mid_hebrew_acronym(value, field_name):
            hits.append(f"item {item.get('id')}.{column}")
    for column, field_name in _HE_LIST_FIELDS:
        for value in item.get(column) or []:
            if isinstance(value, str) and _looks_truncated_mid_hebrew_acronym(value, field_name):
                hits.append(f"item {item.get('id')}.{column}")
    return hits


def _gershayim_violations(item: dict[str, Any]) -> list[str]:
    hits: list[str] = []
    for column, _field_name in _HE_SCALAR_FIELDS:
        value = item.get(column)
        if isinstance(value, str) and _ASCII_QUOTE_BETWEEN_HEBREW_RE.search(value):
            hits.append(f"item {item.get('id')}.{column}")
    for column, _field_name in _HE_LIST_FIELDS:
        for value in item.get(column) or []:
            if isinstance(value, str) and _ASCII_QUOTE_BETWEEN_HEBREW_RE.search(value):
                hits.append(f"item {item.get('id')}.{column}")
    return hits


def _key_facts_has_duplicates(item: dict[str, Any]) -> bool:
    facts = [f.strip().casefold() for f in (item.get("key_facts") or []) if isinstance(f, str) and f.strip()]
    return len(facts) != len(set(facts))


def score_D1(sample: list[dict[str, Any]], conn: Any = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D1: classification/triage deterministic checks over ``sample`` (list of ``items`` rows).

    ``conn`` is accepted for interface consistency with the other ``score_Dn`` functions but is
    not needed here -- every check is a pure function of the item row plus config/taxonomy.
    """
    n = len(sample)
    if n == 0:
        return DomainScore(domain="D1", score_0_100=None, checks=[], n=0, note="empty sample")

    sub_keys = _taxonomy_sub_keys()
    subdomain_bad = [it["id"] for it in sample if not _subdomain_valid(it, sub_keys)]
    gate_bad = [it["id"] for it in sample if not _no_eoir_gate_agrees(it)]
    reason_bad = [it["id"] for it in sample if not _reason_score_consistent(it)]
    truncation_hits = [h for it in sample for h in _truncation_hits(it)]
    gershayim_hits = [h for it in sample for h in _gershayim_violations(it)]
    dup_bad = [it["id"] for it in sample if _key_facts_has_duplicates(it)]
    in_scope = [it for it in sample if (it.get("domain") or "out_of_scope") != "out_of_scope"]
    empty_entities = [
        it["id"] for it in in_scope if not (it.get("entities_mentioned") or it.get("key_facts"))
    ]

    checks = [
        Check(
            "subdomain_valid_vs_taxonomy",
            passed=len(subdomain_bad) == 0,
            weight=2.0,
            evidence=f"{n - len(subdomain_bad)}/{n} valid; bad ids: {subdomain_bad[:10]}",
        ),
        Check(
            "no_eoir_gate_agreement",
            passed=len(gate_bad) == 0,
            weight=2.0,
            evidence=f"{n - len(gate_bad)}/{n} agree; disagreeing ids: {gate_bad[:10]}",
        ),
        Check(
            "reason_score_level_consistency",
            passed=len(reason_bad) == 0,
            weight=2.0,
            evidence=f"{n - len(reason_bad)}/{n} consistent; bad ids: {reason_bad[:10]}",
        ),
        Check(
            "hebrew_truncation_zero_hits",
            passed=len(truncation_hits) == 0,
            weight=2.0,
            evidence=f"{len(truncation_hits)} hits: {truncation_hits[:10]}",
        ),
        Check(
            "gershayim_no_ascii_quote",
            passed=len(gershayim_hits) == 0,
            weight=1.5,
            evidence=f"{len(gershayim_hits)} hits: {gershayim_hits[:10]}",
        ),
        Check(
            "key_facts_no_duplicates",
            passed=len(dup_bad) == 0,
            weight=1.0,
            evidence=f"{len(sample) - len(dup_bad)}/{n} clean; bad ids: {dup_bad[:10]}",
        ),
        Check(
            "entities_mentioned_nonempty_in_scope",
            passed=len(empty_entities) == 0,
            weight=1.5,
            evidence=(
                f"{len(in_scope) - len(empty_entities)}/{len(in_scope)} in-scope items have "
                f"entities or key_facts; empty ids: {empty_entities[:10]}"
                if in_scope
                else "no in-scope items in sample"
            ),
        ),
    ]
    return DomainScore(domain="D1", score_0_100=weighted_score(checks), checks=checks, n=n)
