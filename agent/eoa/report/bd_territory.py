"""Stage: report/bd_territory — A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה".

For one territory (ISO-2 country code or recognized region code -- US, IL, EU, UK/GB, DE, FR,
IN, KR, JP, AU, PL, GR, AE, SA, ...; normalized via ``eoa.report.geography.normalize_country``)
and a lookback window (default 90 days), builds an analyst-grade Hebrew report covering:

1. market picture (``collect_market_items`` -> LLM synthesis, ``market_bullets_he``);
2. procurement/platforms (``collect_platform_events``, deterministic, matched against
   ``eoa.tenders.platform_payloads.yaml`` via ``eoa.tenders.forecast.load_platform_payloads``);
3. tenders/forecasts (``collect_tenders_and_forecasts``, deterministic);
4. active competitors (``collect_active_competitors``, deterministic);
5. upcoming conferences (``collect_conferences_for_territory``, deterministic, best-effort city
   matching since ``conferences`` has no country column);
6. recommended entry points/actions (LLM, structured -- ``BdAction``);
7. risks/assumptions (LLM, citation-exempt like ``outlook_he`` elsewhere).

Pipeline mirrors ``eoa.report.weekly``: collect -> draft (resident model) -> ``qa_citations.check``
-> one corrective retry on failure -> strip any still-uncited sentences -> render docx/md/html via
``eoa.report.docx_builder``'s additive ``extra_sections``/``tables`` hooks -> persist a ``reports``
row (``kind='bd_territory'``, ``territory=<code>``).
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
from eoa.llm.schemas.reports import BdAction, BdTerritoryReportDraft
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
from eoa.report.qa_citations import QAResult, check, citations_in, split_sentences

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

TITLE_TEMPLATE_HE = "דוח מיקוד לפיתוח עסקי — {territory}"

_INSCOPE_LEVELS = ("red", "orange", "yellow")

# events.kind CHECK (db/migrations/versions/0001_core.py) doesn't have separate
# "contract"/"award"/"acquisition"/"trial" values -- these are the closest real enum members
# (m_and_a for acquisitions, test for trials/demonstrations).
_PROCUREMENT_EVENT_KINDS = ("contract_award", "m_and_a", "deployment", "test")

_ISRAELI_INDUSTRY_NAMES = {"Elbit", "Rafael", "IAI", "Controp"}

# Best-effort conference-city -> territory-code heuristic (conferences has no country column --
# see docs/adr, ``eoa.conferences.tracker``). Deliberately small: only the handful of cities that
# actually host EO/IR-relevant defense conferences the tracker seeds. Case-insensitive substring
# match against ``city``/``venue``/``name``.
_CONFERENCE_CITY_TERRITORY: dict[str, str] = {
    "washington": "US", "national harbor": "US", "orlando": "US", "las vegas": "US",
    "huntsville": "US", "arlington": "US", "tampa": "US", "san diego": "US",
    "tel aviv": "IL", "herzliya": "IL", "jerusalem": "IL",
    "london": "GB", "farnborough": "GB",
    "paris": "FR",
    "berlin": "DE",
    "abu dhabi": "AE", "dubai": "AE",
    "seoul": "KR", "goyang": "KR",
    "tokyo": "JP",
    "singapore": "SG",
    "athens": "GR",
    "warsaw": "PL",
    "riyadh": "SA",
    "canberra": "AU", "adelaide": "AU",
    "new delhi": "IN", "bengaluru": "IN",
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
            str(x) for x in (ev.get("title"), ev.get("item_title"), ev.get("clean_text"), ev.get("summary_he")) if x
        )
        match = next((p for p in platforms if p.matches(text)), None)
        parties = ev.get("parties") or []
        vendor = next((p for p in parties if p != ev.get("customer")), None) or (parties[0] if parties else None)
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
    tenders = _fetchall(
        "SELECT * FROM tenders WHERE status IN ('open', 'unknown') "
        "ORDER BY deadline ASC NULLS LAST, relevance DESC NULLS LAST, id DESC LIMIT 500"
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


def collect_active_competitors(
    territory: str, market_item_ids: list[int], period_start: dt.date, period_end: dt.date, *, limit: int = 15
) -> list[dict[str, Any]]:
    """Company entities (relevance >= 0.4) active in the territory -- either headquartered there
    (``entities.country``) or mentioned by one of the territory's market items in the window --
    with their recent wins (``contract_award`` events among those same items) and an
    ``is_israeli_industry`` flag (Elbit/Rafael/IAI/Controp, per config/watchlist.yaml canonical
    names) for the "זווית התעשייה הישראלית" angle."""
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

    active = [
        c for c in companies if normalize_country(c.get("country")) == code or c["name"] in mentions_by_name
    ]
    active.sort(key=lambda c: mentions_by_name.get(c["name"], 0), reverse=True)
    active = active[:limit]

    out: list[dict[str, Any]] = []
    for c in active:
        wins: list[dict[str, Any]] = []
        if market_item_ids:
            wins = _fetchall(
                "SELECT id, item_id, title, date, amount_usd, currency, customer, program FROM events "
                "WHERE item_id = ANY(%(ids)s) AND kind = 'contract_award' "
                "AND (%(name)s = customer OR %(name)s = ANY(parties)) "
                "ORDER BY date DESC NULLS LAST LIMIT 5",
                {"ids": market_item_ids, "name": c["name"]},
            )
        out.append(
            {
                "entity_id": c["id"],
                "name": c["name"],
                "country": c.get("country"),
                "mentions": mentions_by_name.get(c["name"], 0),
                "is_watchlist": bool(c.get("is_watchlist")),
                "is_israeli_industry": c["name"] in _ISRAELI_INDUSTRY_NAMES,
                "recent_wins": wins,
            }
        )
    return out


def format_competitors_block(competitors: list[dict[str, Any]]) -> str:
    """A win's ``[n]`` is the market item it came from (``build_bd_territory`` attaches it via
    ``_attach_win_citations`` before formatting -- every win's ``item_id`` is one of the same
    market items already in the citation registry, so no synthetic entry is needed here, unlike
    tenders/conferences)."""
    if not competitors:
        return "לא זוהו מתחרים פעילים בטריטוריה זו בחלון הזמן שנבדק."
    lines: list[str] = []
    for c in competitors:
        tag = " (תעשייה ישראלית)" if c["is_israeli_industry"] else ""
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
        key=lambda r: (r.get("relevance") or 0),
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


def format_conferences_block(data: dict[str, Any]) -> str:
    """Each line carries the ``[n]`` ``_extend_registry_with_conferences`` assigned -- see
    ``format_tenders_block``'s docstring for why this matters."""
    territory_rows = data.get("territory") or []
    international_rows = data.get("international") or []
    lines: list[str] = []
    if territory_rows:
        lines.append("כנסים בטריטוריה:")
        for c in territory_rows:
            n = f"[{c['n']}] " if c.get("n") is not None else ""
            lines.append(f"- {n}{c.get('name') or '—'} | {fmt_date(c.get('start_date'))} | {c.get('city') or '—'}")
    else:
        lines.append("לא זוהו כנסים מתוכננים בטריטוריה זו ב-12 החודשים הקרובים.")
    lines.append("")
    if international_rows:
        lines.append("כנסים בינלאומיים רלוונטיים (הקשר בלבד):")
        for c in international_rows:
            n = f"[{c['n']}] " if c.get("n") is not None else ""
            lines.append(f"- {n}{c.get('name') or '—'} | {fmt_date(c.get('start_date'))} | {c.get('city') or '—'}")
    return "\n".join(lines)


def conferences_table(data: dict[str, Any]) -> dict[str, Any] | None:
    territory_rows = data.get("territory") or []
    if not territory_rows:
        return None
    headers = ["שם", "תאריכים", "עיר", "רלוונטיות"]
    rows = [
        [
            c.get("name") or "—",
            f"{fmt_date(c.get('start_date'))} - {fmt_date(c.get('end_date'))}",
            c.get("city") or "—",
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


def _extend_registry_with_tenders(citation_items: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Every tender/forecast gets its own registry row (F: without this the model has no valid
    citation for a tender/forecast-derived claim, which in practice produced uncited -- and once,
    degenerate/runaway -- generations, see ``format_tenders_block``)."""
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for t in data.get("tenders") or []:
        next_n = _synthetic_registry_entry(
            citation_items, t, next_n,
            title=t.get("title"), source_name=t.get("agency") or "מכרז", url=t.get("url"),
            published_at=t.get("published_at"),
        )
    for f in data.get("forecasts") or []:
        next_n = _synthetic_registry_entry(
            citation_items, f, next_n,
            title=f.get("platform"), source_name="תחזית רכש", url=None,
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
            citation_items, c, next_n,
            title=c.get("name"), source_name=c.get("organizer") or "כנס", url=c.get("registration_url"),
            published_at=c.get("start_date"),
        )
    return citation_items


def _attach_win_citations(citation_items: list[dict[str, Any]], competitors: list[dict[str, Any]]) -> None:
    """A competitor's "recent win" event's ``item_id`` is always one of the same market items
    already in ``citation_items`` (``collect_active_competitors`` only looks among
    ``market_item_ids``) -- so each win reuses that item's existing ``n`` rather than getting a new
    synthetic registry row."""
    id_to_n = {it["id"]: it["n"] for it in citation_items if it.get("id") is not None and it.get("n") is not None}
    for c in competitors:
        for w in c.get("recent_wins") or []:
            w["n"] = id_to_n.get(w.get("item_id"))


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> BdTerritoryReportDraft:
    return BdTerritoryReportDraft(
        exec_summary_he="לא זוהו בטריטוריה זו פריטים חדשים בחלון הזמן שנבדק. אין ממצאים לדוח המיקוד.",
        market_bullets_he=[],
        sections=[],
        recommended_actions=[],
        risks_assumptions_he="לא נמצא מספיק מידע בטריטוריה זו כדי לבסס המלצות פעולה.",
        outlook_he="",
        open_points_he=[],
    )


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
    role: str = "resident",
    interactive: bool = False,
) -> BdTerritoryReportDraft:
    if not has_items:
        return _no_items_draft()
    prompt = render(
        "report_bd_territory",
        territory_label=territory_label(territory),
        lookback_days=lookback_days,
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
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
        options={"temperature": 0.3},
    )


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
        "ברשימות שסופקו:\n" + errors_text
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
        options={"temperature": 0.2},
    )


def _bullets_text(bullets: list[str]) -> str:
    return "\n".join(b if b.rstrip().endswith((".", "!", "?", ":")) else f"{b}." for b in bullets)


def _qa_extra_sections(draft: BdTerritoryReportDraft) -> list[tuple[str, str]]:
    extra: list[tuple[str, str]] = []
    if draft.market_bullets_he:
        extra.append(("תמונת שוק בטריטוריה", _bullets_text(draft.market_bullets_he)))
    for action in draft.recommended_actions:
        extra.append((f"נימוק לפעולה: {action.action_he}", action.rationale_he))
    return extra


def _run_qa(draft: BdTerritoryReportDraft, citation_items: list[dict[str, Any]]) -> QAResult:
    return check(
        draft,
        citation_items,
        extra_sections=_qa_extra_sections(draft),
        exempt_sections=[("סיכונים והנחות", draft.risks_assumptions_he)] if draft.risks_assumptions_he else None,
    )


def _strip_uncited(draft: BdTerritoryReportDraft, qa: QAResult) -> BdTerritoryReportDraft:
    """Drop the sentences ``qa`` flagged from the exec summary, market bullets, and every action's
    rationale -- mirrors ``eoa.report.weekly._strip_uncited``. An action whose rationale becomes
    empty after stripping is dropped entirely (a recommendation with no surviving grounding is
    worse than no recommendation, per docs/CONVENTIONS.md rule 5)."""
    bad_refs = set(qa.bad_refs)
    uncited = set(qa.uncited_sentences)
    duplicates = set(qa.duplicate_sentences)

    def _clean(text: str, *, extra_drop: set[str] = frozenset()) -> str:
        kept = []
        for sentence in split_sentences(text):
            if sentence in uncited or sentence in extra_drop:
                continue
            if bad_refs and set(citations_in(sentence)) & bad_refs:
                continue
            kept.append(sentence)
        return " ".join(kept)

    new_summary = _clean(draft.exec_summary_he, extra_drop=duplicates)
    if not new_summary:
        new_summary = "תקציר המנהלים קוצץ במלואו עקב בדיקת אזכורים שנכשלה; ראו qa_report לפרטים."

    new_bullets = [b for b in (_clean(bullet) for bullet in draft.market_bullets_he) if b]

    new_actions: list[BdAction] = []
    for action in draft.recommended_actions:
        cleaned = _clean(action.rationale_he)
        if cleaned:
            new_actions.append(action.model_copy(update={"rationale_he": cleaned}))

    return draft.model_copy(
        update={
            "exec_summary_he": new_summary,
            "market_bullets_he": new_bullets,
            "recommended_actions": new_actions,
        }
    )


# --------------------------------------------------------------------------
# rendering helpers
# --------------------------------------------------------------------------

_PRIORITY_LABEL_HE = {"H": "גבוהה", "M": "בינונית", "L": "נמוכה"}


def recommended_actions_table(draft: BdTerritoryReportDraft) -> dict[str, Any] | None:
    if not draft.recommended_actions:
        return None
    headers = ["עדיפות", "פעולה", "נימוק", "אחראי", "תזמון"]
    order = {"H": 0, "M": 1, "L": 2}
    actions = sorted(draft.recommended_actions, key=lambda a: order.get(a.priority, 9))
    rows = [
        [
            _PRIORITY_LABEL_HE.get(a.priority, a.priority),
            a.action_he,
            a.rationale_he,
            a.owner_role_he,
            a.timing_he,
        ]
        for a in actions
    ]
    return {"title_he": "נקודות כניסה ופעולות מומלצות", "headers": headers, "rows": rows}


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
    territory: str, lookback_days: int = 90, *, period_end: dt.date | None = None,
    role: str = "resident", interactive: bool = False,
) -> ReportPaths:
    """Collect -> draft -> QA-gate -> render docx/md/html -> persist, for one territory."""
    code = normalize_country(territory)
    start, end = lookback_range(lookback_days, period_end)

    items = collect_market_items(code, start, end)
    events = collect_platform_events(code, start, end)
    tenders_data = collect_tenders_and_forecasts(code)
    competitors = collect_active_competitors(code, [it["id"] for it in items], start, end)
    conferences_data = collect_conferences_for_territory(code)

    citation_items = list(items)
    _extend_registry_with_source_items(citation_items, events)
    _extend_registry_with_tenders(citation_items, tenders_data)
    _extend_registry_with_conferences(citation_items, conferences_data)
    _attach_win_citations(citation_items, competitors)

    items_block = format_market_items_block(items)
    events_block = format_market_items_block([]) if not events else "\n".join(
        f"[{ev.get('n', '—')}] {ev.get('platform_he')} | {ev.get('buyer')} <- {ev.get('vendor')} | "
        f"{fmt_date(ev.get('date') or ev.get('published_at'))}"
        for ev in events
    ) or "לא זוהו אירועי רכש/פלטפורמות בטריטוריה זו בחלון הזמן."
    tenders_block = format_tenders_block(tenders_data)
    competitors_block = format_competitors_block(competitors)
    conferences_block = format_conferences_block(conferences_data)

    draft = draft_bd_territory(
        code, lookback_days, items_block, events_block, tenders_block, competitors_block, conferences_block,
        has_items=bool(items), role=role, interactive=interactive,
    )
    qa = _run_qa(draft, citation_items)

    if not qa.passed and items:
        log.warning("bd_territory_qa_failed_retrying", territory=code, errors=qa.errors[:10])
        draft = _corrective_retry(
            code, lookback_days, items_block, events_block, tenders_block, competitors_block, conferences_block,
            draft, qa, role=role, interactive=interactive,
        )
        qa = _run_qa(draft, citation_items)

    if not qa.passed and items:
        log.error("bd_territory_qa_failed_stripping", territory=code, errors=qa.errors[:10])
        original_errors = qa
        draft = _strip_uncited(draft, qa)
        _run_qa(draft, citation_items)
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    extra_sections: list[dict[str, Any]] = []
    if draft.market_bullets_he:
        extra_sections.append(
            {
                "title_he": "תמונת שוק בטריטוריה",
                "body_he": _bullets_text(draft.market_bullets_he),
                "position": "after_summary",
            }
        )
    if draft.risks_assumptions_he:
        extra_sections.append(
            {"title_he": "סיכונים והנחות", "body_he": draft.risks_assumptions_he, "position": "after_outlook"}
        )

    tables: list[dict[str, Any]] = []
    for tbl in (
        platform_events_table(events),
        tenders_table(tenders_data),
        competitors_table(competitors),
        conferences_table(conferences_data),
        recommended_actions_table(draft),
    ):
        if tbl is not None:
            tables.append(tbl)

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
        draft, citation_items, [], period_end=end, qa=qa, title_text=title_text,
        extra_sections=extra_sections, tables=tables or None,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft, citation_items, [], period_end=end, qa=qa, title_text=title_text,
        extra_sections=extra_sections, tables=tables or None, include_toc=True,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(code, start, end, docx_path, md_path, html_path, citation_items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa, territory=code)
