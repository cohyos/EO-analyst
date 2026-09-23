"""Stage: report/weekly — FR-5.4 / FR-4.3 / FR-11.4 / FR-12.5 weekly analyst report.

Pipeline: collect the week's red/orange items (+ a yellow-level domain-count summary, context
only) -> ``eoa.report.trends.detect_trends`` (no LLM) -> ``draft_weekly`` (resident model: the
daily report's structured, sentence-per-claim shape — see round-2 note below) ->
``qa_citations.check`` -> on failure, one corrective retry, then (if still failing) replace the
narrative with a deterministic, cited substitute synthesis built straight from the data (round 3,
2026-09-06 -- see ``_deterministic_fallback_draft``, mirrors ``eoa.report.daily``'s own two-failure
fallback) -> render docx/md/html reusing ``docx_builder``'s additive ``extra_sections``/``tables``
hooks for the trend prose, the FR-11.4 meta-summary (deterministic, not LLM-authored — see
``collect_meta_summary``), and the "לוח 90 הימים הקרובים" conference table -> insert a ``reports``
row (``kind='weekly'``).

Round-2 (2026-09-06): migrated ``WeeklyReportDraft`` from free-prose (``exec_summary_he``/
``sections[].prose_he``/``trend_paragraphs``) to the daily report's structured
``Sentence``-per-claim shape (goal 1) — two live rebuilds ran away to 24k- then 54k-char JSON and
hit EOF mid-string on the old free-prose schema. Also reduces the prompt-visible item list (see
:func:`select_items_for_prompt`) to keep the model's own output bounded regardless of how many
red/orange items the week actually produced.
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
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.reports import WeeklyReportDraft
from eoa.report import trends as trends_mod
from eoa.report.artifacts import versioned_paths
from eoa.report.claims_gate import apply_claims_gate, gate_deep_search_entries, gate_item_texts
from eoa.report.daily import (
    _append_event_corroboration_markers,
    _append_item_corroboration_markers,
    collect_deep_search,
    collect_events,
    collect_open_clarifications,
)
from eoa.report.docx_builder import (
    build_docx,
    fmt_amount,
    fmt_date,
    hebrew_date_str,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)
from eoa.report.qa_citations import QAResult, check, strip_so_what_phrases_from_draft
from eoa.report.redundancy import (
    apply_redundancy_pass,
    filter_facts_against_narrative,
    narrative_citation_numbers,
)
from eoa.report.style import apply_style_guard, dedupe_exact_sentences_across_sections
from eoa.report.textnorm import normalize_draft

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

WEEKLY_TITLE_TEXT = "דוח שבועי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_LEVELS_MAIN = ("red", "orange")

_TREND_KIND_LABELS_HE = {
    "entity_cluster": "ריכוז דיווחים",
    # CR-monthly.md item 1(a): "domain_surge" now means a genuine, baseline-backed rise (not the
    # old bare-floor "surge") -- and "domain_active" is the new, honestly-labelled "no baseline to
    # compare to" sibling (eoa.report.trends._domain_surges_from_counts).
    "domain_surge": "עלייה בתחום",
    "domain_active": "תחום פעיל",
    "market_convergence": "התכנסות שוק",
    "tech_race": "מירוץ טכנולוגי",
}

# round-2 (2026-09-06): input-side reduction to keep the model's own output bounded (see the
# module docstring) -- top N items per domain by score, in addition to every 'red' item and every
# item that only feeds a trend's evidence (both kept regardless of the per-domain cap).
_PROMPT_ITEMS_PER_DOMAIN = 6
_PROMPT_SUMMARY_TRUNC_CHARS = 500

# R6-weekly (docs/qa/loop/round_6_fixes.md, docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.2): the weekly
# report's per-item top-level headings (one per trend, one per Israel/tech/patents/BD table or
# prose block) are grouped under these five parent headings -- see
# ``eoa.report.docx_builder``'s ``group_he``/``domain_group_he`` support.
_TRENDS_GROUP_HE = "מגמות השבוע"
_DOMAIN_SECTIONS_GROUP_HE = "סקירה לפי תחום"
_ISRAEL_GROUP_HE = "תעשייה ישראלית"
_TECH_IP_GROUP_HE = "טכנולוגיה ו-IP"
_BD_GROUP_HE = "פיתוח עסקי"


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _week_range(period_end: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The 7-day window ending on ``period_end`` (default: today, Asia/Jerusalem), inclusive."""
    end = period_end or _today_jerusalem()
    start = end - dt.timedelta(days=6)
    return start, end


# --------------------------------------------------------------------------
# taxonomy label / grouping helpers (small, local copies of daily.py's private helpers so this
# module doesn't depend on daily.py's internals — see docs/CONVENTIONS.md "config, not code")
# --------------------------------------------------------------------------

#: Round-14 (CR-editing.md): matches ``eoa.report.daily._UNKNOWN_DOMAIN_LABEL_HE`` verbatim -- the
#: fallback Hebrew label for a ``domain``/pseudo-domain value with no real taxonomy entry, never
#: the raw slug itself.
_UNKNOWN_DOMAIN_LABEL_HE = "תחומים נוספים"


def _domain_label(domain: str | None) -> str:
    """Round-14 (CR-editing.md, "headings that are English keys or taxonomy slugs"): never falls
    back to the raw ``domain`` string itself -- ``out_of_scope``/``archive`` (and any other
    non-taxonomy pseudo-domain value that reaches this report layer) leaked straight into a Hebrew
    heading (e.g. "...בתחום out_of_scope", seen live in the weekly trend-delta list). Mirrors
    ``eoa.report.daily``'s own ``_domain_label`` fix for the same bug (Q3-15)."""
    if not domain:
        return "כללי"
    domains = settings().taxonomy.get("domains", {})
    entry = domains.get(domain, {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else _UNKNOWN_DOMAIN_LABEL_HE


def _level_label(level: str | None) -> str:
    levels = settings().taxonomy.get("triage_levels", {})
    entry = levels.get(level or "", {})
    emoji = entry.get("emoji", "")
    label = entry.get("label", level or "")
    return f"{emoji} {label}".strip()


def _group_by_domain(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    order = list(settings().taxonomy.get("domains", {}).keys())
    buckets: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        buckets.setdefault(it.get("domain") or "secondary", []).append(it)
    ordered = [d for d in order if d in buckets] + [d for d in buckets if d not in order]
    return [(d, buckets[d]) for d in ordered]


def _normalize_section_titles(draft: WeeklyReportDraft | Any) -> WeeklyReportDraft | Any:
    """Force every section's ``title_he`` to the authoritative taxonomy label for its ``domain``
    (F7) — see ``eoa.report.daily._normalize_section_titles`` for why: a domain heading containing
    an embedded literal ``"`` (e.g. 'נגד כטב"מים (C-UAS)') can come back truncated from the model's
    JSON output, so the deterministic taxonomy label is used instead of trusting the model's copy.
    Duck-typed (only touches ``draft.sections``), so ``eoa.report.monthly`` reuses this unchanged
    for ``MonthlyReportDraft``."""
    new_sections = []
    for section in draft.sections:
        domain = canonical_domain_key(section.domain) if section.domain else section.domain
        if domain == "out_of_scope":
            # weekly 2026-09-06 (cloud draft): the model produced an "out_of_scope" section --
            # never a report section; its items are, by definition, not the week's news.
            continue
        if domain:
            section = section.model_copy(update={"domain": domain, "title_he": _domain_label(domain)})
        new_sections.append(section)
    return draft.model_copy(update={"sections": new_sections})


#: weekly 2026-09-06 (first cloud-drafted weekly): the model wrote section ``domain`` keys of its
#: own ("land_eoir", "naval_eoir", "cuas") instead of the taxonomy keys, so the headings rendered
#: as raw identifiers. Map the common paraphrases onto the taxonomy; anything else falls back to
#: the model's key unchanged (its label then prints the key, as before).
_DOMAIN_KEY_ALIASES = {
    "land_eoir": "land_surveillance",
    "land": "land_surveillance",
    "ground_surveillance": "land_surveillance",
    "naval_eoir": "naval_surveillance",
    "naval": "naval_surveillance",
    "maritime_surveillance": "naval_surveillance",
    "cuas": "c_uas",
    "c-uas": "c_uas",
    "counter_uas": "c_uas",
    "counter-uas": "c_uas",
    "cv": "computer_vision",
    "computer-vision": "computer_vision",
    "ai_cv": "computer_vision",
    "airborne": "airborne_pods",
    "pods": "airborne_pods",
    "targeting_pods": "airborne_pods",
    "air-defense": "air_defense",
    "air_defence": "air_defense",
    "missile_defense": "air_defense",
    "technology": "tech_dev",
    "tech": "tech_dev",
}


def canonical_domain_key(domain: str | None) -> str | None:
    """A taxonomy domain key for ``domain`` -- exact key, a known alias, or the input unchanged."""
    if not domain:
        return domain
    key = domain.strip().lower()
    if key in settings().taxonomy.get("domains", {}):
        return key
    return _DOMAIN_KEY_ALIASES.get(
        key,
        key.replace("-", "_") if key.replace("-", "_") in settings().taxonomy.get("domains", {}) else domain,
    )


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_week_items(
    period_start: dt.date, period_end: dt.date, max_items: int | None = None
) -> list[dict[str, Any]]:
    """All red/orange items in the week, ordered by score desc, each carrying a stable 1-based
    ``n``. Capped at ``config.triage.daily_report_max_items * 7`` by default (a week's worth of
    daily caps) — unlike the daily report there is no yellow-level fallback here; yellow items get
    a domain-count summary instead (:func:`collect_yellow_domain_summary`)."""
    cap = max_items or settings().triage.daily_report_max_items * 7
    sql = """
        SELECT i.id, i.url, i.title, i.domain, i.subdomain, i.published_at, i.level, i.score,
               i.summary_he, i.so_what_he, i.report_kind, i.geography, i.trl, i.story_id, i.lang,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.security_status = 'clean'
          AND i.dedup_of IS NULL
          AND i.level = ANY(%(levels)s)
          AND (COALESCE(i.published_at, i.created_at) AT TIME ZONE 'Asia/Jerusalem')::date
              BETWEEN %(start)s AND %(end)s
        ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql, {"levels": list(_LEVELS_MAIN), "start": period_start, "end": period_end, "limit": cap}
        )
        rows = cur.fetchall()
    for row in rows:
        row.setdefault("key_facts", [])
    for idx, row in enumerate(rows, start=1):
        row["n"] = idx
    # Cross-source corroboration (2026-09-07): same marker convention as
    # `eoa.report.daily.collect_items` -- see that module's note for why `title` and not a new
    # column/field.
    _append_item_corroboration_markers(rows)
    log.info("weekly_items_collected", count=len(rows), start=str(period_start), end=str(period_end))
    return rows


def collect_yellow_domain_summary(period_start: dt.date, period_end: dt.date) -> list[dict[str, Any]]:
    """Background-level (yellow) item counts per domain for the week — a summary only, not full
    items (per FR-5.4: "דוח שבועי: ניתוח מגמות")."""
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND level = 'yellow'
          AND (COALESCE(published_at, created_at) AT TIME ZONE 'Asia/Jerusalem')::date
              BETWEEN %(start)s AND %(end)s
        GROUP BY domain
        ORDER BY n DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": period_start, "end": period_end})
        return cur.fetchall()


def collect_meta_summary(period_start: dt.date, period_end: dt.date) -> dict[str, Any]:
    """FR-11.4 hook: meta lessons (``lessons.kind='meta'``) added this week, plus
    ``triage_feedback`` rows where the user's level disagreed with the agent's (a calibration
    delta) — the raw material for the "מה השתנה בעקבות המשוב" transparency loop. Deliberately
    **not** fed to the LLM: it is rendered as a deterministic prose block
    (:func:`format_meta_summary_he`) via ``docx_builder``'s ``extra_sections`` hook, since none of
    it can carry an ``[n]`` citation into the item list and rule 4 ("Never invent") rules out asking
    the model to narrate ungrounded numbers."""
    sql_lessons = """
        SELECT id, text, source_ref, created_at
        FROM lessons
        WHERE kind = 'meta'
          AND (created_at AT TIME ZONE 'Asia/Jerusalem')::date BETWEEN %(start)s AND %(end)s
        ORDER BY created_at
    """
    sql_feedback = """
        SELECT tf.id, tf.item_id, tf.user_level, tf.agent_level, tf.comment, tf.created_at,
               i.title AS item_title
        FROM triage_feedback tf
        LEFT JOIN items i ON i.id = tf.item_id
        WHERE (tf.created_at AT TIME ZONE 'Asia/Jerusalem')::date BETWEEN %(start)s AND %(end)s
        ORDER BY tf.created_at
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_lessons, {"start": period_start, "end": period_end})
        lessons = cur.fetchall()
        cur.execute(sql_feedback, {"start": period_start, "end": period_end})
        feedback = cur.fetchall()
    deltas = net_feedback_deltas(feedback)
    return {"lessons": lessons, "feedback_total": len(feedback), "feedback_deltas": deltas}


#: Weekly 2026-09-06 (report 51): the meta section listed ~170 lines because every feedback row
#: was printed, including the e2e suite's rate-then-restore pairs (red←yellow, yellow←red, …).
#: Only the *net* change per item counts as a calibration, and the section is capped.
_META_MAX_DELTAS = 20


def net_feedback_deltas(feedback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per item: the first agent level seen in the window vs the user's *last* level;
    items whose feedback nets to no change (toggled and restored) are dropped; newest first,
    capped at :data:`_META_MAX_DELTAS`."""
    first_agent: dict[Any, str] = {}
    last: dict[Any, dict[str, Any]] = {}
    for f in feedback:  # ordered by created_at
        if not (f.get("user_level") and f.get("agent_level")):
            continue
        key = f.get("item_id") or f.get("id")
        first_agent.setdefault(key, f["agent_level"])
        last[key] = f
    out: list[dict[str, Any]] = []
    for key, f in last.items():
        if f["user_level"] == first_agent[key]:
            continue  # net zero
        out.append({**f, "agent_level": first_agent[key]})
    out.sort(key=lambda d: d.get("created_at") or 0, reverse=True)
    return out[:_META_MAX_DELTAS]


def format_meta_summary_he(meta: dict[str, Any]) -> str:
    """Render :func:`collect_meta_summary`'s output as deterministic Hebrew prose (FR-11.4)."""
    lines: list[str] = []
    if meta.get("lessons"):
        lines.append("תובנות מטא שנוספו השבוע:")
        lines += [f"- {lesson.get('text') or ''}" for lesson in meta["lessons"]]
    deltas = meta.get("feedback_deltas") or []
    if deltas:
        lines.append("כיולים שבוצעו בעקבות משוב המשתמש:")
        for d in deltas:
            subject = d.get("item_title") or f"פריט #{d.get('item_id')}"
            lines.append(f'- "{subject}": {d.get("agent_level")} ← {d.get("user_level")} (הכרעת המשתמש)')
    total = meta.get("feedback_total") or 0
    lines.append(f'סה"כ פריטי משוב שהתקבלו השבוע: {total}.')
    if not lines:
        return "לא נרשמו תובנות מטא או כיולי משוב חדשים השבוע."
    return "\n".join(lines)


def upcoming_conferences(days: int = 90) -> list[dict[str, Any]]:
    """FR-12.5: "לוח 90 הימים הקרובים". Lazily imports the concurrently-developed
    ``eoa.conferences`` package; falls back to ``[]`` (no calendar table rendered) if it isn't
    built yet or the call fails — a missing conference calendar never blocks the weekly report."""
    try:
        from eoa.conferences.tracker import upcoming
    except ImportError:
        return []
    try:
        return upcoming(days) or []
    except Exception as exc:
        log.warning("conferences_upcoming_failed", error=str(exc)[:200])
        return []


# --------------------------------------------------------------------------
# citation registry
# --------------------------------------------------------------------------


def _extend_registry_with_events(
    items: list[dict[str, Any]], events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Same numbering-extension convention as ``daily._extend_citation_registry``: append any event
    source item not already numbered, so the events table's מקור[n] stays in sync."""
    citation_items = list(items)
    by_item_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    events_out: list[dict[str, Any]] = []
    for ev in events:
        item_id = ev.get("item_id")
        entry = by_item_id.get(item_id) if item_id is not None else None
        if entry is None and item_id is not None:
            entry = {
                "id": item_id,
                "n": next_n,
                "title": ev.get("item_title"),
                "source_name": ev.get("source_name"),
                "url": ev.get("item_url"),
                "published_at": ev.get("published_at"),
            }
            citation_items.append(entry)
            by_item_id[item_id] = entry
            next_n += 1
        ev_out = dict(ev)
        ev_out["n"] = entry.get("n") if entry else None
        events_out.append(ev_out)
    return citation_items, events_out


def _extend_registry_with_ids(items: list[dict[str, Any]], item_ids: set[int]) -> list[dict[str, Any]]:
    """Append any of ``item_ids`` (typically trend evidence) not already numbered, fetched fresh
    from the DB, so a trend paragraph can cite evidence even when it falls outside the red/orange
    item list (e.g. a yellow item that fed an entity cluster)."""
    citation_items = list(items)
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    missing = sorted(iid for iid in item_ids if iid not in by_id)
    if not missing:
        return citation_items
    sql = """
        SELECT i.id, i.url, i.title, i.published_at, COALESCE(src.name, i.url) AS source_name
        FROM items i LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.id = ANY(%(ids)s)
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"ids": missing})
        fetched = {r["id"]: r for r in cur.fetchall()}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for iid in missing:
        row = fetched.get(iid)
        if row is None:
            continue
        row = dict(row)
        row["n"] = next_n
        citation_items.append(row)
        by_id[iid] = row
        next_n += 1
    return citation_items


# --------------------------------------------------------------------------
# prompt formatting
# --------------------------------------------------------------------------


def format_items_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for domain, group in _group_by_domain(items):
        lines.append(f"### {_domain_label(domain)}")
        for it in group:
            lines.append(
                f"[{it['n']}] כותרת: {it.get('title') or '—'} | מקור: "
                f"{it.get('source_name') or it.get('url') or '—'} | תאריך: {fmt_date(it.get('published_at'))} "
                f"| רמה: {_level_label(it.get('level'))}"
            )
            lines.append(f"תקציר: {it.get('summary_he') or '—'}")
            lines.append(f"מה זה אומר: {it.get('so_what_he') or '—'}")
            lines.append("")
    return "\n".join(lines)


def format_yellow_summary_block(yellow_summary: list[dict[str, Any]]) -> str:
    if not yellow_summary:
        return "אין פריטי רקע (yellow) נוספים בתקופה זו."
    return "\n".join(f"- {_domain_label(row['domain'])}: {row['n']} פריטים" for row in yellow_summary)


def _format_trend_numbers_he(t: dict[str, Any]) -> str:
    """CR-monthly.md item 1(e): every trend dict carries ``n_items``/``n_sources``/``baseline_avg``/
    ``ratio`` -- surfaced here so the drafting prompt always shows the model the real numbers behind
    a trend, instead of leaving it to infer "how big" a trend is from the title text alone."""
    n_items = t.get("n_items")
    n_sources = t.get("n_sources")
    parts = []
    if n_items is not None:
        parts.append(f"{n_items} פריטים")
    if n_sources is not None:
        parts.append(f"{n_sources} מקורות")
    baseline_avg = t.get("baseline_avg")
    ratio = t.get("ratio")
    if baseline_avg:
        parts.append(f"ממוצע בסיס {baseline_avg:g}/שבוע")
    if ratio:
        parts.append(f"יחס {ratio:.1f}x")
    return ", ".join(parts) or "—"


def format_trends_block(trend_list: list[dict[str, Any]], id_to_n: dict[int, int]) -> str:
    if not trend_list:
        return "לא זוהו מגמות רוחב מובהקות בתקופה זו."
    lines: list[str] = []
    for i, t in enumerate(trend_list, start=1):
        ns = sorted({id_to_n[iid] for iid in t.get("evidence_item_ids", []) if iid in id_to_n})
        refs = "".join(f"[{n}]" for n in ns) or "—"
        entities = ", ".join(t.get("entities") or []) or "—"
        lines.append(
            f"{i}. [{_TREND_KIND_LABELS_HE.get(t['kind'], t['kind'])}] {t['title_he']} | "
            f"חוזק: {t['strength']}/5 | נתונים: {_format_trend_numbers_he(t)} | ישויות: {entities} | "
            f"ראיות: {refs}"
        )
    return "\n".join(lines)


def _normalize_trend_title(title: str | None) -> str:
    return " ".join((title or "").split()).strip().casefold()


def strip_trend_out_of_scope_sentences(
    trend_sections: list[Any],
    trend_list: list[dict[str, Any]],
    id_to_n: dict[int, int],
    *,
    report_kind: str,
) -> tuple[list[Any], int]:
    """CR-monthly.md item 2 (last sentence): a trend section may cite ONLY the ids that were given
    to the model as THAT trend's own evidence (``detect_trends``'s own ``evidence_item_ids``) — not
    another trend's, and not an item outside any trend's evidence at all. Matches each drafted
    trend section back to its ``detect_trends`` source entry by normalized ``title_he`` (the model
    is instructed to echo it, possibly rephrased but not renumbered); when no match is found (a
    rare paraphrase miss), the section is left untouched rather than stripped wholesale — under-
    enforcing on a title-match miss is safer than deleting a section's entire narrative.

    Removed sentences are logged as ``f"{report_kind}.trend_sentence_out_of_scope"`` (per the task,
    never silently dropped) and returned as a count so the caller can persist it for the D6
    ``trend section cites non-member item`` QA check."""
    by_title = {_normalize_trend_title(t.get("title_he")): t for t in trend_list}
    n_dropped = 0
    out: list[Any] = []
    for sec in trend_sections:
        sentences = getattr(sec, "sentences", None)
        match = by_title.get(_normalize_trend_title(getattr(sec, "title_he", None)))
        if match is None or not sentences:
            out.append(sec)
            continue
        allowed_ns = {id_to_n[i] for i in match.get("evidence_item_ids", []) if i in id_to_n}
        if not allowed_ns:
            out.append(sec)
            continue
        kept = []
        for s in sentences:
            if not set(s.cites) <= allowed_ns:
                n_dropped += 1
                log.info(
                    f"{report_kind}.trend_sentence_out_of_scope",
                    title_he=getattr(sec, "title_he", None),
                    cites=s.cites,
                    allowed=sorted(allowed_ns),
                )
                continue
            kept.append(s)
        out.append(sec.model_copy(update={"sentences": kept}))
    return out, n_dropped


def _truncate_prompt_text(text: str | None, limit: int = _PROMPT_SUMMARY_TRUNC_CHARS) -> str | None:
    if not text or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def select_items_for_prompt(
    items: list[dict[str, Any]],
    evidence_item_ids: set[int] | None = None,
    *,
    per_domain: int = _PROMPT_ITEMS_PER_DOMAIN,
) -> list[dict[str, Any]]:
    """Round-2 (2026-09-06): the full week's red/orange item list (``items``, still the citation
    registry unchanged — ``n`` numbering and ``check()``'s valid range both stay keyed on the full
    list) can run into the hundreds; feeding all of it into the prompt is what produced the 24k-
    then 54k-char runaway JSON that motivated this migration. Returns a reduced, still
    score-ordered subset actually shown to the model: every ``level == 'red'`` item, the top
    ``per_domain`` items per domain by score (``items`` already arrives score-desc from
    ``collect_week_items``, so a plain per-domain slice keeps that order), and any item that only
    feeds a trend's evidence (``evidence_item_ids``) — each returned item is a shallow copy with
    ``summary_he``/``so_what_he`` truncated to ~500 chars (the per-item prompt line, not the
    persisted row) as a second, independent lever on output size."""
    evidence_item_ids = evidence_item_ids or set()
    keep_ids: set[int] = set()
    for _domain, group in _group_by_domain(items):
        for it in group[:per_domain]:
            if it.get("id") is not None:
                keep_ids.add(it["id"])
    for it in items:
        item_id = it.get("id")
        if item_id is None:
            continue
        if it.get("level") == "red" or item_id in evidence_item_ids:
            keep_ids.add(item_id)
    selected = []
    for it in items:
        if it.get("id") not in keep_ids:
            continue
        reduced = dict(it)
        reduced["summary_he"] = _truncate_prompt_text(it.get("summary_he"))
        reduced["so_what_he"] = _truncate_prompt_text(it.get("so_what_he"))
        selected.append(reduced)
    return selected


def _render_trend_sentences(sentences: list[Sentence]) -> str:
    """Flatten a :class:`WeeklyTrendSection`'s structured ``Sentence`` list to one prose string
    with "[n]" markers appended deterministically from each sentence's ``cites`` — a small local
    copy of ``eoa.report.docx_builder``'s own (private) sentence-rendering helper, needed here
    because the trend prose is rendered into the document via the ``extra_sections`` hook (a plain
    string, not a list of ``Sentence`` objects) rather than through ``draft.sections``'s own
    duck-typed structured rendering."""
    parts: list[str] = []
    for sentence in sentences:
        text = sentence.text_he.rstrip()
        markers = "".join(f"[{n}]" for n in sentence.cites)
        parts.append(f"{text} {markers}".rstrip() if markers else text)
    return " ".join(parts)


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> WeeklyReportDraft:
    return WeeklyReportDraft(
        exec_summary=[],
        trends=[],
        sections=[],
        system_note_he=("לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange. אין ממצאים לדיווח השבועי."),
        analyst_note_he=None,
        outlook=[],
        open_points_he=[],
    )


def _qa_failed_twice_draft() -> WeeklyReportDraft:
    """Kept only for the items-somehow-empty edge case (should not happen in practice, mirroring
    ``eoa.report.daily._qa_failed_twice_draft``'s own note). The normal two-failure fallback since
    round 3 (2026-09-06) is :func:`_deterministic_fallback_draft`."""
    return WeeklyReportDraft(
        exec_summary=[],
        trends=[],
        sections=[],
        system_note_he=(
            "הטיוטה הטקסטואלית של הדוח השבועי לא עברה את בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן "
            "הושמטה במלואה מדוח זה כדי לא להציג ניסוח חלקי או לא מאומת. הטבלאות הדטרמיניסטיות "
            "(אירועים, מכרזים, מעקב טכנולוגי, תעשייה ישראלית, פטנטים, לוח כנסים) ונספח המקורות "
            "שלהלן אינם מושפעים ומוצגים במלואם."
        ),
        analyst_note_he=None,
        outlook=[],
        open_points_he=[],
    )


# --------------------------------------------------------------------------
# round-3 (2026-09-06, D6 judge finding 1): deterministic substitute synthesis, mirroring
# eoa.report.daily's own (see that module for the detailed rationale) -- small local copies of the
# helpers per this package's established "no cross-module private-name imports" convention (see
# e.g. this module's own ``_domain_label``/``_level_label`` above).
# --------------------------------------------------------------------------

_FALLBACK_TOP_ITEMS = 6
_FALLBACK_TOP_EVENTS = 5
_FALLBACK_TOP_ISRAEL_ITEMS = 5
_FALLBACK_TEXT_TRUNC_CHARS = 220

_EVENT_KIND_LABELS_HE_FALLBACK = {
    "contract_award": "זכייה בחוזה",
    "m_and_a": "מיזוג/רכישה",
    "partnership": "שותפות",
    "investment": "השקעה",
    "launch": "השקה",
    "test": "ניסוי",
    "deployment": "פריסה",
    "regulation": "רגולציה",
    "other": "אחר",
}


def _fallback_event_sort_key(ev: dict[str, Any]) -> tuple[dt.date, float]:
    date = ev.get("date") or dt.date.min
    amount = ev.get("amount_usd")
    try:
        amount_val = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        amount_val = 0.0
    return (date, amount_val)


def _extend_registry_with_rows(citation_items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """Mutate ``citation_items`` in place, appending any ``rows`` entry not already present by id,
    and stamping ``row["n"]`` -- same convention as :func:`_extend_registry_with_events`. Used by
    :func:`_deterministic_fallback_draft` to fold Israel-relevant items into the registry before
    ``weekly_israel_tables`` runs its own (idempotent) extension of the same list later."""
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for row in rows:
        rid = row.get("id")
        if rid is None:
            continue
        entry = by_id.get(rid)
        if entry is None:
            entry = {
                "id": rid,
                "n": next_n,
                "title": row.get("title"),
                "source_name": row.get("source_name"),
                "url": row.get("url"),
                "published_at": row.get("published_at"),
            }
            citation_items.append(entry)
            by_id[rid] = entry
            next_n += 1
        row["n"] = entry["n"]


def _fallback_truncate(text: str | None, limit: int = _FALLBACK_TEXT_TRUNC_CHARS) -> str:
    text = (text or "").strip()
    if not text:
        return "—"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fallback_top_item_sentences(items: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    """W9 (round 4, docs/qa/loop/round_4_fixes.md): items that are really the same underlying story
    covered by more than one outlet (shared ``dedup_of``, or a near-identical title -- see
    ``eoa.report.clustering``) are folded into one sentence citing the richest item plus every
    other outlet's own registry number, with a "(+N מקורות נוספים)" note."""
    from eoa.report.clustering import cluster_extra_ns, cluster_items, extra_sources_note_he

    clusters = cluster_items(items)
    sentences: list[Sentence] = []
    for cluster in clusters[:limit]:
        it = cluster.primary
        n = it.get("n")
        if n is None:
            continue
        level = _level_label(it.get("level"))
        title = it.get("title") or "—"
        so_what = _fallback_truncate(it.get("so_what_he") or it.get("summary_he"))
        extra_ns = cluster_extra_ns(cluster)
        note = extra_sources_note_he(cluster) if extra_ns else ""
        sentences.append(Sentence(text_he=f"{title} ({level}): {so_what}{note}", cites=[int(n), *extra_ns]))
    return sentences


def _fallback_event_sentences(events_with_n: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    ranked = sorted(
        (ev for ev in events_with_n if ev.get("n") is not None),
        key=_fallback_event_sort_key,
        reverse=True,
    )
    sentences: list[Sentence] = []
    for ev in ranked[:limit]:
        kind_label = _EVENT_KIND_LABELS_HE_FALLBACK.get(ev.get("kind"), ev.get("kind") or "אחר")
        parties = ", ".join(ev.get("parties") or []) or "—"
        customer_program = ev.get("customer") or ev.get("program") or "—"
        sentences.append(
            Sentence(
                text_he=(
                    f"{kind_label}: {parties} — {customer_program}, {fmt_amount(ev)}, "
                    f"בתאריך {fmt_date(ev.get('date'))}."
                ),
                cites=[int(ev["n"])],
            )
        )
    return sentences


def _fallback_israel_item_sentences(israel_items: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    sentences: list[Sentence] = []
    for it in israel_items[:limit]:
        n = it.get("n")
        if n is None:
            continue
        title = it.get("title") or "—"
        so_what = _fallback_truncate(it.get("so_what_he") or it.get("summary_he"))
        sentences.append(Sentence(text_he=f"רלוונטיות ישראלית — {title}: {so_what}", cites=[int(n)]))
    return sentences


def _deterministic_fallback_draft(
    items: list[dict[str, Any]],
    events_with_n: list[dict[str, Any]],
    israel_items: list[dict[str, Any]],
) -> WeeklyReportDraft:
    """Round 3 (2026-09-06, D6 judge finding 1), mirrors
    ``eoa.report.daily._deterministic_fallback_draft``: a deterministic (no LLM) substitute
    executive summary built straight from the week's already-numbered data -- the top red/orange
    items, the week's notable business events, and the Israel-relevant items -- used when the
    LLM-drafted narrative still fails citation QA after one corrective retry. Every sentence cites
    a real, already-registered item ``n``, so this cannot itself fail
    :func:`eoa.report.qa_citations.check` (the caller still runs it once anyway, defensively -- see
    ``build_weekly``).

    Round 5 P3: ``bluf`` stays at its default ``[]`` -- see
    ``eoa.report.daily._deterministic_fallback_draft``'s own docstring for why (P4's
    ``docx_builder._draft_bluf_info`` already synthesizes a labelled BLUF from this exact shape)."""
    sentences: list[Sentence] = []
    sentences.extend(_fallback_top_item_sentences(items, limit=_FALLBACK_TOP_ITEMS))
    sentences.extend(_fallback_event_sentences(events_with_n, limit=_FALLBACK_TOP_EVENTS))
    sentences.extend(_fallback_israel_item_sentences(israel_items, limit=_FALLBACK_TOP_ISRAEL_ITEMS))
    return WeeklyReportDraft(
        exec_summary=sentences,
        trends=[],
        sections=[],
        system_note_he=(
            "תקציר מובנה אוטומטית (ללא ניסוח מודל): הטיוטה הטקסטואלית של הדוח השבועי לא עברה את "
            "בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן ניסוח המודל הושמט במלואו. התקציר שלעיל הופק "
            "ישירות מנתוני מסד הנתונים (ללא ניסוח חופשי של מודל), ומכיל את הפריטים המובילים, "
            "האירועים העסקיים הבולטים והפריטים הרלוונטיים לתעשייה הישראלית לשבוע זה — כל משפט כאן "
            "מצוטט למקורו. הטבלאות הדטרמיניסטיות (אירועים, מכרזים, מעקב טכנולוגי, תעשייה ישראלית, "
            "פטנטים, לוח כנסים) ונספח המקורות שלהלן אינם מושפעים ומוצגים במלואם."
        ),
        analyst_note_he=None,
        outlook=[],
        open_points_he=[],
    )


def draft_weekly(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    *,
    evidence_item_ids: set[int] | None = None,
    role: str = "resident",
    interactive: bool = False,
    period_end: dt.date | None = None,
) -> WeeklyReportDraft:
    """Draft the ``WeeklyReportDraft`` via the resident model; zero items skip the LLM call.

    ``items`` stays the full citation registry (unchanged ``n`` numbering); only the prompt-visible
    item list is reduced (:func:`select_items_for_prompt`) — round-2, see the module docstring.

    F34: ``period_end`` is the report's own resolved period (``build_weekly``'s ``end``), used for
    the prompt's ``{date_he}`` header instead of today -- a historical rebuild must show its own
    date, not the date the rebuild happens to run on. Defaults to today (Asia/Jerusalem).
    """
    if not items:
        return _no_items_draft()
    prompt_items = select_items_for_prompt(items, evidence_item_ids)
    prompt = render(
        "report_weekly",
        date_he=hebrew_date_str(period_end or _today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(prompt_items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    draft = chat_structured(
        role,
        WeeklyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        # 2026-09-06 (round-2): the weekly draft used to run away to 24k- then 54k-char JSON (EOF
        # mid-string) over the old free-prose schema; the structured schema plus the prompt-item
        # reduction above keep the output bounded, but num_predict stays generous per the task.
        options={"temperature": 0.3, "num_predict": 14000},
    )
    return _normalize_section_titles(draft)


def _corrective_retry(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    draft: WeeklyReportDraft,
    qa: QAResult,
    *,
    evidence_item_ids: set[int] | None = None,
    role: str,
    interactive: bool,
    period_end: dt.date | None = None,
) -> WeeklyReportDraft:
    prompt_items = select_items_for_prompt(items, evidence_item_ids)
    prompt = render(
        "report_weekly",
        date_he=hebrew_date_str(period_end or _today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(prompt_items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        'ברשימת הפריטים או ברשימת המגמות. שים לב: אסור לכתוב "[n]" בטקסט עצמו -- מספרי ההפניה '
        "שייכים אך ורק לשדה cites של כל משפט:\n" + errors_text
    )
    draft = chat_structured(
        role,
        WeeklyReportDraft,
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
    return _normalize_section_titles(draft)


# --------------------------------------------------------------------------
# persistence / paths
# --------------------------------------------------------------------------


def _report_path(period_end: dt.date, ext: str) -> Path:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    return out_dir / f"weekly_{period_end.isoformat()}.{ext}"


def _persist_report(
    period_start: dt.date,
    period_end: dt.date,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    items: list[dict[str, Any]],
    qa: QAResult,
    *,
    report_state: dict[str, Any] | None = None,
    extra_qa_fields: dict[str, Any] | None = None,
) -> int:
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
        # CR-monthly.md items 2/3/5 (D6 "unsupported intensifier"/"trend cites non-member item"
        # checks): trend_sentences_out_of_scope / claims_gate_softened / claims_gate_dropped counts,
        # merged in by the caller -- see eoa.qa.d6_daily_report._trend_membership_check.
        **(extra_qa_fields or {}),
    }
    item_ids = [it["id"] for it in items if it.get("id") is not None]
    # Round 5 P2: `report_state` is the raw material `eoa.report.deltas.previous_report_state`
    # reads back for the *next* weekly report's delta (items + trend strengths) -- see
    # `eoa.report.deltas.build_report_state`.
    sql = """
        INSERT INTO reports (kind, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report, report_state)
        VALUES ('weekly', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s,
                %(qa_report)s, %(report_state)s)
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
                "report_state": Json(report_state) if report_state is not None else None,
            },
        )
        report_id: int = cur.fetchone()["id"]
    log.info("weekly_report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_weekly(
    period_end: dt.date | None = None, *, role: str = "resident", interactive: bool = False
) -> ReportPaths:
    """Collect -> detect trends -> draft -> QA-gate -> render docx/md/html -> persist."""
    start, end = _week_range(period_end)

    items = collect_week_items(start, end)
    items = gate_item_texts(items)  # claims gate on quoted item text (lead, 2026-09-08)
    yellow_summary = collect_yellow_domain_summary(start, end)
    events = collect_events(start, end, limit=40)  # F9/F16: cap the weekly events table at 40 rows
    deep_search = collect_deep_search(start, end)
    deep_search = gate_deep_search_entries(deep_search)
    open_clarifications = collect_open_clarifications()
    trend_list = trends_mod.detect_trends((start, end))
    meta = collect_meta_summary(start, end)
    conferences_90 = upcoming_conferences(90)

    all_evidence_ids = {iid for t in trend_list for iid in t.get("evidence_item_ids", [])}
    citation_items = _extend_registry_with_ids(items, all_evidence_ids)
    citation_items, events_with_n = _extend_registry_with_events(citation_items, events)
    id_to_n = {it["id"]: it["n"] for it in citation_items if it.get("id") is not None}
    trends_block = format_trends_block(trend_list, id_to_n)

    draft = draft_weekly(
        items,
        yellow_summary,
        trends_block,
        evidence_item_ids=all_evidence_ids,
        role=role,
        interactive=interactive,
        period_end=end,
    )
    # round-2 (2026-09-06): draft.trends' cites/duplicates are validated directly by
    # qa_citations._check_structured (WeeklyReportDraft is now the structured shape) -- no
    # extra_sections needed here any more (that hook stays reserved for the legacy free-prose
    # monthly/bd_territory drafts).
    qa = check(draft, citation_items)

    if not qa.passed and items:
        log.warning("weekly_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(
            items,
            yellow_summary,
            trends_block,
            draft,
            qa,
            evidence_item_ids=all_evidence_ids,
            role=role,
            interactive=interactive,
            period_end=end,
        )
        qa = check(draft, citation_items)

    if not qa.passed and items:
        # Round 3 (2026-09-06, D6 judge finding 1), mirrors eoa.report.daily: two failures
        # (initial draft + one corrective retry) replace the narrative with a deterministic, cited
        # substitute synthesis built straight from the data -- see `_deterministic_fallback_draft`.
        # The original QA errors are kept in `qa_report` (persisted below) for the analyst to
        # review; `qa.passed` stays False either way.
        log.error("weekly_qa_failed_twice_using_deterministic_fallback", errors=qa.errors[:10])
        original_errors = qa
        israel_items: list[dict[str, Any]] = []
        try:
            from eoa.report.israel_section import collect_israel_items

            week_start_ts = dt.datetime.combine(start, dt.time.min, tzinfo=JERUSALEM).astimezone(dt.UTC)
            week_end_ts = dt.datetime.combine(end, dt.time.max, tzinfo=JERUSALEM).astimezone(dt.UTC)
            israel_items = collect_israel_items(week_start_ts, week_end_ts)
            _extend_registry_with_rows(citation_items, israel_items)
        except Exception as exc:
            log.warning("weekly_report_fallback_israel_collect_failed", error=str(exc)[:160])
        draft = _deterministic_fallback_draft(items, events_with_n, israel_items)
        fallback_qa = check(draft, citation_items)
        if not fallback_qa.passed:
            log.error("weekly_report_fallback_draft_failed_citation_check", errors=fallback_qa.errors[:10])
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    draft = normalize_draft(draft)

    # Round 5 P3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 4/11; docs/qa/loop/round_3_judge.md
    # D2): same draft-QA step `textnorm.normalize_draft` is already called from -- strip the W26
    # filler-phrase list and the D2 so_what template-phrase list from the final draft before it
    # renders (see `eoa.report.daily.build_daily`'s identical wiring for the full rationale).
    draft, _style_report = apply_style_guard(draft, report_kind="weekly")
    _style_report.log_all(report_kind="weekly")
    draft, _so_what_removed = strip_so_what_phrases_from_draft(draft, report_kind="weekly")
    # Round-14 (CR-editing.md, "repeated sentences across sections") -- see
    # `eoa.report.daily.build_daily`'s identical wiring for the full rationale.
    draft, _n_dupes_dropped = dedupe_exact_sentences_across_sections(draft)
    if _n_dupes_dropped:
        log.info("weekly_report_exact_duplicate_sentences_dropped", n=_n_dupes_dropped)

    # CR-monthly.md item 2 (last sentence): a trend section may only cite its OWN evidence -- strip
    # any sentence that leaked a citation from another trend or from outside any trend's evidence,
    # before the claims gate below (order doesn't matter between these two -- neither touches the
    # other's target text).
    new_trends, _n_trend_sentences_out_of_scope = strip_trend_out_of_scope_sentences(
        draft.trends, trend_list, id_to_n, report_kind="weekly"
    )
    if _n_trend_sentences_out_of_scope:
        log.info("weekly_report_trend_sentences_out_of_scope_dropped", n=_n_trend_sentences_out_of_scope)
    draft = draft.model_copy(update={"trends": new_trends})

    # CR-monthly.md item 3: deterministic claims gate -- an evaluative/intensifier sentence
    # ("ניכרת התעצמות דרמטית", "מגמה חדשה" as a bare adjective, "קפיצת מדרגה"...) with no supporting
    # quantity in the same sentence gets softened or, if nothing evidentiary survives, dropped.
    draft, _claims_gate_report = apply_claims_gate(draft, report_kind="weekly")
    if _claims_gate_report.softened or _claims_gate_report.dropped:
        log.info(
            "weekly_report_claims_gate_applied",
            softened=_claims_gate_report.softened,
            dropped=_claims_gate_report.dropped,
        )

    # docs/qa/content_review/REPORT-REDUNDANCY.md (2026-09-08 user feedback: "the report repeats
    # the information overview needlessly"): a deterministic cross-section near-duplicate pass,
    # AFTER drafting and AFTER the claims gate above (softened/dropped text must be final before
    # comparing sentences) -- see `eoa.report.redundancy.apply_redundancy_pass`'s own docstring
    # for the BLUF > exec summary > trends > domain review > analyst note priority order.
    draft, _redundancy_result = apply_redundancy_pass(draft, report_kind="weekly")
    _redundancy_result.log_all(report_kind="weekly")
    if _redundancy_result.dropped:
        log.info(
            "weekly_report_redundancy_pass_applied",
            n_dropped=_redundancy_result.n_dropped,
            n_pointer_sections=len(_redundancy_result.pointer_sections),
        )
    for _entry in deep_search:
        if _entry.get("key_facts"):
            _entry["key_facts"], _n_facts_dropped = filter_facts_against_narrative(
                _entry["key_facts"], _redundancy_result.kept_sentence_texts
            )
            if _n_facts_dropped:
                log.info(
                    "weekly_report_deep_search_facts_redundant_dropped",
                    job_id=_entry.get("job_id"),
                    n=_n_facts_dropped,
                )

    trend_sections = [
        {
            "title_he": tp.title_he,
            "body_he": _render_trend_sentences(tp.sentences),
            "position": "after_summary",
            # R6-weekly (docs/qa/loop/round_6_fixes.md): every trend used to get its own top-level
            # heading (5 on the live 2026-09-07 weekly) -- grouped under one "מגמות השבוע" parent,
            # each trend as an "###" child, to bring the report's H2 count within budget.
            "group_he": _TRENDS_GROUP_HE,
        }
        for tp in draft.trends
    ]
    # U13: suppress the meta-summary section entirely when there is nothing to report, rather than
    # printing a heading followed only by a "nothing happened" placeholder line.
    meta_has_content = (
        bool(meta.get("lessons")) or bool(meta.get("feedback_deltas")) or bool(meta.get("feedback_total"))
    )

    # Round 5 P2 (docs/PLAN_ROUND5_REPORTS.md P2, D4/W3/D5): "מה השתנה מאז הדוח הקודם" (deterministic
    # delta vs. the previous weekly report, including trend appeared/strengthened/weakened/vanished)
    # comes first among the "after_summary" extras (right after the executive summary, before the
    # trend sections above); the "מעקב אינדיקטורים" (I&W) watchlist table renders in the outlook
    # area ("after_outlook", before the meta-summary section below). A failure in either must never
    # break the weekly report.
    #
    # Round 5 P3: BLUF ("שורה תחתונה") and "הנחות והפרכות" need no wiring here -- `docx_builder`
    # (P4) renders both natively from `draft.bluf`/`draft.assumptions` (docs/MODULES.md "Round 5
    # P3").
    extra_sections: list[dict[str, Any]] = []
    indicator_rows: list[dict[str, Any]] = []
    try:
        from eoa.report import deltas

        delta_result = deltas.compute_deltas(
            "weekly", items, before_period_end=end, current_trends=trend_list, id_to_n=id_to_n
        )
        extra_sections.append(
            deltas.delta_extra_section(delta_result, narrative_cites=narrative_citation_numbers(draft))
        )
    except Exception as exc:
        log.warning("weekly_report_deltas_section_failed", error=str(exc)[:160])
    extra_sections.extend(trend_sections)
    try:
        from eoa.report import indicators

        indicator_section, indicator_rows = indicators.build_indicator_watchlist_section(
            "weekly", draft.outlook, items, citation_items
        )
        if indicator_section:
            extra_sections.append(indicator_section)
    except Exception as exc:
        log.warning("weekly_report_indicator_watchlist_failed", error=str(exc)[:160])
    if meta_has_content:
        extra_sections.append(
            {
                "title_he": "סיכום מטא שבועי — משוב משתמש (FR-11.4)",
                "body_he": format_meta_summary_he(meta),
                "position": "after_outlook",
            }
        )

    tables: list[dict[str, Any]] = []
    if conferences_90:
        rows = [
            [
                c.get("name") or "—",
                f"{fmt_date(c.get('start_date'))} - {fmt_date(c.get('end_date'))}",
                c.get("city") or "—",
                c.get("relevance") if c.get("relevance") is not None else "—",
            ]
            for c in conferences_90
        ]
        # R6-weekly: grouped under "פיתוח עסקי" together with the acquisition-watch prose below
        # (docx_builder's `group_he`; see the module-level `_BD_GROUP_HE` note).
        tables.append(
            {
                "title_he": "לוח 90 הימים הקרובים",
                "headers": ["שם", "תאריכים", "עיר", "רלוונטיות"],
                "rows": rows,
                "group_he": _BD_GROUP_HE,
            }
        )

    # A12 (מעקב טכנולוגי): per-subdomain radar aggregation (new papers/actors/momentum/so-what) +
    # a short "developments to follow" pick, deterministic tables extending `citation_items` in
    # place -- same additive mechanism as the conferences table above. A failure here must never
    # break the weekly report. R6-weekly: both tables grouped under "טכנולוגיה ו-IP" (see below).
    try:
        from eoa.pipeline.tech_watch import run_tech_watch_weekly
        from eoa.report.tech_watch import weekly_tech_watch_tables

        week_start_ts = dt.datetime.combine(start, dt.time.min, tzinfo=JERUSALEM).astimezone(dt.UTC)
        week_end_ts = dt.datetime.combine(end, dt.time.max, tzinfo=JERUSALEM).astimezone(dt.UTC)
        tech_aggregates = run_tech_watch_weekly(week_start_ts, week_end_ts, role=role)
        for tbl in weekly_tech_watch_tables(citation_items, tech_aggregates):
            tbl["group_he"] = _TECH_IP_GROUP_HE
            tables.append(tbl)
    except Exception as exc:
        log.warning("weekly_report_tech_watch_section_failed", error=str(exc)[:160])

    # A13 (מיקוד תעשייה ישראלית, 2026-09-06): "תעשייה ישראלית" section (category tables + the
    # per-company mentions/wins/competitors summary table), same additive mechanism as the
    # tech-watch tables above. A failure here must never break the weekly report.
    #
    # R6-weekly (docs/qa/loop/round_6_fixes.md): grouped under one "תעשייה ישראלית" heading (the
    # D6 `israel_single_table_with_type_column` check used to see this as *two* separate
    # top-level "תעשייה ישראלית" headings and fail). The merged table's own `title_he` already
    # equals `_ISRAEL_GROUP_HE` -- docx_builder renders it directly under the group heading with
    # no "###" of its own (see its `_group_entries` note) -- so only the per-company summary
    # table's title is stripped of its redundant "תעשייה ישראלית — " prefix (it would otherwise
    # give that same D6 check a second heading match).
    try:
        from eoa.report.israel_section import weekly_israel_tables

        week_start_ts_il = dt.datetime.combine(start, dt.time.min, tzinfo=JERUSALEM).astimezone(dt.UTC)
        week_end_ts_il = dt.datetime.combine(end, dt.time.max, tzinfo=JERUSALEM).astimezone(dt.UTC)
        for tbl in weekly_israel_tables(citation_items, week_start_ts_il, week_end_ts_il):
            tbl["group_he"] = _ISRAEL_GROUP_HE
            title = tbl.get("title_he") or ""
            if title and title != _ISRAEL_GROUP_HE:
                tbl["title_he"] = title.split("— ", 1)[-1]
            tables.append(tbl)
    except Exception as exc:
        log.warning("weekly_report_israel_section_failed", error=str(exc)[:160])

    # A14 (פטנטים ו-IP, 2026-09-06): "פטנטים ו-IP" section (new filings/grants this week +
    # a value-score table), same additive mechanism as the tech-watch/israel-section tables above.
    # A failure here must never break the weekly report. R6-weekly: both the prose and the table
    # join the "טכנולוגיה ו-IP" group -- the prose becomes a `tables`-list *prose* member (a dict
    # with `body_he` but no `headers`/`rows`; see docx_builder's `_group_entries` note) rather than
    # an `extra_sections` entry, so it can share a parent heading with the tech-watch/new-patents
    # tables above (an `extra_sections` group and a `tables` group render as two separate parents).
    try:
        from eoa.patents.report_section import collect_patents_window, patents_extra_section, patents_table

        patents_data = collect_patents_window(start, end)
        patents_prose = patents_extra_section(patents_data)
        tables.append(
            {
                "title_he": patents_prose["title_he"],
                "body_he": patents_prose["body_he"],
                "group_he": _TECH_IP_GROUP_HE,
            }
        )
        patents_tbl = patents_table(patents_data)
        if patents_tbl:
            patents_tbl["group_he"] = _TECH_IP_GROUP_HE
            tables.append(patents_tbl)
    except Exception as exc:
        log.warning("weekly_report_patents_section_failed", error=str(exc)[:160])

    # A16 (מעקב רכישות ושותפויות, user requirement 2026-09-06): "מעקב רכישות ושותפויות" section
    # (events on acquisition-watch companies/peers + zero-activity lines + patent-proxy values),
    # same additive mechanism as the tech-watch/israel-section/patents sections above. A failure
    # here must never break the weekly report. See docs/MODULES.md's "A16" section for the
    # equivalent one-line call ``eoa.report.bd_territory`` can add to reuse this.
    #
    # R6-weekly: a `tables`-list prose member (see the patents/IP note above) grouped with the
    # 90-day calendar table under "פיתוח עסקי".
    try:
        from eoa.report.acquisition_watch import SECTION_TITLE_HE, acquisition_watch_section_md

        with connection() as acq_conn:
            acq_body = acquisition_watch_section_md(acq_conn, start, end, citation_items)
        if acq_body:
            tables.append({"title_he": SECTION_TITLE_HE, "body_he": acq_body, "group_he": _BD_GROUP_HE})
    except Exception as exc:
        log.warning("weekly_report_acquisition_watch_section_failed", error=str(exc)[:160])

    # R8-reports #1 (round-7 judge D6 #4/#5): re-mark the *fully extended* citation registry right
    # before rendering -- `collect_week_items` only marks its own ~N rows; `citation_items` picks
    # up further rows afterwards (evidence-id/event fallbacks, tech-watch/Israel-industry/patents/
    # acquisition-watch additive tables) that never went through the marker function before. Both
    # marker functions are idempotent (see `eoa.report.daily`'s docstrings), so this is safe even
    # for rows already marked by `collect_week_items`.
    _append_item_corroboration_markers(citation_items)
    _append_event_corroboration_markers(events_with_n)

    docx_path = _report_path(end, "docx")
    md_path = _report_path(end, "md")
    html_path = _report_path(end, "html")
    docx_path, md_path, html_path = versioned_paths(docx_path, md_path, html_path)

    doc = build_docx(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
        include_toc=True,
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
    )
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
        include_toc=True,
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    # Round 5 P2: this issue's own state (items + trend strengths), persisted for the *next*
    # weekly report's delta.
    try:
        from eoa.report import deltas

        open_indicator_ids = [
            r["id"]
            for r in indicator_rows
            if r.get("_row_status") in ("open", "new") and r.get("id") is not None
        ]
        report_state = deltas.build_report_state(items, trends=trend_list, indicator_ids=open_indicator_ids)
    except Exception as exc:
        log.warning("weekly_report_state_build_failed", error=str(exc)[:160])
        report_state = None
    report_id = _persist_report(
        start,
        end,
        docx_path,
        md_path,
        html_path,
        items,
        qa,
        report_state=report_state,
        extra_qa_fields={
            "trend_sentences_out_of_scope": _n_trend_sentences_out_of_scope,
            "claims_gate_softened": _claims_gate_report.softened,
            "claims_gate_dropped": _claims_gate_report.dropped,
        },
    )

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
