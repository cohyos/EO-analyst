"""Patent-survey-specific rendering helpers, additive to ``eoa.report.docx_builder`` (imported
from, never edited -- docs/CONVENTIONS.md ownership note for this task: patents/** owns this
module; docx_builder.py stays untouched, callers only invoke its public functions).

``eoa.report.docx_builder`` already gives every docx/html paragraph and table cell correct
Hebrew/Latin bidi handling at the run level (``split_runs`` / ``_bidi_html``) -- that machinery
only exists inside the docx and html renderers, though. The plain-Markdown renderer
(``render_markdown``) emits GitHub-flavoured-Markdown table cells as bare strings; a cell holding a
Latin patent number/CPC code/assignee name/English title renders correctly in a bidi-aware
Markdown viewer (which infers per-run direction the same way a browser does for plain text) but
can visually fragment when the raw ``.md`` file is opened in a plain-text context that does not --
exactly the "קיטועים" (fragmentation) the user reported (2026-09-06). :func:`ltr_isolate` /
:func:`ltr_join` / :func:`ltr_isolate_if_latin` wrap such values in the Unicode bidi isolate
control characters (LRI/PDI) *once*, in ``eoa.patents.survey``, when the cell value is first
constructed -- the same wrapped string then flows unchanged into the docx/html paths too (harmless
there: the isolate characters are zero-width and sit fully inside whatever run
``split_runs``/``_bidi_html`` would already have classified as a Latin run).
"""

from __future__ import annotations

_LRI = "⁦"  # LEFT-TO-RIGHT ISOLATE (U+2066)
_PDI = "⁩"  # POP DIRECTIONAL ISOLATE (U+2069)

_HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))


def _contains_hebrew(text: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _HEBREW_RANGES) for ch in text)


def ltr_isolate(text: str | None) -> str:
    """Wrap ``text`` (a value known to be Latin/foreign-script, never Hebrew -- a publication
    number, CPC code, or Latin assignee name) in Unicode LRI/PDI isolate marks so it reads
    left-to-right wherever it ends up embedded in RTL Hebrew prose or table cells, including a
    plain-text view of the ``.md`` report where ``docx_builder``'s own run-level bidi splitting
    never applies. A missing/placeholder value (``None``/``""``/``"—"``) passes through
    unchanged -- there is nothing to isolate."""
    if not text or text == "—":
        return text or "—"
    return f"{_LRI}{text}{_PDI}"


def ltr_isolate_if_latin(text: str | None) -> str:
    """:func:`ltr_isolate`, but only applied when ``text`` contains no Hebrew characters at all --
    the safe default for a value that might be a Hebrew title (already correctly handled by
    ``docx_builder``'s own per-run bidi splitting wherever that applies, so wrapping the *whole*
    string LTR would be actively wrong) or a purely Latin/foreign-script one (an English patent
    title or an English news headline) that does need the isolate marks for the plain-Markdown
    path described in the module docstring."""
    if not text or text == "—" or _contains_hebrew(text):
        return text or "—"
    return ltr_isolate(text)


def ltr_join(values: list[str] | None, sep: str = ", ") -> str:
    """:func:`ltr_isolate` applied to ``sep.join(values)`` as one unit -- for a table cell holding
    several Latin names/codes joined together (an assignee list or a CPC-code list), so the whole
    joined run reads left-to-right rather than each token isolating on its own and leaving the
    separators to bidi-reorder unpredictably."""
    if not values:
        return "—"
    return ltr_isolate(sep.join(values))
