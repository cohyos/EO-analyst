"""`GET /api/runs/current` -- U4/F17 (docs/REVIEW_2026-09-05.md).

What "הרץ עכשיו" (run now) kicked off, if anything: the active primary run's stage-by-stage
progress and an ETA, plus any other job (e.g. a `deep_search`) a separate worker has claimed
concurrently -- F17: job #70 ran to completion without the analyst ever seeing it in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter

from eoa.api import services

router = APIRouter(tags=["runs"])


@router.get("/runs/current")
def get_current_run() -> dict:
    return services.current_run_progress()
