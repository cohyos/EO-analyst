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
import time
from collections import Counter
from collections.abc import Callable
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
from eoa.llm.schemas.patents import (
    AssigneeProfile,
    ClusterNarrative,
    PatentBizAction,
    PatentCiteSentence,
    PatentSurveyDraft,
)
from eoa.patents import cluster as cluster_mod
from eoa.patents import scan as scan_mod
from eoa.patents.analyze import analyze_patents, generate_advance_descriptions
from eoa.patents.render import (
    ascii_timeline,
    find_table_by_headers,
    inject_advance_footnotes_html,
    inject_advance_footnotes_md,
    insert_section_before_html_appendix,
    insert_section_before_html_summary,
    insert_section_before_md_appendix,
    insert_section_before_md_summary,
    insert_section_before_summary_docx,
    ltr_isolate,
    ltr_isolate_if_latin,
    ltr_join,
    shade_timeline_table_rows,
    svg_timeline_bar_chart,
)
from eoa.patents.valuation import score_and_persist
from eoa.pipeline.entity_normalize import (
    find_watchlist_aliases_in_text,
    resolve_canonical,
    resolve_country_name,
)
from eoa.report.docx_builder import (
    _domain_from_url,
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

# Round 3 D8 finding 1 (2026-09-06, judge round_1_judge.md): the Anduril survey's executive
# summary asserted Anduril was "the exclusive player" in its field from a sample where 5 of 6
# patents simply carried no assignee data at all (a keyless-search data-source gap, never evidence
# of exclusivity/market share) -- below this coverage bar, a claim of exclusivity/market
# domination is unsupportable and must be replaced with an explicit caveat instead.
_ASSIGNEE_COVERAGE_THRESHOLD = 0.70
_COVERAGE_CAVEAT_TEMPLATE_HE = (
    "ל-{missing} מתוך {total} הפטנטים אין נתוני מקצה — לא ניתן להסיק בלעדיות או נתח שוק."
)
_EXCLUSIVITY_CLAIM_RE = re.compile(
    r"בלעדי|השחקן\s+היחיד|שולט\S*\s+בשוק|\bexclusive\b|\bonly\s+player\b", re.IGNORECASE
)

# Round 3 D8 finding 3 (2026-09-06): when the gathered sample genuinely carries no CPC codes /
# publication-year data at all (the common case for the keyless Google-Patents-search fallback --
# see eoa.patents.scan's own docstring), the corresponding table must never render as a bare,
# silently-empty heading (the exact shape docs/qa/loop/round_1_judge.md flagged the deterministic
# D8 auto-check for missing) -- it is replaced by one of these explicit disclosure sentences
# instead, in both the section itself and the survey's own open-questions list.
_TIMELINE_DISCLOSURE_HE = (
    "אין נתוני ציר זמן שנתי זמינים לפטנטים במדגם זה (חסרים תאריכי פרסום/הגשה במקור הנתונים)."
)
_CPC_DISCLOSURE_HE = (
    "אין נתוני קודי CPC זמינים לפטנטים במדגם זה (מקור החיפוש חסר-המפתחות אינו מספק סיווג CPC -- "
    "ראו eoa.patents.scan; הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי מלא)."
)

# Round 3 D8 finding 4 (2026-09-06): a survey whose LLM synthesis stage never reached Ollama (or
# any configured fallback) despite retries must never be left silently "final" with an empty
# narrative -- this marker is both human-readable (shown in the report itself) and, via
# _persist_report's qa_report["narrative_pending"] flag, machine-detectable by
# find_surveys_pending_narrative for a later regeneration pass (regenerate_pending_narrative).
NARRATIVE_PENDING_MARKER_HE = (
    "⏳ ניתוח שפה טבעית ממתין לרענון: המודל המקומי (Ollama) לא היה זמין במהלך יצירת הסקר, גם "
    "לאחר מספר ניסיונות חוזרים -- הטבלאות הדטרמיניסטיות למעלה (נספח פטנטים, אשכולות, ציר זמן, "
    "יחסים עסקיים) תקפות ומלאות ואינן תלויות ב-LLM. סקר זה מסומן לרענון אוטומטי -- ראו "
    "eoa.patents.survey.find_surveys_pending_narrative / regenerate_pending_narrative."
)


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


_TOPIC_STOPWORDS = frozenset(
    [
        "patents",
        "patent",
        "with",
        "and",
        "the",
        "of",
        "for",
        "in",
        "on",
        "a",
        "an",
        "or",
        "עם",
        "של",
        "על",
        "את",
        "פטנטים",
        "פטנט",
        "ב",
        "ל",
        "מ",
    ]
)
#: Minimum stored patents a survey should rest on before the keyless live search's result alone is
#: trusted; below this the topic's stored patents are pulled in (see _stored_patent_ids_for_topic).
_MIN_SURVEY_PATENTS = 5


def _topic_keywords(topic: str) -> list[str]:
    """Content words of a survey topic ("FPA עם פיקסל דיגיטלי (DROIC)" -> fpa, פיקסל, דיגיטלי,
    droic) for the stored-patent lookup: ≥3 chars, stop-words dropped, order preserved."""
    out: list[str] = []
    for tok in re.findall(r"[\w-]+", topic.lower()):
        tok = tok.strip("-_")
        if len(tok) >= 3 and tok not in _TOPIC_STOPWORDS and tok not in out:
            out.append(tok)
    return out


#: A topic keyword is *distinctive* when at most this share of stored patents mention it
#: ("anduril", "droic"); the rest are generic vocabulary ("optical", "tracking") that alone must
#: never pull a patent into a survey -- the Anduril survey once filled up with 1960s "optical
#: tracking system" patents on that basis (user finding 2026-09-06 evening).
_DISTINCTIVE_DF_MAX = 0.15
#: Stored patents older than this are left out of an on-demand survey unless nothing newer exists
#: (a technology-map survey is about the current landscape; expired art belongs to the timeline
#: only when it actually matches the topic's distinctive terms).
_SUPPLEMENT_MAX_AGE_YEARS = 25


def _keyword_document_frequency(kws: list[str]) -> dict[str, float]:
    """Share of stored patents (title/abstract/assignees/cpc) mentioning each keyword."""
    if not kws:
        return {}
    exprs = ", ".join(
        f"COUNT(*) FILTER (WHERE haystack ILIKE %(kw{i})s)::float AS c{i}" for i in range(len(kws))
    )
    params: dict[str, Any] = {f"kw{i}": f"%{kw}%" for i, kw in enumerate(kws)}
    sql = f"""
        SELECT COUNT(*)::float AS n, {exprs}
        FROM (
            SELECT COALESCE(title, '') || ' ' || COALESCE(abstract, '') || ' ' ||
                   COALESCE(array_to_string(cpc, ' '), '') || ' ' ||
                   COALESCE(array_to_string(assignees, ' '), '') AS haystack
            FROM patents
        ) h
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return {}
    n = float(row["n"] or 0.0)
    return {kw: (float(row[f"c{i}"] or 0.0) / n if n else 0.0) for i, kw in enumerate(kws)}


def _stored_patent_ids_for_topic(
    topic: str, *, limit: int = 60, exclude: list[int] | None = None
) -> list[int]:
    """Round-3 (surveys 45/46, 2026-09-06): the on-demand survey used to rest solely on a fresh
    keyless web search, so a DuckDuckGo timeout produced a survey with zero patents while the
    ``patents`` table already held the topic's rows. Returns stored patent ids that match the
    topic *specifically*: at least one distinctive keyword (document frequency <=
    :data:`_DISTINCTIVE_DF_MAX`) plus a second keyword or an assignee match; two distinctive
    keywords always qualify; generic keywords alone never do. When the topic has no distinctive
    keyword at all, a patent must match most of the keywords. Ranked by distinctive hits, total
    hits, then value_score; patents older than :data:`_SUPPLEMENT_MAX_AGE_YEARS` are dropped
    when newer matches exist."""
    kws = _topic_keywords(topic)
    if not kws:
        return []
    df = _keyword_document_frequency(kws)
    distinctive = [kw for kw in kws if 0.0 < df.get(kw, 1.0) <= _DISTINCTIVE_DF_MAX]
    generic = [kw for kw in kws if kw not in distinctive]

    def _hits(cols: list[str], prefix: str) -> str:
        if not cols:
            return "0"
        return " + ".join(
            f"(CASE WHEN haystack ILIKE %({prefix}{i})s THEN 1 ELSE 0 END)" for i in range(len(cols))
        )

    params: dict[str, Any] = {}
    params.update({f"d{i}": f"%{kw}%" for i, kw in enumerate(distinctive)})
    params.update({f"g{i}": f"%{kw}%" for i, kw in enumerate(generic)})
    params.update({f"a{i}": f"%{kw}%" for i, kw in enumerate(distinctive)})
    params["limit"] = limit
    params["exclude"] = list(exclude or [])
    assignee_hit = (
        " + ".join(
            f"(CASE WHEN assignee_text ILIKE %(a{i})s THEN 1 ELSE 0 END)" for i in range(len(distinctive))
        )
        if distinctive
        else "0"
    )
    # one distinctive term is enough ("droic", "anduril"); generic terms and an assignee match only
    # rank the result higher -- they never admit a patent on their own. Without any distinctive
    # keyword a patent must match most of the generic ones.
    where = "d_hits >= 1" if distinctive else f"g_hits >= {max(2, (len(generic) + 1) // 2 + 1)}"
    sql = f"""
        SELECT id, d_hits, g_hits, publication_date FROM (
            SELECT id, value_score, publication_date,
                   {_hits(distinctive, "d")} AS d_hits,
                   {_hits(generic, "g")} AS g_hits,
                   {assignee_hit} AS a_hits
            FROM (
                SELECT id, value_score, publication_date,
                       COALESCE(title, '') || ' ' || COALESCE(abstract, '') || ' ' ||
                       COALESCE(array_to_string(cpc, ' '), '') || ' ' ||
                       COALESCE(array_to_string(assignees, ' '), '') AS haystack,
                       COALESCE(array_to_string(assignees, ' '), '') AS assignee_text
                FROM patents
                WHERE NOT (id = ANY(%(exclude)s::bigint[]))
            ) h
        ) scored
        WHERE {where}
        ORDER BY d_hits DESC, a_hits DESC, g_hits DESC, value_score DESC NULLS LAST, publication_date DESC NULLS LAST, id DESC
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cutoff = dt.date.today() - dt.timedelta(days=365 * _SUPPLEMENT_MAX_AGE_YEARS)
    recent = [r for r in rows if r.get("publication_date") is None or r["publication_date"] >= cutoff]
    return [r["id"] for r in (recent or rows)]


def _fetch_patent_rows(patent_ids: list[int]) -> list[dict[str, Any]]:
    if not patent_ids:
        return []
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pub_number, title, abstract, assignees, cpc, priority_date, filing_date, "
            "publication_date, grant_date, family_id, forward_citations, backward_citations, "
            "url, value_score, source, claims_summary_he FROM patents WHERE id = ANY(%(ids)s) "
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


# Round 5 D8 (2026-09-06, live example: patent_survey_FPA_.../2026-09-06, patent US20150015759A1,
# assignee parsed as bare "Europe"): a name that resolves to a *non*-company canonical record (a
# curated org entry like "NATO"/"Europe") was already excluded below, but a name that resolves to
# *nothing at all* on either curated list fell through to the permissive default ("unresolved ->
# treat as real company") -- exactly the case for a plain country name typed in English/Hebrew
# ("United States", "ארה\"ב") never registered as a curated org (only ``resolve_country_name``
# recognises it) and a bare corporate-suffix/generic-noun fragment ("Inc", "Ltd", "Systems") that a
# keyless-search scrape sometimes yields as the entire "assignee" string when the real company name
# was truncated by the source page's own formatting. Neither is ever a real company; both are
# rejected outright, before ``resolve_canonical`` is even consulted.
_GENERIC_ASSIGNEE_RE = re.compile(
    r"^(?:inc|incorporated|llc|ltd|limited|co|corp|corporation|company|group|holdings?|"
    r"international|systems?|technolog(?:y|ies)|industries|enterprises?|s\.?a\.?|gmbh|n\.?v\.?|"
    r"plc)\.?$",
    re.IGNORECASE,
)


def _is_real_company_assignee(name: str) -> bool:
    """A watchlist-recognised (or otherwise unresolved, i.e. not on any curated list at all)
    assignee name is treated as a real company; a name that resolves to a *non*-company canonical
    record (a country, or a curated government/org entry like "NATO"/"Europe"), a plain country
    name only ``resolve_country_name`` recognises ("United States", "ארה\"ב" -- round 5 D8), or a
    bare generic corporate-suffix/noun fragment (:data:`_GENERIC_ASSIGNEE_RE`, e.g. "Inc", "Ltd",
    "Systems" -- round 5 D8) is not -- see ``eoa.patents.scan._assignee_candidates_in_text``'s own
    docstring for why such a name could ever end up in ``patents.assignees`` in the first place (a
    keyless-search parsing artifact)."""
    if not name or name == "—":
        return False
    stripped = name.strip()
    if not stripped or _GENERIC_ASSIGNEE_RE.match(stripped) or resolve_country_name(stripped):
        return False
    canonical = resolve_canonical(stripped)
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


def _cpc_assignee_matrix(
    rows: list[dict[str, Any]], top_cpc: list[str], top_assignees: list[str]
) -> list[list[int]]:
    """Round 5 P5/item 6 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.5/item 7, 4/item 7 -- "White spaces:
    add a deterministic CPC x assignee matrix alongside the narrative"): a full ``len(top_cpc)`` x
    ``len(top_assignees)`` count grid (patents carrying both that CPC code and that assignee),
    complementing :func:`_white_spaces`'s own flat gap-list table with the actual coverage picture
    a reader needs to judge *how* white a "white space" cell really is (a cell of ``0`` here is
    exactly one row of :func:`_white_spaces`'s gap list). Caller renders this only when both
    ``top_cpc`` and ``top_assignees`` are non-empty (see :func:`build_patent_survey`) -- a matrix
    needs both axes to mean anything; with either axis empty there is nothing honest to draw."""
    counts: Counter[tuple[str, str]] = Counter()
    for row in rows:
        for cpc in row.get("cpc") or []:
            for assignee in row.get("assignees") or []:
                counts[(cpc, assignee)] += 1
    return [[counts.get((c, a), 0) for a in top_assignees] for c in top_cpc]


def _assignee_coverage(rows: list[dict[str, Any]]) -> tuple[int, int, float]:
    """``(missing, total, coverage)`` -- ``coverage`` is the fraction of ``rows`` (patents) that
    carry at least one real company assignee (:func:`_is_real_company_assignee`); ``missing`` is
    the complementary count. Round 3 D8 finding 1: gates exclusivity/market-share language against
    exactly this signal (see :data:`_ASSIGNEE_COVERAGE_THRESHOLD`) -- a sample where most patents
    simply have no assignee on record (a data-source gap) can never support a claim that one
    company "controls" or is "the only player" in the field. An empty sample counts as full
    (vacuous) coverage -- there is nothing to be under-covered."""
    total = len(rows)
    if total == 0:
        return 0, 0, 1.0
    with_assignee = sum(
        1 for r in rows if any(_is_real_company_assignee(a) for a in (r.get("assignees") or []))
    )
    return total - with_assignee, total, with_assignee / total


def _coverage_caveat_he(missing: int, total: int) -> str:
    return _COVERAGE_CAVEAT_TEMPLATE_HE.format(missing=missing, total=total)


# --------------------------------------------------------------------------
# Round 5 P5/item 1 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md 2.5/P1+P2, 3.5/item 1,
# 4/item 8): a deterministic "שיטה והיקף" (methodology & scope) box, rendered *before* the
# executive summary in every one of docx/md/html -- WIPO PLR-style disclosure of the search query,
# sources scanned, date range, record counts (fresh-this-run vs. supplemented-from-store), and both
# coverage percentages (assignee + CPC) as their own prominent lines, with the low-coverage caveat
# (:func:`_coverage_caveat_he`) folded in as the box's own last line instead of a sentence buried
# inside `exec_summary` (round 3 D8 finding 1's own mechanism). `eoa.report.docx_builder` has no
# "before executive summary" hook (only `extra_sections`' `after_summary`/`after_outlook`
# positions) and stays untouched per this task's ownership split -- the box is spliced into the
# already-rendered docx ``Document``/md/html via ``eoa.patents.render``'s new
# ``insert_section_before_summary_*`` helpers instead, mirroring how the ASCII/SVG timeline chart
# is already spliced in before the sources appendix.
# --------------------------------------------------------------------------

METHODOLOGY_BOX_TITLE_HE = "שיטה והיקף"


def _cpc_coverage(rows: list[dict[str, Any]]) -> tuple[int, int, float]:
    """``(missing, total, coverage)`` -- the CPC-code analogue of :func:`_assignee_coverage`:
    ``coverage`` is the fraction of ``rows`` that carry at least one CPC code. An empty sample
    counts as full (vacuous) coverage, same convention as :func:`_assignee_coverage`."""
    total = len(rows)
    if total == 0:
        return 0, 0, 1.0
    with_cpc = sum(1 for r in rows if r.get("cpc"))
    return total - with_cpc, total, with_cpc / total


def _date_range_he(rows: list[dict[str, Any]]) -> tuple[dt.date, dt.date] | None:
    """``(earliest, latest)`` across every row's ``publication_date`` (falling back to
    ``filing_date``/``priority_date`` when publication is missing, the common keyless-search-
    fallback case) -- ``None`` when not one row in ``rows`` carries any date at all."""
    dates = [r.get("publication_date") or r.get("filing_date") or r.get("priority_date") for r in rows]
    dates = [d for d in dates if d]
    if not dates:
        return None
    return min(dates), max(dates)


def _sources_scanned_he() -> str:
    """Which patent data source(s) this gather actually used (:func:`eoa.patents.scan.
    structured_sources_configured`) -- an honest label for the methodology box rather than always
    implying the full EPO OPS/PatentsView structured search that most dev/CI environments never
    have credentials for."""
    if scan_mod.structured_sources_configured():
        return "EPO OPS / PatentsView (מבני, עם השלמת Google Patents במקרה של תוצאה ריקה)"
    return "Google Patents (חיפוש חסר-מפתחות -- ראו eoa.patents.scan)"


def methodology_box_lines_he(
    *,
    topic: str,
    sources_scanned_he: str,
    date_range: tuple[dt.date, dt.date] | None,
    n_fresh: int,
    n_stored: int,
    assignee_with: int,
    assignee_total: int,
    cpc_with: int,
    cpc_total: int,
    caveat_he: str | None,
    term_derived_cluster_count: int = 0,
) -> list[str]:
    """The ordered lines of the "שיטה והיקף" box (see the module note above) -- a plain
    ``list[str]``, rendering-agnostic, so the same content reaches docx/md/html identically via
    ``eoa.patents.render``'s insertion helpers. ``כיסוי נתוני מקצה`` is deliberately its own line
    (never folded into the record-count line) so it reads as the prominent tag the spec asks for;
    ``caveat_he`` (:func:`_coverage_caveat_he`, only non-``None`` under
    :data:`_ASSIGNEE_COVERAGE_THRESHOLD`) is appended as the box's own last line -- this is now the
    *only* place that sentence appears in the survey's opening (round 5: no longer duplicated into
    `exec_summary`, see :func:`_enforce_coverage_caveat`'s own updated docstring). Round 6 D8
    finding 2 (2026-09-06/07): ``term_derived_cluster_count`` (the number of this survey's
    technology clusters that came from :func:`eoa.patents.cluster.tfidf_subcluster_unclassified`'s
    term-similarity heuristic rather than an actual CPC/taxonomy match) is appended as one more
    honest disclosure line whenever it is non-zero -- these clusters are never given the taxonomy
    label eoa.patents.cluster.cluster_patents' matched clusters carry, but a reader should still be
    told the labelling method differs."""
    assignee_pct = round((assignee_with / assignee_total) * 100) if assignee_total else 100
    cpc_pct = round((cpc_with / cpc_total) * 100) if cpc_total else 100
    date_range_text = (
        f"{fmt_date(date_range[0])} – {fmt_date(date_range[1])}"
        if date_range
        else "לא זמין (חסרים תאריכי פרסום/הגשה במקור הנתונים)"
    )
    lines = [
        f"שאילתת חיפוש: {ltr_isolate(topic)}",
        f"מאגרים שנסרקו: {sources_scanned_he}",
        f"טווח תאריכים: {ltr_isolate(date_range_text)}",
        f"מספר רשומות: {n_fresh + n_stored} (חדשות מהסריקה: {n_fresh}, מהמאגר הקיים: {n_stored})",
        f"כיסוי נתוני מקצה: {assignee_pct}% ({assignee_with}/{assignee_total})",
        f"כיסוי קודי CPC: {cpc_pct}% ({cpc_with}/{cpc_total})",
    ]
    if caveat_he:
        lines.append(caveat_he)
    if term_derived_cluster_count:
        lines.append(
            f"{term_derived_cluster_count} מאשכולות הטכנולוגיה במדגם זה מבוססי-מונחים (דמיון TF-IDF "
            "בין כותרת/תקציר) ולא על מיפוי קוד CPC/טקסונומיה -- ראו "
            "eoa.patents.cluster.tfidf_subcluster_unclassified."
        )
    return lines


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
               e.customer, e.program, i.url AS item_url, i.title AS item_title, i.summary_he AS item_summary_he,
               i.published_at, COALESCE(src.name, i.url) AS source_name
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


# --------------------------------------------------------------------------
# round 3 D8 finding 2 (2026-09-06): a relationship-map row hallucination -- "Anduril <-> Elbit
# Systems | מיזוג/רכישה | Sigma 155 howitzer system" was traceable to no source that actually named
# both parties (the cited source was solely a personnel-appointment article). Every
# eoa.patents.cluster.relationship_edges_from_events edge already carries the registry number of
# the one DB event it was built from (see that function's own docstring) -- this verifies, before
# the edge is ever rendered or handed to the LLM, that BOTH party names (or a watchlist alias of
# either, via eoa.pipeline.entity_normalize) literally appear in that same source's own text
# (event title + parent item title + parent item body) -- an edge that fails this is dropped, never
# silently rendered, and the drop is logged and counted in the survey's open-questions section.
#
# Round 5 D8 (2026-09-06, judge round_3_judge.md: "the untraceable Anduril-Elbit relationship edge
# was 'dropped' is false -- it's still there, live, now duplicated (Elbit + Elbit Systems)"): two
# real bugs behind that regression, both fixed here.
#
# 1. The round-3 ``_name_in_text`` matched a raw, word-boundary-free substring against *every*
#    watchlist alias, including a "strict" alias (``config/watchlist.yaml``'s ``strict_aliases:``
#    -- a product/program codename that collides with an ordinary word or an unrelated program,
#    e.g. Anduril's own "Anvil"/"Lattice"/"Roadrunner"). A source article about an unrelated Elbit
#    personnel appointment that merely happens to contain the ordinary English word "anvil" was
#    therefore read as "names Anduril" and let the edge survive verification -- reproduced live:
#    ``_name_in_text("Anduril", "... appointment today. The old blacksmiths anvil rang loudly ...")``
#    returned ``True`` under the old substring check. This now delegates to
#    ``eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text``, the same strict-alias-safe,
#    whole-word matcher already used for ``items.entities_mentioned`` backfill (a strict alias only
#    counts when the company's own canonical name or a non-strict alias also co-occurs in the same
#    text) -- a name that isn't on the watchlist at all still falls back to a plain whole-word/
#    -phrase, case-insensitive match (never the old bare ``in`` substring check).
# 2. Two raw assignee spellings of the very same real company ("Elbit" and "Elbit Systems" both
#    landing in ``top_assignees`` as distinct strings) each ran their own assignee-profile loop
#    iteration (``build_patent_survey``), independently rebuilding a relationship edge to the same
#    counterparty off the same underlying event -- both survived verification (correctly, since the
#    source genuinely named the company) but rendered as two literal-duplicate rows under two
#    different spellings. Edges are now deduped by *canonical* party pair (``resolve_canonical``
#    when the name is on the watchlist, else the raw name) + kind + citation number; a duplicate is
#    dropped (not silently -- logged the same way an unsupported edge is) rather than rendered
#    twice. The kept edge's own ``from``/``to`` text is left exactly as given (never rewritten to
#    the canonical spelling) so a caller matching on the original assignee name is unaffected.
# --------------------------------------------------------------------------


def _canonical_party_name(name: str) -> str:
    """The watchlist canonical display name for ``name`` (e.g. ``"Elbit Systems"`` -> ``"Elbit"``)
    when it resolves to one, else ``name`` unchanged -- used only as a dedup key (see module note
    above), never to rewrite a rendered edge's own ``from``/``to`` text."""
    canonical = resolve_canonical(name)
    return canonical["name"] if canonical else name


def _name_in_text(name: str, text: str) -> bool:
    """Whether ``name`` is genuinely named in ``text`` -- see the module-level note above for why
    this is no longer a bare case-insensitive substring check over every watchlist alias
    (word-boundary-free, and blind to ``strict_aliases``). A watchlist-resolvable name delegates to
    :func:`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text` (whole-word, strict-alias
    -safe); anything else falls back to a plain whole-word/-phrase, case-insensitive match."""
    if not name or not text:
        return False
    canonical = resolve_canonical(name)
    if canonical:
        return canonical["name"] in find_watchlist_aliases_in_text(text)
    return re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE) is not None


def _verify_relationship_edges(
    edges: list[dict[str, Any]], source_text_by_n: dict[int, str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split ``edges`` (each an ``{"from", "to", "kind", "n", ...}`` dict from
    :func:`eoa.patents.cluster.relationship_edges_from_events`) into ``(kept, dropped_notes)``: an
    edge is kept only when it carries a registry number ``n`` present in ``source_text_by_n`` *and*
    that source's own text names both ``from`` and ``to`` (via :func:`_name_in_text`, which also
    checks watchlist aliases), and is not a canonical-party-pair duplicate of an edge already kept
    (see the module note above -- "Elbit" and "Elbit Systems" naming the same real relationship
    collapse to one row). ``dropped_notes`` is one human-readable Hebrew sentence per dropped edge
    (unsupported *or* duplicate) -- never a silent drop."""
    kept: list[dict[str, Any]] = []
    dropped_notes: list[str] = []
    seen_pairs: set[tuple[str, str, Any, Any]] = set()
    for edge in edges:
        n = edge.get("n")
        text = source_text_by_n.get(n, "") if n is not None else ""
        kind_he = _EVENT_KIND_HE.get(edge.get("kind"), edge.get("kind") or "אחר")
        cite_he = f"[{n}]" if n is not None else "(ללא ציטוט)"
        if n is None or not _name_in_text(edge["from"], text) or not _name_in_text(edge["to"], text):
            dropped_notes.append(
                f'קשר "{edge["from"]} <-> {edge["to"]}" ({kind_he}) הוסר: המקור המצוטט '
                f"{cite_he} אינו מזכיר את שני הצדדים."
            )
            continue
        pair_key = (
            _canonical_party_name(edge["from"]),
            _canonical_party_name(edge["to"]),
            edge.get("kind"),
            n,
        )
        if pair_key in seen_pairs:
            dropped_notes.append(
                f'קשר "{edge["from"]} <-> {edge["to"]}" ({kind_he}) {cite_he} הוסר ככפילות: אותו '
                f'קשר כבר נכלל תחת "{pair_key[0]} <-> {pair_key[1]}" (איות/מקצה שונה של אותה ישות).'
            )
            continue
        seen_pairs.add(pair_key)
        kept.append(edge)
    return kept, dropped_notes


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
    lines.append("אשכולות CPC של המקצה: " + (", ".join(f"{c} ({n})" for c, n in cpc_cluster) or "—"))
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
        lines.append(
            f"לא נמצאה פעילות עסקית במאגר בחלון {PROFILE_LOOKBACK_DAYS} הימים האחרונים עבור מקצה זה."
        )
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


def _cluster_data_lines(clusters: list[cluster_mod.PatentCluster]) -> list[str]:
    lines = ["אשכולות (דטרמיניסטי -- cluster_label_he חייב להעתיק את התווית המצוטטת כאן בדיוק):"]
    for c in clusters:
        lines.append(
            f'- "{c.label_he}" | קוד CPC מוביל: {c.cpc_codes[0] if c.cpc_codes else "—"} | '
            f"גודל: {c.size} | קצב הגשה שנתי ממוצע: {c.filing_velocity_per_year:.1f} | "
            f"מקצה(י) דומיננטי: {', '.join(c.dominant_assignees) or '—'} | "
            f"בגרות (יחס הענקה {c.grant_ratio:.0%}): {c.maturity_label_he}"
        )
    return lines


def _relationship_data_lines(
    co_assign: Counter[tuple[str, str]],
    family_groups: dict[str, list[int]],
    relationship_edges: list[dict[str, Any]],
) -> list[str]:
    lines = ["יחסים עסקיים (דטרמיניסטי):"]
    for (a, b), n in co_assign.most_common(10):
        lines.append(f"- מקצים משותפים (co-assignment): {a} <-> {b} ({n} פטנט(ים) משותפים)")
    for fam, ns in list(family_groups.items())[:10]:
        lines.append(f"- משפחת פטנט משותפת ({ltr_isolate(fam)}): רשומות " + ", ".join(f"[{n}]" for n in ns))
    for edge in relationship_edges[:15]:
        kind_he = _EVENT_KIND_HE.get(edge.get("kind"), edge.get("kind") or "אחר")
        cite = f"[{edge['n']}]" if edge.get("n") is not None else "—"
        program = f" | תוכנית/מוצר: {edge['program']}" if edge.get("program") else ""
        lines.append(f"- {edge['from']} -> {edge['to']} ({kind_he}) {cite}{program}")
    if len(lines) == 1:
        lines.append("- לא זוהו קשרים עסקיים דטרמיניסטיים במדגם.")
    return lines


def _timeline_data_lines(
    timeline_rows: list[cluster_mod.TimelineRow],
    cluster_waves: dict[str, Counter[int]],
    assignee_waves: dict[str, Counter[int]],
) -> list[str]:
    expired = [r for r in timeline_rows if r.flag_he == cluster_mod.FLAG_EXPIRED_HE]
    expiring_soon = [r for r in timeline_rows if r.flag_he == cluster_mod.FLAG_EXPIRING_SOON_HE]
    pending = [r for r in timeline_rows if r.flag_he == cluster_mod.FLAG_PENDING_HE]
    lines = ["ציר זמן (דטרמיניסטי):"]
    lines.append(
        f"- פג תוקף (משוער): {len(expired)} "
        + ("(" + ", ".join(f"[{r.n}]" for r in expired[:10]) + ")" if expired else "")
    )
    lines.append(
        f"- עומד לפוג ב-3 השנים הקרובות: {len(expiring_soon)} "
        + ("(" + ", ".join(f"[{r.n}]" for r in expiring_soon[:10]) + ")" if expiring_soon else "")
    )
    lines.append(
        f"- בבחינה (טרם הענקה בפועל): {len(pending)} "
        + ("(" + ", ".join(f"[{r.n}]" for r in pending[:10]) + ")" if pending else "")
    )
    lines.append("גלי הגשות לפי אשכול (שנה: כמות):")
    for key, years in cluster_waves.items():
        lines.append(f"- {key}: " + ", ".join(f"{y}:{n}" for y, n in sorted(years.items())))
    lines.append("גלי הגשות לפי מקצה (שנה: כמות):")
    for key, years in list(assignee_waves.items())[:10]:
        lines.append(f"- {key}: " + ", ".join(f"{y}:{n}" for y, n in sorted(years.items())))
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
    clusters: list[cluster_mod.PatentCluster],
    co_assign: Counter[tuple[str, str]],
    family_groups: dict[str, list[int]],
    relationship_edges: list[dict[str, Any]],
    timeline_rows: list[cluster_mod.TimelineRow],
    cluster_waves: dict[str, Counter[int]],
    assignee_waves: dict[str, Counter[int]],
    assignee_missing: int = 0,
    assignee_total: int = 0,
) -> str:
    patent_count = sum(1 for it in registry if it.get("kind") != "db_item")
    coverage_pct = (
        round(100 * (assignee_total - assignee_missing) / assignee_total) if assignee_total else 100
    )
    lines = [
        f"נושא: {topic}",
        f'סה"כ פטנטים: {patent_count}',
        f'סה"כ רשומות ברשימה הממוספרת (פטנטים ואז רשומות מאגר): {len(registry)}',
        # Round 3 D8 finding 1 (2026-09-06, prompt rule 8): explicit ground truth for the
        # exclusivity/market-share guard -- see _scrub_exclusivity_claims for the deterministic
        # backstop this line supports.
        f"כיסוי נתוני מקצה: {assignee_total - assignee_missing} מתוך {assignee_total} הפטנטים "
        f"({coverage_pct}%) כוללים בעל-פטנטים מזוהה; ל-{assignee_missing} אין נתוני מקצה כלל.",
        "",
    ]
    lines.append(
        "קודי CPC מובילים: " + (", ".join(f"{c} ({n})" for c, n in top_cpc.most_common(TOP_N_CPC)) or "—")
    )
    lines.append(
        "בעלי פטנטים מובילים: "
        + (", ".join(f"{a} ({n})" for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)) or "—")
    )
    # A14b point 6 (goal 2026-09-06): the FULL deterministic per-assignee count -- not just the
    # top-N display list above -- so the model has an explicit, checkable ground truth for rule 7
    # ("never claim a real assignee has zero patents"); this is also what
    # eoa.patents.cluster.consistency_violations validates the returned draft against afterwards.
    lines.append(
        "ספירת פטנטים לפי מקצה (לבדיקת עקביות -- אסור לטעון 'אין פטנטים' עבור שם המופיע כאן עם מספר > 0): "
        + (", ".join(f"{a}: {n}" for a, n in top_assignees.most_common(30)) or "—")
    )
    lines.append(
        "ציר זמן (שנה: כמות): " + (", ".join(f"{y}: {n}" for y, n in sorted(timeline.items())) or "—")
    )
    lines.append(f"נוכחות ישראלית: {israel_count} רשומות; חברות: {', '.join(israel_companies) or 'אין'}")
    lines.append("")
    lines += _cluster_data_lines(clusters)
    lines.append("")
    lines += _relationship_data_lines(co_assign, family_groups, relationship_edges)
    lines.append("")
    lines += _timeline_data_lines(timeline_rows, cluster_waves, assignee_waves)
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


# Round 3 D8 finding 4 (2026-09-06, observed on report_id=35, "FPA/DROIC"): a single failed
# _run_synthesis call used to be treated as final, even for a transient cause (GPU momentarily
# busy, Ollama briefly unreachable) that a short wait would clear. eoa.llm.ollama_client already
# retries a schema-validation failure once *within* one call, and -- inside the pipeline/worker
# process with llm_providers.mode="cloud" -- walks a full role fallback chain that always
# terminates in local Ollama; this adds the one retry dimension neither of those covers: trying the
# whole synthesis call again, with a short pause, when the *entire* chain came back unavailable.
_SYNTHESIS_MAX_ATTEMPTS = 3
_SYNTHESIS_RETRY_DELAY_S = 5.0


def _run_synthesis_with_retries(
    topic: str,
    registry: list[dict[str, Any]],
    data_block: str,
    *,
    role: str,
    interactive: bool,
    max_attempts: int = _SYNTHESIS_MAX_ATTEMPTS,
    delay_s: float = _SYNTHESIS_RETRY_DELAY_S,
    sleep: Callable[[float], None] = time.sleep,
) -> PatentSurveyDraft | None:
    """:func:`_run_synthesis`, retried up to ``max_attempts`` times (with ``delay_s`` between
    attempts) before giving up -- ``sleep`` is injectable so unit tests exercise every attempt
    without actually waiting. Returns ``None`` only after every attempt failed/deferred (the
    caller then falls back to :func:`_fallback_draft` with :data:`NARRATIVE_PENDING_MARKER_HE` and
    flags the persisted report ``narrative_pending`` for later regeneration, see
    :func:`find_surveys_pending_narrative`)."""
    for attempt in range(1, max_attempts + 1):
        result = _run_synthesis(topic, registry, data_block, role=role, interactive=interactive)
        if result is not None:
            return result
        if attempt < max_attempts:
            log.info("patent_survey_synthesis_retry", topic=topic, attempt=attempt, max_attempts=max_attempts)
            sleep(delay_s)
    return None


_NO_LLM_TEXT_HE = "ניתוח שפה טבעית לא זמין כרגע (המודל המקומי אינו נגיש) -- הטבלאות הכמותיות למעלה תקפות."
_NO_ADVANCE_FALLBACK_HE = "תיאור התקדמות לא זמין."
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


# Round 5 (docs/REPORT_TEMPLATE_BENCHMARK.md P3/item 9, 2026-09-06): every business-implications
# action now carries an explicit priority + confidence (schema-enforced, eoa.llm.schemas.patents.
# PatentBizAction) -- mirrors eoa.report.bd_territory.BdAction/recommended_actions_table's own
# priority-rated recommended-actions table, in the high/medium/low + 0-1 shape this report's own
# spec asks for (that BD table uses a single-letter H/M/L code instead).
_PRIORITY_LABEL_HE = {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה"}
_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _priority_confidence_prefix_he(action: PatentBizAction) -> str:
    priority_he = _PRIORITY_LABEL_HE.get(action.priority, action.priority)
    return f"[עדיפות: {priority_he} | ביטחון: {round(action.confidence * 100)}%]"


def _business_action_sentences(actions: list[PatentBizAction]) -> list[_CiteSentence]:
    out: list[_CiteSentence] = []
    for a in actions:
        out.append(_CiteSentence(text_he=f"{_priority_confidence_prefix_he(a)} {a.action_he}"))
        out.append(_CiteSentence(text_he=f"נימוק: {a.rationale_he}", cites=list(a.rationale_cites)))
    return out


def business_implications_table(actions: list[PatentBizAction]) -> dict[str, Any] | None:
    """Deterministic priority/confidence table for "השלכות עסקיות והמלצות" (round 5, P3/item 9) --
    mirrors :func:`eoa.report.bd_territory.recommended_actions_table`'s own priority-ordered table
    shape (there: ``BdAction``'s H/M/L code; here: ``PatentBizAction.priority``'s high/medium/low +
    an explicit ``confidence`` percentage column). Sorted highest-priority first, ties broken by
    confidence descending -- same reading order a decision-maker would want. ``None`` when
    ``actions`` is empty (no synthesis / no actions at all -- nothing to render)."""
    if not actions:
        return None
    ordered = sorted(actions, key=lambda a: (_PRIORITY_ORDER.get(a.priority, 9), -a.confidence))
    rows = [
        [
            _PRIORITY_LABEL_HE.get(a.priority, a.priority),
            f"{round(a.confidence * 100)}%",
            a.action_he,
            f"{a.rationale_he} " + ("".join(f"[{n}]" for n in a.rationale_cites) or "—"),
        ]
        for a in ordered
    ]
    return {
        "title_he": "השלכות עסקיות והמלצות -- עדיפות וביטחון",
        "headers": ["עדיפות", "ביטחון", "פעולה", "נימוק"],
        "rows": rows,
    }


def _cluster_narrative_section(cluster: ClusterNarrative) -> _RenderSection:
    return _RenderSection(
        title_he=f"אשכול טכנולוגי: {cluster.cluster_label_he}",
        sentences=_to_cite_sentences(cluster.paragraph),
    )


def _build_draft_from_synthesis(
    synthesis: PatentSurveyDraft,
    open_points_extra: list[str],
    assignee_has_cpc: dict[str, bool] | None = None,
) -> _RenderableSurveyDraft:
    assignee_has_cpc = assignee_has_cpc or {}
    sections: list[_RenderSection] = [_RenderSection("נוף הפטנטים", _to_cite_sentences(synthesis.landscape))]
    for cluster in synthesis.tech_clusters:
        sections.append(_cluster_narrative_section(cluster))
    for profile in synthesis.assignee_profiles:
        sections.append(
            _assignee_profile_section(
                profile, has_cpc_data=assignee_has_cpc.get(profile.assignee_name, False)
            )
        )
    if synthesis.relationships:
        sections.append(
            _RenderSection("יחסים עסקיים (מפת יחסים)", _to_cite_sentences(synthesis.relationships))
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
        _RenderSection("השלכות עסקיות והמלצות", _business_action_sentences(synthesis.business_implications))
    )
    if synthesis.timeline_narrative:
        sections.append(_RenderSection("ציר זמן -- ניתוח", _to_cite_sentences(synthesis.timeline_narrative)))
    return _RenderableSurveyDraft(
        exec_summary=_to_cite_sentences(synthesis.exec_summary),
        sections=sections,
        outlook=_to_cite_sentences(synthesis.outlook),
        open_points_he=list(synthesis.open_points_he) + open_points_extra,
    )


# --------------------------------------------------------------------------
# consistency scrub (A14b point 6, goal 2026-09-06): fix the internal-consistency slip P2
# observed ("the summary said 'no Anduril patents' while patent 15 was Anduril") -- deterministic
# per-assignee counts are injected into the prompt (see _synthesis_data_block's dedicated line and
# patent_survey.md rule 7); this is the code-level backstop for when the model violates that rule
# anyway. A violating sentence is never silently dropped (that could remove a real, correctly-cited
# claim sitting next to the false one) -- its text is replaced with a short, honest, deterministic
# correction and the violation is surfaced as an open point so it's never silently invisible either.
# --------------------------------------------------------------------------


def _iter_sentence_lists(synthesis: PatentSurveyDraft) -> list[list[PatentCiteSentence]]:
    lists: list[list[PatentCiteSentence]] = [
        synthesis.exec_summary,
        synthesis.landscape,
        synthesis.relationships,
        synthesis.white_spaces,
        synthesis.israel_position,
        synthesis.timeline_narrative,
        synthesis.outlook,
    ]
    for cluster in synthesis.tech_clusters:
        lists.append(cluster.paragraph)
    for profile in synthesis.assignee_profiles:
        lists.append(profile.tech_product_chain)
        lists.append(profile.recent_activity)
        lists.append(profile.implications_he)
    return lists


def _consistency_replacement_he(violations: list[str]) -> str:
    return "תוקן אוטומטית לפי נתוני הנספח (הטענה המקורית סתרה את ספירת הפטנטים בפועל): " + " ".join(
        violations
    )


def _scrub_consistency_violations(synthesis: PatentSurveyDraft, assignee_counts: dict[str, int]) -> list[str]:
    """Mutates ``synthesis`` in place, replacing the text of every sentence/rationale that falsely
    claims a real, counted assignee has zero patents (:func:`eoa.patents.cluster.
    consistency_violations`); returns the list of violation messages found (empty if none), which
    the caller surfaces as explicit open points -- "rejected", per the user's own wording, means
    detected-and-corrected here, not a silent drop (docs/CONVENTIONS.md rule 5: never invent, but
    also never blocked or hidden)."""
    notes: list[str] = []
    for sentences in _iter_sentence_lists(synthesis):
        for s in sentences:
            violations = cluster_mod.consistency_violations(s.text_he, assignee_counts)
            if violations:
                notes.extend(violations)
                s.text_he = _consistency_replacement_he(violations)
    for action in synthesis.business_implications:
        violations = cluster_mod.consistency_violations(action.rationale_he, assignee_counts)
        if violations:
            notes.extend(violations)
            action.rationale_he = _consistency_replacement_he(violations)
    return notes


def _fallback_draft(
    open_points_extra: list[str], *, exec_summary_he: str = _NO_LLM_TEXT_HE
) -> _RenderableSurveyDraft:
    return _RenderableSurveyDraft(
        exec_summary=[_CiteSentence(text_he=exec_summary_he)],
        open_points_he=open_points_extra,
    )


# --------------------------------------------------------------------------
# round 3 D8 finding 1 (2026-09-06): exclusivity/market-share overclaim guard -- a deterministic
# backstop mirroring _scrub_consistency_violations's own shape (never silently drop a sentence,
# replace it with an honest, deterministic correction; log + surface the violation as an open
# point). Applied whenever _assignee_coverage falls under _ASSIGNEE_COVERAGE_THRESHOLD.
# --------------------------------------------------------------------------


def _scrub_exclusivity_claims(
    synthesis: PatentSurveyDraft, *, coverage: float, missing: int, total: int
) -> list[str]:
    """Mutates ``synthesis`` in place, replacing the text of every sentence/action that claims
    exclusivity or market domination (:data:`_EXCLUSIVITY_CLAIM_RE`) when ``coverage`` (see
    :func:`_assignee_coverage`) is under :data:`_ASSIGNEE_COVERAGE_THRESHOLD` -- such a claim can
    never be supported by a sample where most patents simply lack assignee data. Returns the list
    of violation notes found (empty when ``coverage`` already clears the bar, or no such claim was
    made) for the caller to surface as open points, same contract as
    :func:`_scrub_consistency_violations`."""
    if coverage >= _ASSIGNEE_COVERAGE_THRESHOLD:
        return []
    caveat = _coverage_caveat_he(missing, total)
    notes: list[str] = []
    for sentences in _iter_sentence_lists(synthesis):
        for s in sentences:
            if _EXCLUSIVITY_CLAIM_RE.search(s.text_he):
                notes.append(
                    f'הוסרה טענת בלעדיות/נתח-שוק שאינה נתמכת (כיסוי מקצה {round(coverage * 100)}%): "{s.text_he}"'
                )
                s.text_he = caveat
    for action in synthesis.business_implications:
        if _EXCLUSIVITY_CLAIM_RE.search(action.rationale_he):
            notes.append(f'הוסרה טענת בלעדיות/נתח-שוק שאינה נתמכת בנימוק פעולה: "{action.rationale_he}"')
            action.rationale_he = caveat
        if _EXCLUSIVITY_CLAIM_RE.search(action.action_he):
            notes.append(f'הוסרה טענת בלעדיות/נתח-שוק שאינה נתמכת בפעולה: "{action.action_he}"')
            action.action_he = caveat
    return notes


def _enforce_coverage_caveat(draft: _RenderableSurveyDraft, missing: int, total: int) -> None:
    """Guarantees the coverage caveat (:func:`_coverage_caveat_he`) is present verbatim in every
    assignee-profile section of ``draft`` -- required regardless of whether the LLM (or the no-LLM
    fallback path) ever wrote an exclusivity claim to scrub, per the round 3 D8 finding 1 spec ("the
    survey must carry an explicit caveat sentence ... in the assignee-profile section").

    Round 5 (docs/REPORT_TEMPLATE_BENCHMARK.md 3.5/item 2 -- "תקציר מנהלים: כפי שקיים, בלי לחזור על
    נתון הכיסוי"): this no longer also force-appends the caveat to ``draft.exec_summary`` the way
    round 3 did -- the caveat now has one single, prominent home (the "שיטה והיקף" box built by
    :func:`methodology_box_lines_he` and rendered before the executive summary), and repeating it
    verbatim inside the summary text as well is exactly the redundancy the round-5 spec asks to
    drop. A genuinely false exclusivity *claim* the LLM wrote into the summary is still caught and
    replaced by :func:`_scrub_exclusivity_claims` (a correction of bad content, not an addition of
    a repeated disclosure line) -- that mechanism is unrelated to, and unchanged by, this one."""
    caveat = _coverage_caveat_he(missing, total)
    for section in draft.sections:
        if section.title_he.startswith("פרופיל מקצה:") and not any(
            s.text_he == caveat for s in section.sentences
        ):
            section.sentences.append(_CiteSentence(text_he=caveat))


# --------------------------------------------------------------------------
# R8-reports #5 (round-7 judge D8): sources-appendix "אמינות" column
# --------------------------------------------------------------------------
#
# ``eoa.report.docx_builder``'s own appendix renderers already call ``_reliability_for(item)`` on
# every row (docx and markdown alike), which returns ``item["reliability"]`` verbatim when present
# -- so a `db_item` registry entry (its `source_name` is a real `sources.name`, e.g. a news article
# cited into the survey) is *already* populated for free by that function's own name-based
# ``sources`` lookup; no change needed here for those rows. A patent-registry entry's `source_name`
# is instead the comma-joined assignee list (never a real outlet, see `items_for_appendix` above),
# so name-based lookup never matches it -- :func:`_appendix_reliability` derives a value for those
# rows the way the round-7 brief calls for: a host match between the patent's own ``url`` and
# ``sources.url`` (read-only). Most pure-patent rows (Google Patents / a national patent office,
# never a monitored news source) will still legitimately render "—" -- honest, not a bug; a
# genuine hit only happens when the same host also appears in ``sources``.

_SOURCE_HOST_RELIABILITY_CACHE: dict[str, int] | None = None


def _source_host_reliability_map() -> dict[str, int]:
    """``url host -> sources.reliability`` (1-5), loaded once per process -- the host-keyed
    counterpart of ``eoa.report.docx_builder``'s own name-keyed ``_source_reliability_map``. An
    unreachable DB (unit tests, offline renders) yields an empty map, same "—" degrade."""
    global _SOURCE_HOST_RELIABILITY_CACHE
    if _SOURCE_HOST_RELIABILITY_CACHE is None:
        try:
            with connection(timeout=5) as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT url, reliability FROM sources WHERE url IS NOT NULL AND reliability IS NOT NULL"
                )
                rows = cur.fetchall()
                mapping: dict[str, int] = {}
                for r in rows:
                    url = r["url"] if isinstance(r, dict) else r[0]
                    reliability = r["reliability"] if isinstance(r, dict) else r[1]
                    if not url:
                        continue
                    mapping[_domain_from_url(str(url))] = int(reliability)
                _SOURCE_HOST_RELIABILITY_CACHE = mapping
        except Exception:
            _SOURCE_HOST_RELIABILITY_CACHE = {}
    return _SOURCE_HOST_RELIABILITY_CACHE


def _appendix_reliability(it: dict[str, Any]) -> Any:
    """The ``reliability`` value to attach to one ``items_for_appendix`` entry -- ``None`` (the
    appendix renders "—", same as an item with no reliability data at all) for a ``db_item`` entry
    (``docx_builder._reliability_for`` already resolves those via its own real ``source_name``) or
    a patent entry whose ``url`` host doesn't match any monitored ``sources`` row."""
    if it.get("kind") == "db_item":
        return None
    url = it.get("url")
    if not url:
        return None
    val = _source_host_reliability_map().get(_domain_from_url(url))
    if val is None:
        return None
    return {"kind": "primary" if val >= 4 else "secondary", "score": round(val / 5, 2), "label": None}


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
    topic: str,
    period_end: dt.date,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    patent_ids: list[int],
    *,
    narrative_pending: bool = False,
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
        # Round 3 D8 finding 4: machine-detectable twin of NARRATIVE_PENDING_MARKER_HE -- true only
        # when synthesis exhausted every retry against a real LLM attempt (never for the unrelated
        # "no real assignee data at all" fallback, which no amount of LLM retrying would fix) --
        # see find_surveys_pending_narrative.
        "narrative_pending": narrative_pending,
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
# round 3 D8 finding 4 (2026-09-06): nightly-maintenance regeneration hook. Detection
# (find_surveys_pending_narrative) and the actual regeneration (regenerate_pending_narrative) are
# plain functions here -- no scheduler wiring; docs/MODULES.md "Round 3 D8" describes where a
# nightly job (agent/eoa/orchestrator/jobs.py or the night pipeline's own job list) would call
# these, since wiring a new scheduled job is outside agent/eoa/patents/**'s ownership for this task.
# --------------------------------------------------------------------------


def find_surveys_pending_narrative(limit: int = 20) -> list[dict[str, Any]]:
    """Every ``patent_survey`` report whose persisted ``qa_report`` JSON carries
    ``narrative_pending: true`` (set by :func:`_persist_report` when
    :func:`_run_synthesis_with_retries` exhausted every attempt) -- a survey whose deterministic
    tables rendered fully but whose LLM narrative never did. Returns
    ``[{"report_id", "survey_id", "topic"}, ...]``, most recent first."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.id AS report_id, s.id AS survey_id, s.topic
            FROM reports r
            JOIN patent_surveys s ON s.report_id = r.id
            WHERE r.kind = 'patent_survey' AND (r.qa_report ->> 'narrative_pending')::boolean IS TRUE
            ORDER BY r.id DESC
            LIMIT %(limit)s
            """,
            {"limit": limit},
        )
        return cur.fetchall()


def regenerate_pending_narrative(
    survey_id: int, *, role: str = "resident", interactive: bool = False
) -> PatentSurveyPaths:
    """Re-run the full survey pipeline for a survey previously flagged pending
    (:func:`find_surveys_pending_narrative`) by looking up its original ``topic`` and calling
    :func:`build_patent_survey` again -- a fresh run (including a fresh ``patent_surveys`` row; the
    stale pending row is left as historical record, same convention as a plain re-run of the same
    topic). Territory scoping is not persisted on ``patent_surveys`` today, so a regeneration
    always re-runs unscoped (docs/MODULES.md "Round 3 D8" notes this as a follow-up)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT topic FROM patent_surveys WHERE id = %(id)s", {"id": survey_id})
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"no patent_surveys row with id={survey_id}")
    return build_patent_survey(row["topic"], role=role, interactive=interactive)


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
        try:
            records = scan_mod.search_records(topic, limit=deep_limit)
        except Exception as exc:  # a search outage must not empty the survey
            log.warning("patent_survey_search_failed", topic=topic, error=str(exc)[:200])
            records = []
        pub_to_id = scan_mod.upsert_records(records)
        patent_ids = list(pub_to_id.values())
        n_fresh = len(patent_ids)
        n_stored = 0
        if len(patent_ids) < _MIN_SURVEY_PATENTS:
            stored = _stored_patent_ids_for_topic(topic, exclude=patent_ids)
            if stored:
                log.info(
                    "patent_survey_supplemented_from_store",
                    topic=topic,
                    fresh=len(patent_ids),
                    stored=len(stored),
                )
                n_stored = len(stored)
                patent_ids = [*patent_ids, *stored]

        # Round 6 D8 finding 1 (2026-09-06/07): the keyless Google-Patents-search fallback only
        # carries title+snippet, so most gathered records land with no assignee at all -- for a
        # capped number of the survey's own patents (fresh-this-run and supplemented-from-store
        # alike) still missing one, fetch that patent's own Google Patents detail page and backfill
        # assignee/CPC/priority-date onto the stored row (see eoa.patents.scan's own docstring for
        # this section). Never blocks the survey on a network hiccup.
        try:
            scan_mod.enrich_stored_patents_missing_assignee(patent_ids)
        except Exception as exc:
            log.warning("patent_survey_enrich_assignees_failed", topic=topic, error=str(exc)[:200])

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
        assignee_missing, assignee_total, assignee_coverage = _assignee_coverage(rows)
        cpc_missing, cpc_total, _cpc_coverage_frac = _cpc_coverage(rows)

        registry: list[dict[str, Any]] = [
            {
                "n": i + 1,
                "id": row["id"],
                "title": row.get("title") or row["pub_number"],
                "abstract": row.get("abstract") or "",
                "assignees": row.get("assignees") or [],
                "priority_date": row.get("priority_date"),
                "filing_date": row.get("filing_date"),
                "publication_date": row.get("publication_date"),
                "grant_date": row.get("grant_date"),
                "family_id": row.get("family_id"),
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
        matrix_cpc = [c for c, _ in top_cpc.most_common(5)]
        matrix_ws_assignees = [a for a, _ in top_assignees.most_common(5)]
        cpc_assignee_matrix = (
            _cpc_assignee_matrix(rows, matrix_cpc, matrix_ws_assignees)
            if matrix_cpc and matrix_ws_assignees
            else []
        )

        # A14b (2026-09-06): deterministic clustering (point 2), business relationships (point 3),
        # and timeline/expiry math (point 6) -- all pure, DB-independent computation over the
        # registry/rows already gathered above; see eoa.patents.cluster's own module docstring.
        # Computed before the methodology box below (round 6 D8 finding 2) so that box can report
        # how many of these clusters are term-derived (TF-IDF sub-clusters of the unclassified
        # bucket) rather than mapped to a real CPC/taxonomy entry.
        patent_registry_entries = [it for it in registry if it.get("kind") != "db_item"]
        clusters = cluster_mod.cluster_patents(patent_registry_entries, topics=scan_mod.load_watch_topics())
        term_derived_cluster_count = sum(
            1 for c in clusters if c.key.startswith(cluster_mod.UNCLASSIFIED_KEY)
        )

        # Round 5 P5/item 1: the "שיטה והיקף" box (see methodology_box_lines_he's own docstring)
        # -- spliced before the executive summary in every output format near the very end of this
        # function, after doc/md/html are already rendered (see below).
        methodology_box_lines = methodology_box_lines_he(
            topic=topic,
            sources_scanned_he=_sources_scanned_he(),
            date_range=_date_range_he(rows),
            n_fresh=n_fresh,
            n_stored=n_stored,
            assignee_with=assignee_total - assignee_missing,
            assignee_total=assignee_total,
            cpc_with=cpc_total - cpc_missing,
            cpc_total=cpc_total,
            caveat_he=(
                _coverage_caveat_he(assignee_missing, assignee_total)
                if assignee_coverage < _ASSIGNEE_COVERAGE_THRESHOLD
                else None
            ),
            term_derived_cluster_count=term_derived_cluster_count,
        )

        cross_links = cluster_mod.cross_cluster_links(clusters)
        co_assign = cluster_mod.co_assignment_pairs(rows)
        family_groups = cluster_mod.same_family_groups(patent_registry_entries)
        timeline_rows = cluster_mod.build_timeline_rows(patent_registry_entries, today=period_end)
        id_to_cluster_label = {pid: c.label_he for c in clusters for pid in c.patent_ids if pid is not None}
        cluster_waves = cluster_mod.filing_waves(rows, lambda r: id_to_cluster_label.get(r.get("id")))
        assignee_waves = cluster_mod.filing_waves(rows, lambda r: next(iter(r.get("assignees") or []), None))

        open_points_extra = (
            []
            if scan_mod.structured_sources_configured()
            else ["מקורות פטנטים: מצב חיפוש בלבד -- הזן EPO_OPS_KEY/PATENTSVIEW_API_KEY ב-.env לכיסוי מלא."]
        )

        profile_names = _select_profile_assignees(top_assignees)
        synthesis: PatentSurveyDraft | None = None
        synthesis_llm_failed = False
        assignee_has_cpc: dict[str, bool] = {}
        relationship_edges: list[dict[str, Any]] = []
        dropped_relationship_notes: list[str] = []
        profile_blocks: list[str] = []
        if profile_names:
            since = period_end - dt.timedelta(days=PROFILE_LOOKBACK_DAYS)
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
                raw_edges = cluster_mod.relationship_edges_from_events(name, events)
                source_text_by_n = {
                    ev["n"]: " ".join(
                        filter(None, [ev.get("title"), ev.get("item_title"), ev.get("item_summary_he")])
                    )
                    for ev in events
                    if ev.get("n") is not None
                }
                verified_edges, dropped_notes = _verify_relationship_edges(raw_edges, source_text_by_n)
                relationship_edges += verified_edges
                dropped_relationship_notes += dropped_notes
                profile_blocks.append(
                    _assignee_profile_input_block(
                        name, canonical, cpc_cluster, patent_ns, market_items, events
                    )
                )

            if dropped_relationship_notes:
                log.warning(
                    "patent_survey_relationship_edges_dropped",
                    topic=topic,
                    count=len(dropped_relationship_notes),
                )
                open_points_extra = [
                    *open_points_extra,
                    f"{len(dropped_relationship_notes)} קשרים הוסרו כי המקורות לא תומכים בהם.",
                ]

        else:
            log.info("patent_survey_no_real_assignees", topic=topic, patent_count=len(rows))
            open_points_extra = [*open_points_extra, _NO_ASSIGNEE_DATA_HE]
        # Round-3 fix (survey 48, FPA/DROIC): the narrative used to run only when at least one
        # assignee was known -- keyless patent data has none, so a 17-patent survey shipped with
        # no synthesis at all. The technology map is drawn from abstracts/claims and needs no
        # assignee; the assignee profile/exclusivity sections stay gated by coverage.
        if rows:
            data_block = _synthesis_data_block(
                topic,
                registry,
                top_cpc,
                top_assignees,
                timeline,
                israel_count,
                israel_companies,
                profile_blocks,
                clusters,
                co_assign,
                family_groups,
                relationship_edges,
                timeline_rows,
                cluster_waves,
                assignee_waves,
                assignee_missing=assignee_missing,
                assignee_total=assignee_total,
            )
            synthesis = _run_synthesis_with_retries(
                topic, registry, data_block, role=role, interactive=interactive
            )
            if synthesis is None:
                synthesis_llm_failed = True
                open_points_extra = [*open_points_extra, NARRATIVE_PENDING_MARKER_HE]

        consistency_notes: list[str] = []
        exclusivity_notes: list[str] = []
        if synthesis is not None:
            consistency_notes = _scrub_consistency_violations(synthesis, dict(top_assignees))
            if consistency_notes:
                log.warning("patent_survey_consistency_violations", topic=topic, count=len(consistency_notes))
            exclusivity_notes = _scrub_exclusivity_claims(
                synthesis, coverage=assignee_coverage, missing=assignee_missing, total=assignee_total
            )
            if exclusivity_notes:
                log.warning("patent_survey_exclusivity_violations", topic=topic, count=len(exclusivity_notes))

        draft = (
            _build_draft_from_synthesis(
                synthesis, open_points_extra + consistency_notes + exclusivity_notes, assignee_has_cpc
            )
            if synthesis
            else _fallback_draft(
                open_points_extra + consistency_notes + exclusivity_notes,
                exec_summary_he=NARRATIVE_PENDING_MARKER_HE if synthesis_llm_failed else _NO_LLM_TEXT_HE,
            )
        )
        if assignee_coverage < _ASSIGNEE_COVERAGE_THRESHOLD:
            _enforce_coverage_caveat(draft, assignee_missing, assignee_total)

        # A14b point 4: per-patent "advance" description (problem/solution/novelty), reused for the
        # appendix table column below and the md/html footnote-style first-citation line.
        advance_map = generate_advance_descriptions(rows, role="light", interactive=interactive)
        advance_by_n = {
            it["n"]: advance_map.get(it["id"], _NO_ADVANCE_FALLBACK_HE) for it in patent_registry_entries
        }

        tables = [
            {
                "title_he": "נספח פטנטים",
                "headers": ["מספר", "כותרת EN", "מקצה", "CPC", "ציון ערך", "קישור", "התקדמות"],
                "rows": [
                    [
                        it["n"],
                        ltr_isolate(it["title"]),
                        ltr_join(it["assignees"]),
                        ltr_join(it["cpc"]),
                        it["value_score"] if it["value_score"] is not None else "—",
                        it.get("url") or "—",
                        advance_by_n.get(it["n"], _NO_ADVANCE_FALLBACK_HE),
                    ]
                    for it in patent_registry_entries
                ],
            },
            {
                "title_he": "בעלי פטנטים מובילים",
                "headers": ["בעלים", "מספר פטנטים"],
                "rows": [[ltr_isolate(a), n] for a, n in top_assignees.most_common(TOP_N_ASSIGNEES)],
            },
        ]
        # Round 5 P3/item 9: deterministic priority/confidence table alongside the cited narrative
        # section _business_action_sentences already renders (see business_implications_table's own
        # docstring) -- omitted honestly when there was no synthesis at all (fallback draft path).
        if synthesis is not None:
            biz_table = business_implications_table(synthesis.business_implications)
            if biz_table is not None:
                tables.append(biz_table)
        # Round 3 D8 finding 3 (2026-09-06): when there is genuinely no publication-year / CPC data
        # at all in the gathered sample (the common keyless-search-fallback case), the table is
        # replaced by an explicit disclosure section (never a silently-empty heading) -- rendered as
        # a plain draft section (the same structured-sentence path every other narrative section
        # uses) so it appears identically across docx/md/html without touching docx_builder.py, and
        # mirrored into open_points_he so it also surfaces in "נקודות פתוחות".
        if timeline:
            tables.append(
                {
                    "title_he": "ציר זמן שנתי",
                    "headers": ["שנה", "מספר פטנטים"],
                    "rows": [[y, n] for y, n in sorted(timeline.items())],
                }
            )
        else:
            draft.sections.append(
                _RenderSection(
                    title_he="ציר זמן שנתי", sentences=[_CiteSentence(text_he=_TIMELINE_DISCLOSURE_HE)]
                )
            )
            draft.open_points_he.append(_TIMELINE_DISCLOSURE_HE)
        if top_cpc:
            tables.append(
                {
                    "title_he": "קודי CPC מובילים",
                    "headers": ["קוד CPC", "מספר פטנטים"],
                    "rows": [[ltr_isolate(c), n] for c, n in top_cpc.most_common(TOP_N_CPC)],
                }
            )
        else:
            draft.sections.append(
                _RenderSection(
                    title_he="קודי CPC מובילים", sentences=[_CiteSentence(text_he=_CPC_DISCLOSURE_HE)]
                )
            )
            draft.open_points_he.append(_CPC_DISCLOSURE_HE)
        if white_spaces:
            tables.append(
                {
                    "title_he": "פערים (White Space): צירופי CPC x בעלים שאינם מכוסים",
                    "headers": ["קוד CPC", "בעלים מוביל"],
                    "rows": [[ltr_isolate(c), ltr_isolate(a)] for c, a in white_spaces],
                }
            )
        # Round 5 P5/item 6: the full CPC x assignee count matrix alongside the gap list above --
        # see _cpc_assignee_matrix's own docstring for why a "0" cell here is exactly one row of
        # the gap-list table, and why this is only ever rendered when both axes are non-empty.
        if cpc_assignee_matrix:
            tables.append(
                {
                    "title_he": "מטריצת CPC x מקצה (White Space)",
                    "headers": ["קוד CPC"] + [ltr_isolate(a) for a in matrix_ws_assignees],
                    "rows": [
                        [ltr_isolate(c), *(n if n else "—" for n in counts)]
                        for c, counts in zip(matrix_cpc, cpc_assignee_matrix, strict=True)
                    ],
                }
            )

        # A14b point 2: cluster table + cluster x assignee matrix.
        tables.append(
            {
                "title_he": "אשכולות טכנולוגיה",
                "headers": ["אשכול", "קוד CPC מוביל", "גודל", "קצב הגשה שנתי", "מקצה דומיננטי", "בגרות"],
                "rows": [
                    [
                        c.label_he,
                        ltr_isolate(c.cpc_codes[0]) if c.cpc_codes else "—",
                        c.size,
                        f"{c.filing_velocity_per_year:.1f}",
                        ltr_join(c.dominant_assignees),
                        f"{c.maturity_label_he} ({c.grant_ratio:.0%})",
                    ]
                    for c in clusters
                ],
            }
        )
        matrix_assignees = [a for a, _n in top_assignees.most_common(TOP_N_ASSIGNEES)]
        if matrix_assignees and clusters:
            tables.append(
                {
                    "title_he": "מטריצת אשכול x מקצה",
                    "headers": ["מקצה"] + [c.label_he for c in clusters],
                    "rows": [
                        [ltr_isolate(a)] + [c.assignees.get(a, 0) or "—" for c in clusters]
                        for a in matrix_assignees
                    ],
                }
            )
        if cross_links:
            tables.append(
                {
                    "title_he": "קשרים בין אשכולות (מקצים משותפים)",
                    "headers": ["אשכול א׳", "אשכול ב׳", "מקצים משותפים"],
                    "rows": [[a, b, ltr_join(shared)] for a, b, shared in cross_links],
                }
            )

        # A14b point 3: "מפת יחסים" -- co-assignment, shared patent families, and DB-derived
        # supplier/integrator/customer chains (all deterministic; the narrative in synthesis.
        # relationships is the LLM's cited prose over the same data).
        relationship_table_rows: list[list[Any]] = []
        for (a, b), n in co_assign.most_common(15):
            relationship_table_rows.append(
                [ltr_isolate(a), ltr_isolate(b), "מקצים משותפים (co-assignment)", f"{n} פטנט(ים)"]
            )
        for fam, ns in family_groups.items():
            relationship_table_rows.append(
                [ltr_isolate(fam), "—", "משפחת פטנט משותפת", ", ".join(f"[{n}]" for n in ns)]
            )
        for edge in relationship_edges:
            kind_he = _EVENT_KIND_HE.get(edge.get("kind"), edge.get("kind") or "אחר")
            note = edge.get("program") or edge.get("title") or "—"
            relationship_table_rows.append(
                [ltr_isolate(edge["from"]), ltr_isolate(edge["to"]), kind_he, ltr_isolate_if_latin(note)]
            )
        if relationship_table_rows:
            tables.append(
                {
                    "title_he": "מפת יחסים",
                    "headers": ["צד א׳", "צד ב׳", "סוג קשר", "פרטים"],
                    "rows": relationship_table_rows,
                }
            )

        # A14b point 6: per-patent timeline/expiry table + filing waves.
        timeline_headers = ["מספר", "עדיפות", "הגשה", "פרסום", "הענקה", "תפוגה משוערת (20 שנה)", "סטטוס"]
        tables.append(
            {
                "title_he": "ציר זמן פטנטים",
                "headers": timeline_headers,
                "rows": [
                    [
                        t.n,
                        fmt_date(t.priority_date),
                        fmt_date(t.filing_date),
                        fmt_date(t.publication_date),
                        fmt_date(t.grant_date),
                        fmt_date(t.expiry_date),
                        t.flag_he or "בתוקף",
                    ]
                    for t in timeline_rows
                ],
            }
        )
        if cluster_waves:
            tables.append(
                {
                    "title_he": "גלי הגשות לפי אשכול",
                    "headers": ["אשכול", "שנה", "מספר פטנטים"],
                    "rows": [
                        [key, y, n] for key, years in cluster_waves.items() for y, n in sorted(years.items())
                    ],
                }
            )
        if assignee_waves:
            tables.append(
                {
                    "title_he": "גלי הגשות לפי מקצה",
                    "headers": ["מקצה", "שנה", "מספר פטנטים"],
                    "rows": [
                        [ltr_isolate(key), y, n]
                        for key, years in assignee_waves.items()
                        for y, n in sorted(years.items())
                    ],
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
                "reliability": _appendix_reliability(it),
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
        # Round 5 P5/item 1: the "שיטה והיקף" box, spliced in right before the "תקציר מנהלים"
        # heading -- plain python-docx paragraph insertion into the Document build_docx already
        # returned (see eoa.patents.render.insert_section_before_summary_docx's own docstring for
        # why this can't be a build_docx-side hook without touching that file).
        insert_section_before_summary_docx(doc, METHODOLOGY_BOX_TITLE_HE, methodology_box_lines)
        # A14b point 6: docx table shading for the timeline table (python-docx cell shading --
        # matplotlib is not installed in this venv, so no embedded PNG; see eoa.patents.render's
        # own docstring). Never touches docx_builder.py -- plain post-processing of the Document it
        # already returned.
        timeline_table = find_table_by_headers(doc, timeline_headers)
        if timeline_table is not None:
            shade_timeline_table_rows(
                timeline_table,
                status_col_index=len(timeline_headers) - 1,
                status_colors={
                    cluster_mod.FLAG_EXPIRED_HE: "F4CCCC",
                    cluster_mod.FLAG_EXPIRING_SOON_HE: "FFF2CC",
                    cluster_mod.FLAG_PENDING_HE: "D9D9D9",
                },
            )
        docx_path, md_path, html_path = _report_paths(topic, period_end)
        save_docx(doc, docx_path)
        validate_docx(docx_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)

        md_content = render_markdown(
            draft, items_for_appendix, [], period_end=period_end, title_text=title_text, tables=tables
        )
        # Round 5 P5/item 1 (md-only splice, mirrors the docx insertion above).
        md_content = insert_section_before_md_summary(
            md_content, METHODOLOGY_BOX_TITLE_HE, methodology_box_lines
        )
        # A14b point 6 (md-only): a monospace ASCII/Unicode timeline bar chart, right before the
        # sources appendix.
        md_content = insert_section_before_md_appendix(
            md_content,
            "ציר זמן חזותי (ASCII)",
            "```\n" + ascii_timeline(dict(timeline)) + "\n```",
        )
        # A14b point 4 (md-only footnote): a short line right under the first body appearance of
        # each patent's "[n]" citation -- the docx appendix already carries the same text as its
        # own column instead (per the user's own spec).
        md_content = inject_advance_footnotes_md(md_content, advance_by_n)
        md_path.write_text(md_content, encoding="utf-8")

        html_content = render_html(
            draft,
            items_for_appendix,
            [],
            period_end=period_end,
            title_text=title_text,
            tables=tables,
            include_toc=True,
        )
        # Round 5 P5/item 1 (html-only splice, mirrors the docx insertion above).
        html_content = insert_section_before_html_summary(
            html_content, METHODOLOGY_BOX_TITLE_HE, methodology_box_lines
        )
        # A14b point 6 (html-only): a real, dependency-free inline SVG bar chart.
        html_content = insert_section_before_html_appendix(
            html_content,
            "ציר זמן חזותי",
            svg_timeline_bar_chart(dict(timeline), title_he="הגשות/פרסומים לפי שנה"),
        )
        html_content = inject_advance_footnotes_html(html_content, advance_by_n)
        html_path.write_text(html_content, encoding="utf-8")

        report_id = _persist_report(
            topic,
            period_end,
            docx_path,
            md_path,
            html_path,
            patent_ids,
            narrative_pending=synthesis_llm_failed,
        )
        _finish_survey_row(survey_id, status="done", report_id=report_id)
        log.info(
            "patent_survey_done",
            topic=topic,
            patent_count=len(patent_registry_entries),
            report_id=report_id,
        )
        return PatentSurveyPaths(
            docx=docx_path,
            md=md_path,
            html=html_path,
            report_id=report_id,
            survey_id=survey_id,
            topic=topic,
            patent_count=len(patent_registry_entries),
        )
    except Exception:
        _finish_survey_row(survey_id, status="failed", report_id=None)
        raise
