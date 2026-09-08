"""Unit tests for PD-vocab-reports (docs/PLAN_SPEC_VOCABULARY.md section 6, lane (d)): the
"השוואת מפרט" (spec comparison) section of ``eoa.report.product_line.build_product_line`` --
``spec_comparison_entries`` and its helpers (``_dossier_spec_perf_rows``,
``_resolve_product_vocab_values``, ``_register_dossier_source``, ``_first_number``,
``_spec_diff_sentence``, ``_spec_comparison_dossiers``/``_spec_comparison_caption``).

No DB, no LLM/Ollama calls -- ``product_line._fetchall`` and the vocabulary lookups
(``effective_vocabulary``/``match_key_by_synonym``/``GROUP_ORDER_HE``) are monkeypatched, mirroring
``tests/unit/test_pl_report_round15.py``'s own convention for this module. A small, fixed 4-parameter
fake vocabulary (``FOV``/``WEIGHT``/``RANGE``/``NOT_REQUIRED``) is used throughout instead of the real
121-entry ``config/spec_vocabulary.yaml`` so these tests stay isolated from that file's own content
and schema (covered separately by ``eoa.dossier.vocabulary``'s own test suite).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pl_spec_comparison.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.report import claims_gate
from eoa.report import product_line as pl

FOV = pl.SpecParam(
    key="fov",
    label_he="שדה ראייה",
    label_en="FOV",
    unit="מעלות",
    value_type="range",
    enum_values=None,
    synonyms=["שדה ראייה", "FOV"],
    group_he="אופטיקה",
    required=True,
    notes_he="",
    table="specifications",
)
WEIGHT = pl.SpecParam(
    key="weight",
    label_he="משקל",
    label_en="Weight",
    unit='ק"ג',
    value_type="number",
    enum_values=None,
    synonyms=["משקל", "weight"],
    group_he="מכניקה וסביבה",
    required=True,
    notes_he="",
    table="specifications",
)
RANGE = pl.SpecParam(
    key="range",
    label_he="טווח זיהוי",
    label_en="Range",
    unit='ק"מ',
    value_type="number",
    enum_values=None,
    synonyms=["טווח זיהוי", "range"],
    group_he="ביצועי מערכת",
    required=True,
    notes_he="",
    table="performance",
)
NOT_REQUIRED = pl.SpecParam(
    key="optional_x",
    label_he="פרמטר לא חובה",
    label_en="Optional",
    unit=None,
    value_type="text",
    enum_values=None,
    synonyms=[],
    group_he="אופטיקה",
    required=False,
    notes_he="",
    table="specifications",
)
FAKE_VOCAB = [FOV, WEIGHT, RANGE, NOT_REQUIRED]
FAKE_GROUP_ORDER = ("אופטיקה", "חיישנים", "לייזר", "ייצוב ובקרה", "מכניקה וסביבה", "ממשקים", "ביצועי מערכת", "בשלות ולוגיסטיקה")


def _dossier_row(
    product_key: str,
    product_name: str,
    created_at: Any,
    *,
    specs: list[dict[str, Any]] | None = None,
    perf: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": 1,
        "product_key": product_key,
        "product_name": product_name,
        "vendor": None,
        "data": {"specifications": specs or [], "performance": perf or []},
        "sources": sources or [],
        "created_at": created_at,
    }


# =================================================================================================
# _dossier_spec_perf_rows
# =================================================================================================


class TestDossierSpecPerfRows:
    def test_combines_specifications_and_performance(self) -> None:
        data = {
            "specifications": [{"key": "fov", "parameter_he": "שדה ראייה", "value": "10", "cites": [1]}],
            "performance": [{"key": "range", "metric_he": "טווח", "claimed_value": "20", "cites": [2]}],
        }
        rows = pl._dossier_spec_perf_rows(data)
        assert ("fov", "שדה ראייה", [1], "10") in rows
        assert ("range", "טווח", [2], "20") in rows

    def test_performance_value_falls_back_to_tested_when_claimed_is_empty(self) -> None:
        data = {
            "performance": [
                {"key": "range", "metric_he": "x", "claimed_value": "", "tested_or_operational_value": "15", "cites": []}
            ]
        }
        rows = pl._dossier_spec_perf_rows(data)
        assert rows[0][3] == "15"

    def test_missing_fields_never_crash_and_default_to_empty(self) -> None:
        data = {"specifications": [{}], "performance": [{}]}
        assert pl._dossier_spec_perf_rows(data) == [("", "", [], ""), ("", "", [], "")]

    def test_non_dict_rows_are_skipped_not_raised(self) -> None:
        data = {"specifications": ["not a dict", 5], "performance": [None]}
        assert pl._dossier_spec_perf_rows(data) == []

    def test_empty_data_returns_empty_list(self) -> None:
        assert pl._dossier_spec_perf_rows({}) == []


# =================================================================================================
# _resolve_product_vocab_values
# =================================================================================================


class TestResolveProductVocabValues:
    def test_direct_valid_key_is_used_without_calling_the_matcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(text: str, line: str | None) -> None:
            raise AssertionError("must not fall back to label matching when `key` is already valid")

        monkeypatch.setattr(pl, "match_key_by_synonym", boom)
        data = {"specifications": [{"key": "fov", "parameter_he": "שדה ראייה", "value": "10 מעלות", "cites": [1]}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {"fov": ("10 מעלות", [1])}

    def test_legacy_free_named_row_is_matched_via_synonym_matcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: "fov" if "שדה" in text else None)
        data = {"specifications": [{"key": "", "parameter_he": "שדה הראייה של המערכת", "value": "12", "cites": [1]}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {"fov": ("12", [1])}

    def test_unmatched_row_never_crashes_and_is_simply_left_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        data = {"specifications": [{"key": "", "parameter_he": "משהו אקראי לגמרי שלא קשור לכלום", "value": "x", "cites": []}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {}

    def test_match_to_a_param_outside_required_params_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # the matcher resolves to a real vocabulary key ("optional_x"), but this section was only
        # asked about `[FOV]` -- a non-required (or otherwise out-of-scope) match must not surface.
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: "optional_x")
        data = {"specifications": [{"key": "", "parameter_he": "פרמטר לא חובה בטקסט חופשי", "value": "x", "cites": []}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {}

    def test_invalid_or_hallucinated_key_falls_through_to_label_matching(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: "fov")
        data = {"specifications": [{"key": "not_a_real_vocabulary_key", "parameter_he": "שדה ראייה", "value": "10", "cites": [1]}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {"fov": ("10", [1])}

    def test_row_with_no_value_never_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: "fov")
        data = {"specifications": [{"key": "", "parameter_he": "שדה ראייה", "value": "", "cites": []}]}
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {}

    def test_first_match_wins_when_two_rows_target_the_same_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = {
            "specifications": [
                {"key": "fov", "parameter_he": "שדה ראייה", "value": "first", "cites": [1]},
                {"key": "fov", "parameter_he": "שדה ראייה", "value": "second", "cites": [2]},
            ]
        }
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        resolved = pl._resolve_product_vocab_values(data, [FOV], "targeting_pods")
        assert resolved == {"fov": ("first", [1])}


# =================================================================================================
# _register_dossier_source
# =================================================================================================


class TestRegisterDossierSource:
    def test_registers_a_new_source_and_assigns_the_next_shared_n(self) -> None:
        citation_items: list[dict[str, Any]] = [{"id": 5, "n": 1, "_src_kind": "item"}]
        sources_by_n = {1: {"kind": "web", "id": 99, "title": "t", "url": "https://x", "source_name": "s", "published_at": None}}
        n = pl._register_dossier_source(citation_items, sources_by_n, 1)
        assert n == 2
        assert citation_items[-1]["id"] == 99
        assert citation_items[-1]["n"] == 2

    def test_dedupes_by_kind_and_id_reusing_the_existing_n(self) -> None:
        citation_items: list[dict[str, Any]] = [{"id": 99, "n": 1, "_src_kind": "web"}]
        sources_by_n = {1: {"kind": "web", "id": 99}}
        n = pl._register_dossier_source(citation_items, sources_by_n, 1)
        assert n == 1
        assert len(citation_items) == 1

    def test_same_id_but_different_kind_registers_as_a_separate_source(self) -> None:
        # a dossier "patent" record with id=5 must never collapse into an unrelated "item" id=5
        # already in this report's own registry.
        citation_items: list[dict[str, Any]] = [{"id": 5, "n": 1, "_src_kind": "item"}]
        sources_by_n = {1: {"kind": "patent", "id": 5, "title": "p"}}
        n = pl._register_dossier_source(citation_items, sources_by_n, 1)
        assert n == 2
        assert len(citation_items) == 2

    def test_unresolvable_local_n_returns_none_and_never_raises(self) -> None:
        citation_items: list[dict[str, Any]] = []
        assert pl._register_dossier_source(citation_items, {}, 1) is None
        assert citation_items == []

    def test_pre_existing_untagged_entries_default_to_item_kind_for_identity(self) -> None:
        citation_items: list[dict[str, Any]] = [{"id": 5, "n": 1}]  # no _src_kind at all
        sources_by_n = {1: {"kind": "item", "id": 5}}
        assert pl._register_dossier_source(citation_items, sources_by_n, 1) == 1

    def test_empty_registry_starts_numbering_at_one(self) -> None:
        citation_items: list[dict[str, Any]] = []
        sources_by_n = {1: {"kind": "item", "id": 7}}
        assert pl._register_dossier_source(citation_items, sources_by_n, 1) == 1


# =================================================================================================
# _first_number
# =================================================================================================


class TestFirstNumber:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("20 אינץ'", 20.0),
            ("4x", 4.0),
            ("1,500 מטר", 1500.0),
            ("", None),
            (None, None),
            ("ללא מספר בכלל", None),
            ("-5.5 מעלות", -5.5),
        ],
    )
    def test_parses_the_leading_number(self, value: str | None, expected: float | None) -> None:
        assert pl._first_number(value) == expected


# =================================================================================================
# _spec_diff_sentence
# =================================================================================================


class TestSpecDiffSentence:
    def test_none_for_a_single_numeric_value(self) -> None:
        cells = [("A", '10 ק"ג', [1])]
        assert pl._spec_diff_sentence(WEIGHT, cells) is None

    def test_none_when_every_parseable_value_is_numerically_equal(self) -> None:
        cells = [("A", '10 ק"ג', [1]), ("B", '10 ק"ג', [2])]
        assert pl._spec_diff_sentence(WEIGHT, cells) is None

    def test_sentence_names_the_higher_value_product_first(self) -> None:
        cells = [("A", '10 ק"ג', [1]), ("B", '4 ק"ג', [2])]
        sentence = pl._spec_diff_sentence(WEIGHT, cells)
        assert sentence is not None
        assert sentence.text_he.startswith('A מציע 10 ק"ג ב')
        assert 'לעומת 4 ק"ג של B' in sentence.text_he
        assert sentence.cites == [1, 2]

    def test_sentence_passes_claims_gate_unchanged(self) -> None:
        cells = [("A", "10", [1]), ("B", "4", [2])]
        sentence = pl._spec_diff_sentence(WEIGHT, cells)
        assert sentence is not None
        assert claims_gate.gate_text(sentence.text_he) == sentence.text_he

    def test_non_numeric_values_are_ignored_not_compared(self) -> None:
        cells = [("A", "לא ידוע", [1]), ("B", "5", [2])]
        assert pl._spec_diff_sentence(WEIGHT, cells) is None

    def test_none_when_neither_top_nor_bottom_row_carries_a_citation(self) -> None:
        # Sentence.cites requires >= 1 entry ("an unsourced sentence has no business being a
        # Sentence at all") -- a numerically real difference with no source backing either side is
        # never asserted, it must not raise a pydantic ValidationError either.
        cells = [("A", "10", []), ("B", "4", [])]
        assert pl._spec_diff_sentence(WEIGHT, cells) is None

    def test_sentence_still_produced_when_only_one_side_has_a_citation(self) -> None:
        cells = [("A", "10", []), ("B", "4", [7])]
        sentence = pl._spec_diff_sentence(WEIGHT, cells)
        assert sentence is not None
        assert sentence.cites == [7]

    def test_three_products_compares_max_against_min_only(self) -> None:
        cells = [("A", "10", [1]), ("B", "7", [2]), ("C", "2", [3])]
        sentence = pl._spec_diff_sentence(WEIGHT, cells)
        assert sentence is not None
        assert "A מציע 10" in sentence.text_he
        assert "לעומת 2 של C" in sentence.text_he
        assert "B" not in sentence.text_he


# =================================================================================================
# spec_comparison_entries -- end-to-end over the fake vocabulary
# =================================================================================================


class TestSpecComparisonEntries:
    def _patch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dossiers: list[dict[str, Any]],
        *,
        params: list[pl.SpecParam] = FAKE_VOCAB,
        group_order: tuple[str, ...] = FAKE_GROUP_ORDER,
    ) -> None:
        monkeypatch.setattr(pl, "_fetchall", lambda query, params=None: [dict(d) for d in dossiers])
        monkeypatch.setattr(pl, "effective_vocabulary", lambda line_id: list(params))
        monkeypatch.setattr(pl, "GROUP_ORDER_HE", group_order)

    def test_no_dossiers_renders_a_single_placeholder_entry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch(monkeypatch, [])
        entries = pl.spec_comparison_entries("targeting_pods", [])
        assert len(entries) == 1
        assert entries[0]["title_he"] == pl.SPEC_COMPARISON_TITLE_HE
        assert entries[0]["group_he"] == pl.SPEC_COMPARISON_TITLE_HE
        assert entries[0]["body_he"] == pl._SPEC_COMPARISON_NOT_ENOUGH_HE

    def test_no_required_params_renders_the_no_required_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dossier = _dossier_row("k1", "P1", dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
        self._patch(monkeypatch, [dossier], params=[NOT_REQUIRED])
        entries = pl.spec_comparison_entries("targeting_pods", [])
        assert len(entries) == 1
        assert entries[0]["body_he"] == pl._SPEC_COMPARISON_NO_REQUIRED_PARAMS_HE

    def test_single_product_renders_grouped_tables_with_no_diff_sentence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        dossier = _dossier_row(
            "k1",
            "SPECTRO XR",
            dt.datetime(2026, 9, 8, tzinfo=dt.UTC),
            specs=[{"key": "fov", "parameter_he": "שדה ראייה", "value": "10 מעלות", "cites": [1]}],
            sources=[{"n": 1, "kind": "item", "id": 5, "title": "t", "url": "https://x", "source_name": "s"}],
        )
        self._patch(monkeypatch, [dossier], params=[FOV, WEIGHT, RANGE])
        citation_items: list[dict[str, Any]] = []
        entries = pl.spec_comparison_entries("targeting_pods", citation_items)

        caption_entry = entries[0]
        assert caption_entry["title_he"] == pl.SPEC_COMPARISON_TITLE_HE
        assert "SPECTRO XR" in caption_entry["body_he"]
        assert "2026-09-08" in caption_entry["body_he"]

        table_entries = [e for e in entries if "rows" in e]
        assert len(table_entries) == 3  # one per group_he (אופטיקה/מכניקה וסביבה/ביצועי מערכת)
        fov_table = next(e for e in table_entries if e["title_he"] == "אופטיקה")
        assert fov_table["group_he"] == pl.SPEC_COMPARISON_TITLE_HE
        assert fov_table["headers"] == ["פרמטר", "יחידה", "SPECTRO XR"]
        assert fov_table["rows"] == [["שדה ראייה", "מעלות", "10 מעלות [1]"]]
        assert fov_table["no_dedupe"] is True

        weight_table = next(e for e in table_entries if e["title_he"] == "מכניקה וסביבה")
        assert weight_table["rows"] == [["משקל", 'ק"ג', pl._PLACEHOLDER_HE]]

        # the dossier-local citation [1] was registered as this report's own n=1 (registry started
        # empty) -- resolved against the dossier's own `sources` entry.
        assert len(citation_items) == 1
        assert citation_items[0]["id"] == 5
        assert citation_items[0]["n"] == 1

        # a single product has nothing to compare -- the differences entry is omitted entirely.
        assert not any(e.get("title_he") == "הבדלים כמותיים בין המוצרים" for e in entries)

    def test_more_than_four_products_splits_into_batches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        dossiers = [
            _dossier_row(
                f"k{i}",
                f"P{i}",
                dt.datetime(2026, 9, i, tzinfo=dt.UTC),
                specs=[{"key": "fov", "parameter_he": "שדה ראייה", "value": str(i), "cites": []}],
            )
            for i in range(1, 6)  # 5 products -- one batch of 4, one of 1
        ]
        self._patch(monkeypatch, dossiers, params=[FOV])
        entries = pl.spec_comparison_entries("targeting_pods", [])
        table_entries = [e for e in entries if "rows" in e]
        assert len(table_entries) == 2
        assert table_entries[0]["title_he"] == "אופטיקה (1/2)"
        assert table_entries[1]["title_he"] == "אופטיקה (2/2)"
        assert len(table_entries[0]["headers"]) == 6  # parameter + unit + 4 products, <= 6 columns
        assert len(table_entries[1]["headers"]) == 3  # parameter + unit + 1 product
        # column order is deterministic (product name, casefolded)
        assert table_entries[0]["headers"][2:] == ["P1", "P2", "P3", "P4"]
        assert table_entries[1]["headers"][2:] == ["P5"]

    def test_diff_sentence_appears_for_two_products_with_different_numbers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        d1 = _dossier_row(
            "k1", "Alpha", dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            specs=[{"key": "fov", "parameter_he": "שדה ראייה", "value": "20 מעלות", "cites": [1]}],
            sources=[{"n": 1, "kind": "item", "id": 11, "title": "t1", "url": "https://a", "source_name": "s1"}],
        )
        d2 = _dossier_row(
            "k2", "Beta", dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
            specs=[{"key": "fov", "parameter_he": "שדה ראייה", "value": "10 מעלות", "cites": [1]}],
            sources=[{"n": 1, "kind": "item", "id": 22, "title": "t2", "url": "https://b", "source_name": "s2"}],
        )
        self._patch(monkeypatch, [d1, d2], params=[FOV])
        entries = pl.spec_comparison_entries("targeting_pods", [])
        diff_entry = next(e for e in entries if e.get("title_he") == "הבדלים כמותיים בין המוצרים")
        assert diff_entry["group_he"] == pl.SPEC_COMPARISON_TITLE_HE
        assert "Alpha מציע 20 מעלות" in diff_entry["body_he"]
        assert "לעומת 10 מעלות של Beta" in diff_entry["body_he"]

    def test_products_with_no_dossier_never_crash_matching_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # a row whose free-named label matches nothing at all in the (fake) vocabulary -- must
        # simply render the required-param placeholder, never raise.
        monkeypatch.setattr(pl, "match_key_by_synonym", lambda text, line: None)
        dossier = _dossier_row(
            "k1", "Weird Co", dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            specs=[{"key": "", "parameter_he": "משהו לא קשור לגמרי", "value": "x", "cites": []}],
        )
        self._patch(monkeypatch, [dossier], params=[FOV])
        entries = pl.spec_comparison_entries("targeting_pods", [])
        table = next(e for e in entries if "rows" in e)
        assert table["rows"] == [["שדה ראייה", "מעלות", pl._PLACEHOLDER_HE]]
