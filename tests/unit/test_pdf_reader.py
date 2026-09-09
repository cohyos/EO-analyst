"""Tests for ``eoa.search.pdf_reader`` (PD-datasheet, 2026-09-09): the SSRF-guarded PDF download +
``pypdf`` text extraction the product dossier's datasheet hunt (``eoa.dossier.datasheet``) uses.
No real network calls -- ``httpx.Client.get`` is monkeypatched throughout via a small fake client;
PDF bytes are built in-memory with ``pypdf.PdfWriter``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_pdf_reader.py -q``
"""

from __future__ import annotations

import io

import httpx
import pytest

from eoa.errors import FetchError
from eoa.search import pdf_reader


def _make_pdf_bytes(*, pages_text: list[str], title: str | None = None) -> bytes:
    """A minimal, hand-built single/multi-page PDF (Helvetica base-14 font, no reportlab
    dependency needed -- not available in this venv) with a real, extractable text-showing content
    stream per page. pypdf's ``PdfWriter`` has no "just write this text" API without reportlab, so
    this builds the raw PDF byte structure directly -- standard, well-documented minimal-PDF shape,
    with real (not placeholder) ``xref`` offsets so a strict parse succeeds, not just pypdf's
    damaged-file recovery path."""
    objects: list[bytes] = []
    n_pages = len(pages_text) or 1

    def esc(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    kids = " ".join(f"{3 + i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1"))
    content_obj_start = 3 + n_pages
    font_obj = content_obj_start + n_pages
    for i in range(n_pages):
        content_ref = content_obj_start + i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {content_ref} 0 R >>"
            ).encode("latin-1")
        )
    for i in range(n_pages):
        text = esc(pages_text[i]) if i < len(pages_text) else ""
        stream = f"BT /F1 12 Tf 10 250 Td ({text}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    if title:
        title_obj = font_obj + 1
        objects.append(f"<< /Title ({esc(title)}) >>".encode("latin-1"))
    else:
        title_obj = None

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{idx} 0 obj\n".encode("latin-1"))
        out.write(body)
        out.write(b"\nendobj\n")
    xref_start = out.tell()
    total = len(objects) + 1
    out.write(f"xref\n0 {total}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    trailer = f"<< /Size {total} /Root 1 0 R"
    if title_obj is not None:
        trailer += f" /Info {title_obj} 0 R"
    trailer += " >>"
    out.write(f"trailer\n{trailer}\nstartxref\n{xref_start}\n%%EOF".encode("latin-1"))
    return out.getvalue()


class _FakeResponse:
    def __init__(self, *, status_code: int, content: bytes, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad status", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, url: str) -> _FakeResponse:
        if url not in self._responses:
            raise httpx.ConnectError(f"no fake response for {url}")
        return self._responses[url]


def _patch_client(monkeypatch: pytest.MonkeyPatch, responses: dict[str, _FakeResponse]) -> None:
    monkeypatch.setattr(pdf_reader.httpx, "Client", lambda **kw: _FakeClient(responses))
    monkeypatch.setattr(pdf_reader, "assert_public_http_url", lambda url: {"1.2.3.4"})


def test_looks_like_pdf_url_true_for_pdf_extension() -> None:
    assert pdf_reader.looks_like_pdf_url("https://example.com/brochure.pdf")
    assert pdf_reader.looks_like_pdf_url("https://example.com/dir/spec.PDF?x=1")


def test_looks_like_pdf_url_false_for_html_page() -> None:
    assert not pdf_reader.looks_like_pdf_url("https://example.com/product/spectro-xr")


def test_fetch_pdf_extracts_text(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _make_pdf_bytes(pages_text=["InSb detector 1280x1024 sensor spec"])
    url = "https://elbitsystems.com/brochure.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=body, headers={"content-type": "application/pdf"})},
    )
    result = pdf_reader.fetch_pdf(url)
    assert result["url"] == url
    assert "InSb" in result["text"] or "1280" in result["text"]
    assert result["pages"] == 1
    assert result["byte_size"] == len(body)


def test_fetch_pdf_multipage_reports_total_page_count(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _make_pdf_bytes(pages_text=["Page one InSb detector", "Page two 1280x1024 sensor"])
    url = "https://elbitsystems.com/multi.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=body, headers={"content-type": "application/pdf"})},
    )
    result = pdf_reader.fetch_pdf(url)
    assert result["pages"] == 2
    assert "InSb" in result["text"]
    assert "1280x1024" in result["text"]


def test_fetch_pdf_caps_pages_extracted(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _make_pdf_bytes(pages_text=["first page marker", "second page should not be read"])
    url = "https://example.com/two.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=body, headers={"content-type": "application/pdf"})},
    )
    result = pdf_reader.fetch_pdf(url, max_pages=1)
    assert result["pages"] == 2  # total page count is still reported accurately
    assert "first page marker" in result["text"]
    assert "second page should not be read" not in result["text"]


def test_fetch_pdf_rejects_non_pdf_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/notreally.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=b"<html>not a pdf</html>", headers={"content-type": "text/html"})},
    )
    with pytest.raises(FetchError, match="not a pdf"):
        pdf_reader.fetch_pdf(url)


def test_fetch_pdf_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/huge.pdf"
    _patch_client(
        monkeypatch,
        {
            url: _FakeResponse(
                status_code=200,
                content=b"%PDF-1.4 " + b"0" * 100,
                headers={"content-type": "application/pdf", "content-length": str(10**9)},
            )
        },
    )
    with pytest.raises(FetchError, match="too large"):
        pdf_reader.fetch_pdf(url, max_bytes=1000)


def test_fetch_pdf_rejects_unparsable_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/corrupt.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=b"%PDF-not-really-valid-bytes", headers={"content-type": "application/pdf"})},
    )
    with pytest.raises(FetchError):
        pdf_reader.fetch_pdf(url)


def test_fetch_pdf_rejects_pdf_with_no_extractable_text(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _make_pdf_bytes(pages_text=[])
    url = "https://example.com/blank.pdf"
    _patch_client(
        monkeypatch,
        {url: _FakeResponse(status_code=200, content=body, headers={"content-type": "application/pdf"})},
    )
    with pytest.raises(FetchError, match="no extractable text"):
        pdf_reader.fetch_pdf(url)


def test_fetch_pdf_validates_ssrf_before_download(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _refuse(url: str) -> set[str]:
        calls.append(url)
        raise FetchError("refusing non-public address")

    monkeypatch.setattr(pdf_reader, "assert_public_http_url", _refuse)
    with pytest.raises(FetchError, match="refusing"):
        pdf_reader.fetch_pdf("http://169.254.169.254/brochure.pdf")
    assert calls  # the guard was actually consulted before any download attempt
