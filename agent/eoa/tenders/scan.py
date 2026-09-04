"""Stage: tender / RFI / RFP ingestion (section 5.2 / FR-5.2).

For each source in ``config/tenders.yaml``: fetch (``eoa.fetch.remote.fetch_raw_remote`` for
``api_json``/``rss`` sources needing the raw body; ``eoa.search.searxng_client`` for ``kind:
search`` sources, denylist-filtered) -> parse into ``NoticeRaw`` rows -> a **two-signal gate**
(``_passes_gate``: at least one PROCUREMENT-context signal -- explicit for ``search``/``rss``
sources, implicit for a structured procurement-portal ``api_json`` source like TED/Contracts
Finder -- AND at least one EO/IR/CV DOMAIN signal; server-side keyword params are unreliable, see
config/tenders.yaml notes, so this is always re-applied client-side) -> dedupe by a
globally-unique ``external_ref`` (``"<source_id>:<notice id>"``) -> **LLM relevance gate**
(``chat_structured``, DATA-guarded, budget-capped: ``relevance <= 2`` -> not stored at all,
``== 3`` -> stored with ``status='unknown'``, ``>= 4`` -> stored normally) -> insert one
``tenders`` row + one ``items`` row (``report_kind`` ``"tender"``) so the normal
classify/triage/analyze pipeline covers it.

When the LLM is unavailable/deferred/fails, the row still gets inserted using the deterministic
keyword-hit relevance/status (FR-9 / "never invent" -- a stalled model degrades to "still
ingested on the strength of the two-signal gate alone", never to a fabricated verdict; but a model
that *did* respond and said "this is not relevant" is trusted and blocks persistence). Finally
transitions any ``status='open'`` row whose ``deadline`` has passed to ``status='closed'``.

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
from eoa.fetch.remote import fetch_raw_remote
from eoa.fetch.rss import parse_feed
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.tenders import TenderExtract
from eoa.memory.relational import insert_item, update_item_fields
from eoa.search.searxng_client import SearchHit, search

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
]

MAX_KEYWORDS_PER_API_SOURCE = 5

# Relevance thresholds (LLM gate -- see module docstring / coordinator spec).
RELEVANCE_REJECT_MAX = 2  # <= this: not stored at all
RELEVANCE_UNKNOWN = 3  # == this: stored, but status forced to 'unknown'


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


def _initial_status(notice: NoticeRaw, today: dt.date) -> str:
    if notice.status_hint in ("awarded", "closed"):
        return notice.status_hint
    if notice.deadline is not None and notice.deadline < today:
        return "closed"
    return "open"


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
) -> tuple[int | None, int]:
    """Insert the ``items`` row first (so ``tenders.item_id`` can reference it), then the
    ``tenders`` row itself. ``relevance``/``summary_he``/``entities`` default to the deterministic
    keyword-hit baseline when the caller didn't supply an LLM-derived value (LLM unavailable);
    ``status_override`` forces ``status`` regardless of the deadline-derived value (used for the
    ``relevance == 3`` -> ``'unknown'`` rule). Returns ``(tender_id, item_id)`` -- ``tender_id`` is
    ``None`` if a concurrent scan already inserted the same ``external_ref`` (``ON CONFLICT DO
    NOTHING``)."""
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
    status = status_override or _initial_status(notice, today)
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


def _llm_classify(notice: NoticeRaw, *, role: str, interactive: bool) -> TenderExtract:
    """Pure LLM relevance/summary classification -- no DB writes (the caller decides what to do
    with the result, including whether to store anything at all: see ``scan_tenders``'s relevance
    gate). DATA-guarded via ``wrap_data``, keyed by the notice's ``external_ref`` since no
    ``items`` row exists yet at this point (the gate runs *before* insertion)."""
    prompt = render(
        "tender_extract",
        source_name=notice.source_id,
        country=notice.country or "?",
        data=wrap_data(f"{notice.title}\n\n{notice.summary}"[:6000], notice.external_ref, notice.url or ""),
    )
    return chat_structured(
        role,
        TenderExtract,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="classify",
        interactive=interactive,
    )


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
    duplicates: int = 0
    llm_used: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    llm_rejected: int = 0
    inserted: int = 0
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
            if time.monotonic() < llm_deadline:
                try:
                    extract = _llm_classify(notice, role=role, interactive=interactive)
                    stats.llm_used += 1
                except ResourceUnavailable:
                    stats.llm_deferred += 1
                except LLMOutputError as exc:
                    log.warning("tender_llm_classify_failed", external_ref=notice.external_ref, error=str(exc)[:200])
                    stats.llm_failed += 1
                except Exception as exc:
                    # Never let one notice's LLM call take down the whole scan (docs/
                    # CONVENTIONS.md rule 9) -- fall back to the deterministic gate's own verdict.
                    log.warning(
                        "tender_llm_classify_unexpected_error", external_ref=notice.external_ref, error=str(exc)[:200]
                    )
                    stats.llm_failed += 1
            else:
                stats.llm_deferred += 1

            if extract is not None and (not extract.relevant or extract.relevance <= RELEVANCE_REJECT_MAX):
                stats.llm_rejected += 1
                log.info(
                    "tender_llm_rejected",
                    external_ref=notice.external_ref,
                    relevance=extract.relevance,
                    relevant=extract.relevant,
                )
                seen_refs.add(notice.external_ref)
                continue

            status_override = "unknown" if extract is not None and extract.relevance == RELEVANCE_UNKNOWN else None
            try:
                if extract is not None:
                    tender_id, _item_id = _insert_tender_and_item(
                        notice,
                        extract.matched_terms or domain_terms,
                        relevance=extract.relevance,
                        summary_he=extract.summary_he,
                        entities=extract.entities,
                        status_override=status_override,
                    )
                else:
                    tender_id, _item_id = _insert_tender_and_item(notice, domain_terms)
            except Exception as exc:
                log.warning("tender_insert_failed", external_ref=notice.external_ref, error=str(exc)[:200])
                continue
            seen_refs.add(notice.external_ref)
            if tender_id is None:
                stats.duplicates += 1
                continue
            stats.inserted += 1

    stats.closed_transitioned = _transition_closed()
    log.info("tender_scan_done", **vars(stats))
    return stats
