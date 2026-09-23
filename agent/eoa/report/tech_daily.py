"""Stage: report/tech_daily -- daily EO/IR supply-chain technology-watch report (user request
2026-09-17: "דוח על התפתחויות טכנולוגיות ברמה היומית ... שרשרת האספקה של מערכות אלקטרואופטיות
בכל הדומיינים, החל מה-FPA, דרך עדשות וערוצים אלקטרואופטיים, מראות, מראות סורקות, עיבוד תמונה,
בינה ובינה בקצה ... תפיסות מבצעיות").

Pipeline: collect candidate items (dimension=technology / domain in tech_dev|computer_vision|
directed_energy / tech_maturity set) + patents published in the window -> assign each to 0+ EO/IR
supply-chain layers (``eoa.report.tech_supply_chain``'s deterministic keyword pass first, then one
``chat_structured_batch`` LLM pass over the leftover unmatched items, ``eoa.llm.schemas.tech_daily.
TechLayerAssignment``) -> draft "מה חדש"/"משמעות" per layer that has items (LLM, the daily report's
own ``DailyReportDraft`` schema reused unchanged so every downstream helper -- ``qa_citations``,
``docx_builder``, ``style``, ``redundancy``, ``textnorm`` -- works with zero changes) + a
deterministic "אין חדש בתחום זה בתקופה" line for every layer with none -> ``qa_citations`` QA-gate
(same one-corrective-retry + deterministic-fallback contract as ``eoa.report.daily.build_daily``)
-> render docx/md/html (a compact per-layer status table plus a deterministic "מה השתנה מאתמול"
line, both additive, same ``tables=``/``extra_sections=`` hooks ``eoa.report.tech_watch``/
``eoa.report.deltas`` already use for the daily report) -> persist a ``reports`` row
(``kind='tech_daily'``).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, chat_structured_batch, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import DailyReportDraft, Sentence, StructuredSection
from eoa.llm.schemas.tech_daily import TechLayerAssignment
from eoa.report.artifacts import versioned_paths
from eoa.report.docx_builder import (
    build_docx,
    fmt_date,
    hebrew_date_str,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)
from eoa.report.qa_citations import QAResult, check, strip_so_what_phrases_from_draft
from eoa.report.redundancy import apply_redundancy_pass
from eoa.report.style import apply_style_guard, dedupe_exact_sentences_across_sections
from eoa.report.tech_supply_chain import get_layer, layer_defs
from eoa.report.textnorm import normalize_draft, trim_at_word_boundary

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")
TITLE_TEXT = "דוח התפתחויות טכנולוגיות יומי -- שרשרת האספקה האלקטרואופטית"

_CANDIDATE_DOMAINS = ("tech_dev", "computer_vision", "directed_energy")
_NO_NEWS_TEXT_HE = "אין חדש בתחום זה בתקופה."
_MATURITY_HE = {"lab": "מעבדה", "prototype": "אב-טיפוס", "qualified": "מוסמך", "fielded": "מבצעי"}
_MATURITY_RANK = {"lab": 0, "prototype": 1, "qualified": 2, "fielded": 3}
_MAX_LAYER_ITEMS_FOR_PROMPT = 8
_MAX_SOURCES_IN_TABLE_CELL = 5
_MAX_ITEM_CANDIDATES = 200
_MAX_PATENT_CANDIDATES = 100


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def lookback_range(
    period_end: dt.date | None, lookback_days: int
) -> tuple[dt.datetime, dt.datetime, dt.date]:
    """``(start_ts, end_ts, label)`` -- whole Jerusalem days, ``lookback_days`` back from
    ``period_end`` (default: today) through the last instant of ``period_end`` itself. Mirrors
    ``eoa.report.daily._period``'s own whole-day-boundary convention (``lookback_days=1`` is
    exactly one day, matching the nightly default; the first live build passes
    ``lookback_days=30``)."""
    label = period_end or _today_jerusalem()
    end_ts = dt.datetime.combine(label, dt.time(23, 59, 59, 999999), tzinfo=JERUSALEM)
    start_date = label - dt.timedelta(days=max(lookback_days, 1) - 1)
    start_ts = dt.datetime.combine(start_date, dt.time.min, tzinfo=JERUSALEM)
    return start_ts, end_ts, label


def _fetchall(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_candidate_items(
    start: dt.datetime, end: dt.datetime, *, limit: int = _MAX_ITEM_CANDIDATES
) -> list[dict[str, Any]]:
    """Every clean, non-duplicate item in ``[start, end)`` that is plausibly a technology-watch
    candidate: dimension ``technology``, domain in ``tech_dev``/``computer_vision``/
    ``directed_energy``, or a ``tech_maturity`` already set by an earlier pipeline stage --
    deliberately broad (the layer-assignment pass below is the real filter, same division of
    labour as ``eoa.report.tech_watch.collect_daily_tech_items``)."""
    return _fetchall(
        """
        SELECT i.id, i.url, i.title, i.domain, i.subdomain, i.dimensions, i.published_at, i.score,
               i.trl, i.tech_maturity, i.tech_actor_kind, i.so_what_he, i.summary_he,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.security_status = 'clean' AND i.dedup_of IS NULL
          AND ('technology' = ANY(i.dimensions) OR i.domain = ANY(%(domains)s)
               OR i.tech_maturity IS NOT NULL)
          AND COALESCE(i.published_at, i.created_at) >= %(start)s
          AND COALESCE(i.published_at, i.created_at) < %(end)s
        ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"domains": list(_CANDIDATE_DOMAINS), "start": start, "end": end, "limit": limit},
    )


def collect_candidate_patents(
    start: dt.datetime, end: dt.datetime, *, limit: int = _MAX_PATENT_CANDIDATES
) -> list[dict[str, Any]]:
    """Every patent published in ``[start, end)`` (Jerusalem calendar days) -- layer assignment
    (CPC-hint first, then title/abstract keyword match) happens separately, same division as
    items above."""
    start_date = start.astimezone(JERUSALEM).date()
    end_date = end.astimezone(JERUSALEM).date()
    return _fetchall(
        """
        SELECT id, pub_number, title, abstract, assignees, cpc, publication_date, url
        FROM patents
        WHERE publication_date >= %(start)s AND publication_date < %(end)s
        ORDER BY publication_date DESC
        LIMIT %(limit)s
        """,
        {"start": start_date, "end": end_date, "limit": limit},
    )


# --------------------------------------------------------------------------
# layer assignment
# --------------------------------------------------------------------------


def _item_text(it: dict[str, Any]) -> tuple[str, str]:
    """``(text_he, text_en)`` for the deterministic keyword pass -- same field mapping
    ``eoa.pipeline.analyze`` already uses for ``eoa.product_lines.tagging.tag_product_lines``
    (``text_he`` = summary + so-what, ``text_en`` = title, which in this corpus is frequently an
    English system/company name even inside an otherwise-Hebrew item)."""
    text_he = " ".join(filter(None, [it.get("summary_he"), it.get("so_what_he")]))
    return text_he, it.get("title") or ""


def _layers_catalog_he() -> str:
    return "\n".join(f"- {layer.key}: {layer.label_he} -- {layer.description_he}" for layer in layer_defs())


#: One (layer_key, relevance) hit -- ``relevance`` is ``"core"`` (the item reports a development
#: IN that layer's own technology) or ``"tangential"`` (the layer's component merely appears in an
#: unrelated platform/deal story -- coordinator fix 2026-09-17, "אין חדש = אין חדש": a drone story
#: that happens to carry a gimbal is not itself mirrors_scanning/gimbal_los_control news). Only
#: ``"core"`` hits feed a layer's "מה חדש" drafting -- see :func:`_layers_items_block`.
LayerHit = tuple[str, str]


def assign_item_layers(items: list[dict[str, Any]], *, role: str = "resident") -> dict[int, list[LayerHit]]:
    """Two passes, and the LLM has the last word on relevance.

    1. Deterministic keyword pass (``eoa.report.tech_supply_chain.assign_layers_keyword``) only
       PROPOSES candidate layers. It never decides ``core`` on its own: the first live 30-day build
       (2026-09-17, reports 229/230) showed why -- single-word Hebrew keywords such as "מראה"
       (also the verb "shows"), "כיוון", "ייצוב" matched a laser vehicle, a polymer paper and a
       VLM paper into "מראות סורקות"/"בקרת קווי ראייה", and because keyword hits were treated as
       ``core`` those layers were padded with platform news the draft itself called irrelevant.
    2. Every item -- with or without candidates -- goes through one ``chat_structured_batch``
       pass (``TechLayerAssignment``) that returns per-layer ``core``/``tangential``; the
       candidates are passed as a hint, not a verdict.

    Fail-open: if the LLM pass fails, keyword candidates are kept as ``tangential`` (so the layer
    still renders "אין חדש" with a "N פריטים משיקים" note, never padded prose)."""
    from eoa.report.tech_supply_chain import assign_layers_keyword

    candidates: dict[int, list[str]] = {}
    for it in items:
        text_he, text_en = _item_text(it)
        candidates[it["id"]] = assign_layers_keyword(text_he, text_en)

    assignments: dict[int, list[LayerHit]] = {}
    if not items:
        return assignments
    try:
        catalog = _layers_catalog_he()
        prompts = [
            (
                it["id"],
                render(
                    "tech_daily",
                    layers_catalog_he=catalog,
                    candidate_hint=(
                        "רמז ממילות מפתח (לא הכרעה -- קבע בעצמך core/tangential או השמט): "
                        + ", ".join(candidates[it["id"]])
                        if candidates[it["id"]]
                        else "רמז ממילות מפתח: אין"
                    ),
                    title=it.get("title") or "",
                    source=it.get("source_name") or it.get("url") or "",
                    published_at=fmt_date(it.get("published_at")),
                    data=wrap_data(
                        " ".join(filter(None, [it.get("summary_he"), it.get("so_what_he")]))[:2000],
                        it["id"],
                        it.get("url") or "",
                    ),
                ),
            )
            for it in items
        ]
        system = render("system_analyst", data_guard=DATA_GUARD_SYSTEM)
        results = chat_structured_batch(role, TechLayerAssignment, prompts, system=system, task="classify")
        for item_id, out in results.items():
            if out.layers:
                assignments[item_id] = [(lr.layer, lr.relevance) for lr in out.layers]
    except Exception as exc:
        log.warning("tech_daily_llm_layer_assignment_failed", error=str(exc)[:200])

    # Items the LLM did not classify (batch failure or a dropped item): keyword candidates survive
    # only as tangential -- never enough to draft a section.
    for item_id, layers in candidates.items():
        if item_id not in assignments and layers:
            assignments[item_id] = [(layer, "tangential") for layer in layers]
    return assignments


def assign_patent_layers(patents: list[dict[str, Any]]) -> dict[int, list[LayerHit]]:
    """Deterministic only (no LLM call for patents): a CPC-code prefix hit against a layer's
    ``patent_cpc_hint`` first, falling back to the same title/abstract keyword pass items use.
    Always ``"core"`` -- a patent that CPC/keyword-matches a layer is inherently about that
    technology, not a platform-carrier mention."""
    from eoa.report.tech_supply_chain import assign_layers_keyword

    assignments: dict[int, list[LayerHit]] = {}
    for p in patents:
        cpc_codes = set(p.get("cpc") or [])
        layers = [
            layer.key
            for layer in layer_defs()
            if layer.patent_cpc_hint
            and any(code.startswith(hint) for code in cpc_codes for hint in layer.patent_cpc_hint)
        ]
        if not layers:
            text_en = " ".join(filter(None, [p.get("title"), p.get("abstract")]))
            layers = assign_layers_keyword(None, text_en)
        if layers:
            assignments[p["id"]] = [(layer, "core") for layer in layers]
    return assignments


# --------------------------------------------------------------------------
# citation registry / per-layer content
# --------------------------------------------------------------------------


def build_layer_content(
    items: list[dict[str, Any]],
    patents: list[dict[str, Any]],
    item_layers: dict[int, list[LayerHit]],
    patent_layers: dict[int, list[LayerHit]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Returns ``(citation_items, layer_entries)``: a single flat, sequentially-numbered citation
    registry (items then patents -- patent rows reuse the same ``n`` numbering space but never the
    same ``id`` namespace as an item, since both tables are independent ``BIGSERIAL`` sequences
    that can collide; only ``entry["id"]`` on ``kind == "item"`` entries is later used to populate
    ``reports.items_included``) plus a ``{layer_key: [entry, ...]}`` grouping. Each grouped entry
    is a **shallow copy** of the canonical registry entry with its own ``"relevance"`` key added
    (``"core"``/``"tangential"``, per-layer -- the same item can be ``core`` for one layer and
    ``tangential`` for another, so the relevance tag cannot live on the single shared canonical
    entry)."""
    citation_items: list[dict[str, Any]] = []
    layer_entries: dict[str, list[dict[str, Any]]] = {layer.key: [] for layer in layer_defs()}
    next_n = 1

    for it in items:
        hits = item_layers.get(it["id"]) or []
        if not hits:
            continue
        entry = {
            "n": next_n,
            "id": it.get("id"),
            "kind": "item",
            "title": it.get("title"),
            "source_name": it.get("source_name"),
            "url": it.get("url"),
            "published_at": it.get("published_at"),
            "summary_he": it.get("summary_he"),
            "so_what_he": it.get("so_what_he"),
            "tech_maturity": it.get("tech_maturity"),
        }
        citation_items.append(entry)
        next_n += 1
        for layer_key, relevance in hits:
            if layer_key in layer_entries:
                layer_entries[layer_key].append({**entry, "relevance": relevance})

    for p in patents:
        hits = patent_layers.get(p["id"]) or []
        if not hits:
            continue
        entry = {
            "n": next_n,
            "id": None,
            "kind": "patent",
            "title": p.get("title"),
            "source_name": ", ".join((p.get("assignees") or [])[:2]) or p.get("pub_number"),
            "url": p.get("url"),
            "published_at": p.get("publication_date"),
            "summary_he": None,
            "so_what_he": None,
            "tech_maturity": None,
            "pub_number": p.get("pub_number"),
            "abstract": p.get("abstract"),
        }
        citation_items.append(entry)
        next_n += 1
        for layer_key, relevance in hits:
            if layer_key in layer_entries:
                layer_entries[layer_key].append({**entry, "relevance": relevance})

    return citation_items, layer_entries


def _core_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("relevance", "core") == "core"]


def _tangential_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("relevance") == "tangential"]


def _format_layer_entry(entry: dict[str, Any]) -> str:
    if entry.get("kind") == "patent":
        return (
            f"[{entry['n']}] פטנט {entry.get('pub_number') or '—'}: {entry.get('title') or '—'} | "
            f"בעלים: {entry.get('source_name') or '—'} | תאריך פרסום: {fmt_date(entry.get('published_at'))}\n"
            f"תקציר: {entry.get('abstract') or '—'}"
        )
    return (
        f"[{entry['n']}] כותרת: {entry.get('title') or '—'} | מקור: {entry.get('source_name') or '—'} | "
        f"תאריך: {fmt_date(entry.get('published_at'))}\n"
        f"תקציר: {entry.get('summary_he') or '—'}\n"
        f"מה זה אומר: {entry.get('so_what_he') or '—'}"
    )


def _layers_items_block(layer_entries: dict[str, list[dict[str, Any]]]) -> str:
    """Only ``core`` entries are ever handed to the drafting LLM -- a layer with only
    ``tangential`` entries is treated exactly like a layer with none at all (see
    :func:`draft_tech_daily`/:func:`no_news_extra_sections`), so the model can never draft "מה
    חדש" prose from a platform-carrier mention."""
    lines: list[str] = []
    for layer in layer_defs():
        entries = _core_entries(layer_entries.get(layer.key) or [])
        if not entries:
            continue
        lines.append(f"### {layer.key} -- {layer.label_he}")
        for entry in entries[:_MAX_LAYER_ITEMS_FOR_PROMPT]:
            lines.append(_format_layer_entry(entry))
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _normalize_layer_section_titles(draft: DailyReportDraft) -> DailyReportDraft:
    """Same rationale as ``eoa.report.daily._normalize_section_titles``: force every section's
    ``title_he`` to the authoritative layer label looked up by ``domain`` (here: the layer key)
    rather than trust the model's own copy of a Hebrew heading that may contain characters a small
    model's JSON output mis-escapes."""
    new_sections = []
    for section in draft.sections:
        layer = get_layer(section.domain)
        new_sections.append(section.model_copy(update={"title_he": layer.label_he}) if layer else section)
    return draft.model_copy(update={"sections": new_sections})


def _no_items_draft() -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary=[],
        sections=[],
        system_note_he=(
            "לא זוהו בתקופה זו פריטים או פטנטים המשויכים לאף שכבה בשרשרת האספקה האלקטרואופטית. "
            "אין חדש בתחום זה בתקופה."
        ),
        outlook=[],
        open_points_he=[],
    )


def draft_tech_daily(
    layer_entries: dict[str, list[dict[str, Any]]], *, role: str = "resident", interactive: bool = False
) -> DailyReportDraft:
    """Draft the report (``DailyReportDraft`` reused unchanged, see module docstring) via the
    resident model; a window with no layer with a ``core`` entry skips the LLM call entirely --
    a layer with only ``tangential`` entries is not "content" for this purpose (see
    :func:`_layers_items_block`)."""
    if not any(_core_entries(layer_entries.get(layer.key) or []) for layer in layer_defs()):
        return _no_items_draft()
    prompt = render(
        "report_tech_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        layers_items_block=_layers_items_block(layer_entries),
    )
    draft = chat_structured(
        role,
        DailyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.3},
    )
    return _normalize_layer_section_titles(draft)


def _corrective_retry(
    layer_entries: dict[str, list[dict[str, Any]]],
    draft: DailyReportDraft,
    qa: QAResult,
    *,
    role: str,
    interactive: bool,
) -> DailyReportDraft:
    prompt = render(
        "report_tech_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        layers_items_block=_layers_items_block(layer_entries),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        'ברשימת הפריטים. שים לב: אסור לכתוב "[n]" בטקסט עצמו -- מספרי ההפניה שייכים אך ורק לשדה '
        "cites של כל משפט:\n" + errors_text
    )
    draft = chat_structured(
        role,
        DailyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": correction},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.2},
    )
    return _normalize_layer_section_titles(draft)


def _deterministic_fallback_draft(layer_entries: dict[str, list[dict[str, Any]]]) -> DailyReportDraft:
    """Two QA failures (initial draft + one corrective retry) replace the narrative with a
    deterministic, cited substitute built straight from the data -- same rationale as
    ``eoa.report.daily._deterministic_fallback_draft`` (round 3, 2026-09-06): a report that failed
    its own citation gate is still rendered with real, verifiably-cited content rather than an
    apology, while ``qa.passed`` stays ``False`` so the failure is never hidden from the analyst.
    Only ``core`` entries feed a section here too -- same "אין חדש = אין חדש" strictness as the
    LLM drafting path."""
    sections = []
    for layer in layer_defs():
        entries = _core_entries(layer_entries.get(layer.key) or [])
        if not entries:
            continue
        sentences = [
            Sentence(text_he=trim_at_word_boundary(entry.get("title") or "פריט ללא כותרת", 120), cites=[entry["n"]])
            for entry in entries[:4]
        ]
        sections.append(StructuredSection(title_he=layer.label_he, domain=layer.key, sentences=sentences))
    return DailyReportDraft(
        exec_summary=[],
        sections=sections,
        system_note_he=(
            "הטיוטה האוטומטית נכשלה פעמיים בבדיקת האזכורים האוטומטית -- הסעיפים שלהלן נבנו ישירות "
            "מכותרות הפריטים/הפטנטים ללא ניסוח אנליטי."
        ),
        outlook=[],
        open_points_he=[],
    )


# --------------------------------------------------------------------------
# deterministic tables / extra sections
# --------------------------------------------------------------------------


def _max_maturity_label(entries: list[dict[str, Any]]) -> str:
    ranked = [
        (_MATURITY_RANK.get(e.get("tech_maturity") or "", -1), e.get("tech_maturity"))
        for e in entries
        if e.get("tech_maturity")
    ]
    if not ranked:
        return "—"
    best = max(ranked, key=lambda pair: pair[0])[1]
    return _MATURITY_HE.get(best, best or "—")


def layer_status_table(layer_entries: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """The required (layer | פריטים | חדש/אין חדש | בשלות מרבית | מקורות) table -- 5 columns,
    within the spec's <=6-column cap. Every configured layer gets a row regardless of whether it
    has news, so the table alone already answers "what has news today" at a glance; ``None`` only
    when ``config/tech_supply_chain.yaml`` has no layers configured at all (fail-safe, same
    convention as every other additive table in this codebase)."""
    rows: list[list[Any]] = []
    for layer in layer_defs():
        entries = layer_entries.get(layer.key) or []
        core = _core_entries(entries)
        # Status/maturity reflect `core` only (a layer with only tangential entries is "אין חדש");
        # the sources cell falls back to tangential citations so the analyst can still see what
        # was excluded and why, rather than showing a bare "—" when something was actually found.
        status = "חדש" if core else "אין חדש"
        sources_pool = core or entries
        sources = "".join(f"[{e['n']}]" for e in sources_pool[:_MAX_SOURCES_IN_TABLE_CELL]) or "—"
        rows.append([layer.label_he, str(len(entries)), status, _max_maturity_label(core), sources])
    if not rows:
        return None
    return {
        "title_he": "סטטוס שכבות שרשרת האספקה",
        "headers": ["שכבה", "פריטים", "חדש/אין חדש", "בשלות מרבית", "מקורות"],
        "rows": rows,
        "no_dedupe": True,
    }


def no_news_extra_sections(layer_entries: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """One ``extra_sections`` entry per layer with zero ``core`` entries -- the literal, required
    "אין חדש בתחום זה בתקופה" line (spec: "no padding"), deterministic (never LLM-authored, so it
    can never fail the citation QA gate). A layer with only ``tangential`` entries still gets this
    line (coordinator fix 2026-09-17: a platform merely carrying the component is not "news" for
    the layer), plus one short parenthetical noting how many tangential mentions were excluded and
    why, with their own citation markers so the analyst can still check them."""
    sections: list[dict[str, Any]] = []
    for layer in layer_defs():
        entries = layer_entries.get(layer.key) or []
        if _core_entries(entries):
            continue
        tangential = _tangential_entries(entries)
        body = _NO_NEWS_TEXT_HE
        if tangential:
            markers = "".join(f"[{e['n']}]" for e in tangential[:_MAX_SOURCES_IN_TABLE_CELL])
            body += f" ({len(tangential)} פריטים משיקים בלבד -- פלטפורמות/עסקאות שמזכירות את הרכיב) {markers}"
        sections.append({"title_he": layer.label_he, "body_he": body, "position": "after_summary"})
    return sections


def _previous_tech_daily_layers_with_news(period_end: dt.date) -> list[str] | None:
    rows = _fetchall(
        "SELECT qa_report FROM reports WHERE kind = 'tech_daily' AND period_end < %(end)s "
        "ORDER BY created_at DESC LIMIT 1",
        {"end": period_end},
    )
    if not rows:
        return None
    return (rows[0].get("qa_report") or {}).get("layers_with_news") or []


def changed_since_yesterday_extra_section(
    layer_entries: dict[str, list[dict[str, Any]]], period_end: dt.date
) -> dict[str, Any] | None:
    """"מה השתנה מאתמול" -- ``None`` (section omitted entirely) when this is the first tech_daily
    report ever built; otherwise always returned, even when nothing changed (an honest "no change"
    line rather than silently omitting the section, same convention as
    ``eoa.report.deltas.delta_extra_section``)."""
    previous = _previous_tech_daily_layers_with_news(period_end)
    if previous is None:
        return None
    previous_set = set(previous)
    current_set = {layer.key for layer in layer_defs() if _core_entries(layer_entries.get(layer.key) or [])}
    became_active = [get_layer(k).label_he for k in sorted(current_set - previous_set) if get_layer(k)]
    became_quiet = [get_layer(k).label_he for k in sorted(previous_set - current_set) if get_layer(k)]
    if not became_active and not became_quiet:
        body = "אין שינוי בשכבות עם חדש לעומת הדוח הקודם."
    else:
        parts = []
        if became_active:
            parts.append("שכבות עם חדש היום שלא היו אתמול: " + ", ".join(became_active))
        if became_quiet:
            parts.append("שכבות שהיו פעילות אתמול ואין בהן חדש היום: " + ", ".join(became_quiet))
        body = " | ".join(parts)
    return {"title_he": "מה השתנה מאתמול", "body_he": body, "position": "before_summary"}


# --------------------------------------------------------------------------
# persistence / paths
# --------------------------------------------------------------------------


def _report_path(period_end: dt.date, ext: str) -> Path:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    return out_dir / f"tech_daily_{period_end.isoformat()}.{ext}"


def _persist_report(
    period_start: dt.date,
    period_end: dt.date,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    citation_items: list[dict[str, Any]],
    qa: QAResult,
    *,
    layers_with_news: list[str],
) -> int:
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
        # Read back by eoa.api.services.morning() (layer-with-news count for the "טכנולוגיה היום"
        # morning card) and by changed_since_yesterday_extra_section (next build's own delta) --
        # same "stash extra metadata in qa_report" convention product_dossier/patent_survey use.
        "layers_with_news": layers_with_news,
    }
    item_ids = [e["id"] for e in citation_items if e.get("kind") == "item" and e.get("id") is not None]
    sql = """
        INSERT INTO reports (kind, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report)
        VALUES ('tech_daily', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s,
                %(qa_passed)s, %(qa_report)s)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "start": period_start,
                "end": period_end,
                "docx": str(docx_path),
                "md": str(md_path),
                "html": str(html_path),
                "items": item_ids,
                "qa_passed": qa.passed,
                "qa_report": Json(qa_report),
            },
        )
        report_id: int = cur.fetchone()["id"]
    log.info("tech_daily_report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


def _recent_tech_daily_report(period_end: dt.date, *, within_hours: int = 6) -> ReportPaths | None:
    """F4 idempotency guard, same contract as ``eoa.report.daily._recent_daily_report``: a
    ``tech_daily`` report for ``period_end`` already built within the last ``within_hours`` hours
    is reused as-is unless the caller passes ``force=True``."""
    sql = """
        SELECT id, path_docx, path_md, path_html, qa_passed, qa_report
        FROM reports
        WHERE kind = 'tech_daily' AND period_end = %(period_end)s
          AND created_at > now() - make_interval(hours => %(hours)s)
        ORDER BY created_at DESC
        LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"period_end": period_end, "hours": within_hours})
        row = cur.fetchone()
    if row is None:
        return None
    qa_report = row.get("qa_report") or {}
    qa = QAResult(
        passed=bool(row.get("qa_passed")),
        errors=qa_report.get("errors", []),
        uncited_sentences=qa_report.get("uncited_sentences", []),
        bad_refs=qa_report.get("bad_refs", []),
        duplicate_sentences=qa_report.get("duplicate_sentences", []),
    )
    log.info("tech_daily_report_reused", report_id=row["id"], period_end=str(period_end))
    return ReportPaths(
        docx=Path(row["path_docx"]),
        md=Path(row["path_md"]),
        html=Path(row["path_html"]),
        report_id=row["id"],
        qa=qa,
    )


def _drop_non_core_sections(
    draft: DailyReportDraft, layer_entries: dict[str, list[dict[str, Any]]]
) -> DailyReportDraft:
    """Deterministic backstop (coordinator fix 2026-09-17): even though :func:`_layers_items_block`
    already excludes any layer with zero ``core`` entries from the drafting prompt, this drops any
    section the model wrote anyway for such a layer (e.g. if it hallucinated a ``domain`` outside
    what it was given) -- "a layer section drafted from only tangential items is replaced by the
    'אין חדש' line even if the LLM wrote prose". Safe to run unconditionally: a section this keeps
    is unaffected, and one it drops was never going to have a citation-registry issue either way
    (dropping content never invalidates another section's still-valid ``[n]`` markers)."""
    kept = [s for s in draft.sections if _core_entries(layer_entries.get(s.domain) or [])]
    if len(kept) == len(draft.sections):
        return draft
    kept_ids = {id(s) for s in kept}
    dropped = [s.domain for s in draft.sections if id(s) not in kept_ids]
    log.warning("tech_daily_dropped_non_core_section", domains=dropped)
    return draft.model_copy(update={"sections": kept})


def _period_line_he(lookback_days: int, start_ts: dt.datetime, end_ts: dt.datetime) -> str:
    """Coordinator fix 2026-09-17: a period line under the report's date -- the plain "24 השעות
    האחרונות" wording for a normal nightly (``lookback_days == 1``) run, or the explicit date
    range + day count for a wider opening/backfill survey."""
    if lookback_days <= 1:
        return "תקופת הסקירה: 24 השעות האחרונות"
    start_date = start_ts.astimezone(JERUSALEM).date()
    end_date = end_ts.astimezone(JERUSALEM).date()
    return (
        f"תקופת הסקירה: {start_date:%d.%m.%Y}–{end_date:%d.%m.%Y} "
        f"(סקירה פותחת -- {lookback_days} יום)"
    )


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_tech_daily(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    force: bool = False,
    lookback_days: int = 1,
    role: str = "resident",
    interactive: bool = False,
) -> ReportPaths:
    """Collect -> assign layers -> draft -> QA-gate -> render docx/md/html -> persist.

    ``period_start`` is accepted for signature symmetry with ``eoa.report.daily.build_daily`` but
    the actual window is always ``lookback_days`` back from ``period_end`` (default: today) --
    see :func:`lookback_range`. F4: unless ``force=True``, a ``tech_daily`` report for the same
    ``period_end`` built less than 6 hours ago is returned as-is.
    """
    del period_start  # signature symmetry only, see docstring
    start_ts, end_ts, label = lookback_range(period_end, lookback_days)

    if not force:
        existing = _recent_tech_daily_report(label)
        if existing is not None:
            return existing

    items = collect_candidate_items(start_ts, end_ts)
    patents = collect_candidate_patents(start_ts, end_ts)
    item_layers = assign_item_layers(items, role=role)
    patent_layers = assign_patent_layers(patents)
    citation_items, layer_entries = build_layer_content(items, patents, item_layers, patent_layers)

    draft = draft_tech_daily(layer_entries, role=role, interactive=interactive)
    qa = check(draft, citation_items)

    if not qa.passed and citation_items:
        log.warning("tech_daily_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(layer_entries, draft, qa, role=role, interactive=interactive)
        qa = check(draft, citation_items)

    if not qa.passed and citation_items:
        log.error("tech_daily_qa_failed_twice_using_deterministic_fallback", errors=qa.errors[:10])
        original_errors = qa
        draft = _deterministic_fallback_draft(layer_entries)
        fallback_qa = check(draft, citation_items)
        if not fallback_qa.passed:
            log.error("tech_daily_fallback_draft_failed_citation_check", errors=fallback_qa.errors[:10])
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    draft = _drop_non_core_sections(draft, layer_entries)
    draft = normalize_draft(draft)
    draft, style_report = apply_style_guard(draft, report_kind="tech_daily")
    style_report.log_all(report_kind="tech_daily")
    draft, _so_what_removed = strip_so_what_phrases_from_draft(draft, report_kind="tech_daily")
    draft, _n_dupes_dropped = dedupe_exact_sentences_across_sections(draft)
    if _n_dupes_dropped:
        log.info("tech_daily_exact_duplicate_sentences_dropped", n=_n_dupes_dropped)
    draft, _redundancy_result = apply_redundancy_pass(draft, report_kind="tech_daily")
    _redundancy_result.log_all(report_kind="tech_daily")

    extra_sections = no_news_extra_sections(layer_entries)
    changed_section = changed_since_yesterday_extra_section(layer_entries, label)
    if changed_section:
        extra_sections.insert(0, changed_section)
    # Coordinator fix 2026-09-17 (2 of 3): a period line under the date, always first -- ahead of
    # "מה השתנה מאתמול" -- via the same before_summary extra_sections hook (docx_builder.py itself
    # is not touched: it is mid-edit by another agent right now).
    extra_sections.insert(
        0, {"title_he": "תקופת הסקירה", "body_he": _period_line_he(lookback_days, start_ts, end_ts), "position": "before_summary"}
    )

    tables = []
    status_table = layer_status_table(layer_entries)
    if status_table:
        tables.append(status_table)

    # Coordinator fix 2026-09-17 (3 of 3): keep the daily title, append " -- סקירת פתיחה" only for
    # a wider opening/backfill survey (lookback_days > 1) -- via the existing title_text hook.
    title_text = TITLE_TEXT + (" -- סקירת פתיחה" if lookback_days > 1 else "")

    docx_path = _report_path(label, "docx")
    md_path = _report_path(label, "md")
    html_path = _report_path(label, "html")
    docx_path, md_path, html_path = versioned_paths(docx_path, md_path, html_path)

    doc = build_docx(
        draft,
        citation_items,
        [],
        period_end=label,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections or None,
        tables=tables or None,
    )
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft,
        citation_items,
        [],
        period_end=label,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections or None,
        tables=tables or None,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft,
        citation_items,
        [],
        period_end=label,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections or None,
        tables=tables or None,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    period_start_date = start_ts.astimezone(JERUSALEM).date()
    layers_with_news = [
        layer.key for layer in layer_defs() if _core_entries(layer_entries.get(layer.key) or [])
    ]
    report_id = _persist_report(
        period_start_date, label, docx_path, md_path, html_path, citation_items, qa,
        layers_with_news=layers_with_news,
    )

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
