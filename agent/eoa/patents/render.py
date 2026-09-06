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

import html as _html
import re
from typing import Any

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


# --------------------------------------------------------------------------
# timeline rendering (A14b, point 6, 2026-09-06): a monospace ASCII/Unicode bar chart for the
# plain-Markdown report and a dependency-free inline SVG bar chart for the standalone HTML report
# -- ``eoa.patents.cluster`` computes the underlying ``{year: count}`` data, this module only ever
# turns it into text/markup (same ownership split as the rest of this file).
# --------------------------------------------------------------------------


def ascii_timeline(year_counts: dict[int, int], *, bar_char: str = "█", max_width: int = 30) -> str:
    """A small monospace bar chart, one line per year, safe to embed in a Markdown code fence
    (``eoa.patents.survey`` wraps this in a fenced block) -- ``max_width`` bounds the longest bar in
    characters; every other bar is scaled proportionally to it."""
    if not year_counts:
        return "אין נתוני ציר-זמן זמינים."
    years = sorted(year_counts)
    max_count = max(year_counts[y] for y in years) or 1
    width_digits = max(len(str(y)) for y in years)
    lines = []
    for y in years:
        n = year_counts[y]
        bar_len = max(1, round(n / max_count * max_width)) if n else 0
        lines.append(f"{y:<{width_digits}} | {bar_char * bar_len} {n}")
    return "\n".join(lines)


def svg_timeline_bar_chart(
    year_counts: dict[int, int], *, width: int = 640, height: int = 240, title_he: str = ""
) -> str:
    """A minimal, dependency-free inline ``<svg>`` bar chart (no external chart library --
    Artifacts/browsers render raw SVG natively, and this HTML report is a plain static file with
    no CDN access guaranteed). Static, light-mode colours only (this report is not theme-aware like
    ``docx_builder``'s own embedded stylesheet -- a standalone downloaded report file has no
    surrounding page theme to match)."""
    if not year_counts:
        msg = _html.escape("אין נתוני ציר-זמן זמינים.")
        return (
            f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="{msg}"><text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'font-size="14" fill="#333">{msg}</text></svg>'
        )
    years = sorted(year_counts)
    n = len(years)
    max_count = max(year_counts[y] for y in years) or 1
    margin_x, margin_top, margin_bottom = 30, 34, 34
    plot_w = width - margin_x * 2
    plot_h = height - margin_top - margin_bottom
    gap = plot_w / n
    bar_w = gap * 0.6
    bars: list[str] = []
    labels: list[str] = []
    for i, y in enumerate(years):
        v = year_counts[y]
        bar_h = (v / max_count) * plot_h
        x = margin_x + i * gap + (gap - bar_w) / 2
        y_top = margin_top + (plot_h - bar_h)
        bars.append(
            f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" height="{max(bar_h, 1):.1f}" '
            'fill="#4C72B0" />'
        )
        bars.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{max(y_top - 4, 12):.1f}" font-size="11" '
            f'text-anchor="middle" fill="#333">{v}</text>'
        )
        labels.append(
            f'<text x="{x + bar_w / 2:.1f}" y="{height - margin_bottom + 16:.1f}" font-size="11" '
            f'text-anchor="middle" fill="#333">{y}</text>'
        )
    axis = (
        f'<line x1="{margin_x}" y1="{margin_top + plot_h:.1f}" x2="{width - margin_x}" '
        f'y2="{margin_top + plot_h:.1f}" stroke="#888" stroke-width="1"/>'
    )
    title = (
        f'<text x="{width / 2}" y="18" font-size="14" text-anchor="middle" fill="#111">'
        f"{_html.escape(title_he)}</text>"
        if title_he
        else ""
    )
    label = _html.escape(title_he or "ציר זמן הגשות פטנטים")
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{label}">'
        f'<rect width="{width}" height="{height}" fill="#ffffff" />'
        f"{title}{axis}{''.join(bars)}{''.join(labels)}</svg>"
    )


# --------------------------------------------------------------------------
# per-patent "advance" footnote injection (A14b, point 4, 2026-09-06): a short footnote-style line
# right under the FIRST time a patent's "[n]" citation marker appears in the report BODY (never in
# the appendix table itself, which already carries the same text as its own column) -- md/html
# only, per the user's own spec ("docx appendix only" -- the docx appendix table gets an extra
# column instead, built directly in survey.py). Pure string post-processing over the already-
# rendered ``eoa.report.docx_builder.render_markdown``/``render_html`` output, since neither
# renderer has a hook for "insert a line right after wherever a given citation number first
# appears" -- this file's whole reason to exist (see module docstring).
# --------------------------------------------------------------------------

_MD_APPENDIX_HEADING = "## נספח מקורות"
_HTML_APPENDIX_MARKER = "נספח מקורות"
_MD_CITE_RE = re.compile(r"\[(\d+)\]\(#src-\1\)")
_HTML_CITE_RE = re.compile(r'<a href="#src-(\d+)" class="cite">\[\1\]</a>')


def inject_advance_footnotes_md(text: str, advance_by_n: dict[int, str]) -> str:
    """Insert ``> **התקדמות פטנט [n]:** <advance_by_n[n]>`` right after the first line in which a
    citation marker ``[n](#src-n)`` (``eoa.report.docx_builder``'s own deterministic markdown
    citation-link format) appears, for every ``n`` present in ``advance_by_n`` -- scanning stops at
    the sources-appendix heading so the appendix table itself is never touched twice."""
    if not advance_by_n or not text:
        return text
    seen: set[int] = set()
    out: list[str] = []
    stop = False
    for line in text.split("\n"):
        out.append(line)
        if stop:
            continue
        if line.strip() == _MD_APPENDIX_HEADING:
            stop = True
            continue
        for m in _MD_CITE_RE.finditer(line):
            n = int(m.group(1))
            if n in seen or n not in advance_by_n:
                continue
            seen.add(n)
            out.append(f"  > **התקדמות פטנט [{n}]:** {advance_by_n[n]}")
    return "\n".join(out)


def insert_section_before_md_appendix(text: str, heading_he: str, body: str) -> str:
    """Insert a new ``## heading_he`` section (with the literal ``body`` text below it) right
    before the sources-appendix heading in an already-rendered markdown report -- used for the
    Markdown-only ASCII timeline block (:func:`ascii_timeline`), which has no equivalent in the
    docx/html paths (those get a real table + an SVG chart instead, see the survey module)."""
    lines = text.split("\n")
    try:
        idx = lines.index(_MD_APPENDIX_HEADING)
    except ValueError:
        return text.rstrip("\n") + f"\n\n## {heading_he}\n\n{body}\n"
    insertion = [f"## {heading_he}", "", body, ""]
    return "\n".join(lines[:idx] + insertion + lines[idx:])


def insert_section_before_html_appendix(text: str, heading_he: str, body_html: str) -> str:
    """HTML analogue of :func:`insert_section_before_md_appendix` -- used for the inline SVG
    timeline chart (:func:`svg_timeline_bar_chart`)."""
    lines = text.split("\n")
    idx = None
    for i, line in enumerate(lines):
        if _HTML_APPENDIX_MARKER in line and line.lstrip().startswith("<h2"):
            idx = i
            break
    heading_html = f"<h2>{_html.escape(heading_he)}</h2>"
    if idx is None:
        return text + f"\n{heading_html}\n{body_html}\n"
    return "\n".join([*lines[:idx], heading_html, body_html, *lines[idx:]])


# --------------------------------------------------------------------------
# "שיטה והיקף" (methodology & scope) box insertion (round 5 P5/item 1, 2026-09-06,
# docs/REPORT_TEMPLATE_BENCHMARK.md 3.5/item 1, 4/item 8): ``eoa.report.docx_builder`` has an
# ``extra_sections`` hook, but only for ``after_summary``/``after_outlook`` positions -- there is
# no "before the executive summary" position, and that module stays untouched per this task's
# ownership split (docs/CONVENTIONS.md ownership note, same as every other helper in this file).
# These three insert a small labelled section right before the executive-summary heading in an
# already-rendered docx ``Document``/markdown/HTML report -- ``eoa.patents.survey`` builds the
# actual content (:func:`eoa.patents.survey.methodology_box_lines_he`) as a plain
# rendering-agnostic ``list[str]``, so the exact same lines reach all three formats identically.
# --------------------------------------------------------------------------

_MD_SUMMARY_HEADING = "## תקציר מנהלים"
_HTML_SUMMARY_MARKER = "תקציר מנהלים"


def insert_section_before_md_summary(text: str, heading_he: str, lines_he: list[str]) -> str:
    """Insert a new ``## heading_he`` section (``lines_he`` rendered as a bullet list) right
    before the executive-summary heading in an already-rendered markdown report. A report whose
    markdown never carries that heading at all (should not happen for a patent survey) is
    returned unchanged rather than guessing where to splice."""
    lines = text.split("\n")
    try:
        idx = lines.index(_MD_SUMMARY_HEADING)
    except ValueError:
        return text
    body = "\n".join(f"- {line}" for line in lines_he)
    insertion = [f"## {heading_he}", "", body, ""]
    return "\n".join(lines[:idx] + insertion + lines[idx:])


def insert_section_before_html_summary(text: str, heading_he: str, lines_he: list[str]) -> str:
    """HTML analogue of :func:`insert_section_before_md_summary` -- ``lines_he`` rendered as an
    ``<ul>``, inserted right before the first ``<h2>`` whose text is the executive-summary
    heading."""
    lines = text.split("\n")
    idx = None
    for i, line in enumerate(lines):
        if _HTML_SUMMARY_MARKER in line and line.lstrip().startswith("<h2"):
            idx = i
            break
    if idx is None:
        return text
    heading_html = f"<h2>{_html.escape(heading_he)}</h2>"
    items = "".join(f"<li>{_html.escape(line)}</li>" for line in lines_he)
    body_html = f'<ul class="methodology-box">{items}</ul>'
    return "\n".join([*lines[:idx], heading_html, body_html, *lines[idx:]])


def insert_section_before_summary_docx(doc: Any, heading_he: str, lines_he: list[str]) -> None:
    """Mutates ``doc`` in place: inserts a new Heading-1 paragraph titled ``heading_he`` plus one
    "List Bullet"-styled paragraph per ``lines_he`` immediately before the "תקציר מנהלים" Heading-1
    paragraph -- reusing ``eoa.report.docx_builder.add_mixed_paragraph`` (via a tiny
    ``add_paragraph``-duck-typed shim around python-docx's own
    ``Paragraph.insert_paragraph_before``) so every line still gets that function's Hebrew/Latin
    bidi run-splitting, exactly as if it had been part of the original ``build_docx`` call. A
    document with no such heading at all (should not happen -- every survey renders one) is left
    untouched rather than raising or guessing where to insert."""
    from eoa.report.docx_builder import add_mixed_paragraph

    ref = next(
        (
            p
            for p in doc.paragraphs
            if p.text.strip() == "תקציר מנהלים" and p.style is not None and p.style.name == "Heading 1"
        ),
        None,
    )
    if ref is None:
        return

    class _InsertBeforeRef:
        def add_paragraph(self, style: str | None = None):
            return ref.insert_paragraph_before("", style=style)

    container = _InsertBeforeRef()
    add_mixed_paragraph(container, heading_he, style="Heading 1")
    for line in lines_he:
        add_mixed_paragraph(container, line, style="List Bullet")


def inject_advance_footnotes_html(text: str, advance_by_n: dict[int, str]) -> str:
    """HTML analogue of :func:`inject_advance_footnotes_md`, matching
    ``eoa.report.docx_builder.render_html``'s own citation-link markup
    (``<a href="#src-n" class="cite">[n]</a>``)."""
    if not advance_by_n or not text:
        return text
    seen: set[int] = set()
    out: list[str] = []
    stop = False
    for line in text.split("\n"):
        out.append(line)
        if stop:
            continue
        if _HTML_APPENDIX_MARKER in line and line.lstrip().startswith("<h2"):
            stop = True
            continue
        for m in _HTML_CITE_RE.finditer(line):
            n = int(m.group(1))
            if n in seen or n not in advance_by_n:
                continue
            seen.add(n)
            escaped = _html.escape(advance_by_n[n])
            out.append(
                f'<p class="patent-advance-note"><small><strong>התקדמות פטנט [{n}]:</strong> '
                f"{escaped}</small></p>"
            )
    return "\n".join(out)


# --------------------------------------------------------------------------
# docx timeline table shading (A14b, point 6): python-docx cell shading applied to the already-
# built ``Document`` returned by ``eoa.report.docx_builder.build_docx`` -- that module itself is
# never touched (docs/CONVENTIONS.md ownership note for this task); this is plain python-docx
# OOXML manipulation on a table this project's own code already added via the generic
# ``tables=`` hook.
# --------------------------------------------------------------------------


def find_table_by_headers(doc: Any, headers: list[str]) -> Any:
    """The first table in ``doc.tables`` whose header row (first row) text matches ``headers``
    exactly -- ``eoa.patents.survey`` uses this to locate the timeline table it just asked
    ``build_docx`` to render generically, since ``build_docx`` returns a plain ``Document`` with no
    other handle back to "the table I just described"."""
    for table in doc.tables:
        if not table.rows:
            continue
        header_cells = [c.text.strip() for c in table.rows[0].cells]
        if header_cells == headers:
            return table
    return None


def shade_table_cell(cell: Any, hex_fill: str) -> None:
    """Set one table cell's background shading (``w:shd``) to ``hex_fill`` (6 hex digits, no
    ``#``)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tc_pr.append(shd)


def shade_timeline_table_rows(table: Any, status_col_index: int, status_colors: dict[str, str]) -> None:
    """Shade every data row (the header row, index 0, is never touched) of ``table`` by the Hebrew
    expiry-flag text found in ``status_col_index`` -- ``status_colors`` maps that exact flag text
    (e.g. :data:`eoa.patents.cluster.FLAG_EXPIRED_HE`) to a 6-hex-digit fill colour; a row whose
    flag isn't a key in ``status_colors`` (typically the empty "in force, nothing to flag" status)
    is left unshaded."""
    for row in table.rows[1:]:
        cells = row.cells
        if status_col_index >= len(cells):
            continue
        status = cells[status_col_index].text.strip()
        color = status_colors.get(status)
        if not color:
            continue
        for cell in cells:
            shade_table_cell(cell, color)
