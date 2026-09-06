"""D7 -- business-development territory report deterministic checks (QA_CONTINUOUS_LOOP.md D7).

Operates on rendered ``output/reports/bd_<territory>_<date>.md`` files. Reuses
``eoa.report.bd_territory._COMPETITOR_PROMOTION_VERBS`` (the exact verb list the report builder
itself uses to reject a perspective violation before ever rendering) so this QA check can never
drift from what the live "BD-1" perspective gate already enforces.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from eoa.config import settings
from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.report.bd_territory import _COMPETITOR_PROMOTION_VERBS

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_CONFERENCES_HEADING = "כנסים"
_ACTIONS_HEADING_KEYWORDS = ("פעולות", "המלצ")
_DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})")


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _non_israeli_watchlist_names() -> list[str]:
    wl = settings().watchlist.get("companies", []) or []
    names = []
    for c in wl:
        if (c.get("country") or "").upper() != "IL":
            names.append(c.get("name", ""))
            names.extend(c.get("aliases") or [])
    return [n for n in names if n]


def _empty_headings(sections: list[tuple[str, str]]) -> list[str]:
    return [h for h, body in sections if not body.strip() or body.strip() in ("—", "-", "")]


def _actions_section_text(sections: list[tuple[str, str]]) -> str:
    return "\n".join(body for h, body in sections if any(kw in h for kw in _ACTIONS_HEADING_KEYWORDS))


def _competitor_promotion_hits(actions_text: str, competitor_names: list[str]) -> list[str]:
    """Promotion-verb + watchlist-competitor-name hits, scoped to the recommended-actions
    section only -- mirrors ``eoa.report.bd_territory._action_promoted_competitor``, which checks
    only ``action_he``/``rationale_he`` on each ``BdAction``, never the report's market-overview
    prose (which legitimately names competitors using the same verbs in a purely descriptive
    sense, e.g. "השוק מציג התעצמות טכנולוגית ... Leonardo DRS" -- not a recommendation at all)."""
    hits = []
    for line in actions_text.splitlines():
        if any(verb in line for verb in _COMPETITOR_PROMOTION_VERBS):
            for name in competitor_names:
                if name and name in line:
                    hits.append(f"{name}: {line.strip()[:100]}")
    return hits


def _conference_dates_match_db(sections: list[tuple[str, str]], conn: Any) -> tuple[int, int]:
    """(mismatches, total_checked) comparing every conference date-range mentioned in the report's
    conferences section against the ``conferences`` table by name."""
    conf_body = ""
    for heading, body in sections:
        if _CONFERENCES_HEADING in heading:
            conf_body = body
            break
    if not conf_body or conn is None:
        return 0, 0
    mismatches = 0
    total = 0
    with conn.cursor() as cur:
        for line in conf_body.splitlines():
            if "|" not in line or line.strip().startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            name, date_range = cells[0], cells[1]
            m = _DATE_RANGE_RE.search(date_range)
            if not m or name in ("שם",):
                continue
            total += 1
            cur.execute("SELECT start_date FROM conferences WHERE name = %s", (name,))
            row = cur.fetchone()
            if row is None:
                mismatches += 1
                continue
            reported_start = dt.date.fromisoformat(m.group(1))
            if row["start_date"] != reported_start:
                mismatches += 1
    return mismatches, total


def score_D7(md_paths: list[Path], conn: Any = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D7: deterministic checks over the latest BD report per territory."""
    md_paths = [p for p in md_paths if p and p.exists()]
    if not md_paths:
        return DomainScore(domain="D7", score_0_100=None, checks=[], n=0, note="no bd report files found")

    competitor_names = _non_israeli_watchlist_names()
    total_mismatch = 0
    total_checked = 0
    empty_heading_hits: list[str] = []
    promotion_hits: list[str] = []
    no_actions: list[str] = []

    for path in md_paths:
        text = path.read_text(encoding="utf-8")
        sections = _sections(text)
        empty_heading_hits.extend(f"{path.name}:{h}" for h in _empty_headings(sections))
        actions_text = _actions_section_text(sections)
        promotion_hits.extend(f"{path.name}:{hit}" for hit in _competitor_promotion_hits(actions_text, competitor_names))
        mismatches, checked = _conference_dates_match_db(sections, conn)
        total_mismatch += mismatches
        total_checked += checked
        has_actions = any(
            any(kw in h for kw in _ACTIONS_HEADING_KEYWORDS) and body.strip() for h, body in sections
        )
        if not has_actions:
            no_actions.append(path.name)

    checks = [
        Check(
            "conference_dates_match_db",
            passed=total_mismatch == 0,
            weight=1.5,
            evidence=f"{total_checked - total_mismatch}/{total_checked} conference dates matched the DB" if total_checked else "no conference rows to check",
        ),
        Check(
            "no_empty_headings",
            passed=len(empty_heading_hits) == 0,
            weight=1.0,
            evidence=f"empty headings: {empty_heading_hits[:10]}",
        ),
        Check(
            "no_competitor_promotion_language",
            passed=len(promotion_hits) == 0,
            weight=2.5,
            evidence=f"promotion-verb hits on watchlist competitors: {promotion_hits[:10]}",
        ),
        Check(
            "actions_table_nonempty",
            passed=len(no_actions) == 0,
            weight=2.0,
            evidence=f"reports with no populated actions/recommendations section: {no_actions}",
        ),
    ]
    return DomainScore(domain="D7", score_0_100=weighted_score(checks), checks=checks, n=len(md_paths))
