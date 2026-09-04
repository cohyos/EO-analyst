"""`GET /api/clarifications`, `POST /api/clarifications/{id}/answer`."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["clarifications"])


class AnswerRequest(BaseModel):
    answer: str


@router.get("/clarifications")
def list_clarifications(open: bool = False) -> list[dict]:
    return services.list_clarifications(open)


@router.post("/clarifications/{clarification_id}/answer")
def answer(clarification_id: int, body: AnswerRequest) -> dict:
    row = services.answer_clarification(clarification_id, body.answer)
    if row is None:
        raise not_found("הבקשה להבהרה לא נמצאה")
    return row
