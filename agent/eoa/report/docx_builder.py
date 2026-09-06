"""Render :class:`DailyReportDraft` (+ items/events/deep-search/open-points) to docx/md/html.

Hebrew RTL correctness is the whole point of the docx path: document defaults, every paragraph and
every table are flagged right-to-left (``w:bidi`` / ``w:bidiVisual``), while Latin terms, numbers and
URLs embedded in Hebrew prose stay in their own left-to-right runs so Word's bidi algorithm renders
them correctly instead of mirroring them. ``[n]`` citation markers are rendered as small superscript
runs; the appendix ("נספח מקורות") repeats every numbered item with a clickable hyperlink.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import docx
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.run import Run
from lxml import etree

from eoa.llm.schemas.analysis import DailyReportDraft
from eoa.report.qa_citations import QAResult

# -- constants -----------------------------------------------------------------

HEBREW_FONT = "David"
HEBREW_FONT_FALLBACK = "Arial"
BODY_SIZE_PT = 11

TITLE_TEXT = "דוח יומי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_EVENT_KIND_LABELS_HE = {
    "contract_award": "זכייה בחוזה",
    "m_and_a": "מיזוג/רכישה",
    "partnership": "שותפות",
    "investment": "השקעה",
    "launch": "השקה",
    "test": "ניסוי",
    "deployment": "פריסה",
    "regulation": "רגולציה",
    "other": "אחר",
}
_OUTCOME_LABELS_HE = {
    "found": "נמצא",
    "partial": "חלקי",
    "not_found": "לא נמצא",
    "stopped_budget": "הופסק (תקציב)",
    "stopped_timeout": "הופסק (זמן)",
}

_HE_WEEKDAYS = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")  # Monday=0 .. Sunday=6
_HE_MONTHS = (
    "ינואר",
    "פברואר",
    "מרץ",
    "אפריל",
    "מאי",
    "יוני",
    "יולי",
    "אוגוסט",
    "ספטמבר",
    "אוקטובר",
    "נובמבר",
    "דצמבר",
)

_HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))
_CITATION_RE = re.compile(r"\[(\d+)\]")

# CT_PPr / CT_Settings child tags that follow w:bidi / w:updateFields in the OOXML schema, used with
# ``insert_element_before`` so the elements we inject land in a schema-valid position regardless of
# what optional siblings a given paragraph/style/settings part already has.
_PPR_TAGS_AFTER_BIDI = (
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)
_SETTINGS_TAGS_AFTER_UPDATE_FIELDS = (
    "w:defaultTabStop",
    "w:characterSpacingControl",
    "w:savePreviewPicture",
    "w:compat",
    "w:rsids",
    "w:mathPr",
    "w:themeFontLang",
    "w:clrSchemeMapping",
    "w:doNotAutoCompressPictures",
    "w:shapeDefaults",
    "w:decimalSymbol",
    "w:listSeparator",
    "w:docId",
    "w:defaultImageDpi",
)


# -- Hebrew date formatting ------------------------------------------------------


def hebrew_date_str(d: dt.date) -> str:
    """Format a Gregorian date as a Hebrew-language string, e.g. 'יום חמישי, 4 בספטמבר 2026'."""
    weekday = _HE_WEEKDAYS[d.weekday()]
    month = _HE_MONTHS[d.month - 1]
    return f"יום {weekday}, {d.day} ב{month} {d.year}"


def fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)[:10] or "—"


def fmt_amount(ev: dict) -> str:
    amount = ev.get("amount_usd")
    if amount is None:
        return "—"
    currency = ev.get("currency") or "USD"
    try:
        return f"{float(amount):,.0f} {currency}"
    except (TypeError, ValueError):
        return f"{amount} {currency}"


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in (text or "").split("\n\n")]
    return [p for p in parts if p] or [""]


# -- source display label (F8: never show a raw URL as the "source" column) --------------


_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _domain_from_url(url: str) -> str:
    """A short display domain for ``url`` (e.g. 'sam.gov', 'ted.europa.eu'), stripping a leading
    'www.'. Falls back to the raw string if it doesn't parse as a URL at all."""
    try:
        netloc = urlparse(url).netloc or url
    except ValueError:
        netloc = url
    netloc = netloc.split("@")[-1].split(":")[0]
    if netloc.lower().startswith("www."):
        netloc = netloc[4:]
    return netloc or "—"


def source_label(source_name: str | None, url: str | None) -> str:
    """The display label for an item's source: ``source_name`` when it is a real name, otherwise a
    short domain derived from ``url`` — never the raw URL itself (F8: tender-derived items with no
    linked ``sources`` row fall back to ``COALESCE(src.name, i.url)`` at the query layer, which put
    the full URL in the "מקור" column; the URL belongs only in the dedicated link column)."""
    name = (source_name or "").strip()
    if name and not _URL_RE.match(name):
        return name
    if url:
        return _domain_from_url(url)
    return name or "—"


def _looks_like_url(value: Any) -> bool:
    return isinstance(value, str) and bool(_URL_RE.match(value.strip()))


# -- Hebrew/Latin run segmentation ------------------------------------------------


def _char_class(ch: str) -> str | None:
    """'he' for a Hebrew-script character, 'other' for a Latin letter/digit, None otherwise."""
    cp = ord(ch)
    for lo, hi in _HEBREW_RANGES:
        if lo <= cp <= hi:
            return "he"
    if ch.isalpha() or ch.isdigit():
        return "other"
    return None


_BRACKET_OPEN_TO_CLOSE = {"(": ")", "[": "]", "{": "}"}
_BRACKET_CLOSE_TO_OPEN = {v: k for k, v in _BRACKET_OPEN_TO_CLOSE.items()}


def split_runs(text: str) -> list[tuple[str, str]]:
    """Split ``text`` into ``(cls, chunk)`` pairs, ``cls`` in {'he', 'other'}.

    Whitespace/punctuation characters inherit the class of the run they fall in (so a space between
    two Hebrew words does not itself force a run break); a class change happens only when a Hebrew
    letter follows non-Hebrew content or vice versa.

    Bracket pairs (``()``, ``[]``, ``{}``) and ``"`` pairs are kept symmetric: a closing mark takes
    the class its matching opening mark was assigned, instead of whatever class happens to be
    "current" at the closing mark's own position. Without this, a parenthetical like
    'פודים ומטע"דים אוויריים (Airborne Pods & Payloads)' put the opening '(' in the Hebrew run (it
    follows Hebrew text, per the plain inherit-from-``cur`` rule) but the closing ')' in the Latin
    run (it follows "Payloads") -- an asymmetric split that renders as broken bidi (Q5-4,
    docs/qa/findings_Q5_r1.md).
    """
    if not text:
        return []
    default = "other"
    for ch in text:
        c = _char_class(ch)
        if c:
            default = c
            break
    runs: list[tuple[str, str]] = []
    buf: list[str] = []
    cur = default
    bracket_stack: list[tuple[str, str]] = []  # (opening char, class it was emitted with)
    quote_open_class: str | None = None  # class the currently-open '"' was emitted with, if any
    for ch in text:
        base = _char_class(ch)
        if base is not None:
            c = base
        elif (
            ch in _BRACKET_CLOSE_TO_OPEN
            and bracket_stack
            and bracket_stack[-1][0] == _BRACKET_CLOSE_TO_OPEN[ch]
        ):
            c = bracket_stack[-1][1]
        elif ch == '"' and quote_open_class is not None:
            c = quote_open_class
        else:
            c = cur

        if c != cur and buf:
            runs.append((cur, "".join(buf)))
            buf = []
        cur = c
        buf.append(ch)

        if base is None:
            if ch in _BRACKET_OPEN_TO_CLOSE:
                bracket_stack.append((ch, cur))
            elif (
                ch in _BRACKET_CLOSE_TO_OPEN
                and bracket_stack
                and bracket_stack[-1][0] == _BRACKET_CLOSE_TO_OPEN[ch]
            ):
                bracket_stack.pop()
            elif ch == '"':
                quote_open_class = None if quote_open_class is not None else cur
    if buf:
        runs.append((cur, "".join(buf)))
    return runs


def split_runs_with_citations(text: str) -> list[tuple[str, str]]:
    """Like :func:`split_runs`, but first carves out every ``[n]`` token as its own run tagged
    'cite' (rendered as a superscript later), then runs the Hebrew/Latin classification on what's
    left. Citation tokens are extracted from the raw text *before* he/other classification so a
    bracket never gets absorbed into a neighbouring Hebrew run (punctuation otherwise inherits the
    class of whatever run it falls in)."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _CITATION_RE.finditer(text):
        if m.start() > pos:
            tokens.extend(split_runs(text[pos : m.start()]))
        tokens.append(("cite", m.group(0)))
        pos = m.end()
    if pos < len(text):
        tokens.extend(split_runs(text[pos:]))
    return tokens


# -- low-level OOXML helpers ------------------------------------------------------


def _ensure_bidi(ppr) -> None:
    if ppr.find(qn("w:bidi")) is not None:
        return
    bidi = OxmlElement("w:bidi")
    ppr.insert_element_before(bidi, *_PPR_TAGS_AFTER_BIDI)


def _paragraph_rtl_right(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _ensure_bidi(paragraph._p.get_or_add_pPr())


def _style_run(run: Run, *, hebrew: bool, size_pt: float | None = None) -> None:
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    run.font.rtl = hebrew
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    if hebrew:
        rfonts.set(qn("w:ascii"), HEBREW_FONT_FALLBACK)
        rfonts.set(qn("w:hAnsi"), HEBREW_FONT_FALLBACK)
        rfonts.set(qn("w:cs"), HEBREW_FONT)
    else:
        rfonts.set(qn("w:ascii"), HEBREW_FONT_FALLBACK)
        rfonts.set(qn("w:hAnsi"), HEBREW_FONT_FALLBACK)
        rfonts.set(qn("w:cs"), HEBREW_FONT_FALLBACK)


def _emit_mixed_runs(paragraph, text: str, *, size_pt: float | None = BODY_SIZE_PT) -> None:
    for cls, chunk in split_runs_with_citations(text):
        if not chunk:
            continue
        if cls == "cite":
            m = _CITATION_RE.match(chunk)
            if m:
                add_citation_run(paragraph, int(m.group(1)), size_pt=size_pt)
                continue
        run = paragraph.add_run(chunk)
        _style_run(run, hebrew=(cls == "he"), size_pt=size_pt)


def add_mixed_paragraph(
    container, text: str, style: str | None = None, *, size_pt: float | None = BODY_SIZE_PT
):
    """Add a paragraph to ``container`` (a Document or a table cell) with Hebrew/Latin runs split
    so English tokens, numbers and URLs keep left-to-right reading inside the RTL paragraph.

    Citation markers (``[n]``) are rendered as superscript, non-Hebrew runs.
    """
    paragraph = container.add_paragraph(style=style) if style else container.add_paragraph()
    _paragraph_rtl_right(paragraph)
    _emit_mixed_runs(paragraph, text, size_pt=size_pt)
    return paragraph


def _fill_cell(cell, text: str, *, bold: bool = False, size_pt: float = 10) -> None:
    paragraph = cell.paragraphs[0]
    _paragraph_rtl_right(paragraph)
    _emit_mixed_runs(paragraph, text, size_pt=size_pt)
    if bold:
        for run in paragraph.runs:
            run.font.bold = True


def add_hyperlink(paragraph, url: str, text: str, *, hebrew: bool = False) -> Run:
    """Add a clickable, relationship-based hyperlink run to ``paragraph``; returns the run."""
    part = paragraph.part
    r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run_elm = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rstyle = OxmlElement("w:rStyle")
    rstyle.set(qn("w:val"), "Hyperlink")
    rpr.append(rstyle)
    run_elm.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run_elm.append(t)
    hyperlink.append(run_elm)
    paragraph._p.append(hyperlink)
    run = Run(run_elm, paragraph)
    _style_run(run, hebrew=hebrew, size_pt=10)
    return run


def add_internal_hyperlink(
    paragraph,
    anchor: str,
    text: str,
    *,
    hebrew: bool = True,
    style: str | None = "Hyperlink",
    size_pt: float = 10,
) -> Run:
    """Add a run-of-the-mill *internal* hyperlink (a Word bookmark reference, ``w:anchor`` rather
    than a relationship ``r:id``) to ``paragraph``; returns the run. Used to build a real,
    immediately-navigable table of contents (see :func:`_add_bookmark`/:func:`_add_real_toc`)
    instead of a ``TOC`` field that shows nothing until the user manually updates fields (F10), and
    (F23) to turn every ``[n]`` citation marker into a real jump to its row in the sources appendix
    (``style=None`` there — a small superscript numeral, not a full blue/underlined "Hyperlink"
    styled chunk)."""
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("w:anchor"), anchor)
    run_elm = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    if style:
        rstyle = OxmlElement("w:rStyle")
        rstyle.set(qn("w:val"), style)
        rpr.append(rstyle)
    run_elm.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run_elm.append(t)
    hyperlink.append(run_elm)
    paragraph._p.append(hyperlink)
    run = Run(run_elm, paragraph)
    _style_run(run, hebrew=hebrew, size_pt=size_pt)
    return run


def add_citation_run(paragraph, n: int, *, size_pt: float | None = None) -> Run:
    """A ``[n]`` citation marker rendered as an internal-hyperlink run (F23): superscript,
    non-Hebrew styled, jumping straight to the bookmark ``src_{n}`` on that item's row in the
    sources appendix (:func:`_add_sources_appendix`) instead of being inert text. Used both by
    :func:`_emit_mixed_runs` (citations inside prose) and :func:`_add_events_table` (the events
    table's "מקור" column), so every ``[n]`` in the document behaves identically.

    Real Word footnotes (a ``word/footnotes.xml`` part with ``w:footnoteReference``) were
    considered for F23 as well, but python-docx has no footnote API and hand-rolling the extra
    OOXML part (content-type override, document-relationship, its own rels part for the source
    URL, id bookkeeping) carries real corruption risk for a document this pipeline regenerates
    nightly with no human review before delivery. The internal-hyperlink-to-appendix approach
    below gives the same practical outcome — one click from a citation to its full source record
    — without that risk, so it is the only mechanism implemented here.
    """
    run = add_internal_hyperlink(
        paragraph, f"src_{n}", f"[{n}]", hebrew=False, style=None, size_pt=(size_pt - 2) if size_pt else 9
    )
    run.font.superscript = True
    return run


_bookmark_id_counter = 0


def _add_bookmark(paragraph, name: str) -> None:
    """Wrap ``paragraph`` in a ``w:bookmarkStart``/``w:bookmarkEnd`` pair named ``name`` so an
    internal hyperlink (:func:`add_internal_hyperlink`) can jump straight to it."""
    global _bookmark_id_counter
    _bookmark_id_counter += 1
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(_bookmark_id_counter))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(_bookmark_id_counter))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def _set_table_rtl(table) -> None:
    tbl_pr = table._tbl.tblPr
    if tbl_pr.find(qn("w:bidiVisual")) is None:
        tbl_pr.append(OxmlElement("w:bidiVisual"))


def _shade_header_row(table, *, fill: str = "D9D9D9") -> None:
    """Light grey shading on every cell of a table's first (header) row, per F10's "table style
    with header row shading" requirement."""
    for cell in table.rows[0].cells:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = tc_pr.find(qn("w:shd"))
        if shd is None:
            shd = OxmlElement("w:shd")
            tc_pr.append(shd)
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)


# -- document-level defaults ------------------------------------------------------


def _configure_document_defaults(doc: DocxDocument) -> None:
    """RTL paragraphs by default (docDefaults + Normal/Heading styles), Hebrew font 'David'
    (fallback 'Arial'), 11pt body."""
    styles_elm = doc.styles.element
    doc_defaults = styles_elm.find(qn("w:docDefaults"))
    if doc_defaults is None:
        doc_defaults = OxmlElement("w:docDefaults")
        styles_elm.insert(0, doc_defaults)

    rpr_default = doc_defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        rpr_default = OxmlElement("w:rPrDefault")
        doc_defaults.append(rpr_default)
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), HEBREW_FONT_FALLBACK)
    rfonts.set(qn("w:hAnsi"), HEBREW_FONT_FALLBACK)
    rfonts.set(qn("w:cs"), HEBREW_FONT)
    if rpr.find(qn("w:rtl")) is None:
        rpr.append(OxmlElement("w:rtl"))

    ppr_default = doc_defaults.find(qn("w:pPrDefault"))
    if ppr_default is None:
        ppr_default = OxmlElement("w:pPrDefault")
        doc_defaults.append(ppr_default)
    ppr = ppr_default.find(qn("w:pPr"))
    if ppr is None:
        ppr = OxmlElement("w:pPr")
        ppr_default.append(ppr)
    _ensure_bidi(ppr)
    jc = ppr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        ppr.append(jc)
    jc.set(qn("w:val"), "right")

    normal = doc.styles["Normal"]
    normal.font.name = HEBREW_FONT_FALLBACK
    normal.font.size = Pt(BODY_SIZE_PT)
    normal.font.rtl = True
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), HEBREW_FONT)
    normal.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _ensure_bidi(normal.element.get_or_add_pPr())

    for style_name, size in (("Heading 1", 16), ("Heading 2", 13), ("Title", 22), ("Subtitle", 14)):
        try:
            style = doc.styles[style_name]
        except KeyError:
            continue
        style.font.name = HEBREW_FONT_FALLBACK
        style.font.size = Pt(size)
        style.font.rtl = True
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:cs"), HEBREW_FONT)
        style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        _ensure_bidi(style.element.get_or_add_pPr())


def _flag_update_fields(doc: DocxDocument) -> None:
    """Flag the document so Word refreshes TOC/PAGE fields the moment it is opened."""
    settings_elm = doc.settings.element
    if settings_elm.find(qn("w:updateFields")) is not None:
        return
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    settings_elm.insert_element_before(el, *_SETTINGS_TAGS_AFTER_UPDATE_FIELDS)


def _add_real_toc(doc: DocxDocument, entries: list[tuple[str, str]]) -> None:
    """A literal, immediately-clickable table of contents built from Word bookmarks
    (:func:`_add_bookmark`/:func:`add_internal_hyperlink`) rather than a ``TOC`` field — a field
    shows a stale/placeholder instruction ("update fields") until the user manually refreshes it in
    Word, which is exactly the F10 complaint. ``entries`` is ``[(title, bookmark_name), ...]`` in
    document order; used only for the weekly/monthly reports (see ``build_docx``'s ``include_toc``)
    since the daily report drops the TOC entirely rather than show a fake one."""
    add_mixed_paragraph(doc, "תוכן עניינים", style="Heading 1")
    for title, bookmark in entries:
        if not title:
            continue
        paragraph = doc.add_paragraph(style="List Bullet")
        _paragraph_rtl_right(paragraph)
        add_internal_hyperlink(paragraph, bookmark, title)


def _add_header(doc: DocxDocument, title_text: str, period_end: dt.date) -> None:
    """A running header (report title + date) on every page, per F10."""
    section = doc.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    paragraph.text = ""
    _paragraph_rtl_right(paragraph)
    _emit_mixed_runs(paragraph, f"{title_text} — {hebrew_date_str(period_end)}", size_pt=9)


def _add_footer_page_number(doc: DocxDocument) -> None:
    section = doc.sections[0]
    footer = section.footer
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    paragraph.text = ""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _ensure_bidi(paragraph._p.get_or_add_pPr())
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    inner_r = OxmlElement("w:r")
    inner_t = OxmlElement("w:t")
    inner_t.text = "1"
    inner_r.append(inner_t)
    fld.append(inner_r)
    paragraph._p.append(fld)


# -- section builders ---------------------------------------------------------------


def _add_events_table(doc: DocxDocument, events: list[dict]) -> None:
    headers = ["תאריך", "סוג", "צדדים", "לקוח/תוכנית", "סכום", "מקור"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    _set_table_rtl(table)
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _fill_cell(cell, text, bold=True)
    _shade_header_row(table)
    for ev in events:
        row = table.add_row().cells
        _fill_cell(row[0], fmt_date(ev.get("date")))
        _fill_cell(row[1], _EVENT_KIND_LABELS_HE.get(ev.get("kind"), ev.get("kind") or "—"))
        _fill_cell(row[2], ", ".join(ev.get("parties") or []) or "—")
        _fill_cell(row[3], ev.get("customer") or ev.get("program") or "—")
        _fill_cell(row[4], fmt_amount(ev))
        source_cell_p = row[5].paragraphs[0]
        _paragraph_rtl_right(source_cell_p)
        n = ev.get("n")
        if n is not None:
            add_citation_run(source_cell_p, n, size_pt=11)
        else:
            _emit_mixed_runs(
                source_cell_p, source_label(ev.get("source_name"), ev.get("item_url")), size_pt=9
            )


def _add_sources_appendix(doc: DocxDocument, items: list[dict]) -> None:
    headers = ["#", "כותרת", "מקור", "תאריך", "קישור"]
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    _set_table_rtl(table)
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _fill_cell(cell, text, bold=True)
    _shade_header_row(table)
    for it in sorted(items, key=lambda x: x.get("n") or 0):
        row = table.add_row().cells
        _fill_cell(row[0], str(it.get("n", "")))
        n = it.get("n")
        if n is not None:
            # F23: a bookmark on this row so every `[n]` citation elsewhere in the document
            # (:func:`add_citation_run`) can jump straight here via an internal hyperlink.
            _add_bookmark(row[0].paragraphs[0], f"src_{n}")
        _fill_cell(row[1], it.get("title") or "—")
        _fill_cell(row[2], source_label(it.get("source_name"), it.get("url")))
        _fill_cell(row[3], fmt_date(it.get("published_at")))
        url = it.get("url") or ""
        link_p = row[4].paragraphs[0]
        _paragraph_rtl_right(link_p)
        if url:
            add_hyperlink(link_p, url, url)
        else:
            _emit_mixed_runs(link_p, "—", size_pt=10)


def _add_generic_table_body(doc: DocxDocument, headers: list[str], rows: list[list[Any]]) -> None:
    """The table itself (no heading) for a deterministic, non-citation RTL table — the 90-day
    conference lookahead (weekly) and the players-map/top-events/24-month-horizon tables (monthly).
    Split out so ``build_docx`` can add the Heading-1 itself
    (wrapped in a TOC bookmark when ``include_toc`` is set) immediately before the table."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    _set_table_rtl(table)
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _fill_cell(cell, text, bold=True)
    _shade_header_row(table)
    for row_values in rows:
        row = table.add_row().cells
        for cell, value in zip(row, row_values, strict=True):
            if _looks_like_url(value):
                link_p = cell.paragraphs[0]
                _paragraph_rtl_right(link_p)
                add_hyperlink(link_p, str(value), str(value))
            else:
                _fill_cell(cell, "—" if value is None else str(value))


def _add_deep_search_section(doc: DocxDocument, deep_search: list[dict]) -> None:
    for entry in deep_search:
        heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
        add_mixed_paragraph(doc, heading, style="Heading 2")
        outcome = _OUTCOME_LABELS_HE.get(entry.get("outcome"), entry.get("outcome") or "—")
        confidence = entry.get("confidence")
        conf_str = f"{confidence:.0%}" if isinstance(confidence, int | float) else "—"
        add_mixed_paragraph(doc, f"תוצאה: {outcome} | רמת ביטחון: {conf_str}", size_pt=10)
        if entry.get("answer_he"):
            add_mixed_paragraph(doc, entry["answer_he"], size_pt=BODY_SIZE_PT)
        if entry.get("contradictions_he"):
            add_mixed_paragraph(doc, f"סתירות/אי-ודאות: {entry['contradictions_he']}", size_pt=10)


# -- public entry points --------------------------------------------------------


def _planned_headings(
    draft: Any,
    events: list[dict],
    deep_search: list[dict],
    open_points: list[str],
    extra_sections: list[dict[str, Any]],
    tables: list[dict[str, Any]] | None,
) -> list[str]:
    """The ordered list of top-level ("Heading 1") section titles this draft will actually render
    — computed once so a real table of contents (docx bookmarks / html anchors) can be built
    without duplicating each renderer's own conditionals (F10)."""
    headings = ["תקציר מנהלים"]
    headings += [
        sec.get("title_he") or ""
        for sec in extra_sections
        if (sec.get("position") or "after_summary") == "after_summary"
    ]
    headings += [section.title_he for section in draft.sections]
    if events:
        headings.append("טבלת אירועים עסקיים")
    if deep_search:
        headings.append("חקירות עומק")
    if open_points:
        headings.append("נקודות פתוחות")
    if draft.outlook_he:
        headings.append("מבט קדימה")
    headings += [
        sec.get("title_he") or ""
        for sec in extra_sections
        if (sec.get("position") or "after_summary") == "after_outlook"
    ]
    headings += [t.get("title_he") or "" for t in (tables or [])]
    headings.append("נספח מקורות")
    return headings


def build_docx(
    draft: DailyReportDraft | Any,
    items: list[dict],
    events: list[dict],
    *,
    period_end: dt.date,
    deep_search: list[dict] | None = None,
    open_clarifications: list[dict] | None = None,
    generated_at: dt.datetime | None = None,
    qa: QAResult | None = None,
    title_text: str | None = None,
    extra_sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    include_toc: bool = False,
) -> DocxDocument:
    """Build the full report ``Document`` in memory (caller saves it).

    ``draft`` only needs ``exec_summary_he``, ``sections`` (``title_he``/``prose_he``),
    ``outlook_he`` and ``open_points_he`` — duck-typed, so the weekly/monthly drafts render here
    unchanged. ``title_text`` overrides the daily-report title (defaults to ``TITLE_TEXT``);
    ``extra_sections`` and ``tables`` (see
    :func:`_add_generic_table_body`) are additive, optional hooks used only by the weekly/monthly report
    builders — omitted, this reproduces the original daily-report layout exactly.

    ``include_toc`` (F10): the daily report never shows one (``False``, the default) rather than a
    ``TOC`` field that displays nothing until the reader manually updates fields in Word; the
    weekly/monthly builders pass ``True`` to get a real, immediately-clickable bookmark-based TOC
    (:func:`_add_real_toc`) instead.
    """
    deep_search = deep_search or []
    open_clarifications = open_clarifications or []
    extra_sections = extra_sections or []
    generated_at = generated_at or dt.datetime.now(dt.UTC)
    resolved_title = title_text or TITLE_TEXT

    open_points = list(draft.open_points_he or [])
    open_points += [c.get("question") or "" for c in open_clarifications if c.get("question")]

    headings = _planned_headings(draft, events, deep_search, open_points, extra_sections, tables)
    bookmark_names = [f"eoa_toc_{i}" for i in range(len(headings))]
    bookmarks = iter(bookmark_names)
    toc_entries = list(zip(headings, bookmark_names, strict=True))

    def _heading1(doc: DocxDocument, text: str):
        paragraph = add_mixed_paragraph(doc, text, style="Heading 1")
        if include_toc:
            name = next(bookmarks, None)
            if name:
                _add_bookmark(paragraph, name)
        return paragraph

    doc = docx.Document()
    _configure_document_defaults(doc)
    _add_header(doc, resolved_title, period_end)
    _add_footer_page_number(doc)

    add_mixed_paragraph(doc, resolved_title, style="Title")
    add_mixed_paragraph(doc, hebrew_date_str(period_end), style="Subtitle")
    add_mixed_paragraph(
        doc,
        f"נוצר אוטומטית על ידי EO-Analyst — {generated_at.strftime('%Y-%m-%d %H:%M')} UTC",
        size_pt=9,
    )
    if qa is not None and not qa.passed:
        warn = add_mixed_paragraph(
            doc,
            "אזהרה: הדוח לא עבר את בדיקת האזכורים במלואה — חלק מהמשפטים הוסרו אוטומטית, "
            "או שהדוח מסומן כלא-מאומת במלואו. יש לעיין ב-qa_report.",
            size_pt=10,
        )
        for run in warn.runs:
            run.font.bold = True
    doc.add_page_break()

    if include_toc:
        _add_real_toc(doc, toc_entries)
        doc.add_page_break()

    _heading1(doc, "תקציר מנהלים")
    add_mixed_paragraph(doc, draft.exec_summary_he or "אין תקציר לתקופה זו.")

    for sec in extra_sections:
        if (sec.get("position") or "after_summary") != "after_summary":
            continue
        _heading1(doc, sec.get("title_he") or "")
        for para in _split_paragraphs(sec.get("body_he") or ""):
            add_mixed_paragraph(doc, para)

    for section in draft.sections:
        _heading1(doc, section.title_he)
        for para in _split_paragraphs(section.prose_he):
            add_mixed_paragraph(doc, para)

    if events:
        _heading1(doc, "טבלת אירועים עסקיים")
        _add_events_table(doc, events)

    if deep_search:
        _heading1(doc, "חקירות עומק")
        _add_deep_search_section(doc, deep_search)

    if open_points:
        _heading1(doc, "נקודות פתוחות")
        for point in open_points:
            add_mixed_paragraph(doc, point, style="List Bullet")

    if draft.outlook_he:
        _heading1(doc, "מבט קדימה")
        add_mixed_paragraph(doc, draft.outlook_he)

    for sec in extra_sections:
        if (sec.get("position") or "after_summary") != "after_outlook":
            continue
        _heading1(doc, sec.get("title_he") or "")
        for para in _split_paragraphs(sec.get("body_he") or ""):
            add_mixed_paragraph(doc, para)

    for tbl in tables or []:
        _heading1(doc, tbl.get("title_he") or "")
        _add_generic_table_body(doc, tbl.get("headers") or [], tbl.get("rows") or [])

    _heading1(doc, "נספח מקורות")
    _add_sources_appendix(doc, items)

    _flag_update_fields(doc)
    return doc


def save_docx(doc: DocxDocument, path: str | Path) -> Path:
    """Save ``doc`` to ``path``, creating parent directories, and return the resolved path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def validate_docx(path: str | Path) -> None:
    """Re-open ``path`` and sanity-check it: no duplicate ZIP parts, every XML part well-formed,
    and python-docx itself can parse the document. Raises ``ValueError`` on any problem."""
    p = Path(path)
    with zipfile.ZipFile(p) as zf:
        names = zf.namelist()
        seen: set[str] = set()
        dupes = set()
        for name in names:
            if name in seen:
                dupes.add(name)
            seen.add(name)
        if dupes:
            raise ValueError(f"docx has duplicate zip parts: {sorted(dupes)}")
        for name in names:
            if name.endswith(".xml") or name.endswith(".rels"):
                data = zf.read(name)
                try:
                    etree.fromstring(data)
                except etree.XMLSyntaxError as exc:
                    raise ValueError(f"malformed XML in {name}: {exc}") from exc
    docx.Document(str(p))  # raises if python-docx itself can't parse it


# -- markdown / html renderers -----------------------------------------------------


def _qa_warning_line(qa: QAResult | None) -> str | None:
    if qa is not None and not qa.passed:
        return "אזהרה: הדוח לא עבר את בדיקת האזכורים במלואה; חלק מהמשפטים הוסרו או שהדוח מסומן כלא-מאומת."
    return None


def _extra_sections_md(lines: list[str], sections: list[dict[str, Any]], position: str) -> None:
    for sec in sections:
        if (sec.get("position") or "after_summary") != position:
            continue
        lines += [f"## {sec.get('title_he') or ''}", "", _md_citations(sec.get("body_he") or ""), ""]


def _md_cell(value: Any) -> str:
    """A markdown table cell: a bare URL becomes a real ``[url](url)`` link (never a raw URL
    string sitting in running text), per F10."""
    if value is None:
        return "—"
    text = str(value)
    return f"[{text}]({text})" if _looks_like_url(text) else text


def _md_citations(text: str) -> str:
    """(F23) Turn every ``[n]`` citation marker in ``text`` into a real markdown link to its
    appendix row anchor (``[n](#src-n)``) instead of inert bracketed text."""
    return _CITATION_RE.sub(lambda m: f"[{m.group(1)}](#src-{m.group(1)})", text or "")


def _tables_md(lines: list[str], tables: list[dict[str, Any]]) -> None:
    for tbl in tables:
        headers = tbl.get("headers") or []
        lines += [
            f"## {tbl.get('title_he') or ''}",
            "",
            "| " + " | ".join(headers) + " |",
            "|" + "---|" * len(headers),
        ]
        for row in tbl.get("rows") or []:
            lines.append("| " + " | ".join(_md_cell(v) for v in row) + " |")
        lines.append("")


def render_markdown(
    draft: DailyReportDraft | Any,
    items: list[dict],
    events: list[dict],
    *,
    period_end: dt.date | None = None,
    deep_search: list[dict] | None = None,
    open_clarifications: list[dict] | None = None,
    qa: QAResult | None = None,
    title_text: str | None = None,
    extra_sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> str:
    """Render the report as GitHub-flavoured Markdown (see :func:`build_docx` for the shared,
    additive ``title_text``/``extra_sections``/``tables`` hooks)."""
    deep_search = deep_search or []
    open_clarifications = open_clarifications or []
    extra_sections = extra_sections or []
    lines = [f"# {title_text or TITLE_TEXT}", ""]
    if period_end is not None:
        lines += [f"**תאריך:** {hebrew_date_str(period_end)}", ""]
    warning = _qa_warning_line(qa)
    if warning:
        lines += [f"> **{warning}**", ""]

    lines += [
        "## תקציר מנהלים",
        "",
        _md_citations(draft.exec_summary_he) or "אין תקציר לתקופה זו.",
        "",
    ]

    _extra_sections_md(lines, extra_sections, "after_summary")

    for section in draft.sections:
        lines += [f"## {section.title_he}", "", _md_citations(section.prose_he), ""]

    if events:
        lines += [
            "## טבלת אירועים עסקיים",
            "",
            "| תאריך | סוג | צדדים | לקוח/תוכנית | סכום | מקור |",
            "|---|---|---|---|---|---|",
        ]
        for ev in events:
            n = ev.get("n")
            src = (
                f"[{n}](#src-{n})"
                if n is not None
                else source_label(ev.get("source_name"), ev.get("item_url"))
            )
            lines.append(
                f"| {fmt_date(ev.get('date'))} "
                f"| {_EVENT_KIND_LABELS_HE.get(ev.get('kind'), ev.get('kind') or '—')} "
                f"| {', '.join(ev.get('parties') or []) or '—'} "
                f"| {ev.get('customer') or ev.get('program') or '—'} "
                f"| {fmt_amount(ev)} | {src} |"
            )
        lines.append("")

    if deep_search:
        lines += ["## חקירות עומק", ""]
        for entry in deep_search:
            heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
            outcome = _OUTCOME_LABELS_HE.get(entry.get("outcome"), entry.get("outcome") or "—")
            lines.append(f"- **{heading}** — {outcome}: {_md_citations(entry.get('answer_he', ''))}")
        lines.append("")

    open_points = list(draft.open_points_he or [])
    open_points += [c.get("question") or "" for c in open_clarifications if c.get("question")]
    if open_points:
        lines += ["## נקודות פתוחות", ""]
        lines += [f"- {_md_citations(p)}" for p in open_points]
        lines.append("")

    if draft.outlook_he:
        lines += ["## מבט קדימה", "", _md_citations(draft.outlook_he), ""]

    _extra_sections_md(lines, extra_sections, "after_outlook")
    _tables_md(lines, tables or [])

    lines += ["## נספח מקורות", "", "| # | כותרת | מקור | תאריך | קישור |", "|---|---|---|---|---|"]
    for it in sorted(items, key=lambda x: x.get("n") or 0):
        url = it.get("url") or ""
        link = f"[{url}]({url})" if url else "—"
        n = it.get("n")
        # F23: an inline HTML anchor (GFM tables can't carry a raw markdown link target of their
        # own) so `[n](#src-n)` from `_md_citations` above lands on this exact row in renderers
        # that pass raw HTML through (GitHub, `markdown-it` with `html: true`, `react-markdown` +
        # `rehype-raw`); in a renderer that doesn't, the anchor is simply invisible and the row
        # number cell still reads correctly.
        n_cell = f'<a id="src-{n}"></a>{n}' if n is not None else ""
        lines.append(
            f"| {n_cell} | {it.get('title') or '—'} | {source_label(it.get('source_name'), url)} "
            f"| {fmt_date(it.get('published_at'))} | {link} |"
        )
    return "\n".join(lines) + "\n"


def _bidi_html(text: str) -> str:
    """Escape ``text`` for HTML, wrapping every Latin/digit run in ``<bdi dir="ltr">`` so embedded
    English names, numbers, and citation markers read correctly inside RTL Hebrew prose/headings —
    the HTML analogue of ``docx_builder``'s own per-run bidi handling (:func:`split_runs`), per F10
    ("HTML: proper dir=rtl, <bdi>/dir=ltr for URLs and Latin names")."""
    parts = []
    for cls, chunk in split_runs(text or ""):
        escaped = html.escape(chunk)
        parts.append(f'<bdi dir="ltr">{escaped}</bdi>' if cls == "other" else escaped)
    return "".join(parts)


def _html_link(url: str, text: str | None = None) -> str:
    """A real ``<a href>`` for ``url``, its visible text wrapped LTR — never a raw URL sitting in
    running Hebrew text."""
    label = text if text is not None else url
    return f'<a href="{html.escape(url)}"><bdi dir="ltr">{html.escape(label)}</bdi></a>'


def _html_cell(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    return _html_link(text) if _looks_like_url(text) else _bidi_html(text)


def _extra_sections_html(parts: list[str], sections: list[dict[str, Any]], position: str, h2) -> None:
    for sec in sections:
        if (sec.get("position") or "after_summary") != position:
            continue
        parts.append(h2(sec.get("title_he") or ""))
        parts.append(f"<p>{_bidi_html(sec.get('body_he') or '')}</p>")


def _tables_html(parts: list[str], tables: list[dict[str, Any]], h2) -> None:
    for tbl in tables:
        headers = tbl.get("headers") or []
        parts.append(h2(tbl.get("title_he") or ""))
        parts.append(
            "<table><thead><tr>"
            + "".join(f"<th>{html.escape(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
        )
        for row in tbl.get("rows") or []:
            cells = "".join(f"<td>{_html_cell(v)}</td>" for v in row)
            parts.append(f"<tr>{cells}</tr>")
        parts.append("</tbody></table>")


_EOA_HTML_STYLE = """
/* F21 (docs/REVIEW_2026-09-05.md): theme-aware stylesheet.
 *
 * This markup ends up rendered two different ways (see web/src/components/reports/ReportBody.tsx
 * + web/src/lib/reportHtml.ts): (a) inlined via React `dangerouslySetInnerHTML` straight into the
 * dark/light Morning-screen page -- NOT an <iframe>, so this <style> tag itself becomes part of
 * the real page's DOM and its selectors match against the *real* document root, letting a
 * `:root[data-theme]` rule here see the app's own theme attribute (set by
 * web/src/components/shell/AppShell.tsx on <html>); or (b) opened directly as the standalone
 * `output/reports/*.html` file, where <html>/<body> are real and carry no `data-theme`.
 *
 * `.eoa-report`'s own text colour/background are `inherit`/`transparent` (not a fixed light
 * palette) so case (a) always blends into whatever the embedding page already looks like, with
 * zero visible seam -- this is the fix for the reported white-box-on-dark-page clash. Case (b)
 * then naturally renders with the browser's own default black-on-white, which is exactly the
 * "print-friendly light" standalone look; `@media print` below pins that explicitly regardless of
 * on-screen theme.
 *
 * Everything that ISN'T plain text/background (table borders, header fill, link colour, the QA
 * warning colour, the TOC box) can't just "inherit" -- those get real light-mode defaults via CSS
 * variables, overridden for dark via `@media (prefers-color-scheme: dark)` (comfortable standalone
 * reading on a dark OS) and via an explicit `[data-theme="dark"/"light"]` rule (the embedding page
 * wins over the OS preference when it sets one).
 */
.eoa-report{
  --eoa-border:#d0d5dd;
  --eoa-border-strong:#10243e;
  --eoa-th-bg:#eef1f5;
  --eoa-link:#1a56db;
  --eoa-warning:#b42318;
  --eoa-toc-bg:#f8f9fb;
  --eoa-toc-border:#e2e5ea;
  --eoa-date:#555;
  font-family:-apple-system,"Segoe UI",Arial,sans-serif;line-height:1.7;
  color:inherit;background:transparent;max-width:920px;margin:0 auto;padding:1.5rem}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .eoa-report{
    --eoa-border:#3a4650;
    --eoa-border-strong:#4f6a86;
    --eoa-th-bg:#1c2b38;
    --eoa-link:#6ea8fe;
    --eoa-warning:#ff8a80;
    --eoa-toc-bg:#16222c;
    --eoa-toc-border:#2c3d42;
    --eoa-date:#9fb0b4}
}
:root[data-theme="dark"] .eoa-report{
  --eoa-border:#3a4650;
  --eoa-border-strong:#4f6a86;
  --eoa-th-bg:#1c2b38;
  --eoa-link:#6ea8fe;
  --eoa-warning:#ff8a80;
  --eoa-toc-bg:#16222c;
  --eoa-toc-border:#2c3d42;
  --eoa-date:#9fb0b4}
:root[data-theme="light"] .eoa-report{
  --eoa-border:#d0d5dd;
  --eoa-border-strong:#10243e;
  --eoa-th-bg:#eef1f5;
  --eoa-link:#1a56db;
  --eoa-warning:#b42318;
  --eoa-toc-bg:#f8f9fb;
  --eoa-toc-border:#e2e5ea;
  --eoa-date:#555}
.eoa-report h1{font-size:1.5rem;border-bottom:2px solid var(--eoa-border-strong);padding-bottom:.4rem;color:inherit}
.eoa-report h2{font-size:1.15rem;margin-top:1.8rem;color:inherit}
.eoa-report p{margin:.5rem 0}
.eoa-report table{width:100%;border-collapse:collapse;margin:.75rem 0 1.25rem;font-size:.9rem}
.eoa-report th,.eoa-report td{border:1px solid var(--eoa-border);padding:.4rem .6rem;text-align:right;vertical-align:top}
.eoa-report th{background:var(--eoa-th-bg)}
.eoa-report a{color:var(--eoa-link)}
.eoa-report a.cite{text-decoration:none;font-size:.75em;vertical-align:super}
.eoa-report .qa-warning{color:var(--eoa-warning)}
.eoa-report .date{color:var(--eoa-date)}
.eoa-report nav.toc{background:var(--eoa-toc-bg);border:1px solid var(--eoa-toc-border);border-radius:6px;padding:.25rem 1.25rem;margin:1rem 0}
.eoa-report nav.toc ul{margin:.5rem 0;padding-inline-start:1.25rem}
.eoa-report nav.toc li{margin:.15rem 0}
@media print{
  .eoa-report{color:#1a1a1a !important;background:#fff !important}
}
"""


def render_html(
    draft: DailyReportDraft | Any,
    items: list[dict],
    events: list[dict],
    *,
    period_end: dt.date | None = None,
    deep_search: list[dict] | None = None,
    open_clarifications: list[dict] | None = None,
    qa: QAResult | None = None,
    title_text: str | None = None,
    extra_sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    include_toc: bool = False,
) -> str:
    """Render the report as a self-contained, standalone RTL HTML document — a full
    ``<!doctype html>`` page with its own embedded stylesheet (scoped under the ``.eoa-report``
    class so it stays inert if this markup is instead embedded as a fragment, e.g. the Morning
    screen's ``ReportBody`` component), not just an inner ``<div>`` fragment (F10/U1: the file at
    ``output/reports/*.html`` is also served and opened directly via the report's "html" download
    link, where it must stand on its own). ``include_toc`` (see :func:`build_docx`) adds a simple
    anchor-based table of contents; the daily report leaves it off.
    """
    deep_search = deep_search or []
    open_clarifications = open_clarifications or []
    extra_sections = extra_sections or []
    resolved_title = title_text or TITLE_TEXT
    item_by_n = {it.get("n"): it for it in items}

    def cite_links(text: str) -> str:
        raw = text or ""
        out: list[str] = []
        pos = 0
        for m in _CITATION_RE.finditer(raw):
            out.append(_bidi_html(raw[pos : m.start()]))
            n = int(m.group(1))
            target = "#src-" + str(n) if n in item_by_n else "#"
            out.append(f'<a href="{target}" class="cite">[{n}]</a>')
            pos = m.end()
        out.append(_bidi_html(raw[pos:]))
        return "".join(out)

    open_points = list(draft.open_points_he or [])
    open_points += [c.get("question") or "" for c in open_clarifications if c.get("question")]

    headings = _planned_headings(draft, events, deep_search, open_points, extra_sections, tables)
    heading_ids = [f"sec-{i}" for i in range(len(headings))]
    id_iter = iter(heading_ids)

    def h2(title: str) -> str:
        hid = next(id_iter, None)
        attr = f' id="{hid}"' if hid else ""
        return f"<h2{attr}>{_bidi_html(title)}</h2>"

    parts = [f"<h1>{_bidi_html(resolved_title)}</h1>"]
    if period_end is not None:
        parts.append(f'<p class="date">{_bidi_html(hebrew_date_str(period_end))}</p>')
    warning = _qa_warning_line(qa)
    if warning:
        parts.append(f'<p class="qa-warning"><strong>{html.escape(warning)}</strong></p>')

    if include_toc:
        toc_items = "".join(
            f'<li><a href="#{hid}">{_bidi_html(title)}</a></li>'
            for title, hid in zip(headings, heading_ids, strict=True)
            if title
        )
        parts.append(f'<nav class="toc"><h2>תוכן עניינים</h2><ul>{toc_items}</ul></nav>')

    parts.append(h2("תקציר מנהלים"))
    parts.append(f"<p>{cite_links(draft.exec_summary_he or 'אין תקציר לתקופה זו.')}</p>")

    _extra_sections_html(parts, extra_sections, "after_summary", h2)

    for section in draft.sections:
        parts.append(h2(section.title_he))
        for para in _split_paragraphs(section.prose_he):
            parts.append(f"<p>{cite_links(para)}</p>")

    if events:
        parts.append(h2("טבלת אירועים עסקיים"))
        parts.append(
            "<table><thead><tr><th>תאריך</th><th>סוג</th><th>צדדים</th>"
            "<th>לקוח/תוכנית</th><th>סכום</th><th>מקור</th></tr></thead><tbody>"
        )
        for ev in events:
            n = ev.get("n")
            src = (
                f'<a href="#src-{n}" class="cite">[{n}]</a>'
                if n is not None
                else _bidi_html(source_label(ev.get("source_name"), ev.get("item_url")))
            )
            parts.append(
                "<tr>"
                f"<td>{html.escape(fmt_date(ev.get('date')))}</td>"
                f"<td>{html.escape(_EVENT_KIND_LABELS_HE.get(ev.get('kind'), ev.get('kind') or '—'))}</td>"
                f"<td>{_bidi_html(', '.join(ev.get('parties') or []) or '—')}</td>"
                f"<td>{_bidi_html(ev.get('customer') or ev.get('program') or '—')}</td>"
                f"<td>{html.escape(fmt_amount(ev))}</td>"
                f"<td>{src}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")

    if deep_search:
        parts.append(h2("חקירות עומק"))
        parts.append("<ul>")
        for entry in deep_search:
            heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
            outcome = _OUTCOME_LABELS_HE.get(entry.get("outcome"), entry.get("outcome") or "—")
            parts.append(
                f"<li><strong>{_bidi_html(heading)}</strong> — {html.escape(outcome)}: "
                f"{_bidi_html(entry.get('answer_he', ''))}</li>"
            )
        parts.append("</ul>")

    if open_points:
        parts.append(h2("נקודות פתוחות"))
        parts.append("<ul>")
        parts += [f"<li>{_bidi_html(p)}</li>" for p in open_points]
        parts.append("</ul>")

    if draft.outlook_he:
        parts.append(h2("מבט קדימה"))
        parts.append(f"<p>{_bidi_html(draft.outlook_he)}</p>")

    _extra_sections_html(parts, extra_sections, "after_outlook", h2)
    _tables_html(parts, tables or [], h2)

    parts.append(h2("נספח מקורות"))
    parts.append(
        "<table><thead><tr><th>#</th><th>כותרת</th><th>מקור</th><th>תאריך</th>"
        "<th>קישור</th></tr></thead><tbody>"
    )
    for it in sorted(items, key=lambda x: x.get("n") or 0):
        url = it.get("url") or ""
        link = _html_link(url) if url else "—"
        parts.append(
            f'<tr id="src-{it.get("n")}">'
            f"<td>{it.get('n')}</td>"
            f"<td>{_bidi_html(it.get('title') or '—')}</td>"
            f"<td>{_bidi_html(source_label(it.get('source_name'), url))}</td>"
            f"<td>{html.escape(fmt_date(it.get('published_at')))}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")

    body = "\n".join(parts)
    return (
        "<!doctype html>\n"
        '<html lang="he" dir="rtl">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(resolved_title)}</title>\n"
        f"<style>{_EOA_HTML_STYLE}</style>\n"
        "</head>\n"
        "<body>\n"
        f'<div class="eoa-report" dir="rtl" lang="he">\n{body}\n</div>\n'
        "</body>\n"
        "</html>\n"
    )
