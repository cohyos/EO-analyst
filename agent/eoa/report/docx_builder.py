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
from eoa.report.textnorm import canonicalize_hebrew_names_deep, trim_at_word_boundary

# -- constants -----------------------------------------------------------------

HEBREW_FONT = "David"
HEBREW_FONT_FALLBACK = "Arial"
BODY_SIZE_PT = 11

TITLE_TEXT = "דוח יומי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_EVENT_KIND_LABELS_HE = {
    "financial_results": "דיווח פיננסי",
    "appointment": "מינוי",
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
    # DS3/P7 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.6): a technically/security-blocked
    # investigation never actually ran -- distinct from `not_found` ("investigated in full, found
    # nothing"). This label alone already keeps `eoa.qa.d4_investigations`'s
    # `blocked_distinct_from_not_found` check happy (it only flags an entry labeled *`not_found`*
    # that also carries a block-signal word).
    "blocked": "נחסם (לא נחקר בפועל)",
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


_MD_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def _split_md_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


_ROW_CITE_RE = re.compile(r"\[(\d+)\]")


# -- Round 5 P4 (2026-09-06) row-carried metadata (W5 trend cross-reference) --------------------
#
# A table row has always been a plain ``list[Any]`` of cell values (positional, matching
# ``headers``) everywhere in this codebase (``weekly.py``/``monthly.py``/``bd_territory.py``/
# ``tech_watch.py``/``israel_section.py``/``patents/survey.py``) -- ``_row_cells``/
# ``_row_related_trend`` below additionally accept a row shaped
# ``{"cells": [...], "related_trend_he": "<trend title>"}`` so a collector can *optionally* attach
# a "this row relates to trend X" note without any change to the plain-list shape every existing
# caller still uses (a no-op for all of them: ``_row_related_trend`` returns ``None`` for a plain
# list row).


def _row_cells(row: Any) -> list[Any]:
    """The positional cell values of a table row -- ``row`` itself when it's a plain list (every
    existing caller), or ``row["cells"]`` for the new optional dict-with-metadata row shape."""
    if isinstance(row, dict):
        return list(row.get("cells") or [])
    if isinstance(row, list):
        return row
    return [row]


def _row_related_trend(row: Any) -> str | None:
    """The row's own ``related_trend_he`` (W5) when it is the new dict-with-metadata shape;
    ``None`` for a plain-list row (the no-op case)."""
    if isinstance(row, dict):
        return row.get("related_trend_he") or None
    return None


def _apply_row_trend_note(cells: list[Any], trend: str | None) -> list[Any]:
    """Fold a row's ``related_trend_he`` (W5) into its last cell as a "(מגמה: …)" suffix -- the
    same small, non-structural note in all three outputs (md/html table cells and docx table
    cells can't otherwise carry per-row metadata without reshaping the table itself). A no-op
    when ``trend`` is falsy, so every existing table (which never sets it) renders unchanged."""
    if not trend:
        return cells
    cells = list(cells) if cells else [None]
    last = cells[-1]
    last_text = "—" if last is None else str(last)
    cells[-1] = f"{last_text} (מגמה: {trend})"
    return cells


# Round-14 (CR-editing.md, "tables ... cells > ~25 words"): a generic-table cell over this length
# is trimmed at render time -- applied *after* `_row_identity`/dedup (never inside `_row_cells`
# itself) so cross-table row-dedup keeps comparing full, untrimmed text.
_CELL_MAX_CHARS = 220
_CELL_TRIM_SUFFIX = " … (פירוט במקורות)"


def _trim_cell_text(value: Any) -> Any:
    """Cap an over-long table-cell string at :data:`_CELL_MAX_CHARS`, cutting at a word boundary
    (never mid-word/mid-token, unlike a bare ``value[:n]`` slice) and pointing the reader at the
    row's own citations instead of leaving a bare mid-sentence "…". A no-op for non-string values,
    short strings, and URLs (URLs are rendered as hyperlinks, never trimmed)."""
    if not isinstance(value, str) or _looks_like_url(value):
        return value
    return trim_at_word_boundary(value, _CELL_MAX_CHARS, suffix=_CELL_TRIM_SUFFIX)


def _trim_row_cells(cells: list[Any]) -> list[Any]:
    """:func:`_trim_cell_text` applied to every cell in a row -- the one call site all three
    renderers (docx/md/html) share, right before a row's cells are written to the page."""
    return [_trim_cell_text(v) for v in cells]


#: Round-15 (PL-REPORT-FIX, user screenshot 2026-09-08 20:30): a table of this many rows or fewer
#: renders no synthesized row-count caption at all -- see :func:`_table_caption_text`.
_CAPTION_MIN_ROWS_FOR_SYNTHESIS = 3


def _table_caption_text(tbl: dict[str, Any]) -> str | None:
    """A short caption line for a rendered table (defect: "missing table captions",
    CR-editing.md) -- the author-provided ``note_he`` when the table already carries one,
    otherwise a synthesized row-count line so a reader always knows a table's size before reading
    it. ``None`` (render nothing) for a table with zero rows (nothing to caption) or with
    :data:`_CAPTION_MIN_ROWS_FOR_SYNTHESIS` rows or fewer (a bare "3 שורות" under a table the
    reader can already see is 3 rows long is noise, not information).

    Round-15 (PL-REPORT-FIX, user screenshot 2026-09-08 20:30): the synthesized caption used to
    wrap the count in literal ASCII parentheses (``"(3 שורות)"``). Inside an RTL Hebrew paragraph,
    ASCII ``(``/``)`` are *mirrored* characters under the Unicode bidi algorithm, so that caption
    rendered flipped in the UI (``")שורות 3("``) -- confirmed live in the pl_targeting_pods
    2026-09-08 report. The synthesized form now emits plain "N שורות" with no bracketing at all; an
    author-provided ``note_he`` (free text, not this synthesized shape) is returned unchanged."""
    note = tbl.get("note_he")
    if note:
        return note
    n = len(tbl.get("rows") or [])
    if n <= _CAPTION_MIN_ROWS_FOR_SYNTHESIS:
        return None
    return f"{n} שורות"


def _row_identity(row: Any) -> str:
    """Identity of a table row across a report: the sorted set of its [n] citations when it has
    any (the same item/event cited in two tables), else the whitespace-normalised cell text."""
    cells = _row_cells(row)
    cites = sorted({m for cell in cells for m in _ROW_CITE_RE.findall(str(cell))})
    if cites:
        return "n:" + ",".join(cites)
    return "t:" + " ".join(" ".join(str(c) for c in cells).split()).casefold()


def dedupe_rows_across_tables(tables: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """User finding W28 (2026-09-06 21:30): the report's tables repeated the same rows table
    after table (an item in "זכיות וחוזים" again in "תחרות ומתחרים", an event in the events
    table again in a territory table). A row is rendered once, in the first table that carries
    it; a later table drops it and gains a note "N שורות כבר הופיעו בטבלאות קודמות". Tables with
    fewer than two cells per row (single-column lists) are left alone."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for tbl in tables or []:
        rows = list(tbl.get("rows") or [])
        if tbl.get("no_dedupe"):
            # actions / forecasts cite the items already shown above by design
            out.append(tbl)
            continue
        if not rows or any(len(_row_cells(r)) < 2 for r in rows):
            out.append(tbl)
            for r in rows:
                seen.add(_row_identity(r))
            continue
        kept: list[Any] = []
        dropped = 0
        for r in rows:
            key = _row_identity(r)
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            kept.append(r)
        if dropped:
            note = (tbl.get("note_he") or "").strip()
            # Round-14 (CR-editing.md, "Hebrew grammar and register"): "1 שורות כבר הופיעו" is a
            # number/gender-agreement error (שורה is feminine singular; a bare count-prefix template
            # only ever produces the plural verb/noun form, wrong for count == 1) -- singular
            # phrasing for exactly one dropped row, the existing plural phrasing otherwise.
            extra = (
                "שורה אחת כבר הופיעה בטבלה קודמת בדוח ולא חזרה כאן."
                if dropped == 1
                else f"{dropped} שורות כבר הופיעו בטבלאות קודמות בדוח ולא חזרו כאן."
            )
            tbl = {**tbl, "rows": kept, "note_he": f"{note} {extra}".strip()}
        out.append(tbl)
    return out


# R6-weekly (docs/qa/loop/round_6_fixes.md): the weekly/monthly reports had grown to 33-36 ``##``
# headings (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.2 budgets ~14, the D6 heading-budget check
# allows 16) -- most of them one-per-item lists (a trend, a domain section, a tech-radar table, a
# patents table, ...) that read better grouped under one parent heading. ``group_he`` is an
# optional key on an ``extra_sections`` dict OR a ``tables`` dict: every entry sharing the same
# non-empty ``group_he`` value renders as ONE ``##``/Heading-1 parent (the group_he text itself)
# with each member indented one level (``###``/Heading-2) under it, instead of each getting its own
# top-level heading. A member whose own ``title_he`` equals the group's ``group_he`` (the "primary"
# item in the group, e.g. the Israel report's pre-existing merged table) renders directly under the
# parent with no ``###`` of its own -- everyone else in the group gets one. Entries with no
# ``group_he`` (every existing caller, unchanged) render exactly as before. A ``tables`` entry may
# also be a *prose* member -- no ``headers``/``rows``, just a ``body_he`` string -- so a
# previously-``extra_sections`` item (e.g. the patents/IP writeup) can join a group whose other
# members are real tables (which must stay in the ``tables`` list, not become inline Markdown, to
# keep :func:`dedupe_rows_across_tables`'s cross-table row de-duplication and per-row
# ``related_trend_he`` notes working on it).
def _group_entries(
    entries: list[dict[str, Any]], position: str | None = None
) -> list[dict[str, Any] | list[dict[str, Any]]]:
    """``entries`` (an ``extra_sections`` or ``tables`` list) with every run of same-``group_he``
    items collapsed into one ``list[dict]`` (first-appearance order) alongside the untouched,
    ungrouped dicts -- see module note above. ``position`` (``extra_sections`` only) first filters
    to ``(entry.get("position") or "after_summary") == position``; leave it ``None`` for ``tables``,
    which carry no ``position`` field of their own."""
    if position is not None:
        entries = [e for e in entries if (e.get("position") or "after_summary") == position]
    groups: dict[str, list[dict[str, Any]]] = {}
    out: list[Any] = []
    for entry in entries:
        group_he = entry.get("group_he")
        if not group_he:
            out.append(entry)
            continue
        members = groups.get(group_he)
        if members is None:
            members = [entry]
            groups[group_he] = members
            out.append(members)  # same list object `members` -- later appends show up in `out` too
        else:
            members.append(entry)
    return out


def _group_title(entry: dict[str, Any] | list[dict[str, Any]]) -> str:
    """The one heading text `entry` (a :func:`_group_entries` element) contributes to the report:
    a group's own ``group_he``, or an ungrouped entry's ``title_he``."""
    if isinstance(entry, list):
        return entry[0].get("group_he") or ""
    return entry.get("title_he") or ""


def _md_blocks(text: str) -> list[tuple[str, Any]]:
    """Split a section body into render blocks: ``("table", (headers, rows))`` for a Markdown
    pipe table, ``("bullets", [items])`` for a run of ``- `` lines, ``("para", text)`` otherwise.
    User finding 2026-09-06 evening ("העיצוב?!"): the acquisition-watch section (A16) and the
    payload-price table (A17) hand the renderer Markdown table bodies, and both the HTML and the
    docx paths pasted them as one prose paragraph full of pipes. Every extra-section body now goes
    through this splitter, so a data section can be authored once in Markdown and render as a real
    table/list in all three outputs."""
    blocks: list[tuple[str, Any]] = []
    lines = (text or "").splitlines()
    i = 0
    para: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(("para", " ".join(x.strip() for x in para).strip()))
            para.clear()

    while i < len(lines):
        stripped = lines[i].strip()
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if stripped.startswith("|") and _MD_TABLE_SEP_RE.match(next_line):
            flush_para()
            headers = _split_md_cells(stripped)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_md_cells(lines[i])
                if len(cells) < len(headers):
                    cells = cells + [""] * (len(headers) - len(cells))
                rows.append(cells[: len(headers)])
                i += 1
            blocks.append(("table", (headers, rows)))
            continue
        if stripped.startswith(("- ", "* ")):
            flush_para()
            items: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append(("bullets", items))
            continue
        if not stripped:
            flush_para()
            i += 1
            continue
        para.append(stripped)
        i += 1
    flush_para()
    return blocks or [("para", "")]


# -- goal-1 (2026-09-06) structured-sentence rendering helpers ---------------------------------
#
# ``eoa.llm.schemas.analysis.DailyReportDraft`` moved from free-text prose (with the model
# expected to type its own "[n]" markers) to a structured sentence-per-claim schema
# (``Sentence``: ``text_he`` + non-empty ``cites``); these helpers are what actually emit the
# "[n]" markers now, deterministically, from ``cites`` -- the model never writes a citation
# bracket in its own text. ``eoa.report.weekly``/``monthly``/``bd_territory`` still hand this
# module the legacy free-text shape (``exec_summary_he`` / ``sections[].prose_he`` /
# ``outlook_he``) unchanged, so every helper below is duck-typed to handle both.


def _is_legacy_prose_draft(draft: Any) -> bool:
    """True for the legacy free-text draft shape (weekly/monthly/bd_territory), false for the
    goal-1 structured :class:`DailyReportDraft`."""
    return hasattr(draft, "exec_summary_he")


def _render_sentence(sentence: Any) -> str:
    """One ``Sentence``/``OutlookIndicator``-like object (``text_he`` + ``cites``) rendered to
    display text with its "[n]" markers appended deterministically from ``cites``."""
    text = (getattr(sentence, "text_he", "") or "").rstrip()
    cites = getattr(sentence, "cites", None) or []
    markers = "".join(f"[{n}]" for n in cites)
    return f"{text} {markers}".rstrip() if markers else text


def _render_sentences(sentences: Any) -> str:
    return " ".join(_render_sentence(s) for s in sentences or [])


def _section_prose(section: Any) -> str:
    """Prose text for one report section: the legacy free-text ``prose_he`` string, or the goal-1
    structured ``sentences`` list rendered as one flowing paragraph."""
    if hasattr(section, "sentences"):
        return _render_sentences(section.sentences)
    return section.prose_he


def _draft_exec_summary_text(draft: Any) -> str:
    if _is_legacy_prose_draft(draft):
        return draft.exec_summary_he
    return _render_sentences(getattr(draft, "exec_summary", None))


def _draft_outlook_text(draft: Any) -> str:
    if _is_legacy_prose_draft(draft):
        return draft.outlook_he or ""
    return " ".join(_render_outlook_indicator(ind) for ind in getattr(draft, "outlook", None) or [])


def _draft_analyst_note_text(draft: Any) -> str:
    """'הערכת האנליסט' (goal 1) -- empty for a draft that doesn't carry this field at all (every
    legacy draft type)."""
    note = getattr(draft, "analyst_note_he", None)
    sentences = getattr(note, "sentences_he", None) if note is not None else None
    text = " ".join(sentences) if sentences else ""
    return "" if _is_junk_note(text) else text


_HEBREW_WORD_RE = re.compile(r"[א-ת]{2,}")


def _is_junk_note(text: str) -> bool:
    """Round-3 (bd_il 2026-09-06 16:06): a structured draft's analyst note rendered as
    ``"]}, "`` -- a JSON fragment the model left in the field. The note is uncited by design, so
    the citation QA never sees it; this is the one deterministic gate it gets: drop it when it
    carries JSON punctuation or fewer than two Hebrew words."""
    t = (text or "").strip()
    if not t:
        return True
    if any(ch in t for ch in "{}[]"):
        return True
    return len(_HEBREW_WORD_RE.findall(t)) < 2


def _draft_system_note_text(draft: Any) -> str:
    """A deterministic, non-LLM-authored notice (goal 1) -- e.g. "no items this period" or the
    two-failure QA fallback message. Empty for a draft that doesn't carry this field."""
    return getattr(draft, "system_note_he", "") or ""


def _exec_summary_display_text(draft: Any) -> str | None:
    """The "תקציר מנהלים" paragraph to render, or ``None`` to render nothing under that heading.

    Round-14 (CR-editing.md): the "אין תקציר לתקופה זו." placeholder used to appear even when
    ``system_note_he`` (rendered right below it) *did* carry real substance -- e.g.
    pl_mws_eo_2026-09-07.md read "אין תקציר לתקופה זו." immediately followed by a system note
    listing 3 patents found in the period, flatly contradicting the placeholder's own claim that
    there was nothing to summarize. The placeholder is now shown only when there is truly nothing
    to say anywhere in this slot -- no exec_summary sentence *and* no system note either; when a
    system note exists, it alone carries this slot's content (rendered immediately after, by every
    caller of this function)."""
    text = _draft_exec_summary_text(draft)
    if text:
        return text
    if _draft_system_note_text(draft):
        return None
    return "אין תקציר לתקופה זו."


# -- Round 5 P4 (2026-09-06) BLUF / likelihood-confidence / assumptions ------------------------
#
# docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.1#1 (BLUF), 3.1#9 (likelihood/confidence split), 3.4#10
# (assumption <-> falsifier). None of ``bluf``/``OutlookIndicator.likelihood``/
# ``OutlookIndicator.confidence_level``/``confidence_basis_he``/``assumptions`` exist on any draft
# schema yet (P3, ``eoa.llm.schemas.analysis``, landing separately this same evening) -- every
# helper below is ``getattr``-guarded against the field's absence so this module works unchanged
# today and picks up the new fields the moment P3 adds them, with zero further changes here.


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute or dict-key access, whichever ``obj`` supports -- P3's new schema fields will
    almost certainly be pydantic model attributes (the codebase convention), but this stays
    tolerant of a plain-dict shape too since nothing here can see the real schema yet."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _draft_bluf_info(draft: Any) -> tuple[list[Any], bool]:
    """``(sentences, is_system_built)`` for the "שורה תחתונה" (BLUF) section.

    - ``draft.bluf`` (P3, ``list[Sentence]``) when present and non-empty -- the model-authored
      BLUF, returned as-is (``is_system_built=False``).
    - Otherwise, only for the daily report's own zero-narrative deterministic fallback
      (``eoa.report.daily._deterministic_fallback_draft``: no ``bluf`` field yet, empty
      ``sections``, a non-empty ``system_note_he`` explaining the model's draft was dropped, and
      an ``exec_summary`` built straight from the top-scored item/event/Israel-relevant data) --
      the top 1-2 ``exec_summary`` sentences double as a synthesized BLUF
      (``is_system_built=True``) so the reader still gets a bottom line even with no model text.
    - ``([], False)`` for every other shape (legacy free-prose drafts, or a structured draft with
      real section content but no ``bluf`` -- P3 hasn't reached that report type yet).
    """
    bluf = getattr(draft, "bluf", None)
    if bluf:
        return list(bluf), False
    if _is_legacy_prose_draft(draft):
        return [], False
    system_note = _draft_system_note_text(draft)
    exec_summary = getattr(draft, "exec_summary", None) or []
    if system_note and not draft.sections and exec_summary:
        return list(exec_summary[:2]), True
    return [], False


_SYSTEM_BUILT_BLUF_PREFIX_HE = "(שורה תחתונה אוטומטית מהנתונים, ללא ניסוח מודל) "


def _draft_bluf_text(draft: Any) -> str:
    """The rendered BLUF text (deterministic ``[n]`` markers from ``cites``, same as any other
    ``Sentence`` list) -- empty string when there is nothing to show (see
    :func:`_draft_bluf_info`)."""
    sentences, is_system_built = _draft_bluf_info(draft)
    if not sentences:
        return ""
    text = _render_sentences(sentences)
    return f"{_SYSTEM_BUILT_BLUF_PREFIX_HE}{text}" if is_system_built else text


_CONFIDENCE_LEVEL_LABELS_HE = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}


def _format_likelihood(value: Any) -> str:
    """``value`` as a Hebrew percentage: a ``0..1`` float is treated as a ratio, anything already
    ``> 1`` (or a non-numeric value the model returned as a string) is shown as-is."""
    if isinstance(value, int | float):
        pct = value * 100 if 0 <= value <= 1 else value
        return f"{pct:.0f}%"
    return str(value)


def _format_confidence_level(value: Any) -> str:
    if isinstance(value, str):
        return _CONFIDENCE_LEVEL_LABELS_HE.get(value.strip().lower(), value)
    return str(value)


def _render_outlook_indicator(indicator: Any) -> str:
    """One ``OutlookIndicator`` rendered to display text (docs/REPORT_TEMPLATE_BENCHMARK.md
    3.1#9): the base sourced/assessment sentence, plus -- only when the field exists on this
    indicator (P3) -- "סבירות: X%" and "ביטחון: <רמה> (<בסיס>)" as two separate clauses, split by
    a semicolon so ``eoa.qa.d6_daily_report``'s deterministic clause-boundary check
    (``_CLAUSE_SPLIT_RE = re.compile(r"[.,;]")``) never sees both Hebrew keywords in the same
    clause. A legacy indicator without these fields renders exactly as before (no change)."""
    base = _render_sentence(indicator)
    likelihood = _field(indicator, "likelihood")
    confidence_level = _field(indicator, "confidence_level")
    if likelihood is None and confidence_level is None:
        return base
    clauses = []
    if likelihood is not None:
        clauses.append(f"סבירות: {_format_likelihood(likelihood)}")
    if confidence_level is not None:
        basis = _field(indicator, "confidence_basis_he")
        conf_clause = f"ביטחון: {_format_confidence_level(confidence_level)}"
        if basis:
            conf_clause += f" ({basis})"
        clauses.append(conf_clause)
    base = base.rstrip(". ")
    return f"{base}. {'; '.join(clauses)}."


def _draft_assumptions(draft: Any) -> list[Any]:
    """``draft.assumptions`` (P3, docs/REPORT_TEMPLATE_BENCHMARK.md 3.4#10 "הנחה <-> הפרכה") when
    present and non-empty; ``[]`` for every draft that doesn't carry this field yet."""
    return list(getattr(draft, "assumptions", None) or [])


def _render_assumption(assumption: Any) -> str:
    """One assumption/falsifier pair rendered as one bullet line: "<assumption_he> — הפרכה:
    <falsifier_he> [n]" -- ``cites`` (when present) render as deterministic ``[n]`` markers, same
    convention as every other cited claim in this module. Wording note (P6 cross-team discovery,
    docs/MODULES.md "Round 5 P6"): "הפרכה" (not the grammatically-also-valid "יופרך אם") is
    required so the rendered line contains a substring `eoa.qa.d7_bd_report`'s (and, by the same
    duplicated-check convention, presumably `d6_daily_report`'s) deterministic
    ``assumptions_falsifiers_list_present``/equivalent check actually looks for
    (`_FALSIFIER_KEYWORDS_HE = ("פריך", "הפרכ", "falsif")` -- "יופרך" contains neither "פריך" nor
    "הפרכ" as a substring, "הפרכה" contains "הפרכ")."""
    assumption_he = (_field(assumption, "assumption_he", "") or "").rstrip()
    falsifier_he = (_field(assumption, "falsifier_he", "") or "").rstrip()
    cites = _field(assumption, "cites", None) or []
    markers = "".join(f"[{n}]" for n in cites)
    text = f"{assumption_he} — הפרכה: {falsifier_he}" if falsifier_he else assumption_he
    return f"{text} {markers}".rstrip() if markers else text


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


# -- Round 5 P4 (2026-09-06) source-reliability appendix column ---------------------------------
#
# docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 item 12: the sources appendix gains a "אמינות" column.
# ``sources.reliability`` (a 1-5 primary/secondary-ish scale, ``db/migrations/versions/0001_core.py``)
# and the per-date ``source_reliability`` table (rolling ``score``, confirmed/contradicted counts)
# both already exist in the DB -- populating them onto each item dict is a collector-side job
# (``daily.py``/``weekly.py``/etc., out of this package's file scope). This renderer only needs an
# *optional* ``reliability`` key on a registry item; "—" when it's absent, exactly like every other
# optional appendix field in this module.
_RELIABILITY_KIND_LABELS_HE = {"primary": "מקור ראשוני", "secondary": "מקור משני"}


_SOURCE_RELIABILITY_CACHE: dict[str, int] | None = None


def _source_reliability_map() -> dict[str, int]:
    """``sources.name -> sources.reliability`` (1-5), loaded once per process; an unreachable DB
    (unit tests, offline renders) yields an empty map and the appendix keeps rendering "—"."""
    global _SOURCE_RELIABILITY_CACHE
    if _SOURCE_RELIABILITY_CACHE is None:
        try:
            from eoa.db import connection

            with connection(timeout=5) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT name, reliability FROM sources WHERE name IS NOT NULL AND reliability IS NOT NULL"
                )
                rows = cur.fetchall()
                _SOURCE_RELIABILITY_CACHE = {
                    str(r["name"] if isinstance(r, dict) else r[0]): int(
                        r["reliability"] if isinstance(r, dict) else r[1]
                    )
                    for r in rows
                }
        except Exception:
            _SOURCE_RELIABILITY_CACHE = {}
    return _SOURCE_RELIABILITY_CACHE


def _reliability_for(item: dict[str, Any]) -> Any:
    """Round-6 judge (D9): the "אמינות" column existed but every cell was "—" because no collector
    attached a value. Use the item's own ``reliability`` when present, else derive it from the
    ``sources.reliability`` scale (1-5) of the item's source: >= 4 -> primary, else secondary,
    score normalised to 0-1."""
    if item.get("reliability") is not None:
        return item["reliability"]
    raw = item.get("source_reliability")
    if raw is None:
        name = item.get("source_name")
        raw = _source_reliability_map().get(str(name)) if name else None
    if raw is None:
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return {"kind": "primary" if val >= 4 else "secondary", "score": round(val / 5, 2), "label": None}


def reliability_label(value: Any) -> str:
    """The "אמינות" appendix cell for one item's optional ``reliability`` value.

    Accepts three shapes so a collector can populate whatever it already has cheaply:

    - ``None`` / missing -- "—" (the DB doesn't carry reliability data for this source yet).
    - a plain ``str`` -- rendered verbatim (a collector that already composed its own label).
    - a ``dict`` -- ``{"kind": "primary"|"secondary", "score": float|None, "label": str|None}``,
      composed here as "<מקור ראשוני/משני> · <label> · <score>" (whichever parts are present);
      ``kind`` maps to "מקור ראשוני"/"מקור משני" (the reliability-scale primary/secondary split),
      ``score`` is the rolling ``source_reliability.score`` (or ``sources.reliability``,
      normalised to 0-1) formatted to two decimals, ``label`` an already-Hebrew free-text label.
    """
    if value is None:
        return "—"
    if isinstance(value, str):
        return value.strip() or "—"
    if isinstance(value, dict):
        parts: list[str] = []
        kind_he = _RELIABILITY_KIND_LABELS_HE.get(value.get("kind"))
        if kind_he:
            parts.append(kind_he)
        label = value.get("label")
        if label:
            parts.append(str(label))
        score = value.get("score")
        if isinstance(score, int | float):
            parts.append(f"{float(score):.2f}")
        return " · ".join(parts) if parts else "—"
    return "—"


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

    Round 16 (BIDI-REPORTS.md): the single joining space between a Hebrew run and an adjacent
    Latin/digit run is never left trapped as leading/trailing whitespace *inside* the Latin/digit
    run (see :func:`_rebalance_boundary_whitespace`, applied to this function's result below). An
    HTML ``<bdi dir="ltr">``/OOXML-run isolate is atomic: a boundary space left inside it does not
    act as a normal separator, and the two words either side render glued together with no visible
    gap at all -- e.g. "3 " (trailing space) inside an isolate immediately followed by "דולר"
    renders "3דולר", not "3 דולר". Confirmed empirically in a live browser (character-level
    ``getBoundingClientRect`` measurement, not just visual inspection): moving the same space to
    sit *after* the isolate instead of inside it eliminates the glue in every case checked,
    including the harder "opening bracket immediately before an isolate" case ("(Axon Vision, "
    followed by Hebrew) -- there the *bracket itself* ends up pixel-adjacent to the isolate's
    *trailing* character rather than its leading one (an isolate's own internal layout is fixed
    left-to-right regardless of which neighbour is logically nearer), but since a bracket sitting
    directly against a letter with no gap is normal, unremarkable typography (unlike two *letters*
    from different scripts touching with no gap at all), only the whitespace side of this actually
    needs correcting -- rebalancing it alone was verified to produce zero letter-to-letter glues
    across every reported symptom phrase, so no separate bracket-specific handling was added here.
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
    return _rebalance_boundary_whitespace(runs)


def _rebalance_boundary_whitespace(runs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Move whitespace sitting at the edge of an 'other' (Latin/digit) run into the adjacent 'he'
    run instead, so the joining space between a Hebrew word and an LTR run is never left *inside*
    the isolate/run :func:`split_runs` emits for the LTR side -- see that function's docstring for
    why a boundary space trapped inside the isolate renders glued rather than separated (round 16,
    BIDI-REPORTS.md, confirmed empirically in a live browser: "3 " (trailing space) inside a
    ``dir="ltr"`` isolate immediately followed by "דולר" renders as "3דולר", no visible gap; moving
    the same space to sit *after* the isolate instead of inside it renders correctly).

    Whitespace *inside* an LTR run that is not at a Hebrew boundary (e.g. the space between "Axon"
    and "Vision") is untouched -- :func:`split_runs`'s runs already strictly alternate 'he'/'other'
    (a class only changes on a real script transition), so this only ever touches a run's own
    leading/trailing edge, never text in its interior.
    """
    if len(runs) < 2:
        return runs
    runs = list(runs)
    for i, (cls, chunk) in enumerate(runs):
        if cls != "other" or not chunk:
            continue
        if i > 0 and runs[i - 1][0] == "he":
            stripped = chunk.lstrip()
            lead = chunk[: len(chunk) - len(stripped)]
            if lead:
                runs[i - 1] = ("he", runs[i - 1][1] + lead)
                chunk = stripped
        if i + 1 < len(runs) and runs[i + 1][0] == "he":
            stripped = chunk.rstrip()
            trail = chunk[len(stripped) :]
            if trail:
                runs[i + 1] = ("he", trail + runs[i + 1][1])
                chunk = stripped
        runs[i] = (cls, chunk)
    return [(cls, chunk) for cls, chunk in runs if chunk]


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
    headers = ["#", "כותרת", "מקור", "אמינות", "תאריך", "קישור"]
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
        _fill_cell(row[3], reliability_label(_reliability_for(it)))
        _fill_cell(row[4], fmt_date(it.get("published_at")))
        url = it.get("url") or ""
        link_p = row[5].paragraphs[0]
        _paragraph_rtl_right(link_p)
        if url:
            add_hyperlink(link_p, url, url)
        else:
            _emit_mixed_runs(link_p, "—", size_pt=10)


def _add_generic_table_body(doc: DocxDocument, headers: list[str], rows: list[list[Any]]) -> None:
    """The table itself (no heading) for a deterministic, non-citation RTL table — the 90-day
    conference lookahead (weekly) and the players-map/top-events/24-month-horizon tables (monthly).
    Split out so ``build_docx`` can add the Heading-1 itself
    (wrapped in a TOC bookmark when ``include_toc`` is set) immediately before the table.

    ``rows`` may hold a plain ``list[Any]`` (every existing caller) or the newer
    ``{"cells": [...], "related_trend_he": "..."}`` dict shape (W5, see :func:`_row_cells`); a
    row's ``related_trend_he`` folds into its last cell as a "(מגמה: …)" suffix
    (:func:`_apply_row_trend_note`)."""
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    _set_table_rtl(table)
    for cell, text in zip(table.rows[0].cells, headers, strict=True):
        _fill_cell(cell, text, bold=True)
    _shade_header_row(table)
    for row_values in rows:
        cell_values = _trim_row_cells(
            _apply_row_trend_note(_row_cells(row_values), _row_related_trend(row_values))
        )
        row = table.add_row().cells
        for cell, value in zip(row, cell_values, strict=True):
            if _looks_like_url(value):
                link_p = cell.paragraphs[0]
                _paragraph_rtl_right(link_p)
                add_hyperlink(link_p, str(value), str(value))
            else:
                _fill_cell(cell, "—" if value is None else str(value))


def _add_deep_search_section(doc: DocxDocument, deep_search: list[dict], items: list[dict]) -> None:
    # R10-links: `items` is the same numbered registry `_add_sources_appendix` renders below --
    # a deep-search entry's trigger item is only ever a report's own item (R9's
    # `_filter_deep_search_to_items_included`), so when it's present it already carries an `n`.
    item_by_id = {it.get("id"): it for it in items if it.get("id") is not None}
    for entry in deep_search:
        heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
        add_mixed_paragraph(doc, heading, style="Heading 2")
        outcome_key = entry.get("outcome")
        outcome = _OUTCOME_LABELS_HE.get(outcome_key, outcome_key or "—")
        confidence = entry.get("confidence")
        conf_str = f"{confidence:.0%}" if isinstance(confidence, int | float) else "—"
        add_mixed_paragraph(doc, f"תוצאה: {outcome} | רמת ביטחון: {conf_str}", size_pt=10)
        # R10-links: "חקירה #<id>" -- plain text; a docx has no live app to open
        # `/investigations/<id>` in, unlike the md/html renderers below, which link it for real.
        # "פריט מקור [n]" -- when the trigger item has a registry number, written as a plain `[n]`
        # token so `add_mixed_paragraph`'s own citation handling (`_emit_mixed_runs`/
        # `_CITATION_RE`) turns it into the same real internal jump-to-appendix every other `[n]`
        # in this document gets, for free -- no direct `add_citation_run` call needed here.
        job_id = entry.get("job_id")
        if job_id is not None:
            trigger_item_id = entry.get("item_id") or entry.get("trigger_item_id")
            trigger_n = item_by_id.get(trigger_item_id, {}).get("n") if trigger_item_id is not None else None
            meta_text = f"חקירה #{job_id}"
            if trigger_n is not None:
                meta_text += f" · פריט מקור [{trigger_n}]"
            add_mixed_paragraph(doc, meta_text, size_pt=9)
        if outcome_key == "blocked":
            # DS3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.6): a blocked investigation never ran
            # at all -- shown here, never as "לא נמצא".
            reason = entry.get("blocked_reason_he") or "—"
            blocked_p = add_mixed_paragraph(doc, f"נחסם (לא נחקר בפועל): {reason}", size_pt=BODY_SIZE_PT)
            for run in blocked_p.runs:
                run.font.bold = True
        elif entry.get("answer_he"):
            add_mixed_paragraph(doc, entry["answer_he"], size_pt=BODY_SIZE_PT)
        if entry.get("rerun_note_he"):
            # `eoa.report.daily.reconcile_deep_search_reruns` -- the same question investigated
            # more than once this period; the reconciled entry above is the best-outcome run.
            note_p = add_mixed_paragraph(doc, entry["rerun_note_he"], size_pt=9)
            for run in note_p.runs:
                run.font.italic = True
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
    *,
    domain_group_he: str | None = None,
    open_points_in_outlook: bool = False,
) -> list[str]:
    """The ordered list of top-level ("Heading 1") section titles this draft will actually render
    — computed once so a real table of contents (docx bookmarks / html anchors) can be built
    without duplicating each renderer's own conditionals (F10).

    R6-weekly: ``domain_group_he`` (default ``None``, the unchanged daily-report behaviour) wraps
    ``draft.sections`` under one parent heading instead of listing each domain's own; a truthy
    ``group_he`` on an ``extra_sections``/``tables`` entry likewise collapses same-group entries to
    one heading (:func:`_group_entries`/:func:`_group_title`). ``open_points_in_outlook`` (default
    ``False``) folds the "נקודות פתוחות" heading into "מבט קדימה" as a child instead of its own
    top-level heading."""
    headings: list[str] = []
    if _draft_bluf_text(draft):
        # Round 5 P4 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.1#1): the native BLUF, when present,
        # always leads -- before any `before_summary` extra_sections and before the summary itself.
        headings.append("שורה תחתונה")
    headings += [_group_title(e) for e in _group_entries(extra_sections, "before_summary")]
    headings.append("תקציר מנהלים")
    headings += [_group_title(e) for e in _group_entries(extra_sections, "after_summary")]
    if domain_group_he and draft.sections:
        headings.append(domain_group_he)
    else:
        headings += [section.title_he for section in draft.sections]
    if events:
        headings.append("טבלת אירועים עסקיים")
    if deep_search:
        headings.append("חקירות עומק")
    if not open_points_in_outlook and open_points:
        headings.append("נקודות פתוחות")
    if _draft_outlook_text(draft) or (open_points_in_outlook and open_points):
        headings.append("מבט קדימה")
    if _draft_assumptions(draft):
        # Round 5 P4 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.4#10): rendered right after the outlook,
        # before the after_outlook extra_sections (indicator watchlist, etc.).
        headings.append("הנחות והפרכות")
    headings += [_group_title(e) for e in _group_entries(extra_sections, "after_outlook")]
    headings += [_group_title(e) for e in _group_entries(tables or [])]
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
    domain_group_he: str | None = None,
    open_points_in_outlook: bool = False,
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

    ``domain_group_he``/``open_points_in_outlook`` (R6-weekly, default off -- the daily report is
    unaffected): see :func:`_planned_headings` for what each does.
    """
    tables = dedupe_rows_across_tables(tables) or None
    # Round-17 (2026-09-17, Hebrew company-name canonicalisation): the single choke point every
    # report kind's deterministic table data (headers/rows/captions/note_he -- daily/weekly/
    # monthly/bd_territory/product_line/dossier/patents-survey all funnel their `tables` argument
    # through this exact function) passes through before rendering, so this is where a company-
    # name misspelling in table cell text (as opposed to LLM-authored draft prose, already covered
    # separately by each report kind's own `normalize_draft`/`_normalize_draft_text` call) gets
    # folded onto its canonical spelling regardless of which report kind built the table.
    if tables:
        tables = canonicalize_hebrew_names_deep(tables)
    deep_search = deep_search or []
    open_clarifications = open_clarifications or []
    extra_sections = extra_sections or []
    generated_at = generated_at or dt.datetime.now(dt.UTC)
    resolved_title = title_text or TITLE_TEXT

    open_points = list(draft.open_points_he or [])
    open_points += [c.get("question") or "" for c in open_clarifications if c.get("question")]

    headings = _planned_headings(
        draft,
        events,
        deep_search,
        open_points,
        extra_sections,
        tables,
        domain_group_he=domain_group_he,
        open_points_in_outlook=open_points_in_outlook,
    )
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

    def _heading2(doc: DocxDocument, text: str):
        # R6-weekly: the ``###``/child heading under a grouped ``##`` parent -- not part of the
        # TOC's own bookmark list (only top-level `_heading1` headings are).
        return add_mixed_paragraph(doc, text, style="Heading 2")

    def _render_extra_group(doc: DocxDocument, position: str) -> None:
        for entry in _group_entries(extra_sections, position):
            if isinstance(entry, list):
                group_he = entry[0].get("group_he") or ""
                _heading1(doc, group_he)
                for sec in entry:
                    title = sec.get("title_he") or ""
                    if title and title != group_he:
                        _heading2(doc, title)
                    _add_md_body_docx(doc, sec.get("body_he") or "")
            else:
                _heading1(doc, entry.get("title_he") or "")
                _add_md_body_docx(doc, entry.get("body_he") or "")

    def _render_table_entry_docx(doc: DocxDocument, tbl: dict[str, Any]) -> None:
        if "headers" in tbl or "rows" in tbl:
            caption = _table_caption_text(tbl)
            if caption:
                cap_p = add_mixed_paragraph(doc, caption, size_pt=9)
                for run in cap_p.runs:
                    run.font.italic = True
            _add_generic_table_body(doc, tbl.get("headers") or [], tbl.get("rows") or [])
        elif tbl.get("body_he"):
            _add_md_body_docx(doc, tbl["body_he"])

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
    # Goal 1 (2026-09-06): the old blanket "citation QA failed" banner is kept only for the
    # legacy free-prose drafts (weekly/monthly/bd_territory), which still degrade by silently
    # stripping flagged sentences and therefore still need a visible flag. The structured daily
    # draft never reaches this state with partial content -- two QA failures replace the whole
    # narrative with `system_note_he` (rendered below, in the body, not as a bolted-on banner).
    if qa is not None and not qa.passed and _is_legacy_prose_draft(draft):
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

    bluf_text = _draft_bluf_text(draft)
    if bluf_text:
        _heading1(doc, "שורה תחתונה")
        bluf_p = add_mixed_paragraph(doc, bluf_text)
        for run in bluf_p.runs:
            run.font.bold = True

    _render_extra_group(doc, "before_summary")

    _heading1(doc, "תקציר מנהלים")
    exec_summary_display = _exec_summary_display_text(draft)
    if exec_summary_display:
        add_mixed_paragraph(doc, exec_summary_display)

    system_note = _draft_system_note_text(draft)
    if system_note:
        add_mixed_paragraph(doc, system_note)

    analyst_note = _draft_analyst_note_text(draft)
    if analyst_note:
        note_p = add_mixed_paragraph(doc, f"הערכת האנליסט: {analyst_note}")
        for run in note_p.runs:
            run.font.italic = True

    _render_extra_group(doc, "after_summary")

    if domain_group_he and draft.sections:
        _heading1(doc, domain_group_he)
        for section in draft.sections:
            _heading2(doc, section.title_he)
            for para in _split_paragraphs(_section_prose(section)):
                add_mixed_paragraph(doc, para)
    else:
        for section in draft.sections:
            _heading1(doc, section.title_he)
            for para in _split_paragraphs(_section_prose(section)):
                add_mixed_paragraph(doc, para)

    if events:
        _heading1(doc, "טבלת אירועים עסקיים")
        _add_events_table(doc, events)

    if deep_search:
        _heading1(doc, "חקירות עומק")
        _add_deep_search_section(doc, deep_search, items)

    if not open_points_in_outlook and open_points:
        _heading1(doc, "נקודות פתוחות")
        for point in open_points:
            add_mixed_paragraph(doc, point, style="List Bullet")

    outlook_text = _draft_outlook_text(draft)
    if outlook_text or (open_points_in_outlook and open_points):
        _heading1(doc, "מבט קדימה")
        if outlook_text:
            add_mixed_paragraph(doc, outlook_text)
        if open_points_in_outlook and open_points:
            _heading2(doc, "נקודות פתוחות")
            for point in open_points:
                add_mixed_paragraph(doc, point, style="List Bullet")

    assumptions = _draft_assumptions(draft)
    if assumptions:
        _heading1(doc, "הנחות והפרכות")
        for assumption in assumptions:
            add_mixed_paragraph(doc, _render_assumption(assumption), style="List Bullet")

    _render_extra_group(doc, "after_outlook")

    for entry in _group_entries(tables or []):
        if isinstance(entry, list):
            group_he = entry[0].get("group_he") or ""
            _heading1(doc, group_he)
            for tbl in entry:
                title = tbl.get("title_he") or ""
                if title and title != group_he:
                    _heading2(doc, title)
                _render_table_entry_docx(doc, tbl)
        else:
            _heading1(doc, entry.get("title_he") or "")
            _render_table_entry_docx(doc, entry)

    _heading1(doc, "נספח מקורות")
    _add_sources_appendix(doc, items)

    _flag_update_fields(doc)
    return doc


def _add_md_body_docx(doc: DocxDocument, body: str) -> None:
    """Render a section body's Markdown blocks (see :func:`_md_blocks`) into ``doc``."""
    for kind, payload in _md_blocks(body):
        if kind == "table":
            headers, rows = payload
            _add_generic_table_body(doc, headers, rows)
        elif kind == "bullets":
            for item in payload:
                add_mixed_paragraph(doc, item, style="List Bullet")
        elif payload:
            add_mixed_paragraph(doc, payload)


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
    for entry in _group_entries(sections, position):
        if isinstance(entry, list):
            group_he = entry[0].get("group_he") or ""
            lines += [f"## {group_he}", ""]
            for sec in entry:
                title = sec.get("title_he") or ""
                if title and title != group_he:
                    lines += [f"### {title}", ""]
                lines += [_md_citations(sec.get("body_he") or ""), ""]
        else:
            lines += [
                f"## {entry.get('title_he') or ''}",
                "",
                _md_citations(entry.get("body_he") or ""),
                "",
            ]


def _escape_md_table_cell(text: str) -> str:
    """Round-14 (CR-editing.md): a literal ``|`` inside cell text (most often a scraped page title
    of the common "Headline | Site Name" shape -- confirmed live in bd_il_2026-09-07.md's sources
    appendix) is a GFM table *column separator*, not data -- left unescaped it silently splits one
    cell into two, shifting every following cell in the row one column to the right and corrupting
    the whole table. Escaped to ``\\|`` (GFM's own escape), same treatment a real newline inside a
    cell gets (collapsed to a space -- a raw newline would just as surely break the row)."""
    if not text:
        return text
    return text.replace("|", "\\|").replace("\n", " ")


def _md_cell(value: Any) -> str:
    """A markdown table cell: a bare URL becomes a real ``[url](url)`` link (never a raw URL
    string sitting in running text), per F10. W17 (round 4b, docs/REVIEW_2026-09-06_evening.md):
    a non-URL cell still gets its ``[n]`` citation markers turned into real links to the sources
    appendix (``_md_citations``), same as prose -- a cell that was previously plain "[10]" text.
    Round-14: a literal ``|``/newline in the cell text is escaped first (:func:`_escape_md_table_cell`)
    so it can never split the row into extra columns."""
    if value is None:
        return "—"
    text = _escape_md_table_cell(str(value))
    return f"[{text}]({text})" if _looks_like_url(text) else _md_citations(text)


def _md_citations(text: str) -> str:
    """(F23) Turn every ``[n]`` citation marker in ``text`` into a real markdown link to its
    appendix row anchor (``[n](#src-n)``) instead of inert bracketed text."""
    return _CITATION_RE.sub(lambda m: f"[{m.group(1)}](#src-{m.group(1)})", text or "")


def _render_one_table_md(lines: list[str], tbl: dict[str, Any]) -> None:
    """One table's body (caption/trend line + header/rows) -- no heading; see :func:`_tables_md`."""
    headers = tbl.get("headers") or []
    caption = _table_caption_text(tbl)
    if caption:
        lines += [f"*{_md_citations(caption)}*", ""]
    if tbl.get("related_trend_he"):
        # W5 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.2#9): a table-level trend cross-reference.
        lines += [_md_citations(f"מגמה: {tbl['related_trend_he']}"), ""]
    lines += [
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    for row in tbl.get("rows") or []:
        cells = _trim_row_cells(_apply_row_trend_note(_row_cells(row), _row_related_trend(row)))
        lines.append("| " + " | ".join(_md_cell(v) for v in cells) + " |")
    lines.append("")


def _render_table_entry_md(lines: list[str], tbl: dict[str, Any]) -> None:
    """A ``tables``-list entry's body: a real table, or (R6-weekly, a grouped member only) plain
    Markdown prose (``body_he``, no ``headers``/``rows``) -- see :func:`_group_entries`'s note."""
    if "headers" in tbl or "rows" in tbl:
        _render_one_table_md(lines, tbl)
    elif tbl.get("body_he"):
        lines += [_md_citations(tbl["body_he"]), ""]


def _tables_md(lines: list[str], tables: list[dict[str, Any]]) -> None:
    for entry in _group_entries(tables):
        if isinstance(entry, list):
            group_he = entry[0].get("group_he") or ""
            lines += [f"## {group_he}", ""]
            for tbl in entry:
                title = tbl.get("title_he") or ""
                if title and title != group_he:
                    lines += [f"### {title}", ""]
                _render_table_entry_md(lines, tbl)
        else:
            lines += [f"## {entry.get('title_he') or ''}", ""]
            _render_table_entry_md(lines, entry)


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
    domain_group_he: str | None = None,
    open_points_in_outlook: bool = False,
) -> str:
    """Render the report as GitHub-flavoured Markdown (see :func:`build_docx` for the shared,
    additive ``title_text``/``extra_sections``/``tables``/``domain_group_he``/
    ``open_points_in_outlook`` hooks)."""
    tables = dedupe_rows_across_tables(tables) or None
    # Round-17 (2026-09-17, Hebrew company-name canonicalisation): the single choke point every
    # report kind's deterministic table data (headers/rows/captions/note_he -- daily/weekly/
    # monthly/bd_territory/product_line/dossier/patents-survey all funnel their `tables` argument
    # through this exact function) passes through before rendering, so this is where a company-
    # name misspelling in table cell text (as opposed to LLM-authored draft prose, already covered
    # separately by each report kind's own `normalize_draft`/`_normalize_draft_text` call) gets
    # folded onto its canonical spelling regardless of which report kind built the table.
    if tables:
        tables = canonicalize_hebrew_names_deep(tables)
    deep_search = deep_search or []
    open_clarifications = open_clarifications or []
    extra_sections = extra_sections or []
    lines = [f"# {title_text or TITLE_TEXT}", ""]
    if period_end is not None:
        lines += [f"**תאריך:** {hebrew_date_str(period_end)}", ""]
    # Goal 1: the blanket QA-failure banner is legacy-draft-only now (see `build_docx`'s own note).
    warning = _qa_warning_line(qa) if _is_legacy_prose_draft(draft) else None
    if warning:
        lines += [f"> **{warning}**", ""]

    bluf_text = _draft_bluf_text(draft)
    if bluf_text:
        lines += ["## שורה תחתונה", "", f"**{_md_citations(bluf_text)}**", ""]

    _extra_sections_md(lines, extra_sections, "before_summary")

    lines += ["## תקציר מנהלים", ""]
    exec_summary_display = _exec_summary_display_text(draft)
    if exec_summary_display:
        lines += [_md_citations(exec_summary_display), ""]

    system_note = _draft_system_note_text(draft)
    if system_note:
        lines += [system_note, ""]

    analyst_note = _draft_analyst_note_text(draft)
    if analyst_note:
        lines += [f"*הערכת האנליסט: {analyst_note}*", ""]

    _extra_sections_md(lines, extra_sections, "after_summary")

    if domain_group_he and draft.sections:
        lines += [f"## {domain_group_he}", ""]
        for section in draft.sections:
            lines += [f"### {section.title_he}", "", _md_citations(_section_prose(section)), ""]
    else:
        for section in draft.sections:
            lines += [f"## {section.title_he}", "", _md_citations(_section_prose(section)), ""]

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
            # Round-14 (CR-editing.md): `parties`/`customer`/`program` are free scraped text and,
            # same as the sources appendix, can carry a literal "|" that would otherwise split this
            # hand-built row into extra columns (see `_escape_md_table_cell`).
            parties_cell = _escape_md_table_cell(", ".join(ev.get("parties") or []) or "—")
            customer_cell = _escape_md_table_cell(ev.get("customer") or ev.get("program") or "—")
            lines.append(
                f"| {fmt_date(ev.get('date'))} "
                f"| {_EVENT_KIND_LABELS_HE.get(ev.get('kind'), ev.get('kind') or '—')} "
                f"| {parties_cell} "
                f"| {customer_cell} "
                f"| {fmt_amount(ev)} | {src} |"
            )
        lines.append("")

    if deep_search:
        lines += ["## חקירות עומק", ""]
        # R10-links: same numbered registry the sources appendix (below) renders -- a deep-search
        # entry's trigger item is always one of this report's own items (R9's
        # `_filter_deep_search_to_items_included`), so when present it already carries an `n`.
        item_by_id = {it.get("id"): it for it in items if it.get("id") is not None}
        for entry in deep_search:
            heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
            outcome_key = entry.get("outcome")
            outcome = _OUTCOME_LABELS_HE.get(outcome_key, outcome_key or "—")
            # DS3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.6): a blocked investigation shows its
            # blocked_reason_he, never the answer_he it never actually produced.
            body = (
                (entry.get("blocked_reason_he") or "—")
                if outcome_key == "blocked"
                else entry.get("answer_he", "")
            )
            lines.append(f"- **{heading}** — {outcome}: {_md_citations(body)}")
            # R10-links: a real link to the investigation's own detail page, plus a `[n]` citation
            # to the trigger item's sources-appendix row when it has one -- indented (like the
            # rerun note below) so eoa.qa.d4_investigations's `^-\s+\*\*...` entry regex, which only
            # matches un-indented lines, never mistakes it for a second investigation entry.
            job_id = entry.get("job_id")
            if job_id is not None:
                trigger_item_id = entry.get("item_id") or entry.get("trigger_item_id")
                trigger_n = (
                    item_by_id.get(trigger_item_id, {}).get("n") if trigger_item_id is not None else None
                )
                meta = f"[חקירה #{job_id}](/investigations/{job_id})"
                if trigger_n is not None:
                    meta += f" · פריט מקור {_md_citations(f'[{trigger_n}]')}"
                lines.append(f"  - {meta}")
            if entry.get("rerun_note_he"):
                # eoa.report.daily.reconcile_deep_search_reruns -- indented, so it is never
                # mistaken for a new investigation entry by eoa.qa.d4_investigations's `^-` regex.
                lines.append(f"  - {_md_citations(entry['rerun_note_he'])}")
        lines.append("")

    open_points = list(draft.open_points_he or [])
    open_points += [c.get("question") or "" for c in open_clarifications if c.get("question")]
    if not open_points_in_outlook and open_points:
        lines += ["## נקודות פתוחות", ""]
        lines += [f"- {_md_citations(p)}" for p in open_points]
        lines.append("")

    outlook_text = _draft_outlook_text(draft)
    if outlook_text or (open_points_in_outlook and open_points):
        lines += ["## מבט קדימה", ""]
        if outlook_text:
            lines += [_md_citations(outlook_text), ""]
        if open_points_in_outlook and open_points:
            lines += ["### נקודות פתוחות", ""]
            lines += [f"- {_md_citations(p)}" for p in open_points]
            lines.append("")

    assumptions = _draft_assumptions(draft)
    if assumptions:
        lines += ["## הנחות והפרכות", ""]
        lines += [f"- {_md_citations(_render_assumption(a))}" for a in assumptions]
        lines.append("")

    _extra_sections_md(lines, extra_sections, "after_outlook")
    _tables_md(lines, tables or [])

    lines += [
        "## נספח מקורות",
        "",
        "| # | כותרת | מקור | אמינות | תאריך | קישור |",
        "|---|---|---|---|---|---|",
    ]
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
        # Round-14 (CR-editing.md): a scraped item title routinely carries a literal "|" (the
        # common "Headline | Site Name" page-title convention -- confirmed live in
        # bd_il_2026-09-07.md) -- escaped so it can never split this row into extra columns (see
        # `_escape_md_table_cell`).
        title_cell = _escape_md_table_cell(it.get("title") or "—")
        source_cell = _escape_md_table_cell(source_label(it.get("source_name"), url))
        lines.append(
            f"| {n_cell} | {title_cell} | {source_cell} "
            f"| {reliability_label(_reliability_for(it))} "
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


def _bidi_html_with_citations(text: str) -> str:
    """Like :func:`_bidi_html`, but every ``[n]`` citation marker is first turned into a real
    anchor link to its row in the sources appendix (``#src-n``) -- same convention as
    :func:`_md_citations` (markdown) and the prose ``cite_links`` closure inside
    :func:`render_html`, extended here to table cells (W17, round 4b,
    docs/REVIEW_2026-09-06_evening.md): a table cell whose value is exactly (or contains) a
    ``[n]`` marker -- e.g. the acquisition-watch/procurement-events "מקור" column -- used to render
    as inert bracketed text instead of a clickable link."""
    parts = []
    pos = 0
    for m in _CITATION_RE.finditer(text):
        parts.append(_bidi_html(text[pos : m.start()]))
        n = m.group(1)
        parts.append(f'<a href="#src-{n}" class="cite">[{n}]</a>')
        pos = m.end()
    parts.append(_bidi_html(text[pos:]))
    return "".join(parts)


def _html_cell(value: Any) -> str:
    if value is None:
        return "—"
    text = str(value)
    return _html_link(text) if _looks_like_url(text) else _bidi_html_with_citations(text)


def _h3_html(title: str) -> str:
    """R6-weekly: a grouped member's child heading -- no anchor id (only top-level ``h2()``
    headings get one, for the TOC/heading-count list)."""
    return f"<h3>{_bidi_html(title)}</h3>"


def _render_prose_body_html(parts: list[str], body_he: str) -> None:
    for kind, payload in _md_blocks(body_he or ""):
        if kind == "table":
            headers, rows = payload
            parts.append(
                "<table><thead><tr>"
                + "".join(f"<th>{html.escape(h)}</th>" for h in headers)
                + "</tr></thead><tbody>"
            )
            for row in rows:
                parts.append("<tr>" + "".join(f"<td>{_html_cell(v)}</td>" for v in row) + "</tr>")
            parts.append("</tbody></table>")
        elif kind == "bullets":
            parts.append("<ul>" + "".join(f"<li>{_bidi_html(it)}</li>" for it in payload) + "</ul>")
        elif payload:
            parts.append(f"<p>{_bidi_html(payload)}</p>")


def _extra_sections_html(parts: list[str], sections: list[dict[str, Any]], position: str, h2) -> None:
    for entry in _group_entries(sections, position):
        if isinstance(entry, list):
            group_he = entry[0].get("group_he") or ""
            parts.append(h2(group_he))
            for sec in entry:
                title = sec.get("title_he") or ""
                if title and title != group_he:
                    parts.append(_h3_html(title))
                _render_prose_body_html(parts, sec.get("body_he") or "")
        else:
            parts.append(h2(entry.get("title_he") or ""))
            _render_prose_body_html(parts, entry.get("body_he") or "")


def _render_one_table_html(parts: list[str], tbl: dict[str, Any]) -> None:
    """One table's body (caption/trend line + header/rows) -- no heading; see :func:`_tables_html`."""
    headers = tbl.get("headers") or []
    caption = _table_caption_text(tbl)
    if caption:
        parts.append(f"<p><em>{_bidi_html(caption)}</em></p>")
    table_trend = tbl.get("related_trend_he")
    if table_trend:
        # W5 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.2#9): a table-level trend cross-reference.
        parts.append(f"<p>{_bidi_html('מגמה: ' + str(table_trend))}</p>")
    parts.append(
        "<table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr></thead><tbody>"
    )
    for row in tbl.get("rows") or []:
        row_cells = _trim_row_cells(_apply_row_trend_note(_row_cells(row), _row_related_trend(row)))
        cells = "".join(f"<td>{_html_cell(v)}</td>" for v in row_cells)
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</tbody></table>")


def _render_table_entry_html(parts: list[str], tbl: dict[str, Any]) -> None:
    """A ``tables``-list entry's body: a real table, or (R6-weekly, a grouped member only) plain
    prose (``body_he``, no ``headers``/``rows``) -- see :func:`_group_entries`'s note."""
    if "headers" in tbl or "rows" in tbl:
        _render_one_table_html(parts, tbl)
    elif tbl.get("body_he"):
        _render_prose_body_html(parts, tbl["body_he"])


def _tables_html(parts: list[str], tables: list[dict[str, Any]], h2) -> None:
    for entry in _group_entries(tables):
        if isinstance(entry, list):
            group_he = entry[0].get("group_he") or ""
            parts.append(h2(group_he))
            for tbl in entry:
                title = tbl.get("title_he") or ""
                if title and title != group_he:
                    parts.append(_h3_html(title))
                _render_table_entry_html(parts, tbl)
        else:
            parts.append(h2(entry.get("title_he") or ""))
            _render_table_entry_html(parts, entry)


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
  --eoa-blocked:#8a5a00;
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
    --eoa-blocked:#e0a94e;
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
  --eoa-blocked:#e0a94e;
  --eoa-toc-bg:#16222c;
  --eoa-toc-border:#2c3d42;
  --eoa-date:#9fb0b4}
:root[data-theme="light"] .eoa-report{
  --eoa-border:#d0d5dd;
  --eoa-border-strong:#10243e;
  --eoa-th-bg:#eef1f5;
  --eoa-link:#1a56db;
  --eoa-warning:#b42318;
  --eoa-blocked:#8a5a00;
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
.eoa-report .ds-blocked{color:var(--eoa-blocked);font-weight:600}
.eoa-report .ds-rerun-note{color:var(--eoa-date)}
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
    domain_group_he: str | None = None,
    open_points_in_outlook: bool = False,
) -> str:
    """Render the report as a self-contained, standalone RTL HTML document — a full
    ``<!doctype html>`` page with its own embedded stylesheet (scoped under the ``.eoa-report``
    class so it stays inert if this markup is instead embedded as a fragment, e.g. the Morning
    screen's ``ReportBody`` component), not just an inner ``<div>`` fragment (F10/U1: the file at
    ``output/reports/*.html`` is also served and opened directly via the report's "html" download
    link, where it must stand on its own). ``include_toc`` (see :func:`build_docx`) adds a simple
    anchor-based table of contents; the daily report leaves it off.
    """
    tables = dedupe_rows_across_tables(tables) or None
    # Round-17 (2026-09-17, Hebrew company-name canonicalisation): the single choke point every
    # report kind's deterministic table data (headers/rows/captions/note_he -- daily/weekly/
    # monthly/bd_territory/product_line/dossier/patents-survey all funnel their `tables` argument
    # through this exact function) passes through before rendering, so this is where a company-
    # name misspelling in table cell text (as opposed to LLM-authored draft prose, already covered
    # separately by each report kind's own `normalize_draft`/`_normalize_draft_text` call) gets
    # folded onto its canonical spelling regardless of which report kind built the table.
    if tables:
        tables = canonicalize_hebrew_names_deep(tables)
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

    headings = _planned_headings(
        draft,
        events,
        deep_search,
        open_points,
        extra_sections,
        tables,
        domain_group_he=domain_group_he,
        open_points_in_outlook=open_points_in_outlook,
    )
    heading_ids = [f"sec-{i}" for i in range(len(headings))]
    id_iter = iter(heading_ids)

    def h2(title: str) -> str:
        hid = next(id_iter, None)
        attr = f' id="{hid}"' if hid else ""
        return f"<h2{attr}>{_bidi_html(title)}</h2>"

    parts = [f"<h1>{_bidi_html(resolved_title)}</h1>"]
    if period_end is not None:
        parts.append(f'<p class="date">{_bidi_html(hebrew_date_str(period_end))}</p>')
    # Goal 1: the blanket QA-failure banner is legacy-draft-only now (see `build_docx`'s own note).
    warning = _qa_warning_line(qa) if _is_legacy_prose_draft(draft) else None
    if warning:
        parts.append(f'<p class="qa-warning"><strong>{html.escape(warning)}</strong></p>')

    if include_toc:
        toc_items = "".join(
            f'<li><a href="#{hid}">{_bidi_html(title)}</a></li>'
            for title, hid in zip(headings, heading_ids, strict=True)
            if title
        )
        parts.append(f'<nav class="toc"><h2>תוכן עניינים</h2><ul>{toc_items}</ul></nav>')

    bluf_text = _draft_bluf_text(draft)
    if bluf_text:
        parts.append(h2("שורה תחתונה"))
        parts.append(f"<p><strong>{cite_links(bluf_text)}</strong></p>")

    _extra_sections_html(parts, extra_sections, "before_summary", h2)

    parts.append(h2("תקציר מנהלים"))
    exec_summary_display = _exec_summary_display_text(draft)
    if exec_summary_display:
        parts.append(f"<p>{cite_links(exec_summary_display)}</p>")

    system_note = _draft_system_note_text(draft)
    if system_note:
        parts.append(f"<p>{_bidi_html(system_note)}</p>")

    analyst_note = _draft_analyst_note_text(draft)
    if analyst_note:
        parts.append(f'<p class="analyst-note"><em>הערכת האנליסט: {_bidi_html(analyst_note)}</em></p>')

    _extra_sections_html(parts, extra_sections, "after_summary", h2)

    if domain_group_he and draft.sections:
        parts.append(h2(domain_group_he))
        for section in draft.sections:
            parts.append(_h3_html(section.title_he))
            for para in _split_paragraphs(_section_prose(section)):
                parts.append(f"<p>{cite_links(para)}</p>")
    else:
        for section in draft.sections:
            parts.append(h2(section.title_he))
            for para in _split_paragraphs(_section_prose(section)):
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
        # R10-links: same numbered registry the sources appendix renders -- a deep-search entry's
        # trigger item is always one of this report's own items (R9's
        # `_filter_deep_search_to_items_included`), so when present it already carries an `n`.
        item_by_id = {it.get("id"): it for it in items if it.get("id") is not None}
        for entry in deep_search:
            heading = entry.get("question") or entry.get("trigger_title") or "חקירת עומק"
            outcome_key = entry.get("outcome")
            outcome_label = _OUTCOME_LABELS_HE.get(outcome_key, outcome_key or "—")
            is_blocked = outcome_key == "blocked"
            # DS3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.6): amber styling, never rendered as
            # "לא נמצא" -- distinguishes "technically blocked, never actually investigated" from a
            # genuine "searched thoroughly, nothing there" outcome.
            body = (entry.get("blocked_reason_he") or "—") if is_blocked else entry.get("answer_he", "")
            outcome_html = (
                f'<span class="ds-blocked">{html.escape(outcome_label)}</span>'
                if is_blocked
                else html.escape(outcome_label)
            )
            # Model headings/bullets are prose structure, not literal Markdown in the report.
            body = re.sub(r"(?:^|\s)#{1,6}\s+", "\n", body)
            body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)
            body_parts: list[str] = []
            _render_prose_body_html(body_parts, body)
            li = f"<li><strong>{_bidi_html(heading)}</strong> — {outcome_html}: {''.join(body_parts)}"
            # R10-links: a real link to the investigation's own detail page, plus the trigger
            # item's own `[n]` citation (via the same `cite_links` closure every other citation in
            # this document goes through) when it has one.
            job_id = entry.get("job_id")
            if job_id is not None:
                trigger_item_id = entry.get("item_id") or entry.get("trigger_item_id")
                trigger_n = (
                    item_by_id.get(trigger_item_id, {}).get("n") if trigger_item_id is not None else None
                )
                inv_link = f'<a href="/investigations/{job_id}">{_bidi_html(f"חקירה #{job_id}")}</a>'
                li += f'<br><span class="ds-provenance">{inv_link}'
                if trigger_n is not None:
                    li += f" · פריט מקור {cite_links(f'[{trigger_n}]')}"
                li += "</span>"
            rerun_note = entry.get("rerun_note_he")
            if rerun_note:
                li += f'<br><em class="ds-rerun-note">{_bidi_html(rerun_note)}</em>'
            li += "</li>"
            parts.append(li)
        parts.append("</ul>")

    if not open_points_in_outlook and open_points:
        parts.append(h2("נקודות פתוחות"))
        parts.append("<ul>")
        parts += [f"<li>{_bidi_html(p)}</li>" for p in open_points]
        parts.append("</ul>")

    outlook_text = _draft_outlook_text(draft)
    if outlook_text or (open_points_in_outlook and open_points):
        parts.append(h2("מבט קדימה"))
        if outlook_text:
            parts.append(f"<p>{cite_links(outlook_text)}</p>")
        if open_points_in_outlook and open_points:
            parts.append(_h3_html("נקודות פתוחות"))
            parts.append("<ul>")
            parts += [f"<li>{_bidi_html(p)}</li>" for p in open_points]
            parts.append("</ul>")

    assumptions = _draft_assumptions(draft)
    if assumptions:
        parts.append(h2("הנחות והפרכות"))
        parts.append("<ul>")
        parts += [f"<li>{cite_links(_render_assumption(a))}</li>" for a in assumptions]
        parts.append("</ul>")

    _extra_sections_html(parts, extra_sections, "after_outlook", h2)
    _tables_html(parts, tables or [], h2)

    parts.append(h2("נספח מקורות"))
    parts.append(
        "<table><thead><tr><th>#</th><th>כותרת</th><th>מקור</th><th>אמינות</th><th>תאריך</th>"
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
            f"<td>{_bidi_html(reliability_label(_reliability_for(it)))}</td>"
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
