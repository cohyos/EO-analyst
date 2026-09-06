"""D9 -- tenders / forecasts / conferences deterministic checks (docs/QA_CONTINUOUS_LOOP.md D9).

Global (not per-sampled-item) checks over the current ``tenders``, ``conferences`` and ``sources``
tables -- these are small, config-driven tables where "the whole table" is the natural QA unit,
mirroring how ``scripts/purge_stale_tenders.py`` re-checks every row rather than a sample.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from eoa.qa.types import Check, DomainScore, weighted_score

_VALID_CONFERENCE_STATUSES = frozenset({"confirmed", "estimated", "past", "cancelled"})
_SOURCE_FRESHNESS_DAYS = 7

# Round 5 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 item 12): a source-reliability
# column in the report's own appendix is landing in another engineer's file scope
# (``docx_builder.py``/``render_markdown``) this same evening -- small weight per the task brief
# ("source_reliability_column_in_appendix (weight small)").
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_APPENDIX_HEADING_HE = "נספח מקורות"
_RELIABILITY_COLUMN_KEYWORDS_HE = ("אמינות", "מהימנות")


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _source_reliability_column_check(report_path: Path | None) -> Check:
    """A reliability column (``sources.source_reliability``, per docs/CONVENTIONS.md) rendered in
    the report's own source appendix -- not just present in the DB (docs/REPORT_TEMPLATE_BENCHMARK.md
    finding D7: "the DB has this column but the appendix renderer doesn't show it")."""
    if report_path is None or not report_path.exists():
        return Check(
            "source_reliability_column_in_appendix",
            True,
            weight=0.5,
            evidence="no report file this round -- check not applicable",
        )
    text = report_path.read_text(encoding="utf-8")
    for h, body in _sections(text):
        if _APPENDIX_HEADING_HE in h:
            lines = [ln.strip() for ln in body.splitlines() if ln.strip().startswith("|")]
            if not lines:
                return Check(
                    "source_reliability_column_in_appendix",
                    False,
                    weight=0.5,
                    evidence=f"'{_APPENDIX_HEADING_HE}' heading found but no table under it",
                )
            header_cells = [c.strip() for c in lines[0].strip("|").split("|")]
            has_col = any(any(kw in c for kw in _RELIABILITY_COLUMN_KEYWORDS_HE) for c in header_cells)
            return Check(
                "source_reliability_column_in_appendix",
                has_col,
                weight=0.5,
                evidence=f"appendix header cells: {header_cells}",
            )
    return Check(
        "source_reliability_column_in_appendix",
        False,
        weight=0.5,
        evidence=f"no '{_APPENDIX_HEADING_HE}' heading found in {report_path.name}",
    )


def score_D9(conn: Any, *, report_path: Path | None = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D9: tenders/conferences/sources deterministic checks over the whole current table state.

    ``report_path`` (round 5, optional): the latest daily/weekly report Markdown, used only for
    :func:`_source_reliability_column_check`.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, status, deadline, published_at FROM tenders")
        tenders = cur.fetchall()
        cur.execute("SELECT id, status, start_date, organizer FROM conferences")
        conferences = cur.fetchall()
        cur.execute(
            "SELECT id, name, last_fetched_at FROM sources WHERE active = true"
        )
        sources = cur.fetchall()

    n = len(tenders) + len(conferences) + len(sources)
    if n == 0:
        return DomainScore(domain="D9", score_0_100=None, checks=[], n=0, note="empty tables")

    # 1. an 'open' tender with neither a deadline nor a published_at date carries no evidence it
    #    was ever actually verified against a live page (mirrors purge_stale_tenders.py's rule 5)
    #    -- it should have been stored as 'unknown', never 'open'.
    open_undated = [
        t["id"] for t in tenders if t["status"] == "open" and t["deadline"] is None and t["published_at"] is None
    ]

    # 2. conferences: status must be one of the known lifecycle values, start_date should be
    #    present for anything not cancelled, and a "1..N sequential day-of-month" pattern across
    #    the whole table is a red flag for synthetic/placeholder dates (never observed in a real
    #    scrape, where dates cluster around actual event calendars).
    status_bad = [c["id"] for c in conferences if c["status"] not in _VALID_CONFERENCE_STATUSES]
    missing_date = [c["id"] for c in conferences if c["status"] != "cancelled" and c["start_date"] is None]
    days = sorted(c["start_date"].day for c in conferences if c["start_date"] is not None)
    synthetic_sequence = len(days) >= 8 and days == list(range(1, len(days) + 1))

    # 3. every active source should have been fetched within the last week -- a source that never
    #    fetches is silently dead weight (or a scheduler regression) even if it's still marked
    #    active.
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=_SOURCE_FRESHNESS_DAYS)
    stale_sources = [s["id"] for s in sources if s["last_fetched_at"] is None or s["last_fetched_at"] < cutoff]

    checks = [
        Check(
            "tenders_open_rows_have_dates",
            passed=len(open_undated) == 0,
            weight=2.0,
            evidence=f"{len(tenders)} tenders; open-with-no-dates ids: {open_undated[:10]}",
        ),
        Check(
            "conferences_status_and_dates_real",
            passed=len(status_bad) == 0 and len(missing_date) == 0 and not synthetic_sequence,
            weight=2.0,
            evidence=(
                f"{len(conferences)} conferences; bad status ids: {status_bad[:10]}; "
                f"missing start_date ids: {missing_date[:10]}; synthetic day sequence: {synthetic_sequence}"
            ),
        ),
        Check(
            "sources_enabled_fetched_recently",
            passed=len(stale_sources) == 0,
            weight=1.0,
            evidence=(
                f"{len(sources) - len(stale_sources)}/{len(sources)} fetched within "
                f"{_SOURCE_FRESHNESS_DAYS}d; stale ids: {stale_sources[:10]}"
            ),
        ),
        _source_reliability_column_check(report_path),
    ]
    return DomainScore(domain="D9", score_0_100=weighted_score(checks), checks=checks, n=n)
