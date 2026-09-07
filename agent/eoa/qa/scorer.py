"""Aggregates the ten ``eoa.qa.d*.score_Dn`` domain scorers behind one import surface.

The brief for this package described every domain scorer uniformly as ``score_Dn(sample, conn)``.
In practice D1-D4 genuinely are per-item/per-job samples scored against a live connection, but
D6-D8 score one *report file* (there is no meaningful "sample of a report"), D9 scores the whole
current ``tenders``/``conferences``/``sources`` table state (small, config-driven tables where
sampling would just hide real rows), and D10 scores a Playwright JSON run with no DB/sample
involvement at all. Each ``score_Dn`` therefore keeps the parameter shape its own domain actually
needs (documented in its own module); this module is the single place that knows how to call all
ten uniformly from ``scripts/qa_score.py``.
"""

from __future__ import annotations

from typing import Any

from eoa.qa.d1_classify import score_D1
from eoa.qa.d2_summary import score_D2
from eoa.qa.d3_events_entities import score_D3
from eoa.qa.d4_investigations import score_D4
from eoa.qa.d5_chat import score_D5
from eoa.qa.d6_daily_report import score_D6
from eoa.qa.d7_bd_report import score_D7
from eoa.qa.d8_patent_survey import score_D8
from eoa.qa.d9_tenders_conferences import score_D9
from eoa.qa.d10_ui_e2e import score_D10
from eoa.qa.report_files import (
    latest_bd_reports,
    latest_daily_md,
    latest_monthly_md,
    latest_patent_survey_html,
    latest_patent_survey_md,
    latest_product_line_reports,
    latest_weekly_md,
)
from eoa.qa.sample import ResolvedSample
from eoa.qa.types import DomainScore

#: Weights from docs/QA_CONTINUOUS_LOOP.md section 1's table (sum to 100).
DOMAIN_WEIGHTS: dict[str, float] = {
    "D1": 15,
    "D2": 15,
    "D3": 8,
    "D4": 12,
    "D5": 10,
    "D6": 15,
    "D7": 8,
    "D8": 7,
    "D9": 5,
    "D10": 5,
}


def score_all_domains(
    resolved: ResolvedSample,
    conn: Any,
    *,
    golden_questions: list[str],
    run_link_check: bool = True,
    run_e2e: bool = False,
) -> dict[str, DomainScore]:
    """Run all ten deterministic domain scorers for one round.

    ``resolved`` carries the already-fetched item rows and investigation job ids (see
    :mod:`eoa.qa.sample`); report file paths are re-resolved fresh each call (``latest_*``) since
    a new report may have been produced since the sample was chosen.
    """
    items_all = resolved.golden_items + resolved.rotating_items
    daily_or_weekly = latest_daily_md() or latest_weekly_md()

    return {
        "D1": score_D1(items_all, conn),
        "D2": score_D2(items_all, conn),
        "D3": score_D3(items_all, conn),
        "D4": score_D4(resolved.investigation_job_ids, conn, report_path=daily_or_weekly),
        "D5": score_D5(golden_questions, conn),
        "D6": score_D6(daily_or_weekly, run_link_check=run_link_check, monthly_path=latest_monthly_md()),
        # PL-backend (2026-09-07): product-line reports score under the same D7 domain as BD-
        # territory reports (eoa.qa.d7_bd_report's module docstring) -- one combined file list.
        "D7": score_D7(latest_bd_reports() + latest_product_line_reports(), conn),
        "D8": score_D8(latest_patent_survey_md(), latest_patent_survey_html()),
        "D9": score_D9(conn, report_path=daily_or_weekly),
        "D10": score_D10(run_e2e=run_e2e),
    }


def weighted_total(
    scores: dict[str, DomainScore], *, judge_scores: dict[str, float] | None = None
) -> float | None:
    """Overall weighted total per section 1: domain score = 0.5*auto + 0.5*judge (when a judge
    score exists for that domain); a domain with ``score_0_100=None`` and no judge score is
    excluded from both the numerator and the weight denominator (never silently counted as 0)."""
    judge_scores = judge_scores or {}
    num = 0.0
    den = 0.0
    for domain, weight in DOMAIN_WEIGHTS.items():
        ds = scores.get(domain)
        auto = ds.score_0_100 if ds else None
        judge = judge_scores.get(domain)
        if auto is None and judge is None:
            continue
        if auto is not None and judge is not None:
            domain_score = 0.5 * auto + 0.5 * judge
        else:
            domain_score = auto if auto is not None else judge
        num += weight * domain_score
        den += weight
    if den == 0:
        return None
    return round(num / den, 1)
