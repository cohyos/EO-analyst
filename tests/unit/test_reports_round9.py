"""Unit tests for the R9-reports package (round-9 QA loop, closing round-8 judge worst-list items
#5/#6/#8/#9/#10 -- docs/qa/loop/round_8_judge.md).

Every DB-touching function is monkeypatched at the module level (no live DB, no Ollama), mirroring
the conventions already used by tests/unit/test_reports_round8.py and
tests/unit/test_backfill_source_last_fetched.py (the latter for the ``scripts/repair_round9.py``
module-from-file import).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round9.py -q``
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from eoa.patents import survey
from eoa.report import indicators
from eoa.report import product_line as pl
from eoa.tenders import scan as tenders_scan

UTC = dt.UTC

_REPAIR_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_round9.py"
_spec = importlib.util.spec_from_file_location("repair_round9", _REPAIR_SCRIPT_PATH)
repair = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(repair)


# --------------------------------------------------------------------------
# Finding #1 -- indicator-watchlist evidence column, Hebrew-only indicators (D6 #5).
# --------------------------------------------------------------------------


class TestHebrewIndicatorMatching:
    def test_two_hebrew_terms_match_returns_true(self) -> None:
        text = "מערכת הגילוי החדשה של טייוואן תיכנס לשירות בקרוב"
        item = {
            "title": "מערכת גילוי חדשה הוצגה בטייוואן",
            "summary_he": "",
            "so_what_he": "",
        }
        assert indicators._item_matches_indicator(text, item) is True

    def test_single_hebrew_term_match_returns_false(self) -> None:
        # Same indicator, but the item shares only one of its (quality-filtered) Hebrew key terms
        # ("טייוואן") -- a lone term is too weak alone (keeps the same precision bar a lone Latin
        # term already met before this fix).
        text = "מערכת הגילוי החדשה של טייוואן תיכנס לשירות בקרוב"
        item = {"title": "טייוואן מארחת פסטיבל תרבות בינלאומי", "summary_he": "", "so_what_he": ""}
        assert indicators._item_matches_indicator(text, item) is False

    def test_generic_domain_words_alone_do_not_count(self) -> None:
        # "מערכת"/"ישראל"/"קרוב" are domain-ubiquitous (see :data:`indicators._MATCH_GENERIC_HE`'s
        # own docstring note) -- an item sharing only these (none of the indicator's own real
        # distinctive terms -- "גילוי"/"טייוואן"/"תיכנס"/"שירות") must not count as corroboration.
        text = "מערכת הגילוי החדשה של טייוואן תיכנס לשירות בקרוב"
        item = {
            "title": "מערכת ביטחונית חדשה תוצג בתערוכה בישראל בקרוב מאוד",
            "summary_he": "",
            "so_what_he": "",
        }
        assert indicators._item_matches_indicator(text, item) is False

    def test_no_overlap_returns_false(self) -> None:
        text = "מערכת הגילוי החדשה של טייוואן תיכנס לשירות בקרוב"
        item = {"title": "דוח כלכלי כללי", "summary_he": "", "so_what_he": ""}
        assert indicators._item_matches_indicator(text, item) is False

    def test_latin_term_alone_still_sufficient(self) -> None:
        # Regression guard: a Latin key term match must stay sufficient on its own (unchanged
        # behavior), even though the same indicator now also yields Hebrew content tokens the item
        # doesn't share.
        text = "להערכתנו מערכת ה-DROIC תבשיל בקרוב"
        item = {"title": "DROIC sensor unveiled", "summary_he": "", "so_what_he": ""}
        assert indicators._item_matches_indicator(text, item) is True

    def test_bare_calendar_year_does_not_count_as_a_term(self) -> None:
        # Live verification (docs/qa/loop/round_9_fixes.md) found a bare "2026"-style year alone
        # contributing to false-positive matches across unrelated items (a year mention is
        # near-universal, not distinctive). Here the item shares the year AND one real surviving
        # term ("אספקת") with the indicator -- the year must not count toward the 2-term bar, so
        # one real term plus a shared year is still short of a match.
        text = "אספקת המערכת הראשונה צפויה בשנת 2026."
        item = {
            "title": "חברה זרה זכתה בחוזה אספקת ציוד לכנס טכנולוגי גדול שייערך בשנת 2026",
            "summary_he": "",
            "so_what_he": "",
        }
        assert indicators._item_matches_indicator(text, item) is False

    def test_public_extract_key_terms_is_unaffected(self) -> None:
        # The public, Latin-only extractor's contract (and its own existing tests in
        # tests/unit/test_report_deltas_round5.py) must be untouched by this fix.
        assert indicators.extract_key_terms("להערכתנו יחול שיפור ניכר בשוק") == set()

    def test_proper_noun_phrase_widens_hebrew_term_match(self, monkeypatch) -> None:
        monkeypatch.setattr(
            indicators, "_taxonomy_subdomain_labels_he", lambda: frozenset({"פודי ציון מטרות"})
        )
        monkeypatch.setattr(indicators, "_watchlist_hebrew_names", lambda: frozenset({"אלביט מערכות"}))
        text = "אלביט מערכות תשיק פודי ציון מטרות חדשים"
        item = {
            "title": "אלביט מערכות חשפה פודי ציון מטרות בתערוכה",
            "summary_he": "",
            "so_what_he": "",
        }
        assert indicators._item_matches_indicator(text, item) is True

    def test_taxonomy_subdomain_labels_he_strips_parenthetical(self, monkeypatch) -> None:
        fake_settings = type(
            "S",
            (),
            {"taxonomy": {"domains": {"d1": {"sub": {"k1": "פודי ציון מטרות (Targeting Pods)", "k2": ""}}}}},
        )()
        monkeypatch.setattr(indicators, "settings", lambda: fake_settings)
        indicators._taxonomy_subdomain_labels_he.cache_clear()
        try:
            assert indicators._taxonomy_subdomain_labels_he() == frozenset({"פודי ציון מטרות"})
        finally:
            indicators._taxonomy_subdomain_labels_he.cache_clear()

    def test_watchlist_hebrew_names_filters_non_hebrew(self, monkeypatch) -> None:
        fake_settings = type(
            "S",
            (),
            {
                "watchlist": {
                    "companies": [
                        {"name": "RTX", "aliases": ["Raytheon"]},
                        {"name": "Elbit", "aliases": ["אלביט", "אלביט מערכות"], "strict_aliases": []},
                    ]
                }
            },
        )()
        monkeypatch.setattr(indicators, "settings", lambda: fake_settings)
        indicators._watchlist_hebrew_names.cache_clear()
        try:
            names = indicators._watchlist_hebrew_names()
            assert names == frozenset({"אלביט", "אלביט מערכות"})
        finally:
            indicators._watchlist_hebrew_names.cache_clear()


# --------------------------------------------------------------------------
# Finding #2 -- product-line report event-kind labels (D7 #8).
# --------------------------------------------------------------------------


class TestProductLineEventKindLabels:
    def _event(self, kind: str | None) -> dict:
        return {
            "n": 1,
            "kind": kind,
            "title": "Deal X",
            "program": None,
            "customer": "Client Y",
            "date": dt.date(2026, 1, 1),
            "amount_usd": None,
            "currency": None,
        }

    def test_format_events_block_translates_known_kind(self) -> None:
        block = pl.format_events_block([self._event("contract_award")])
        assert "זכייה בחוזה" in block
        assert "contract_award" not in block

    def test_events_table_translates_known_kind(self) -> None:
        table = pl.events_table([self._event("deployment")])
        assert table is not None
        assert table["rows"][0][0] == "פריסה"

    def test_unknown_kind_falls_back_to_other(self) -> None:
        assert pl._event_kind_label("some_new_kind_not_in_map") == "אחר"

    def test_missing_kind_falls_back_to_other(self) -> None:
        assert pl._event_kind_label(None) == "אחר"

    def test_every_configured_kind_has_a_hebrew_label(self) -> None:
        for kind, label in pl._EVENT_KIND_LABELS_HE_FALLBACK.items():
            assert pl._event_kind_label(kind) == label
            assert label != kind


# --------------------------------------------------------------------------
# Finding #4 -- patent-survey appendix reliability for patent-office hosts (D8 #9).
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestPatentOfficeHostReliability:
    def test_patents_google_com_no_source_row_returns_official_primary(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(_FakeCursor([])))
        val = survey._appendix_reliability(
            {"kind": "patent", "url": "https://patents.google.com/patent/US123"}
        )
        assert val == {"kind": "primary", "score": 1.0, "label": survey._PATENT_OFFICE_RELIABILITY_LABEL_HE}

    def test_espacenet_no_source_row_returns_official_primary(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(_FakeCursor([])))
        val = survey._appendix_reliability(
            {"kind": "patent", "url": "https://worldwide.espacenet.com/patent/EP123"}
        )
        assert val == {"kind": "primary", "score": 1.0, "label": survey._PATENT_OFFICE_RELIABILITY_LABEL_HE}

    def test_uspto_patft_no_source_row_returns_official_primary(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(_FakeCursor([])))
        val = survey._appendix_reliability({"kind": "patent", "url": "https://patft.uspto.gov/patent/US1"})
        assert val == {"kind": "primary", "score": 1.0, "label": survey._PATENT_OFFICE_RELIABILITY_LABEL_HE}

    def test_ppubs_uspto_no_source_row_returns_official_primary(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(_FakeCursor([])))
        val = survey._appendix_reliability({"kind": "patent", "url": "https://ppubs.uspto.gov/patent/US2"})
        assert val == {"kind": "primary", "score": 1.0, "label": survey._PATENT_OFFICE_RELIABILITY_LABEL_HE}

    def test_non_patent_office_unmatched_host_still_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(_FakeCursor([])))
        val = survey._appendix_reliability({"kind": "patent", "url": "https://some-random-blog.example/x"})
        assert val is None

    def test_real_source_row_wins_over_patent_office_fallback(self, monkeypatch) -> None:
        monkeypatch.setattr(survey, "_SOURCE_HOST_RELIABILITY_CACHE", None, raising=False)
        fake_cursor = _FakeCursor([{"url": "https://patents.google.com/x", "reliability": 2}])
        monkeypatch.setattr(survey, "connection", lambda timeout=5: _FakeConn(fake_cursor))
        val = survey._appendix_reliability({"kind": "patent", "url": "https://patents.google.com/patent/US9"})
        assert val == {"kind": "secondary", "score": 0.4, "label": None}


# --------------------------------------------------------------------------
# Finding #3a -- tender product-line tagging at intake (D9 #6).
# --------------------------------------------------------------------------


class _InsertFakeCursor:
    def __init__(self):
        self.executed: list[tuple[str, dict | None]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return {"id": 99}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _InsertFakeConn:
    def __init__(self, cursor: _InsertFakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTenderProductLineTaggingAtIntake:
    def test_insert_tender_and_item_tags_product_lines(self, monkeypatch) -> None:
        captured: dict = {}

        def fake_tag_product_lines(*, text_he, text_en, entities, subdomain):
            captured.update(text_he=text_he, text_en=text_en, entities=entities, subdomain=subdomain)
            return ["targeting_pods"]

        monkeypatch.setattr(tenders_scan, "tag_product_lines", fake_tag_product_lines)
        monkeypatch.setattr(tenders_scan, "insert_item", lambda **kw: 555)
        monkeypatch.setattr(tenders_scan, "update_item_fields", lambda *a, **kw: None)
        cursor = _InsertFakeCursor()
        monkeypatch.setattr(tenders_scan, "connection", lambda: _InsertFakeConn(cursor))

        notice = tenders_scan.NoticeRaw(
            source_id="ted",
            external_ref="ted:1",
            title="Targeting Pod RFI",
            summary="Procurement of an advanced targeting pod system",
            country="EU",
            cpv_naics=["35700000"],
        )
        tender_id, item_id = tenders_scan._insert_tender_and_item(
            notice,
            ["targeting pod"],
            relevance=8,
            relevance_score=0.8,
            intake="accepted",
            summary_he="רכש פוד ציון מטרות",
            entities=["Elbit"],
        )

        assert tender_id == 99
        assert item_id == 555
        # title + description (summary) + CPV all feed the tagger's text_en, entities pass through,
        # and the LLM's own Hebrew summary feeds text_he.
        assert captured["text_he"] == "רכש פוד ציון מטרות"
        assert "Targeting Pod RFI" in captured["text_en"]
        assert "advanced targeting pod system" in captured["text_en"]
        assert "35700000" in captured["text_en"]
        assert captured["entities"] == ["Elbit"]

        insert_sql, insert_params = cursor.executed[0]
        assert "product_lines" in insert_sql
        assert insert_params["product_lines"] == ["targeting_pods"]

    def test_insert_tender_and_item_no_match_inserts_empty_list(self, monkeypatch) -> None:
        monkeypatch.setattr(tenders_scan, "tag_product_lines", lambda **kw: [])
        monkeypatch.setattr(tenders_scan, "insert_item", lambda **kw: 1)
        monkeypatch.setattr(tenders_scan, "update_item_fields", lambda *a, **kw: None)
        cursor = _InsertFakeCursor()
        monkeypatch.setattr(tenders_scan, "connection", lambda: _InsertFakeConn(cursor))

        notice = tenders_scan.NoticeRaw(
            source_id="src", external_ref="src:1", title="Unrelated HR consulting RFP", country="NL"
        )
        tenders_scan._insert_tender_and_item(notice, [])

        _sql, params = cursor.executed[0]
        assert params["product_lines"] == []


# --------------------------------------------------------------------------
# Finding #3a/#3b -- scripts/repair_round9.py (D9 #6/#10).
# --------------------------------------------------------------------------


class _RepairCursor:
    def __init__(self, select_rows_by_marker: dict[str, list[dict]]):
        self._select_rows = select_rows_by_marker
        self.updates: list[tuple[str, dict | None]] = []
        self._last_marker: str | None = None

    def execute(self, sql, params=None):
        if "UPDATE tenders" in sql:
            self.updates.append((sql, params))
            return
        self._last_marker = next((m for m in self._select_rows if m in sql), None)

    def fetchall(self):
        return list(self._select_rows.get(self._last_marker, []))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RepairConn:
    def __init__(self, cursor: _RepairCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestRepairTagMissingProductLines:
    def test_dry_run_lists_without_writing(self, monkeypatch) -> None:
        rows = {
            "product_lines = '{}'": [
                {
                    "id": 42,
                    "title": "TED Notice",
                    "summary_he": "רכש פוד ציון מטרות",
                    "cpv_naics": ["35700000"],
                    "entities": [],
                    "item_id": None,
                }
            ]
        }
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.product_lines.tagging.tag_product_lines", lambda **kw: ["targeting_pods"])

        results = repair.tag_missing_product_lines(apply=False)

        assert results == [{"id": 42, "title": "TED Notice", "product_lines": ["targeting_pods"]}]
        assert cursor.updates == []

    def test_apply_writes_update(self, monkeypatch) -> None:
        rows = {
            "product_lines = '{}'": [
                {
                    "id": 42,
                    "title": "TED Notice",
                    "summary_he": "",
                    "cpv_naics": [],
                    "entities": [],
                    "item_id": None,
                }
            ]
        }
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.product_lines.tagging.tag_product_lines", lambda **kw: ["targeting_pods"])

        results = repair.tag_missing_product_lines(apply=True)

        assert len(results) == 1
        assert len(cursor.updates) == 1
        _sql, params = cursor.updates[0]
        assert params == {"lines": ["targeting_pods"], "id": 42}

    def test_no_match_is_skipped(self, monkeypatch) -> None:
        rows = {
            "product_lines = '{}'": [
                {
                    "id": 7,
                    "title": "Unrelated notice",
                    "summary_he": "",
                    "cpv_naics": [],
                    "entities": [],
                    "item_id": None,
                }
            ]
        }
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.product_lines.tagging.tag_product_lines", lambda **kw: [])

        results = repair.tag_missing_product_lines(apply=True)

        assert results == []
        assert cursor.updates == []

    def test_unions_linked_item_tags(self, monkeypatch) -> None:
        rows = {
            "product_lines = '{}'": [
                {
                    "id": 8,
                    "title": "Linked notice",
                    "summary_he": "",
                    "cpv_naics": [],
                    "entities": [],
                    "item_id": 700,
                }
            ],
            "id = ANY": [{"id": 700, "product_lines": ["mws_eo"]}],
        }
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.product_lines.tagging.tag_product_lines", lambda **kw: [])

        results = repair.tag_missing_product_lines(apply=False)

        assert results == [{"id": 8, "title": "Linked notice", "product_lines": ["mws_eo"]}]


class TestRepairResolveUnknownStatus:
    def test_above_threshold_becomes_open(self, monkeypatch) -> None:
        rows = {"status = 'unknown'": [{"id": 38, "title": "Some RFI", "relevance_score": 0.8}]}
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.tenders.feedback.get_relevance_threshold", lambda: 0.6)

        results = repair.resolve_unknown_status(apply=True)

        assert results[0]["new_status"] == "open"
        _sql, params = cursor.updates[0]
        assert params == {"status": "open", "id": 38}

    def test_below_threshold_becomes_archived(self, monkeypatch) -> None:
        rows = {"status = 'unknown'": [{"id": 38, "title": "Some RFI", "relevance_score": 0.3}]}
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.tenders.feedback.get_relevance_threshold", lambda: 0.6)

        results = repair.resolve_unknown_status(apply=True)

        assert results[0]["new_status"] == "archived"
        _sql, params = cursor.updates[0]
        assert params == {"status": "archived", "id": 38}

    def test_dry_run_does_not_write(self, monkeypatch) -> None:
        rows = {"status = 'unknown'": [{"id": 38, "title": "Some RFI", "relevance_score": 0.8}]}
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.tenders.feedback.get_relevance_threshold", lambda: 0.6)

        results = repair.resolve_unknown_status(apply=False)

        assert len(results) == 1
        assert cursor.updates == []

    def test_missing_relevance_score_uses_neutral_default(self, monkeypatch) -> None:
        rows = {"status = 'unknown'": [{"id": 38, "title": "Some RFI", "relevance_score": None}]}
        cursor = _RepairCursor(rows)
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.tenders.feedback.get_relevance_threshold", lambda: 0.4)

        # neutral baseline (0.5) >= 0.4 threshold -> open
        results = repair.resolve_unknown_status(apply=False)

        assert results[0]["relevance_score"] == 0.5
        assert results[0]["new_status"] == "open"

    def test_no_unknown_rows_is_empty(self, monkeypatch) -> None:
        cursor = _RepairCursor({"status = 'unknown'": []})
        monkeypatch.setattr("eoa.db.connection", lambda: _RepairConn(cursor))
        monkeypatch.setattr("eoa.tenders.feedback.get_relevance_threshold", lambda: 0.6)

        assert repair.resolve_unknown_status(apply=True) == []
        assert cursor.updates == []
