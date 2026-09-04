"""FR-11: the daily/weekly feedback survey -- question bank, rotation, and answer ingestion.

`QUESTION_BANK` holds the 12 rotating Hebrew questions (FR-11.1: ~70%
choice/scale, ~30% open). `create_for_report(report_id)` returns the
existing survey for that report or creates a new one, rotating the subset
deterministically off `report_id` as the seed (same report → same
questions, every time, without persisting a "why these 6" decision
anywhere else). `ingest_answers(survey_id, answers)` is the FR-11.3 writer:
closed answers tagged with a `signal` feed a `style` lesson when they cross
a threshold; open answers are run through `parse_free_text()` and, when it
recognizes a watchlist request, both a `watchlist` lesson AND a
`clarifications` row proposing the change are written (never
`config/watchlist.yaml` directly -- that stays a human decision, per FR-10).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

import structlog
from psycopg.types.json import Json

from eoa.db import connection
from eoa.memory.relational import add_lesson

log = structlog.get_logger(__name__)

# 12 rotating Hebrew questions; 8 choice/scale (~67%) + 4 open (~33%), close to
# FR-11.1's ~70/30 split while keeping every open question genuinely open-ended.
# `signal`, where present, tags a closed question for `_ingest_closed_answer()`.
QUESTION_BANK: list[dict[str, Any]] = [
    {
        "id": "q1",
        "type": "scale",
        "text_he": "עד כמה הדוח היום היה רלוונטי לתחומי המעקב שלך?",
        "options": ["1", "2", "3", "4", "5"],
        "signal": "relevance",
    },
    {
        "id": "q2",
        "type": "scale",
        "text_he": "עד כמה הציונים (red/orange/yellow) תאמו את השיפוט שלך?",
        "options": ["1", "2", "3", "4", "5"],
        "signal": "triage_accuracy",
    },
    {
        "id": "q3",
        "type": "choice",
        "text_he": "האם היו כתבות שסווגו red/orange שהיו צריכות רמה נמוכה יותר?",
        "options": ["כן, הרבה", "כן, מעט", "לא"],
    },
    {
        "id": "q4",
        "type": "choice",
        "text_he": "האם היו כתבות רלוונטיות שהיו צריכות סיווג גבוה יותר?",
        "options": ["כן, הרבה", "כן, מעט", "לא"],
    },
    {
        "id": "q5",
        "type": "choice",
        "text_he": "האם רשימת החברות למעקב (watchlist) עדכנית?",
        "options": ["כן", "חסרות חברות", "יש חברות מיותרות"],
    },
    {
        "id": "q6",
        "type": "scale",
        "text_he": "עד כמה הסיכומים בעברית (summary/so-what) היו ברורים ומדויקים?",
        "options": ["1", "2", "3", "4", "5"],
        "signal": "clarity",
    },
    {
        "id": "q7",
        "type": "choice",
        "text_he": "האם תדירות הדוחות (יומי/שבועי) מתאימה?",
        "options": ["מתאימה", "יותר מדי", "פחות מדי"],
        "signal": "frequency",
    },
    {
        "id": "q8",
        "type": "scale",
        "text_he": "עד כמה החקירות המעמיקות (deep search) הביאו ערך מוסף?",
        "options": ["1", "2", "3", "4", "5"],
    },
    {
        "id": "q9",
        "type": "open",
        "text_he": "אילו נושאים או חברות היית רוצה שנעקוב אחריהם ועדיין לא עוקבים?",
        "options": None,
    },
    {"id": "q10", "type": "open", "text_he": "האם היו טעויות עובדתיות בדוח? אם כן, פרט/י.", "options": None},
    {"id": "q11", "type": "open", "text_he": "מה היה הכי שימושי בדוח היום/השבוע?", "options": None},
    {"id": "q12", "type": "open", "text_he": "הערות חופשיות נוספות לשיפור המערכת.", "options": None},
]

_SCALE_LOW_THRESHOLD = 2

# --------------------------------------------------------------------------
# parse_free_text heuristics (FR-11.2: "הסוכן מפרסר גם תשובות חופשיות")
# --------------------------------------------------------------------------

_SHORTER_RE = re.compile(r"יותר\s*קצר|קצר\s*יותר")
_LONGER_RE = re.compile(r"יותר\s*ארוך|ארוך\s*יותר")
_ADD_WATCHLIST_RE = re.compile(r"להוסיף\s+מעקב\s+אחרי\s+(.+?)(?:[.,\n]|$)")
_NOT_RELEVANT_RE = re.compile(r"לא\s+רלוונטי\s*:\s*(.+?)(?:[.,\n]|$)")


def parse_free_text(text: str) -> dict[str, Any]:
    """Heuristically map Hebrew free-text feedback to structured fields (FR-11.2).

    Recognizes: a request for shorter/longer reports ("יותר קצר"/"יותר
    ארוך"), a watchlist addition ("להוסיף מעקב אחרי X"), and an explicit
    irrelevance marker ("לא רלוונטי: Y"). Returns only the fields it
    actually recognized -- an empty dict when nothing matches, never a
    guess at intent.
    """
    if not text or not text.strip():
        return {}
    out: dict[str, Any] = {}
    if _SHORTER_RE.search(text):
        out["length"] = "shorter"
    elif _LONGER_RE.search(text):
        out["length"] = "longer"
    m = _ADD_WATCHLIST_RE.search(text)
    if m:
        out["add_watchlist"] = m.group(1).strip()
    m = _NOT_RELEVANT_RE.search(text)
    if m:
        out["not_relevant"] = m.group(1).strip()
    return out


# --------------------------------------------------------------------------
# rotation
# --------------------------------------------------------------------------


def rotating_subset(seed: int, k: int = 6) -> list[dict[str, Any]]:
    """Deterministically pick `k` consecutive (wrapping) questions from `QUESTION_BANK`, starting
    at `seed % len(QUESTION_BANK)` -- same `seed` always yields the same subset in the same order."""
    n = len(QUESTION_BANK)
    start = seed % n
    return [QUESTION_BANK[(start + i) % n] for i in range(k)]


# --------------------------------------------------------------------------
# DB boundary
# --------------------------------------------------------------------------


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _execute(query: str, params: Any = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)


def _survey_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "report_id": row.get("report_id"),
        "questions": row.get("questions") or [],
        "answers": row.get("answers") or {},
    }


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


def create_for_report(report_id: int | None) -> dict[str, Any]:
    """Return the not-yet-answered survey for `report_id` if one exists, else create+return a
    freshly rotated one. `report_id=None` covers the ad-hoc/no-report case (same convention as the
    previous inline implementation this replaces)."""
    if report_id is not None:
        existing = _fetchone(
            "SELECT * FROM feedback_surveys WHERE report_id = %s ORDER BY created_at DESC LIMIT 1",
            (report_id,),
        )
    else:
        existing = _fetchone(
            "SELECT * FROM feedback_surveys WHERE report_id IS NULL ORDER BY created_at DESC LIMIT 1"
        )
    if existing:
        return _survey_view(existing)

    seed = report_id if report_id is not None else int(dt.datetime.now(tz=dt.UTC).timestamp())
    questions = rotating_subset(seed)
    row = _fetchone(
        "INSERT INTO feedback_surveys (report_id, questions, answers) VALUES (%s, %s, %s) RETURNING *",
        (report_id, Json(questions), Json({})),
    )
    return (
        _survey_view(row)
        if row
        else {"id": None, "report_id": report_id, "questions": questions, "answers": {}}
    )


def _ingest_closed_answer(survey_id: int, question: dict[str, Any], value: Any) -> None:
    signal = question.get("signal")
    if signal is None:
        return
    qid = question["id"]
    source_ref = f"survey:{survey_id}:{qid}"

    if signal == "clarity" and question.get("type") == "scale":
        try:
            score = int(value)
        except (TypeError, ValueError):
            return
        if score <= _SCALE_LOW_THRESHOLD:
            add_lesson(
                "style",
                f"המשתמש דירג את בהירות הסיכומים נמוך ({score}/5) — לחדד ולקצר את summary_he/so_what_he.",
                source_ref=source_ref,
            )
    elif signal == "frequency" and question.get("type") == "choice":
        if value == "יותר מדי":
            add_lesson(
                "style",
                "המשתמש ציין שתדירות/אורך הדוחות גבוהים מדי — לשקול לצמצם.",
                source_ref=source_ref,
            )
        elif value == "פחות מדי":
            add_lesson(
                "style",
                "המשתמש ציין שתדירות הדוחות נמוכה מדי — לשקול להגביר.",
                source_ref=source_ref,
            )
    # "relevance" / "triage_accuracy" signals are intentionally not turned into
    # lessons here: low scores on those overlap with what `feedback.calibration`
    # already derives from `triage_feedback` itself, so raising a *second*,
    # differently-worded lesson from the same signal would just be noise.


def _propose_watchlist_addition(target: str, source_ref: str) -> None:
    """Record a `clarifications` row proposing the watchlist change. Per FR-10, this is a
    proposal for a human to confirm -- `config/watchlist.yaml` is never edited directly from
    survey feedback."""
    question = f'להוסיף את "{target}" לרשימת המעקב (watchlist)?'
    options = ["כן", "לא"]
    _execute(
        "INSERT INTO clarifications (kind, question, options, asked_at) VALUES (%s, %s, %s::jsonb, now())",
        ("watchlist_proposal", question, json.dumps(options, ensure_ascii=False)),
    )
    log.info("feedback.watchlist_proposal_created", target=target, source_ref=source_ref)


def _ingest_open_answer(survey_id: int, qid: str, text: str) -> None:
    parsed = parse_free_text(text)
    source_ref = f"survey:{survey_id}:{qid}"

    if "add_watchlist" in parsed:
        target = parsed["add_watchlist"]
        add_lesson("watchlist", f"המשתמש ביקש להוסיף מעקב אחרי: {target}", source_ref=source_ref)
        _propose_watchlist_addition(target, source_ref)
    if "length" in parsed:
        wording = "קצרים יותר" if parsed["length"] == "shorter" else "ארוכים יותר"
        add_lesson("style", f"המשתמש ביקש דוחות {wording}.", source_ref=source_ref)
    if "not_relevant" in parsed:
        add_lesson("decision", f"המשתמש סימן כלא רלוונטי: {parsed['not_relevant']}", source_ref=source_ref)
    if not parsed:
        # Free text the heuristic couldn't structure is still preserved as a
        # `decision` lesson -- FR-11.3 says the feedback is written to
        # `lessons`, and an unrecognized comment must never be silently
        # dropped just because it didn't match a pattern.
        add_lesson("decision", text, source_ref=source_ref)


def ingest_answers(survey_id: int, answers: dict[str, Any]) -> dict[str, Any] | None:
    """Persist `answers` onto the survey and derive lessons/clarifications from them (FR-11.3).
    Returns the full updated `feedback_surveys` row, or `None` if `survey_id` doesn't exist."""
    row = _fetchone("SELECT * FROM feedback_surveys WHERE id = %s", (survey_id,))
    if row is None:
        return None
    _execute(
        "UPDATE feedback_surveys SET answers = %s, answered_at = now() WHERE id = %s",
        (Json(answers), survey_id),
    )

    questions_by_id = {q["id"]: q for q in (row.get("questions") or [])}
    for qid, value in answers.items():
        question = questions_by_id.get(qid)
        if question is None:
            continue
        qtype = question.get("type")
        if qtype in ("choice", "scale"):
            _ingest_closed_answer(survey_id, question, value)
        elif qtype == "open" and isinstance(value, str) and value.strip():
            _ingest_open_answer(survey_id, qid, value.strip())

    return _fetchone("SELECT * FROM feedback_surveys WHERE id = %s", (survey_id,))
