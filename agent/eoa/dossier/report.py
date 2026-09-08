"""Stage 5 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.5): orchestrates corpus -> plan -> extract ->
diff, renders md/html/docx through ``eoa.report.docx_builder`` (reused, not modified -- this module
duck-types its own small rendering dataclasses to that builder's existing structured-draft path,
exactly the convention ``eoa.patents.survey`` already established for the same reason), and
persists a ``product_dossiers`` row plus a normal ``reports`` row (``kind='product_dossier'``, the
``product_key`` stored in the existing ``territory`` column -- same reuse ``eoa.report.product_line``
already documents for that column).

:func:`build_product_dossier` is the single entry point ``eoa.orchestrator.jobs.run_product_dossier``
and the API services layer (``eoa.api.services``) both call.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.dossier.corpus import CorpusResult, build_corpus
from eoa.dossier.diff import compute_diff
from eoa.dossier.extract import DroppedField, build_dossier
from eoa.dossier.plan import PlanResult, run_plan
from eoa.errors import LLMOutputError
from eoa.llm.schemas.product_dossier import ProductDossierOut
from eoa.report.docx_builder import build_docx, render_html, render_markdown, save_docx, validate_docx

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

TITLE_TEMPLATE_HE = "סקירת שוק עמוקה למוצר — {name}"

PLACEHOLDER_HE = "לא נמצא במקורות"

_FOUND_MIN_SIGNALS = 5
_PARTIAL_MIN_SIGNALS = 1


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _cite_cell(cites: list[int]) -> str:
    return ", ".join(f"[{n}]" for n in cites) if cites else "—"


def _cell(value: Any) -> Any:
    if value in (None, ""):
        return PLACEHOLDER_HE
    return value


# --------------------------------------------------------------------------
# render-side duck-typed dataclasses -- see eoa.patents.survey's own identical-purpose note for
# why this is a small local shape rather than a docx_builder.py change.
# --------------------------------------------------------------------------


@dataclass
class _CiteSentence:
    text_he: str
    cites: list[int] = field(default_factory=list)


@dataclass
class _RenderSection:
    title_he: str
    sentences: list[_CiteSentence] = field(default_factory=list)


@dataclass
class _RenderableDossierDraft:
    exec_summary: list[_CiteSentence] = field(default_factory=list)
    sections: list[_RenderSection] = field(default_factory=list)
    outlook: list[_CiteSentence] = field(default_factory=list)
    open_points_he: list[str] = field(default_factory=list)
    system_note_he: str = ""
    analyst_note_he: Any = None


def _sentence_or_placeholder(sentences: list[Any]) -> list[_CiteSentence]:
    if not sentences:
        return [_CiteSentence(text_he=PLACEHOLDER_HE, cites=[])]
    return [_CiteSentence(text_he=s.text_he, cites=s.cites) for s in sentences]


def _build_render_draft(dossier: ProductDossierOut) -> _RenderableDossierDraft:
    exec_summary = _sentence_or_placeholder(dossier.summary_he)
    sections: list[_RenderSection] = []

    identity = dossier.identity
    identity_line = (
        f"יצרן: {_cell(identity.vendor)} | משפחת מוצרים: {_cell(identity.product_family)} | "
        f"קטגוריה: {_cell(identity.category_he)} | תאריך הכרזה: {_cell(identity.first_announced)} | "
        f"סטטוס: {identity.status_he}"
    )
    sections.append(
        _RenderSection("זיהוי המוצר", [_CiteSentence(text_he=identity_line, cites=identity.cites)])
    )

    maturity = dossier.maturity
    maturity_bits = [
        f"TRL: {_cell(maturity.trl)}",
        f"מפעילים: {', '.join(maturity.operational_users) or PLACEHOLDER_HE}",
        f"פלטפורמות: {', '.join(maturity.platforms_integrated) or PLACEHOLDER_HE}",
        f"פריסה ראשונה: {_cell(maturity.first_fielding)}",
        _cell(maturity.assessment_he),
    ]
    sections.append(
        _RenderSection(
            "בשלות ופריסה", [_CiteSentence(text_he=" | ".join(maturity_bits), cites=maturity.cites)]
        )
    )

    regulatory = dossier.regulatory_export
    if regulatory.export_regime_he or regulatory.restrictions_he:
        reg_text = f"{_cell(regulatory.export_regime_he)} — {_cell(regulatory.restrictions_he)}"
        sections.append(
            _RenderSection("רגולציה וייצוא", [_CiteSentence(text_he=reg_text, cites=regulatory.cites)])
        )

    sections.append(_RenderSection("פערים ואי-ודאויות", _sentence_or_placeholder(dossier.risks_and_gaps_he)))
    sections.append(_RenderSection("משמעות עסקית", _sentence_or_placeholder(dossier.bd_implications_he)))
    if dossier.what_changed_he is not None:
        sections.append(_RenderSection("מה השתנה", _sentence_or_placeholder(dossier.what_changed_he)))

    return _RenderableDossierDraft(exec_summary=exec_summary, sections=sections)


def _specifications_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.specifications:
        return None
    headers = ["פרמטר", "ערך", "יחידה/וריאנט", "סוג מקור", "מקור"]
    rows = [
        [
            r.parameter_he,
            _cell(r.value),
            " / ".join(x for x in (r.unit, r.variant) if x) or "—",
            r.source_kind,
            _cite_cell(r.cites),
        ]
        for r in dossier.specifications
    ]
    return {"title_he": "מפרט", "headers": headers, "rows": rows}


def _variants_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.variants_and_versions:
        return None
    headers = ["גרסה/דגם", "שנה", "שינויים", "פלטפורמות", "מקור"]
    rows = [
        [
            r.name,
            _cell(r.year),
            _cell(r.changes_he)[:200],
            ", ".join(r.platforms) or "—",
            _cite_cell(r.cites),
        ]
        for r in dossier.variants_and_versions
    ]
    return {"title_he": "גרסאות", "headers": headers, "rows": rows}


def _performance_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.performance:
        return None
    headers = ["מדד", "ערך מוצהר", "ערך נמדד/מבצעי", "תנאים", "מקור"]
    rows = [
        [
            r.metric_he,
            _cell(r.claimed_value),
            _cell(r.tested_or_operational_value),
            _cell(r.conditions_he)[:150],
            _cite_cell(r.cites),
        ]
        for r in dossier.performance
    ]
    return {"title_he": "ביצועים (מוצהר מול נמדד)", "headers": headers, "rows": rows}


def _deals_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.deals:
        return None
    headers = ["תאריך", "לקוח", "מדינה", "סוג", "היקף", "מקור"]
    rows = [
        [
            _cell(r.date),
            _cell(r.customer),
            _cell(r.country),
            r.kind,
            _cell(r.amount),
            _cite_cell(r.cites),
        ]
        for r in dossier.deals
    ]
    return {"title_he": "עסקאות", "headers": headers, "rows": rows}


def _pricing_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.pricing:
        return None
    headers = ["סכום", "בסיס", "תאריך", "סוג מקור", "מקור"]
    rows = [
        [_cell(r.figure), _cell(r.basis_he), _cell(r.date), r.source_kind, _cite_cell(r.cites)]
        for r in dossier.pricing
    ]
    return {"title_he": "מחירים", "headers": headers, "rows": rows}


def _partnerships_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.partnerships:
        return None
    headers = ["שותף", "תפקיד", "מאז", "מקור"]
    rows = [[r.partner, r.role_he, _cell(r.since), _cite_cell(r.cites)] for r in dossier.partnerships]
    return {"title_he": "שותפויות", "headers": headers, "rows": rows}


def _competitors_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.competitors:
        return None
    headers = ["מוצר מתחרה", "יצרן", "השוואה", "מקור"]
    rows = [
        [r.product, _cell(r.vendor), _cell(r.comparison_he)[:200], _cite_cell(r.cites)]
        for r in dossier.competitors
    ]
    return {"title_he": "מתחרים", "headers": headers, "rows": rows}


def _patents_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.patents:
        return None
    headers = ["מספר פרסום", "כותרת", "בעלים", "רלוונטיות", "מקור"]
    rows = [
        [
            _cell(r.pub_number),
            _cell(r.title),
            _cell(r.assignee),
            _cell(r.relevance_he)[:150],
            _cite_cell(r.cites),
        ]
        for r in dossier.patents
    ]
    return {"title_he": "פטנטים", "headers": headers, "rows": rows}


def _tenders_table(dossier: ProductDossierOut) -> dict[str, Any] | None:
    if not dossier.tenders_and_forecasts:
        return None
    headers = ["מכרז/תחזית", "סטטוס", "רלוונטיות", "מקור"]
    rows = [
        [_cell(r.title), _cell(r.status), _cell(r.relevance_he)[:150], _cite_cell(r.cites)]
        for r in dossier.tenders_and_forecasts
    ]
    return {"title_he": "מכרזים ותחזיות", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# outcome / confidence
# --------------------------------------------------------------------------


def _signal_count(dossier: ProductDossierOut) -> int:
    return sum(
        len(x)
        for x in (
            dossier.summary_he,
            dossier.specifications,
            dossier.variants_and_versions,
            dossier.performance,
            dossier.deals,
            dossier.pricing,
            dossier.partnerships,
            dossier.competitors,
        )
    )


def _compute_outcome_confidence(dossier: ProductDossierOut, plan_result: PlanResult) -> tuple[str, float]:
    signals = _signal_count(dossier)
    confidences = [f.investigation.result.confidence for f in plan_result.findings if f.investigation.result]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    if signals >= _FOUND_MIN_SIGNALS:
        outcome = "found"
    elif signals >= _PARTIAL_MIN_SIGNALS:
        outcome = "partial"
    else:
        outcome = "not_found"
        avg_conf = min(avg_conf, 0.3)
    return outcome, round(min(max(avg_conf, 0.0), 1.0), 2)


def _empty_dossier(product_name: str, vendor: str | None) -> ProductDossierOut:
    from eoa.llm.schemas.product_dossier import IdentityBlock

    return ProductDossierOut(identity=IdentityBlock(product_name=product_name, vendor=vendor or ""))


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _report_paths(product_key: str, today: dt.date) -> tuple[Path, Path, Path]:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    base = out_dir / f"dossier_{product_key}_{today.isoformat()}"
    return base.with_suffix(".docx"), base.with_suffix(".md"), base.with_suffix(".html")


def _persist(
    corpus: CorpusResult,
    dossier: ProductDossierOut,
    *,
    outcome: str,
    confidence: float,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    job_id: int | None,
    product_line: str | None,
    dropped: list[DroppedField],
) -> tuple[int, int]:
    today = _today_jerusalem()
    item_ids = [it["id"] for it in corpus.items if it.get("id")]
    qa_report = {
        "passed": True,
        "errors": [],
        "dropped_fields": [{"field": d.field, "reason": d.reason, "value": d.value} for d in dropped],
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reports (kind, territory, period_start, period_end, path_docx, path_md, path_html,
                                  items_included, qa_passed, qa_report)
            VALUES ('product_dossier', %(key)s, %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s,
                    %(items)s, %(qa_passed)s, %(qa_report)s)
            RETURNING id
            """,
            {
                "key": corpus.product_key,
                "start": today,
                "end": today,
                "docx": str(docx_path),
                "md": str(md_path),
                "html": str(html_path),
                "items": item_ids,
                "qa_passed": True,
                "qa_report": Json(qa_report, dumps=_json_dumps),
            },
        )
        report_id: int = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO product_dossiers (product_key, product_name, vendor, aliases, product_line,
                                           job_id, report_id, data, sources, outcome, confidence)
            VALUES (%(key)s, %(name)s, %(vendor)s, %(aliases)s, %(line)s, %(job_id)s, %(report_id)s,
                    %(data)s, %(sources)s, %(outcome)s, %(confidence)s)
            RETURNING id
            """,
            {
                "key": corpus.product_key,
                "name": corpus.product_name,
                "vendor": corpus.vendor,
                "aliases": corpus.aliases,
                "line": product_line,
                "job_id": job_id,
                "report_id": report_id,
                "data": Json(dossier.model_dump(), dumps=_json_dumps),
                "sources": Json(corpus.registry, dumps=_json_dumps),
                "outcome": outcome,
                "confidence": confidence,
            },
        )
        dossier_id: int = cur.fetchone()["id"]
    log.info(
        "product_dossier_persisted",
        product_key=corpus.product_key,
        report_id=report_id,
        dossier_id=dossier_id,
        outcome=outcome,
    )
    return report_id, dossier_id


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


@dataclass
class DossierPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    dossier_id: int
    product_key: str
    outcome: str
    confidence: float


def build_product_dossier(
    product_name: str,
    vendor: str | None = None,
    aliases: list[str] | None = None,
    *,
    product_line: str | None = None,
    budget_multiplier: float | None = None,
    job_id: int | None = None,
    role: str = "resident",
    interactive: bool = False,
) -> DossierPaths:
    """Collect (corpus) -> research (plan) -> extract+ground -> diff -> render -> persist, for one
    product. A failed extraction (LLM unavailable/invalid output after ``chat_structured``'s own
    retries) never blocks the run -- the dossier renders honestly as an empty, ``not_found`` record
    (every table shows the "לא נמצא במקורות" placeholder) rather than failing the job outright,
    matching every other report module's own two-failure-fallback discipline."""
    corpus = build_corpus(product_name, vendor, aliases, product_line=product_line)
    plan_result = run_plan(corpus, job_id=job_id, budget_multiplier=budget_multiplier)

    dropped: list[DroppedField] = []
    try:
        grounding = build_dossier(corpus, plan_result, role=role, interactive=interactive)
        dossier = grounding.dossier
        dropped = grounding.dropped
    except LLMOutputError as exc:
        log.error("product_dossier_extract_failed", product_key=corpus.product_key, error=str(exc)[:200])
        dossier = _empty_dossier(product_name, vendor)

    previous_data = corpus.previous.get("data") if corpus.previous else None
    what_changed = compute_diff(previous_data, dossier)
    dossier = dossier.model_copy(update={"what_changed_he": what_changed})

    outcome, confidence = _compute_outcome_confidence(dossier, plan_result)

    draft = _build_render_draft(dossier)
    tables = [
        tbl
        for tbl in (
            _specifications_table(dossier),
            _variants_table(dossier),
            _performance_table(dossier),
            _deals_table(dossier),
            _pricing_table(dossier),
            _partnerships_table(dossier),
            _competitors_table(dossier),
            _patents_table(dossier),
            _tenders_table(dossier),
        )
        if tbl
    ]

    today = _today_jerusalem()
    docx_path, md_path, html_path = _report_paths(corpus.product_key, today)
    title_text = TITLE_TEMPLATE_HE.format(name=corpus.product_name)

    doc = build_docx(draft, corpus.registry, [], period_end=today, title_text=title_text, tables=tables)
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft, corpus.registry, [], period_end=today, title_text=title_text, tables=tables
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft, corpus.registry, [], period_end=today, title_text=title_text, tables=tables
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id, dossier_id = _persist(
        corpus,
        dossier,
        outcome=outcome,
        confidence=confidence,
        docx_path=docx_path,
        md_path=md_path,
        html_path=html_path,
        job_id=job_id,
        product_line=product_line,
        dropped=dropped,
    )

    return DossierPaths(
        docx=docx_path,
        md=md_path,
        html=html_path,
        report_id=report_id,
        dossier_id=dossier_id,
        product_key=corpus.product_key,
        outcome=outcome,
        confidence=confidence,
    )


__all__ = ["DossierPaths", "build_product_dossier"]
