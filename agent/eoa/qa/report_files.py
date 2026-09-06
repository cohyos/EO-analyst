"""Locate the latest report artifact files on disk for D6/D7/D8 -- pure filesystem globbing, no DB.

Reports are written to ``output/reports/<kind>_<slug>_<date>.{md,html,docx}`` by
``eoa.report.daily``/``weekly``/``bd_territory``/``patents`` (see docs/CONVENTIONS.md's layout and
``docs/MODULES.md`` for the exact builders). This module only reads whatever is already on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _reports_dir() -> Path:
    # repo root is two levels up from agent/eoa/qa/
    return Path(__file__).resolve().parents[3] / "output" / "reports"


def _latest_by_date(pattern: str, reports_dir: Path | None = None) -> Path | None:
    base = reports_dir or _reports_dir()
    candidates = sorted(base.glob(pattern), key=lambda p: _DATE_RE.search(p.name).group(1) if _DATE_RE.search(p.name) else "")
    return candidates[-1] if candidates else None


def latest_daily_md(reports_dir: Path | None = None) -> Path | None:
    return _latest_by_date("daily_*.md", reports_dir)


def latest_weekly_md(reports_dir: Path | None = None) -> Path | None:
    return _latest_by_date("weekly_*.md", reports_dir)


def latest_bd_reports(reports_dir: Path | None = None) -> list[Path]:
    """One latest file per territory code (``bd_<territory>_<date>.md``)."""
    base = reports_dir or _reports_dir()
    by_territory: dict[str, Path] = {}
    for path in base.glob("bd_*.md"):
        m = re.match(r"bd_([a-z]+)_\d{4}-\d{2}-\d{2}\.md$", path.name)
        if not m:
            continue
        territory = m.group(1)
        existing = by_territory.get(territory)
        if existing is None or path.name > existing.name:
            by_territory[territory] = path
    return list(by_territory.values())


def latest_patent_survey_md(reports_dir: Path | None = None) -> Path | None:
    return _latest_by_date("patent_survey_*.md", reports_dir)


def latest_patent_survey_html(reports_dir: Path | None = None) -> Path | None:
    return _latest_by_date("patent_survey_*.html", reports_dir)
