"""Deterministic patent value-score proxy (A14).

**This is not a financial valuation.** ``value_score`` (0-100) is a documented, deterministic
proxy for "how much perceived strategic weight does this patent carry", built from five signals
this module actually has data for:

1. **Jurisdiction breadth** (0-25) -- how many countries/regions the family is filed in
   (``patents.jurisdictions``), the closest proxy available here to true INPADOC family size
   without EPO OPS keys: a wider family costs more to prosecute and signals the assignee expected
   it to matter in more markets.
2. **Forward citations, age-normalised** (0-25) -- ``forward_citations`` divided by the patent's
   age in years (from ``publication_date``); a young, heavily-cited patent scores as high as an
   old one with proportionally more citations.
3. **Remaining life** (0-20) -- a rough 20-year term from ``filing_date`` (or ``priority_date``
   when filing is missing); a patent close to expiry contributes less remaining strategic value
   than a freshly-filed one, all else equal.
4. **Assignee filing velocity** (0-15) -- how many *other* patents already tracked in this DB
   share an assignee with this one (a proxy for "is this assignee actively building a portfolio
   in this space", not the patent's own intrinsic quality).
5. **Litigation flag** (0-15) -- only ever set from an explicit signal in the source data
   (``raw`` carrying a litigation marker); this project's sources essentially never populate this,
   so it defaults to 0 for almost every row -- documented here rather than silently omitted.

Every component is explained in ``value_reasons`` (Hebrew), which always ends with the mandatory
disclaimer: "מדד פרוקסי, לא הערכת שווי כספית".
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)

DISCLAIMER_HE = "מדד פרוקסי, לא הערכת שווי כספית"

_PATENT_TERM_YEARS = 20.0
_DAYS_PER_YEAR = 365.25


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _jurisdiction_score(jurisdictions: list[str] | None) -> tuple[float, str]:
    n = len({j for j in (jurisdictions or []) if j})
    score = min(n, 5) / 5 * 25
    if n == 0:
        return 0.0, "אין מידע על מדינות הגשה -- לא ניתן להעריך רוחב משפחת הפטנט"
    return score, f"הוגש/פורסם ב-{n} מדינות/אזורים -- אינדיקציה לרוחב משפחת הפטנט"


def _citation_score(
    forward_citations: int | None, publication_date: dt.date | None, today: dt.date
) -> tuple[float, str]:
    if forward_citations is None:
        return 0.0, "אין נתוני ציטוטים קדימה זמינים"
    age_years = max(1.0, (today - publication_date).days / _DAYS_PER_YEAR) if publication_date else 1.0
    rate = forward_citations / age_years
    score = _clamp01(rate / 2.0) * 25  # 2+ citations/year saturates the component
    return score, f"{forward_citations} ציטוטים קדימה (~{rate:.1f} לשנה מאז הפרסום)"


def _remaining_life_score(
    filing_date: dt.date | None, priority_date: dt.date | None, today: dt.date
) -> tuple[float, str]:
    base = filing_date or priority_date
    if base is None:
        return 10.0, "אין תאריך הגשה/עדיפות ידוע -- הונח אורך חיים נותר ממוצע"
    elapsed_years = (today - base).days / _DAYS_PER_YEAR
    remaining = max(0.0, _PATENT_TERM_YEARS - elapsed_years)
    score = _clamp01(remaining / _PATENT_TERM_YEARS) * 20
    return score, f"כ-{remaining:.0f} שנות חיים נותרות מתוך {_PATENT_TERM_YEARS:.0f} שנות תוקף משוער"


def _velocity_score(assignee_patent_count: int) -> tuple[float, str]:
    score = min(assignee_patent_count, 10) / 10 * 15
    if assignee_patent_count == 0:
        return 0.0, "אין פטנטים נוספים של אותו בעלים במאגר"
    return score, f"{assignee_patent_count} פטנטים נוספים של אותו בעלים במאגר -- קצב הגשה פעיל"


def _litigation_score(raw: dict[str, Any] | None) -> tuple[float, str | None]:
    if raw and raw.get("litigation"):
        return 15.0, "סומן כמעורב בהליך משפטי/התנגדות במקור הנתונים"
    return 0.0, None  # no signal available for almost every source -> silently contributes 0


def score_patent(
    rec: dict[str, Any],
    *,
    assignee_patent_count: int = 0,
    today: dt.date | None = None,
) -> tuple[int, list[str]]:
    """Deterministic ``(value_score, value_reasons_he)`` for one patent row (a dict with the
    ``patents`` table's own column names -- works directly on a DB row or a plain dict in tests).
    Always deterministic given the same inputs -- no LLM/network call."""
    today = today or dt.date.today()
    reasons: list[str] = []
    total = 0.0

    s, r = _jurisdiction_score(rec.get("jurisdictions"))
    total += s
    reasons.append(r)

    s, r = _citation_score(rec.get("forward_citations"), rec.get("publication_date"), today)
    total += s
    reasons.append(r)

    s, r = _remaining_life_score(rec.get("filing_date"), rec.get("priority_date"), today)
    total += s
    reasons.append(r)

    s, r = _velocity_score(assignee_patent_count)
    total += s
    reasons.append(r)

    s, r = _litigation_score(rec.get("raw"))
    total += s
    if r:
        reasons.append(r)

    reasons.append(DISCLAIMER_HE)
    return round(_clamp01(total / 100) * 100), reasons


def _assignee_patent_counts(assignee_lists: list[list[str] | None]) -> dict[str, int]:
    names = {a for lst in assignee_lists for a in (lst or []) if a}
    if not names:
        return {}
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT unnest(assignees) AS assignee, count(*) AS c FROM patents "
            "WHERE assignees && %(names)s GROUP BY assignee",
            {"names": list(names)},
        )
        return {row["assignee"]: row["c"] for row in cur.fetchall()}


def score_and_persist(limit: int = 200) -> int:
    """Score every ``patents`` row still missing a ``value_score`` (up to ``limit``), persisting
    ``value_score``/``value_reasons``. Returns the number of rows scored. Pure DB orchestration --
    the actual math is :func:`score_patent`, kept dependency-free for unit testing."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, jurisdictions, forward_citations, publication_date, filing_date, "
            "priority_date, assignees, raw FROM patents WHERE value_score IS NULL "
            "ORDER BY id LIMIT %(limit)s",
            {"limit": limit},
        )
        rows = cur.fetchall()
    if not rows:
        return 0

    counts = _assignee_patent_counts([r["assignees"] for r in rows])
    today = dt.date.today()
    scored = 0
    for row in rows:
        assignee_count = (
            max((counts.get(a, 1) - 1) for a in (row["assignees"] or [None])) if row["assignees"] else 0
        )
        value_score, reasons = score_patent(row, assignee_patent_count=max(assignee_count, 0), today=today)
        try:
            with connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE patents SET value_score = %(score)s, value_reasons = %(reasons)s WHERE id = %(id)s",
                    {"score": value_score, "reasons": reasons, "id": row["id"]},
                )
            scored += 1
        except Exception as exc:
            log.warning("patents_valuation_persist_failed", patent_id=row["id"], error=str(exc)[:200])
    log.info("patents_valuation_done", scored=scored)
    return scored
