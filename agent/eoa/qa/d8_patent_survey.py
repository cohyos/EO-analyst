"""D8 -- patent survey deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D8).

The Markdown survey has no way to express LTR isolation (it's plain text), so the LTR-isolation
check reads the sibling ``.html`` file instead -- ``eoa.report.docx_builder``'s ``<bdi dir="ltr">``
convention (see its module comment) is what every patent number / CPC code / Latin phrase should
be wrapped in there.
"""

from __future__ import annotations

import re
from pathlib import Path

from eoa.qa.types import Check, DomainScore, weighted_score

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_PATENT_NUMBER_RE = re.compile(r"\b[A-Z]{2}\d{5,}[A-Z0-9]*\b")
_BDI_WRAPPED_TOKEN_RE = re.compile(r"<bdi[^>]*>([^<]*)</bdi>")
_TIMELINE_HEADING = "ציר זמן"
_CPC_HEADING = "CPC"
_INLINE_P_CITATION_RE = re.compile(r"\[P(\d+)\]")
_TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{1,}:?$")

# Round 3 D8 finding 3 (2026-09-06, round_1_judge.md): the deterministic auto-check used to report
# 100% for "timeline"/"CPC" data purely because a heading with that name existed, even when the
# table under it had zero data rows (docs/qa/loop/round_1_judge.md: "the deterministic auto-check
# reports 100% because it only checks heading presence, not data population"). These must match
# eoa.patents.survey's own disclosure sentences (_TIMELINE_DISCLOSURE_HE / _CPC_DISCLOSURE_HE) --
# duplicated here (rather than imported) so this QA module never needs to import the patents
# package just to read two literal strings; a change to either survey.py constant should be
# mirrored here.
_TIMELINE_DISCLOSURE_HE = "אין נתוני ציר זמן שנתי זמינים לפטנטים במדגם זה"
_CPC_DISCLOSURE_HE = "אין נתוני קודי CPC זמינים לפטנטים במדגם זה"

# ---------------------------------------------------------------------------------------------
# Round 5 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 8/9, sec 3.5 rows 1/7/9): the
# methodology box, coverage tag, and business-implications priority/confidence fields are landing
# in other engineers' file scopes (``eoa.patents.survey``, ``patent_survey.md``, the schema) this
# same evening -- these are the deterministic, read-only-of-the-rendered-markdown QA gates for that
# work. Every check is tolerant of the feature not existing yet (a normal ``passed=False``, never
# an exception) -- most are expected to fail on today's already-rendered survey until it lands.
# ``cpc_assignee_matrix_present`` (sec 3.5 row 7) is the one exception: ``eoa.patents.survey``
# already renders a "מטריצת אשכול x מקצה" table pre-round-5, so this check is expected to pass on
# an already-rendered survey with patent data, not merely "tolerated" as a future feature.
# ---------------------------------------------------------------------------------------------

_EXEC_SUMMARY_HEADING = "תקציר מנהלים"
_METHODOLOGY_HEADING_HE = "שיטה והיקף"
_COVERAGE_TAG_RE = re.compile(r"כיסוי\s+נתוני\s+מקצה\s*[:：]\s*\d{1,3}\s*%")
_IMPLICATIONS_HEADING_HE = "השלכות עסקיות"
_PRIORITY_MARKER_HE = "עדיפות"
_CONFIDENCE_MARKERS_D8 = ("ביטחון", "confidence")
_ASSIGNEE_HEADING_RE = re.compile(r"^פרופיל מקצה\s*[:：]\s*(.+)$")
_BOGUS_ASSIGNEE_NAMES = frozenset(
    {"europe", "united states", "u.s.", "usa", "u.s.a.", "inc", "inc.", "unknown", "n/a", "various", "asia"}
)
_CLUSTER_HEADING_KEYWORD_HE = "אשכול"
_UNCLASSIFIED_LABELS_HE = ("לא מסווג", "ללא סיווג", "unclassified")
_PATENTS_TABLE_HEADING_KEYWORDS = ("טבלת פטנטים", "נספח פטנטים")
#: docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.5 row 7 ("White spaces: להוסיף מטריצת CPC×מקצה") --
#: matches eoa.patents.survey's own "מטריצת אשכול x מקצה" table heading (clusters are themselves
#: CPC-code-derived, see survey.py's _cluster_by_cpc) without hardcoding the exact axis label, so a
#: future rename to e.g. "מטריצת CPC x מקצה" still matches.
_MATRIX_HEADING_KEYWORD_HE = "מטריצ"


def _methodology_box_check(sections: list[tuple[str, str]]) -> Check:
    """Item 8: a "שיטה והיקף" box before the exec summary, carrying the coverage-% tag."""
    method_idx = summary_idx = None
    method_body = ""
    for i, (h, body) in enumerate(sections):
        if _METHODOLOGY_HEADING_HE in h and method_idx is None:
            method_idx, method_body = i, body
        if _EXEC_SUMMARY_HEADING in h and summary_idx is None:
            summary_idx = i
    if method_idx is None:
        return Check(
            "methodology_box_before_summary",
            False,
            weight=2.0,
            evidence=f"no '{_METHODOLOGY_HEADING_HE}' heading found",
        )
    before_summary = summary_idx is None or method_idx < summary_idx
    has_coverage_tag = bool(_COVERAGE_TAG_RE.search(method_body))
    return Check(
        "methodology_box_before_summary",
        before_summary and has_coverage_tag,
        weight=2.0,
        evidence=f"positioned_before_summary={before_summary}, coverage_tag_in_box={has_coverage_tag}",
    )


def _coverage_tag_check(full_text: str) -> Check:
    """Item 8: "כיסוי נתוני מקצה: NN%" as a deterministic tag, not buried in exec-summary prose."""
    m = _COVERAGE_TAG_RE.search(full_text)
    return Check(
        "coverage_tag_present",
        bool(m),
        weight=1.0,
        evidence=f"coverage tag found: {m.group(0) if m else None}",
    )


def _implications_priority_confidence_check(sections: list[tuple[str, str]]) -> Check:
    """Item 9: business_implications carry ``priority``/``confidence``, like the BD report's
    ``recommended_actions`` already do."""
    for h, body in sections:
        if _IMPLICATIONS_HEADING_HE in h:
            has_priority = _PRIORITY_MARKER_HE in body
            has_confidence = any(m in body for m in _CONFIDENCE_MARKERS_D8)
            return Check(
                "implications_have_priority_confidence",
                has_priority and has_confidence,
                weight=1.5,
                evidence=f"heading '{h}': priority={has_priority}, confidence={has_confidence}",
            )
    return Check(
        "implications_have_priority_confidence",
        False,
        weight=1.5,
        evidence=f"no '{_IMPLICATIONS_HEADING_HE}' heading found",
    )


def _no_bogus_assignee_check(sections: list[tuple[str, str]]) -> Check:
    """No assignee profile named a bare country/continent/generic-suffix placeholder (observed
    live: "פרופיל מקצה: Europe") -- a real company/entity name is required."""
    bad = []
    for h, _b in sections:
        m = _ASSIGNEE_HEADING_RE.match(h.strip())
        if m and m.group(1).strip().casefold() in _BOGUS_ASSIGNEE_NAMES:
            bad.append(m.group(1).strip())
    return Check(
        "no_bogus_assignee",
        len(bad) == 0,
        weight=1.5,
        evidence=f"bogus assignee name(s) found: {bad}",
    )


def _no_unclassified_cluster_check(sections: list[tuple[str, str]]) -> Check:
    """A survey with real patent data shouldn't dump everything into an unclassified bucket."""
    has_patent_data = any(
        any(kw in h for kw in _PATENTS_TABLE_HEADING_KEYWORDS) and _section_has_data_row(body)
        for h, body in sections
    )
    if not has_patent_data:
        return Check(
            "no_unclassified_cluster_when_patents_exist",
            True,
            weight=1.0,
            evidence="no populated patents table this round -- check not applicable",
        )
    unclassified_hits = [
        h
        for h, body in sections
        if _CLUSTER_HEADING_KEYWORD_HE in h
        and any(lbl in (h + body).casefold() for lbl in _UNCLASSIFIED_LABELS_HE)
    ]
    return Check(
        "no_unclassified_cluster_when_patents_exist",
        len(unclassified_hits) == 0,
        weight=1.0,
        evidence=f"unclassified cluster heading(s): {unclassified_hits}",
    )


def _cpc_assignee_matrix_check(sections: list[tuple[str, str]]) -> Check:
    """Item 7 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.5 row 7): a deterministic CPC/cluster x
    assignee matrix alongside the White-space narrative -- not applicable when the survey has no
    populated patents table this round (nothing to matrix), same "not applicable" convention as
    :func:`_no_unclassified_cluster_check`."""
    has_patent_data = any(
        any(kw in h for kw in _PATENTS_TABLE_HEADING_KEYWORDS) and _section_has_data_row(body)
        for h, body in sections
    )
    if not has_patent_data:
        return Check(
            "cpc_assignee_matrix_present",
            True,
            weight=1.0,
            evidence="no populated patents table this round -- check not applicable",
        )
    for h, body in sections:
        if _MATRIX_HEADING_KEYWORD_HE in h:
            has_data = _section_has_data_row(body)
            return Check(
                "cpc_assignee_matrix_present",
                has_data,
                weight=1.0,
                evidence=f"heading '{h}' found; has populated matrix table: {has_data}",
            )
    return Check(
        "cpc_assignee_matrix_present",
        False,
        weight=1.0,
        evidence="no heading matching a CPC/cluster x assignee matrix found",
    )


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _section_has_data_row(body: str) -> bool:
    """True when ``body`` (one heading's section text) contains a real markdown table (a header
    row immediately followed by a ``|---|---|``-style separator row, matching every table this
    project's own renderer emits) with at least one actual data row below it. A bare heading, a
    heading with only the header+separator rows and zero data rows -- the exact shape round 1's
    judge caught the old heading-only check missing -- or anything not shaped like a table at all,
    returns False."""
    table_lines = [
        ln.strip() for ln in body.splitlines() if ln.strip().startswith("|") and ln.strip().endswith("|")
    ]
    if len(table_lines) < 2:
        return False
    separator_cells = [c.strip() for c in table_lines[1].strip("|").split("|")]
    if not all(_TABLE_SEPARATOR_CELL_RE.match(c) for c in separator_cells if c):
        return False  # second "|"-line isn't a header separator -- not a table this check trusts
    return any(any(c.strip() for c in line.strip("|").split("|")) for line in table_lines[2:])


def _sections_matching(sections: list[tuple[str, str]], keyword: str) -> list[str]:
    return [body for title, body in sections if keyword in title]


def _patent_numbers_isolated_in_html(html_text: str) -> tuple[int, int]:
    """(isolated_count, total_count) of distinct patent-number-looking tokens found in the HTML,
    and whether each occurrence sits inside a ``<bdi ...>...</bdi>`` span."""
    all_numbers = set(_PATENT_NUMBER_RE.findall(html_text))
    if not all_numbers:
        return 0, 0
    bdi_contents = " ".join(_BDI_WRAPPED_TOKEN_RE.findall(html_text))
    isolated = sum(1 for num in all_numbers if num in bdi_contents)
    return isolated, len(all_numbers)


def score_D8(md_path: Path | None, html_path: Path | None = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D8: deterministic checks over the latest patent survey report."""
    if md_path is None or not md_path.exists():
        return DomainScore(domain="D8", score_0_100=None, checks=[], n=0, note="no patent survey file found")

    text = md_path.read_text(encoding="utf-8")
    sections = _sections(text)

    # Round 3 D8 finding 3 (2026-09-06): a heading alone used to be enough to pass -- now a
    # timeline/CPC heading only passes when at least one of its sections carries either a real
    # table data row (_section_has_data_row) or the explicit "data genuinely absent" disclosure
    # sentence eoa.patents.survey writes in that same case (never a silently-empty table).
    timeline_sections = _sections_matching(sections, _TIMELINE_HEADING)
    timeline_present = bool(timeline_sections) and any(
        _section_has_data_row(body) or _TIMELINE_DISCLOSURE_HE in body for body in timeline_sections
    )
    cpc_sections = _sections_matching(sections, _CPC_HEADING)
    cpc_present = bool(cpc_sections) and any(
        _section_has_data_row(body) or _CPC_DISCLOSURE_HE in body for body in cpc_sections
    )

    cited_ps = {int(n) for n in _INLINE_P_CITATION_RE.findall(text)}
    # [Pn] numbers off the patents table's own "#" column, not the source appendix -- so "every
    # cited [Pn] has an appendix row" means every [Pn] number also appears as a row index "n" in
    # the "טבלת פטנטים" table, checked via the same leading "| n |" column pattern.
    table_row_ns = {int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", text, re.MULTILINE)}
    orphan_ps = sorted(cited_ps - table_row_ns) if cited_ps else []

    checks = [
        Check(
            "timeline_present",
            passed=timeline_present,
            weight=1.0,
            evidence=(
                f"{len(timeline_sections)} heading(s) matched '{_TIMELINE_HEADING}'; "
                f"data row or disclosure present: {timeline_present}"
            ),
        ),
        Check(
            "cpc_present",
            passed=cpc_present,
            weight=1.0,
            evidence=(
                f"{len(cpc_sections)} heading(s) matched '{_CPC_HEADING}'; "
                f"data row or disclosure present: {cpc_present}"
            ),
        ),
        Check(
            "every_cited_patent_has_appendix_row",
            passed=len(orphan_ps) == 0,
            weight=1.5,
            evidence=(
                f"{len(cited_ps)} [Pn] citations found; orphan: {orphan_ps[:10]}"
                if cited_ps
                else "no [Pn]-style inline citations in this survey (table-only format)"
            ),
        ),
        # Round 5 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 8/9): see this module's own
        # "Round 5" section above for what each check verifies.
        _methodology_box_check(sections),
        _coverage_tag_check(text),
        _implications_priority_confidence_check(sections),
        _no_bogus_assignee_check(sections),
        _no_unclassified_cluster_check(sections),
        _cpc_assignee_matrix_check(sections),
    ]

    n_extra = 0
    if html_path is not None and html_path.exists():
        html_text = html_path.read_text(encoding="utf-8")
        isolated, total = _patent_numbers_isolated_in_html(html_text)
        checks.append(
            Check(
                "patent_numbers_ltr_isolated",
                passed=total == 0 or isolated / total >= 0.95,
                weight=2.0,
                evidence=f"{isolated}/{total} distinct patent-number tokens wrapped in <bdi>"
                if total
                else "no patent-number tokens found in HTML",
            )
        )
        n_extra = total

    return DomainScore(
        domain="D8",
        score_0_100=weighted_score(checks),
        checks=checks,
        n=len(sections) + n_extra,
        note=str(md_path),
    )
