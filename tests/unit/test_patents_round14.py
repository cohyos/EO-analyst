"""Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md) -- unit tests for the patent
assignee-misattribution fix: patent id 64 (CN112074705A) was stored with ``assignees =
['Anduril']`` purely because its Google-Patents search snippet named an unrelated FPGA component,
"fpga lcmxo3lft-2100e-5UWG49 CTR50(Lattice Semiconductor Corporation, USA)." -- "Lattice" is
Anduril's own *product* alias in ``config/watchlist.yaml``, not a company-name alias, and the
matched span is plainly part of a different, longer organisation's name ("Lattice Semiconductor
Corporation").

Covers:

1. ``eoa.pipeline.entity_normalize``: the ``product_aliases`` watchlist concept, the assignee-safe
   matcher ``find_watchlist_company_names_in_text`` (rule a: never a product alias; rule b: never a
   match embedded in a longer org name/citation), and that the general-purpose
   ``find_watchlist_aliases_in_text`` (topic/NER matching) is unaffected -- product aliases still
   count there.
2. ``eoa.patents.scan``: ``_assignee_candidates_in_text`` on the id-64 fixture; ``raw.assignee_
   source`` stamped ``"snippet"`` by ``_google_patents_records`` (rule c); ``_backfill_patent_
   fields``'s new ``overwrite_assignees`` path; ``_patents_missing_assignee``'s widened query;
   ``enrich_stored_patents_missing_assignee``'s ``entity_ids`` resync (rule d).

Mirrors tests/unit/test_patents_round6.py's own "stub every DB/network call" convention -- no live
DB/HTTP calls anywhere in this file.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.patents.models import PatentRecord
from eoa.patents.scan import (
    _assignee_candidates_in_text,
    _backfill_patent_fields,
    _google_patents_records,
    _patents_missing_assignee,
    enrich_stored_patents_missing_assignee,
)
from eoa.pipeline.entity_normalize import (
    find_watchlist_aliases_in_text,
    find_watchlist_company_names_in_text,
    resolve_canonical,
)

# --------------------------------------------------------------------------
# fixture -- patent id 64's real, live-verified raw record (title + search snippet), exactly as
# stored in `patents.raw` before this round's repair (scripts/repair_round14_patents.py).
# --------------------------------------------------------------------------

ID64_TITLE = "CN112074705A - Method and system for optical inertial tracking of moving object"
ID64_SNIPPET = (
    "Furthermore, the solution allows to reduce the requirements regarding the capacity of the "
    "storage device 107. In an illustrative embodiment of the present invention, the optical "
    "sensor data preprocessing device is implemented based on fpga lcmxo3lft-2100e-5UWG49 "
    "CTR50(Lattice Semiconductor Corporation, USA)."
)
ID64_TEXT = f"{ID64_TITLE}\n{ID64_SNIPPET}"


class TestProductAliasesWatchlistConcept:
    def test_anduril_product_aliases_are_a_subset_of_its_aliases(self):
        canonical = resolve_canonical("Anduril")
        assert canonical is not None
        assert canonical["kind"] == "company"
        assert set(canonical["product_aliases"]) == {"Lattice", "Anvil", "Roadrunner"}
        assert "Anduril Industries" in canonical["aliases"]
        assert "Anduril Industries" not in canonical["product_aliases"]


class TestFindWatchlistCompanyNamesInText:
    """Rule (a)+(b): the assignee-safe matcher used by eoa.patents.scan._assignee_candidates_in_text."""

    def test_id64_fixture_never_attributes_the_patent_to_anduril(self):
        assert find_watchlist_company_names_in_text(ID64_TEXT) == []

    def test_bare_product_alias_alone_matches_nothing(self):
        assert find_watchlist_company_names_in_text("The Lattice mesh network connects sensors.") == []

    def test_product_alias_co_occurring_with_the_real_company_name_still_only_yields_the_company(self):
        text = "Anduril uses its own Lattice mesh network for sensor fusion."
        assert find_watchlist_company_names_in_text(text) == ["Anduril"]

    def test_full_legal_name_alias_followed_by_a_corporate_suffix_is_not_rejected(self):
        # "Anduril Industries" is a genuine company-name alias (not a product alias); a following
        # "Inc." is its own ordinary corporate suffix, not evidence of a *different* company --
        # the longer-org-name guard is scoped to single-word/bare aliases only (see the module
        # docstring on _looks_like_longer_org_name_or_citation).
        text = "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril Industries Inc."
        assert find_watchlist_company_names_in_text(text) == ["Anduril"]

    def test_bare_single_word_alias_immediately_followed_by_a_corporate_suffix_is_rejected(self):
        # Rule (b): "Lattice Semiconductor Corporation" names a different company than Anduril's
        # own "Lattice" product.
        text = "The chip is built on a Lattice Semiconductor Corporation FPGA."
        assert find_watchlist_company_names_in_text(text) == []

    def test_bare_single_word_alias_preceded_by_a_capitalised_token_is_rejected(self):
        # BlueHalo's "Titan" is both a strict_aliases and product_aliases entry; a preceding
        # capitalised word reads as a fragment of a different, longer capitalised phrase (here,
        # the real US Army TITAN program awarded to Palantir/Anduril, not BlueHalo).
        text = "The US Army TITAN ground-station program was awarded to Palantir."
        result = find_watchlist_company_names_in_text(text)
        assert "BlueHalo" not in result
        assert "Palantir" in result  # a genuinely, plainly-named different company is unaffected

    def test_never_returns_a_program_org_or_country(self):
        # "Europe" is a curated org-table entry (kind="org"), never a real patent assignee --
        # "Sofradir" is a genuine company-name alias of Lynred, so that hit is correctly kept.
        text = "Sofradir EC, Inc. is a leading FPA manufacturer in Europe."
        assert find_watchlist_company_names_in_text(text) == ["Lynred"]

    def test_ordinary_company_mention_with_no_product_alias_involved_is_unaffected(self):
        assert find_watchlist_company_names_in_text("Raytheon Technologies filed the patent.") == ["RTX"]


class TestFindWatchlistAliasesInTextStillUsesProductAliasesForTopicMatching:
    """The general-purpose NER/topic matcher (eoa.pipeline.analyze etc.) is unaffected by round 14
    -- a product alias is still a legitimate topic/NER signal, just never an assignee signal.
    Uses Saab's "Giraffe" (a product_aliases entry that is *not* also a strict_aliases entry) so
    the co-occurrence gate round-2 already applies to strict aliases doesn't confound the test --
    "Lattice" itself is both product- and strict-flagged, so it was already excluded from bare,
    unaccompanied general-NER matches before this round for an unrelated reason (see
    TestFindWatchlistCompanyNamesInText's own fixtures for that case instead)."""

    def test_bare_product_alias_alone_still_resolves_the_company_for_general_ner(self):
        assert find_watchlist_aliases_in_text("The Giraffe radar system was deployed.") == ["Saab"]

    def test_but_the_assignee_safe_matcher_rejects_the_same_bare_product_alias(self):
        assert find_watchlist_company_names_in_text("The Giraffe radar system was deployed.") == []


class TestAssigneeCandidatesInText:
    """eoa.patents.scan._assignee_candidates_in_text -- the thin wrapper actually called from the
    Google-Patents search-fallback path."""

    def test_id64_fixture_yields_no_assignee(self):
        assert _assignee_candidates_in_text(ID64_TEXT) == []

    def test_genuine_company_mention_still_works(self):
        assert _assignee_candidates_in_text("Assigned to Anduril Industries Inc.") == ["Anduril"]


class _FakeHit:
    def __init__(self, url, title, snippet, engine="ddgs"):
        self.url = url
        self.title = title
        self.snippet = snippet
        self.engine = engine


class _FakeSearchResponse:
    def __init__(self, hits, error=None):
        self.hits = hits
        self.error = error


class TestGooglePatentsRecordsAssigneeSourceMarker:
    """Rule (c): a snippet-derived assignee is marked low-confidence via raw.assignee_source, so
    the enrichment pass knows to re-confirm (and, if it disagrees, overwrite) it later."""

    def test_id64_style_hit_gets_no_assignee_and_no_assignee_source_marker(self):
        hit = _FakeHit("https://patents.google.com/patent/CN112074705A/en", ID64_TITLE, ID64_SNIPPET)
        with patch("eoa.patents.scan.search", return_value=_FakeSearchResponse([hit])):
            records = _google_patents_records("optical inertial tracking")
        assert len(records) == 1
        assert records[0].assignees == []
        assert "assignee_source" not in records[0].raw

    def test_genuine_snippet_match_is_marked_low_confidence(self):
        hit = _FakeHit(
            "https://patents.google.com/patent/US10506436B1/en",
            "US10506436B1 - Counter drone system",
            "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril Industries Inc.",
        )
        with patch("eoa.patents.scan.search", return_value=_FakeSearchResponse([hit])):
            records = _google_patents_records("counter drone Anduril")
        assert len(records) == 1
        assert records[0].assignees == ["Anduril"]
        assert records[0].raw["assignee_source"] == "snippet"


class _FakeCursor:
    def __init__(self, fetchall_result=None, fetchone_result=None):
        self.calls: list[tuple[str, object]] = []
        self._fetchall_result = fetchall_result or []
        self._fetchone_result = fetchone_result

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestBackfillPatentFieldsOverwriteAssignees:
    def test_default_never_overwrites_uses_coalesce(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rec = PatentRecord(pub_number="US1", assignees=["ALT LLC"])
        _backfill_patent_fields("US1", rec)
        query, params = cur.calls[0]
        assert "assignees = COALESCE(NULLIF(assignees, ARRAY[]::text[]), %(assignees)s)" in query
        assert "assignee_source" not in query
        assert params["assignees"] == ["ALT LLC"]

    def test_overwrite_assignees_unconditionally_sets_and_stamps_detail_page_source(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rec = PatentRecord(pub_number="US1", assignees=["ALT LLC"])
        _backfill_patent_fields("US1", rec, overwrite_assignees=True)
        query, params = cur.calls[0]
        assert "assignees = %(assignees)s" in query
        assert "COALESCE(NULLIF(assignees" not in query
        assert "jsonb_build_object('assignee_source', 'detail_page')" in query
        assert params["assignees"] == ["ALT LLC"]

    def test_overwrite_assignees_with_no_assignees_on_the_record_never_touches_assignees_column(
        self, monkeypatch
    ):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rec = PatentRecord(pub_number="US1", cpc=["G01J5"])
        _backfill_patent_fields("US1", rec, overwrite_assignees=True)
        query, _params = cur.calls[0]
        assert "assignees" not in query
        assert "cpc" in query


class TestPatentsMissingAssigneeWidenedQuery:
    def test_query_also_matches_snippet_derived_low_confidence_rows(self, monkeypatch):
        cur = _FakeCursor(fetchall_result=[{"id": 64, "pub_number": "CN112074705A"}])
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rows = _patents_missing_assignee([64], limit=25)
        assert rows == [{"id": 64, "pub_number": "CN112074705A"}]
        query, params = cur.calls[0]
        assert "assignees IS NULL OR assignees = ARRAY[]::text[]" in query
        assert "raw->>'assignee_source' = 'snippet'" in query
        assert params == {"ids": [64], "limit": 25}


class TestEnrichStoredPatentsMissingAssigneeEntityIdsResync:
    """Rule (d): entity_ids must always be derived from the final assignees -- exercised here with
    the id-64 shape (a detail-page assignee that disagrees with -- and overwrites -- a previously
    stored snippet-derived one)."""

    def test_detail_page_assignee_overwrites_and_resyncs_entity_ids(self):
        targets = [{"id": 64, "pub_number": "CN112074705A"}]
        detail = {
            "assignees": ["ALT LLC"],
            "cpc": [],
            "priority_date": dt.date(2019, 5, 1),
            "filing_date": None,
            "publication_date": None,
        }
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value="<html/>"),
            patch("eoa.patents.scan._parse_google_patent_detail_html", return_value=detail),
            patch("eoa.patents.scan._backfill_patent_fields") as mocked_backfill,
            patch("eoa.patents.scan._entity_ids_for_assignees", return_value=[]) as mocked_resolve,
            patch("eoa.patents.scan._set_entity_ids") as mocked_set_ids,
            patch("eoa.patents.scan.time.sleep"),
        ):
            enriched = enrich_stored_patents_missing_assignee([64])
        assert enriched == 1
        mocked_backfill.assert_called_once()
        _, rec = mocked_backfill.call_args[0]
        assert rec.assignees == ["ALT LLC"]
        assert mocked_backfill.call_args.kwargs == {"overwrite_assignees": True}
        mocked_resolve.assert_called_once_with(["ALT LLC"])
        mocked_set_ids.assert_called_once_with("CN112074705A", [])

    def test_entity_ids_resync_failure_does_not_undo_the_assignee_correction(self):
        """A failure resolving/writing entity_ids is logged and skipped on its own -- it must
        never roll back the assignee/cpc correction that was already successfully applied
        (docs/CONVENTIONS.md rule 9)."""
        targets = [{"id": 64, "pub_number": "CN112074705A"}]
        detail = {
            "assignees": ["ALT LLC"],
            "cpc": [],
            "priority_date": None,
            "filing_date": None,
            "publication_date": None,
        }
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value="<html/>"),
            patch("eoa.patents.scan._parse_google_patent_detail_html", return_value=detail),
            patch("eoa.patents.scan._backfill_patent_fields"),
            patch("eoa.patents.scan._entity_ids_for_assignees", side_effect=RuntimeError("db down")),
            patch("eoa.patents.scan.time.sleep"),
        ):
            enriched = enrich_stored_patents_missing_assignee([64])
        assert enriched == 1
