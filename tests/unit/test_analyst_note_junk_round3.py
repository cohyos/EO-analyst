"""Round-3: a JSON-fragment analyst note is dropped instead of rendered."""

from eoa.report.docx_builder import _draft_analyst_note_text, _is_junk_note


class _Note:
    def __init__(self, s):
        self.sentences_he = s


class _Draft:
    def __init__(self, s):
        self.analyst_note_he = _Note(s)


def test_json_fragment_note_is_junk() -> None:
    assert _is_junk_note("]}, ")
    assert _is_junk_note("")
    assert _is_junk_note('{"a": 1}')
    assert _is_junk_note("ok")


def test_real_note_kept() -> None:
    assert not _is_junk_note("להערכתנו, אלביט תשמור על יתרונה בשוק ה-ISR בשנה הקרובה.")
    assert (
        _draft_analyst_note_text(_Draft(["להערכתנו, אלביט תשמור על יתרונה."]))
        == "להערכתנו, אלביט תשמור על יתרונה."
    )
    assert _draft_analyst_note_text(_Draft(["]}, "])) == ""
