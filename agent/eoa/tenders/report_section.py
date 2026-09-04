"""Daily-report integration for tenders/forecasts (section 5.2 / FR-5.2).

Deliberately separate from ``eoa.report.daily`` (which is owned/edited concurrently) -- this module
only *collects and renders*; ``daily.py`` wires it in with a small additive block that passes the
result through ``eoa.report.docx_builder``'s existing ``extra_sections``/``tables`` hooks (already
used by the weekly/monthly reports), so nothing here touches the LLM-drafted ``DailyReportDraft``
schema or the citation QA gate.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.db import connection

SECTION_TITLE_HE = "מכרזים, RFI/RFP ותחזית"

_STATUS_HE = {"open": "פתוח", "closed": "סגור", "awarded": "הוענק", "unknown": "לא ידוע"}


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def collect_tenders(
    period_start: dt.date | None = None, period_end: dt.date | None = None, *, open_limit: int = 15
) -> dict[str, list[dict[str, Any]]]:
    """Open tenders (most urgent deadline first) + forecasts created/refreshed within the report
    period, most likely first. Never raises -- both queries are simple/DB-only, callers wrap this
    in a try/except anyway (a report section is never allowed to break the whole daily report)."""
    open_tenders = _fetchall(
        "SELECT * FROM tenders WHERE status = 'open' "
        "ORDER BY deadline ASC NULLS LAST, relevance DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {"limit": open_limit},
    )
    if period_start is not None and period_end is not None:
        new_forecasts = _fetchall(
            "SELECT * FROM tender_forecasts WHERE updated_at::date BETWEEN %(start)s AND %(end)s "
            "ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT 15",
            {"start": period_start, "end": period_end},
        )
    else:
        new_forecasts = _fetchall(
            "SELECT * FROM tender_forecasts ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT 15"
        )
    return {"open_tenders": open_tenders, "new_forecasts": new_forecasts}


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def tenders_extra_section(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """``extra_sections`` entry (see ``eoa.report.docx_builder._add_extra_sections``) -- deterministic
    prose, positioned ``after_outlook`` so it reads as a standalone appendix-style section, never
    mixed into the LLM-drafted/QA-gated body."""
    open_tenders = data.get("open_tenders") or []
    new_forecasts = data.get("new_forecasts") or []

    lines: list[str] = []
    if open_tenders:
        lines.append(f"{len(open_tenders)} מכרזים/RFI/RFP פתוחים הרלוונטיים לתחומי EO/IR/CV, ממוינים לפי דדליין:")
        for t in open_tenders[:10]:
            lines.append(
                f"- {t.get('title') or '—'} | {t.get('agency') or t.get('country') or '—'} | "
                f"דדליין: {_fmt_date(t.get('deadline'))} | {t.get('url') or '—'}"
            )
    else:
        lines.append("לא זוהו כרגע מכרזים/RFI/RFP פתוחים בתחומי העניין.")

    lines.append("")
    if new_forecasts:
        lines.append("תחזיות מכרזים עתידיים (מבוססות אירועי פלטפורמות אחרונים):")
        for f in new_forecasts[:10]:
            likelihood = f.get("likelihood")
            pct = f"{likelihood:.0%}" if isinstance(likelihood, int | float) else "—"
            lines.append(
                f"- {f.get('platform') or '—'} ({f.get('buyer_country') or '—'}): "
                f"{f.get('payload_need') or '—'} — סבירות {pct}, חלון "
                f"{_fmt_date(f.get('window_from'))} עד {_fmt_date(f.get('window_to'))}. "
                f"{f.get('rationale_he') or ''}"
            )
    else:
        lines.append("לא נוצרו תחזיות מכרזים חדשות בתקופה זו.")

    return {"title_he": SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def tenders_table(data: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """``tables`` entry (see ``eoa.report.docx_builder._add_generic_table``) -- the open-tenders
    board. ``None`` when there is nothing to show (the caller skips an empty table)."""
    open_tenders = data.get("open_tenders") or []
    if not open_tenders:
        return None
    headers = ["כותרת", "מדינה", "גורם מזמין", "דדליין", "סטטוס", "קישור"]
    rows = [
        [
            t.get("title") or "—",
            t.get("country") or "—",
            t.get("agency") or "—",
            _fmt_date(t.get("deadline")),
            _STATUS_HE.get(t.get("status"), t.get("status") or "—"),
            t.get("url") or "—",
        ]
        for t in open_tenders[:15]
    ]
    return {"title_he": "מכרזים פתוחים (EO/IR)", "headers": headers, "rows": rows}
