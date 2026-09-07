"""Round 6 QA-loop repair (R6-patents, docs/qa/loop/round_5_judge.md D8) -- unit tests for:

1. Assignee/CPC/priority-date enrichment of survey patents that still carry no assignee at all
   (the keyless Google-Patents-search fallback only ever returns title+snippet): fetching each
   patent's own Google Patents *detail* page (not a search result) and parsing its structured
   ``<meta>``/``<span>``/``<time>`` markup -- ``eoa.patents.scan._parse_google_patent_detail_html``,
   ``_normalize_cpc_codes``, ``_fetch_patent_detail_html``, ``_patents_missing_assignee``,
   ``enrich_stored_patents_missing_assignee``, and ``_backfill_patent_fields``'s new
   ``priority_date`` column.
2. The "לא מסווג" ("unclassified") cluster-label fix: a TF-IDF sub-cluster that has real top terms
   must never be labelled with the literal "לא מסווג"/"ללא סיווג" substring anywhere (round 5's own
   test hard-coded the *old* "לא מסווג: <terms>" shape this round explicitly replaces -- see
   docs/qa/loop/round_6_fixes.md's own "### R6-patents status" section for that conflict) --
   ``eoa.patents.cluster._unclassified_label_he`` / ``cluster_patents``.
3. ``eoa.patents.survey.methodology_box_lines_he``'s new ``term_derived_cluster_count`` line.

Mirrors tests/unit/test_patents_scan.py's/test_patents_round5.py's own "stub every DB/network
call" convention -- no live DB/Ollama/HTTP calls anywhere in this file.
"""

from __future__ import annotations

import datetime as dt
from typing import ClassVar
from unittest.mock import patch

from eoa.patents.cluster import (
    UNCLASSIFIED_KEY,
    UNCLASSIFIED_LABEL_HE,
    _unclassified_label_he,
    cluster_patents,
)
from eoa.patents.models import PatentRecord
from eoa.patents.scan import (
    _backfill_patent_fields,
    _fetch_patent_detail_html,
    _normalize_cpc_codes,
    _parse_google_patent_detail_html,
    _patents_missing_assignee,
    enrich_stored_patents_missing_assignee,
)
from eoa.patents.survey import methodology_box_lines_he

# --------------------------------------------------------------------------
# fixtures -- a trimmed real Google Patents detail-page shape (confirmed live 2026-09-06/07
# against several real patents.google.com/patent/<pub>/en pages, see agent/eoa/patents/scan.py's
# own "Round 6 D8 finding 1" module comment)
# --------------------------------------------------------------------------

_SAMPLE_DETAIL_HTML = """
<meta name="DC.contributor" content="Dwaine A. Parker" scheme="inventor">
<meta name="DC.contributor" content="Xidrone Systems Inc" scheme="assignee">
<section>
<h2>Classifications</h2>
<ul>
<li itemprop="classifications" itemscope repeat>
<span itemprop="Code">G</span>&mdash;<span itemprop="Description">PHYSICS</span>
<meta itemprop="IsCPC" content="true">
</li>
<li itemprop="classifications" itemscope repeat>
<span itemprop="Code">G01S13/00</span>&mdash;<span itemprop="Description">Radar systems</span>
<meta itemprop="IsCPC" content="true">
</li>
<li itemprop="classifications" itemscope repeat>
<span itemprop="Code">G01S13/02</span>&mdash;<span itemprop="Description">detail</span>
<meta itemprop="IsCPC" content="true">
</li>
<li itemprop="classifications" itemscope repeat>
<span itemprop="Code">A01B1/00</span>&mdash;<span itemprop="Description">legacy US class</span>
<meta itemprop="IsCPC" content="false">
</li>
</ul>
</section>
<dl>
<dt>Priority date</dt>
<dd><time itemprop="priorityDate" datetime="2014-12-19">2014-12-19</time></dd>
<dt>Filing date</dt>
<dd><time itemprop="filingDate" datetime="2019-03-22">2019-03-22</time></dd>
<dt>Publication date</dt>
<dd><time itemprop="publicationDate" datetime="2020-10-06">2020-10-06</time></dd>
</dl>
"""

_SAMPLE_DETAIL_HTML_NO_MATCHES = "<html><body><p>Nothing structured here.</p></body></html>"

_SAMPLE_DETAIL_HTML_TWO_ASSIGNEES = """
<meta name="DC.contributor" content="Alpha Systems Inc" scheme="assignee">
<meta name="DC.contributor" content="Beta Defense Ltd" scheme="assignee">
"""


class TestNormalizeCpcCodes:
    def test_dedupes_and_strips_subgroup_suffix(self):
        assert _normalize_cpc_codes(["G01S13/00", "G01S13/02", "G01S3/00"]) == ["G01S13", "G01S3"]

    def test_drops_bare_class_and_subclass_codes_without_slash(self):
        assert _normalize_cpc_codes(["G", "G01", "G01S"]) == []

    def test_empty_input_returns_empty(self):
        assert _normalize_cpc_codes([]) == []

    def test_preserves_first_seen_order(self):
        assert _normalize_cpc_codes(["H04N23/00", "G01J5/00", "H04N23/60"]) == ["H04N23", "G01J5"]


class TestParseGooglePatentDetailHtml:
    def test_extracts_assignee_not_inventor(self):
        detail = _parse_google_patent_detail_html(_SAMPLE_DETAIL_HTML)
        assert detail["assignees"] == ["Xidrone Systems Inc"]
        assert "Dwaine A. Parker" not in detail["assignees"]

    def test_extracts_normalized_cpc_only_for_iscpc_true(self):
        detail = _parse_google_patent_detail_html(_SAMPLE_DETAIL_HTML)
        assert detail["cpc"] == ["G01S13"]
        assert "A01B1" not in detail["cpc"]

    def test_extracts_all_three_dates(self):
        detail = _parse_google_patent_detail_html(_SAMPLE_DETAIL_HTML)
        assert detail["priority_date"] == dt.date(2014, 12, 19)
        assert detail["filing_date"] == dt.date(2019, 3, 22)
        assert detail["publication_date"] == dt.date(2020, 10, 6)

    def test_missing_markup_yields_empty_not_guessed(self):
        detail = _parse_google_patent_detail_html(_SAMPLE_DETAIL_HTML_NO_MATCHES)
        assert detail == {
            "assignees": [],
            "cpc": [],
            "priority_date": None,
            "filing_date": None,
            "publication_date": None,
            # Round 14 (docs/qa/content_review/CR-patents.md text-grounding rule): the real
            # detail-page abstract, None when the page carries no DC.description meta tag either.
            "abstract": None,
        }

    def test_multiple_assignees_all_captured(self):
        detail = _parse_google_patent_detail_html(_SAMPLE_DETAIL_HTML_TWO_ASSIGNEES)
        assert detail["assignees"] == ["Alpha Systems Inc", "Beta Defense Ltd"]


class TestFetchPatentDetailHtml:
    def test_returns_text_on_success(self):
        with patch("eoa.patents.scan.fetch_raw_remote", return_value={"text": "<html>ok</html>"}) as mocked:
            html = _fetch_patent_detail_html("US1234567B2")
        assert html == "<html>ok</html>"
        (url,), _kwargs = mocked.call_args
        assert url == "https://patents.google.com/patent/US1234567B2/en"

    def test_returns_none_on_fetch_exception(self):
        with patch("eoa.patents.scan.fetch_raw_remote", side_effect=RuntimeError("boom")):
            assert _fetch_patent_detail_html("US1") is None

    def test_returns_none_when_text_missing(self):
        with patch("eoa.patents.scan.fetch_raw_remote", return_value={"status": 200}):
            assert _fetch_patent_detail_html("US1") is None

    def test_returns_none_when_text_empty_string(self):
        with patch("eoa.patents.scan.fetch_raw_remote", return_value={"text": ""}):
            assert _fetch_patent_detail_html("US1") is None


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


class TestPatentsMissingAssignee:
    def test_empty_patent_ids_never_touches_db(self, monkeypatch):
        def _boom():
            raise AssertionError("connection() must not be called for an empty id list")

        monkeypatch.setattr("eoa.patents.scan.connection", _boom)
        assert _patents_missing_assignee([], limit=25) == []

    def test_passes_ids_and_limit_through_to_query(self, monkeypatch):
        cur = _FakeCursor(fetchall_result=[{"id": 1, "pub_number": "US1"}])
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rows = _patents_missing_assignee([1, 2, 3], limit=25)
        assert rows == [{"id": 1, "pub_number": "US1"}]
        query, params = cur.calls[0]
        assert "assignees IS NULL OR assignees = ARRAY[]::text[]" in query
        assert params == {"ids": [1, 2, 3], "limit": 25}


class TestBackfillPatentFieldsPriorityDate:
    def test_priority_date_alone_triggers_a_scalar_coalesce_update(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rec = PatentRecord(pub_number="US1", priority_date=dt.date(2020, 1, 1))
        _backfill_patent_fields("US1", rec)
        assert len(cur.calls) == 1
        query, params = cur.calls[0]
        assert "priority_date = COALESCE(priority_date, %(priority_date)s)" in query
        assert "NULLIF" not in query.split("priority_date = COALESCE")[1].split(",")[0]
        assert params["priority_date"] == dt.date(2020, 1, 1)

    def test_no_fields_at_all_never_touches_connection(self, monkeypatch):
        def _boom():
            raise AssertionError("connection() must not be called when nothing to backfill")

        monkeypatch.setattr("eoa.patents.scan.connection", _boom)
        _backfill_patent_fields("US1", PatentRecord(pub_number="US1"))

    def test_assignees_cpc_and_priority_date_all_combine_into_one_update(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.scan.connection", lambda: _FakeConnection(cur))
        rec = PatentRecord(
            pub_number="US1", assignees=["Anduril"], cpc=["G01J5"], priority_date=dt.date(2019, 5, 1)
        )
        _backfill_patent_fields("US1", rec)
        assert len(cur.calls) == 1
        query, params = cur.calls[0]
        assert "assignees" in query and "cpc" in query and "priority_date" in query
        assert params["assignees"] == ["Anduril"]
        assert params["cpc"] == ["G01J5"]
        assert params["priority_date"] == dt.date(2019, 5, 1)


class TestEnrichStoredPatentsMissingAssignee:
    def test_enriches_target_and_writes_via_backfill(self):
        targets = [{"id": 1, "pub_number": "US1"}]
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value="<html/>"),
            patch(
                "eoa.patents.scan._parse_google_patent_detail_html",
                return_value={
                    "assignees": ["Xidrone Systems Inc"],
                    "cpc": ["G01S13"],
                    "priority_date": dt.date(2014, 12, 19),
                    "filing_date": None,
                    "publication_date": None,
                },
            ),
            patch("eoa.patents.scan._backfill_patent_fields") as mocked_backfill,
            patch("eoa.patents.scan.time.sleep") as mocked_sleep,
            # Round 14 (docs/qa/content_review/CR-patents.md rule d): a detail-page assignee also
            # re-syncs entity_ids -- mocked out here (both the resolution and the write) so this
            # test never attempts a real DB/entity lookup.
            patch("eoa.patents.scan._entity_ids_for_assignees", return_value=[42]) as mocked_resolve,
            patch("eoa.patents.scan._set_entity_ids") as mocked_set_ids,
        ):
            enriched = enrich_stored_patents_missing_assignee([1], cap=25)
        assert enriched == 1
        mocked_backfill.assert_called_once()
        pub_number, rec = mocked_backfill.call_args[0]
        assert pub_number == "US1"
        assert rec.assignees == ["Xidrone Systems Inc"]
        assert rec.cpc == ["G01S13"]
        assert mocked_backfill.call_args.kwargs == {"overwrite_assignees": True}
        mocked_resolve.assert_called_once_with(["Xidrone Systems Inc"])
        mocked_set_ids.assert_called_once_with("US1", [42])
        mocked_sleep.assert_not_called()  # a single target never sleeps

    def test_sleeps_between_targets_but_not_before_the_first(self):
        targets = [{"id": 1, "pub_number": "US1"}, {"id": 2, "pub_number": "US2"}]
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value=None),
            patch("eoa.patents.scan.time.sleep") as mocked_sleep,
        ):
            enriched = enrich_stored_patents_missing_assignee([1, 2], cap=25, sleep_s=2.0)
        assert enriched == 0
        mocked_sleep.assert_called_once_with(2.0)

    def test_skips_target_when_fetch_returns_no_html(self):
        targets = [{"id": 1, "pub_number": "US1"}]
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value=None),
            patch("eoa.patents.scan._backfill_patent_fields") as mocked_backfill,
        ):
            enriched = enrich_stored_patents_missing_assignee([1])
        assert enriched == 0
        mocked_backfill.assert_not_called()

    def test_skips_target_when_parse_finds_nothing_useful(self):
        targets = [{"id": 1, "pub_number": "US1"}]
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value="<html/>"),
            patch(
                "eoa.patents.scan._parse_google_patent_detail_html",
                return_value={
                    "assignees": [],
                    "cpc": [],
                    "priority_date": None,
                    "filing_date": None,
                    "publication_date": None,
                },
            ),
            patch("eoa.patents.scan._backfill_patent_fields") as mocked_backfill,
        ):
            enriched = enrich_stored_patents_missing_assignee([1])
        assert enriched == 0
        mocked_backfill.assert_not_called()

    def test_one_backfill_failure_does_not_abort_the_rest_of_the_batch(self):
        targets = [{"id": 1, "pub_number": "US1"}, {"id": 2, "pub_number": "US2"}]
        detail = {
            "assignees": ["Some Co"],
            "cpc": [],
            "priority_date": None,
            "filing_date": None,
            "publication_date": None,
        }
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=targets),
            patch("eoa.patents.scan._fetch_patent_detail_html", return_value="<html/>"),
            patch("eoa.patents.scan._parse_google_patent_detail_html", return_value=detail),
            patch("eoa.patents.scan._backfill_patent_fields", side_effect=[RuntimeError("db down"), None]),
            patch("eoa.patents.scan.time.sleep"),
            patch("eoa.patents.scan._entity_ids_for_assignees", return_value=[]),
            patch("eoa.patents.scan._set_entity_ids"),
        ):
            enriched = enrich_stored_patents_missing_assignee([1, 2])
        assert enriched == 1  # only the second target's backfill succeeded

    def test_passes_cap_through_to_patents_missing_assignee(self):
        with (
            patch("eoa.patents.scan._patents_missing_assignee", return_value=[]) as mocked_lookup,
        ):
            enrich_stored_patents_missing_assignee([1, 2, 3], cap=7)
        mocked_lookup.assert_called_once_with([1, 2, 3], 7)


class TestUnclassifiedLabelHe:
    def test_no_terms_falls_back_to_bare_unclassified_label(self):
        assert _unclassified_label_he([]) == UNCLASSIFIED_LABEL_HE

    def test_with_terms_never_contains_the_unclassified_substring(self):
        label = _unclassified_label_he(["sensor", "drone", "counter"])
        assert "לא מסווג" not in label
        assert "ללא סיווג" not in label
        assert label == "אשכול נושאי: sensor / drone / counter"


class TestClusterPatentsUnclassifiedLabellingRound6:
    """Round 6 D8 finding 2: replaces round 5's own (now-outdated) assumption that a term-derived
    sub-cluster label starts with "לא מסווג:" -- see this file's own module docstring for the
    round_5 test this necessarily supersedes."""

    _THERMAL_AND_BATTERY_ROWS: ClassVar[list[dict]] = [
        {
            "n": 1,
            "id": 1,
            "cpc": [],
            "assignees": [],
            "title": "thermal infrared sensor array pixel",
            "abstract": "infrared detector array pixel readout",
            "publication_date": None,
        },
        {
            "n": 2,
            "id": 2,
            "cpc": [],
            "assignees": [],
            "title": "thermal infrared detector pixel array",
            "abstract": "infrared sensor readout pixel array",
            "publication_date": None,
        },
        {
            "n": 3,
            "id": 3,
            "cpc": [],
            "assignees": [],
            "title": "battery charging power circuit",
            "abstract": "charging circuit management power",
            "publication_date": None,
        },
        {
            "n": 4,
            "id": 4,
            "cpc": [],
            "assignees": [],
            "title": "battery power charging circuit management",
            "abstract": "power circuit charging management",
            "publication_date": None,
        },
    ]

    def test_multiple_subclusters_never_carry_the_unclassified_substring(self):
        clusters = cluster_patents(self._THERMAL_AND_BATTERY_ROWS)
        unclassified = [c for c in clusters if c.key.startswith(UNCLASSIFIED_KEY)]
        assert len(unclassified) >= 2
        assert all("לא מסווג" not in c.label_he for c in unclassified)
        assert all(c.label_he.startswith("אשכול נושאי:") for c in unclassified)

    def test_single_leftover_subcluster_with_real_terms_also_gets_descriptive_label(self):
        rows = self._THERMAL_AND_BATTERY_ROWS[:2]  # both thermal -- one sub-cluster, real terms
        clusters = cluster_patents(rows)
        unclassified = [c for c in clusters if c.key.startswith(UNCLASSIFIED_KEY)]
        assert len(unclassified) == 1
        assert unclassified[0].label_he != UNCLASSIFIED_LABEL_HE
        assert "לא מסווג" not in unclassified[0].label_he

    def test_truly_nameless_single_row_still_falls_back_to_bare_label(self):
        """Regression guard (round 5's test_unclassified_bucket_for_no_signal): a row with no
        title/abstract tokens at all has nothing to derive a term-label from, so it keeps the bare
        :data:`UNCLASSIFIED_LABEL_HE` -- the literal string is fine here since there are no real
        terms being suppressed, and the deterministic checker's own "not applicable" branch
        (no populated patents table) covers a lone unlabelled patent regardless."""
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": [],
                "assignees": [],
                "title": "",
                "abstract": "",
                "publication_date": None,
            }
        ]
        clusters = cluster_patents(rows)
        assert clusters[0].label_he == UNCLASSIFIED_LABEL_HE


class TestMethodologyBoxTermDerivedClusterLine:
    def _base_kwargs(self, **overrides):
        base = dict(
            topic="thermal imaging seeker",
            sources_scanned_he="Google Patents (חיפוש חסר-מפתחות)",
            date_range=(dt.date(2018, 1, 1), dt.date(2024, 1, 1)),
            n_fresh=12,
            n_stored=5,
            assignee_with=3,
            assignee_total=17,
            cpc_with=0,
            cpc_total=17,
            caveat_he=None,
        )
        base.update(overrides)
        return base

    def test_no_line_when_zero_term_derived_clusters_default(self):
        lines = methodology_box_lines_he(**self._base_kwargs())
        assert not any("מבוססי-מונחים" in line for line in lines)

    def test_no_line_when_zero_term_derived_clusters_explicit(self):
        lines = methodology_box_lines_he(**self._base_kwargs(), term_derived_cluster_count=0)
        assert not any("מבוססי-מונחים" in line for line in lines)

    def test_line_appended_with_count_when_present(self):
        lines = methodology_box_lines_he(**self._base_kwargs(), term_derived_cluster_count=2)
        matches = [line for line in lines if "מבוססי-מונחים" in line]
        assert len(matches) == 1
        assert matches[0].startswith("2 ")

    def test_term_derived_line_appended_after_the_coverage_caveat(self):
        lines = methodology_box_lines_he(
            **self._base_kwargs(caveat_he="caveat text here", assignee_with=0, assignee_total=10),
            term_derived_cluster_count=1,
        )
        assert lines[-2] == "caveat text here"
        assert "מבוססי-מונחים" in lines[-1]
