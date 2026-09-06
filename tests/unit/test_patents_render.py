"""Tests for eoa.patents.render (2026-09-06 goal: bidi isolation for Latin/English values in a
plain-Markdown table cell -- see the module docstring for why this exists alongside
eoa.report.docx_builder's own run-level bidi splitting)."""

from __future__ import annotations

from eoa.patents.render import ltr_isolate, ltr_isolate_if_latin, ltr_join

_LRI = "⁦"
_PDI = "⁩"


class TestLtrIsolate:
    def test_wraps_latin_value(self):
        out = ltr_isolate("US9197834B2")
        assert out == f"{_LRI}US9197834B2{_PDI}"

    def test_passthrough_for_none(self):
        assert ltr_isolate(None) == "—"

    def test_passthrough_for_placeholder_dash(self):
        assert ltr_isolate("—") == "—"

    def test_passthrough_for_empty_string(self):
        assert ltr_isolate("") == "—"


class TestLtrJoin:
    def test_joins_and_isolates_as_one_unit(self):
        out = ltr_join(["Anduril", "RTX"])
        assert out == f"{_LRI}Anduril, RTX{_PDI}"

    def test_empty_list_is_dash(self):
        assert ltr_join([]) == "—"

    def test_none_is_dash(self):
        assert ltr_join(None) == "—"

    def test_custom_separator(self):
        out = ltr_join(["G01J5", "G02B23/27"], sep=" | ")
        assert out == f"{_LRI}G01J5 | G02B23/27{_PDI}"


class TestLtrIsolateIfLatin:
    def test_wraps_pure_latin_title(self):
        out = ltr_isolate_if_latin("Digital ROIC enhancement and repetition")
        assert out.startswith(_LRI) and out.endswith(_PDI)

    def test_leaves_hebrew_text_untouched(self):
        text = "התעשייה הישראלית אינה נוכחת בנוף הפטנטים"
        assert ltr_isolate_if_latin(text) == text

    def test_leaves_mixed_hebrew_latin_untouched(self):
        """A title mixing scripts is left alone -- docx_builder's own per-run bidi splitting (which
        this helper is additive to, not a replacement for) already handles that case correctly
        inside the docx/html renderers; wrapping the *whole* mixed string LTR here would be wrong."""
        text = "אלביט מערכות (Elbit Systems) מכריזה על פטנט חדש"
        assert ltr_isolate_if_latin(text) == text

    def test_passthrough_for_none(self):
        assert ltr_isolate_if_latin(None) == "—"
