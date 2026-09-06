"""Stage: on-demand patent landscape survey ("סקר פטנטים", A14).

``build_patent_survey(topic)``: gather up to ``deep_limit`` patent records for a free-text topic
(reusing ``eoa.patents.scan.search_records`` -- the same EPO OPS/PatentsView/Google-Patents-search
sources as the routine scan, just deeper and scoped to one on-demand query), upsert them into
``patents`` (dedup by ``pub_number``), analyze/value the freshest of them so the report tables have
something to show, compute deterministic aggregates (CPC/assignee clustering, a yearly timeline,
top assignees, CPC-x-assignee white space, an Israeli-industry position count), synthesize a short
LLM narrative over a numbered citation registry (same ``[n]`` convention as the daily/weekly/
monthly reports -- see ``eoa.report.qa_citations``), render docx/md/html via
``eoa.report.docx_builder``, and persist both a ``reports`` row (``kind='patent_survey'``) and a
``patent_surveys`` row linking to it.

A synthesis failure (LLM unavailable/invalid output) never blocks the survey -- the report still
renders with a short, honest "ניתוח שפה טבעית לא זמין" placeholder in the narrative sections; every
deterministic table (clustering/timeline/top assignees/white space) always renders regardless.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.patents import PatentSurveySynthesisOut
from eoa.patents import scan as scan_mod
from eoa.patents.analyze import analyze_patents
from eoa.patents.valuation import score_and_persist
from eoa.pipeline.entity_normalize import resolve_canonical
from eoa.report.docx_builder import build_docx, render_html, render_markdown, save_docx, validate_docx

log = structlog.get_logger(__name__)

DEFAULT_DEEP_LIMIT = 150
MAX_ANALYZE_PER_SURVEY = 20
TOP_N_CPC = 8
TOP_N_ASSIGNEES = 10


@dataclass
class _Section:
    title_he: str
    prose_he: str


@dataclass
class _SurveyDraft:
    exec_summary_he: str
    sections: list[_Section] = field(default_factory=list)
    outlook_he: str = ""
    open_points_he: list[str] = field(default_factory=list)


@dataclass
class PatentSurveyPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    survey_id: int
    topic: str
    patent_count: int


def _slug(topic: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", topic, flags=re.UNICODE).strip("_")
    return (s or "topic")[:60]


def _report_paths(topic: str, today: dt.date) -> tuple[Path, Path, Path]:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    base = out_dir / f"patent_survey_{_slug(topic)}_{today.isoformat()}"
    return base.with_suffix(".docx"), base.with_suffix(".md"), base.with_suffix(".html")


def _fetch_patent_rows(patent_ids: list[int]) -> list[dict[str, Any]]:
    if not patent_ids:
        return []
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pub_number, title, abstract, assignees, cpc, publication_date, "
            "url, value_score, source FROM patents WHERE id = ANY(%(ids)s) "
            "ORDER BY publication_date DESC NULLS LAST, id DESC",
            {"ids": patent_ids},
        )
        return cur.fetchall()


# --------------------------------------------------------------------------
# deterministic aggregates
# --------------------------------------------------------------------------


def _cluster_by(rows: list[dict[str, Any]], field_name: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for value in row.get(field_name) or []:
            if value:
                counter[value] += 1
    return counter


def _timeline_by_year(rows: list[dict[str, Any]]) -> Counter[int]:
    counter: Counter[int] = Counter()
    for row in rows:
        d = row.get("publication_date")
        if d:
            counter[d.year] += 1
    return counter


def _white_spaces(
    rows: list[dict[str, Any]], top_cpc: list[str], top_assignees: list[str]
) -> list[tuple[str, str]]:
    """CPC x assignee combinations that never co-occur among the top clusters -- a coarse
    "unclaimed" signal (a real freedom-to-operate/white-space analysis needs a patent attorney;
    this is a landscape hint, not legal advice)."""
    observed: set[tuple[str, str]] = set()
    for row in rows:
        for cpc in row.get("cpc") or []:
            for assignee in row.get("assignees") or []:
                observed.add((cpc, assignee))
    gaps = [(c, a) for c in top_cpc for a in top_assignees if (c, a) not in observed]
    return gaps[:10]


def _israel_position(rows: list[dict[str, Any]]) -> tuple[int, list[str]]:
    israeli_companies: set[str] = set()
    count = 0
    for row in rows:
        hit = False
        for assignee in row.get("assignees") or []:
            canonical = resolve_canonical(assignee)
            if canonical and (canonical.get("country") or "").upper() == "IL":
                israeli_companies.add(canonical["name"])
                hit = True
        if hit:
            count += 1
    return count, sorted(israeli_companies)


# --------------------------------------------------------------------------
# LLM synthesis
# --------------------------------------------------------------------------


def _synthesis_data_block(
    topic: str,
    registry: list[dict[str, Any]],
    top_cpc: Counter[str],
    top_assignees: Counter[str],
    timeline: Counter[int],
    israel_count: int,
    israel_companies: list[str],
) -> str:
    lines = [f"נושא: {topic}", f'סה"כ רשומות: {len(registry)}', ""]
    lines.append(
        "קודי CPC מובילים: " + ", ".join(f"{c} ({n})" for c, n in top_cpc.most_common(TOP_N_CPC)) or "—"
    )
    lines.append(
        "בעלי פטנטים מובילים: "
        + (", ".join(f"{a} ({n})" for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)) or "—")
    )
    lines.append("ציר זמן (שנה: כמות): " + ", ".join(f"{y}: {n}" for y, n in sorted(timeline.items())) or "—")
    lines.append(f"נוכחות ישראלית: {israel_count} רשומות; חברות: {', '.join(israel_companies) or 'אין'}")
    lines.append("")
    lines.append("רשומות ממוספרות:")
    for it in registry:
        lines.append(
            f"[{it['n']}] {it.get('title') or '—'} | {', '.join(it.get('assignees') or []) or '—'} | "
            f"{it.get('publication_date') or '—'} | CPC: {', '.join(it.get('cpc') or []) or '—'} | "
            f"ציון-ערך: {it.get('value_score') if it.get('value_score') is not None else '—'}"
        )
    return "\n".join(lines)


def _run_synthesis(
    topic: str, registry: list[dict[str, Any]], data_block: str, *, role: str, interactive: bool
) -> PatentSurveySynthesisOut | None:
    prompt = render(
        "patent_survey", topic=topic, n_patents=len(registry), data=wrap_data(data_block, "survey", "")
    )
    try:
        return chat_structured(
            role,
            PatentSurveySynthesisOut,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="report",
            interactive=interactive,
        )
    except ResourceUnavailable:
        log.info("patent_survey_synthesis_deferred", topic=topic)
    except LLMOutputError as exc:
        log.warning("patent_survey_synthesis_failed", topic=topic, error=str(exc)[:200])
    except Exception as exc:
        log.warning("patent_survey_synthesis_unexpected_error", topic=topic, error=str(exc)[:200])
    return None


_NO_LLM_TEXT_HE = "ניתוח שפה טבעית לא זמין כרגע (המודל המקומי אינו נגיש) -- הטבלאות הכמותיות למעלה תקפות."


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def _create_survey_row(topic: str) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO patent_surveys (topic, status) VALUES (%(topic)s, 'running') RETURNING id",
            {"topic": topic},
        )
        return cur.fetchone()["id"]


def _finish_survey_row(survey_id: int, *, status: str, report_id: int | None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE patent_surveys SET status = %(status)s, report_id = %(report_id)s WHERE id = %(id)s",
            {"status": status, "report_id": report_id, "id": survey_id},
        )


def _persist_report(
    topic: str, period_end: dt.date, docx_path: Path, md_path: Path, html_path: Path, patent_ids: list[int]
) -> int:
    query = """
        INSERT INTO reports (kind, period_end, path_docx, path_md, path_html, items_included, qa_passed, qa_report)
        VALUES ('patent_survey', %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, true, %(qa_report)s)
        RETURNING id
    """
    qa_report = {"note": "citation QA gate not applied to patent surveys", "topic": topic}
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            query,
            {
                "end": period_end,
                "docx": str(docx_path),
                "md": str(md_path),
                "html": str(html_path),
                "items": patent_ids or None,
                "qa_report": Json(qa_report),
            },
        )
        return cur.fetchone()["id"]


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_patent_survey(
    topic: str,
    *,
    deep_limit: int = DEFAULT_DEEP_LIMIT,
    role: str = "resident",
    interactive: bool = False,
    period_end: dt.date | None = None,
) -> PatentSurveyPaths:
    """A14 step 5: the full on-demand "סקר פטנטים" pipeline for one free-text ``topic`` (min 8
    characters, enforced by the API layer)."""
    period_end = period_end or dt.date.today()
    survey_id = _create_survey_row(topic)
    try:
        records = scan_mod.search_records(topic, limit=deep_limit)
        pub_to_id = scan_mod.upsert_records(records)
        patent_ids = list(pub_to_id.values())

        # Analyze/value only the freshest slice so a large gather doesn't blow the LLM budget --
        # every record still gets its deterministic clustering/timeline/white-space treatment
        # below regardless of whether it was analyzed.
        try:
            analyze_patents(min(len(patent_ids), MAX_ANALYZE_PER_SURVEY), role=role, interactive=interactive)
        except Exception as exc:
            log.warning("patent_survey_analyze_failed", topic=topic, error=str(exc)[:200])
        try:
            score_and_persist(limit=len(patent_ids) or 1)
        except Exception as exc:
            log.warning("patent_survey_valuation_failed", topic=topic, error=str(exc)[:200])

        rows = _fetch_patent_rows(patent_ids)
        registry = [
            {
                "n": i + 1,
                "id": row["id"],
                "title": row.get("title") or row["pub_number"],
                "assignees": row.get("assignees") or [],
                "publication_date": row.get("publication_date"),
                "cpc": row.get("cpc") or [],
                "value_score": row.get("value_score"),
                "url": row.get("url"),
                "source_name": row.get("source"),
                "pub_number": row["pub_number"],
            }
            for i, row in enumerate(rows)
        ]

        top_cpc = _cluster_by(rows, "cpc")
        top_assignees = _cluster_by(rows, "assignees")
        timeline = _timeline_by_year(rows)
        israel_count, israel_companies = _israel_position(rows)
        white_spaces = _white_spaces(
            rows,
            [c for c, _ in top_cpc.most_common(5)],
            [a for a, _ in top_assignees.most_common(5)],
        )

        data_block = _synthesis_data_block(
            topic, registry, top_cpc, top_assignees, timeline, israel_count, israel_companies
        )
        synthesis = _run_synthesis(topic, registry, data_block, role=role, interactive=interactive)

        draft = _SurveyDraft(
            exec_summary_he=(synthesis.overview_he if synthesis else _NO_LLM_TEXT_HE),
            sections=(
                [
                    _Section("שחקנים מובילים", synthesis.leaders_he),
                    _Section("מיצוב התעשייה הישראלית", synthesis.israel_position_he),
                    _Section("פערים והזדמנויות (White Space)", synthesis.white_spaces_he),
                ]
                if synthesis
                else [_Section("ניתוח", _NO_LLM_TEXT_HE)]
            ),
            outlook_he=synthesis.outlook_he if synthesis else "",
            open_points_he=(
                []
                if scan_mod.structured_sources_configured()
                else [
                    "מקורות פטנטים: מצב חיפוש בלבד -- הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי מלא."
                ]
            ),
        )

        tables = [
            {
                "title_he": "טבלת פטנטים (מדגם)",
                "headers": ["#", "מספר פרסום", "כותרת", "בעלים", "CPC", "ציון-ערך", "תאריך פרסום"],
                "rows": [
                    [
                        it["n"],
                        it["pub_number"],
                        it["title"],
                        ", ".join(it["assignees"]) or "—",
                        ", ".join(it["cpc"]) or "—",
                        it["value_score"] if it["value_score"] is not None else "—",
                        it["publication_date"].isoformat() if it["publication_date"] else "—",
                    ]
                    for it in registry[:50]
                ],
            },
            {
                "title_he": "בעלי פטנטים מובילים",
                "headers": ["בעלים", "מספר פטנטים"],
                "rows": [[a, n] for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)],
            },
            {
                "title_he": "ציר זמן שנתי",
                "headers": ["שנה", "מספר פטנטים"],
                "rows": [[y, n] for y, n in sorted(timeline.items())],
            },
            {
                "title_he": "קודי CPC מובילים",
                "headers": ["קוד CPC", "מספר פטנטים"],
                "rows": [[c, n] for c, n in top_cpc.most_common(TOP_N_CPC)],
            },
        ]
        if white_spaces:
            tables.append(
                {
                    "title_he": "פערים (White Space): צירופי CPC x בעלים שאינם מכוסים",
                    "headers": ["קוד CPC", "בעלים מוביל"],
                    "rows": [[c, a] for c, a in white_spaces],
                }
            )

        title_text = f"סקר פטנטים: {topic}"
        items_for_appendix = [
            {
                "n": it["n"],
                "title": it["title"],
                "source_name": ", ".join(it["assignees"]) or it["source_name"],
                "url": it["url"],
                "published_at": it["publication_date"],
            }
            for it in registry
        ]

        doc = build_docx(
            draft,
            items_for_appendix,
            [],
            period_end=period_end,
            title_text=title_text,
            tables=tables,
            include_toc=True,
        )
        docx_path, md_path, html_path = _report_paths(topic, period_end)
        save_docx(doc, docx_path)
        validate_docx(docx_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            render_markdown(
                draft, items_for_appendix, [], period_end=period_end, title_text=title_text, tables=tables
            ),
            encoding="utf-8",
        )
        html_path.write_text(
            render_html(
                draft,
                items_for_appendix,
                [],
                period_end=period_end,
                title_text=title_text,
                tables=tables,
                include_toc=True,
            ),
            encoding="utf-8",
        )

        report_id = _persist_report(topic, period_end, docx_path, md_path, html_path, patent_ids)
        _finish_survey_row(survey_id, status="done", report_id=report_id)
        log.info("patent_survey_done", topic=topic, patent_count=len(registry), report_id=report_id)
        return PatentSurveyPaths(
            docx=docx_path,
            md=md_path,
            html=html_path,
            report_id=report_id,
            survey_id=survey_id,
            topic=topic,
            patent_count=len(registry),
        )
    except Exception:
        _finish_survey_row(survey_id, status="failed", report_id=None)
        raise
