"""`POST /api/feedback/calibrate`, `GET /api/feedback/meta` (FR-3.3 / FR-11.4)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from eoa.feedback.calibration import calibrate
from eoa.feedback.meta import weekly_meta_summary

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("/calibrate")
def run_calibrate() -> dict:
    summary = calibrate()
    return {
        "period_days": summary.period_days,
        "feedback_n": summary.feedback_n,
        "domain_deltas": summary.domain_deltas,
        "source_kind_deltas": summary.source_kind_deltas,
        "biases": [
            {"scope": b.scope, "key": b.key, "n": b.n, "mean_delta": b.mean_delta} for b in summary.biases
        ],
        "lessons_created": summary.lessons_created,
        "lessons_updated": summary.lessons_updated,
        "lessons_deactivated": summary.lessons_deactivated,
    }


@router.get("/meta")
def get_meta(period_days: int = Query(7, ge=1, le=90)) -> dict:
    summary = weekly_meta_summary(period_days)
    return {
        "period_days": summary.period_days,
        "lesson_count": summary.lesson_count,
        "by_kind": summary.by_kind,
        "text_he": summary.text_he,
    }
