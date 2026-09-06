"""Round-3 D2: generic so_what formulas get one targeted corrective pass."""

from __future__ import annotations

from eoa.llm.schemas.analysis import AnalyzeOut, SoWhatRepairOut
from eoa.pipeline import analyze as mod


def _out(so_what: str) -> AnalyzeOut:
    return AnalyzeOut(
        summary_he="חברת X השיקה עדשה חדשה.", so_what_he=so_what, key_facts=[], events=[], edges=[]
    )


def test_generic_phrase_detection_is_morphology_tolerant() -> None:
    assert mod.generic_so_what_phrase("להערכתנו, ההשקה מחזקת את מעמדה של תע״א") == "מחזקת את מעמד"
    assert mod.generic_so_what_phrase("להערכתנו, המהלך מחזק את מעמד החברה") == "מחזק את מעמד"
    assert mod.generic_so_what_phrase("להערכתנו, זהו צעד שמעיד על מגמה") is not None
    assert (
        mod.generic_so_what_phrase("להערכתנו, Ophir תיקח מ-Controp את עסקת ה-LRF של חיל האוויר ההודי") is None
    )
    assert mod.generic_so_what_phrase(None) is None


def test_repair_replaces_generic_so_what(monkeypatch) -> None:
    calls = []

    def fake_chat(role, schema, messages, **kw):
        calls.append((schema, messages[-1]["content"]))
        assert schema is SoWhatRepairOut
        return SoWhatRepairOut(
            so_what_he="להערכתנו, Ophir נכנסת לנישה שבה Controp מובילה; הלקוח ההודי יקבל חלופה זולה."
        )

    monkeypatch.setattr(mod, "chat_structured", fake_chat)
    out = mod._repair_generic_so_what(
        {"id": 39, "title": "t"},
        _out("להערכתנו, ההשקה מחזקת את מעמדה של תע״א."),
        role="resident",
        interactive=False,
    )
    assert out.so_what_he.startswith("להערכתנו, Ophir")
    assert len(calls) == 1 and "מחזקת את מעמדה" in calls[0][1]


def test_repair_keeps_original_when_rewrite_is_still_generic(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "chat_structured", lambda *a, **k: SoWhatRepairOut(so_what_he="להערכתנו, זה מחזק את מעמד החברה.")
    )
    original = _out("להערכתנו, ההשקה מחזקת את מעמדה של תע״א.")
    assert mod._repair_generic_so_what({"id": 1}, original, role="resident", interactive=False) is original


def test_repair_skipped_when_not_generic(monkeypatch) -> None:
    monkeypatch.setattr(
        mod, "chat_structured", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call"))
    )
    original = _out("להערכתנו, Ophir תיקח מ-Controp את העסקה.")
    assert mod._repair_generic_so_what({"id": 1}, original, role="resident", interactive=False) is original


def test_repair_survives_llm_failure(monkeypatch) -> None:
    def boom(*a, **k):
        raise mod.LLMOutputError("bad json")

    monkeypatch.setattr(mod, "chat_structured", boom)
    original = _out("להערכתנו, ההשקה מחזקת את מעמדה של תע״א.")
    assert mod._repair_generic_so_what({"id": 1}, original, role="resident", interactive=False) is original
