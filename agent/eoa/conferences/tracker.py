"""FR-12: rolling conference/exhibition tracker.

``roll_horizon`` keeps the ``conferences`` table populated with an ``estimated`` row for every
seed/recurring conference occurrence within the next ``months`` (default 24), and transitions
rows whose date has passed to ``status='past'``. ``verify_conference`` runs a small
deep-search-lite pass (a handful of SearXNG queries + up to two page fetches +
``chat_structured``) to confirm/refresh one conference's details. ``discover_new`` searches for
conferences not yet tracked and proposes them as new ``estimated`` rows. ``monthly_scan`` is the
FR-12.3 entry point that ties the three together and is budget-aware (stops verifying on
``ResourceUnavailable`` rather than blocking the whole scan).

Every LLM call goes through ``eoa.llm.ollama_client`` (DATA-guarded); every page fetch goes
through ``eoa.fetch.remote.fetch_remote``; every search through ``eoa.search.searxng_client``.
Nothing here invents a date, URL, or fact that was not present in fetched text.
"""

from __future__ import annotations

import datetime as dt
import re
from difflib import SequenceMatcher
from typing import Any

import structlog
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta
from psycopg.types.json import Json

from eoa.config import settings
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.fetch.remote import fetch_remote
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.conferences import ConferenceCandidate, ConferenceCandidates, ConferenceExtract
from eoa.search.searxng_client import SearchHit, search

log = structlog.get_logger(__name__)

CONFIDENCE_MIN = 0.6

DATE_FIELDS = ("start_date", "end_date", "registration_opens", "early_bird_deadline", "cfp_deadline")
_VERIFY_FIELDS = (*DATE_FIELDS, "city", "venue", "cost_range", "registration_url", "entry_conditions")

_NOISY_DOMAINS = (
    "wikipedia.org",
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "reddit.com",
    "instagram.com",
)

_RUBRIC_KEYWORDS = (
    "defense",
    "defence",
    "electro-optic",
    "eo/ir",
    "infrared",
    "thermal",
    "c-uas",
    "counter-uas",
    "counter uas",
    "drone",
    "uas",
    "air defense",
    "air defence",
    "naval",
    "imaging",
    "surveillance",
    "targeting",
    "optronics",
    "sensing",
)

_RELEVANCE_HE = {5: "קריטית", 4: "גבוהה", 3: "בינונית", 2: "נמוכה", 1: "שולית"}

DEFAULT_DISCOVER_QUERIES = (
    "2026 2027 defense electro-optics conference exhibition",
    "counter-UAS conference 2027",
    "infrared imaging symposium 2027",
    "naval defence exhibition 2027",
)
DEFAULT_DISCOVER_KEYWORDS = ["electro-optic", "infrared", "counter-uas", "air defense", "naval surveillance"]


# --------------------------------------------------------------------------
# generic DB helpers (mirrors eoa.api.services' private style)
# --------------------------------------------------------------------------


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _execute(query: str, params: Any = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)


# --------------------------------------------------------------------------
# occurrence math (FR-12.1: annual / biennial_odd / biennial_even)
# --------------------------------------------------------------------------


def _occurs_in_year(cadence: str | None, year: int) -> bool:
    """Whether a conference with this ``cadence`` seed value occurs in ``year``."""
    c = (cadence or "annual").strip().lower()
    if c in ("", "annual"):
        return True
    if c == "biennial_odd":
        return year % 2 == 1
    if c in ("biennial_even", "biennial"):
        # Plain "biennial" (no parity given in config/watchlist.yaml, e.g. ISDEF) is treated as
        # even-year by default so the horizon math is deterministic; verify_conference corrects
        # the actual year once an official announcement is found.
        return year % 2 == 0
    # Unknown cadence string: never silently drop the conference from the horizon.
    return True


def _years_in_horizon(cadence: str | None, month: int, today: dt.date, horizon_end: dt.date) -> list[int]:
    """Years (within ``[today, horizon_end]``, cadence-filtered) this conference next occurs in."""
    years: list[int] = []
    for year in range(today.year, horizon_end.year + 1):
        if not _occurs_in_year(cadence, year):
            continue
        if year == today.year and month < today.month:
            continue  # this year's occurrence already happened
        candidate = dt.date(year, month, 15)
        if candidate <= horizon_end:
            years.append(year)
    return years


# --------------------------------------------------------------------------
# roll_horizon (FR-12.1 / FR-12.3)
# --------------------------------------------------------------------------


def _insert_estimated(
    *,
    name: str,
    city: str | None,
    cadence: str | None,
    relevance: int | None,
    start_date: dt.date,
    end_date: dt.date,
    rationale: str,
) -> bool:
    """Insert one ``estimated`` row keyed by its unique ``name``; no-op (returns False) if it exists."""
    row = _fetchone(
        """
        INSERT INTO conferences (name, city, cadence, relevance, start_date, end_date, status, rationale)
        VALUES (%(name)s, %(city)s, %(cadence)s, %(relevance)s, %(start_date)s, %(end_date)s, 'estimated', %(rationale)s)
        ON CONFLICT (name) DO NOTHING
        RETURNING id
        """,
        {
            "name": name,
            "city": city,
            "cadence": cadence,
            "relevance": relevance,
            "start_date": start_date,
            "end_date": end_date,
            "rationale": rationale,
        },
    )
    return row is not None


def _transition_past(today: dt.date, rows: list[dict[str, Any]] | None = None) -> int:
    """Flip any non-terminal row whose date has passed to ``status='past'``; returns the count."""
    if rows is None:
        rows = _fetchall(
            "SELECT id, start_date, end_date FROM conferences WHERE status NOT IN ('past', 'cancelled')"
        )
    to_transition = [r["id"] for r in rows if _is_past(r, today)]
    if to_transition:
        _execute("UPDATE conferences SET status = 'past' WHERE id = ANY(%s)", (to_transition,))
    return len(to_transition)


def _is_past(row: dict[str, Any], today: dt.date) -> bool:
    ref = row.get("end_date") or row.get("start_date")
    return bool(ref) and ref < today


def roll_horizon(months: int = 24) -> dict[str, Any]:
    """FR-12.1/12.3: ensure every seed conference has an ``estimated`` row for each occurrence
    within the next ``months``, never duplicating an existing (confirmed or estimated) row for
    the same year, then transition passed conferences to ``status='past'``.
    """
    today = dt.date.today()
    horizon_end = today + relativedelta(months=months)
    seeds = (settings().watchlist or {}).get("conferences_seed") or []

    created, skipped = 0, 0
    for seed in seeds:
        name, month = seed.get("name"), seed.get("month")
        if not name or not month:
            log.warning("conference_seed_missing_fields", seed=seed)
            continue
        cadence = seed.get("cadence", "annual")
        for year in _years_in_horizon(cadence, month, today, horizon_end):
            start_date = dt.date(year, month, 15)
            inserted = _insert_estimated(
                name=f"{name} {year}",
                city=seed.get("city"),
                cadence=cadence,
                relevance=seed.get("relevance"),
                start_date=start_date,
                end_date=start_date + dt.timedelta(days=3),
                rationale=(
                    f"מועד משוער לפי מחזוריות היסטורית (חודש {month}, {cadence}); "
                    "יאומת בסריקה החודשית (FR-12.3)."
                ),
            )
            if inserted:
                created += 1
            else:
                skipped += 1

    transitioned = _transition_past(today)
    log.info("conference_roll_horizon_done", created=created, skipped=skipped, transitioned_past=transitioned)
    return {
        "created": created,
        "skipped_existing": skipped,
        "transitioned_past": transitioned,
        "horizon_end": horizon_end.isoformat(),
    }


# --------------------------------------------------------------------------
# verify_conference (FR-12.3: deep-search-lite)
# --------------------------------------------------------------------------


def _year_from_name(name: str) -> int | None:
    m = re.search(r"(20\d{2})\s*$", name)
    return int(m.group(1)) if m else None


def _top_official_urls(hits: list[SearchHit], limit: int = 2) -> list[str]:
    """Highest-scoring hits, deduplicated by domain, skipping social/aggregator noise."""
    from urllib.parse import urlparse

    seen_domains: set[str] = set()
    out: list[str] = []
    for h in sorted(hits, key=lambda x: x.score, reverse=True):
        if not h.url:
            continue
        domain = urlparse(h.url).netloc.lower()
        if not domain or domain in seen_domains or any(bad in domain for bad in _NOISY_DOMAINS):
            continue
        seen_domains.add(domain)
        out.append(h.url)
        if len(out) >= limit:
            break
    return out


def _parse_date(raw: str | None) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        pass
    try:
        return date_parser.parse(raw, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


def _apply_conference_update(
    conf_id: int, updates: dict[str, Any], prev_snapshot: dict[str, Any], status: str
) -> None:
    set_clauses = ", ".join(f"{k} = %({k})s" for k in updates)
    prefix = f"{set_clauses}, " if updates else ""
    query = (
        f"UPDATE conferences SET {prefix}prev_snapshot = %(prev_snapshot)s, "
        "last_verified_at = now(), status = %(status)s WHERE id = %(id)s"
    )
    _execute(query, {**updates, "id": conf_id, "prev_snapshot": Json(prev_snapshot), "status": status})


def verify_conference(conf_id: int) -> dict[str, Any]:
    """FR-12.3: deep-search-lite verification of one conference's dates/registration/CFP details.

    3-5 SearXNG queries -> fetch top 2 official-looking pages -> ``chat_structured`` extraction.
    Writes only fields with ``confidence >= 0.6``; keeps the previous values in ``prev_snapshot``
    (jsonb) for "what changed" reporting; always stamps ``last_verified_at``.
    """
    row = _fetchone("SELECT * FROM conferences WHERE id = %s", (conf_id,))
    if row is None:
        raise ValueError(f"conference {conf_id} not found")

    name = row["name"]
    year = _year_from_name(name) or dt.date.today().year
    queries = [
        f"{name} official site",
        f"{name} {year} dates",
        f"{name} {year} registration",
        f"{name} {year} call for papers",
    ]
    hits: list[SearchHit] = []
    for q in queries:
        resp = search(q, lang="en", max_results=5)
        if not resp.error:
            hits.extend(resp.hits)

    pages: list[tuple[str, str]] = []
    for url in _top_official_urls(hits, limit=2):
        try:
            page = fetch_remote(url)
            text = (page.get("text") or "").strip()
            if text:
                pages.append((url, text))
        except Exception as exc:
            log.warning("conference_verify_fetch_failed", conf_id=conf_id, url=url, error=str(exc)[:160])

    if not pages:
        _execute("UPDATE conferences SET last_verified_at = now() WHERE id = %s", (conf_id,))
        return {"verified": False, "reason": "no_pages_fetched", "queries": len(queries)}

    data_block = "\n\n".join(
        wrap_data(text[:8000], f"conf-{conf_id}-{i}", src=url) for i, (url, text) in enumerate(pages)
    )
    prompt = render("conference_extract", conf_name=name, year=year, data=data_block)
    try:
        extracted = chat_structured(
            "resident",
            ConferenceExtract,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="classify",
        )
    except LLMOutputError as exc:
        log.warning("conference_verify_llm_failed", conf_id=conf_id, error=str(exc)[:200])
        _execute("UPDATE conferences SET last_verified_at = now() WHERE id = %s", (conf_id,))
        return {"verified": False, "reason": "llm_output_error", "queries": len(queries)}

    if not extracted.found or extracted.confidence < CONFIDENCE_MIN:
        _execute("UPDATE conferences SET last_verified_at = now() WHERE id = %s", (conf_id,))
        return {"verified": False, "reason": "low_confidence", "confidence": extracted.confidence}

    updates: dict[str, Any] = {}
    for field in _VERIFY_FIELDS:
        raw = getattr(extracted, field, None)
        if not raw:
            continue
        if field in DATE_FIELDS:
            parsed = _parse_date(raw)
            if parsed is not None:
                updates[field] = parsed
        else:
            updates[field] = raw
    if extracted.key_exhibitors:
        stamp = dt.date.today().isoformat()
        exhibitors = ", ".join(extracted.key_exhibitors[:15])
        updates["rationale"] = f"{row.get('rationale') or ''}\nמציגים מרכזיים ({stamp}): {exhibitors}".strip()

    new_status = row["status"]
    if extracted.cancelled:
        new_status = "cancelled"
    elif "start_date" in updates or "end_date" in updates:
        new_status = "confirmed"

    if not updates and new_status == row["status"]:
        _execute("UPDATE conferences SET last_verified_at = now() WHERE id = %s", (conf_id,))
        return {"verified": True, "changed": {}, "confidence": extracted.confidence, "status": row["status"]}

    prev_snapshot = {k: _jsonable(row.get(k)) for k in updates}
    _apply_conference_update(conf_id, updates, prev_snapshot, new_status)
    changed = {
        k: {"from": prev_snapshot[k], "to": _jsonable(v)}
        for k, v in updates.items()
        if prev_snapshot[k] != _jsonable(v)
    }
    return {"verified": True, "changed": changed, "confidence": extracted.confidence, "status": new_status}


# --------------------------------------------------------------------------
# discover_new (FR-12.3)
# --------------------------------------------------------------------------


def _is_near_duplicate(name: str, existing: list[str], threshold: float = 0.85) -> bool:
    n = name.strip().lower()
    return any(SequenceMatcher(None, n, other.strip().lower()).ratio() >= threshold for other in existing)


def _relevance_score(candidate: ConferenceCandidate, extra_keywords: list[str]) -> int:
    """1-5 rubric: 1 point base + 1 per distinct domain-keyword hit in name/rationale, capped at 5."""
    text = f"{candidate.name} {candidate.rationale_he}".lower()
    keywords = set(_RUBRIC_KEYWORDS) | {k.lower() for k in extra_keywords}
    hits = sum(1 for kw in keywords if kw in text)
    return max(1, min(5, 1 + hits))


def discover_new(domain_keywords: list[str] | None = None) -> dict[str, Any]:
    """FR-12.3: search for conferences not yet tracked and insert plausible new candidates
    as ``estimated`` rows, skipping near-duplicates of already-tracked names."""
    keywords = domain_keywords or []
    extra = [f"{kw} defense conference 2027" for kw in keywords[:2]] or [
        "airborne targeting pod trade show 2027",
        "SPIE defense sensing symposium 2027",
    ]
    queries = (*DEFAULT_DISCOVER_QUERIES, *extra)[:6]

    hits: list[SearchHit] = []
    for q in queries:
        resp = search(q, lang="en", max_results=8)
        if not resp.error:
            hits.extend(resp.hits)
    if not hits:
        return {"searched": len(queries), "candidates_found": 0, "inserted": 0, "skipped_duplicate": 0}

    seen_urls: set[str] = set()
    blocks: list[str] = []
    for h in hits:
        if h.url in seen_urls:
            continue
        seen_urls.add(h.url)
        blocks.append(f"- {h.title} | {h.url}\n  {h.snippet}")
        if len(blocks) >= 40:
            break

    prompt = (
        "מצאת תוצאות חיפוש (DATA למטה) על כנסים ותערוכות ביטחוניים/EO-IR/C-UAS/הגנה אווירית צפויים ב-2026–2027. "
        "הצע מועמדים לכנסים אמיתיים ומובחנים בלבד (לא כפילויות של אותו כנס בניסוחים שונים, ולא ניחושים ללא בסיס "
        "בתוצאות החיפוש). לכל מועמד מלא: name (שם רשמי), dates (כפי שמופיע, אם ידוע), city, url (הקישור המקורי "
        "הרלוונטי ביותר), ו-rationale_he (עברית קצרה: למה זה רלוונטי לתחומי EO/IR/C-UAS/הגנה אווירית). "
        "החזר JSON בלבד לפי הסכמה.\n\n" + wrap_data("\n".join(blocks), "conf-discover", src="searxng")
    )
    try:
        result = chat_structured(
            "resident",
            ConferenceCandidates,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="classify",
        )
    except LLMOutputError as exc:
        log.warning("conference_discover_llm_failed", error=str(exc)[:200])
        return {
            "searched": len(queries),
            "candidates_found": 0,
            "inserted": 0,
            "skipped_duplicate": 0,
            "error": "llm_output_error",
        }

    existing_names = [r["name"] for r in _fetchall("SELECT name FROM conferences")]
    inserted, skipped = 0, 0
    for cand in result.candidates:
        if not cand.name or _is_near_duplicate(cand.name, existing_names):
            skipped += 1
            continue
        row = _fetchone(
            """
            INSERT INTO conferences (name, city, relevance, rationale, start_date, registration_url, status)
            VALUES (%(name)s, %(city)s, %(relevance)s, %(rationale)s, %(start_date)s, %(url)s, 'estimated')
            ON CONFLICT (name) DO NOTHING
            RETURNING id
            """,
            {
                "name": cand.name,
                "city": cand.city,
                "relevance": _relevance_score(cand, keywords),
                "rationale": cand.rationale_he,
                "start_date": _parse_date(cand.dates),
                "url": cand.url,
            },
        )
        if row is not None:
            existing_names.append(cand.name)
            inserted += 1
        else:
            skipped += 1

    return {
        "searched": len(queries),
        "candidates_found": len(result.candidates),
        "inserted": inserted,
        "skipped_duplicate": skipped,
    }


# --------------------------------------------------------------------------
# monthly_scan (FR-12.3 entry point)
# --------------------------------------------------------------------------


def _conferences_needing_verification(cap: int = 15) -> list[dict[str, Any]]:
    today = dt.date.today()
    return _fetchall(
        """
        SELECT * FROM conferences
        WHERE status NOT IN ('cancelled', 'past')
          AND start_date IS NOT NULL
          AND start_date <= %(within)s
          AND (last_verified_at IS NULL OR last_verified_at < now() - interval '30 days')
        ORDER BY start_date ASC
        LIMIT %(cap)s
        """,
        {"within": today + dt.timedelta(days=365), "cap": cap},
    )


def monthly_scan(discover_keywords: list[str] | None = None, *, verify_cap: int = 15) -> dict[str, Any]:
    """FR-12.3: ``roll_horizon`` + verify up to ``verify_cap`` stale conferences within 12 months +
    ``discover_new``. Budget-aware: stops verifying (but still runs discovery) on ``ResourceUnavailable``.
    """
    stats: dict[str, Any] = {"roll_horizon": roll_horizon()}

    verified: list[dict[str, Any]] = []
    stopped_budget = False
    for row in _conferences_needing_verification(cap=verify_cap):
        try:
            result = verify_conference(row["id"])
        except ResourceUnavailable as exc:
            log.warning("conference_monthly_scan_budget_stop", conf_id=row["id"], error=str(exc)[:160])
            stopped_budget = True
            break
        verified.append({"id": row["id"], "name": row["name"], **result})
    stats["verified"] = verified
    stats["verify_stopped_budget"] = stopped_budget

    try:
        stats["discover"] = discover_new(discover_keywords or DEFAULT_DISCOVER_KEYWORDS)
    except ResourceUnavailable as exc:
        log.warning("conference_monthly_scan_discover_budget_stop", error=str(exc)[:160])
        stats["discover"] = {"searched": 0, "candidates_found": 0, "inserted": 0, "skipped_duplicate": 0}

    log.info(
        "conference_monthly_scan_done",
        created=stats["roll_horizon"]["created"],
        verified=len(verified),
        discovered=stats["discover"]["inserted"],
    )
    return stats


# --------------------------------------------------------------------------
# report-layer helpers (FR-12.5): upcoming / full horizon table / row -> API card
# --------------------------------------------------------------------------


def _changes_vs_prev(row: dict[str, Any]) -> dict[str, Any]:
    prev = row.get("prev_snapshot") or {}
    changes: dict[str, Any] = {}
    for field, old in prev.items():
        new = _jsonable(row.get(field))
        if old != new:
            changes[field] = {"from": old, "to": new}
    return changes


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else value


def conference_card(row: dict[str, Any]) -> dict[str, Any]:
    """Row -> API/report shape. Includes both the front-end-compatible fields already in
    ``web/src/types/api.ts`` (``starts_at``/``ends_at``/``location``/``url``/``relevance_he``)
    and the full FR-12 field set, plus ``changes`` vs ``prev_snapshot`` for "what changed"."""
    start, end = row.get("start_date"), row.get("end_date")
    city, venue = row.get("city"), row.get("venue")
    relevance = row.get("relevance")
    return {
        "id": row["id"],
        "name": row.get("name"),
        # legacy/front-end-compatible fields
        "location": ", ".join(x for x in (city, venue) if x) or None,
        "starts_at": _iso(start) or "",
        "ends_at": _iso(end) or "",
        "url": row.get("registration_url"),
        "relevance_he": f"{_RELEVANCE_HE[relevance]} ({relevance})" if relevance in _RELEVANCE_HE else None,
        # full FR-12 field set (additive, per docs/API.md)
        "organizer": row.get("organizer"),
        "start_date": _iso(start),
        "end_date": _iso(end),
        "city": city,
        "venue": venue,
        "cadence": row.get("cadence"),
        "relevance": relevance,
        "rationale": row.get("rationale"),
        "registration_opens": _iso(row.get("registration_opens")),
        "early_bird_deadline": _iso(row.get("early_bird_deadline")),
        "cfp_deadline": _iso(row.get("cfp_deadline")),
        "cost_range": row.get("cost_range"),
        "registration_url": row.get("registration_url"),
        "entry_conditions": row.get("entry_conditions"),
        "status": row.get("status"),
        "last_verified_at": _iso(row.get("last_verified_at")),
        "changes": _changes_vs_prev(row),
    }


def upcoming(days: int = 90) -> list[dict[str, Any]]:
    """FR-12.5: the weekly report's "next 90 days" board."""
    today = dt.date.today()
    rows = _fetchall(
        "SELECT * FROM conferences WHERE status != 'cancelled' AND start_date IS NOT NULL "
        "AND start_date BETWEEN %(start)s AND %(end)s ORDER BY start_date ASC",
        {"start": today, "end": today + dt.timedelta(days=days)},
    )
    return [conference_card(r) for r in rows]


def full_horizon_table() -> list[dict[str, Any]]:
    """FR-12.5: the monthly report's full rolling 24-month table, start_date ascending."""
    rows = _fetchall(
        "SELECT * FROM conferences WHERE status != 'cancelled' ORDER BY start_date ASC NULLS LAST"
    )
    return [conference_card(r) for r in rows]
