"""D6 -- daily/weekly report deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D6).

Operates on the already-rendered ``output/reports/{daily,weekly}_<date>.md`` file (Markdown, the
same content the ``.docx``/``.html`` siblings are built from -- see ``eoa.report.daily``/``weekly``
and ``docs/MODULES.md``). Reuses ``eoa.report.qa_citations``'s sentence-splitting/factuality/
citation-extraction helpers rather than re-implementing them; link liveness reuses
``eoa.qa.links.check_links``.

Round 3 (2026-09-06, D6 judge finding 3): the ``no_duplicate_sentences`` check used to scan every
``##``/``###`` section including "נספח מקורות" (the sources appendix) -- but the appendix is a
*registry*, not narrative prose: it deliberately re-renders every numbered item's own title once,
by design, so a table row citing item ``[n]`` (a deterministic table like "תעשייה ישראלית" or the
events table) and the appendix's own row for that same item legitimately carry the identical title
text. Two fixes were considered for this false positive -- (a) change table/appendix rendering so
one of the two never repeats the title verbatim, at the cost of making it harder for a reader to
cross-reference a table row back to its full source record, or (b) exempt the appendix section
from this deterministic scan, since its whole purpose is to be a citable index, not fresh prose.
(b) is implemented below (the appendix heading is excluded from :func:`score_D6`'s
``all_sentences``) -- it keeps every table's own rendering unchanged and the appendix fully useful
as a cross-reference, and it does not weaken the check for its actual purpose: two *narrative*
sections (exec summary, LLM-drafted sections, deterministic non-appendix tables) restating the same
sentence is still caught exactly as before.
"""

from __future__ import annotations

import re
from pathlib import Path

from eoa.qa.links import check_links
from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.report.qa_citations import (
    _normalize_for_dup_check,
    citations_in,
    is_factual,
    split_sentences,
)

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_RAW_SLUG_HEADING_RE = re.compile(r"^##\s+[a-z_]+$", re.MULTILINE)
_APPENDIX_ANCHOR_RE = re.compile(r'<a id="src-(\d+)"></a>')
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
#: `[n]` report-citation markers -- but not the unrelated `[item N]` convention used inside
#: forecast rationale text to reference an internal items.id (see the daily report sample this
#: was written against: "זוהו 5 אירוע(ים) ... [item 10] [item 47]").
_INLINE_CITATION_RE = re.compile(r"(?<!item )\[(\d+)\]")

_EXEC_SUMMARY_HEADING = "תקציר מנהלים"
_APPENDIX_HEADING = "נספח מקורות"
_SECTION_ANCHORS = {
    "israel": ("ישראל",),
    "tenders": ("מכרזים",),
    "tech": ("טכנולוג",),
}

# ---------------------------------------------------------------------------------------------
# Round 5 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 1/3/4/5/12): the twelve
# implementation items are landing in *other* engineers' file scopes this same evening (schemas,
# prompts, docx_builder/daily.py/weekly.py rendering) -- these checks are the deterministic,
# read-only-of-the-rendered-markdown QA gates for that work, written against the exact evidence
# strings/headings docs/REPORT_TEMPLATE_BENCHMARK.md sec 2 cites from the live 2026-09-06 reports.
# Every check below is tolerant of the feature not existing yet in an older report file (that must
# read as a normal FAIL via ``passed=False``, never an exception) -- most are expected to fail on
# today's already-rendered files until the parallel work lands.
# ---------------------------------------------------------------------------------------------

_BLUF_HEADING_HE = "שורה תחתונה"
_MAX_BLUF_SENTENCES = 2
_MAX_BLUF_WORDS = 40
_WHAT_CHANGED_KEYWORD_HE = "השתנה"
_INDICATOR_WATCHLIST_HEADING_HE = "מעקב אינדיקטורים"
_INDICATOR_STATUS_WORDS_HE = ("חדש", "פתוח", "הבשיל", "בוטל")
_ISRAEL_HEADING_KEYWORD_HE = "תעשייה ישראלית"
_ISRAEL_TYPE_COLUMN_HE = "סוג"
_OUTLOOK_HEADING_HE = "מבט קדימה"
_LIKELIHOOD_WORD_HE = "סבירות"
_CONFIDENCE_WORD_HE = "ביטחון"
_CLAUSE_SPLIT_RE = re.compile(r"[.,;]")
_H2_ONLY_RE = re.compile(r"(?m)^##(?!#)\s+\S")

try:  # pragma: no cover -- exercised indirectly; import guarded per the task brief ("if it exists")
    from eoa.report.style import BANNED_FILLER_PHRASES_HE as _FILLER_PHRASES_HE
except ImportError:  # pragma: no cover -- defensive only, style.py exists in this repo today
    _FILLER_PHRASES_HE = ("יש לציין", "חשוב להדגיש", "בהקשר זה", "ראוי לציין")

_BARE_CITATION_RE = re.compile(r"\[(\d+)\]")


def _first_table_header_cells(body: str) -> list[str]:
    lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
    if not lines:
        return []
    return [c.strip() for c in lines[0].strip("|").split("|")]


def _bluf_check(sections: list[tuple[str, str]]) -> Check:
    """Item 4/1: a ``שורה תחתונה`` heading, before the exec summary, 1-2 short cited sentences."""
    bluf_idx = exec_idx = None
    bluf_body = ""
    for i, (h, body) in enumerate(sections):
        if _BLUF_HEADING_HE in h and bluf_idx is None:
            bluf_idx, bluf_body = i, body
        if _EXEC_SUMMARY_HEADING in h and exec_idx is None:
            exec_idx = i
    if bluf_idx is None:
        return Check(
            "bluf_present_and_short", False, weight=2.0, evidence=f"no '{_BLUF_HEADING_HE}' heading found"
        )
    sentences = split_sentences(bluf_body)
    word_count = len(bluf_body.split())
    cited = bool(sentences) and all(citations_in(s) for s in sentences)
    before_summary = exec_idx is None or bluf_idx < exec_idx
    ok = (
        0 < len(sentences) <= _MAX_BLUF_SENTENCES
        and word_count <= _MAX_BLUF_WORDS
        and cited
        and before_summary
    )
    return Check(
        "bluf_present_and_short",
        ok,
        weight=2.0,
        evidence=(
            f"{len(sentences)} sentence(s), {word_count} words, cited={cited}, "
            f"before_exec_summary={before_summary}"
        ),
    )


def _what_changed_check(sections: list[tuple[str, str]]) -> Check:
    """Item 3: a "מה השתנה מאז הדוח הקודם" section right after the executive summary."""
    hits = [h for h, _b in sections if _WHAT_CHANGED_KEYWORD_HE in h]
    return Check(
        "what_changed_section_present",
        len(hits) > 0,
        weight=1.5,
        evidence=f"headings matching '{_WHAT_CHANGED_KEYWORD_HE}': {hits}",
    )


def _indicator_watchlist_check(sections: list[tuple[str, str]]) -> Check:
    """Item 6: a "מעקב אינדיקטורים" table with statuses חדש/פתוח/הבשיל/בוטל."""
    for h, body in sections:
        if _INDICATOR_WATCHLIST_HEADING_HE in h:
            hits = [w for w in _INDICATOR_STATUS_WORDS_HE if w in body]
            return Check(
                "indicator_watchlist_table_present",
                len(hits) > 0,
                weight=1.5,
                evidence=f"status words found: {hits}"
                if hits
                else "heading found but no status word in body",
            )
    return Check(
        "indicator_watchlist_table_present",
        False,
        weight=1.5,
        evidence=f"no '{_INDICATOR_WATCHLIST_HEADING_HE}' heading found",
    )


def _israel_single_table_check(sections: list[tuple[str, str]]) -> Check:
    """Item 5: the Israeli-industry tables merged into ONE table with a "סוג" column."""
    matches = [(h, body) for h, body in sections if _ISRAEL_HEADING_KEYWORD_HE in h]
    if not matches:
        return Check(
            "israel_single_table_with_type_column", False, weight=1.5, evidence="no israel heading found"
        )
    if len(matches) > 1:
        return Check(
            "israel_single_table_with_type_column",
            False,
            weight=1.5,
            evidence=f"{len(matches)} separate israel headings found (should be merged into one): "
            f"{[h for h, _ in matches]}",
        )
    header_cells = _first_table_header_cells(matches[0][1])
    has_type_col = _ISRAEL_TYPE_COLUMN_HE in header_cells
    return Check(
        "israel_single_table_with_type_column",
        has_type_col,
        weight=1.5,
        evidence=f"single israel heading found; header cells: {header_cells}",
    )


def _outlook_likelihood_confidence_check(sections: list[tuple[str, str]]) -> Check:
    """Item 2: OutlookIndicator carries likelihood AND confidence, never mixed in one clause."""
    body = ""
    for h, b in sections:
        if _OUTLOOK_HEADING_HE in h:
            body = b
            break
    if not body.strip():
        return Check(
            "outlook_likelihood_and_confidence_separated",
            False,
            weight=1.5,
            evidence=f"no '{_OUTLOOK_HEADING_HE}' section found",
        )
    items = [ln.strip("-* ").strip() for ln in body.splitlines() if ln.strip().startswith(("-", "*"))]
    if not items:
        items = [body]
    bad: list[str] = []
    for item in items:
        if not item:
            continue
        has_likelihood = _LIKELIHOOD_WORD_HE in item
        has_confidence = _CONFIDENCE_WORD_HE in item
        mixed = any(
            _LIKELIHOOD_WORD_HE in clause and _CONFIDENCE_WORD_HE in clause
            for clause in _CLAUSE_SPLIT_RE.split(item)
        )
        if not (has_likelihood and has_confidence) or mixed:
            bad.append(item[:80])
    return Check(
        "outlook_likelihood_and_confidence_separated",
        len(bad) == 0,
        weight=1.5,
        evidence=f"{len(bad)}/{len(items)} indicator(s) missing a separated marker or mixing them: {bad[:5]}",
    )


def _no_filler_check(sections: list[tuple[str, str]]) -> Check:
    """W26 backstop: no banned analyst-filler phrase left in the exec summary."""
    exec_text = _exec_summary_text(sections)
    hits = [p for p in _FILLER_PHRASES_HE if p in exec_text]
    return Check(
        "exec_summary_no_filler_phrases",
        len(hits) == 0,
        weight=1.0,
        evidence=f"filler phrases found in exec summary: {hits}",
    )


def _extract_tables(md_text: str) -> list[list[str]]:
    tables: list[list[str]] = []
    current: list[str] = []
    for line in md_text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            current.append(s)
        else:
            if len(current) >= 2:
                tables.append(current)
            current = []
    if len(current) >= 2:
        tables.append(current)
    return tables


def _no_row_repeated_across_tables_check(md_text: str) -> Check:
    """No two rows in *different* tables share the exact same ``[n]`` citation set -- a sign the
    same underlying fact/event was duplicated into two separate tables instead of appearing once."""
    tables = _extract_tables(md_text)
    seen: dict[frozenset[int], tuple[int, str]] = {}
    dups: list[str] = []
    for ti, table in enumerate(tables):
        if len(table) < 3:
            continue
        for row in table[2:]:
            ns = frozenset(int(n) for n in _BARE_CITATION_RE.findall(row))
            if not ns:
                continue
            prior = seen.get(ns)
            if prior is not None and prior[0] != ti:
                dups.append(f"{prior[1][:60]!r} == {row[:60]!r}")
            else:
                seen.setdefault(ns, (ti, row))
    return Check(
        "no_row_repeated_across_tables",
        len(dups) == 0,
        weight=1.0,
        evidence=f"{len(dups)} cross-table duplicate row(s) sharing a citation set: {dups[:5]}",
    )


def _heading_budget_check(md_path: Path, md_text: str) -> Check:
    """Item: weekly ≤ 16 ``##`` headings (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.2: "~14" target
    down from 24); a daily report gets a tighter budget (12) since it's meant to read in ~10 min."""
    is_weekly = md_path.name.startswith("weekly")
    budget = 16 if is_weekly else 12
    count = len(_H2_ONLY_RE.findall(md_text))
    return Check(
        "heading_count_within_budget",
        count <= budget,
        weight=1.0,
        evidence=f"{count} H2 headings (budget: {budget}, kind: {'weekly' if is_weekly else 'daily'})",
    )


def _monthly_structured_check(monthly_path: Path) -> Check:
    """Item M1: the monthly report adopts the same ``Sentence{text_he, cites}`` structured-citation
    rendering as daily/weekly/BD -- rendered citations are ``[n](#src-n)`` links, never a bare
    ``[n]`` left over from the legacy free-prose path."""
    text = monthly_path.read_text(encoding="utf-8")
    bad = [m.group(1) for m in _BARE_CITATION_RE.finditer(text) if text[m.end() : m.end() + 1] != "("]
    return Check(
        "monthly_is_structured",
        len(bad) == 0,
        weight=1.0,
        evidence=f"{len(bad)} bare (non-link) [n] citation(s) found: {bad[:10]}",
    )


def _sections(md_text: str) -> list[tuple[str, str]]:
    """``[(heading_text, body_until_next_heading)]`` for every ``##``/``###`` heading."""
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _exec_summary_text(sections: list[tuple[str, str]]) -> str:
    for heading, body in sections:
        if _EXEC_SUMMARY_HEADING in heading:
            return body
    return ""


def _appendix_numbers(md_text: str) -> set[int]:
    return {int(n) for n in _APPENDIX_ANCHOR_RE.findall(md_text)}


def _appendix_urls(sections: list[tuple[str, str]]) -> list[str]:
    for heading, body in sections:
        if _APPENDIX_HEADING in heading:
            return [url for _label, url in _MD_LINK_RE.findall(body)]
    return []


def _uncited_factual_sentences(text: str) -> list[str]:
    bad = []
    for sentence in split_sentences(text):
        if is_factual(sentence) and not citations_in(sentence):
            bad.append(sentence[:80])
    return bad


def _orphan_citations(text: str, appendix_ns: set[int]) -> list[int]:
    cited = {int(n) for n in _INLINE_CITATION_RE.findall(text)}
    return sorted(cited - appendix_ns)


def score_D6(  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    md_path: Path | None, *, run_link_check: bool = True, monthly_path: Path | None = None
) -> DomainScore:
    """D6: deterministic checks over one rendered daily/weekly report Markdown file.

    ``md_path=None`` (no report file found on disk for the round) yields a "manual only" result
    rather than a misleading 0.

    ``monthly_path`` (round 5, optional): the latest monthly report, if one exists this round --
    checked only for ``monthly_is_structured`` (see :func:`_monthly_structured_check`), a concern
    independent of the daily/weekly file above. Omitted from the check list entirely (not scored as
    a fail) when no monthly report has been produced this round -- there's nothing to critique yet.
    """
    if md_path is None or not md_path.exists():
        return DomainScore(domain="D6", score_0_100=None, checks=[], n=0, note="no report file found")

    text = md_path.read_text(encoding="utf-8")
    sections = _sections(text)
    exec_summary = _exec_summary_text(sections)
    appendix_ns = _appendix_numbers(text)

    uncited = _uncited_factual_sentences(exec_summary)
    orphan_ns = _orphan_citations(text, appendix_ns)
    raw_slugs = _RAW_SLUG_HEADING_RE.findall(text)

    # Round 3 (2026-09-06, D6 judge finding 3): the appendix is excluded from the duplicate-
    # sentence scan -- see the module docstring for why.
    all_sentences = [
        s
        for h, body in sections
        if _APPENDIX_HEADING not in h
        for s in split_sentences(body)
        if len(s.strip()) > 15
    ]
    seen: dict[str, str] = {}
    duplicates = []
    for s in all_sentences:
        key = _normalize_for_dup_check(s)
        if key in seen and key:
            duplicates.append(s[:80])
        else:
            seen[key] = s

    headings_blob = " ".join(h for h, _b in sections)
    anchors_present = {name: any(kw in headings_blob for kw in kws) for name, kws in _SECTION_ANCHORS.items()}
    anchors_hit = sum(anchors_present.values())

    checks = [
        Check(
            "every_factual_exec_summary_sentence_cited",
            passed=len(uncited) == 0,
            weight=2.5,
            evidence=f"{len(uncited)} uncited factual sentences: {uncited[:5]}",
        ),
        Check(
            "every_inline_citation_in_appendix",
            passed=len(orphan_ns) == 0,
            weight=2.0,
            evidence=f"appendix has {len(appendix_ns)} entries; orphan [n] refs: {orphan_ns[:10]}",
        ),
        Check(
            "no_raw_slug_headings",
            passed=len(raw_slugs) == 0,
            weight=1.0,
            evidence=f"raw-slug headings: {raw_slugs[:5]}",
        ),
        Check(
            "no_duplicate_sentences",
            passed=len(duplicates) == 0,
            weight=1.5,
            evidence=f"{len(duplicates)} duplicate sentences: {duplicates[:5]}",
        ),
        Check(
            "israel_tech_tenders_sections_present",
            passed=anchors_hit >= 2,
            weight=1.5,
            evidence=f"present: {[k for k, v in anchors_present.items() if v]}; missing: {[k for k, v in anchors_present.items() if not v]}",
        ),
        # Round 5 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 1-5, 12): see this module's own
        # "Round 5" section above for what each check verifies and why it's expected to fail on an
        # older report that predates the parallel implementation work.
        _bluf_check(sections),
        _what_changed_check(sections),
        _indicator_watchlist_check(sections),
        _israel_single_table_check(sections),
        _outlook_likelihood_confidence_check(sections),
        _no_filler_check(sections),
        _no_row_repeated_across_tables_check(text),
        _heading_budget_check(md_path, text),
    ]
    if monthly_path is not None and monthly_path.exists():
        checks.append(_monthly_structured_check(monthly_path))

    n = len(all_sentences)
    if run_link_check:
        urls = _appendix_urls(sections)
        results = check_links(urls) if urls else []
        bad = [r.url for r in results if not r.ok]
        checks.append(
            Check(
                "appendix_links_http_ok",
                passed=len(urls) == 0 or len(bad) / len(urls) <= 0.15,
                weight=2.0,
                evidence=f"{len(urls) - len(bad)}/{len(urls)} OK (200/3xx or allow-listed); bad: {bad[:10]}",
            )
        )
        n += len(urls)

    return DomainScore(domain="D6", score_0_100=weighted_score(checks), checks=checks, n=n, note=str(md_path))
