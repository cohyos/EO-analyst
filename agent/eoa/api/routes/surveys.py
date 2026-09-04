"""`GET /api/surveys/latest`, `POST /api/surveys/{id}/answers`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["surveys"])


class SurveyAnswers(BaseModel):
    answers: dict[str, Any]


@router.get("/surveys/latest")
def latest_survey() -> dict:
    return services.latest_survey()


@router.post("/surveys/{survey_id}/answers")
def submit_answers(survey_id: int, body: SurveyAnswers) -> dict:
    row = services.submit_survey_answers(survey_id, body.answers)
    if row is None:
        raise not_found("הסקר לא נמצא")
    return row
