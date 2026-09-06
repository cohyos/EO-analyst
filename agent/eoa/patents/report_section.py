"""Report integration for patents/IP (A14) -- mirrors ``eoa.tenders.report_section``'s shape:
deliberately separate collect+render helpers that the weekly/monthly/BD-territory report builders
wire in with one additive call each through ``eoa.report.docx_builder``'s existing
``extra_sections``/``tables`` hooks, never touching those builders' own LLM-drafted/QA-gated
sections.

Patents have no ``items`` row of their own (unlike tenders), so the tables below use a plain
sequential "#" column rather than the reports' global ``[n]`` citation system -- there is no
``items.id`` to cite.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.db import connection
from eoa.pipeline.entity_normalize import resolve_canonical
from eoa.report.geography import normalize_country

WEEKLY_SECTION_TITLE_HE = "פטנטים ו-IP"
MONTHLY_SECTION_TITLE_HE = "נוף פטנטים -- סיכום חודשי"
BD_SECTION_TITLE_HE = "מיצוב IP של מתחרים בטריטוריה"


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _fmt_date(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else ("—" if value is None else str(value))


# --------------------------------------------------------------------------
# weekly: new filings/grants in the report window
# --------------------------------------------------------------------------


def collect_patents_window(period_start: dt.date, period_end: dt.date, *, limit: int = 15) -> dict[str, Any]:
    """New/updated patent rows within ``[period_start, period_end]`` (by ``created_at``, i.e. when
    this system first saw them -- not the patent's own publication date, which can predate the
    scan by months). Never raises (a report section must never break the whole report)."""
    try:
        rows = _fetchall(
            "SELECT * FROM patents WHERE created_at::date BETWEEN %(start)s AND %(end)s "
            "ORDER BY value_score DESC NULLS LAST, id DESC LIMIT %(limit)s",
            {"start": period_start, "end": period_end, "limit": limit},
        )
    except Exception:
        rows = []
    return {"new_patents": rows}


def patents_extra_section(data: dict[str, Any]) -> dict[str, Any]:
    new_patents = data.get("new_patents") or []
    lines: list[str] = []
    if new_patents:
        lines.append(f"{len(new_patents)} פטנטים/פרסומים חדשים בתחומי EO/IR/CV זוהו השבוע:")
        for p in new_patents[:10]:
            assignees = ", ".join(p.get("assignees") or []) or "—"
            score = p.get("value_score")
            lines.append(
                f"- {p.get('title') or p.get('pub_number')} | {assignees} | "
                f"תת-תחום: {p.get('subdomain') or '—'} | ציון-ערך: {score if score is not None else '—'}"
            )
    else:
        lines.append("לא זוהו פטנטים/פרסומים חדשים בתחומי העניין השבוע.")
    return {"title_he": WEEKLY_SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def patents_table(data: dict[str, Any]) -> dict[str, Any] | None:
    new_patents = data.get("new_patents") or []
    if not new_patents:
        return None
    headers = ["#", "מספר פרסום", "כותרת", "בעלים", "תת-תחום", "ציון-ערך"]
    rows = [
        [
            i + 1,
            p.get("pub_number") or "—",
            p.get("title") or "—",
            ", ".join(p.get("assignees") or []) or "—",
            p.get("subdomain") or "—",
            p.get("value_score") if p.get("value_score") is not None else "—",
        ]
        for i, p in enumerate(new_patents[:15])
    ]
    return {"title_he": "פטנטים חדשים (EO/IR)", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# monthly: landscape summary by subdomain + top assignees
# --------------------------------------------------------------------------


def collect_patents_landscape(*, lookback_days: int = 90, limit: int = 12) -> dict[str, Any]:
    try:
        since = dt.date.today() - dt.timedelta(days=lookback_days)
        by_subdomain = _fetchall(
            "SELECT COALESCE(subdomain, 'לא מסווג') AS subdomain, count(*) AS c FROM patents "
            "WHERE created_at::date >= %(since)s GROUP BY subdomain ORDER BY c DESC LIMIT %(limit)s",
            {"since": since, "limit": limit},
        )
        by_assignee = _fetchall(
            "SELECT assignee, count(*) AS c FROM ("
            "  SELECT unnest(assignees) AS assignee FROM patents WHERE created_at::date >= %(since)s"
            ") s WHERE assignee IS NOT NULL GROUP BY assignee ORDER BY c DESC LIMIT %(limit)s",
            {"since": since, "limit": limit},
        )
    except Exception:
        by_subdomain, by_assignee = [], []
    return {"by_subdomain": by_subdomain, "by_assignee": by_assignee}


def patents_landscape_extra_section(data: dict[str, Any]) -> dict[str, Any]:
    by_subdomain = data.get("by_subdomain") or []
    by_assignee = data.get("by_assignee") or []
    lines: list[str] = []
    if by_subdomain:
        lines.append("התפלגות פטנטים לפי תת-תחום (90 הימים האחרונים):")
        lines += [f"- {r['subdomain']}: {r['c']}" for r in by_subdomain]
    else:
        lines.append("לא נאספו מספיק פטנטים בחודש האחרון לסיכום נוף.")
    lines.append("")
    if by_assignee:
        lines.append("בעלי הפטנטים הפעילים ביותר:")
        lines += [f"- {r['assignee']}: {r['c']}" for r in by_assignee]
    return {"title_he": MONTHLY_SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def patents_landscape_table(data: dict[str, Any]) -> dict[str, Any] | None:
    by_assignee = data.get("by_assignee") or []
    if not by_assignee:
        return None
    return {
        "title_he": "בעלי פטנטים מובילים (חודשי)",
        "headers": ["בעלים", "מספר פטנטים"],
        "rows": [[r["assignee"], r["c"]] for r in by_assignee],
    }


# --------------------------------------------------------------------------
# BD territory: competitor IP position (assignees whose country == territory)
# --------------------------------------------------------------------------


def collect_patents_bd(territory: str, *, lookback_days: int = 365, limit: int = 15) -> dict[str, Any]:
    """Patents whose assignee list includes at least one watchlist/curated company known to be
    headquartered in ``territory`` (normalized via ``eoa.report.geography.normalize_country``,
    same convention ``eoa.report.bd_territory`` already uses)."""
    code = normalize_country(territory)
    try:
        since = dt.date.today() - dt.timedelta(days=lookback_days)
        rows = _fetchall(
            "SELECT * FROM patents WHERE created_at::date >= %(since)s "
            "AND assignees IS NOT NULL ORDER BY value_score DESC NULLS LAST, id DESC",
            {"since": since},
        )
    except Exception:
        rows = []
    matched = []
    for row in rows:
        for assignee in row.get("assignees") or []:
            canonical = resolve_canonical(assignee)
            if canonical and normalize_country(canonical.get("country") or "") == code:
                matched.append(row)
                break
        if len(matched) >= limit:
            break
    return {"territory": code, "competitor_patents": matched}


def patents_bd_extra_section(data: dict[str, Any]) -> dict[str, Any]:
    matched = data.get("competitor_patents") or []
    territory = data.get("territory") or "—"
    lines: list[str] = []
    if matched:
        lines.append(f"מיקוד IP של מתחרים בטריטוריה {territory} (12 החודשים האחרונים):")
        for p in matched[:10]:
            lines.append(
                f"- {p.get('title') or p.get('pub_number')} | {', '.join(p.get('assignees') or []) or '—'} | "
                f"ציון-ערך: {p.get('value_score') if p.get('value_score') is not None else '—'}"
            )
    else:
        lines.append(f"לא זוהו פטנטים של מתחרים בטריטוריה {territory} בתקופה זו.")
    return {"title_he": BD_SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def patents_bd_table(data: dict[str, Any]) -> dict[str, Any] | None:
    matched = data.get("competitor_patents") or []
    if not matched:
        return None
    headers = ["#", "כותרת", "בעלים", "ציון-ערך"]
    rows = [
        [
            i + 1,
            p.get("title") or p.get("pub_number") or "—",
            ", ".join(p.get("assignees") or []) or "—",
            p.get("value_score") if p.get("value_score") is not None else "—",
        ]
        for i, p in enumerate(matched[:15])
    ]
    return {"title_he": "פטנטים של מתחרים בטריטוריה", "headers": headers, "rows": rows}
