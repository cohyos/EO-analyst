"""PDF datasheet/brochure reader (PD-datasheet, 2026-09-09) for the product dossier's datasheet
hunt (``eoa.dossier.datasheet``): downloads one PDF URL and extracts its text with ``pypdf``.

Deliberately its own small module rather than a branch inside ``eoa.fetch.html``/``eoa.fetch.remote``
(``eoa.fetch.html.fetch_page`` always decodes the response body as text -- fine for HTML, wrong for
a PDF's binary bytes) -- this is the "existing page reader's PDF branch" the task brief pointed at,
made a sibling module instead once ``agent/eoa/search/`` turned out to hold no such branch yet.

Same SSRF discipline as ``eoa.fetch.remote``/``eoa.fetch.html`` (``docs/CONVENTIONS.md`` rule 13):
every hop -- the initial URL and every redirect -- is validated with
``eoa.fetch.remote.assert_public_http_url`` before it is requested, and the download is capped at
:data:`MAX_PDF_BYTES`. Unlike ``eoa.fetch.remote.fetch_remote``, this module has no ``EOA_ROLE ==
"agent"`` job-queue path yet (the `fetcher` container's own job-kind dispatch is
``docker/fetcher``'s file, out of this task's ownership) -- it always fetches in-process. In
practice this is not a live gap today: the product dossier only ever builds on host/CLI
(``eoa.dossier.report.build_product_dossier``), never inside the isolated ``agent`` container's
item-ingestion pipeline. Flagged here for whoever eventually wires an ``agent``-role PDF job kind.
"""

from __future__ import annotations

import io
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

from eoa.errors import FetchError
from eoa.fetch.remote import assert_public_http_url

log = structlog.get_logger(__name__)

#: Wall-clock budget for one PDF download -- brochures are typically a few MB at most; generous
#: enough for a slow vendor CDN without letting one hung download stall a whole datasheet hunt.
DEFAULT_TIMEOUT_S = 40.0
#: Hard byte cap on the downloaded body -- refuses (rather than silently truncating) anything
#: larger, matching ``eoa.fetch.html``'s own "cap, don't truncate-and-pretend" discipline.
MAX_PDF_BYTES = 20_000_000
#: Bound on how many of a multi-page PDF's own pages get text-extracted -- a brochure is a handful
#: of pages; a scanned multi-hundred-page manual (rare, but possible from a catalogue site) must
#: not turn one datasheet fetch into a multi-minute CPU-bound extraction.
MAX_PAGES_EXTRACTED = 60
#: Bound on how many characters of extracted text are kept -- generous for a brochure (a few
#: thousand chars/page at most) while still bounding what a single PDF can inject into the corpus.
MAX_TEXT_CHARS = 120_000
_MAX_REDIRECT_HOPS = 5
_USER_AGENT = "eo-analyst/0.1 (+local OSINT research; contact: none)"


def looks_like_pdf_url(url: str) -> bool:
    """A cheap, false-negative-tolerant pre-filter (never the only gate -- :func:`fetch_pdf` always
    verifies the real ``content-type``/magic bytes after downloading) so a candidate-ranking step
    can prefer an obviously-PDF URL without paying for a download first."""
    path = urlsplit((url or "").strip()).path.lower()
    return path.endswith(".pdf")


def _validated_get(url: str, *, timeout_s: float, max_bytes: int) -> httpx.Response:
    """Manual redirect loop mirroring ``eoa.fetch.remote._fetch_local``'s own Q2-4 discipline: every
    hop -- including each redirect target -- is re-validated with ``assert_public_http_url`` BEFORE
    it is requested, never trusting `httpx`'s built-in redirect-follow (which would connect first,
    validate after, if at all)."""
    current = url
    with httpx.Client(timeout=timeout_s, follow_redirects=False, headers={"User-Agent": _USER_AGENT}) as client:
        for _ in range(_MAX_REDIRECT_HOPS + 1):
            assert_public_http_url(current)
            resp = client.get(current)
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                continue
            resp.raise_for_status()
            content_length = resp.headers.get("content-length")
            if content_length is not None and int(content_length) > max_bytes:
                raise FetchError(f"pdf too large ({content_length} bytes, cap {max_bytes}) at {current[:200]}")
            return resp
    raise FetchError(f"too many redirects fetching pdf at {url[:200]}")


def _extract_pdf_text(body: bytes, *, max_pages: int) -> tuple[str, int]:
    """``(joined_text, total_page_count)``. Raises :class:`FetchError` for anything ``pypdf`` can't
    parse at all (encrypted with no empty password, corrupt, not really a PDF despite the
    content-type) -- callers (``eoa.dossier.datasheet``) catch and skip, one bad PDF must never
    break the datasheet hunt."""
    import pypdf

    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise FetchError(f"encrypted pdf, could not decrypt: {exc}") from exc
        total_pages = len(reader.pages)
        chunks: list[str] = []
        for page in reader.pages[:max_pages]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception as exc:
                log.debug("pdf_page_extract_failed", error=str(exc)[:200])
        return "\n".join(c for c in chunks if c), total_pages
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"unparsable pdf: {exc}") from exc


def _pdf_title(body: bytes) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(body))
        meta = reader.metadata
        title = (meta.title if meta else "") or ""
        return title.strip()
    except Exception:
        return ""


def fetch_pdf(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = MAX_PDF_BYTES,
    max_pages: int = MAX_PAGES_EXTRACTED,
) -> dict[str, Any]:
    """Download + extract text from one PDF url.

    Returns ``{"url", "title", "text", "pages", "byte_size", "truncated"}`` -- ``pages`` is the
    PDF's own total page count (even when ``max_pages`` capped how many were actually read);
    ``truncated`` is ``True`` when :data:`MAX_TEXT_CHARS` cut the extracted text short.

    Raises :class:`~eoa.errors.FetchError` for every failure mode (SSRF-refused host, non-PDF
    content, oversized body, network error, unparsable/undecryptable PDF, or a PDF with no
    extractable text at all) -- callers are expected to catch this and skip the candidate, exactly
    the discipline every other network-touching helper in this codebase already follows
    (``docs/CONVENTIONS.md`` rule 9: a single bad source must never fail the whole build).
    """
    assert_public_http_url(url)
    try:
        resp = _validated_get(url, timeout_s=timeout_s, max_bytes=max_bytes)
    except FetchError:
        raise
    except httpx.HTTPError as exc:
        raise FetchError(f"pdf fetch failed for {url[:200]}: {exc}") from exc
    body = resp.content
    if len(body) > max_bytes:
        raise FetchError(f"pdf too large ({len(body)} bytes, cap {max_bytes}) at {url[:200]}")
    content_type = resp.headers.get("content-type", "").lower()
    if "pdf" not in content_type and body[:5] != b"%PDF-":
        raise FetchError(f"not a pdf (content-type={content_type!r}) at {url[:200]}")
    text, total_pages = _extract_pdf_text(body, max_pages=max_pages)
    text = " ".join(text.split())
    if not text.strip():
        raise FetchError(f"pdf had no extractable text at {url[:200]}")
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS].rstrip()
    title = _pdf_title(body)
    return {
        "url": url,
        "title": title,
        "text": text,
        "pages": total_pages,
        "byte_size": len(body),
        "truncated": truncated,
    }


__all__ = ["MAX_PAGES_EXTRACTED", "MAX_PDF_BYTES", "MAX_TEXT_CHARS", "fetch_pdf", "looks_like_pdf_url"]
