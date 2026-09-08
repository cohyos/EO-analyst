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


@pytest.fixture(autouse=True)
def _no_live_patents_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Task 3 (2026-09-08): build_corpus now calls collect_patents_ops by default, which reaches
    eoa.patents.scan.search_records_for_applicant/upsert_records -- real, network/DB-touching
    functions. Every test in this file that does not explicitly exercise that path gets it
    stubbed to a no-op (empty gather) here, preserving this file's own "no real Postgres, no live
    network calls" convention; the dedicated collect_patents_ops/merge tests below override this
    per-test with recorded-shape fixtures."""
    import eoa.patents.scan as patents_scan

    monkeypatch.setattr(patents_scan, "search_records_for_applicant", lambda *a, **kw: [])


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


# --------------------------------------------------------------------------
# TENDERS-SAM (2026-09-08, docs/qa/content_review/TENDERS-SAM.md item 3): collect_tenders must
# only ever cite intake='accepted' rows -- same relevance-gate philosophy already applied to the
# daily/weekly report's own open-tenders table (eoa.tenders.report_section.collect_tenders).
# --------------------------------------------------------------------------


def test_collect_tenders_query_restricts_to_accepted_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        q = query.upper()
        if "FROM TENDERS" in q:
            captured["text"] = q
            return [
                {
                    "id": 99,
                    "title": "SPECTRO XR sensor tender",
                    "agency": "USAF",
                    "country": "US",
                    "deadline": None,
                    "status": "open",
                    "url": "https://example.com/t",
                    "summary_he": "",
                    "relevance_score": 0.9,
                    "intake": "accepted",
                }
            ]
        return []

    monkeypatch.setattr(dossier_corpus, "_fetchall", fake_fetchall)
    rows = dossier_corpus.collect_tenders(
        ["SPECTRO XR"], product_name="SPECTRO XR", vendor="Elbit Systems", aliases=["Spectro"]
    )
    assert len(rows) == 1
    assert "INTAKE = 'ACCEPTED'" in captured["text"]


def test_collect_tenders_returns_empty_when_only_candidate_rows_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 'candidate' (below-threshold or negative-keyword-demoted) tender must never surface in a
    dossier's corpus -- the SQL WHERE clause itself excludes it, so the fake DB below (which
    ignores the WHERE clause, matching the module's own dispatch-by-substring convention) simply
    returns nothing at all for FROM TENDERS to model that exclusion."""

    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        if "FROM TENDERS" in query.upper():
            return []  # the real WHERE intake='accepted' excludes the candidate row entirely
        return []

    monkeypatch.setattr(dossier_corpus, "_fetchall", fake_fetchall)
    rows = dossier_corpus.collect_tenders(
        ["SPECTRO XR"], product_name="SPECTRO XR", vendor="Elbit Systems", aliases=["Spectro"]
    )
    assert rows == []


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


# --------------------------------------------------------------------------
# PATENTS-OPS (2026-09-08, user finding): the SPECTRO XR dossier's `data->'patents'` was empty
# because collect_patents alone only reads the pre-scanned `patents` table -- collect_patents_ops
# adds a live EPO OPS/ODP (else keyless Google Patents fallback) query per product, upserted into
# the same table so it gets a real citable `id`. eoa.patents.scan.search_records_for_applicant /
# upsert_records are mocked throughout -- no live network/DB calls, per the task's own instruction.
# --------------------------------------------------------------------------


def _ops_record(pub_number: str, title: str = "OPS title") -> Any:
    from eoa.patents.models import PatentRecord

    return PatentRecord(
        pub_number=pub_number,
        title=title,
        abstract="An OPS-sourced abstract.",
        assignees=["Elbit Systems"],
        cpc=["G02B27"],
        source="epo_ops",
    )


class TestCollectPatentsOps:
    def test_no_vendor_or_alias_returns_empty_without_querying(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        called = []
        monkeypatch.setattr(
            patents_scan, "search_records_for_applicant", lambda *a, **kw: called.append(1) or []
        )
        out = dossier_corpus.collect_patents_ops("SPECTRO XR", vendor=None, aliases=[])
        assert out == []
        assert called == []

    def test_blank_product_name_returns_empty(self) -> None:
        assert dossier_corpus.collect_patents_ops("   ", vendor="Elbit Systems") == []

    def test_vendor_used_as_applicant_and_upserted_rows_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        captured: dict[str, Any] = {}

        def fake_search(applicant, keywords, *, limit=10):
            captured["applicant"] = applicant
            captured["keywords"] = list(keywords)
            return [_ops_record("US99999999A1")]

        def fake_upsert(records):
            return {"US99999999A1": 501}

        monkeypatch.setattr(patents_scan, "search_records_for_applicant", fake_search)
        monkeypatch.setattr(patents_scan, "upsert_records", fake_upsert)
        monkeypatch.setattr(
            dossier_corpus,
            "_fetchall",
            lambda query, params=None: [
                {
                    "id": 501,
                    "pub_number": "US99999999A1",
                    "title": "OPS title",
                    "abstract": "An OPS-sourced abstract.",
                    "assignees": ["Elbit Systems"],
                    "cpc": ["G02B27"],
                    "publication_date": dt.date(2026, 1, 1),
                    "filing_date": dt.date(2025, 1, 1),
                    "url": "https://patents.google.com/patent/US99999999A1/en",
                    "value_score": None,
                }
            ],
        )
        out = dossier_corpus.collect_patents_ops(
            "SPECTRO XR", vendor="Elbit Systems", aliases=["Spectro"], product_line="EO/IR payloads"
        )
        assert captured["applicant"] == "Elbit Systems"
        assert captured["keywords"] == ["SPECTRO XR", "EO/IR payloads"]
        assert len(out) == 1
        assert out[0]["id"] == 501
        assert out[0]["pub_number"] == "US99999999A1"

    def test_alias_used_as_applicant_when_no_vendor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        captured: dict[str, Any] = {}

        def fake_search(applicant, keywords, *, limit=10):
            captured["applicant"] = applicant
            return []

        monkeypatch.setattr(patents_scan, "search_records_for_applicant", fake_search)
        dossier_corpus.collect_patents_ops("SPECTRO XR", vendor=None, aliases=["Elbit"])
        assert captured["applicant"] == "Elbit"

    def test_empty_gather_returns_empty_without_upsert_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        upsert_called = []
        monkeypatch.setattr(patents_scan, "search_records_for_applicant", lambda *a, **kw: [])
        monkeypatch.setattr(
            patents_scan, "upsert_records", lambda records: upsert_called.append(1) or {}
        )
        out = dossier_corpus.collect_patents_ops("SPECTRO XR", vendor="Elbit Systems")
        assert out == []
        assert upsert_called == []

    def test_search_failure_is_caught_and_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        def boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(patents_scan, "search_records_for_applicant", boom)
        out = dossier_corpus.collect_patents_ops("SPECTRO XR", vendor="Elbit Systems")
        assert out == []

    def test_upsert_failure_is_caught_and_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        monkeypatch.setattr(
            patents_scan, "search_records_for_applicant", lambda *a, **kw: [_ops_record("US1A1")]
        )

        def boom(records):
            raise RuntimeError("db down")

        monkeypatch.setattr(patents_scan, "upsert_records", boom)
        out = dossier_corpus.collect_patents_ops("SPECTRO XR", vendor="Elbit Systems")
        assert out == []


class TestBuildCorpusPatentsOpsMerge:
    def test_ops_patents_merged_into_patents_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import eoa.patents.scan as patents_scan

        def fake_search(applicant, keywords, *, limit=10):
            return [_ops_record("US_NEW_OPS_1")]

        def fake_upsert(records):
            return {"US_NEW_OPS_1": 999}

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            q = query.upper()
            if "WHERE ID = ANY" in q:
                return [
                    {
                        "id": 999,
                        "pub_number": "US_NEW_OPS_1",
                        "title": "OPS title",
                        "abstract": "An OPS-sourced abstract.",
                        "assignees": ["Elbit Systems"],
                        "cpc": ["G02B27"],
                        "publication_date": None,
                        "filing_date": None,
                        "url": None,
                        "value_score": None,
                    }
                ]
            return _fake_fetchall(query, params)

        monkeypatch.setattr(patents_scan, "search_records_for_applicant", fake_search)
        monkeypatch.setattr(patents_scan, "upsert_records", fake_upsert)
        monkeypatch.setattr(dossier_corpus, "_fetchall", fake_fetchall)

        result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
        pub_numbers = {p["pub_number"] for p in result.patents}
        assert "US1234567B2" in pub_numbers  # the pre-existing DB-table match (fixture)
        assert "US_NEW_OPS_1" in pub_numbers  # the new live OPS-sourced row
        assert len(result.patents) == 2
        kinds_in_order = [r["kind"] for r in result.registry]
        assert kinds_in_order.count("patent") == 2

    def test_ops_patent_already_in_db_match_is_not_duplicated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The same pub_number surfacing from both collect_patents (DB match) and
        collect_patents_ops (live query) must appear exactly once in the merged list -- the
        DB-table match wins (it is already the richer, previously-reviewed row)."""
        import eoa.patents.scan as patents_scan

        def fake_search(applicant, keywords, *, limit=10):
            return [_ops_record("US1234567B2")]  # same pub_number as _FIXTURE_PATENTS

        def fake_upsert(records):
            return {"US1234567B2": 20}

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            if "WHERE ID = ANY" in query.upper():
                return [dict(_FIXTURE_PATENTS[0])]
            return _fake_fetchall(query, params)

        monkeypatch.setattr(patents_scan, "search_records_for_applicant", fake_search)
        monkeypatch.setattr(patents_scan, "upsert_records", fake_upsert)
        monkeypatch.setattr(dossier_corpus, "_fetchall", fake_fetchall)

        result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
        assert len(result.patents) == 1
        assert result.patents[0]["pub_number"] == "US1234567B2"

    def test_include_live_patents_ops_false_skips_the_live_query_entirely(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import eoa.patents.scan as patents_scan

        called = []
        monkeypatch.setattr(
            patents_scan, "search_records_for_applicant", lambda *a, **kw: called.append(1) or []
        )
        result = dossier_corpus.build_corpus(
            "SPECTRO XR", "Elbit Systems", ["Spectro"], include_live_patents_ops=False
        )
        assert called == []
        assert len(result.patents) == 1  # only the DB-table match from the fixture


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): patent relevance gate -- reproduces the live SPECTRO XR patents
# defect (8 generic "pod"/"target" hits, all with an empty assignee, kept anyway) and the fix's own
# rule: an empty-assignee row is always dropped; a row WITH an assignee is kept only when that
# assignee matches the vendor/an alias, or the product name itself appears in title/abstract.
# --------------------------------------------------------------------------


class TestPatentRelevanceGate:
    def test_empty_assignee_row_is_dropped_even_with_product_name_in_title(self) -> None:
        assert (
            dossier_corpus.patent_relevance_he(
                assignees=[],
                title="SPECTRO XR targeting pod mount",
                source_text="",
                product_name="SPECTRO XR",
                vendor="Elbit Systems",
                aliases=["Spectro"],
            )
            is None
        )

    def test_unrelated_assignee_and_no_product_name_is_dropped(self) -> None:
        assert (
            dossier_corpus.patent_relevance_he(
                assignees=["Some Other Company Ltd"],
                title="target positioning pod apparatus",
                source_text="a generic pod for target positioning",
                product_name="SPECTRO XR",
                vendor="Elbit Systems",
                aliases=["Spectro"],
            )
            is None
        )

    def test_matching_assignee_is_kept_and_states_the_grounded_link(self) -> None:
        relevance = dossier_corpus.patent_relevance_he(
            assignees=["Elbit Systems Ltd"],
            title="EO/IR payload apparatus",
            source_text="",
            product_name="SPECTRO XR",
            vendor="Elbit Systems",
            aliases=["Spectro"],
        )
        assert relevance is not None
        assert "Elbit Systems Ltd" in relevance

    def test_unrelated_assignee_but_product_name_in_title_is_kept(self) -> None:
        relevance = dossier_corpus.patent_relevance_he(
            assignees=["Unrelated Assignee Inc"],
            title="SPECTRO XR compact payload housing",
            source_text="",
            product_name="SPECTRO XR",
            vendor="Elbit Systems",
            aliases=["Spectro"],
        )
        assert relevance is not None
        assert "SPECTRO XR" in relevance

    def test_build_corpus_drops_empty_assignee_generic_keyword_patents(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reproduces the live defect end-to-end: the DB-table match returns generic "pod"/"target"
        hits with empty assignees alongside the one genuinely relevant, assignee-matched row -- only
        the relevant row survives build_corpus."""

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            q = query.upper()
            if "FROM PATENTS" in q:
                return [
                    dict(_FIXTURE_PATENTS[0]),  # assignee="Elbit Systems" -- relevant, kept
                    {
                        "id": 21,
                        "pub_number": "CN113804187A",
                        "title": "target positioning pod apparatus",
                        "abstract": "a generic pod for target positioning",
                        "assignees": [],
                        "cpc": [],
                        "publication_date": dt.date(2025, 1, 1),
                        "filing_date": None,
                        "url": None,
                        "value_score": None,
                    },
                ]
            return _fake_fetchall(query, params)

        monkeypatch.setattr(dossier_corpus, "_fetchall", fake_fetchall)
        result = dossier_corpus.build_corpus("SPECTRO XR", "Elbit Systems", ["Spectro"])
        pub_numbers = {p["pub_number"] for p in result.patents}
        assert pub_numbers == {"US1234567B2"}

    def test_cap_is_15_and_assignee_matches_ranked_first(self) -> None:
        """Unit-level: exercises ``_filter_and_cap_patents`` directly (the merge-time gate
        ``build_corpus`` applies) rather than the full DB pipeline -- ``collect_patents``'s own SQL
        query already requires the product name/alias to appear somewhere in the combined title/
        abstract/assignees text (its ``_search_terms`` clause), so a row whose ONLY grounding is an
        assignee match (with no product-name mention at all in its title) would never even be
        fetched from a real Postgres in the first place; testing the shared gate function in
        isolation avoids conflating that separate, pre-existing SQL-side filter with this one."""
        rank1_rows = [
            {
                "pub_number": f"US{i}NAME",
                "title": "SPECTRO XR variant",  # product-name-only match, rank 1
                "abstract": "",
                "assignees": ["Some Other Company"],
                "publication_date": None,
            }
            for i in range(10)
        ]
        rank0_rows = [
            {
                "pub_number": f"US{i}ASSIGNEE",
                "title": "SPECTRO XR family member",
                "abstract": "",
                "assignees": ["Elbit Systems"],  # assignee-matched, rank 0
                "publication_date": None,
            }
            for i in range(10)
        ]
        kept = dossier_corpus._filter_and_cap_patents(
            [*rank1_rows, *rank0_rows],
            product_name="SPECTRO XR",
            vendor="Elbit Systems",
            aliases=["Spectro"],
        )
        assert len(kept) == 15
        # All 10 assignee-matched rows must be present (ranked ahead of the product-name-only ones).
        kept_pub_numbers = {p["pub_number"] for p in kept}
        assert all(f"US{i}ASSIGNEE" in kept_pub_numbers for i in range(10))
