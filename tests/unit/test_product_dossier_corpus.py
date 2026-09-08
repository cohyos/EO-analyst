"""Corpus-stage tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.corpus`` -- ``slugify_product_key``, the registry-numbering assembly, and the
DB-touching collectors against fixture rows (monkeypatched ``_fetchall``/``_fetchone``, mirrors
``tests/unit/test_product_lines.py``'s own convention -- no real Postgres).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_corpus.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.dossier import corpus as dossier_corpus


def test_slugify_product_key_basic() -> None:
    assert dossier_corpus.slugify_product_key("Elbit Systems", "SPECTRO XR") == "elbit-systems-spectro-xr"


def test_slugify_product_key_no_vendor() -> None:
    assert dossier_corpus.slugify_product_key(None, "MOSP 5000") == "mosp-5000"


def test_slugify_product_key_all_hebrew_falls_back_to_hash() -> None:
    key = dossier_corpus.slugify_product_key(None, "ספקטרו")
    assert key.startswith("product-")


_FIXTURE_ITEMS = [
    {
        "id": 1,
        "title": "Elbit unveils SPECTRO XR payload",
        "url": "https://example.com/a",
        "source_name": "Example News",
        "published_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "summary_he": "אלביט חשפה את SPECTRO XR.",
        "so_what_he": "להערכתנו זהו מוצר חדש.",
        "key_facts": ['משקל 25 ק"ג'],
        "entities_mentioned": ["Elbit Systems"],
        "domain": "airborne_pods",
        "subdomain": "targeting_pods",
    }
]
_FIXTURE_EVENTS = [
    {
        "id": 10,
        "item_id": 1,
        "kind": "contract_award",
        "title": "SPECTRO XR contract",
        "date": dt.date(2026, 2, 1),
        "amount_usd": 5_000_000,
        "currency": "USD",
        "parties": ["Elbit Systems"],
        "customer": "US Air Force",
        "program": None,
        "summary_he": "חוזה עבור SPECTRO XR.",
        "item_title": "Elbit unveils SPECTRO XR payload",
        "item_url": "https://example.com/a",
        "source_name": "Example News",
        "published_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    }
]
_FIXTURE_PATENTS = [
    {
        "id": 20,
        "pub_number": "US1234567B2",
        "title": "EO/IR payload apparatus",
        "abstract": "A SPECTRO XR related payload apparatus.",
        "assignees": ["Elbit Systems"],
        "cpc": ["G01J5"],
        "publication_date": dt.date(2025, 6, 1),
        "filing_date": dt.date(2024, 1, 1),
        "url": "https://patents.example.com/1234567",
        "value_score": 70,
    }
]
_FIXTURE_TENDERS: list[dict[str, Any]] = []
_FIXTURE_FORECASTS: list[dict[str, Any]] = []


def _fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    q = query.upper()
    if "FROM ITEMS" in q:
        return list(_FIXTURE_ITEMS)
    if "FROM EVENTS" in q:
        return list(_FIXTURE_EVENTS)
    if "FROM PATENTS" in q:
        return list(_FIXTURE_PATENTS)
    if "FROM TENDER_FORECASTS" in q:
        return list(_FIXTURE_FORECASTS)
    if "FROM TENDERS" in q:
        return list(_FIXTURE_TENDERS)
    if "FROM ENTITIES" in q:
        return []
    if "FROM GRAPH_EDGES" in q:
        return []
    return []


def _fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    if "FROM PRODUCT_DOSSIERS" in query.upper():
        return None
    rows = _fake_fetchall(query, params)
    return rows[0] if rows else None


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dossier_corpus, "_fetchall", _fake_fetchall)
    monkeypatch.setattr(dossier_corpus, "_fetchone", _fake_fetchone)


def test_build_corpus_gathers_items_events_patents() -> None:
    result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro", "ספקטרו"])
    assert result.product_key == "elbit-systems-spectro-xr"
    assert len(result.items) == 1
    assert len(result.events) == 1
    assert len(result.patents) == 1
    assert result.tenders == []
    assert result.forecasts == []


def test_build_corpus_registry_numbering_order() -> None:
    result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
    kinds_in_order = [r["kind"] for r in result.registry]
    # items first, then events, then patents (tenders/forecasts empty in this fixture) -- matches
    # eoa.patents.survey's own flat-numbering convention (registry rows numbered in collection
    # order, never re-sorted).
    assert kinds_in_order == ["item", "event", "patent"]
    ns = [r["n"] for r in result.registry]
    assert ns == [1, 2, 3]


def test_build_corpus_respects_max_sources_cap() -> None:
    result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"], max_sources=2)
    assert len(result.registry) == 2


def test_build_corpus_no_terms_returns_empty() -> None:
    result = dossier_corpus.build_corpus("", None, [])
    assert result.items == []
    assert result.registry == []


def test_corpus_summary_he_mentions_product_and_vendor() -> None:
    result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
    summary = result.summary_he()
    assert "SPECTRO XR" in summary
    assert "Elbit Systems" in summary


def test_corpus_next_n_increments() -> None:
    result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
    assert result.next_n == 4


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 1): alias-matching precision -- a short alias ("Spectro") must not
# substring-match an unrelated word ("spectroscopy"); the live corpus dry run pulled in "Infrared
# spectroscopy - Wikipedia" this way (docs/qa/content_review/PD-backend.md).
# --------------------------------------------------------------------------


def test_word_present_requires_whole_word() -> None:
    assert dossier_corpus._word_present("Infrared spectroscopy overview", "Spectro") is False
    assert dossier_corpus._word_present("Elbit unveils SPECTRO XR payload", "SPECTRO XR") is True
    assert dossier_corpus._word_present("the Spectro pod is new", "Spectro") is True


def test_short_alias_alone_does_not_match_without_vendor() -> None:
    assert (
        dossier_corpus._matches_product_precisely(
            "some Spectro thing", "SPECTRO XR", None, ["Spectro"]
        )
        is False
    )


def test_short_alias_matches_together_with_vendor() -> None:
    assert (
        dossier_corpus._matches_product_precisely(
            "Elbit Systems unveils its new Spectro pod", "SPECTRO XR", "Elbit Systems", ["Spectro"]
        )
        is True
    )


def test_long_alias_matches_alone() -> None:
    assert (
        dossier_corpus._matches_product_precisely(
            "the SPECTRO XR payload was shown", "SPECTRO XR", None, ["SPECTRO XR"]
        )
        is True
    )


def test_general_reference_domain_requires_full_product_name() -> None:
    assert dossier_corpus._is_general_reference_domain("en.wikipedia.org") is True
    assert dossier_corpus._is_general_reference_domain("example.com") is False


def test_collect_items_filters_out_wikipedia_spectroscopy_false_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact live false positive: a short alias ("Spectro") substring-matching
    "Infrared spectroscopy - Wikipedia" via the broad SQL ILIKE candidate query -- the precise
    Python-side filter must drop it (no whole-word match, and it's a general-reference domain with
    no full product name present either)."""
    rows = [
        {
            "id": 1,
            "title": "Elbit unveils SPECTRO XR payload",
            "summary_he": "",
            "so_what_he": "",
            "domain": "airborne_pods",
        },
        {
            "id": 2,
            "title": "Infrared spectroscopy - Wikipedia",
            "summary_he": "",
            "so_what_he": "",
            "domain": "wikipedia.org",
        },
    ]
    monkeypatch.setattr(dossier_corpus, "_fetchall", lambda query, params=None: list(rows))
    out = dossier_corpus.collect_items(
        ["SPECTRO XR", "Spectro"], product_name="SPECTRO XR", vendor="Elbit Systems", aliases=["Spectro"]
    )
    assert [r["id"] for r in out] == [1]
