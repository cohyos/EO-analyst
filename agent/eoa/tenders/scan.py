"""Stage: tender / RFI / RFP ingestion (section 5.2 / FR-5.2).

For each source in ``config/tenders.yaml``: fetch (``eoa.fetch.remote.fetch_raw_remote`` for
``api_json``/``rss`` sources needing the raw body; ``eoa.search.searxng_client`` for ``kind:
search`` sources, denylist-filtered) -> parse into ``NoticeRaw`` rows -> a **two-signal gate**
(``_passes_gate``: at least one PROCUREMENT-context signal -- explicit for ``search``/``rss``
sources, implicit for a structured procurement-portal ``api_json`` source like TED/Contracts
Finder -- AND at least one EO/IR/CV DOMAIN signal; server-side keyword params are unreliable, see
config/tenders.yaml notes, so this is always re-applied client-side) -> dedupe by a
globally-unique ``external_ref`` (``"<source_id>:<notice id>"``) -> LLM classification
(``chat_structured``, DATA-guarded, budget-capped) -> a **strict F24 quality gate**
(``_gate_reject_reason``, docs/QA_PROGRAM.md section 4, 2026-09-06): reject outright (never insert,
log ``tender_rejected`` with a reason) on any of -- no successful LLM classification at all;
``relevance < 6``; ``notice_type`` not one of ``rfi``/``rfp``/``rfq``/``sources_sought``/``tender``
(so an already-``award``ed notice or anything the model couldn't place is dropped, not stored as
"closed"/"awarded"); a ``deadline`` already in the past; a ``published_at`` older than 90 days; a
document-hosting/aggregator domain (Scribd, DocPlayer, Yumpu, SlideShare, ...); or an *unverified*
notice (only a search snippet, the actual page was never successfully fetched) that also has no
date at all. A notice that clears every check but states no date either way is stored with
``status='unknown'`` -- reachable only because the gate above already confirmed its page was
actually fetched -- and insert one ``tenders`` row + one ``items`` row (``report_kind`` ``"tender"``)
so the normal classify/triage/analyze pipeline covers it.

Unlike before this fix, an unavailable/deferred/failed LLM call is itself a rejection, not a
"still insert on the deterministic two-signal gate's own strength alone" degrade path -- that
degrade path is exactly how the stale/irrelevant rows this fix targets (an unrelated "Green Tech
Projects Corp." hit, a Scribd PDF reupload of an old RFP, 13 undated "unknown" rows nobody ever
actually verified) got into the DB. Finally transitions any ``status='open'`` row whose
``deadline`` has passed, or whose ``published_at`` is stale (>365 days) with no deadline at all, to
``status='closed'`` (see ``_initial_status``/``_transition_closed``, F2 2026-09-05: an undated
notice is never assumed to stay open forever), and archives any ``status='closed'`` row whose
``deadline`` passed more than 30 days ago (``_archive_stale_closed``, F24 2026-09-06).

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
from urllib.parse import urlencode, urlparse

import structlog
import yaml
from dateutil import parser as date_parser
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eoa.config import CONFIG_DIR
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.fetch.remote import fetch_raw_remote, fetch_remote
from eoa.fetch.rss import parse_feed
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.tenders import TenderExtract
from eoa.memory.relational import insert_item, update_item_fields
from eoa.search.provider import SearchHit, search

log = structlog.get_logger(__name__)

TENDERS_YAML = CONFIG_DIR / "tenders.yaml"

# Fallback if a config entry/file somehow omits these (every entry in config/tenders.yaml merges
# the shared `&kw` anchor and the file carries top-level `procurement_signals`/`deny_domains`, so
# these should never actually trigger outside of ad-hoc test fixtures).
DEFAULT_KEYWORDS = [
    "electro-optical",
    "infrared",
    "thermal imaging",
    "thermal imager",
    "FLIR",
    "targeting pod",
    "EO/IR",
    "gimbal",
    "laser rangefinder",
    "laser designator",
    "seeker",
    "counter-UAS",
    "C-UAS",
    "surveillance camera",
    "night vision",
    "image intensifier",
    "hyperspectral",
    "optronic",
    "computer vision",
    "ATR",
]

DEFAULT_PROCUREMENT_SIGNALS = [
    "tender",
    "RFP",
    "RFI",
    "RFQ",
    "solicitation",
    "sources sought",
    "invitation to tender",
    "contract notice",
    "prior information notice",
    "request for proposal",
    "request for information",
    "request for quotation",
    "call for proposals",
    "מכרז",
    "בקשה למידע",
    "בקשת מידע",
]

DEFAULT_DENY_DOMAINS = [
    "wikipedia.org",
    "reddit.com",
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "quora.com",
    "medium.com",
    "britannica.com",
    "nasa.gov",
    "marketsandmarkets.com",
    "grandviewresearch.com",
    "alliedmarketresearch.com",
    "researchandmarkets.com",
    "globenewswire.com",
    "prnewswire.com",
    "businesswire.com",
    "techradar.com",
    "sciencenotes.org",
    "scienceinsights.org",
    "coolcosmos.ipac.caltech.edu",
    "howstuffworks.com",
    "thoughtco.com",
    "investopedia.com",
    # F24: document-hosting/aggregator sites -- see config/tenders.yaml's deny_domains comment.
    "scribd.com",
    "docplayer.net",
    "docplayer.com",
    "yumpu.com",
    "slideshare.net",
    "pdfcoffee.com",
    "coursehero.com",
]

MAX_KEYWORDS_PER_API_SOURCE = 5

# F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): the strict post-classification quality gate --
# see _gate_reject_reason. Replaces the old three-tier relevance rubric (<=2 reject / ==3 unknown /
# >=4 store): a live audit of the DB (21 rows, 8 closed 2015-2025, 13 undated "unknown", including
# an unrelated "Green Tech Projects Corp." row and a Scribd PDF reupload) showed that rubric let far
# too much through, largely via the "LLM unavailable -> insert on the deterministic gate alone"
# degrade path.
RELEVANCE_MIN_ACCEPT = 6  # below this: never stored, regardless of the deterministic gate's own hit count
NOTICE_MAX_AGE_DAYS = 90  # a notice published longer ago than this is stale -- never stored
VALID_NOTICE_TYPES = frozenset({"rfi", "rfp", "rfq", "sources_sought", "tender"})


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


def _load_yaml(path: str | Path | None) -> dict[str, Any]:
    file_path = Path(path) if path is not None else TENDERS_YAML
    with file_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_tender_sources(path: str | Path | None = None) -> list[TenderSource]:
    """Load and validate every entry in ``config/tenders.yaml`` (or an alternate ``path``)."""
    raw = _load_yaml(path)
    sources = [TenderSource.model_validate(e) for e in raw.get("sources", [])]
    log.debug("tenders.sources_loaded", count=len(sources), path=str(path or TENDERS_YAML))
    return sources


def load_procurement_signals(path: str | Path | None = None) -> list[str]:
    """Top-level ``procurement_signals`` list from ``config/tenders.yaml`` -- words/phrases whose
    presence marks a notice as being *about* a solicitation/tender process (as opposed to e.g. a
    general-interest article that merely mentions a domain term)."""
    raw = _load_yaml(path)
    return list(raw.get("procurement_signals") or DEFAULT_PROCUREMENT_SIGNALS)


def load_deny_domains(path: str | Path | None = None) -> list[str]:
    """Top-level ``deny_domains`` list -- hosts (matched by exact domain or subdomain) never
    treated as a tender/RFI/RFP lead regardless of keyword matches (Wikipedia, Reddit, market-
    research-report publishers, ...): defense-in-depth against a generic web search returning
    content that happens to mention a domain term without being a procurement source at all."""
    raw = _load_yaml(path)
    return list(raw.get("deny_domains") or DEFAULT_DENY_DOMAINS)


# --------------------------------------------------------------------------
# NoticeRaw + parsing
# --------------------------------------------------------------------------


@dataclass
class NoticeRaw:
    """One parsed notice, before the gate / DB insertion."""

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


def _is_denylisted_domain(url: str, deny_domains: list[str]) -> bool:
    """True if ``url``'s host is (or is a subdomain of) one of ``deny_domains``."""
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in deny_domains)


def _parse_search_hits(hits: list[SearchHit], src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for h in hits:
        if not h.url or _is_denylisted_domain(h.url, deny_domains):
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


def _parse_rss_notices(raw_text: str, src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for e in parse_feed(raw_text):
        if not e.url or _is_denylisted_domain(e.url, deny_domains):
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
    """Requirement: the EO keyword set must ride in the API query itself, not just a post-filter
    -- ``query_template``/``query_params`` (config/tenders.yaml) both embed ``{keyword}`` directly
    in the request TED/Contracts Finder actually receive; the client-side gate below is a
    (necessary, per each source's own notes on unreliable server-side filtering) second pass, not
    the only one."""
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


def _fetch_search(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for q in src.queries or []:
        resp = search(q, lang=src.engine_lang, max_results=8)
        if resp.error:
            log.debug("tender_search_query_failed", source=src.id, query=q, error=resp.error)
            continue
        out.extend(_parse_search_hits(resp.hits, src, deny_domains))
    return out


def _fetch_rss(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    if not src.url:
        return []
    resp = fetch_raw_remote(src.url, method="GET")
    text = resp.get("text")
    if not text:
        return []
    return _parse_rss_notices(text, src, deny_domains)


def _collect_source_notices(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
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
        return _fetch_search(src, deny_domains)
    if src.kind == "rss":
        return _fetch_rss(src, deny_domains)
    return []  # html: documented, not scraped (see config/tenders.yaml notes)


# --------------------------------------------------------------------------
# two-signal gate / dedupe / status
# --------------------------------------------------------------------------


def _matches_keywords(notice: NoticeRaw, keywords: list[str]) -> list[str]:
    """DOMAIN signal: EO/IR/CV terms actually present (casefold substring) in title+summary."""
    text = f"{notice.title} {notice.summary}".casefold()
    return [kw for kw in (keywords or DEFAULT_KEYWORDS) if kw.casefold() in text]


def _has_procurement_signal(notice: NoticeRaw, src_kind: str, procurement_signals: list[str]) -> bool:
    """PROCUREMENT signal: explicit tender/RFI/RFP/... language in title+summary for a general
    source (search/rss -- could be any web content), or implicitly satisfied for a structured
    procurement-portal API (``api_json`` -- TED/Contracts Finder notices *are*, by construction,
    real tender/contract records; most don't literally spell "tender" in their title text, so
    requiring the word there would reject the overwhelming majority of genuine notices)."""
    if src_kind == "api_json":
        return True
    text = f"{notice.title} {notice.summary}".casefold()
    return any(sig.casefold() in text for sig in (procurement_signals or DEFAULT_PROCUREMENT_SIGNALS))


def _passes_gate(
    notice: NoticeRaw, src_kind: str, domain_keywords: list[str], procurement_signals: list[str]
) -> list[str]:
    """Two-signal gate (coordinator requirement): a notice is stored only if it carries at least
    one DOMAIN term AND at least one PROCUREMENT signal. Returns the matched domain terms (used
    as ``tenders.matched_terms``) on success, or ``[]`` -- treated as "gate failed" by the caller,
    including the case where domain terms matched but the procurement signal did not."""
    domain_terms = _matches_keywords(notice, domain_keywords)
    if not domain_terms:
        return []
    if not _has_procurement_signal(notice, src_kind, procurement_signals):
        return []
    return domain_terms


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


# F2 (2026-09-05): "assume open when undated" bug -- a notice with no deadline (the overwhelming
# majority of search/rss hits) used to default straight to 'open' forever. A notice whose only date
# signal (published_at) is this old, with still no deadline, is stale enough to treat as closed
# rather than showing it as an open opportunity indefinitely.
_STALE_DAYS = 365


def _initial_status(notice: NoticeRaw, today: dt.date, notice_type: str | None = None) -> str:
    """F2 status rubric. Priority order: (1) a structured source's own explicit tag
    (``status_hint`` -- e.g. Contracts Finder's OCDS ``tag``) is the most authoritative signal
    available and wins outright; (2) the LLM extraction's ``notice_type == 'award'`` (a notice
    reporting an already-signed contract, not a future ask) also means 'awarded'; (3) a known
    deadline decides open/closed; (4) with no deadline at all, a notice with no ``published_at``
    either is 'unknown' (not assumed open -- there is no date evidence either way); (5) with no
    deadline but a ``published_at`` older than ``_STALE_DAYS``, treat it as closed (stale); (6)
    otherwise open."""
    if notice.status_hint in ("awarded", "closed"):
        return notice.status_hint
    if notice_type == "award":
        return "awarded"
    if notice.deadline is not None:
        return "closed" if notice.deadline < today else "open"
    if notice.published_at is None:
        return "unknown"
    if (today - notice.published_at).days > _STALE_DAYS:
        return "closed"
    return "open"


# F13 (2026-09-05): country-by-domain fallback for the generic `kind: search` sources, which carry
# a placeholder `country` (e.g. "other"/"US" aggregate) in config/tenders.yaml rather than the
# notice's real geography. Covers the common portals seen live in production (SAM.gov/HigherGov/
# usarfp.com notices all landing as country='other' via the rfi_rfp_news/sam_gov_search sources)
# plus every structured-portal domain already in config/tenders.yaml for completeness. Only used
# when the notice's own country is missing/'other' AND the LLM extraction didn't supply one either
# -- never overrides a real value.
_DOMAIN_COUNTRY_FALLBACK: dict[str, str] = {
    "sam.gov": "US",
    "highergov.com": "US",
    "usarfp.com": "US",
    "ted.europa.eu": "EU",
    "contractsfinder.service.gov.uk": "UK",
    "mod.gov.il": "IL",
    "nspa.nato.int": "NATO",
    "ncia.nato.int": "NATO",
    "tenders.gov.au": "AU",
    "canadabuys.canada.ca": "CA",
}


def _country_from_domain(url: str | None) -> str | None:
    """Best-effort country inference from a notice URL's hostname (see ``_DOMAIN_COUNTRY_FALLBACK``
    above). Matches exact domain or subdomain, same convention as ``_is_denylisted_domain``."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    for domain, country in _DOMAIN_COUNTRY_FALLBACK.items():
        if host == domain or host.endswith("." + domain):
            return country
    return None


def _apply_extraction_to_notice(notice: NoticeRaw, extract: TenderExtract) -> None:
    """F2/F13: fill date/agency/country gaps on ``notice`` from the LLM extraction -- never
    overwrites a value the source's own structured parser already supplied (TED/Contracts Finder
    stay authoritative over their own fields; this only helps the generic search/rss sources that
    had nothing but a title+snippet to begin with)."""
    if notice.published_at is None and extract.published_at is not None:
        notice.published_at = extract.published_at
    if notice.deadline is None and extract.deadline is not None:
        notice.deadline = extract.deadline
    if not notice.agency and extract.agency:
        notice.agency = extract.agency
    if (not notice.country or notice.country == "other") and extract.country:
        notice.country = extract.country


def _apply_domain_country_fallback(notice: NoticeRaw) -> None:
    """Runs regardless of whether the LLM extraction ran/succeeded -- a pure URL-based fallback for
    when the country is still missing/generic after the above (F13)."""
    if not notice.country or notice.country == "other":
        domain_country = _country_from_domain(notice.url)
        if domain_country:
            notice.country = domain_country


def _as_datetime(d: dt.date | None) -> dt.datetime | None:
    return None if d is None else dt.datetime.combine(d, dt.time(), tzinfo=dt.UTC)


def _insert_tender_and_item(
    notice: NoticeRaw,
    matched_terms: list[str],
    *,
    relevance: int | None = None,
    summary_he: str = "",
    entities: list[str] | None = None,
    status_override: str | None = None,
    notice_type: str | None = None,
) -> tuple[int | None, int]:
    """Insert the ``items`` row first (so ``tenders.item_id`` can reference it), then the
    ``tenders`` row itself. ``relevance``/``summary_he``/``entities`` default to the deterministic
    keyword-hit baseline when the caller didn't supply an LLM-derived value (LLM unavailable);
    ``status_override`` forces ``status`` regardless of the deadline-derived value (used for the
    ``relevance == 3`` -> ``'unknown'`` rule); ``notice_type`` (from the LLM extraction, F2) feeds
    ``_initial_status``'s ``'award' -> 'awarded'`` rule when ``status_override`` doesn't already
    force something else. ``notice.agency``/``notice.country``/``notice.published_at``/
    ``notice.deadline`` are expected to already carry any LLM-filled values by the time this is
    called (see ``_apply_extraction_to_notice``/``_apply_domain_country_fallback`` in
    ``scan_tenders``). Returns ``(tender_id, item_id)`` -- ``tender_id`` is ``None`` if a concurrent
    scan already inserted the same ``external_ref`` (``ON CONFLICT DO NOTHING``)."""
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
    if summary_he:
        update_item_fields(item_id, summary_he=summary_he)

    today = dt.date.today()
    status = status_override or _initial_status(notice, today, notice_type)
    final_relevance = relevance if relevance is not None else max(1, min(10, len(matched_terms)))
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenders (
                source, external_ref, title, agency, country, published_at, deadline,
                url, cpv_naics, summary_he, relevance, matched_terms, entities, status, item_id, raw
            )
            VALUES (
                %(source)s, %(external_ref)s, %(title)s, %(agency)s, %(country)s, %(published_at)s,
                %(deadline)s, %(url)s, %(cpv_naics)s, %(summary_he)s, %(relevance)s, %(matched_terms)s,
                %(entities)s, %(status)s, %(item_id)s, %(raw)s
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
                "summary_he": summary_he or "",
                "relevance": final_relevance,
                "matched_terms": matched_terms or None,
                "entities": entities or None,
                "status": status,
                "item_id": item_id,
                "raw": Json(notice.raw),
            },
        )
        row = cur.fetchone()
    return (row["id"] if row else None), item_id


_NOTICE_FETCH_CHAR_CAP = 6000


def _fetch_notice_text(notice: NoticeRaw, src_kind: str) -> tuple[str, bool]:
    """F2/F24: for a ``search``/``rss``-hit-derived notice (title+snippet only), fetch the actual
    notice page so the LLM extraction can find real dates/agency/notice_type -- a snippet
    essentially never carries a deadline. ``api_json`` sources (TED/Contracts Finder) already
    parsed those fields structurally and don't need a page fetch. Never raises: a fetch failure
    (network, bot-block, SSRF-guard rejection, ...) just falls back to title+summary, same as
    before this fix -- a failing fetch must never block classification (docs/CONVENTIONS.md rule 9).

    Returns ``(text, page_verified)``. ``page_verified`` is ``True`` for a structured ``api_json``
    source (the record itself, not a page, is the authoritative source) or when a live page fetch
    for a ``search``/``rss`` notice actually returned real content; ``False`` when there was no URL
    to fetch, the fetch failed, or it came back empty -- meaning the caller is left with nothing
    but a search snippet/RSS blurb. F24's gate (:func:`_gate_reject_reason`) rejects an undated
    notice outright when this is ``False``: an unverified snippet with no dates is exactly the
    "unknown, undated, never actually looked at" shape that let stale/irrelevant rows in before.
    """
    base = f"{notice.title}\n\n{notice.summary}".strip()
    if src_kind == "api_json":
        return base, True
    if not notice.url:
        return base, False
    try:
        page = fetch_remote(notice.url)
    except Exception as exc:
        log.debug("tender_notice_fetch_failed", url=(notice.url or "")[:200], error=str(exc)[:200])
        return base, False
    text = (page.get("text") or "").strip()
    if text:
        return text[:_NOTICE_FETCH_CHAR_CAP], True
    return base, False


def _llm_classify(
    notice: NoticeRaw, *, role: str, interactive: bool, src_kind: str = "search"
) -> tuple[TenderExtract, bool]:
    """Pure LLM relevance/summary/date classification -- no DB writes (the caller decides what to
    do with the result, including whether to store anything at all: see ``scan_tenders``'s gate).
    DATA-guarded via ``wrap_data``, keyed by the notice's ``external_ref`` since no ``items`` row
    exists yet at this point (the gate runs *before* insertion). ``src_kind`` controls whether the
    notice page itself is fetched first (see ``_fetch_notice_text``). Returns ``(extract,
    page_verified)`` -- the latter feeds F24's "unverified + undated -> reject" rule."""
    text_for_llm, page_verified = _fetch_notice_text(notice, src_kind)
    prompt = render(
        "tender_extract",
        source_name=notice.source_id,
        country=notice.country or "?",
        data=wrap_data(text_for_llm[:_NOTICE_FETCH_CHAR_CAP], notice.external_ref, notice.url or ""),
    )
    extract = chat_structured(
        role,
        TenderExtract,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="classify",
        interactive=interactive,
    )
    return extract, page_verified


def _gate_reject_reason(
    notice: NoticeRaw,
    extract: TenderExtract | None,
    *,
    page_verified: bool,
    today: dt.date,
    deny_domains: list[str],
) -> str | None:
    """F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): the final quality gate applied just before a
    notice is ever inserted. Returns a short machine-readable rejection reason (logged as
    ``tender_rejected``), or ``None`` if the notice clears every check and may be stored.

    Nothing is stored any more without a real, successful LLM classification -- ``extract is None``
    (LLM deferred/failed/unavailable) is itself a rejection now. The old "insert on the
    deterministic two-signal gate's own strength alone" degrade path is exactly how the
    stale/irrelevant rows this fix targets got in (an unrelated "Green Tech Projects Corp." hit, a
    Scribd PDF reupload, 13 undated rows nobody ever actually verified).

    ``'unknown'`` status (assigned by the caller via :func:`_initial_status`) remains possible only
    for a notice that clears every other check but has no ``deadline``/``published_at`` at all --
    which by construction here means the page WAS actually verified (``page_verified``), it's a
    real, current, sufficiently-relevant notice; it simply doesn't state a date.
    """
    if notice.status_hint in ("awarded", "closed"):
        return f"status_hint_{notice.status_hint}"
    if extract is None:
        return "no_llm_classification"
    if not extract.relevant or extract.relevance < RELEVANCE_MIN_ACCEPT:
        return f"relevance_{extract.relevance}_below_{RELEVANCE_MIN_ACCEPT}"
    if extract.notice_type not in VALID_NOTICE_TYPES:
        return f"notice_type_{extract.notice_type}"
    if notice.deadline is not None and notice.deadline < today:
        return "deadline_passed"
    if notice.published_at is not None and (today - notice.published_at).days > NOTICE_MAX_AGE_DAYS:
        return "published_over_90_days"
    if _is_denylisted_domain(notice.url or "", deny_domains):
        return "denylisted_domain"
    if not page_verified and notice.deadline is None and notice.published_at is None:
        return "unverified_undated"
    return None


def _transition_closed() -> int:
    """Nightly status transition (F2): close any ``'open'`` tender whose ``deadline`` has passed
    (unchanged), AND any ``'open'`` tender that has no deadline at all but whose ``published_at`` is
    older than ``_STALE_DAYS`` -- the "assume open forever when undated" bug this fix addresses
    also applied to rows already sitting in the DB from before a deadline could be filled in."""
    today = dt.date.today()
    stale_before = today - dt.timedelta(days=_STALE_DAYS)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tenders SET status = 'closed'
            WHERE status = 'open' AND (
                (deadline IS NOT NULL AND deadline < %(today)s)
                OR (deadline IS NULL AND published_at IS NOT NULL AND published_at < %(stale_before)s)
            )
            RETURNING id
            """,
            {"today": today, "stale_before": stale_before},
        )
        return len(cur.fetchall())


# F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): a tender/RFI/RFP stays useful as a "recently
# closed" reference for a while, but a board cluttered with notices closed months or years ago
# (the review found rows closed 2015-2025 still showing) is not -- archived rows are never
# deleted (F1: no fetched-content row is ever destroyed outright), just excluded from the tenders
# board's default view (see eoa.api.services / TendersPage).
_ARCHIVE_AFTER_DAYS = 30


def redrive_all_tender_statuses(today: dt.date | None = None) -> int:
    """Round-3 (D9 finding 4b, docs/qa/loop/round_1_judge.md): a whole-table maintenance sweep
    that re-derives ``status`` purely from ``deadline`` vs ``today`` for every row that still
    carries one of the two "current" statuses (``'open'``/``'unknown'``) -- closes any row whose
    deadline has now passed, and (re-)opens an ``'unknown'`` row that has since acquired a
    still-future deadline (e.g. an LLM re-extraction backfilled one after the fact). Idempotent
    and safe to run as often as desired (a dedicated nightly/maintenance entry point, unlike
    ``_transition_closed``/``_archive_stale_closed`` above, which only run as the tail end of a
    full ``scan_tenders`` pass); never touches a terminal status this module assigns deliberately
    (``'awarded'``/``'archived'``) or a row with no deadline at all (that undated case is exactly
    what ``_transition_closed``'s ``_STALE_DAYS`` rule already covers). Returns the number of rows
    changed."""
    today = today or dt.date.today()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tenders SET status = 'closed' "
            "WHERE status IN ('open', 'unknown') AND deadline IS NOT NULL AND deadline < %(today)s "
            "RETURNING id",
            {"today": today},
        )
        closed = len(cur.fetchall())
        cur.execute(
            "UPDATE tenders SET status = 'open' "
            "WHERE status = 'unknown' AND deadline IS NOT NULL AND deadline >= %(today)s "
            "RETURNING id",
            {"today": today},
        )
        reopened = len(cur.fetchall())
    if closed or reopened:
        log.info("tender_statuses_redriven", closed=closed, reopened=reopened)
    return closed + reopened


def _archive_stale_closed() -> int:
    """Nightly transition (F24): move any ``'closed'`` tender to ``'archived'`` once it has been
    closed for more than ``_ARCHIVE_AFTER_DAYS`` -- measured from ``deadline`` when known, else
    ``published_at``, else (both absent -- a stale-undated closure, see ``_transition_closed``)
    ``updated_at`` (the moment it was actually transitioned to ``'closed'``)."""
    today = dt.date.today()
    archive_before = today - dt.timedelta(days=_ARCHIVE_AFTER_DAYS)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tenders SET status = 'archived'
            WHERE status = 'closed'
              AND COALESCE(deadline, published_at::date, updated_at::date) < %(archive_before)s
            RETURNING id
            """,
            {"archive_before": archive_before},
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
    duplicates: int = 0
    llm_used: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    llm_rejected: int = 0  # LLM ran and explicitly said "not relevant" / below the relevance floor
    gate_rejected: int = 0  # F24: passed matching but failed the strict post-classification gate
    # (no LLM classification at all, an invalid notice_type, an expired deadline, a stale
    # publish date, a denylisted document-host domain, or an unverified undated snippet)
    inserted: int = 0
    closed_transitioned: int = 0
    archived_transitioned: int = 0
    statuses_redriven: int = 0  # round-3 D9 finding 4b: redrive_all_tender_statuses()


LLM_BUDGET_SECONDS_DEFAULT = 15 * 60


def scan_tenders(
    since_days: int = 3,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_budget_s: float = LLM_BUDGET_SECONDS_DEFAULT,
    sources: list[TenderSource] | None = None,
) -> TenderStats:
    """FR/section-5.2 entry point: scan every configured tender source, apply the two-signal gate,
    dedupe, LLM-relevance-gate within ``llm_budget_s`` (best-effort -- degrades to the
    deterministic baseline when unavailable, per the module docstring), insert ``tenders``+
    ``items`` rows, and transition passed-deadline tenders to ``status='closed'``. A single source
    failing (network, parse error, ...) never stops the others (docs/CONVENTIONS.md rule 9)."""
    stats = TenderStats()
    today = dt.date.today()
    llm_deadline = time.monotonic() + llm_budget_s
    seen_refs: set[str] = set()
    procurement_signals = load_procurement_signals()
    deny_domains = load_deny_domains()

    for src in sources if sources is not None else load_tender_sources():
        if src.kind == "html":
            continue
        if src.kind == "api_json" and not src.verified:
            continue
        try:
            notices = _collect_source_notices(src, deny_domains)
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
            domain_terms = _passes_gate(notice, src.kind, src.keywords, procurement_signals)
            if not domain_terms:
                continue
            stats.matched += 1
            if _tender_exists(notice.external_ref):
                stats.duplicates += 1
                seen_refs.add(notice.external_ref)
                continue

            extract: TenderExtract | None = None
            page_verified = False
            if time.monotonic() < llm_deadline:
                try:
                    extract, page_verified = _llm_classify(
                        notice, role=role, interactive=interactive, src_kind=src.kind
                    )
                    stats.llm_used += 1
                except ResourceUnavailable:
                    stats.llm_deferred += 1
                except LLMOutputError as exc:
                    log.warning(
                        "tender_llm_classify_failed", external_ref=notice.external_ref, error=str(exc)[:200]
                    )
                    stats.llm_failed += 1
                except Exception as exc:
                    # Never let one notice's LLM call take down the whole scan (docs/
                    # CONVENTIONS.md rule 9) -- it just falls through to F24's gate below, which
                    # rejects an unclassified notice outright (see _gate_reject_reason).
                    log.warning(
                        "tender_llm_classify_unexpected_error",
                        external_ref=notice.external_ref,
                        error=str(exc)[:200],
                    )
                    stats.llm_failed += 1
            else:
                stats.llm_deferred += 1

            # F2/F13: fill in date/agency/country gaps from the LLM extraction (when it ran and
            # succeeded), then fall back to the URL-domain country table regardless -- both mutate
            # `notice` in place so the gate below and `_insert_tender_and_item` (which reads
            # notice.* directly) pick them up without needing their own signatures to grow further.
            if extract is not None:
                _apply_extraction_to_notice(notice, extract)
            _apply_domain_country_fallback(notice)

            reject_reason = _gate_reject_reason(
                notice, extract, page_verified=page_verified, today=today, deny_domains=deny_domains
            )
            if reject_reason is not None:
                if extract is not None and reject_reason.startswith("relevance_"):
                    stats.llm_rejected += 1
                else:
                    stats.gate_rejected += 1
                log.info(
                    "tender_rejected",
                    external_ref=notice.external_ref,
                    reason=reject_reason,
                    relevance=(extract.relevance if extract else None),
                )
                seen_refs.add(notice.external_ref)
                continue

            # Past the gate: `extract` is guaranteed non-None (a None extract always yields
            # "no_llm_classification" above), so `_initial_status` naturally resolves to 'open'
            # (a future deadline or a recent publish date -- both already gate-verified) or
            # 'unknown' (no date at all, but the gate has already confirmed the page was actually
            # verified) -- never 'closed'/'awarded', both of which are gate-rejected outright.
            assert extract is not None
            try:
                tender_id, _item_id = _insert_tender_and_item(
                    notice,
                    extract.matched_terms or domain_terms,
                    relevance=extract.relevance,
                    summary_he=extract.summary_he,
                    entities=extract.entities,
                    notice_type=extract.notice_type,
                )
            except Exception as exc:
                log.warning("tender_insert_failed", external_ref=notice.external_ref, error=str(exc)[:200])
                continue
            seen_refs.add(notice.external_ref)
            if tender_id is None:
                stats.duplicates += 1
                continue
            stats.inserted += 1

    stats.closed_transitioned = _transition_closed()
    stats.archived_transitioned = _archive_stale_closed()
    # D9 finding 4b (round-3): a comprehensive whole-table status re-derivation on top of the two
    # targeted transitions above -- also reopens an 'unknown' row that has since acquired a
    # still-future deadline, which neither of the above ever does.
    stats.statuses_redriven = redrive_all_tender_statuses(today)
    log.info("tender_scan_done", **vars(stats))
    return stats
