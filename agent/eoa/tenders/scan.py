"""Stage: tender / RFI / RFP ingestion (section 5.2 / FR-5.2).

For each source in ``config/tenders.yaml``: fetch (``eoa.fetch.remote.fetch_raw_remote`` for
``api_json``/``rss`` sources needing the raw body; ``eoa.search.searxng_client`` for ``kind:
search`` sources) -> parse into ``NoticeRaw`` rows -> client-side keyword filter (server-side
keyword params are unreliable, see config/tenders.yaml notes) -> dedupe by a globally-unique
``external_ref`` (``"<source_id>:<notice id>"``) -> insert one ``tenders`` row + one ``items`` row
(``source`` ``"tenders:<source_id>"``, ``report_kind`` ``"tender"``) so the normal
classify/triage/analyze pipeline covers it -> best-effort LLM relevance/summary enrichment
(``chat_structured``, DATA-guarded, budget-capped).

The deterministic parts (keyword-hit relevance, matched_terms, status) are always written first
and never depend on the LLM call succeeding (FR-9 / "never invent" -- a stalled/unavailable model
degrades to "still ingested, just not yet enriched", never to fabricated data). Finally transitions
any ``tenders`` row whose ``deadline`` has passed to ``status='closed'``.

``kind: html`` sources in config/tenders.yaml are documented but intentionally not scraped here
(see the notes on each entry -- bot-protected or client-hydrated pages); ``kind: api_json`` sources
marked ``verified: false`` are skipped the same way. Both are covered instead by a sibling
``kind: search`` source that runs through the already-verified, keyless SearXNG client.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import structlog
import yaml
from dateutil import parser as date_parser
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eoa.config import CONFIG_DIR
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.fetch.remote import fetch_raw_remote
from eoa.fetch.rss import parse_feed
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.tenders import TenderExtract
from eoa.memory.relational import insert_item, update_item_fields
from eoa.search.searxng_client import SearchHit, search

log = structlog.get_logger(__name__)

TENDERS_YAML = CONFIG_DIR / "tenders.yaml"

# Fallback if a config entry somehow omits `keywords` (every entry in config/tenders.yaml merges
# the shared `&kw` anchor, so this should never actually trigger outside of ad-hoc test fixtures).
DEFAULT_KEYWORDS = [
    "electro-optical",
    "infrared",
    "thermal imaging",
    "targeting pod",
    "EO/IR",
    "gimbal",
    "laser rangefinder",
    "seeker",
    "counter-UAS",
    "surveillance camera",
    "night vision",
    "hyperspectral",
    "optronic",
]

MAX_KEYWORDS_PER_API_SOURCE = 5


# --------------------------------------------------------------------------
# source config
# --------------------------------------------------------------------------


class TenderSource(BaseModel):
    """One ``config/tenders.yaml`` entry, validated."""

    id: str
    name: str
    kind: Literal["api_json", "rss", "html", "search"]
    country: str
    url: str | None = None
    method: str = "GET"
    query_template: str | None = None
    query_params: dict[str, str] | None = None
    queries: list[str] | None = None
    engine_lang: str = "en"
    keywords: list[str] = Field(default_factory=list)
    parse_hints: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    verified_at: str | None = None
    notes: str | None = None


def load_tender_sources(path: str | Path | None = None) -> list[TenderSource]:
    """Load and validate every entry in ``config/tenders.yaml`` (or an alternate ``path``)."""
    file_path = Path(path) if path is not None else TENDERS_YAML
    with file_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    entries = raw.get("sources", [])
    sources = [TenderSource.model_validate(e) for e in entries]
    log.debug("tenders.sources_loaded", count=len(sources), path=str(file_path))
    return sources


# --------------------------------------------------------------------------
# NoticeRaw + parsing
# --------------------------------------------------------------------------


@dataclass
class NoticeRaw:
    """One parsed notice, before keyword filtering / DB insertion."""

    source_id: str
    external_ref: str
    title: str
    summary: str = ""
    agency: str | None = None
    country: str | None = None
    published_at: dt.date | None = None
    deadline: dt.date | None = None
    url: str | None = None
    cpv_naics: list[str] = field(default_factory=list)
    status_hint: str | None = None  # 'open' | 'closed' | 'awarded' | None (unknown -> derived)
    raw: dict[str, Any] = field(default_factory=dict)


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path (``"tender.title"``) through nested dicts; ``None`` on any miss."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _parse_date(raw: Any) -> dt.date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    s = str(raw)
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return date_parser.parse(s, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_ted_notices(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """TED v3 ``/notices/search`` response: ``{"notices": [{"ND", "TI": {lang: title}, "PD", "links"}]}``."""
    out: list[NoticeRaw] = []
    for n in payload.get("notices") or []:
        nd = n.get("ND") or n.get("publication-number")
        if not nd:
            continue
        ti = n.get("TI")
        title = ""
        if isinstance(ti, dict):
            title = ti.get("eng") or next((v for v in ti.values() if v), "")
        elif isinstance(ti, str):
            title = ti
        url = _dig(n, "links.htmlDirect.ENG") or _dig(n, "links.html.ENG")
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{nd}",
                title=title or f"TED notice {nd}",
                country=src.country,
                published_at=_parse_date(n.get("PD")),
                url=url,
                raw=n,
            )
        )
    return out


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _parse_contracts_finder(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """UK Contracts Finder OCDS response: ``{"releases": [{"ocid", "tender": {...}, "buyer": {...}}]}``."""
    out: list[NoticeRaw] = []
    for r in payload.get("releases") or []:
        ocid = r.get("ocid") or r.get("id")
        if not ocid:
            continue
        tags = r.get("tag") or []
        status_hint = "awarded" if "award" in tags else ("closed" if "tenderCancellation" in tags else None)
        m = _UUID_RE.search(str(r.get("id") or ocid))
        url = f"https://www.contractsfinder.service.gov.uk/Notice/{m.group(0)}" if m else None
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{ocid}",
                title=_dig(r, "tender.title") or f"Contracts Finder {ocid}",
                summary=_dig(r, "tender.description") or "",
                agency=_dig(r, "buyer.name"),
                country=src.country,
                published_at=_parse_date(r.get("date")),
                deadline=_parse_date(_dig(r, "tender.tenderPeriod.endDate")),
                url=url,
                status_hint=status_hint,
                raw=r,
            )
        )
    return out


_API_PARSERS: dict[str, Callable[[dict[str, Any], TenderSource], list[NoticeRaw]]] = {
    "ted_eu": _parse_ted_notices,
    "uk_contracts_finder": _parse_contracts_finder,
}


def _parse_search_hits(hits: list[SearchHit], src: TenderSource) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for h in hits:
        if not h.url:
            continue
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{h.url}",
                title=h.title or h.url,
                summary=h.snippet or "",
                country=src.country,
                published_at=_parse_date(h.published),
                url=h.url,
                raw={"snippet": h.snippet, "engine": h.engine},
            )
        )
    return out


def _parse_rss_notices(raw_text: str, src: TenderSource) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for e in parse_feed(raw_text):
        if not e.url:
            continue
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{e.url}",
                title=e.title or e.url,
                summary=e.summary or "",
                country=src.country,
                published_at=e.published_at.date() if e.published_at else None,
                url=e.url,
            )
        )
    return out


# --------------------------------------------------------------------------
# per-kind fetchers
# --------------------------------------------------------------------------


def _fetch_api_json(src: TenderSource, keyword: str) -> list[NoticeRaw]:
    if not src.url:
        return []
    if src.query_template:
        # NOT str.format(): query_template is a JSON literal ('{"query":"...","fields":[...]}') --
        # its own braces would be misparsed as format fields. Substitute the placeholder directly,
        # JSON-string-escaping the keyword first so it can't break the surrounding JSON syntax.
        escaped_keyword = json.dumps(keyword)[1:-1]
        body = json.loads(src.query_template.replace("{keyword}", escaped_keyword))
        resp = fetch_raw_remote(src.url, method=src.method or "POST", json_body=body)
    else:
        params = {k: v.format(keyword=keyword) for k, v in (src.query_params or {}).items()}
        resp = fetch_raw_remote(f"{src.url}?{urlencode(params)}", method=src.method or "GET")
    data = resp.get("json")
    if not isinstance(data, dict):
        return []
    parser = _API_PARSERS.get(src.id)
    return parser(data, src) if parser else []


def _fetch_search(src: TenderSource) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for q in src.queries or []:
        resp = search(q, lang=src.engine_lang, max_results=8)
        if resp.error:
            log.debug("tender_search_query_failed", source=src.id, query=q, error=resp.error)
            continue
        out.extend(_parse_search_hits(resp.hits, src))
    return out


def _fetch_rss(src: TenderSource) -> list[NoticeRaw]:
    if not src.url:
        return []
    resp = fetch_raw_remote(src.url, method="GET")
    text = resp.get("text")
    if not text:
        return []
    return _parse_rss_notices(text, src)


def _collect_source_notices(src: TenderSource) -> list[NoticeRaw]:
    if src.kind == "api_json":
        seen: set[str] = set()
        out: list[NoticeRaw] = []
        for kw in (src.keywords or DEFAULT_KEYWORDS)[:MAX_KEYWORDS_PER_API_SOURCE]:
            for n in _fetch_api_json(src, kw):
                if n.external_ref not in seen:
                    seen.add(n.external_ref)
                    out.append(n)
        return out
    if src.kind == "search":
        return _fetch_search(src)
    if src.kind == "rss":
        return _fetch_rss(src)
    return []  # html: documented, not scraped (see config/tenders.yaml notes)


# --------------------------------------------------------------------------
# keyword filter / dedupe / persistence
# --------------------------------------------------------------------------


def _matches_keywords(notice: NoticeRaw, keywords: list[str]) -> list[str]:
    text = f"{notice.title} {notice.summary}".casefold()
    return [kw for kw in (keywords or DEFAULT_KEYWORDS) if kw.casefold() in text]


def _within_window(notice: NoticeRaw, since_days: int, today: dt.date) -> bool:
    """Only structured API sources (TED/Contracts Finder) carry a trustworthy publish date that's
    worth filtering on -- TED's full-text search returns its entire archive back to ~2016, so
    without this filter every scan would re-touch thousands of old notices. Search-hit/RSS
    "published" dates are unreliable enough (often missing or the crawl date) that they're never
    filtered out on age -- an undated or old-looking lead is still worth a look."""
    if notice.published_at is None:
        return True
    return (today - notice.published_at).days <= since_days


def _tender_exists(external_ref: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM tenders WHERE external_ref = %s", (external_ref,))
        return cur.fetchone() is not None


def _initial_status(notice: NoticeRaw, today: dt.date) -> str:
    if notice.status_hint in ("awarded", "closed"):
        return notice.status_hint
    if notice.deadline is not None and notice.deadline < today:
        return "closed"
    return "open"


def _as_datetime(d: dt.date | None) -> dt.datetime | None:
    return None if d is None else dt.datetime.combine(d, dt.time(), tzinfo=dt.UTC)


def _insert_tender_and_item(notice: NoticeRaw, matched_terms: list[str]) -> tuple[int | None, int]:
    """Insert the ``items`` row first (so ``tenders.item_id`` can reference it), then the
    ``tenders`` row itself. Returns ``(tender_id, item_id)`` -- ``tender_id`` is ``None`` if a
    concurrent scan already inserted the same ``external_ref`` (``ON CONFLICT DO NOTHING``)."""
    clean_text = f"{notice.title}\n\n{notice.summary}".strip()
    url = notice.url or f"urn:tender:{notice.external_ref}"
    item_id = insert_item(
        source_id=None,
        url=url,
        title=notice.title,
        lang="he" if notice.country == "IL" else "en",
        published_at=_as_datetime(notice.published_at),
        clean_text=clean_text,
        report_kind="tender",
        geography=notice.country,
    )

    today = dt.date.today()
    status = _initial_status(notice, today)
    relevance = max(1, min(10, len(matched_terms)))
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenders (
                source, external_ref, title, agency, country, published_at, deadline,
                url, cpv_naics, summary_he, relevance, matched_terms, status, item_id, raw
            )
            VALUES (
                %(source)s, %(external_ref)s, %(title)s, %(agency)s, %(country)s, %(published_at)s,
                %(deadline)s, %(url)s, %(cpv_naics)s, %(summary_he)s, %(relevance)s, %(matched_terms)s,
                %(status)s, %(item_id)s, %(raw)s
            )
            ON CONFLICT (external_ref) DO NOTHING
            RETURNING id
            """,
            {
                "source": notice.source_id,
                "external_ref": notice.external_ref,
                "title": notice.title,
                "agency": notice.agency,
                "country": notice.country,
                "published_at": _as_datetime(notice.published_at),
                "deadline": notice.deadline,
                "url": notice.url,
                "cpv_naics": notice.cpv_naics or None,
                "summary_he": "",
                "relevance": relevance,
                "matched_terms": matched_terms,
                "status": status,
                "item_id": item_id,
                "raw": Json(notice.raw),
            },
        )
        row = cur.fetchone()
    return (row["id"] if row else None), item_id


def _llm_enrich(tender_id: int, item_id: int, notice: NoticeRaw, *, role: str, interactive: bool) -> TenderExtract:
    """Best-effort LLM relevance/summary pass; overwrites the deterministic baseline only on
    success. Raises ``ResourceUnavailable``/``LLMOutputError`` to the caller, which treats either
    as "still ingested, just not yet enriched" (the deterministic row already exists)."""
    prompt = render(
        "tender_extract",
        source_name=notice.source_id,
        country=notice.country or "?",
        data=wrap_data(f"{notice.title}\n\n{notice.summary}"[:6000], item_id, notice.url or ""),
    )
    out = chat_structured(
        role,
        TenderExtract,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="classify",
        interactive=interactive,
    )
    if out.confidence >= 0.4:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tenders SET relevance=%(relevance)s, summary_he=%(summary_he)s, "
                "matched_terms=%(matched_terms)s, entities=%(entities)s WHERE id=%(id)s",
                {
                    "relevance": out.relevance,
                    "summary_he": out.summary_he,
                    "matched_terms": out.matched_terms or None,
                    "entities": out.entities or None,
                    "id": tender_id,
                },
            )
        if out.summary_he:
            update_item_fields(item_id, summary_he=out.summary_he)
    return out


def _transition_closed() -> int:
    today = dt.date.today()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tenders SET status = 'closed' "
            "WHERE status = 'open' AND deadline IS NOT NULL AND deadline < %s RETURNING id",
            (today,),
        )
        return len(cur.fetchall())


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


@dataclass
class TenderStats:
    sources_scanned: int = 0
    sources_failed: int = 0
    notices_fetched: int = 0
    matched: int = 0
    inserted: int = 0
    duplicates: int = 0
    llm_enriched: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    closed_transitioned: int = 0


LLM_BUDGET_SECONDS_DEFAULT = 15 * 60


def scan_tenders(
    since_days: int = 3,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_budget_s: float = LLM_BUDGET_SECONDS_DEFAULT,
    sources: list[TenderSource] | None = None,
) -> TenderStats:
    """FR/section-5.2 entry point: scan every configured tender source, keyword-filter, dedupe,
    insert ``tenders``+``items`` rows, best-effort LLM-enrich within ``llm_budget_s``, and
    transition passed-deadline tenders to ``status='closed'``. A single source failing (network,
    parse error, ...) never stops the others (docs/CONVENTIONS.md rule 9)."""
    stats = TenderStats()
    today = dt.date.today()
    llm_deadline = time.monotonic() + llm_budget_s
    seen_refs: set[str] = set()

    for src in sources if sources is not None else load_tender_sources():
        if src.kind == "html":
            continue
        if src.kind == "api_json" and not src.verified:
            continue
        try:
            notices = _collect_source_notices(src)
        except Exception as exc:
            log.warning("tender_source_failed", source=src.id, error=str(exc)[:200])
            stats.sources_failed += 1
            continue
        stats.sources_scanned += 1
        stats.notices_fetched += len(notices)

        for notice in notices:
            if notice.external_ref in seen_refs:
                continue
            if src.kind == "api_json" and not _within_window(notice, since_days, today):
                continue
            terms = _matches_keywords(notice, src.keywords)
            if not terms:
                continue
            stats.matched += 1
            if _tender_exists(notice.external_ref):
                stats.duplicates += 1
                seen_refs.add(notice.external_ref)
                continue
            try:
                tender_id, item_id = _insert_tender_and_item(notice, terms)
            except Exception as exc:
                log.warning("tender_insert_failed", external_ref=notice.external_ref, error=str(exc)[:200])
                continue
            seen_refs.add(notice.external_ref)
            if tender_id is None:
                stats.duplicates += 1
                continue
            stats.inserted += 1

            if time.monotonic() >= llm_deadline:
                stats.llm_deferred += 1
                continue
            try:
                _llm_enrich(tender_id, item_id, notice, role=role, interactive=interactive)
                stats.llm_enriched += 1
            except ResourceUnavailable:
                stats.llm_deferred += 1
            except LLMOutputError as exc:
                log.warning("tender_llm_enrich_failed", tender_id=tender_id, error=str(exc)[:200])
                stats.llm_failed += 1
            except Exception as exc:
                # Never let one tender's LLM enrichment call take down the whole scan (docs/
                # CONVENTIONS.md rule 9). The deterministic tenders/items rows are already inserted.
                log.warning("tender_llm_enrich_unexpected_error", tender_id=tender_id, error=str(exc)[:200])
                stats.llm_failed += 1

    stats.closed_transitioned = _transition_closed()
    log.info("tender_scan_done", **vars(stats))
    return stats
