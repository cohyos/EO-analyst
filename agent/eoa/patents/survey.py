"""Stage: on-demand patent landscape survey ("סקר פטנטים", A14).

``build_patent_survey(topic)``: gather up to ``deep_limit`` patent records for a free-text topic
(reusing ``eoa.patents.scan.search_records`` -- the same EPO OPS/PatentsView/Google-Patents-search
sources as the routine scan, just deeper and scoped to one on-demand query), upsert them into
``patents`` (dedup by ``pub_number``), analyze/value the freshest of them so the report tables have
something to show, compute deterministic aggregates (CPC/assignee clustering, a yearly timeline,
top assignees, CPC-x-assignee white space, an Israeli-industry position count), build a
business-depth "profile מקצה" for each of the top real (watchlist-or-not) company assignees from
this project's own database (canonical entity, watchlist products/programs, recent 180-day
items/events -- mirrors ``eoa.report.bd_territory``'s own entity-activity queries), synthesize a
structured, per-sentence-cited LLM narrative (``eoa.llm.schemas.patents.PatentSurveyDraft``) over a
numbered citation registry that puts every gathered patent first and every referenced database
record immediately after (one flat numbering sequence -- see that schema's module docstring),
render docx/md/html via ``eoa.report.docx_builder``, and persist both a ``reports`` row
(``kind='patent_survey'``) and a ``patent_surveys`` row linking to it.

Goal (2026-09-06, user feedback: "יש שיבושי עברית/אנגלית וקיטועים; חסר ניתוח עומק עסקי"):
citation discipline is now enforced *by construction* at the pydantic-schema level (every sentence
either carries a non-empty ``cites`` list into the registry above, or is explicitly labelled
"ידע כללי (לא מאומת במאגר)") rather than by a post-hoc regex QA pass -- there is therefore no
separate ``qa_citations.check`` gate for this report type (the existing ``qa_report`` note below is
unchanged and still accurate: it explains why). The narrative is rendered through
``eoa.report.docx_builder``'s existing goal-1 *structured* draft path (the same one
``eoa.report.daily.DailyReportDraft`` uses): this module's own small ``_Renderable*`` dataclasses
below are duck-typed to that path (no ``exec_summary_he`` attribute; ``sections`` carry a
``sentences`` list rather than free ``prose_he``), so every citation marker, Hebrew/Latin bidi run
split, and internal-hyperlink-to-appendix jump is emitted by code already in ``docx_builder.py`` --
that file needed no changes for this rework. The one rendering primitive it did not already have
(Unicode bidi isolation for a Latin/English value sitting in a *plain-Markdown* table cell, which
``docx_builder``'s own run-level bidi splitting only reaches inside the docx/html paths) lives in
the new, additive ``eoa.patents.render`` module instead.

A synthesis failure (LLM unavailable/invalid output) never blocks the survey -- the report still
renders with a short, honest "ניתוח שפה טבעית לא זמין" placeholder in the narrative sections; every
deterministic table (clustering/timeline/top assignees/white space/patents appendix) always renders
regardless. Likewise, when the gathered sample carries no real (non-empty, non-generic) company
assignee at all -- an inherent limitation of the keyless Google-Patents-search fallback used
without EPO_OPS_KEY/PATENTSVIEW_API_KEY, see ``eoa.patents.scan``'s own docstring -- this module
never asks the LLM to fabricate an assignee profile from nothing; it skips synthesis and surfaces
an explicit, honest open point instead (docs/CONVENTIONS.md rule 5: never invent).
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
from eoa.llm.schemas.patents import AssigneeProfile, PatentBizAction, PatentCiteSentence, PatentSurveyDraft
from eoa.patents import scan as scan_mod
from eoa.patents.analyze import analyze_patents
from eoa.patents.render import ltr_isolate, ltr_isolate_if_latin, ltr_join
from eoa.patents.valuation import score_and_persist
from eoa.pipeline.entity_normalize import resolve_canonical
from eoa.report.docx_builder import (
    build_docx,
    fmt_date,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)

log = structlog.get_logger(__name__)

DEFAULT_DEEP_LIMIT = 150
MAX_ANALYZE_PER_SURVEY = 20
TOP_N_CPC = 8
TOP_N_ASSIGNEES = 10
TOP_N_PROFILES = 3
PROFILE_LOOKBACK_DAYS = 180

# BD-1-style override (see eoa.report.bd_territory._BD_NUM_PREDICT): the structured survey draft
# (exec summary + landscape + tech clusters + 1-5 nested assignee profiles + white spaces +
# israel position + 3-6 business actions + outlook) produces a substantially longer completion
# than the shared config default (`ollama.num_predict.report` = 6000, config/config.yaml) was
# tuned for -- raised here, per-call, so a busy topic's completion is never cut off mid-JSON
# (goal 2026-09-06: "never let a truncated JSON pass").
_SURVEY_NUM_PREDICT = 24000

_EVENT_KIND_HE = {
    "contract_award": "חוזה",
    "m_and_a": "מיזוג/רכישה",
    "partnership": "שותפות",
    "investment": "השקעה",
    "launch": "השקה",
    "test": "ניסוי",
    "deployment": "פריסה",
    "regulation": "רגולציה",
    "other": "אחר",
}

_PUB_NUMBER_COUNTRY_RE = re.compile(r"^([A-Z]{2})")


# --------------------------------------------------------------------------
# rendering-side draft shape -- duck-typed to eoa.report.docx_builder's goal-1 structured-draft
# path (see this module's docstring above); no changes to docx_builder.py itself.
# --------------------------------------------------------------------------


@dataclass
class _CiteSentence:
    """One rendered sentence: ``text_he`` plus the registry numbers docx_builder's
    ``_render_sentence`` turns into deterministic ``[n]`` markers. Unlike the pydantic
    ``PatentCiteSentence`` the LLM must return, this local dataclass has no non-empty-``cites``
    constraint -- it is also used for a handful of code-authored, uncited connector/fallback lines
    (e.g. "פעילות עדכנית:", the no-LLM placeholder) that are never presented as sourced claims."""

    text_he: str
    cites: list[int] = field(default_factory=list)


@dataclass
class _RenderSection:
    title_he: str
    sentences: list[_CiteSentence] = field(default_factory=list)


@dataclass
class _RenderableSurveyDraft:
    """Deliberately shaped like ``eoa.llm.schemas.analysis.DailyReportDraft`` (``exec_summary`` /
    ``sections[].sentences`` / ``outlook`` -- no ``exec_summary_he``) so
    ``eoa.report.docx_builder.build_docx``/``render_markdown``/``render_html`` render every field
    below through their existing structured (per-sentence, deterministic ``[n]``) path."""

    exec_summary: list[_CiteSentence] = field(default_factory=list)
    sections: list[_RenderSection] = field(default_factory=list)
    outlook: list[_CiteSentence] = field(default_factory=list)
    open_points_he: list[str] = field(default_factory=list)
    system_note_he: str = ""
    analyst_note_he: Any = None


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


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _pub_country(pub_number: str | None) -> str | None:
    m = _PUB_NUMBER_COUNTRY_RE.match(pub_number or "")
    return m.group(1) if m else None


def _territory_filter(rows: list[dict[str, Any]], territory: str | None) -> list[dict[str, Any]]:
    """Best-effort territory filter (2026-09-06 request) by the publication number's leading WIPO
    ST.16 country/office code (e.g. "US9197834B2" -> "US") -- there is no structured jurisdiction
    field reliably filled by the keyless Google-Patents-search fallback (``eoa.patents.scan``), so
    the number itself is the only signal available without EPO_OPS_KEY/PATENTSVIEW_API_KEY.
    ``territory=None`` (the default) returns ``rows`` unchanged."""
    if not territory:
        return rows
    code = territory.strip().upper()
    return [r for r in rows if _pub_country(r.get("pub_number")) == code]


def _domain_label(domain: str | None) -> str:
    """Small local copy of ``eoa.report.bd_territory``'s own helper (docs/CONVENTIONS.md rule 6:
    this module doesn't depend on other report modules' internals) -- turns a watchlist ``focus``
    tag (which is exactly a ``taxonomy.yaml`` domain id, e.g. "c_uas") into its Hebrew+English
    label."""
    domains = settings().taxonomy.get("domains", {})
    entry = domains.get(domain or "", {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else (domain or "")


def _our_company_block_he() -> str:
    company = settings().bd_report.our_company
    aliases = f" (גם: {', '.join(company.aliases)})" if company.aliases else ""
    return f"{company.name}{aliases}"


def _perspective_he() -> str:
    return settings().bd_report.perspective_he


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


def _is_real_company_assignee(name: str) -> bool:
    """A watchlist-recognised (or otherwise unresolved, i.e. not on any curated list at all)
    assignee name is treated as a real company; a name that resolves to a *non*-company canonical
    record (a country, or a curated government/org entry like "NATO"/"Europe") is not -- see
    ``eoa.patents.scan._assignee_candidates_in_text``'s own docstring for why such a name could
    ever end up in ``patents.assignees`` in the first place (a pre-fix stale row)."""
    if not name or name == "—":
        return False
    canonical = resolve_canonical(name)
    return not (canonical and canonical.get("kind") != "company")


def _cluster_assignees(rows: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for row in rows:
        for value in row.get("assignees") or []:
            if _is_real_company_assignee(value):
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
# business-depth assignee profiles: canonical entity + watchlist products/programs + recent
# (PROFILE_LOOKBACK_DAYS-day) database activity -- mirrors eoa.report.bd_territory's own
# entity-activity queries (docs/CONVENTIONS.md rule 6: a small local copy, not an import of that
# module's internals).
# --------------------------------------------------------------------------


def _select_profile_assignees(top_assignees: Counter[str], limit: int = TOP_N_PROFILES) -> list[str]:
    return [name for name, _n in top_assignees.most_common(limit)]


def _assignee_names_for_matching(assignee_name: str, canonical: dict[str, Any] | None) -> set[str]:
    names = {assignee_name}
    if canonical:
        names.add(canonical.get("name") or "")
        names.update(canonical.get("aliases") or [])
    return {n for n in names if n}


def _assignee_market_items(names: set[str], since: dt.date, limit: int = 8) -> list[dict[str, Any]]:
    """Items mentioning one of ``names`` (``items.entities_mentioned``) published since ``since`` --
    same "load a bounded recent batch, filter in Python" shape as
    ``eoa.report.bd_territory.collect_market_items`` (Q3-8's ``entities_mentioned`` convention, no
    SQL array-overlap operator needed)."""
    if not names:
        return []
    rows = _fetchall(
        """
        SELECT i.id, i.url, i.title, i.published_at, i.summary_he, i.so_what_he,
               i.entities_mentioned, COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.security_status = 'clean' AND i.dedup_of IS NULL
          AND COALESCE(i.published_at, i.fetched_at, i.created_at)::date >= %(since)s
        ORDER BY i.published_at DESC NULLS LAST
        LIMIT 2000
        """,
        {"since": since},
    )
    out = [r for r in rows if set(r.get("entities_mentioned") or []) & names]
    return out[:limit]


def _assignee_events(names: set[str], since: dt.date, limit: int = 6) -> list[dict[str, Any]]:
    """Contract/partnership/etc. events (``events.kind``) naming one of ``names`` as customer or a
    party, dated since ``since``."""
    if not names:
        return []
    rows = _fetchall(
        """
        SELECT e.id, e.item_id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties,
               e.customer, e.program, i.url AS item_url, i.title AS item_title, i.published_at,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE COALESCE(e.date, i.published_at::date, i.fetched_at::date) >= %(since)s
        ORDER BY e.date DESC NULLS LAST
        LIMIT 3000
        """,
        {"since": since},
    )
    out = [r for r in rows if (r.get("customer") in names) or (set(r.get("parties") or []) & names)]
    return out[:limit]


def _extend_registry_with_db_records(
    citation_items: list[dict[str, Any]], entries: list[dict[str, Any]], *, item_id_key: str
) -> None:
    """Append any database item/event referenced by ``entries`` that isn't already numbered in
    ``citation_items`` (patents are numbered first; this continues that same flat sequence for the
    database half of the registry -- see ``eoa.llm.schemas.patents``'s module docstring), mutating
    each entry in place with its resolved ``n``. Mirrors
    ``eoa.report.bd_territory._extend_registry_with_source_items``'s numbering-extension
    convention. An event whose parent item was already numbered (as a market item, or by an
    earlier assignee's own events) reuses that same registry row rather than getting a duplicate
    one -- ``item_id_key`` is ``"id"`` for a market item (its own id) and ``"item_id"`` for an
    event (its parent item's id); both converge on the same underlying-item identity."""
    by_item_id = {it["item_id"]: it for it in citation_items if it.get("item_id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for entry in entries:
        item_id = entry.get(item_id_key)
        found = by_item_id.get(item_id) if item_id is not None else None
        if found is None and item_id is not None:
            found = {
                "id": -(2_000_000 + next_n),
                "item_id": item_id,
                "n": next_n,
                "title": entry.get("title") or entry.get("item_title"),
                "source_name": entry.get("source_name"),
                "url": entry.get("url") or entry.get("item_url"),
                "published_at": entry.get("published_at"),
                "kind": "db_item",
            }
            citation_items.append(found)
            by_item_id[item_id] = found
            next_n += 1
        entry["n"] = found.get("n") if found else None


def _assignee_profile_input_block(
    name: str,
    canonical: dict[str, Any] | None,
    cpc_cluster: list[tuple[str, int]],
    patent_ns: list[int],
    market_items: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> str:
    lines = [f"### מקצה: {name}"]
    if canonical:
        aliases = ", ".join(canonical.get("aliases") or []) or "—"
        focus = ", ".join(_domain_label(f) for f in canonical.get("focus") or []) or "—"
        lines.append(
            f"ישות קנונית ברשימת המעקב: {canonical.get('name')} | מדינה: {canonical.get('country') or '—'} "
            f"| מוצרים/תוכניות ידועים (aliases ברשימת המעקב): {aliases} | תחומי מיקוד: {focus}"
        )
    else:
        lines.append("לא נמצאה ישות קנונית ברשימת המעקב (watchlist) עבור מקצה זה.")
    lines.append("מספרי הפטנטים של מקצה זה ברשימה: " + (", ".join(f"[{n}]" for n in patent_ns) or "—"))
    lines.append(
        "אשכולות CPC של המקצה: " + (", ".join(f"{c} ({n})" for c, n in cpc_cluster) or "—")
    )
    if market_items or events:
        lines.append(f"פעילות עסקית מהמאגר ({PROFILE_LOOKBACK_DAYS} יום אחרונים):")
        for it in market_items:
            lines.append(f"- [{it['n']}] {it.get('title') or '—'} ({fmt_date(it.get('published_at'))})")
        for ev in events:
            kind_he = _EVENT_KIND_HE.get(ev.get("kind"), ev.get("kind") or "—")
            lines.append(
                f"- [{ev['n']}] {kind_he}: {ev.get('title') or ev.get('program') or '—'} "
                f"({fmt_date(ev.get('date'))})"
            )
    else:
        lines.append(f"לא נמצאה פעילות עסקית במאגר בחלון {PROFILE_LOOKBACK_DAYS} הימים האחרונים עבור מקצה זה.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# LLM synthesis
# --------------------------------------------------------------------------


def _registry_lines(registry: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for it in registry:
        if it.get("kind") == "db_item":
            lines.append(
                f"[{it['n']}] (מאגר) {it.get('title') or '—'} | מקור: {it.get('source_name') or '—'} "
                f"| תאריך: {fmt_date(it.get('published_at'))}"
            )
        else:
            lines.append(
                f"[{it['n']}] (פטנט) {it.get('title') or it.get('pub_number')} | בעלים: "
                f"{', '.join(it.get('assignees') or []) or '—'} | תאריך: "
                f"{fmt_date(it.get('publication_date'))} | CPC: {', '.join(it.get('cpc') or []) or '—'} "
                f"| ציון-ערך: {it.get('value_score') if it.get('value_score') is not None else '—'}"
            )
    return lines


def _synthesis_data_block(
    topic: str,
    registry: list[dict[str, Any]],
    top_cpc: Counter[str],
    top_assignees: Counter[str],
    timeline: Counter[int],
    israel_count: int,
    israel_companies: list[str],
    profile_blocks: list[str],
) -> str:
    patent_count = sum(1 for it in registry if it.get("kind") != "db_item")
    lines = [
        f"נושא: {topic}",
        f'סה"כ פטנטים: {patent_count}',
        f'סה"כ רשומות ברשימה הממוספרת (פטנטים ואז רשומות מאגר): {len(registry)}',
        "",
    ]
    lines.append(
        "קודי CPC מובילים: " + (", ".join(f"{c} ({n})" for c, n in top_cpc.most_common(TOP_N_CPC)) or "—")
    )
    lines.append(
        "בעלי פטנטים מובילים: "
        + (", ".join(f"{a} ({n})" for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)) or "—")
    )
    lines.append(
        "ציר זמן (שנה: כמות): " + (", ".join(f"{y}: {n}" for y, n in sorted(timeline.items())) or "—")
    )
    lines.append(f"נוכחות ישראלית: {israel_count} רשומות; חברות: {', '.join(israel_companies) or 'אין'}")
    lines.append("")
    lines.append("רשומות ממוספרות (פטנטים תחילה, ואז רשומות מאגר -- רצף מספור אחד):")
    lines += _registry_lines(registry)
    if profile_blocks:
        lines.append("")
        lines.append("נתוני מקצים מובילים לבניית פרופיל עסקי (ישות קנונית + אשכולות CPC + פעילות מאגר):")
        for block in profile_blocks:
            lines.append(block)
            lines.append("")
    return "\n".join(lines)


def _run_synthesis(
    topic: str, registry: list[dict[str, Any]], data_block: str, *, role: str, interactive: bool
) -> PatentSurveyDraft | None:
    prompt = render(
        "patent_survey",
        topic=topic,
        n_patents=sum(1 for it in registry if it.get("kind") != "db_item"),
        n_registry=len(registry),
        data=wrap_data(data_block, "survey", ""),
        our_company_block=_our_company_block_he(),
        perspective_he=_perspective_he(),
    )
    try:
        return chat_structured(
            role,
            PatentSurveyDraft,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="report",
            interactive=interactive,
            options={"temperature": 0.3, "num_predict": _SURVEY_NUM_PREDICT},
        )
    except ResourceUnavailable:
        log.info("patent_survey_synthesis_deferred", topic=topic)
    except LLMOutputError as exc:
        log.warning("patent_survey_synthesis_failed", topic=topic, error=str(exc)[:200])
    except Exception as exc:
        log.warning("patent_survey_synthesis_unexpected_error", topic=topic, error=str(exc)[:200])
    return None


_NO_LLM_TEXT_HE = "ניתוח שפה טבעית לא זמין כרגע (המודל המקומי אינו נגיש) -- הטבלאות הכמותיות למעלה תקפות."
_NO_ASSIGNEE_DATA_HE = (
    "לא זוהה בעל-פטנטים (assignee) אחד לפחות הניתן לזיהוי במדגם שנאסף -- מגבלה של מקור החיפוש "
    "חסר-המפתחות (ראו eoa.patents.scan); הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי בעלים "
    "מלא. לא ניתן היה לבנות פרופילי מקצה מבוססי-מאגר עבור נושא זה, כדי לא להמציא נתונים."
)


# --------------------------------------------------------------------------
# draft assembly: PatentSurveyDraft (LLM, cited) -> _RenderableSurveyDraft (docx_builder-shaped)
# --------------------------------------------------------------------------


def _to_cite_sentences(sentences: list[PatentCiteSentence]) -> list[_CiteSentence]:
    return [_CiteSentence(text_he=s.text_he, cites=list(s.cites)) for s in sentences]


# Goal (2026-09-06, observed live on the "Anduril Lattice ..." survey, report_id=31): the model
# fabricated a specific CPC code ("Y10S 7/00, Y10S 7/160") inside an otherwise validly-cited
# tech_product_chain sentence, even though this assignee's own CPC-cluster data block was empty
# ("—") -- a factual invention PatentCiteSentence's non-empty-`cites` validation cannot catch (it
# validates that a claim is attributed to a real registry entry, not that every specific detail
# inside the sentence is grounded in the data actually supplied). The prompt now forbids this
# explicitly (patent_survey.md rule 6); this is the deterministic, code-level backstop -- applied
# only to a profile whose real cpc_cluster was empty, so a profile that *does* have real CPC data
# is never touched.
_CPC_ASIDE_RE = re.compile(r"\s*\([^()]*CPC[^()]*\)")
_CPC_TOKEN_RE = re.compile(r"\b[A-Y]\d{2}[A-Z]\s?\d+(?:/\d+)?\b")


def _strip_unfounded_cpc_mentions(text: str) -> str:
    text = _CPC_ASIDE_RE.sub("", text)
    text = _CPC_TOKEN_RE.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _clean_profile_sentences(
    sentences: list[PatentCiteSentence], *, has_cpc_data: bool
) -> list[_CiteSentence]:
    out = _to_cite_sentences(sentences)
    if not has_cpc_data:
        for s in out:
            s.text_he = _strip_unfounded_cpc_mentions(s.text_he)
    return out


def _assignee_profile_section(profile: AssigneeProfile, *, has_cpc_data: bool) -> _RenderSection:
    sentences: list[_CiteSentence] = list(
        _clean_profile_sentences(profile.tech_product_chain, has_cpc_data=has_cpc_data)
    )
    if profile.recent_activity:
        sentences.append(_CiteSentence(text_he="פעילות עדכנית:"))
        sentences += _clean_profile_sentences(profile.recent_activity, has_cpc_data=has_cpc_data)
    sentences.append(_CiteSentence(text_he="השלכות:"))
    sentences += _clean_profile_sentences(profile.implications_he, has_cpc_data=has_cpc_data)
    return _RenderSection(title_he=f"פרופיל מקצה: {profile.assignee_name}", sentences=sentences)


def _business_action_sentences(actions: list[PatentBizAction]) -> list[_CiteSentence]:
    out: list[_CiteSentence] = []
    for a in actions:
        out.append(_CiteSentence(text_he=a.action_he))
        out.append(_CiteSentence(text_he=f"נימוק: {a.rationale_he}", cites=list(a.rationale_cites)))
    return out


def _build_draft_from_synthesis(
    synthesis: PatentSurveyDraft,
    open_points_extra: list[str],
    assignee_has_cpc: dict[str, bool] | None = None,
) -> _RenderableSurveyDraft:
    assignee_has_cpc = assignee_has_cpc or {}
    sections: list[_RenderSection] = [_RenderSection("נוף הפטנטים", _to_cite_sentences(synthesis.landscape))]
    if synthesis.tech_clusters:
        sections.append(_RenderSection("אשכולות טכנולוגיה", _to_cite_sentences(synthesis.tech_clusters)))
    for profile in synthesis.assignee_profiles:
        sections.append(
            _assignee_profile_section(
                profile, has_cpc_data=assignee_has_cpc.get(profile.assignee_name, False)
            )
        )
    if synthesis.white_spaces:
        sections.append(
            _RenderSection("חורים והזדמנויות (White Space)", _to_cite_sentences(synthesis.white_spaces))
        )
    if synthesis.israel_position:
        sections.append(
            _RenderSection("עמדת התעשייה הישראלית", _to_cite_sentences(synthesis.israel_position))
        )
    sections.append(
        _RenderSection(
            "השלכות עסקיות והמלצות", _business_action_sentences(synthesis.business_implications)
        )
    )
    return _RenderableSurveyDraft(
        exec_summary=_to_cite_sentences(synthesis.exec_summary),
        sections=sections,
        outlook=_to_cite_sentences(synthesis.outlook),
        open_points_he=list(synthesis.open_points_he) + open_points_extra,
    )


def _fallback_draft(open_points_extra: list[str]) -> _RenderableSurveyDraft:
    return _RenderableSurveyDraft(
        exec_summary=[_CiteSentence(text_he=_NO_LLM_TEXT_HE)],
        open_points_he=open_points_extra,
    )


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
    qa_report = {
        "note": (
            "citation QA gate (eoa.report.qa_citations) not applied to patent surveys -- citation "
            "discipline is enforced by construction instead, at the PatentSurveyDraft pydantic-"
            "schema level (every sentence carries non-empty cites or is labelled general knowledge)"
        ),
        "topic": topic,
    }
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
    territory: str | None = None,
) -> PatentSurveyPaths:
    """A14 step 5: the full on-demand "סקר פטנטים" pipeline for one free-text ``topic`` (min 8
    characters, enforced by the API layer). ``territory`` (2026-09-06, optional, e.g. ``"US"``)
    restricts the gathered sample to that territory's publication-number prefix before any
    aggregate/synthesis step runs -- see :func:`_territory_filter`."""
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

        rows = _territory_filter(_fetch_patent_rows(patent_ids), territory)

        registry: list[dict[str, Any]] = [
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
                "kind": "patent",
            }
            for i, row in enumerate(rows)
        ]

        top_cpc = _cluster_by(rows, "cpc")
        top_assignees = _cluster_assignees(rows)
        timeline = _timeline_by_year(rows)
        israel_count, israel_companies = _israel_position(rows)
        white_spaces = _white_spaces(
            rows,
            [c for c, _ in top_cpc.most_common(5)],
            [a for a, _ in top_assignees.most_common(5)],
        )

        open_points_extra = (
            []
            if scan_mod.structured_sources_configured()
            else ["מקורות פטנטים: מצב חיפוש בלבד -- הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי מלא."]
        )

        profile_names = _select_profile_assignees(top_assignees)
        synthesis: PatentSurveyDraft | None = None
        assignee_has_cpc: dict[str, bool] = {}
        if profile_names:
            since = period_end - dt.timedelta(days=PROFILE_LOOKBACK_DAYS)
            profile_blocks: list[str] = []
            for name in profile_names:
                canonical = resolve_canonical(name)
                names_for_matching = _assignee_names_for_matching(name, canonical)
                cpc_cluster = Counter(
                    c for r in rows for c in (r.get("cpc") or []) if name in (r.get("assignees") or [])
                ).most_common(6)
                assignee_has_cpc[name] = bool(cpc_cluster)
                patent_ns = [it["n"] for it in registry if name in (it.get("assignees") or [])]
                market_items = _assignee_market_items(names_for_matching, since)
                events = _assignee_events(names_for_matching, since)
                _extend_registry_with_db_records(registry, market_items, item_id_key="id")
                _extend_registry_with_db_records(registry, events, item_id_key="item_id")
                profile_blocks.append(
                    _assignee_profile_input_block(
                        name, canonical, cpc_cluster, patent_ns, market_items, events
                    )
                )

            data_block = _synthesis_data_block(
                topic, registry, top_cpc, top_assignees, timeline, israel_count, israel_companies,
                profile_blocks,
            )
            synthesis = _run_synthesis(topic, registry, data_block, role=role, interactive=interactive)
        else:
            log.info("patent_survey_no_real_assignees", topic=topic, patent_count=len(rows))
            open_points_extra = [*open_points_extra, _NO_ASSIGNEE_DATA_HE]

        draft = (
            _build_draft_from_synthesis(synthesis, open_points_extra, assignee_has_cpc)
            if synthesis
            else _fallback_draft(open_points_extra)
        )

        patent_rows_for_table = [it for it in registry if it.get("kind") != "db_item"]
        tables = [
            {
                "title_he": "נספח פטנטים",
                "headers": ["מספר", "כותרת EN", "מקצה", "CPC", "ציון ערך", "קישור"],
                "rows": [
                    [
                        it["n"],
                        ltr_isolate(it["title"]),
                        ltr_join(it["assignees"]),
                        ltr_join(it["cpc"]),
                        it["value_score"] if it["value_score"] is not None else "—",
                        it.get("url") or "—",
                    ]
                    for it in patent_rows_for_table
                ],
            },
            {
                "title_he": "בעלי פטנטים מובילים",
                "headers": ["בעלים", "מספר פטנטים"],
                "rows": [[ltr_isolate(a), n] for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)],
            },
            {
                "title_he": "ציר זמן שנתי",
                "headers": ["שנה", "מספר פטנטים"],
                "rows": [[y, n] for y, n in sorted(timeline.items())],
            },
            {
                "title_he": "קודי CPC מובילים",
                "headers": ["קוד CPC", "מספר פטנטים"],
                "rows": [[ltr_isolate(c), n] for c, n in top_cpc.most_common(TOP_N_CPC)],
            },
        ]
        if white_spaces:
            tables.append(
                {
                    "title_he": "פערים (White Space): צירופי CPC x בעלים שאינם מכוסים",
                    "headers": ["קוד CPC", "בעלים מוביל"],
                    "rows": [[ltr_isolate(c), ltr_isolate(a)] for c, a in white_spaces],
                }
            )

        title_text = f"סקר פטנטים: {topic}"
        items_for_appendix = [
            {
                "n": it["n"],
                "title": ltr_isolate_if_latin(it["title"]),
                "source_name": (
                    ", ".join(it["assignees"]) if it.get("kind") != "db_item" else it.get("source_name")
                )
                or it.get("source_name"),
                "url": it.get("url"),
                "published_at": it.get("publication_date") or it.get("published_at"),
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
        log.info(
            "patent_survey_done",
            topic=topic,
            patent_count=len(patent_rows_for_table),
            report_id=report_id,
        )
        return PatentSurveyPaths(
            docx=docx_path,
            md=md_path,
            html=html_path,
            report_id=report_id,
            survey_id=survey_id,
            topic=topic,
            patent_count=len(patent_rows_for_table),
        )
    except Exception:
        _finish_survey_row(survey_id, status="failed", report_id=None)
        raise
