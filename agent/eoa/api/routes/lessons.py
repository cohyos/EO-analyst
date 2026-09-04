"""`GET/POST /api/lessons`, `DELETE /api/lessons/{id}`."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from eoa.api import services
from eoa.api.errors import not_found

router = APIRouter(tags=["lessons"])


class LessonCreate(BaseModel):
    kind: str
    text: str


@router.get("/lessons")
def list_lessons() -> list[dict]:
    return services.list_lessons()


@router.post("/lessons")
def create_lesson(body: LessonCreate) -> dict:
    return services.create_lesson(body.kind, body.text)


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int) -> dict:
    # Soft-delete (active=false): lessons are a persistent calibration trail
    # (docs/CONVENTIONS.md "provenance everywhere"), so a `DELETE` deactivates
    # rather than destroys the row.
    ok = services.deactivate_lesson(lesson_id)
    if not ok:
        raise not_found("הלקח לא נמצא")
    return {"ok": True}
