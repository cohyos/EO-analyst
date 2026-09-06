"""D6 -- daily/weekly report deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D6).

Operates on the already-rendered ``output/reports/{daily,weekly}_<date>.md`` file (Markdown, the
same content the ``.docx``/``.html`` siblings are built from -- see ``eoa.report.daily``/``weekly``
and ``docs/MODULES.md``). Reuses ``eoa.report.qa_citations``'s sentence-splitting/factuality/
citation-extraction helpers rather than re-implementing them; link liveness reuses
``eoa.qa.links.check_links``.
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


def score_D6(md_path: Path | None, *, run_link_check: bool = True) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D6: deterministic checks over one rendered daily/weekly report Markdown file.

    ``md_path=None`` (no report file found on disk for the round) yields a "manual only" result
    rather than a misleading 0.
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

    all_sentences = [s for _h, body in sections for s in split_sentences(body) if len(s.strip()) > 15]
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
    ]

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
