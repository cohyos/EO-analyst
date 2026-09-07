"""Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md / CR-factcheck.md) -- unit tests for
the patent-description *fabrication* fix (distinct from test_patents_round14.py's own
assignee-misattribution fix).

Confirmed defect: US10506436B1 ("Lattice mesh", a real Anduril patent) has a stored ``abstract``
that is literally a USPTO assignment-transfer notice ("2019-03-07 Assigned to Anduril Industries
Inc...") with zero technical content, yet the stored ``claims_summary_he``/``so_what_he`` describe
a fully invented optical lens/mirror/fiber array. Covers ``eoa.patents.analyze``'s two guards:

1. Guard 1 (pre-call gate): ``_has_sufficient_technical_text`` / the ``analyze_patents`` code path
   that never calls the LLM for a text-less record and instead writes the fixed placeholder +
   ``raw.text_available = false``.
2. Guard 2 (post-call check): ``ground_generated_text`` strips any generated sentence whose
   technical tokens cannot be verified against the record's own title/abstract/assignees/CPC/pub
   number, logging ``patent.ungrounded_description_removed``.

Mirrors tests/unit/test_patents_round14.py's own "stub every DB/network call" convention -- no live
DB/HTTP/LLM calls anywhere in this file.
"""

from __future__ import annotations

from unittest.mock import patch

from eoa.llm.schemas.patents import PatentClaimsOut
from eoa.patents.analyze import (
    _INSUFFICIENT_AFTER_GROUNDING_HE,
    _MIN_TECHNICAL_WORDS,
    _NO_TEXT_SO_WHAT_HE,
    _NO_TEXT_SUMMARY_HE,
    PatentAnalyzeStats,
    _grounding_source_pool,
    _has_sufficient_technical_text,
    _persist_no_text_analysis,
    _rows_missing_analysis,
    _technical_word_count,
    analyze_patents,
    ground_generated_text,
)

# --------------------------------------------------------------------------
# fixture -- US10506436B1's real, live-verified stored abstract (a bare USPTO assignment notice,
# 18 words, zero technical content) and CN112074705A's (40 words, genuine if fragmentary technical
# text) -- both confirmed live 2026-09-07 against the `patents` table.
# --------------------------------------------------------------------------

LATTICE_MESH_TITLE = "US10506436B1 - Lattice mesh"
LATTICE_MESH_ABSTRACT = (
    "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril Industries Inc. "
    "ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)."
)
CN_TITLE = "CN112074705A - Method and system for optical inertial tracking of moving object"
CN_ABSTRACT = (
    "Furthermore, the solution allows to reduce the requirements regarding the capacity of the "
    "storage device 107. In an illustrative embodiment of the present invention, the optical "
    "sensor data preprocessing device is implemented based on fpga lcmxo3lft-2100e-5UWG49 "
    "CTR50(Lattice Semiconductor Corporation, USA)."
)

FABRICATED_CLAIMS_HE = (
    "הפטנט מתאר מערכת 'רשת רשתית' (Lattice Mesh) שמטרתה לשפר ביצועי חיישנים אלקטרואופטיים. "
    "המערכת כוללת רשת של אלמנטים אופטיים כגון עדשות, מראות או סיבים אופטיים המסודרים במבנה גיאומטרי."
)


class TestTechnicalWordCount:
    def test_counts_abstract_words(self):
        assert _technical_word_count("one two three", None) == 3

    def test_none_abstract_is_zero(self):
        assert _technical_word_count(None, None) == 0

    def test_includes_raw_claims_text_when_present(self):
        n = _technical_word_count("one two", {"claims_text": "three four five"})
        assert n == 5

    def test_real_lattice_mesh_abstract_is_18_words(self):
        assert _technical_word_count(LATTICE_MESH_ABSTRACT, None) == 18

    def test_real_cn_abstract_is_40_words(self):
        assert _technical_word_count(CN_ABSTRACT, None) == 40


class TestHasSufficientTechnicalText:
    def test_below_threshold_is_insufficient(self):
        assert _has_sufficient_technical_text("word " * (_MIN_TECHNICAL_WORDS - 1), None) is False

    def test_at_threshold_is_sufficient(self):
        assert _has_sufficient_technical_text("word " * _MIN_TECHNICAL_WORDS, None) is True

    def test_lattice_mesh_assignment_notice_is_insufficient(self):
        """The exact bug case: a bare assignment-transfer notice never clears the bar."""
        assert _has_sufficient_technical_text(LATTICE_MESH_ABSTRACT, None) is False

    def test_cn112074705a_snippet_clears_the_bar(self):
        """40 words of genuine (if fragmentary) technical prose -- not a fabrication case, this
        record's only round-14 defect was assignee misattribution (test_patents_round14.py), not
        description fabrication."""
        assert _has_sufficient_technical_text(CN_ABSTRACT, None) is True

    def test_empty_abstract_is_insufficient(self):
        assert _has_sufficient_technical_text("", None) is False
        assert _has_sufficient_technical_text(None, None) is False


class TestGroundingSourcePool:
    def test_pool_includes_title_abstract_assignees_cpc_pub_number(self):
        row = {
            "title": "Widget system",
            "abstract": "a lidar sensor",
            "assignees": ["Acme Corp"],
            "cpc": ["G01S13"],
            "pub_number": "US123",
            "raw": {},
        }
        pool = _grounding_source_pool(row)
        for expected in ("widget system", "lidar", "acme corp", "g01s13", "us123"):
            assert expected in pool

    def test_missing_fields_do_not_raise(self):
        assert isinstance(_grounding_source_pool({}), str)


class TestGroundGeneratedText:
    def test_grounded_sentence_survives(self):
        text = "המערכת כוללת LIDAR למעקב."
        out = ground_generated_text(text, "lidar tracking system", pub_number="X1", field_name="claims_summary_he")
        assert out == text

    def test_ungrounded_sentence_is_stripped_and_logged(self):
        text = "המערכת כוללת LIDAR למעקב. המערכת כוללת גם FAKE9999 שאינו קיים במקור."
        with patch("eoa.patents.analyze.log") as mock_log:
            out = ground_generated_text(
                text, "lidar tracking system", pub_number="US10506436B1", field_name="claims_summary_he"
            )
        assert "LIDAR" in out
        assert "FAKE9999" not in out
        mock_log.info.assert_called_once()
        _, kwargs = mock_log.info.call_args
        assert kwargs["pub_number"] == "US10506436B1"
        assert kwargs["field"] == "claims_summary_he"
        assert kwargs["token"] == "FAKE9999"

    def test_all_sentences_stripped_falls_back_to_insufficient_marker(self):
        text = "המערכת כוללת FAKE1234. המערכת כוללת גם FAKE5678."
        out = ground_generated_text(text, "nothing relevant here", pub_number="X1", field_name="claims_summary_he")
        assert out == _INSUFFICIENT_AFTER_GROUNDING_HE

    def test_grounded_numeric_and_component_tokens_from_real_cn_case_survive(self):
        """Reproduces the CN112074705A case: a sentence naming components/numbers that genuinely
        appear in the source abstract must not be stripped."""
        sentence = "המערכת כוללת fpga lcmxo3lft-2100e-5UWG49 CTR50 לעיבוד נתוני חיישן אופטי."
        out = ground_generated_text(
            sentence, CN_ABSTRACT.casefold(), pub_number="CN112074705A", field_name="claims_summary_he"
        )
        assert out == sentence

    def test_hebrew_only_sentence_with_no_technical_tokens_is_never_stripped(self):
        text = "אין מספיק מידע בתקציר לניתוח תביעות מלא."
        out = ground_generated_text(text, "", pub_number="X1", field_name="claims_summary_he")
        assert out == text

    def test_empty_text_passes_through(self):
        assert ground_generated_text("", "anything", pub_number="X1", field_name="so_what_he") == ""
        assert ground_generated_text(None, "anything", pub_number="X1", field_name="so_what_he") is None

    def test_ungrounded_english_component_in_the_real_fabrication_case_is_stripped(self):
        """Reproduces the flagship bug case with an English technical token the real detail-page
        abstract does not support ("waveguide" -- not part of the real 'lattice mesh' networking
        abstract, unlike "Lattice"/"Mesh" themselves which genuinely are the patent's own title)."""
        real_detail_abstract = (
            "A system for a lattice mesh comprises an interface and a processor. The interface is "
            "configured to receive a request to register from a host, wherein the request to "
            "register includes a key and a set of asset IDs that the host wishes to claim."
        )
        sentence = "המערכת כוללת waveguide אופטי לשיפור איכות התמונה."
        out = ground_generated_text(
            sentence, real_detail_abstract.casefold(), pub_number="US10506436B1", field_name="claims_summary_he"
        )
        assert out == _INSUFFICIENT_AFTER_GROUNDING_HE

    def test_known_limitation_a_purely_hebrew_fabricated_sentence_has_no_token_to_check(self):
        """Documents this heuristic's real scope: :func:`ground_generated_text` only verifies
        English words/acronyms and >=2-digit numbers (module docstring) -- a sentence written
        entirely in Hebrew, with no such token at all, has nothing for it to check and survives
        untouched even when its content is fabricated. This is exactly why guard 1 (the pre-call
        word-count gate, :func:`_has_sufficient_technical_text`) is the real defense for the
        US10506436B1 case: its actual stored abstract is 18 words of pure assignment-notice
        boilerplate, so the LLM is never called for it in the first place and this Hebrew-fabrication
        text is never generated to begin with."""
        real_detail_abstract = (
            "A system for a lattice mesh comprises an interface and a processor. The interface is "
            "configured to receive a request to register from a host, wherein the request to "
            "register includes a key and a set of asset IDs that the host wishes to claim."
        )
        out = ground_generated_text(
            FABRICATED_CLAIMS_HE,
            real_detail_abstract.casefold(),
            pub_number="US10506436B1",
            field_name="claims_summary_he",
        )
        assert out == FABRICATED_CLAIMS_HE  # not stripped -- see docstring above


# --------------------------------------------------------------------------
# DB-call shape tests (mocked connection, mirrors test_patents_round14.py's _FakeCursor/
# _FakeConnection convention -- no live DB call).
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

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


class TestRowsMissingAnalysisSelectsRaw:
    def test_query_selects_raw_column(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.analyze.connection", lambda: _FakeConnection(cur))
        _rows_missing_analysis(10)
        query, _params = cur.calls[0]
        assert "raw" in query
        assert "abstract" in query


class TestPersistNoTextAnalysis:
    def test_writes_fixed_placeholders_and_stamps_text_available_false(self, monkeypatch):
        cur = _FakeCursor()
        monkeypatch.setattr("eoa.patents.analyze.connection", lambda: _FakeConnection(cur))
        _persist_no_text_analysis(42, 0.0, [])
        query, params = cur.calls[0]
        assert params["claims_summary_he"] == _NO_TEXT_SUMMARY_HE
        assert params["so_what_he"] == _NO_TEXT_SO_WHAT_HE
        assert params["id"] == 42
        assert "text_available" in query
        assert "false" in query


# --------------------------------------------------------------------------
# analyze_patents integration (LLM/DB/entity-resolution boundaries mocked).
# --------------------------------------------------------------------------


class TestAnalyzePatentsTextGate:
    def test_text_less_row_never_calls_llm_and_gets_placeholder(self, monkeypatch):
        row = {
            "id": 62,
            "pub_number": "US10506436B1",
            "title": LATTICE_MESH_TITLE,
            "abstract": LATTICE_MESH_ABSTRACT,
            "assignees": ["Anduril Industries Inc"],
            "cpc": [],
            "jurisdictions": ["US"],
            "url": "https://patents.google.com/patent/US10506436B1/en",
            "raw": {"snippet": LATTICE_MESH_ABSTRACT},
        }
        persisted = {}

        def _fake_persist_no_text(patent_id, relevance, entity_ids):
            persisted["patent_id"] = patent_id
            persisted["relevance"] = relevance
            persisted["entity_ids"] = entity_ids

        with (
            patch("eoa.patents.analyze._rows_missing_analysis", return_value=[row]),
            patch("eoa.patents.analyze._tech_dev_subdomain_keys", return_value=[]),
            patch("eoa.patents.analyze.chat_structured") as mock_chat,
            patch("eoa.patents.analyze._israel_relevance", return_value=0.8),
            patch("eoa.patents.analyze._resolve_entity_ids", return_value=[7]),
            patch("eoa.patents.analyze._persist_no_text_analysis", side_effect=_fake_persist_no_text),
            patch("eoa.patents.analyze._persist_analysis") as mock_persist,
        ):
            stats = analyze_patents(limit=10)

        mock_chat.assert_not_called()
        mock_persist.assert_not_called()
        assert persisted["patent_id"] == 62
        assert persisted["entity_ids"] == [7]
        assert isinstance(stats, PatentAnalyzeStats)
        assert stats.text_unavailable == 1
        assert stats.analyzed == 0

    def test_text_available_row_calls_llm_and_grounds_result(self, monkeypatch):
        row = {
            "id": 64,
            "pub_number": "CN112074705A",
            "title": CN_TITLE,
            "abstract": CN_ABSTRACT,
            "assignees": ["ALT LLC"],
            "cpc": [],
            "jurisdictions": ["CN"],
            "url": "https://patents.google.com/patent/CN112074705A/en",
            "raw": {"snippet": CN_ABSTRACT},
        }
        llm_out = PatentClaimsOut(
            claims_summary_he="הפטנט כולל FPGA לעיבוד נתוני חיישן אופטי. הפטנט כולל גם FAKE7777 שלא קיים במקור.",
            subdomain="",
            so_what_he="משמעות עסקית קצרה ללא אזכורים טכניים.",
        )
        persisted = {}

        def _fake_persist(patent_id, out, relevance, entity_ids):
            persisted["patent_id"] = patent_id
            persisted["out"] = out

        with (
            patch("eoa.patents.analyze._rows_missing_analysis", return_value=[row]),
            patch("eoa.patents.analyze._tech_dev_subdomain_keys", return_value=[]),
            patch("eoa.patents.analyze.chat_structured", return_value=llm_out) as mock_chat,
            patch("eoa.patents.analyze._israel_relevance", return_value=0.0),
            patch("eoa.patents.analyze._resolve_entity_ids", return_value=[]),
            patch("eoa.patents.analyze._persist_analysis", side_effect=_fake_persist),
            patch("eoa.patents.analyze._persist_no_text_analysis") as mock_no_text,
        ):
            stats = analyze_patents(limit=10)

        mock_chat.assert_called_once()
        mock_no_text.assert_not_called()
        assert persisted["patent_id"] == 64
        assert "FPGA" in persisted["out"].claims_summary_he
        assert "FAKE7777" not in persisted["out"].claims_summary_he
        assert stats.analyzed == 1
        assert stats.text_unavailable == 0
        assert stats.ungrounded_sentences_removed >= 1
