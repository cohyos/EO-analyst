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
import re
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
from eoa.llm.schemas.product_dossier import DealRow, ProductDossierOut
from eoa.report.docx_builder import (
    _EVENT_KIND_LABELS_HE,
    build_docx,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

TITLE_TEMPLATE_HE = "סקירת שוק עמוקה למוצר — {name}"

PLACEHOLDER_HE = "לא נמצא במקורות"

_FOUND_MIN_SIGNALS = 5
_PARTIAL_MIN_SIGNALS = 1


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _write_job_progress(job_id: int | None, progress: list[dict[str, Any]]) -> None:
    """PD-fix-3 (2026-09-08, item 2): best-effort, persists ``progress`` (one entry per research
    topic -- ``eoa.dossier.plan``'s ``ProgressEntry``) into ``jobs.payload->'progress'`` WHILE the
    job is still ``running`` -- so ``GET /api/dossiers/{key}``'s ``pending_job.progress``
    (``eoa.api.services``) can render a live per-topic banner instead of the ~2h run showing no
    progress at all.

    ``jobs.payload.progress`` (not ``result``) is the ONE documented location for this -- the
    original PD-fix (item 5) wrote into ``result``, which ``eoa.orchestrator.jobs``'s own
    ``finish_job`` REPLACES wholesale (never merges) the moment the job reaches a terminal state,
    and which a live DB read of job 194 confirmed: ``result`` held only the final
    ``{"product_dossier": {...}}`` payload, no trace of the mid-run progress list. ``payload`` is
    merged (jsonb ``||``, additive over the existing ``product_key``/``vendor``/... keys set at
    enqueue time), never replaced by ``finish_job``, so it is the one column a poller can read
    consistently for the life of the job. Only ever touches a row still ``running`` (a progress
    write racing a job that already finished must not resurrect/clobber a finished row) and never
    raises -- a progress-write failure must not break the dossier build itself."""
    if job_id is None:
        return
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET payload = COALESCE(payload, '{}'::jsonb) || %(patch)s::jsonb "
                "WHERE id = %(id)s AND state = 'running'",
                {"id": job_id, "patch": Json({"progress": progress}, dumps=_json_dumps)},
            )
    except Exception as exc:
        log.warning("dossier_progress_write_failed", job_id=job_id, error=str(exc)[:200])


def _cite_cell(cites: list[int]) -> str:
    return ", ".join(f"[{n}]" for n in cites) if cites else "—"


def _cell(value: Any) -> Any:
    if value in (None, ""):
        return PLACEHOLDER_HE
    return value


#: PD-fix-3 (2026-09-08, item 4): the deals table's own customer placeholder -- deliberately
#: distinct from the generic `PLACEHOLDER_HE` ("לא נמצא במקורות", which reads as "the research
#: never covered this at all"): a deal row itself IS grounded/cited, it is specifically the
#: customer's identity that is unknown, which "לא צוין" (not specified) says honestly without
#: implying the whole row is unsourced.
CUSTOMER_PLACEHOLDER_HE = "לא צוין"


def _deal_customer_cell(r: DealRow) -> str:
    """Never the raw `"—"`/`None` value -- `eoa.dossier.extract._normalize_customer` already turns
    a model-written placeholder string into a real `None`, so the only remaining case to handle
    here is that `None` itself (or, defensively, an already-blank string on old persisted data)."""
    customer = (r.customer or "").strip()
    return customer or CUSTOMER_PLACEHOLDER_HE


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
    """PD-fix (2026-09-08, item 4): only ``exec_summary`` (תקציר מנהלים) still goes through
    ``docx_builder``'s ``draft.sections`` slot -- every other section moved into the ordered
    ``tables`` list (:func:`_ordered_report_entries`) so the WHOLE document (prose sections + real
    data tables, interleaved) follows the plan's exact section order instead of "prose sections
    block, then every table" (``docx_builder.build_docx`` renders ``draft.sections`` as one group
    before ``tables`` as a second group -- the only way to interleave them without touching that
    shared, not-owned-by-this-round module is to keep ``draft.sections`` empty and put everything,
    prose included, into ``tables`` -- a ``tables`` entry with no ``headers``/``rows``, just
    ``body_he``, already renders as plain prose in every one of ``docx_builder``'s three renderers,
    see ``_render_table_entry_docx``/``_render_table_entry_md``/``_render_table_entry_html``)."""
    return _RenderableDossierDraft(exec_summary=_sentence_or_placeholder(dossier.summary_he), sections=[])


# --------------------------------------------------------------------------
# PD-fix item 4: ordered section/table entries -- section order EXACTLY per docs/
# PLAN_PRODUCT_DOSSIER.md section 6 (as re-affirmed by the fix brief): תקציר (exec_summary, above),
# זיהוי, מפרט, גרסאות, ביצועים, בשלות, עסקאות, מחירים, שותפויות, מתחרים, פטנטים, מכרזים ותחזיות,
# רגולציה, פערים, משמעות עסקית, מה השתנה, מקורות (the sources appendix, always rendered last by
# ``docx_builder`` itself -- not part of this list). Every one of the 15 entries below is ALWAYS
# present -- an empty section renders its own single placeholder line/body, it is never omitted
# (a caller checking "did this section render" must see it either way).
# --------------------------------------------------------------------------


def _cite_suffix(cites: list[int]) -> str:
    return "".join(f"[{n}]" for n in cites)


def _prose_entry(title_he: str, text: str, cites: list[int] | None = None) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"title_he": title_he, "body_he": PLACEHOLDER_HE}
    suffix = _cite_suffix(cites or [])
    body = f"{text} {suffix}".rstrip() if suffix else text
    return {"title_he": title_he, "body_he": body}


def _sentence_list_entry(title_he: str, sentences: list[Any]) -> dict[str, Any]:
    if not sentences:
        return {"title_he": title_he, "body_he": PLACEHOLDER_HE}
    parts = []
    for s in sentences:
        suffix = _cite_suffix(s.cites)
        text = (s.text_he or "").rstrip()
        parts.append(f"{text} {suffix}".rstrip() if suffix else text)
    return {"title_he": title_he, "body_he": " ".join(parts)}


def _identity_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    identity = dossier.identity
    line = (
        f"יצרן: {_cell(identity.vendor)} | משפחת מוצרים: {_cell(identity.product_family)} | "
        f"קטגוריה: {_cell(identity.category_he)} | תאריך הכרזה: {_cell(identity.first_announced)} | "
        f"סטטוס: {identity.status_he}"
    )
    return _prose_entry("זיהוי המוצר", line, identity.cites)


def _maturity_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    maturity = dossier.maturity
    bits = [
        f"TRL: {_cell(maturity.trl)}",
        f"מפעילים: {', '.join(maturity.operational_users) or PLACEHOLDER_HE}",
        f"פלטפורמות: {', '.join(maturity.platforms_integrated) or PLACEHOLDER_HE}",
        f"פריסה ראשונה: {_cell(maturity.first_fielding)}",
        _cell(maturity.assessment_he),
    ]
    return _prose_entry("בשלות ופריסה", " | ".join(bits), maturity.cites)


def _regulatory_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    regulatory = dossier.regulatory_export
    if not (regulatory.export_regime_he or regulatory.restrictions_he):
        return {"title_he": "רגולציה וייצוא", "body_he": PLACEHOLDER_HE}
    text = f"{_cell(regulatory.export_regime_he)} — {_cell(regulatory.restrictions_he)}"
    return _prose_entry("רגולציה וייצוא", text, regulatory.cites)


def _what_changed_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    title = "מה השתנה"
    if dossier.what_changed_he is None:
        return {"title_he": title, "body_he": "אין סקירה קודמת להשוואה — זוהי הסקירה הראשונה של מוצר זה."}
    if not dossier.what_changed_he:
        return {"title_he": title, "body_he": "לא זוהו שינויים לעומת הסקירה הקודמת."}
    return _sentence_list_entry(title, dossier.what_changed_he)


def _ordered_report_entries(dossier: ProductDossierOut) -> list[dict[str, Any]]:
    return [
        _identity_entry(dossier),
        _specifications_table(dossier),
        _variants_table(dossier),
        _performance_table(dossier),
        _maturity_entry(dossier),
        _deals_table(dossier),
        _pricing_table(dossier),
        _partnerships_table(dossier),
        _competitors_table(dossier),
        _patents_table(dossier),
        _tenders_table(dossier),
        _regulatory_entry(dossier),
        _sentence_list_entry("פערים ואי-ודאויות", dossier.risks_and_gaps_he),
        _sentence_list_entry("משמעות עסקית", dossier.bd_implications_he),
        _what_changed_entry(dossier),
    ]


def _specifications_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.specifications:
        return {"title_he": "מפרט", "body_he": PLACEHOLDER_HE}
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


def _variants_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.variants_and_versions:
        return {"title_he": "גרסאות", "body_he": PLACEHOLDER_HE}
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


def _performance_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.performance:
        return {"title_he": "ביצועים (מוצהר מול נמדד)", "body_he": PLACEHOLDER_HE}
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


#: PD-fix-2 item 3: a `date`/`published_at` value can carry a time-of-day and timezone offset
#: (e.g. ``"2026-09-02 09:04:00+03:00"``, backfilled from a cited item's own `published_at` --
#: `eoa.dossier.extract._ground_deal_row`, not owned by this round) -- the deals table shows a
#: date only, never a time/timezone. Rendering-only: the stored value itself is untouched.
_DATE_ONLY_RE = re.compile(r"^(\d{4}(?:-\d{2}(?:-\d{2})?)?)")


def _date_only(value: str | None) -> str | None:
    if not value:
        return value
    m = _DATE_ONLY_RE.match(value.strip())
    return m.group(1) if m else value


#: PD-fix-2 item 3: Hebrew label for a deal's `kind` -- reuses `docx_builder._EVENT_KIND_LABELS_HE`
#: for the one kind the two enums share (`contract_award`), extended with the deal-only kinds that
#: enum doesn't cover; an unrecognised kind falls back to the raw value (same convention as every
#: other `_EVENT_KIND_LABELS_HE(...).get(kind, kind)` call site in this codebase).
_DEAL_KIND_LABELS_HE_EXTRA = {
    "FMS": "מכירת ציוד ביטחוני זר (FMS)",
    "framework": "הסכם מסגרת",
    "option": "אופציה בחוזה",
    "export_license": "רישיון ייצוא",
}


def _deal_kind_label(kind: str) -> str:
    return _EVENT_KIND_LABELS_HE.get(kind) or _DEAL_KIND_LABELS_HE_EXTRA.get(kind) or kind


def _deal_date_cell(r: DealRow) -> str:
    """PD-fix item 3: a date backfilled from the cited source's own publish date (never the actual
    deal-closing date) says so, rather than reading as indistinguishable from one that was. PD-fix-2
    item 3: rendered as a date only -- never a time/timezone."""
    text = _cell(_date_only(r.date))
    if r.date and r.date_kind == "published":
        return f"{text} (תאריך פרסום)"
    return text


def _deal_amount_cell(r: DealRow) -> str:
    """The published figure stays primary (never replaced); the deterministically parsed numeric
    value, when one was found, is appended in parentheses -- e.g. "כ-80 מיליון דולר (80,000,000
    USD)" -- never the other way around (no derived/computed value is ever presented as if it were
    itself the published figure)."""
    if not r.amount:
        return PLACEHOLDER_HE
    if r.amount_value is not None:
        currency = f" {r.currency}" if r.currency else ""
        return f"{r.amount} ({r.amount_value:,.0f}{currency})"
    return r.amount


def _deals_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.deals:
        return {"title_he": "עסקאות", "body_he": PLACEHOLDER_HE}
    headers = ["תאריך", "לקוח", "מדינה", "סוג", "היקף", "מקור"]
    rows = [
        [
            _deal_date_cell(r),
            _deal_customer_cell(r),
            # PD-fix item 3: a region-only source ("Asia-Pacific country") never fabricates a
            # specific country here -- `country` is empty and `region_he` carries the region text.
            _cell(r.country or r.region_he),
            _deal_kind_label(r.kind),
            _deal_amount_cell(r),
            _cite_cell(r.cites),
        ]
        for r in dossier.deals
    ]
    return {"title_he": "עסקאות", "headers": headers, "rows": rows}


def _pricing_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.pricing:
        return {"title_he": "מחירים", "body_he": PLACEHOLDER_HE}
    headers = ["סכום", "בסיס", "תאריך", "סוג מקור", "מקור"]
    rows = [
        [_cell(r.figure), _cell(r.basis_he), _cell(r.date), r.source_kind, _cite_cell(r.cites)]
        for r in dossier.pricing
    ]
    return {"title_he": "מחירים", "headers": headers, "rows": rows}


def _partnerships_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.partnerships:
        return {"title_he": "שותפויות", "body_he": PLACEHOLDER_HE}
    headers = ["שותף", "תפקיד", "מאז", "מקור"]
    rows = [[r.partner, r.role_he, _cell(r.since), _cite_cell(r.cites)] for r in dossier.partnerships]
    return {"title_he": "שותפויות", "headers": headers, "rows": rows}


def _competitors_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.competitors:
        return {"title_he": "מתחרים", "body_he": PLACEHOLDER_HE}
    headers = ["מוצר מתחרה", "יצרן", "השוואה", "מקור"]
    rows = [
        [r.product, _cell(r.vendor), _cell(r.comparison_he)[:200], _cite_cell(r.cites)]
        for r in dossier.competitors
    ]
    return {"title_he": "מתחרים", "headers": headers, "rows": rows}


def _patents_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.patents:
        return {"title_he": "פטנטים", "body_he": PLACEHOLDER_HE}
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


def _tenders_table(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.tenders_and_forecasts:
        return {"title_he": "מכרזים ותחזיות", "body_he": PLACEHOLDER_HE}
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
    plan_result = run_plan(
        corpus,
        job_id=job_id,
        budget_multiplier=budget_multiplier,
        on_progress=lambda progress: _write_job_progress(job_id, progress),
    )

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
    # PD-fix item 4: the full section order (identity through "מה השתנה") -- every entry always
    # present, prose sections included -- lives in this one ordered list now; see
    # `_build_render_draft`'s own docstring for why prose moved out of `draft.sections`.
    tables = _ordered_report_entries(dossier)

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
