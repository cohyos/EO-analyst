"""Stage: report/bd_territory — A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה".

For one territory (ISO-2 country code or recognized region code -- US, IL, EU, UK/GB, DE, FR,
IN, KR, JP, AU, PL, GR, AE, SA, ...; normalized via ``eoa.report.geography.normalize_country``)
and a lookback window (default 90 days), builds an analyst-grade Hebrew report covering:

1. market picture (``collect_market_items`` -> LLM synthesis, ``market_bullets``);
2. procurement/platforms (``collect_platform_events``, deterministic, matched against
   ``eoa.tenders.platform_payloads.yaml`` via ``eoa.tenders.forecast.load_platform_payloads``);
3. tenders/forecasts (``collect_tenders_and_forecasts``, deterministic);
4. active competitors (``collect_active_competitors``, deterministic) plus an LLM read on their
   recent moves (``competitor_moves``);
5. upcoming conferences (``collect_conferences_for_territory``, deterministic, best-effort city
   matching since ``conferences`` has no country column);
6. recommended entry points/actions (LLM, structured -- ``BdRecommendedAction``);
7. risks/assumptions (LLM, citation-exempt like ``outlook_he`` elsewhere).

Round 3 (2026-09-06, D7 judge finding, ``docs/qa/loop/round_2_judge.md`` -- score 35, "the US BD
report was rebuilt 8 times in one day and failed citation QA every time"): migrated the drafting
schema from free Hebrew prose (post-hoc regex citation stripping, ``eoa.llm.schemas.reports.
BdTerritoryReportDraft``/``BdAction``) to the structured, citations-by-construction shape
``eoa.report.daily``/``eoa.report.weekly`` already use (``eoa.llm.schemas.bd_territory.
BdTerritoryReportDraft``: ``Sentence{text_he, cites[]}`` everywhere a factual claim is made) --
see that module for the schema itself. An uncited factual claim is now a pydantic validation
error the model must fix (``chat_structured``'s existing retry-with-error-message), not something
a post-hoc QA pass notices and strips out of already-generated prose.

Pipeline: collect -> draft (resident model, structured schema) -> perspective gate (BD-1: a
recommended action must never promote a competitor) -> ``qa_citations.check`` + this module's own
structured-field citation check (``_run_qa``) -> one corrective retry on failure -> on a *second*
failure, replace the narrative with a deterministic, cited substitute synthesis built straight from
the data (mirrors ``eoa.report.daily``/``eoa.report.weekly``'s own two-failure fallback, see
``_deterministic_fallback_draft``) -> render docx/md/html via ``eoa.report.docx_builder``'s
additive ``extra_sections``/``tables`` hooks -> persist a ``reports`` row (``kind='bd_territory'``,
``territory=<code>``).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.errors import LLMOutputError
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.bd_territory import BdRecommendedAction, BdTerritoryReportDraft
from eoa.report.docx_builder import (
    build_docx,
    fmt_date,
    hebrew_date_str,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)
from eoa.report.geography import normalize_country
from eoa.report.qa_citations import QAResult, check
from eoa.report.textnorm import normalize_hebrew_punctuation

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

TITLE_TEMPLATE_HE = "דוח מיקוד לפיתוח עסקי — {territory}"

_INSCOPE_LEVELS = ("red", "orange", "yellow")

# events.kind CHECK (db/migrations/versions/0001_core.py) doesn't have separate
# "contract"/"award"/"acquisition"/"trial" values -- these are the closest real enum members
# (m_and_a for acquisitions, test for trials/demonstrations).
_PROCUREMENT_EVENT_KINDS = ("contract_award", "m_and_a", "deployment", "test")


# A13 (מיקוד תעשייה ישראלית, 2026-09-06): additive -- was a hardcoded 4-name set; now derived
# from every `country: IL` company on `config/watchlist.yaml` (eoa.pipeline.israel_focus), so the
# BD competitors table's "is_israeli_industry" flag stays in sync with the watchlist without this
# file needing to be touched again each time an Israeli company is added there. A bare `set()`
# fallback (never raises) keeps this file's behavior unchanged if the watchlist/settings are
# unavailable for any reason (e.g. a bare unit test with no config fixture).
def _israeli_industry_names() -> set[str]:
    try:
        from eoa.pipeline.israel_focus import israeli_watchlist_names

        return set(israeli_watchlist_names())
    except Exception:
        return {"Elbit", "Rafael", "IAI", "Controp"}


# BD-1 (docs/qa/findings_Q3_r2.md): observed a live truncated-JSON crash (schema validation
# failed with "EOF while parsing a string") against a busy territory (22 market items) at the
# shared config default (``ollama.num_predict.report`` = 6000) -- five DATA blocks plus the
# perspective framing produce a longer completion (exec summary + up to 8 bullets + up to 8
# structured actions) than the other report types this default was tuned for. Overridden here via
# ``chat_structured``'s per-call ``options`` (merged over the config default in
# ``eoa.llm.ollama_client._ollama_chat``) rather than raising the shared config default, so
# daily/weekly/monthly are unaffected.
_BD_NUM_PREDICT = 9000

#: Round-3 (2026-09-06, job 97): the first structured US draft ran away to a 54k-char JSON that
#: ended mid-string (EOF at column 54376) -- the same failure the weekly report had before its
#: round-2 input-side reduction. Same cure: the citation registry keeps every market item (``n``
#: numbering unchanged), but the model only *sees* every red item plus the top few per domain,
#: with per-item text truncated -- two independent levers on output size, on top of the lower
#: ``_BD_NUM_PREDICT`` above (9000 tokens is ample for a structured draft over ~15 items and
#: makes a runaway fail in a third of the time).
_BD_PROMPT_ITEMS_PER_DOMAIN = 4
_BD_PROMPT_ITEM_TEXT_CHARS = 400


def select_bd_items_for_prompt(
    items: list[dict[str, Any]], *, per_domain: int = _BD_PROMPT_ITEMS_PER_DOMAIN
) -> list[dict[str, Any]]:
    """The reduced, score-ordered subset of ``items`` shown to the model (every ``level == 'red'``
    item + the top ``per_domain`` per domain), each a shallow copy with ``summary_he`` /
    ``so_what_he`` truncated to :data:`_BD_PROMPT_ITEM_TEXT_CHARS`. ``items`` itself (the citation
    registry) is never modified."""
    keep: set[int] = set()
    for _domain, group in _group_by_domain(items):
        for it in group[:per_domain]:
            if it.get("id") is not None:
                keep.add(it["id"])
    for it in items:
        if it.get("level") == "red" and it.get("id") is not None:
            keep.add(it["id"])
    out: list[dict[str, Any]] = []
    for it in items:
        if it.get("id") not in keep:
            continue
        copy = dict(it)
        for f in ("summary_he", "so_what_he"):
            v = copy.get(f)
            if isinstance(v, str) and len(v) > _BD_PROMPT_ITEM_TEXT_CHARS:
                copy[f] = v[: _BD_PROMPT_ITEM_TEXT_CHARS - 1].rstrip() + "…"
        out.append(copy)
    return out


# Best-effort conference-city -> territory-code heuristic (conferences has no country column --
# see docs/adr, ``eoa.conferences.tracker``). Deliberately small: only the handful of cities that
# actually host EO/IR-relevant defense conferences the tracker seeds. Case-insensitive substring
# match against ``city``/``venue``/``name``.
_CONFERENCE_CITY_TERRITORY: dict[str, str] = {
    "washington": "US",
    "national harbor": "US",
    "orlando": "US",
    "las vegas": "US",
    "huntsville": "US",
    "arlington": "US",
    "tampa": "US",
    "san diego": "US",
    "tel aviv": "IL",
    "herzliya": "IL",
    "jerusalem": "IL",
    "london": "GB",
    "farnborough": "GB",
    "paris": "FR",
    "berlin": "DE",
    "abu dhabi": "AE",
    "dubai": "AE",
    "seoul": "KR",
    "goyang": "KR",
    "tokyo": "JP",
    "singapore": "SG",
    "athens": "GR",
    "warsaw": "PL",
    "riyadh": "SA",
    "canberra": "AU",
    "adelaide": "AU",
    "new delhi": "IN",
    "bengaluru": "IN",
}


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult
    territory: str


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def lookback_range(lookback_days: int = 90, period_end: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The ``lookback_days``-day window ending on ``period_end`` (default: today, Asia/Jerusalem),
    inclusive."""
    end = period_end or _today_jerusalem()
    start = end - dt.timedelta(days=max(lookback_days - 1, 0))
    return start, end


def territory_label(territory: str) -> str:
    """Display label for a territory code -- the code itself, upper-cased; the frontend attaches
    the flag/human name (``web/src/lib/countries.ts``)."""
    return normalize_country(territory)


# --------------------------------------------------------------------------
# taxonomy label / grouping helpers (small, local copies -- see eoa.report.weekly's identical
# comment: this module doesn't depend on daily.py/weekly.py's internals, docs/CONVENTIONS.md rule 6)
# --------------------------------------------------------------------------


def _domain_label(domain: str | None) -> str:
    domains = settings().taxonomy.get("domains", {})
    entry = domains.get(domain or "", {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else (domain or "כללי")


def _group_by_domain(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    order = list(settings().taxonomy.get("domains", {}).keys())
    buckets: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        buckets.setdefault(it.get("domain") or "secondary", []).append(it)
    ordered = [d for d in order if d in buckets] + [d for d in buckets if d not in order]
    return [(d, buckets[d]) for d in ordered]


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


# --------------------------------------------------------------------------
# 1. market picture
# --------------------------------------------------------------------------


def _territory_entity_names(code: str) -> set[str]:
    """Entity names whose ``country`` normalizes to ``code`` -- used to broaden item-territory
    matching beyond ``items.geography`` to items that only mention a territory-local entity
    (e.g. an article about "IAI" that never set ``geography``)."""
    rows = _fetchall("SELECT name, country FROM entities WHERE country IS NOT NULL")
    return {r["name"] for r in rows if normalize_country(r.get("country")) == code}


def collect_market_items(
    territory: str, period_start: dt.date, period_end: dt.date, *, max_items: int = 250
) -> list[dict[str, Any]]:
    """In-scope items (level >= yellow, i.e. not archived) whose ``geography`` normalizes to the
    territory, or whose ``entities_mentioned`` includes a territory-local entity, published in the
    window. Ordered by score desc, each carrying a stable 1-based ``n``."""
    code = normalize_country(territory)
    territory_entities = _territory_entity_names(code)
    rows = _fetchall(
        """
        SELECT i.id, i.url, i.title, i.domain, i.subdomain, i.published_at, i.level, i.score,
               i.summary_he, i.so_what_he, i.geography, i.entities_mentioned,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.security_status = 'clean'
          AND i.dedup_of IS NULL
          AND i.level = ANY(%(levels)s)
          AND COALESCE(i.published_at, i.fetched_at, i.created_at)::date
              BETWEEN %(start)s AND %(end)s
        ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
        """,
        {"levels": list(_INSCOPE_LEVELS), "start": period_start, "end": period_end},
    )
    matched: list[dict[str, Any]] = []
    for row in rows:
        if normalize_country(row.get("geography")) == code:
            matched.append(row)
            continue
        mentioned = set(row.get("entities_mentioned") or [])
        if mentioned & territory_entities:
            matched.append(row)
    matched = matched[:max_items]
    for idx, row in enumerate(matched, start=1):
        row["n"] = idx
    log.info(
        "bd_territory_market_items_collected",
        territory=code,
        count=len(matched),
        start=str(period_start),
        end=str(period_end),
    )
    return matched


def format_market_items_block(items: list[dict[str, Any]]) -> str:
    if not items:
        return "לא זוהו פריטי שוק בטריטוריה זו בחלון הזמן שנבדק."
    lines: list[str] = []
    for domain, group in _group_by_domain(items):
        lines.append(f"### {_domain_label(domain)}")
        for it in group:
            lines.append(
                f"[{it['n']}] כותרת: {it.get('title') or '—'} | מקור: "
                f"{it.get('source_name') or it.get('url') or '—'} | תאריך: {fmt_date(it.get('published_at'))}"
            )
            lines.append(f"תקציר: {it.get('summary_he') or '—'}")
            if it.get("so_what_he"):
                lines.append(f"מה זה אומר: {it['so_what_he']}")
            lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 2. procurement / platforms
# --------------------------------------------------------------------------


def collect_platform_events(
    territory: str, period_start: dt.date, period_end: dt.date, *, limit: int = 25
) -> list[dict[str, Any]]:
    """Procurement/platform business events in the territory in the window (F: substitutes for
    "contract/award/acquisition/deployment/trial" using the real ``events.kind`` enum --
    ``contract_award``/``m_and_a``/``deployment``/``test``), matched against
    ``eoa.tenders.platform_payloads.yaml`` (via ``eoa.tenders.forecast.load_platform_payloads``)
    to attach the typical EO/IR payload need those platforms imply."""
    code = normalize_country(territory)
    rows = _fetchall(
        """
        SELECT e.id, e.item_id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties,
               e.customer, e.program, e.summary_he,
               i.url AS item_url, i.title AS item_title, i.published_at, i.geography, i.clean_text,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE e.kind = ANY(%(kinds)s)
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
        ORDER BY e.date DESC NULLS LAST, e.id DESC
        """,
        {"kinds": list(_PROCUREMENT_EVENT_KINDS), "start": period_start, "end": period_end},
    )
    rows = [r for r in rows if normalize_country(r.get("geography")) == code]

    try:
        from eoa.tenders.forecast import load_platform_payloads

        platforms = load_platform_payloads()
    except Exception as exc:  # pragma: no cover -- tenders package unavailable/broken
        log.warning("bd_territory_platform_payloads_load_failed", error=str(exc)[:200])
        platforms = []

    out: list[dict[str, Any]] = []
    for ev in rows:
        text = " ".join(
            str(x)
            for x in (ev.get("title"), ev.get("item_title"), ev.get("clean_text"), ev.get("summary_he"))
            if x
        )
        match = next((p for p in platforms if p.matches(text)), None)
        parties = ev.get("parties") or []
        vendor = next((p for p in parties if p != ev.get("customer")), None) or (
            parties[0] if parties else None
        )
        out.append(
            {
                "item_id": ev.get("item_id"),
                "item_url": ev.get("item_url"),
                "item_title": ev.get("item_title"),
                "source_name": ev.get("source_name"),
                "published_at": ev.get("published_at"),
                "date": ev.get("date"),
                "platform_he": match.category_he if match else (ev.get("program") or ev.get("title") or "—"),
                "payload_need_he": match.payload_need_he if match else None,
                "buyer": ev.get("customer") or (parties[0] if parties else None) or "—",
                "vendor": vendor or "—",
                "amount_usd": ev.get("amount_usd"),
                "currency": ev.get("currency"),
            }
        )
    return out[:limit]


def platform_events_table(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    headers = ["תאריך", "פלטפורמה/תוכנית", "רוכש", "ספק", "סכום", "צורך EO/IR נגזר", "מקור"]
    rows = []
    for ev in events:
        amount = f"{ev['amount_usd']:,.0f} {ev.get('currency') or 'USD'}" if ev.get("amount_usd") else "—"
        src = f"[{ev['n']}]" if ev.get("n") is not None else "—"
        rows.append(
            [
                fmt_date(ev.get("date") or ev.get("published_at")),
                ev.get("platform_he") or "—",
                ev.get("buyer") or "—",
                ev.get("vendor") or "—",
                amount,
                ev.get("payload_need_he") or "—",
                src,
            ]
        )
    return {"title_he": "רכש ופלטפורמות", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# 3. tenders / forecasts
# --------------------------------------------------------------------------


def collect_tenders_and_forecasts(territory: str, *, limit: int = 20) -> dict[str, Any]:
    """Open/unknown tenders whose ``country`` normalizes to the territory, and forecasts whose
    ``buyer_country`` normalizes to the territory -- section 3 (מכרזים ותחזיות)."""
    code = normalize_country(territory)
    # Round-3 (D9 finding 4c, docs/qa/loop/round_1_judge.md): open rows first (earliest deadline
    # first), then unknown rows (most-recently-published first, "unknown-recent") -- closed/
    # archived rows are excluded outright by the WHERE clause, never shown here at all. Mirrors
    # ``eoa.api.services.list_tenders``'s identical ordering fix for the tenders API/UI.
    tenders = _fetchall(
        "SELECT * FROM tenders WHERE status IN ('open', 'unknown') "
        "ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
        "CASE WHEN status = 'open' THEN deadline END ASC NULLS LAST, "
        "CASE WHEN status = 'unknown' THEN published_at END DESC NULLS LAST, "
        "relevance DESC NULLS LAST, id DESC LIMIT 500"
    )
    tenders = [t for t in tenders if normalize_country(t.get("country")) == code][:limit]

    forecasts = _fetchall(
        "SELECT * FROM tender_forecasts ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT 500"
    )
    forecasts = [f for f in forecasts if normalize_country(f.get("buyer_country")) == code][:limit]
    return {"tenders": tenders, "forecasts": forecasts}


def format_tenders_block(data: dict[str, Any]) -> str:
    """Each line carries the ``[n]`` the calling ``build_bd_territory`` assigned via
    ``_extend_registry_with_tenders`` -- without it the model has no valid citation to attach to a
    tender/forecast-derived claim, which in practice produced uncited (and, worse, sometimes
    runaway/degenerate) generations."""
    tenders = data.get("tenders") or []
    forecasts = data.get("forecasts") or []
    lines: list[str] = []
    if tenders:
        lines.append("מכרזים פתוחים/לא ידועים:")
        for t in tenders:
            status_he = {"open": "פתוח", "unknown": "לא ידוע"}.get(t.get("status"), t.get("status") or "—")
            n = f"[{t['n']}] " if t.get("n") is not None else ""
            lines.append(
                f"- {n}{t.get('title') or '—'} | {t.get('agency') or '—'} | דדליין: {fmt_date(t.get('deadline'))} "
                f"| סטטוס: {status_he} | {t.get('url') or '—'}"
            )
    else:
        lines.append("לא זוהו מכרזים פתוחים/לא ידועים בטריטוריה זו.")
    lines.append("")
    if forecasts:
        lines.append("תחזיות רכש:")
        for f in forecasts:
            likelihood = f.get("likelihood")
            pct = f"{likelihood:.0%}" if isinstance(likelihood, int | float) else "—"
            n = f"[{f['n']}] " if f.get("n") is not None else ""
            lines.append(
                f"- {n}{f.get('platform') or '—'}: {f.get('payload_need') or '—'} — סבירות {pct}, חלון "
                f"{fmt_date(f.get('window_from'))} עד {fmt_date(f.get('window_to'))}"
            )
    else:
        lines.append("לא נמצאו תחזיות רכש בטריטוריה זו.")
    return "\n".join(lines)


def tenders_table(data: dict[str, Any]) -> dict[str, Any] | None:
    tenders = data.get("tenders") or []
    if not tenders:
        return None
    status_he = {"open": "פתוח", "unknown": "לא ידוע"}
    headers = ["כותרת", "גורם מזמין", "דדליין", "סטטוס", "קישור"]
    rows = [
        [
            t.get("title") or "—",
            t.get("agency") or "—",
            fmt_date(t.get("deadline")),
            status_he.get(t.get("status"), t.get("status") or "—"),
            t.get("url") or "—",
        ]
        for t in tenders
    ]
    return {"title_he": "מכרזים בטריטוריה", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# 4. active competitors
# --------------------------------------------------------------------------


def _entity_edge_activity(entity_id: int, market_item_ids: list[int]) -> int:
    """Count of ``graph_edges`` rows touching ``entity_id`` and evidenced by one of the window's
    market items -- part of BD-1's "active in the window" test (mention/edge/event), alongside
    ``entities_mentioned`` and ``contract_award`` events."""
    if not market_item_ids:
        return 0
    rows = _fetchall(
        "SELECT count(*) AS n FROM graph_edges WHERE item_id = ANY(%(ids)s) "
        "AND (src_entity_id = %(id)s OR dst_entity_id = %(id)s)",
        {"ids": market_item_ids, "id": entity_id},
    )
    return int(rows[0]["n"]) if rows else 0


def collect_active_competitors(
    territory: str, market_item_ids: list[int], period_start: dt.date, period_end: dt.date, *, limit: int = 15
) -> list[dict[str, Any]]:
    """Company entities (relevance >= 0.4) active in the territory -- either headquartered there
    (``entities.country``) or mentioned by one of the territory's market items in the window --
    with their recent wins (``contract_award`` events among those same items) and an
    ``is_israeli_industry`` flag (Elbit/Rafael/IAI/Controp, per config/watchlist.yaml canonical
    names) for the "זווית התעשייה הישראלית" angle.

    BD-1 (docs/qa/findings_Q3_r2.md): a company merely headquartered in the territory but with no
    activity in the window (no mention, no graph edge, no event) is noise, not a "competitor
    active in the territory" -- excluded here. ``collect_dormant_watchlist_competitors`` reports
    the watchlist names this filter drops, for the report's one-line dormant-competitors note."""
    code = normalize_country(territory)
    companies = _fetchall(
        "SELECT id, name, country, relevance, is_watchlist FROM entities "
        "WHERE kind = 'company' AND relevance >= 0.4"
    )
    if not companies:
        return []

    mentions_by_name: dict[str, int] = {}
    if market_item_ids:
        mention_rows = _fetchall(
            "SELECT unnest(entities_mentioned) AS name, count(*) AS n FROM items "
            "WHERE id = ANY(%(ids)s) AND entities_mentioned IS NOT NULL GROUP BY 1",
            {"ids": market_item_ids},
        )
        mentions_by_name = {r["name"]: r["n"] for r in mention_rows}

    candidates = [
        c for c in companies if normalize_country(c.get("country")) == code or c["name"] in mentions_by_name
    ]

    israeli_names = _israeli_industry_names()
    out: list[dict[str, Any]] = []
    for c in candidates:
        wins: list[dict[str, Any]] = []
        if market_item_ids:
            wins = _fetchall(
                "SELECT id, item_id, title, date, amount_usd, currency, customer, program FROM events "
                "WHERE item_id = ANY(%(ids)s) AND kind = 'contract_award' "
                "AND (%(name)s = customer OR %(name)s = ANY(parties)) "
                "ORDER BY date DESC NULLS LAST LIMIT 5",
                {"ids": market_item_ids, "name": c["name"]},
            )
        mentions = mentions_by_name.get(c["name"], 0)
        edges = 0 if (mentions or wins) else _entity_edge_activity(c["id"], market_item_ids)
        if mentions < 1 and not wins and edges < 1:
            continue  # BD-1: no activity in the window -- excluded as noise, not a real signal
        out.append(
            {
                "entity_id": c["id"],
                "name": c["name"],
                "country": c.get("country"),
                "mentions": mentions,
                "is_watchlist": bool(c.get("is_watchlist")),
                "is_israeli_industry": c["name"] in israeli_names,
                "recent_wins": wins,
            }
        )
    out.sort(key=lambda c: c["mentions"], reverse=True)
    return out[:limit]


def collect_dormant_watchlist_competitors(
    territory: str, active_names: set[str], *, limit: int = 20
) -> list[str]:
    """BD-1: watchlist company names headquartered in the territory that ``collect_active_competitors``
    excluded for having zero activity in the window -- surfaced as a one-line note rather than
    silently dropped, so the analyst knows they were checked and simply had nothing to report."""
    code = normalize_country(territory)
    rows = _fetchall("SELECT name, country FROM entities WHERE kind = 'company' AND is_watchlist = true")
    dormant = [
        r["name"]
        for r in rows
        if normalize_country(r.get("country")) == code and r["name"] not in active_names
    ]
    return dormant[:limit]


def format_competitors_block(competitors: list[dict[str, Any]]) -> str:
    """A win's ``[n]`` is the market item it came from (``build_bd_territory`` attaches it via
    ``_attach_win_citations`` before formatting -- every win's ``item_id`` is one of the same
    market items already in the citation registry, so no synthetic entry is needed here, unlike
    tenders/conferences)."""
    if not competitors:
        return "לא זוהו מתחרים פעילים בטריטוריה זו בחלון הזמן שנבדק."
    lines: list[str] = []
    for c in competitors:
        tag = (
            " (תעשייה ישראלית)"
            if c["is_israeli_industry"]
            else " (מתחרה ברשימת המעקב)"
            if c.get("is_watchlist")
            else " (מתחרה)"
        )
        lines.append(f"- {c['name']}{tag} | מדינה: {c.get('country') or '—'} | אזכורים: {c['mentions']}")
        for w in c.get("recent_wins") or []:
            amount = f"{w['amount_usd']:,.0f} {w.get('currency') or 'USD'}" if w.get("amount_usd") else "—"
            n = f"[{w['n']}] " if w.get("n") is not None else ""
            lines.append(
                f"  - {n}זכייה: {w.get('title') or w.get('program') or '—'} | {fmt_date(w.get('date'))} | {amount}"
            )
    return "\n".join(lines)


def competitors_table(competitors: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not competitors:
        return None
    headers = ["מתחרה", "מדינה", "אזכורים בחלון", "תעשייה ישראלית", "זכייה אחרונה"]
    rows = []
    for c in competitors:
        last_win = (c.get("recent_wins") or [None])[0]
        last_win_text = (
            f"{last_win.get('title') or last_win.get('program') or '—'} ({fmt_date(last_win.get('date'))})"
            if last_win
            else "—"
        )
        rows.append(
            [
                c["name"],
                c.get("country") or "—",
                c["mentions"],
                "כן" if c["is_israeli_industry"] else "—",
                last_win_text,
            ]
        )
    return {"title_he": "מתחרים פעילים בטריטוריה", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# 5. upcoming conferences
# --------------------------------------------------------------------------


def _conference_territory(conf: dict[str, Any]) -> str | None:
    haystack = " ".join(str(conf.get(k) or "") for k in ("city", "venue", "name")).lower()
    for city, code in _CONFERENCE_CITY_TERRITORY.items():
        if city in haystack:
            return code
    return None


def collect_conferences_for_territory(
    territory: str, *, months: int = 12, international_limit: int = 5
) -> dict[str, Any]:
    """Upcoming conferences (next ``months`` months) in the territory (best-effort city match,
    ``_conference_territory``) plus the top ``international_limit`` upcoming conferences overall
    (by relevance) as "international ones the territory's buyers attend" context, per A11."""
    code = normalize_country(territory)
    today = dt.date.today()
    horizon = today + dt.timedelta(days=months * 31)
    try:
        from eoa.conferences.tracker import upcoming as _upcoming
    except ImportError:
        return {"territory": [], "international": []}
    try:
        rows = _upcoming(months * 31) or []
    except Exception as exc:
        log.warning("bd_territory_conferences_failed", error=str(exc)[:200])
        return {"territory": [], "international": []}

    territory_rows = [r for r in rows if _conference_territory(r) == code]
    international_rows = sorted(
        (r for r in rows if _conference_territory(r) != code),
        key=lambda r: r.get("relevance") or 0,
        reverse=True,
    )[:international_limit]
    log.info(
        "bd_territory_conferences_collected",
        territory=code,
        territory_count=len(territory_rows),
        international_count=len(international_rows),
        horizon=str(horizon),
    )
    return {"territory": territory_rows, "international": international_rows}


_CONFERENCE_STATUS_HE = {"confirmed": "מאושר", "estimated": "משוער"}


def _conference_status_he(conf: dict[str, Any]) -> str:
    """BD-1 (docs/qa/findings_Q3_r2.md): the real ``conferences.status`` value -- never a
    synthesized/placeholder date or status. Anything other than the two known statuses (e.g. a
    future status value this code doesn't know about yet) is shown as-is rather than hidden."""
    status = conf.get("status")
    return _CONFERENCE_STATUS_HE.get(status, status or "—")


def format_conferences_block(data: dict[str, Any]) -> str:
    """Each line carries the ``[n]`` ``_extend_registry_with_conferences`` assigned -- see
    ``format_tenders_block``'s docstring for why this matters. Dates, status (confirmed/estimated)
    and organizer are read straight off the ``conferences`` row (``eoa.conferences.tracker``,
    ``conferences.start_date``/``end_date``/``status``/``organizer``) -- never synthesized here
    (BD-1: a previous build showed a placeholder date derived from list position rather than the
    real, now-verified, row)."""
    territory_rows = data.get("territory") or []
    international_rows = data.get("international") or []
    lines: list[str] = []
    if territory_rows:
        lines.append("כנסים בטריטוריה:")
        for c in territory_rows:
            n = f"[{c['n']}] " if c.get("n") is not None else ""
            lines.append(
                f"- {n}{c.get('name') or '—'} | {fmt_date(c.get('start_date'))} עד "
                f"{fmt_date(c.get('end_date'))} | {c.get('city') or '—'} | סטטוס: {_conference_status_he(c)} "
                f"| מארגן: {c.get('organizer') or '—'}"
            )
    else:
        lines.append("לא זוהו כנסים מתוכננים בטריטוריה זו ב-12 החודשים הקרובים.")
    lines.append("")
    if international_rows:
        lines.append("כנסים בינלאומיים רלוונטיים (הקשר בלבד):")
        for c in international_rows:
            n = f"[{c['n']}] " if c.get("n") is not None else ""
            lines.append(
                f"- {n}{c.get('name') or '—'} | {fmt_date(c.get('start_date'))} עד "
                f"{fmt_date(c.get('end_date'))} | {c.get('city') or '—'} | סטטוס: {_conference_status_he(c)} "
                f"| מארגן: {c.get('organizer') or '—'}"
            )
    return "\n".join(lines)


def conferences_table(data: dict[str, Any]) -> dict[str, Any] | None:
    territory_rows = data.get("territory") or []
    if not territory_rows:
        return None
    headers = ["שם", "תאריכים", "עיר", "סטטוס", "מארגן", "רלוונטיות"]
    rows = [
        [
            c.get("name") or "—",
            f"{fmt_date(c.get('start_date'))} - {fmt_date(c.get('end_date'))}",
            c.get("city") or "—",
            _conference_status_he(c),
            c.get("organizer") or "—",
            c.get("relevance") if c.get("relevance") is not None else "—",
        ]
        for c in territory_rows
    ]
    return {"title_he": "כנסים קרובים בטריטוריה", "headers": headers, "rows": rows}


# --------------------------------------------------------------------------
# citation registry (extends the market-items registry with event/tender source items)
# --------------------------------------------------------------------------


def _extend_registry_with_source_items(
    citation_items: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    *,
    item_id_key: str = "item_id",
    item_title_key: str = "item_title",
    item_url_key: str = "item_url",
) -> list[dict[str, Any]]:
    """Same numbering-extension convention as ``eoa.report.weekly._extend_registry_with_events``:
    append any source item referenced by ``entries`` (events/tenders) that isn't already numbered
    in ``citation_items``, mutating each entry in place with its resolved ``n``. Returns the
    (possibly extended) ``citation_items`` list."""
    by_item_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for entry in entries:
        item_id = entry.get(item_id_key)
        found = by_item_id.get(item_id) if item_id is not None else None
        if found is None and item_id is not None:
            found = {
                "id": item_id,
                "n": next_n,
                "title": entry.get(item_title_key),
                "source_name": entry.get("source_name"),
                "url": entry.get(item_url_key),
                "published_at": entry.get("published_at"),
            }
            citation_items.append(found)
            by_item_id[item_id] = found
            next_n += 1
        entry["n"] = found.get("n") if found else None
    return citation_items


def _synthetic_registry_entry(
    citation_items: list[dict[str, Any]],
    row: dict[str, Any],
    next_n: int,
    *,
    title: str | None,
    source_name: str | None,
    url: str | None,
    published_at: Any,
) -> int:
    """Register a synthetic citation-registry row for a source that carries its own ``url`` but no
    ``item_id`` (tenders/forecasts/conferences), keyed by a negative id so it never collides with a
    real item id, and stamp ``row["n"]`` so the caller's ``format_*_block`` can print ``[n]``.
    Returns the next free ``n``."""
    citation_items.append(
        {
            "id": -(1000 + next_n),
            "n": next_n,
            "title": title,
            "source_name": source_name,
            "url": url,
            "published_at": published_at,
        }
    )
    row["n"] = next_n
    return next_n + 1


def _extend_registry_with_tenders(
    citation_items: list[dict[str, Any]], data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Every tender/forecast gets its own registry row (F: without this the model has no valid
    citation for a tender/forecast-derived claim, which in practice produced uncited -- and once,
    degenerate/runaway -- generations, see ``format_tenders_block``)."""
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for t in data.get("tenders") or []:
        next_n = _synthetic_registry_entry(
            citation_items,
            t,
            next_n,
            title=t.get("title"),
            source_name=t.get("agency") or "מכרז",
            url=t.get("url"),
            published_at=t.get("published_at"),
        )
    for f in data.get("forecasts") or []:
        next_n = _synthetic_registry_entry(
            citation_items,
            f,
            next_n,
            title=f.get("platform"),
            source_name="תחזית רכש",
            url=None,
            published_at=f.get("created_at"),
        )
    return citation_items


def _extend_registry_with_conferences(
    citation_items: list[dict[str, Any]], data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Same convention as :func:`_extend_registry_with_tenders`, for both the territory and the
    international conference lists -- a conference has no ``item_id`` either."""
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for c in (data.get("territory") or []) + (data.get("international") or []):
        next_n = _synthetic_registry_entry(
            citation_items,
            c,
            next_n,
            title=c.get("name"),
            source_name=c.get("organizer") or "כנס",
            url=c.get("registration_url"),
            published_at=c.get("start_date"),
        )
    return citation_items


def _attach_win_citations(citation_items: list[dict[str, Any]], competitors: list[dict[str, Any]]) -> None:
    """A competitor's "recent win" event's ``item_id`` is always one of the same market items
    already in ``citation_items`` (``collect_active_competitors`` only looks among
    ``market_item_ids``) -- so each win reuses that item's existing ``n`` rather than getting a new
    synthetic registry row."""
    id_to_n = {
        it["id"]: it["n"] for it in citation_items if it.get("id") is not None and it.get("n") is not None
    }
    for c in competitors:
        for w in c.get("recent_wins") or []:
            w["n"] = id_to_n.get(w.get("item_id"))


# --------------------------------------------------------------------------
# sentence-rendering helpers (round 3: small local copy of the same convention
# eoa.report.weekly._render_trend_sentences/eoa.report.docx_builder's own private rendering
# helpers use -- deterministic "[n]" markers emitted from a Sentence's own cites, never written by
# the model itself)
# --------------------------------------------------------------------------


def _render_sentence(sentence: Sentence) -> str:
    text = sentence.text_he.rstrip()
    markers = "".join(f"[{n}]" for n in sentence.cites)
    return f"{text} {markers}".rstrip() if markers else text


def _render_sentences(sentences: list[Sentence]) -> str:
    return " ".join(_render_sentence(s) for s in sentences)


def _sentences_bullets_text(sentences: list[Sentence]) -> str:
    """One rendered sentence per line -- used for the ``market_bullets``/``competitor_moves``
    extra sections, mirroring the old free-prose ``_bullets_text``'s one-bullet-per-line layout."""
    return "\n".join(_render_sentence(s) for s in sentences)


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> BdTerritoryReportDraft:
    return BdTerritoryReportDraft(
        exec_summary=[],
        market_bullets=[],
        competitor_moves=[],
        sections=[],
        recommended_actions=[],
        analyst_note_he=None,
        risks_assumptions_he="לא נמצא מספיק מידע בטריטוריה זו כדי לבסס המלצות פעולה.",
        system_note_he="לא זוהו בטריטוריה זו פריטים חדשים בחלון הזמן שנבדק. אין ממצאים לדוח המיקוד.",
        open_points_he=[],
    )


@dataclass
class BdTableCounts:
    """BD-1 / Q3-14 (docs/qa/findings_Q3_r2.md, mirrors ``eoa.report.daily.TableCounts``): counts
    of report content that is rendered as a deterministic table rather than drafted by the LLM
    (procurement events, tenders, forecasts, competitors, conferences) -- ``draft_bd_territory``
    needs these so the exec summary never claims "no findings" while these tables are non-empty,
    even when the LLM-facing ``items`` (market items) list is empty."""

    events: int = 0
    tenders: int = 0
    forecasts: int = 0
    competitors: int = 0
    conferences: int = 0

    @property
    def total(self) -> int:
        return self.events + self.tenders + self.forecasts + self.competitors + self.conferences

    def context_he(self) -> str:
        if not self.total:
            return "אין (כל הטבלאות ריקות בתקופה זו)."
        parts = []
        if self.events:
            parts.append(f"{self.events} אירועי רכש/פלטפורמות")
        if self.tenders:
            parts.append(f"{self.tenders} מכרזים פתוחים/לא ידועים")
        if self.forecasts:
            parts.append(f"{self.forecasts} תחזיות רכש")
        if self.competitors:
            parts.append(f"{self.competitors} מתחרים פעילים")
        if self.conferences:
            parts.append(f"{self.conferences} כנסים קרובים בטריטוריה")
        return "; ".join(parts) + "."


def _tables_only_draft(territory: str, counts: BdTableCounts) -> BdTerritoryReportDraft:
    """BD-1 / Q3-14: used when ``items`` (market items) is empty but at least one other table
    (events/tenders/forecasts/competitors/conferences) is not -- a short, honest, deterministic
    summary of what the report *does* contain, instead of :func:`_no_items_draft`'s blanket "no
    findings" claim contradicting the non-empty tables rendered right below it."""
    return BdTerritoryReportDraft(
        exec_summary=[],
        market_bullets=[],
        competitor_moves=[],
        sections=[],
        recommended_actions=[],
        analyst_note_he=None,
        risks_assumptions_he=(
            "לא נמצאו פריטי שוק חדשים בחלון הזמן שנבדק, כך שלא ניתן היה לבסס המלצות פעולה מנומקות; "
            "ראו את הטבלאות הדטרמיניסטיות בדוח (רכש/מכרזים/מתחרים/כנסים) לפירוט המלא."
        ),
        system_note_he=(
            f"לא זוהו פריטי שוק חדשים בטריטוריה {territory_label(territory)} בחלון הזמן שנבדק, אך "
            f"קיים תוכן רלוונטי בטבלאות הדוח: {counts.context_he()} פירוט מלא בטבלאות בהמשך הדוח."
        ),
        open_points_he=[],
    )


def _our_company_block_he() -> str:
    """Render ``bd_report.our_company`` (BD-1) into the one line the prompt embeds so the model
    always knows which entity it is writing *for*."""
    company = settings().bd_report.our_company
    aliases = f" (גם: {', '.join(company.aliases)})" if company.aliases else ""
    industry = "כן" if company.is_israeli_industry else "לא"
    return f"{company.name}{aliases} | מדינה: {company.country} | תעשייה ביטחונית ישראלית: {industry}"


def _perspective_he() -> str:
    return settings().bd_report.perspective_he


def draft_bd_territory(
    territory: str,
    lookback_days: int,
    items_block: str,
    events_block: str,
    tenders_block: str,
    competitors_block: str,
    conferences_block: str,
    *,
    has_items: bool,
    table_counts: BdTableCounts | None = None,
    role: str = "resident",
    interactive: bool = False,
) -> BdTerritoryReportDraft:
    counts = table_counts or BdTableCounts()
    if not has_items:
        return _tables_only_draft(territory, counts) if counts.total else _no_items_draft()
    prompt = render(
        "report_bd_territory",
        territory_label=territory_label(territory),
        lookback_days=lookback_days,
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        our_company_block=_our_company_block_he(),
        perspective_he=_perspective_he(),
        items_block=wrap_data(items_block, "bd_items", "internal"),
        events_block=wrap_data(events_block, "bd_events", "internal"),
        tenders_block=wrap_data(tenders_block, "bd_tenders", "internal"),
        competitors_block=wrap_data(competitors_block, "bd_competitors", "internal"),
        conferences_block=wrap_data(conferences_block, "bd_conferences", "internal"),
    )
    return chat_structured(
        role,
        BdTerritoryReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.3, "num_predict": _BD_NUM_PREDICT},
    )


def _corrective_retry_or_none(*args: Any, **kwargs: Any) -> BdTerritoryReportDraft | None:
    """:func:`_corrective_retry`, but an invalid/runaway model output returns ``None`` (the caller
    falls through to the deterministic substitute) instead of failing the whole report job."""
    try:
        return _corrective_retry(*args, **kwargs)
    except LLMOutputError as exc:
        log.error("bd_territory_corrective_retry_invalid_output", error=str(exc)[:200])
        return None


def _corrective_retry(
    territory: str,
    lookback_days: int,
    items_block: str,
    events_block: str,
    tenders_block: str,
    competitors_block: str,
    conferences_block: str,
    draft: BdTerritoryReportDraft,
    qa: QAResult,
    *,
    role: str,
    interactive: bool,
) -> BdTerritoryReportDraft:
    prompt = render(
        "report_bd_territory",
        territory_label=territory_label(territory),
        lookback_days=lookback_days,
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        our_company_block=_our_company_block_he(),
        perspective_he=_perspective_he(),
        items_block=wrap_data(items_block, "bd_items", "internal"),
        events_block=wrap_data(events_block, "bd_events", "internal"),
        tenders_block=wrap_data(tenders_block, "bd_tenders", "internal"),
        competitors_block=wrap_data(competitors_block, "bd_competitors", "internal"),
        conferences_block=wrap_data(conferences_block, "bd_conferences", "internal"),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        'ברשימות שסופקו. שים לב: אסור לכתוב "[n]" בטקסט עצמו -- מספרי ההפניה שייכים אך ורק לשדה '
        "cites של כל Sentence:\n" + errors_text
    )
    return chat_structured(
        role,
        BdTerritoryReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": correction},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.2, "num_predict": _BD_NUM_PREDICT},
    )


# --------------------------------------------------------------------------
# structured citation QA (round 3): qa_citations.check() already validates draft.exec_summary/
# draft.sections/draft.analyst_note_he generically (duck-typed, see that module) since this
# schema's exec_summary is a list[Sentence] and it carries no exec_summary_he attribute --
# market_bullets/competitor_moves/recommended_actions[].rationale are BD-specific field names
# qa_citations doesn't know about, so this module validates those itself rather than touching that
# shared module (docs/CONVENTIONS.md rule 6: no cross-module private-name coupling for a
# report-specific shape).
# --------------------------------------------------------------------------


def _valid_ns(citation_items: list[dict[str, Any]]) -> set[int]:
    return {int(it["n"]) for it in citation_items if it.get("n") is not None}


def _check_sentence_group(
    label: str, sentences: list[Sentence], valid_ns: set[int]
) -> tuple[list[str], set[int]]:
    errors: list[str] = []
    bad_refs: set[int] = set()
    for sentence in sentences:
        for n in sentence.cites:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(
                    f'ב{label}: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה — "{sentence.text_he}"'
                )
    return errors, bad_refs


def _run_qa(
    draft: BdTerritoryReportDraft, citation_items: list[dict[str, Any]], *, has_items: bool = False
) -> QAResult:
    base = check(
        draft,
        citation_items,
        exempt_sections=[("סיכונים והנחות", draft.risks_assumptions_he)]
        if draft.risks_assumptions_he
        else None,
    )
    valid_ns = _valid_ns(citation_items)
    errors = list(base.errors)
    bad_refs = set(base.bad_refs)

    mb_errors, mb_bad = _check_sentence_group("בולטי תמונת השוק", draft.market_bullets, valid_ns)
    errors += mb_errors
    bad_refs |= mb_bad

    cm_errors, cm_bad = _check_sentence_group("מהלכי מתחרים", draft.competitor_moves, valid_ns)
    errors += cm_errors
    bad_refs |= cm_bad

    for action in draft.recommended_actions:
        a_errors, a_bad = _check_sentence_group(
            f"נימוק לפעולה '{action.action_he}'", action.rationale, valid_ns
        )
        errors += a_errors
        bad_refs |= a_bad

    if has_items and not draft.exec_summary:
        errors.append("תקציר המנהלים ריק למרות שיש נתוני שוק בטריטוריה זו.")

    return QAResult(
        passed=not errors,
        errors=errors,
        uncited_sentences=base.uncited_sentences,
        bad_refs=sorted(bad_refs),
        duplicate_sentences=base.duplicate_sentences,
    )


# --------------------------------------------------------------------------
# perspective validation (BD-1: recommendations must be ours, never a competitor's)
# --------------------------------------------------------------------------

_COMPETITOR_PROMOTION_VERBS = ("להציג", "לקדם", "לשווק", "מציג", "מקדם", "משווק", "הצגת", "קידום", "שיווק")


def _watchlist_competitor_names(competitors: list[dict[str, Any]]) -> set[str]:
    return {c["name"] for c in competitors if c.get("is_watchlist") and c.get("name")}


def _action_text(action: BdRecommendedAction) -> str:
    rationale_text = " ".join(s.text_he for s in action.rationale)
    return f"{action.action_he} {rationale_text}"


def _action_promoted_competitor(action: BdRecommendedAction, watchlist_names: set[str]) -> str | None:
    """The watchlist competitor name an action illegitimately promotes, or ``None``. An action
    "promotes" a competitor when it both names one of the territory's watchlist competitors *and*
    uses a promotion verb (להציג/לקדם/לשווק and inflections) -- e.g. "להציג יכולת של Shield AI
    בכנס AUSA" when Shield AI is a tracked competitor, not our company."""
    text = _action_text(action)
    if not any(verb in text for verb in _COMPETITOR_PROMOTION_VERBS):
        return None
    for name in watchlist_names:
        if name and name in text:
            return name
    return None


def _perspective_violations(
    draft: BdTerritoryReportDraft, competitors: list[dict[str, Any]]
) -> list[tuple[BdRecommendedAction, str]]:
    watchlist_names = _watchlist_competitor_names(competitors)
    if not watchlist_names:
        return []
    violations = []
    for action in draft.recommended_actions:
        competitor = _action_promoted_competitor(action, watchlist_names)
        if competitor:
            violations.append((action, competitor))
    return violations


def _perspective_corrective_retry(
    territory: str,
    lookback_days: int,
    items_block: str,
    events_block: str,
    tenders_block: str,
    competitors_block: str,
    conferences_block: str,
    draft: BdTerritoryReportDraft,
    violations: list[tuple[BdRecommendedAction, str]],
    *,
    role: str,
    interactive: bool,
) -> BdTerritoryReportDraft:
    """BD-1: one regeneration attempt naming the exact offending action(s) and competitor(s) --
    mirrors the citation ``_corrective_retry``'s "quote the specific problem, ask for a full valid
    draft back" shape."""
    prompt = render(
        "report_bd_territory",
        territory_label=territory_label(territory),
        lookback_days=lookback_days,
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        our_company_block=_our_company_block_he(),
        perspective_he=_perspective_he(),
        items_block=wrap_data(items_block, "bd_items", "internal"),
        events_block=wrap_data(events_block, "bd_events", "internal"),
        tenders_block=wrap_data(tenders_block, "bd_tenders", "internal"),
        competitors_block=wrap_data(competitors_block, "bd_competitors", "internal"),
        conferences_block=wrap_data(conferences_block, "bd_conferences", "internal"),
    )
    company = settings().bd_report.our_company
    offending = "\n".join(
        f'- "{action.action_he}" — מקדמת/מציגה את המתחרה {competitor} במקום את {company.name}'
        for action, competitor in violations
    )
    correction = (
        "הטיוטה הקודמת שלך הפרה את כלל נקודת המבט: פעולה מומלצת אסור שתקדם, תציג או תשווק מתחרה. "
        f"הפעולות הבאות שגויות (המתחרה מוזכר ככזה שאנחנו מקדמים אותו, לא כמתחרה שלנו):\n{offending}\n"
        f"כתוב טיוטה מלאה ותקינה מחדש (JSON לפי הסכמה בלבד) שבה כל פעולה היא פעולה של {company.name} "
        "כלפי השוק/המתחרה (לפנות, להגיב למכרז, להשתתף/להציג בכנס בעצמנו, לנטר את המתחרה) — לעולם לא "
        "פעולה המקדמת את המתחרה עצמו."
    )
    return chat_structured(
        role,
        BdTerritoryReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": correction},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.2, "num_predict": _BD_NUM_PREDICT},
    )


def _drop_perspective_violations(
    draft: BdTerritoryReportDraft, violations: list[tuple[BdRecommendedAction, str]]
) -> BdTerritoryReportDraft:
    """Fallback when the regeneration retry still violates the perspective rule: drop exactly the
    still-offending actions rather than render a recommendation that promotes a competitor (a
    missing recommendation is safer than a wrong one, per docs/CONVENTIONS.md rule 5)."""
    bad = {id(action) for action, _competitor in violations}
    kept = [a for a in draft.recommended_actions if id(a) not in bad]
    return draft.model_copy(update={"recommended_actions": kept})


# --------------------------------------------------------------------------
# BD-1: observed a live model echo the prompt's own illustrative placeholders ("מכרז X", "להציג
# יכולת Y בכנס Z הקרוב") verbatim into real report content even after the prompt explicitly told
# it not to (``report_bd_territory.md``'s field instructions/rule 9) -- this defensive, code-level
# check is a second line of defense that does not depend on model compliance. A bare single capital
# letter right after a tender/conference/client/capability noun is never a real name in this
# report's data (every real entity/tender/conference name from the DB is either Hebrew or a
# multi-character Latin name).
# --------------------------------------------------------------------------

_PLACEHOLDER_ECHO_RE = re.compile(r"(מכרז|כנס|לקוח|יכולת|תוכנית|גורם מזמין|שותף)\s+[A-Z]\b")


def _contains_placeholder_echo(text: str | None) -> bool:
    return bool(text) and bool(_PLACEHOLDER_ECHO_RE.search(text))


def _filter_sentences_echo(sentences: list[Sentence]) -> list[Sentence]:
    return [s for s in sentences if not _contains_placeholder_echo(s.text_he)]


def _strip_placeholder_echoes(draft: BdTerritoryReportDraft) -> BdTerritoryReportDraft:
    """Drop any Sentence/action that echoes one of the prompt's own illustrative placeholders (see
    module note above) -- applied after every draft/retry, before both the perspective gate and the
    citation QA gate. An action whose ``target``/``action_he`` is a placeholder echo is dropped
    entirely; one whose rationale becomes empty after filtering out an echoing sentence is dropped
    too (a recommendation with no surviving grounding is worse than no recommendation)."""
    updates: dict[str, Any] = {}

    new_summary = _filter_sentences_echo(draft.exec_summary)
    if len(new_summary) != len(draft.exec_summary):
        updates["exec_summary"] = new_summary

    new_bullets = _filter_sentences_echo(draft.market_bullets)
    if len(new_bullets) != len(draft.market_bullets):
        updates["market_bullets"] = new_bullets

    new_moves = _filter_sentences_echo(draft.competitor_moves)
    if len(new_moves) != len(draft.competitor_moves):
        updates["competitor_moves"] = new_moves

    new_actions: list[BdRecommendedAction] = []
    actions_changed = False
    for action in draft.recommended_actions:
        if _contains_placeholder_echo(action.action_he) or _contains_placeholder_echo(action.target):
            actions_changed = True
            continue
        cleaned_rationale = _filter_sentences_echo(action.rationale)
        if not cleaned_rationale:
            actions_changed = True
            continue
        if len(cleaned_rationale) != len(action.rationale):
            actions_changed = True
            new_actions.append(action.model_copy(update={"rationale": cleaned_rationale}))
        else:
            new_actions.append(action)
    if actions_changed:
        updates["recommended_actions"] = new_actions

    return draft.model_copy(update=updates) if updates else draft


_MAX_MARKET_BULLETS = 8
_MAX_COMPETITOR_MOVES = 8
_MAX_RECOMMENDED_ACTIONS = 8


def _cap_draft_lengths(draft: BdTerritoryReportDraft) -> BdTerritoryReportDraft:
    """BD-1 defense-in-depth: the prompt asks for "5-8 בדיוק" bullets/actions, but nothing enforces
    it at the schema level (unlike ``exec_summary``'s ``max_length=8``, chosen to avoid a needless
    retry round-trip over the shared, GPU-contended resident model for a soft length preference).
    Truncates to the first ``_MAX_*`` items -- applied after every draft/retry, before both the
    perspective gate and the citation QA gate."""
    updates: dict[str, Any] = {}
    if len(draft.market_bullets) > _MAX_MARKET_BULLETS:
        updates["market_bullets"] = draft.market_bullets[:_MAX_MARKET_BULLETS]
    if len(draft.competitor_moves) > _MAX_COMPETITOR_MOVES:
        updates["competitor_moves"] = draft.competitor_moves[:_MAX_COMPETITOR_MOVES]
    if len(draft.recommended_actions) > _MAX_RECOMMENDED_ACTIONS:
        updates["recommended_actions"] = draft.recommended_actions[:_MAX_RECOMMENDED_ACTIONS]
    return draft.model_copy(update=updates) if updates else draft


# --------------------------------------------------------------------------
# text normalization (D7 round-3 finding 2: doubled ASCII quotes in Hebrew abbreviations)
# --------------------------------------------------------------------------


def _normalize_text_list(values: list[str]) -> list[str]:
    return [normalize_hebrew_punctuation(v) or v for v in values]


def _normalize_sentence(sentence: Sentence) -> Sentence:
    normalized = normalize_hebrew_punctuation(sentence.text_he)
    if normalized == sentence.text_he:
        return sentence
    return sentence.model_copy(update={"text_he": normalized or sentence.text_he})


def _normalize_sentences(sentences: list[Sentence]) -> list[Sentence]:
    return [_normalize_sentence(s) for s in sentences]


def _normalize_draft_text(draft: BdTerritoryReportDraft) -> BdTerritoryReportDraft:
    """Round-3 (D7 finding 2, docs/qa/loop/round_1_judge.md): apply the shared Hebrew-punctuation
    normaliser (``eoa.report.textnorm.normalize_hebrew_punctuation``) to every LLM-authored text
    field on ``draft`` -- collapses a doubled ASCII quote and converts a lone ASCII quote/
    apostrophe between Hebrew letters to the correct gershayim/geresh mark (e.g. observed live:
    'ארה""ב' -> 'ארה״ב'). Applied once, as late as possible (after every other draft mutation:
    perspective gate, citation QA, length capping, placeholder stripping), so no upstream step
    needs to know about it and normalization can never interfere with citation-marker matching
    (``[n]`` tokens are untouched -- digits/brackets are never adjacent to Hebrew letters in a way
    that would trigger either substitution)."""
    return draft.model_copy(
        update={
            "exec_summary": _normalize_sentences(draft.exec_summary),
            "market_bullets": _normalize_sentences(draft.market_bullets),
            "competitor_moves": _normalize_sentences(draft.competitor_moves),
            "recommended_actions": [
                a.model_copy(
                    update={
                        "action_he": normalize_hebrew_punctuation(a.action_he),
                        "rationale": _normalize_sentences(a.rationale),
                        "owner_role_he": normalize_hebrew_punctuation(a.owner_role_he),
                        "timing_he": normalize_hebrew_punctuation(a.timing_he),
                        "target": normalize_hebrew_punctuation(a.target) or a.target,
                    }
                )
                for a in draft.recommended_actions
            ],
            "risks_assumptions_he": normalize_hebrew_punctuation(draft.risks_assumptions_he),
            "open_points_he": _normalize_text_list(draft.open_points_he),
        }
    )


def _normalize_cell(value: Any) -> Any:
    return normalize_hebrew_punctuation(value) if isinstance(value, str) else value


def _normalize_table(table: dict[str, Any] | None) -> dict[str, Any] | None:
    """Same normalization, applied to a rendered deterministic table's string cells/note (the
    other half of D7 finding 2's "prose *and table cells*") -- headers are static Hebrew strings
    authored in this module and never need it, so only ``rows``/``note_he`` are touched."""
    if table is None:
        return None
    normalized = dict(table)
    if table.get("note_he"):
        normalized["note_he"] = normalize_hebrew_punctuation(table["note_he"])
    normalized["rows"] = [[_normalize_cell(v) for v in row] for row in table.get("rows") or []]
    return normalized


# --------------------------------------------------------------------------
# conference-date prose correction (D7 round-3 finding 1: dates must be deterministic)
# --------------------------------------------------------------------------

_CONF_DATE_MENTION_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CONF_DATE_MENTION_WINDOW = 40


def _conferences_date_lookup(data: dict[str, Any]) -> dict[str, dt.date]:
    """``{conference name: real conferences.start_date}`` for every conference
    ``collect_conferences_for_territory`` returned (territory + international) -- the ground truth
    the rendered table already reads directly off the DB row; used here to also check/correct any
    free-prose mention of the same date."""
    lookup: dict[str, dt.date] = {}
    for c in (data.get("territory") or []) + (data.get("international") or []):
        name, start = c.get("name"), c.get("start_date")
        if name and start:
            lookup[name] = start
    return lookup


def _correct_conference_date_mentions(
    text: str, conferences_by_name: dict[str, dt.date]
) -> tuple[str, list[str]]:
    """D7 round-3 finding 1: defense-in-depth for LLM-authored prose (each exec-summary/market-
    bullet/competitor-move ``Sentence.text_he``) that paraphrases a tracked conference's date. The
    rendered conferences *table* is already 100% DB-sourced by construction
    (``conferences_table``/``format_conferences_block`` read ``start_date``/``end_date`` straight
    off the ``conferences`` row -- never synthesized) -- but nothing stops the model from also
    mentioning a date in a Sentence's prose, which it can get wrong (observed: AUSA 2026 cited as
    starting 2026-10-01 in prose vs. the DB's 2026-10-12). Scans for every tracked conference name
    followed within a short window by an ISO date; a mismatching date is replaced with the real one
    (corrected, never merely dropped -- a description with a corrected date is more useful than
    silently deleting the whole sentence) and logged. Returns ``(possibly-corrected text,
    correction log lines)``."""
    if not text or not conferences_by_name:
        return text, []
    corrections: list[str] = []
    search_from = 0
    while True:
        idx = -1
        matched_name: str | None = None
        for name in conferences_by_name:
            pos = text.find(name, search_from)
            if pos != -1 and (idx == -1 or pos < idx):
                idx, matched_name = pos, name
        if idx == -1 or matched_name is None:
            break
        window = text[idx : min(len(text), idx + len(matched_name) + _CONF_DATE_MENTION_WINDOW)]
        m = _CONF_DATE_MENTION_RE.search(window)
        if m:
            real_start = conferences_by_name[matched_name]
            mentioned = dt.date.fromisoformat(m.group(1))
            if mentioned != real_start:
                real_str = fmt_date(real_start)
                abs_start, abs_end = idx + m.start(1), idx + m.end(1)
                corrections.append(
                    f"{matched_name}: '{m.group(1)}' -> '{real_str}' (DB start_date={real_start})"
                )
                text = text[:abs_start] + real_str + text[abs_end:]
        search_from = idx + len(matched_name)
    return text, corrections


def _correct_sentence_conference_dates(
    sentences: list[Sentence], conferences_by_name: dict[str, dt.date]
) -> tuple[list[Sentence], list[str]]:
    new_sentences: list[Sentence] = []
    all_corrections: list[str] = []
    changed = False
    for sentence in sentences:
        corrected_text, corrections = _correct_conference_date_mentions(sentence.text_he, conferences_by_name)
        if corrections:
            changed = True
            all_corrections += corrections
            new_sentences.append(sentence.model_copy(update={"text_he": corrected_text}))
        else:
            new_sentences.append(sentence)
    return (new_sentences if changed else sentences), all_corrections


def _correct_draft_conference_dates(
    draft: BdTerritoryReportDraft, conferences_by_name: dict[str, dt.date], *, territory: str
) -> BdTerritoryReportDraft:
    if not conferences_by_name:
        return draft
    all_corrections: list[str] = []
    new_summary, corr = _correct_sentence_conference_dates(draft.exec_summary, conferences_by_name)
    all_corrections += corr
    new_bullets, corr = _correct_sentence_conference_dates(draft.market_bullets, conferences_by_name)
    all_corrections += corr
    new_moves, corr = _correct_sentence_conference_dates(draft.competitor_moves, conferences_by_name)
    all_corrections += corr
    if not all_corrections:
        return draft
    log.warning("bd_territory_conference_date_corrected", territory=territory, corrections=all_corrections)
    return draft.model_copy(
        update={"exec_summary": new_summary, "market_bullets": new_bullets, "competitor_moves": new_moves}
    )


# --------------------------------------------------------------------------
# no-activity marker (D7 round-3 finding 3: honest "no activity" reports must not fail
# actions_table_nonempty)
# --------------------------------------------------------------------------

#: Machine-detectable marker sentence (``eoa.qa.d7_bd_report`` matches on this exact text) for a
#: territory where the market-item collection AND every deterministic table (procurement events,
#: tenders, forecasts, competitors, conferences) all came back empty for the lookback window --
#: distinct from "the LLM/data pipeline failed to produce actions", which is a real gap, not an
#: honest "nothing to report".
NO_ACTIVITY_MARKER_HE = "לא זוהתה פעילות רלוונטית בטריטוריה בחלון זה — אין פעולות מומלצות."


def _no_activity_actions_section_he(watchlist_checked: list[str]) -> str:
    checked = "; ".join(watchlist_checked) if watchlist_checked else "לא הוגדרו מתחרי מעקב לטריטוריה זו"
    return f"{NO_ACTIVITY_MARKER_HE} מתחרי מעקב שנבדקו בטריטוריה זו: {checked}."


# --------------------------------------------------------------------------
# round-3 (2026-09-06, D7 judge finding): deterministic substitute synthesis, mirroring
# eoa.report.daily/weekly's own two-failure fallback -- when the LLM-drafted narrative still fails
# citation QA after one corrective retry, replace it with a deterministic (no LLM) executive
# summary built straight from the territory's already-numbered data, plus the same deterministic
# candidate actions this module already falls back to when the model produces no actions at all
# (see :func:`_deterministic_candidate_actions`, defined further below and reused here).
# --------------------------------------------------------------------------

_FALLBACK_TOP_ITEMS = 6
_FALLBACK_TOP_EVENTS = 4
_FALLBACK_TEXT_TRUNC_CHARS = 220


def _fallback_truncate(text: str | None, limit: int = _FALLBACK_TEXT_TRUNC_CHARS) -> str:
    text = (text or "").strip()
    if not text:
        return "—"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fallback_top_item_sentences(items: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    sentences: list[Sentence] = []
    for it in items[:limit]:
        n = it.get("n")
        if n is None:
            continue
        title = it.get("title") or "—"
        so_what = _fallback_truncate(it.get("so_what_he") or it.get("summary_he"))
        sentences.append(Sentence(text_he=f"{title}: {so_what}", cites=[int(n)]))
    return sentences


def _fallback_event_sort_key(ev: dict[str, Any]) -> dt.date:
    return ev.get("date") or ev.get("published_at") or dt.date.min


def _fallback_event_sentences(events: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    ranked = sorted(
        (ev for ev in events if ev.get("n") is not None), key=_fallback_event_sort_key, reverse=True
    )
    sentences: list[Sentence] = []
    for ev in ranked[:limit]:
        payload = ev.get("payload_need_he") or ev.get("platform_he") or "—"
        sentences.append(
            Sentence(
                text_he=(
                    f"{ev.get('platform_he') or '—'}: {ev.get('buyer') or '—'} מול "
                    f"{ev.get('vendor') or '—'}, צורך {payload}, בתאריך "
                    f"{fmt_date(ev.get('date') or ev.get('published_at'))}."
                ),
                cites=[int(ev["n"])],
            )
        )
    return sentences


def _deterministic_fallback_draft(
    territory: str,
    items: list[dict[str, Any]],
    events: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
    tenders_data: dict[str, Any],
    conferences_data: dict[str, Any],
) -> BdTerritoryReportDraft:
    """Round 3 (2026-09-06, D7 judge finding, ``docs/qa/loop/round_2_judge.md``): mirrors
    ``eoa.report.daily``/``eoa.report.weekly``'s own two-failure deterministic substitute -- when
    the LLM-drafted narrative still fails citation QA after one corrective retry, replace it with a
    deterministic (no LLM) executive summary built straight from the territory's already-numbered
    data (the top market items and the window's notable procurement/platform events), plus the same
    deterministic candidate actions this module already falls back to when the model produces no
    actions at all. Every sentence cites a real, already-registered item ``n``, so this cannot
    itself fail :func:`_run_qa` (the caller still runs it once anyway, defensively)."""
    sentences: list[Sentence] = []
    sentences.extend(_fallback_top_item_sentences(items, limit=_FALLBACK_TOP_ITEMS))
    sentences.extend(_fallback_event_sentences(events, limit=_FALLBACK_TOP_EVENTS))
    actions = _deterministic_candidate_actions(competitors, tenders_data, conferences_data, events)
    return BdTerritoryReportDraft(
        exec_summary=sentences,
        market_bullets=[],
        competitor_moves=[],
        sections=[],
        recommended_actions=actions,
        analyst_note_he=None,
        risks_assumptions_he="",
        system_note_he=(
            "תקציר מובנה אוטומטית (ללא ניסוח מודל): הטיוטה הטקסטואלית של דוח המיקוד לטריטוריה "
            f"{territory_label(territory)} לא עברה את בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן "
            "ניסוח המודל הושמט במלואו. התקציר שלעיל ורשימת הפעולות המומלצות (אם קיימת) הופקו ישירות "
            "מנתוני מסד הנתונים (ללא ניסוח חופשי של מודל) -- כל משפט כאן מצוטט למקורו. הטבלאות "
            "הדטרמיניסטיות (רכש/פלטפורמות, מכרזים, מתחרים, כנסים) ונספח המקורות שלהלן אינם מושפעים "
            "ומוצגים במלואם."
        ),
        open_points_he=[],
    )


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

_PRIORITY_LABEL_HE = {"H": "גבוהה", "M": "בינונית", "L": "נמוכה"}


#: D7 round-1 fix (docs/qa/loop/round_1_fixes.md, ``actions_table_nonempty``): title/note used
#: only for the deterministic fallback table (:func:`_deterministic_candidate_actions`) -- kept
#: visually distinct from the normal LLM-authored "נקודות כניסה ופעולות מומלצות" table so a reader
#: (and ``eoa.qa.d7_bd_report``'s ``no_competitor_promotion_language``/heading checks, which match
#: on "פעולות"/"המלצ" regardless of which title is used) can tell the two apart.
_DETERMINISTIC_ACTIONS_TITLE_HE = "פעולות מוצעות (נגזרות מהנתונים)"
_DETERMINISTIC_ACTIONS_NOTE_HE = (
    "הטיוטה האנליטית (הנרטיב) לא עברה את בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן הפעולות שלהלן "
    "נגזרו ישירות מהנתונים הדטרמיניסטיים (רכש, מכרזים, מתחרים, כנסים) ללא ניסוח אנליסט."
)


def _deterministic_candidate_actions(
    competitors: list[dict[str, Any]],
    tenders_data: dict[str, Any],
    conferences_data: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[BdRecommendedAction]:
    """D7 round-1 fix: when the LLM produced no cited recommended actions even after both retries
    (perspective gate + citation QA), :func:`build_bd_territory` falls back to a small set of
    candidate actions built directly from the same deterministic tables the report already
    renders -- every one traceable to a real row (``[n]`` into the citation registry, already
    attached to each row by ``_attach_win_citations``/``_extend_registry_with_tenders``/
    ``_extend_registry_with_conferences``/``_extend_registry_with_source_items`` by the time this
    runs), never invented text. Order: competitor wins, upcoming territory conferences, open/
    unknown RFIs, then the single top (most recent) platform event -- matches the task brief. A row
    with no registered ``n`` yet (should not happen by the time this runs, but defensive) is
    skipped rather than emitted as an uncited ``Sentence``, which the schema itself would reject."""
    actions: list[BdRecommendedAction] = []

    for c in competitors:
        for win in c.get("recent_wins") or []:
            n = win.get("n")
            if n is None:
                continue
            program = win.get("program") or win.get("title") or "—"
            actions.append(
                BdRecommendedAction(
                    action_he=f"לבחון תגובה תחרותית ל-{c['name']} ב-{program}",
                    priority="M",
                    rationale=[
                        Sentence(
                            text_he=f"{c['name']} זוהתה כזוכה באירוע עסקי בטריטוריה זו בחלון הזמן שנבדק.",
                            cites=[int(n)],
                        )
                    ],
                    owner_role_he="פיתוח עסקי",
                    timing_he="רבעון הקרוב",
                    target=c["name"],
                    confidence=0.6,
                )
            )

    for conf in conferences_data.get("territory") or []:
        n = conf.get("n")
        if n is None:
            continue
        start, end = conf.get("start_date"), conf.get("end_date")
        dates = " - ".join(fmt_date(d) for d in (start, end) if d)
        actions.append(
            BdRecommendedAction(
                action_he=f"להיערך ל-{conf.get('name') or '—'} ({dates or '—'})",
                priority="M",
                rationale=[Sentence(text_he="כנס בטריטוריה זו בחלון 12 החודשים הקרובים.", cites=[int(n)])],
                owner_role_he="שיווק",
                timing_he="תוך חצי שנה",
                target=conf.get("name") or "",
                confidence=0.6,
            )
        )

    for tender in tenders_data.get("tenders") or []:
        n = tender.get("n")
        if n is None:
            continue
        actions.append(
            BdRecommendedAction(
                action_he=f"לבחון מענה ל-{tender.get('title') or '—'}",
                priority="H",
                rationale=[Sentence(text_he="מכרז/RFI פתוח או במעמד לא ידוע בטריטוריה זו.", cites=[int(n)])],
                owner_role_he="פיתוח עסקי",
                timing_he="מיידי",
                target=tender.get("title") or "",
                confidence=0.65,
            )
        )

    if events:
        top = events[0]
        n = top.get("n")
        if n is not None:
            payload = top.get("payload_need_he") or top.get("platform_he") or "—"
            actions.append(
                BdRecommendedAction(
                    action_he=f"לפנות ל-{top.get('buyer') or '—'} בנושא {payload}",
                    priority="H",
                    rationale=[
                        Sentence(
                            text_he="אירוע הרכש/פלטפורמה המשמעותי ביותר שזוהה בטריטוריה זו בחלון הזמן.",
                            cites=[int(n)],
                        )
                    ],
                    owner_role_he="מכירות",
                    timing_he="מיידי",
                    target=top.get("buyer") or "",
                    confidence=0.65,
                )
            )

    return actions


def recommended_actions_table(
    draft: BdTerritoryReportDraft, *, deterministic: bool = False
) -> dict[str, Any] | None:
    if not draft.recommended_actions:
        return None
    headers = ["עדיפות", "פעולה", "נימוק", "אחראי", "תזמון"]
    order = {"H": 0, "M": 1, "L": 2}
    actions = sorted(draft.recommended_actions, key=lambda a: order.get(a.priority, 9))
    rows = [
        [
            _PRIORITY_LABEL_HE.get(a.priority, a.priority),
            a.action_he,
            _render_sentences(a.rationale),
            a.owner_role_he,
            a.timing_he,
        ]
        for a in actions
    ]
    title_he = _DETERMINISTIC_ACTIONS_TITLE_HE if deterministic else "נקודות כניסה ופעולות מומלצות"
    table: dict[str, Any] = {"title_he": title_he, "headers": headers, "rows": rows}
    if deterministic:
        table["note_he"] = _DETERMINISTIC_ACTIONS_NOTE_HE
    return table


# --------------------------------------------------------------------------
# persistence / paths
# --------------------------------------------------------------------------


def _report_path(territory: str, period_end: dt.date, ext: str) -> Path:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    code = normalize_country(territory).lower()
    return out_dir / f"bd_{code}_{period_end.isoformat()}.{ext}"


def _persist_report(
    territory: str,
    period_start: dt.date,
    period_end: dt.date,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    items: list[dict[str, Any]],
    qa: QAResult,
) -> int:
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
    }
    item_ids = [it["id"] for it in items if it.get("id") is not None and it["id"] > 0]
    sql = """
        INSERT INTO reports (kind, territory, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report)
        VALUES ('bd_territory', %(territory)s, %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s,
                %(items)s, %(qa_passed)s, %(qa_report)s)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "territory": normalize_country(territory),
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
    log.info("bd_territory_report_persisted", report_id=report_id, territory=territory, qa_passed=qa.passed)
    return report_id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_bd_territory(
    territory: str,
    lookback_days: int = 90,
    *,
    period_end: dt.date | None = None,
    role: str = "resident",
    interactive: bool = False,
) -> ReportPaths:
    """Collect -> draft -> perspective-gate -> QA-gate -> render docx/md/html -> persist, for one
    territory. BD-1 (docs/qa/findings_Q3_r2.md): the perspective gate runs *before* the citation
    QA gate, since a regenerated/edited draft still needs the normal citation check afterward."""
    code = normalize_country(territory)
    start, end = lookback_range(lookback_days, period_end)

    items = collect_market_items(code, start, end)
    events = collect_platform_events(code, start, end)
    tenders_data = collect_tenders_and_forecasts(code)
    competitors = collect_active_competitors(code, [it["id"] for it in items], start, end)
    conferences_data = collect_conferences_for_territory(code)
    dormant_competitors = collect_dormant_watchlist_competitors(code, {c["name"] for c in competitors})

    citation_items = list(items)
    _extend_registry_with_source_items(citation_items, events)
    _extend_registry_with_tenders(citation_items, tenders_data)
    _extend_registry_with_conferences(citation_items, conferences_data)
    _attach_win_citations(citation_items, competitors)

    items_block = format_market_items_block(select_bd_items_for_prompt(items))
    events_block = (
        format_market_items_block([])
        if not events
        else "\n".join(
            f"[{ev.get('n', '—')}] {ev.get('platform_he')} | {ev.get('buyer')} <- {ev.get('vendor')} | "
            f"{fmt_date(ev.get('date') or ev.get('published_at'))}"
            for ev in events
        )
        or "לא זוהו אירועי רכש/פלטפורמות בטריטוריה זו בחלון הזמן."
    )
    tenders_block = format_tenders_block(tenders_data)
    competitors_block = format_competitors_block(competitors)
    conferences_block = format_conferences_block(conferences_data)

    table_counts = BdTableCounts(
        events=len(events),
        tenders=len(tenders_data.get("tenders") or []),
        forecasts=len(tenders_data.get("forecasts") or []),
        competitors=len(competitors),
        conferences=len(conferences_data.get("territory") or []),
    )

    llm_draft_failed = False
    try:
        draft = draft_bd_territory(
            code,
            lookback_days,
            items_block,
            events_block,
            tenders_block,
            competitors_block,
            conferences_block,
            has_items=bool(items),
            table_counts=table_counts,
            role=role,
            interactive=interactive,
        )
    except LLMOutputError as exc:
        # Round-3 (job 97): a draft that never validated (runaway/EOF JSON even after
        # chat_structured's own corrective retry) must not fail the job -- it gets the same
        # deterministic, cited substitute as a twice-failed QA, and skips every further LLM pass.
        log.error(
            "bd_territory_draft_invalid_output_using_deterministic_fallback",
            territory=code,
            error=str(exc)[:200],
        )
        draft = _deterministic_fallback_draft(
            code, items, events, competitors, tenders_data, conferences_data
        )
        llm_draft_failed = True
    draft = _cap_draft_lengths(_strip_placeholder_echoes(draft))

    if items and not llm_draft_failed:
        violations = _perspective_violations(draft, competitors)
        if violations:
            log.warning(
                "bd_territory_perspective_violation_retrying",
                territory=code,
                actions=[a.action_he for a, _competitor in violations],
            )
            draft = _perspective_corrective_retry(
                code,
                lookback_days,
                items_block,
                events_block,
                tenders_block,
                competitors_block,
                conferences_block,
                draft,
                violations,
                role=role,
                interactive=interactive,
            )
            draft = _cap_draft_lengths(_strip_placeholder_echoes(draft))
            violations = _perspective_violations(draft, competitors)
            if violations:
                log.error(
                    "bd_territory_perspective_violation_dropping",
                    territory=code,
                    actions=[a.action_he for a, _competitor in violations],
                )
                draft = _drop_perspective_violations(draft, violations)

    qa = _run_qa(draft, citation_items, has_items=bool(items))

    if not qa.passed and items and not llm_draft_failed:
        log.warning("bd_territory_qa_failed_retrying", territory=code, errors=qa.errors[:10])
        draft = _corrective_retry_or_none(
            code,
            lookback_days,
            items_block,
            events_block,
            tenders_block,
            competitors_block,
            conferences_block,
            draft,
            qa,
            role=role,
            interactive=interactive,
        )
        if draft is None:
            draft = _deterministic_fallback_draft(
                code, items, events, competitors, tenders_data, conferences_data
            )
            llm_draft_failed = True
        draft = _cap_draft_lengths(_strip_placeholder_echoes(draft))
        qa = _run_qa(draft, citation_items, has_items=bool(items))

    used_deterministic_actions = False

    if not qa.passed and items:
        # Round 3 (2026-09-06, D7 judge finding, docs/qa/loop/round_2_judge.md): two failures
        # (initial draft + one corrective retry) replace the narrative with a deterministic, cited
        # substitute synthesis built straight from the data -- mirrors eoa.report.daily/weekly.
        # The original QA errors are kept in `qa_report` (persisted below) for the analyst to
        # review; `qa.passed` stays False either way.
        log.error(
            "bd_territory_qa_failed_twice_using_deterministic_fallback", territory=code, errors=qa.errors[:10]
        )
        original_errors = qa
        draft = _deterministic_fallback_draft(
            code, items, events, competitors, tenders_data, conferences_data
        )
        used_deterministic_actions = bool(draft.recommended_actions)
        fallback_qa = _run_qa(draft, citation_items, has_items=bool(items))
        if not fallback_qa.passed:
            log.error(
                "bd_territory_fallback_draft_failed_citation_check",
                territory=code,
                errors=fallback_qa.errors[:10],
            )
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    # D7 round-3 finding 2: normalize Hebrew punctuation (doubled ASCII quotes -> gershayim/geresh)
    # on every LLM-authored text field before it feeds any of the extra-section bodies built below
    # (which quote draft.market_bullets/competitor_moves/risks_assumptions_he verbatim) -- applied
    # again, below, after the deterministic-actions fallback so that path is covered too.
    draft = _normalize_draft_text(draft)
    # D7 round-3 finding 1: correct any prose mention of a tracked conference's date against the
    # DB (the rendered table itself is already 100% DB-sourced by construction -- see
    # conferences_table/format_conferences_block).
    draft = _correct_draft_conference_dates(draft, _conferences_date_lookup(conferences_data), territory=code)

    extra_sections: list[dict[str, Any]] = []
    if draft.market_bullets:
        extra_sections.append(
            {
                "title_he": "תמונת שוק בטריטוריה",
                "body_he": _sentences_bullets_text(draft.market_bullets),
                "position": "after_summary",
            }
        )
    if draft.competitor_moves:
        extra_sections.append(
            {
                "title_he": "מהלכי מתחרים בטריטוריה",
                "body_he": _sentences_bullets_text(draft.competitor_moves),
                "position": "after_summary",
            }
        )
    if len(competitors) < 3 and dormant_competitors:
        extra_sections.append(
            {
                "title_he": "מתחרי מעקב ללא פעילות בחלון הזמן",
                "body_he": (
                    "לא נמצאה פעילות (אזכור, קשר גרפי או אירוע) בחלון הזמן שנבדק עבור מתחרי רשימת "
                    f"המעקב הבאים: {', '.join(dormant_competitors)}."
                ),
                "position": "after_summary",
            }
        )
    if draft.risks_assumptions_he:
        extra_sections.append(
            {"title_he": "סיכונים והנחות", "body_he": draft.risks_assumptions_he, "position": "after_outlook"}
        )
    # A16 (מעקב רכישות ושותפויות) + A17 (מחירי ייחוס למטע"דים): data-driven, model-free sections;
    # each extends ``citation_items`` in place so its [n] marks resolve in the source appendix, and
    # each is skipped entirely (never a placeholder) when it has nothing to show or its DB read
    # fails -- the BD report must not depend on either feature's tables being populated.
    try:
        from eoa.report.acquisition_watch import SECTION_TITLE_HE as _ACQ_TITLE_HE
        from eoa.report.acquisition_watch import acquisition_watch_section_md

        with connection() as conn:
            acq_body = acquisition_watch_section_md(conn, start, end, citation_items)
        if acq_body.strip():
            extra_sections.append(
                {"title_he": _ACQ_TITLE_HE, "body_he": acq_body, "position": "after_outlook"}
            )
    except Exception as exc:  # optional section, never blocks the report
        log.warning("bd_territory_acquisition_section_failed", territory=code, error=str(exc)[:160])
    try:
        from eoa.payloads.report_section import payload_price_table_md

        with connection() as conn:
            price_body = payload_price_table_md(code, conn)
        # the helper renders its own "## ..." heading; the section renderer adds the title itself
        price_body = chr(10).join(ln for ln in price_body.splitlines() if not ln.startswith("## ")).strip()
        if price_body and "|" in price_body:
            extra_sections.append(
                {
                    "title_he": 'מחירי ייחוס למטע"דים בטריטוריה',
                    "body_he": price_body,
                    "position": "after_outlook",
                }
            )
    except Exception as exc:
        log.warning("bd_territory_payload_prices_failed", territory=code, error=str(exc)[:160])

    # D7 round-1 fix (docs/qa/loop/round_1_fixes.md, ``actions_table_nonempty``): if the LLM's
    # recommended actions ended up empty by this point -- never drafted any (has_items=False),
    # dropped entirely by the perspective gate (_drop_perspective_violations), or the deterministic
    # fallback above itself found nothing to build actions from -- ship a deterministic candidate
    # list built straight from the same tables already rendered below, rather than an empty BD
    # recommendations section.
    if not draft.recommended_actions:
        deterministic_actions = _deterministic_candidate_actions(
            competitors, tenders_data, conferences_data, events
        )
        if deterministic_actions:
            log.warning(
                "bd_territory_actions_deterministic_fallback",
                territory=code,
                n_actions=len(deterministic_actions),
            )
            draft = draft.model_copy(update={"recommended_actions": deterministic_actions})
            used_deterministic_actions = True
    draft = _normalize_draft_text(draft)  # covers text injected by the deterministic fallback too

    # D7 round-3 finding 3: a genuine "no activity in this territory in this window" report (no
    # market items AND every deterministic table empty -- the bd_kr case) has nothing for either
    # the LLM or the deterministic fallback above to produce, so the recommendations section would
    # otherwise be silently omitted entirely (failing ``actions_table_nonempty`` despite the report
    # being an honest, correctly-empty one). Render an explicit, machine-detectable marker instead
    # -- naming every watchlist competitor that was actually checked -- so an analyst (and the
    # deterministic QA check) can tell "checked, nothing found" apart from "the pipeline failed to
    # produce a recommendations section at all".
    if not items and table_counts.total == 0 and not draft.recommended_actions:
        watchlist_checked = dormant_competitors or sorted({c["name"] for c in competitors})
        extra_sections.append(
            {
                "title_he": "נקודות כניסה ופעולות מומלצות",
                "body_he": _no_activity_actions_section_he(watchlist_checked),
                "position": "after_outlook",
            }
        )

    tables: list[dict[str, Any]] = []
    for tbl in (
        platform_events_table(events),
        tenders_table(tenders_data),
        competitors_table(competitors),
        conferences_table(conferences_data),
        recommended_actions_table(draft, deterministic=used_deterministic_actions),
    ):
        if tbl is not None:
            tables.append(_normalize_table(tbl))

    # A14 (פטנטים ו-IP, 2026-09-06): competitor IP position in the territory, same additive
    # mechanism as the tables above. A failure here must never break the BD report.
    try:
        from eoa.patents.report_section import collect_patents_bd, patents_bd_extra_section, patents_bd_table

        patents_bd_data = collect_patents_bd(code)
        extra_sections.append(patents_bd_extra_section(patents_bd_data))
        patents_bd_tbl = patents_bd_table(patents_bd_data)
        if patents_bd_tbl:
            tables.append(patents_bd_tbl)
    except Exception as exc:
        log.warning("bd_report_patents_section_failed", error=str(exc)[:160])

    title_text = TITLE_TEMPLATE_HE.format(territory=territory_label(code))
    docx_path = _report_path(code, end, "docx")
    md_path = _report_path(code, end, "md")
    html_path = _report_path(code, end, "html")

    doc = build_docx(
        draft,
        citation_items,
        [],
        period_end=end,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections,
        tables=tables or None,
        include_toc=True,
    )
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft,
        citation_items,
        [],
        period_end=end,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections,
        tables=tables or None,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft,
        citation_items,
        [],
        period_end=end,
        qa=qa,
        title_text=title_text,
        extra_sections=extra_sections,
        tables=tables or None,
        include_toc=True,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(code, start, end, docx_path, md_path, html_path, citation_items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa, territory=code)
