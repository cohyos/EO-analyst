"""Round 16 bidi content-review fixtures (docs/qa/content_review/BIDI-REPORTS.md).

Filename kept as ``test_report_bidi_round15.py`` per the investigation brief that requested it.

2026-09-08 20:45 screenshot of the daily report embedded on the morning page showed systemic
Hebrew/Latin bidi defects: digits glued to the following Hebrew word ("3דולר"), a percent sign
displaced from its number ("ב- %75את"), Latin company/system names glued to the following Hebrew
word ("AeroVironmentבעלות", "6מערכות", "ReDroneנוספות", "C-UASישראלית", "5פריטים"), and a
parenthetical's opening mark visually adrift ("( Axon Vision, מזכר").

Root cause (confirmed via the .md source, the generated .html, and a live-browser
``getBoundingClientRect`` measurement -- see BIDI-REPORTS.md): the .md source already carried
correct single spaces everywhere; ``docx_builder.split_runs``/``_bidi_html`` wrapped each Latin/
digit run in ``<bdi dir="ltr">`` correctly, but let the single joining space between a Hebrew run
and an adjacent Latin/digit run get trapped *inside* that isolate as leading/trailing whitespace.
An HTML ``<bdi>``/OOXML-run isolate is atomic, so a boundary space left inside it does not act as a
normal separator -- the two words on either side render glued together with zero visible gap.

These fixtures exercise the exact reported phrases end to end through :func:`split_runs` and
:func:`_bidi_html` (the shared run-splitter also used by the docx/Word run-building path), plus the
two narrowly-scoped source-text defenses added to ``textnorm`` for the percent-sign-order and
Hebrew-prefix-hyphen-space variants of the same underlying report content.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from eoa.report import docx_builder as db
from eoa.report.textnorm import (
    collapse_space_after_hebrew_prefix_hyphen,
    fix_percent_sign_order,
    normalize_hebrew_punctuation,
)

# --------------------------------------------------------------------------
# The seven reported symptom phrases, verbatim from the 2026-09-08 daily (or a minimal
# reproduction of the same construction), each as (source_text, must_appear_in_html) pairs.
# --------------------------------------------------------------------------

SYMPTOM_PHRASES = [
    # 1. "3דולר לירי" -- digit glued to the following Hebrew word.
    (
        "נשק לייזר של AeroVironment בעלות 3 דולר לירי צמצם",
        '<bdi dir="ltr">3</bdi> דולר',
    ),
    # 2. "ב- %75את" -- percent sign displaced, number glued to the next word.
    (
        "צמצם ב-75% את טיסות רחפני הקרטלים",
        'ב-<bdi dir="ltr">75%</bdi> את',
    ),
    # 3. "AeroVironmentבעלות" -- Latin company name glued to the following Hebrew word.
    (
        "נשק לייזר של AeroVironment בעלות 3 דולר",
        '<bdi dir="ltr">AeroVironment</bdi> בעלות',
    ),
    # 4/5. "6מערכות" and "ReDroneנוספות" -- digit and Latin system name glued to Hebrew.
    (
        "הולנד מזמינה 6 מערכות ReDrone נוספות מאלביט",
        (
            'מזמינה <bdi dir="ltr">6</bdi> מערכות',
            '<bdi dir="ltr">ReDrone</bdi> נוספות',
        ),
    ),
    # 6. "C-UASישראלית" -- Latin acronym glued to the following Hebrew word.
    (
        "לצד הזמנת C-UAS ישראלית נוספת",
        '<bdi dir="ltr">C-UAS</bdi> ישראלית',
    ),
    # 7. "5פריטים" -- digit glued to the following Hebrew word.
    (
        "לעומת הדוח היומי הקודם: 5 פריטים חדשים",
        '<bdi dir="ltr">5</bdi> פריטים',
    ),
]


@pytest.mark.parametrize("source_text,expected", SYMPTOM_PHRASES)
def test_bidi_html_no_longer_glues_reported_phrases(source_text, expected):
    """Each phrase's html must carry the wrapped Latin/digit run with the joining space *outside*
    the ``<bdi>`` tag (never trailing/leading inside it), which is what actually eliminates the
    glued-word rendering -- not just "some bdi tag exists somewhere"."""
    html_out = db._bidi_html(source_text)
    expected_snippets = expected if isinstance(expected, tuple) else (expected,)
    for snippet in expected_snippets:
        assert snippet in html_out, f"expected {snippet!r} in {html_out!r}"
    # And the specific glued form must never appear.
    assert "AeroVirementבעלות" not in html_out


def test_split_runs_never_traps_boundary_space_inside_other_run():
    """Direct check on the run-splitter itself (shared by the docx/Word paragraph-building path,
    not just the HTML renderer): no ``'other'`` run may start or end with whitespace when it has a
    Hebrew neighbour on that side -- the boundary space must have been rebalanced onto the Hebrew
    run instead (see ``_rebalance_boundary_whitespace``)."""
    for source_text, _ in SYMPTOM_PHRASES:
        runs = db.split_runs(source_text)
        assert "".join(chunk for _, chunk in runs) == source_text, "split_runs must be lossless"
        for i, (cls, chunk) in enumerate(runs):
            if cls != "other":
                continue
            if i > 0 and runs[i - 1][0] == "he":
                assert not chunk[:1].isspace(), f"leading space trapped in {chunk!r} ({source_text!r})"
            if i + 1 < len(runs) and runs[i + 1][0] == "he":
                assert not chunk[-1:].isspace(), f"trailing space trapped in {chunk!r} ({source_text!r})"


def test_paren_mixed_hebrew_latin_content_no_glued_words():
    """The "( Axon Vision, מזכר" finding: a parenthetical opening directly against Latin content,
    with Hebrew content resuming before the closing paren. No Hebrew letter may end up directly
    adjacent (zero-width) to a Latin letter anywhere in the rendering -- confirmed empirically (a
    live-browser character-position measurement) to be exactly the condition that makes text read
    as glued; a bracket sitting flush against a letter is normal, unrelated typography and is left
    alone."""
    text = "שדווחו היום (Axon Vision, מזכר עין שלישית) נמוכים"
    runs = db.split_runs(text)
    assert "".join(chunk for _, chunk in runs) == text
    for (cls_a, chunk_a), (cls_b, chunk_b) in pairwise(runs):
        if cls_a == cls_b or not chunk_a or not chunk_b:
            continue
        left_ch, right_ch = chunk_a[-1], chunk_b[0]
        if left_ch.isalpha() and right_ch.isalpha():
            pytest.fail(f"letters from different runs touch with no space: {left_ch!r}{right_ch!r}")
    html_out = db._bidi_html(text)
    assert "מזכרAxon" not in html_out
    assert "מזכר" in html_out and "Axon Vision" in html_out


def test_split_runs_bracket_pair_stays_symmetric_regression():
    """Q5-4 regression (docs/qa/findings_Q5_r1.md), re-asserted here alongside the round-16
    fixtures: the whitespace rebalancing added for this round must not disturb the pre-existing
    symmetric-bracket behavior for a parenthetical that already carries its own real spaces on
    both sides (no boundary space trapped inside the isolate to begin with)."""
    text = "פודים ומטע״דים אוויריים (Airborne Pods & Payloads)"
    runs = db.split_runs(text)
    assert runs == [
        ("he", "פודים ומטע״דים אוויריים ("),
        ("other", "Airborne Pods & Payloads"),
        ("he", ")"),
    ]


# --------------------------------------------------------------------------
# textnorm: narrowly-scoped source-text defenses for the percent-sign-order and
# Hebrew-prefix-hyphen-space variants of the same underlying content.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_text,expected",
    [
        ("צמצם ב-75% את טיסות", "צמצם ב-75% את טיסות"),  # already correct: no-op
        # sign-order only here; the stray space after the maqaf hyphen is a separate pass, see
        # test_normalize_hebrew_punctuation_repairs_full_percent_finding for the combined fix.
        ("צמצם ב- %75 את טיסות", "צמצם ב- 75% את טיסות"),
        ("%75", "75%"),
        ("% 75", "75%"),
    ],
)
def test_fix_percent_sign_order(source_text, expected):
    assert fix_percent_sign_order(source_text) == expected


@pytest.mark.parametrize(
    "source_text,expected",
    [
        ("ב-75%", "ב-75%"),  # already correct: no-op
        ("ב- 75%", "ב-75%"),
        ("כ- 10 מערכות", "כ-10 מערכות"),
    ],
)
def test_collapse_space_after_hebrew_prefix_hyphen(source_text, expected):
    assert collapse_space_after_hebrew_prefix_hyphen(source_text) == expected


def test_normalize_hebrew_punctuation_repairs_full_percent_finding():
    """The exact finding text: "ב- %75" -> "ב-75%" end to end through the full pipeline."""
    assert normalize_hebrew_punctuation("צמצם ב- %75 את טיסות") == "צמצם ב-75% את טיסות"


def test_normalize_hebrew_punctuation_leaves_correct_text_unchanged():
    """Idempotence / no false positives: text that was already correct must round-trip unchanged."""
    text = "צבא ארה״ב מדווח כי נשק לייזר של AeroVironment בעלות 3 דולר לירי צמצם ב-75% את טיסות"
    assert normalize_hebrew_punctuation(text) == text


def test_normalize_hebrew_punctuation_does_not_touch_hebrew_conjunction_prefix():
    """Confirmed-real corpus content (docs/qa/content_review/BIDI-REPORTS.md): a single-letter
    Hebrew conjunction ("ו-", "and") correctly glues directly to a following Latin/digit token with
    zero space per ordinary Hebrew grammar -- e.g. a patent-code citation "וH04N5" ("and H04N5").
    This must never be "fixed" by inserting a space."""
    text = "בין הפטנטים נכללים H04N5 וH04N5 (שני פטנטים) וגם Sabanci University"
    assert normalize_hebrew_punctuation(text) == text
