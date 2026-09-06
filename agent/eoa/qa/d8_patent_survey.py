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
_INLINE_P_CITATION_RE = re.compile(r"\[P(\d+)\]")


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


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
    headings_blob = " ".join(h for h, _b in sections)

    timeline_present = _TIMELINE_HEADING in headings_blob

    cited_ps = {int(n) for n in _INLINE_P_CITATION_RE.findall(text)}
    # [Pn] numbers off the patents table's own "#" column, not the source appendix -- so "every
    # cited [Pn] has an appendix row" means every [Pn] number also appears as a row index "n" in
    # the "טבלת פטנטים" table, checked via the same leading "| n |" column pattern.
    table_row_ns = {int(n) for n in re.findall(r"^\|\s*(\d+)\s*\|", text, re.MULTILINE)}
    orphan_ps = sorted(cited_ps - table_row_ns) if cited_ps else []

    checks = [
        Check(
            "timeline_section_present",
            passed=timeline_present,
            weight=1.0,
            evidence=f"headings: {[h for h, _b in sections]}",
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
                evidence=f"{isolated}/{total} distinct patent-number tokens wrapped in <bdi>" if total else "no patent-number tokens found in HTML",
            )
        )
        n_extra = total

    return DomainScore(domain="D8", score_0_100=weighted_score(checks), checks=checks, n=len(sections) + n_extra, note=str(md_path))
