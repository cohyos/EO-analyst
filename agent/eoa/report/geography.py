"""Country/geography normalization + per-country reporting (U7).

Normalizes free-text country/geography values (as found in `items.geography`
and `entities.country`) to ISO-3166-1 alpha-2 codes, or a small set of
recognized non-ISO region codes (EU, NATO, UN), falling back to `"other"`
for anything unrecognized. Two things live here, both built on the same
`normalize_country`/`_ALIASES` vocabulary so there is exactly one place that
knows what "US"/"USA"/"United States" mean:

- `items_by_country` -- backs `eoa.api.services.list_items`'s additive
  `country=`/`group_by=country` params and the new `GET /api/items/by-country`
  endpoint (U7a/U7c).
- `collect_by_country`/`format_country_section` -- a report section
  ("לפי מדינה") the report agent can wire into the daily/weekly report later
  (U7d). Deliberately separate from `eoa.report.daily`/`weekly` (owned/edited
  concurrently) -- this module only *collects and renders*, mirroring
  `eoa.tenders.report_section`'s split: the result is a plain
  `{"title_he", "body_he", "position"}` dict matching the same
  `extra_sections` shape `docx_builder` already knows how to render, so
  wiring it in is a small additive change, not a new contract.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.db import connection

UNKNOWN_COUNTRY = "other"

# Common free-text spellings/aliases -> normalized ISO-2/region code. Keys
# are matched case-insensitively after stripping whitespace and a trailing
# period. This is the single source of truth for the mapping -- both the
# `GET /api/items` country filter (via `raw_values_for_country`, the reverse
# lookup) and the report section use it, so they can never disagree about
# what a given raw value means.
_ALIASES: dict[str, str] = {
    "us": "US", "usa": "US", "u.s": "US", "u.s.a": "US",
    "united states": "US", "united states of america": "US",
    "israel": "IL", "ישראל": "IL", "il": "IL",
    "uk": "GB", "u.k": "GB", "united kingdom": "GB", "great britain": "GB",
    "gb": "GB", "britain": "GB",
    "eu": "EU", "european union": "EU",
    "nato": "NATO",
    "un": "UN", "united nations": "UN",
    "germany": "DE", "deutschland": "DE", "de": "DE",
    "france": "FR", "fr": "FR",
    "italy": "IT", "it": "IT",
    "turkey": "TR", "türkiye": "TR", "turkiye": "TR", "tr": "TR",
    "south korea": "KR", "korea, south": "KR", "republic of korea": "KR", "kr": "KR",
    "north korea": "KP", "kp": "KP",
    "japan": "JP", "jp": "JP",
    "china": "CN", "prc": "CN", "cn": "CN",
    "india": "IN", "in": "IN",
    "russia": "RU", "russian federation": "RU", "ru": "RU",
    "ukraine": "UA", "ua": "UA",
    "poland": "PL", "pl": "PL",
    "spain": "ES", "es": "ES",
    "netherlands": "NL", "holland": "NL", "nl": "NL",
    "sweden": "SE", "se": "SE",
    "norway": "NO", "no": "NO",
    "finland": "FI", "fi": "FI",
    "canada": "CA", "ca": "CA",
    "australia": "AU", "au": "AU",
    "saudi arabia": "SA", "sa": "SA",
    "uae": "AE", "united arab emirates": "AE", "ae": "AE",
    "singapore": "SG", "sg": "SG",
    "taiwan": "TW", "tw": "TW",
    "brazil": "BR", "br": "BR",
    "greece": "GR", "gr": "GR",
    "other": UNKNOWN_COUNTRY,
    "unknown": UNKNOWN_COUNTRY,
}


def normalize_country(raw: str | None) -> str:
    """Best-effort mapping of a free-text geography/country value to an
    ISO-2 code or a recognized region code (EU/NATO/UN). Unrecognized or
    empty input normalizes to `"other"` -- never raises, never returns
    `None`, so callers can group/aggregate on the result unconditionally."""
    if not raw:
        return UNKNOWN_COUNTRY
    key = raw.strip().lower().rstrip(".")
    if key in _ALIASES:
        return _ALIASES[key]
    if len(key) == 2 and key.isalpha():
        return key.upper()
    return UNKNOWN_COUNTRY


def raw_values_for_country(code: str) -> list[str]:
    """Reverse lookup: every raw alias string known to normalize to `code`
    (plus the bare code itself, upper/lower) -- used to build a `WHERE
    UPPER(geography) = ANY(...)` filter without re-deriving the alias table
    in SQL."""
    code_upper = code.strip().upper()
    raws = [k for k, v in _ALIASES.items() if v == code_upper]
    raws.append(code_upper)
    raws.append(code_upper.lower())
    return raws


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


_LEVELS = ("red", "orange", "yellow", "archive")


def items_by_country(
    *,
    level: list[str] | None = None,
    domain: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Per-country item counts + level breakdown for `GET /api/items/by-country`
    and `GET /api/items?group_by=country` (U7a/U7c) -- the same
    `level`/`domain`/`since` filters `eoa.api.services.list_items` accepts,
    applied before normalization/grouping so the country map always matches
    the feed's current filters. Sorted by total descending."""
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if level:
        where.append("i.level = ANY(%(levels)s)")
        params["levels"] = level
    if domain:
        where.append("i.domain = %(domain)s")
        params["domain"] = domain
    if since:
        where.append("COALESCE(i.published_at, i.fetched_at) >= %(since)s")
        params["since"] = since
    rows = _fetchall(f"SELECT geography, level FROM items i WHERE {' AND '.join(where)}", params)

    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_country(row.get("geography"))
        bucket = buckets.setdefault(code, {"country": code, "total": 0, **dict.fromkeys(_LEVELS, 0)})
        bucket["total"] += 1
        lvl = row.get("level")
        if lvl in _LEVELS:
            bucket[lvl] += 1
    return sorted(buckets.values(), key=lambda b: b["total"], reverse=True)


def collect_by_country(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    top_items_per_country: int = 3,
) -> dict[str, Any]:
    """Per-country item counts + top items (highest score first) within the
    report period, for the report's "לפי מדינה" section (U7d). Never
    raises -- simple DB-only queries; callers wrap this in a try/except
    anyway (a report section is never allowed to break the whole report)."""
    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if period_start is not None and period_end is not None:
        where.append("COALESCE(i.published_at, i.fetched_at)::date BETWEEN %(start)s AND %(end)s")
        params["start"] = period_start
        params["end"] = period_end
    rows = _fetchall(
        f"""
        SELECT i.id, i.title, i.geography, i.level, i.score
        FROM items i
        WHERE {' AND '.join(where)}
        ORDER BY i.score DESC NULLS LAST, i.id DESC
        """,
        params,
    )

    countries: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = normalize_country(row.get("geography"))
        entry = countries.setdefault(code, {"country": code, "count": 0, "top_items": []})
        entry["count"] += 1
        if len(entry["top_items"]) < top_items_per_country:
            entry["top_items"].append(
                {
                    "id": row["id"],
                    "title": row.get("title"),
                    "level": row.get("level"),
                    "score": row.get("score"),
                }
            )
    ordered = sorted(countries.values(), key=lambda c: c["count"], reverse=True)
    return {"countries": ordered, "total_items": len(rows)}


def format_country_section(data: dict[str, Any]) -> dict[str, Any]:
    """Renders `collect_by_country`'s output as a Hebrew markdown report
    section ("לפי מדינה"), matching the `{"title_he", "body_he", "position"}`
    shape `eoa.tenders.report_section.tenders_extra_section` also returns, so
    the report agent can wire this in through the existing `docx_builder`
    `extra_sections` hook."""
    countries = data.get("countries") or []
    if not countries:
        return {
            "title_he": "לפי מדינה",
            "body_he": "לא זוהו פריטים עם שיוך גאוגרפי בתקופה זו.",
            "position": "after_outlook",
        }
    lines: list[str] = []
    for c in countries:
        lines.append(f"- **{c['country']}** — {c['count']} פריטים")
        for item in c.get("top_items", []):
            title = item.get("title") or "(ללא כותרת)"
            lines.append(f"  - [{item['id']}] {title}")
    return {
        "title_he": "לפי מדינה",
        "body_he": "\n".join(lines),
        "position": "after_outlook",
    }
