"""Datasheet hunt (PD-datasheet, 2026-09-09, LESSONS-1 item 1): for one product, search the
vendor's own domain(s) and known EO/IR product catalogues for a brochure/datasheet/spec-sheet PDF
(or, failing that, the vendor's own product page), download it, and extract its text.

This is the single biggest gap ``docs/qa/content_review/LESSONS-fable-dossier.md`` found between a
hand-written product review and the automated pipeline: the manual review's entire specification
table came from the 2023 Elbit brochure, and the pipeline never looked for one at all. This module
is deliberately network-only and DB/corpus-free (no import of ``eoa.dossier.corpus`` or
``eoa.dossier.plan``) -- :func:`hunt_datasheets` takes plain strings in and returns plain dicts out,
so ``eoa.dossier.plan.run_plan`` (the caller, which owns ``CorpusResult.registry``/``.datasheets``
and the citation-numbering discipline) can register the results itself, and a unit test here never
needs a fake ``CorpusResult`` at all -- see that module's own wiring for how the two meet.

``search`` (``eoa.search.provider.search``) and ``fetch_pdf``/``looks_like_pdf_url``
(``eoa.search.pdf_reader``) are imported by name (not behind an inner function) specifically so a
test can ``monkeypatch.setattr(datasheet, "search", fake_search)`` / ``fetch_pdf`` -- the same
"module-level name, not a lazy import" convention ``eoa.dossier.plan`` already uses for
``investigate``/``fetch_remote``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import structlog

from eoa.search.pdf_reader import fetch_pdf, looks_like_pdf_url
from eoa.search.provider import search

log = structlog.get_logger(__name__)

#: Generic product/brochure catalogue sites worth searching even when they aren't the vendor's own
#: domain -- deliberately small and specific to defense EO/IR (not a general product-search list).
KNOWN_CATALOG_DOMAINS: tuple[str, ...] = (
    "instro.com",
    "army-technology.com",
    "naval-technology.com",
    "airforce-technology.com",
    "defense-update.com",
)

#: Query templates run against the vendor's own domain (``site:<domain> ...``) plus a plain, no-site
#: fallback set -- both use the same templates, ``build_datasheet_queries`` decides which combination
#: of (template, site restriction) pairs to actually emit.
_QUERY_TEMPLATES: tuple[str, ...] = (
    "{product} datasheet",
    "{product} datasheet pdf",
    "{product} brochure pdf",
    "{product} spec sheet pdf",
    "{product} {vendor} datasheet",
)

#: How many search hits (across all queries, deduped) are kept as download candidates.
_MAX_CANDIDATES = 12
#: How many PDFs are actually downloaded+parsed -- the search itself is cheap; a PDF download+parse
#: is not, and a brochure hunt only ever needs the first usable hit per product.
_MAX_PDF_DOWNLOADS = 4
#: When no PDF at all could be downloaded, how many non-PDF product/catalogue pages (fetched via the
#: plain HTML reader) are kept as a fallback "datasheet-equivalent" text source instead.
_MAX_FALLBACK_PAGES = 2

_KEYWORD_HINTS = ("datasheet", "data-sheet", "data sheet", "brochure", "spec sheet", "specsheet", "fact sheet")


def _host(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _norm(url: str) -> str:
    """Local dedup key -- deliberately not ``eoa.dossier.plan.normalize_url`` (importing it would
    create ``plan`` <-> ``datasheet`` module-load cycle, since ``plan`` imports this module). Same
    idea, smaller: lower-cased host (``www.`` stripped) + path with trailing slash dropped."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"{netloc}{parts.path.rstrip('/')}"


def build_datasheet_queries(
    product_name: str, vendor: str | None, *, vendor_domain: str | None, catalog_domains: tuple[str, ...] = KNOWN_CATALOG_DOMAINS
) -> list[tuple[str, str]]:
    """Pure, deterministic query list: ``[(query_text, lang), ...]``. Vendor-site-restricted queries
    (when ``vendor_domain`` is known) come first, then a plain no-site query set, then one
    ``site:<catalog domain> <product>`` query per known catalogue -- ranking candidates from the
    likeliest source first without ever excluding a plain web search."""
    product = (product_name or "").strip()
    vendor_name = (vendor or "").strip()
    if not product:
        return []
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        key = q.casefold()
        if key in seen:
            return
        seen.add(key)
        queries.append((q, "en"))

    if vendor_domain:
        for tmpl in _QUERY_TEMPLATES:
            base = tmpl.format(product=product, vendor=vendor_name)
            _add(f"site:{vendor_domain} {base}")
    for tmpl in _QUERY_TEMPLATES:
        _add(tmpl.format(product=product, vendor=vendor_name))
    for domain in catalog_domains:
        if domain == vendor_domain:
            continue
        _add(f"site:{domain} {product}")
    return queries


def _looks_datasheet_ish(url: str, title: str) -> bool:
    hay = f"{url} {title}".lower()
    return any(hint in hay for hint in _KEYWORD_HINTS)


def _rank_key(url: str, title: str, *, vendor_domain: str | None) -> tuple[int, int, int]:
    """Lower sorts first. Rank 0: a PDF URL that also reads as a datasheet/brochure. Rank 1: any
    other PDF URL. Rank 2: a non-PDF page that at least reads as a datasheet/brochure. Rank 3:
    everything else. Within a rank, a hit on the vendor's own domain sorts ahead of one that
    isn't (0 vs 1); a known catalogue domain (KNOWN_CATALOG_DOMAINS) then ahead of a plain hit."""
    is_pdf = looks_like_pdf_url(url)
    is_ds = _looks_datasheet_ish(url, title)
    if is_pdf and is_ds:
        rank = 0
    elif is_pdf:
        rank = 1
    elif is_ds:
        rank = 2
    else:
        rank = 3
    host = _host(url)
    on_vendor = 0 if (vendor_domain and (host == vendor_domain or host.endswith("." + vendor_domain))) else 1
    on_catalog = 0 if any(host == d or host.endswith("." + d) for d in KNOWN_CATALOG_DOMAINS) else 1
    return (rank, on_vendor, on_catalog)


def gather_candidates(
    product_name: str,
    vendor: str | None,
    *,
    vendor_domain: str | None,
    max_candidates: int = _MAX_CANDIDATES,
    catalog_domains: tuple[str, ...] = KNOWN_CATALOG_DOMAINS,
) -> list[dict[str, str]]:
    """Runs :func:`build_datasheet_queries` through ``eoa.search.provider.search`` (module-level
    name, monkeypatchable) and returns up to ``max_candidates`` deduped, ranked
    ``{"url", "title"}`` dicts -- the best (most likely to be an actual datasheet) first. Never
    raises: a single query's own search failure is logged and skipped, matching the "no source
    ever takes the whole build down" discipline every other network-touching stage in this package
    follows."""
    queries = build_datasheet_queries(product_name, vendor, vendor_domain=vendor_domain, catalog_domains=catalog_domains)
    seen: dict[str, dict[str, str]] = {}
    for query, lang in queries:
        try:
            resp = search(query, lang, categories="general", max_results=8)
        except Exception as exc:
            log.warning("datasheet_search_failed", query=query[:120], error=str(exc)[:200])
            continue
        if resp.error:
            continue
        for hit in resp.hits:
            url = (hit.url or "").strip()
            if not url:
                continue
            key = _norm(url)
            if key in seen:
                continue
            seen[key] = {"url": url, "title": (hit.title or "").strip()}
    ranked = sorted(seen.values(), key=lambda c: _rank_key(c["url"], c["title"], vendor_domain=vendor_domain))
    return ranked[:max_candidates]


def hunt_datasheets(
    product_name: str,
    vendor: str | None,
    aliases: list[str] | None = None,
    *,
    vendor_domain: str | None = None,
    max_downloads: int = _MAX_PDF_DOWNLOADS,
    max_fallback_pages: int = _MAX_FALLBACK_PAGES,
    catalog_domains: tuple[str, ...] = KNOWN_CATALOG_DOMAINS,
) -> list[dict[str, Any]]:
    """The full hunt: search -> rank -> download the top PDFs -> extract text; if NO PDF yielded
    usable text, fall back to reading the top ``max_fallback_pages`` non-PDF candidate pages
    (vendor product pages / catalogue entries -- ``eoa.fetch.remote.fetch_remote``, the same plain
    page reader ``eoa.dossier.plan``'s must-read step already uses) so a product whose vendor never
    published a downloadable PDF (only an HTML product page) still gets *something* registered as a
    ``source_kind="datasheet"`` source, per the task's own explicit acceptance case ("the
    elbitsystems.com product page + instro.com page" as an accepted alternative to a literal PDF).

    Returns a list of ``{"url", "title", "text", "pages", "kind"}`` dicts (``"kind"`` is ``"pdf"``
    or ``"page"``) -- callers (``eoa.dossier.plan.run_plan``) register each as a
    ``source_kind="datasheet"``/``reliability="primary"`` registry row and fold the text into the
    shared research context before the specification-ish topics run. Never raises."""
    if not (product_name or "").strip():
        return []
    try:
        candidates = gather_candidates(
            product_name, vendor, vendor_domain=vendor_domain, catalog_domains=catalog_domains
        )
    except Exception as exc:
        log.warning("datasheet_gather_candidates_failed", error=str(exc)[:200])
        return []

    results: list[dict[str, Any]] = []
    pdf_candidates = [c for c in candidates if looks_like_pdf_url(c["url"])]
    for cand in pdf_candidates[:max_downloads]:
        try:
            pdf = fetch_pdf(cand["url"])
        except Exception as exc:
            log.info("datasheet_pdf_fetch_failed", url=cand["url"][:300], error=str(exc)[:200])
            continue
        results.append(
            {
                "url": pdf["url"],
                "title": pdf.get("title") or cand.get("title") or "",
                "text": pdf["text"],
                "pages": pdf.get("pages"),
                "kind": "pdf",
            }
        )
        log.info("datasheet_pdf_found", url=pdf["url"][:300], pages=pdf.get("pages"), chars=len(pdf["text"]))

    if results:
        return results

    # Fallback: no downloadable PDF anywhere -- read the top non-PDF candidates as plain pages
    # (deliberately lazy-imported: eoa.fetch.remote pulls in eoa.fetch.html/eoa.fetch.sanitize,
    # only worth the import cost on the fallback path, not every datasheet hunt).
    from eoa.fetch.remote import fetch_remote

    fallback_candidates = [c for c in candidates if not looks_like_pdf_url(c["url"])][:max_fallback_pages]
    for cand in fallback_candidates:
        try:
            page = fetch_remote(cand["url"])
        except Exception as exc:
            log.info("datasheet_fallback_page_failed", url=cand["url"][:300], error=str(exc)[:200])
            continue
        text = " ".join((page.get("text") or "").split())
        if not text:
            continue
        results.append(
            {
                "url": cand["url"],
                "title": (page.get("title") or "").strip() or cand.get("title") or "",
                "text": text[:60_000],
                "pages": None,
                "kind": "page",
            }
        )
        log.info("datasheet_fallback_page_found", url=cand["url"][:300], chars=len(text))
    return results


__all__ = [
    "KNOWN_CATALOG_DOMAINS",
    "build_datasheet_queries",
    "gather_candidates",
    "hunt_datasheets",
]
