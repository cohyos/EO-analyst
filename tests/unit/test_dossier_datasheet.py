"""Tests for ``eoa.dossier.datasheet`` (PD-datasheet, 2026-09-09, LESSONS-1 item 1): query
building, candidate ranking, and the search->download->fallback orchestration in
:func:`hunt_datasheets` -- ``search``/``fetch_pdf``/``fetch_remote`` are all monkeypatched, no real
network calls.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_dossier_datasheet.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.dossier import datasheet
from eoa.search.provider import SearchHit, SearchResponse


def _hit(url: str, title: str = "") -> SearchHit:
    return SearchHit(url=url, title=title, snippet="", engine="test")


# --------------------------------------------------------------------------
# build_datasheet_queries
# --------------------------------------------------------------------------


def test_build_datasheet_queries_prefixes_vendor_site_first() -> None:
    queries = datasheet.build_datasheet_queries("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert queries[0][0].startswith("site:elbitsystems.com")


def test_build_datasheet_queries_no_vendor_domain_skips_site_restriction() -> None:
    queries = datasheet.build_datasheet_queries("SPECTRO XR", "Elbit Systems", vendor_domain=None)
    assert not any(q.startswith("site:elbitsystems.com") for q, _lang in queries)
    assert any("datasheet" in q for q, _lang in queries)


def test_build_datasheet_queries_includes_catalog_domains() -> None:
    queries = datasheet.build_datasheet_queries("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert any("site:instro.com" in q for q, _lang in queries)


def test_build_datasheet_queries_empty_product_name_yields_no_queries() -> None:
    assert datasheet.build_datasheet_queries("", "Elbit Systems", vendor_domain="elbitsystems.com") == []


def test_build_datasheet_queries_dedupes() -> None:
    queries = datasheet.build_datasheet_queries("SPECTRO XR", "Elbit Systems", vendor_domain=None)
    texts = [q for q, _lang in queries]
    assert len(texts) == len(set(t.casefold() for t in texts))


# --------------------------------------------------------------------------
# gather_candidates / ranking
# --------------------------------------------------------------------------


def test_gather_candidates_ranks_vendor_pdf_first(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(
            query,
            lang,
            hits=[
                _hit("https://news.example.com/story", "SPECTRO XR unveiled"),
                _hit("https://elbitsystems.com/files/spectro-xr-datasheet.pdf", "SPECTRO XR Datasheet"),
            ],
        )

    monkeypatch.setattr(datasheet, "search", fake_search)
    candidates = datasheet.gather_candidates("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert candidates[0]["url"].endswith(".pdf")
    assert "elbitsystems.com" in candidates[0]["url"]


def test_gather_candidates_dedupes_across_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(query, lang, hits=[_hit("https://elbitsystems.com/brochure.pdf", "Brochure")])

    monkeypatch.setattr(datasheet, "search", fake_search)
    candidates = datasheet.gather_candidates("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert len(candidates) == 1


def test_gather_candidates_swallows_search_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        raise RuntimeError("search backend down")

    monkeypatch.setattr(datasheet, "search", failing_search)
    assert datasheet.gather_candidates("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com") == []


def test_gather_candidates_skips_query_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return SearchResponse(query, lang, hits=[], error="rate limited")
        return SearchResponse(query, lang, hits=[_hit("https://elbitsystems.com/x.pdf", "X")])

    monkeypatch.setattr(datasheet, "search", fake_search)
    candidates = datasheet.gather_candidates("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert any(c["url"].endswith("x.pdf") for c in candidates)


# --------------------------------------------------------------------------
# hunt_datasheets
# --------------------------------------------------------------------------


def test_hunt_datasheets_downloads_top_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(query, lang, hits=[_hit("https://elbitsystems.com/brochure.pdf", "Brochure")])

    def fake_fetch_pdf(url: str, **kwargs: Any) -> dict[str, Any]:
        return {"url": url, "title": "SPECTRO XR Brochure", "text": "InSb 1280x1024 detector", "pages": 4}

    monkeypatch.setattr(datasheet, "search", fake_search)
    monkeypatch.setattr(datasheet, "fetch_pdf", fake_fetch_pdf)
    results = datasheet.hunt_datasheets("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert len(results) == 1
    assert results[0]["kind"] == "pdf"
    assert "InSb" in results[0]["text"]


def test_hunt_datasheets_skips_failed_pdf_and_tries_next(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(
            query,
            lang,
            hits=[
                _hit("https://elbitsystems.com/bad.pdf", "Bad datasheet"),
                _hit("https://elbitsystems.com/good.pdf", "Good datasheet"),
            ],
        )

    def fake_fetch_pdf(url: str, **kwargs: Any) -> dict[str, Any]:
        if "bad" in url:
            from eoa.errors import FetchError

            raise FetchError("corrupt pdf")
        return {"url": url, "title": "Good", "text": "InSb 1280x1024", "pages": 2}

    monkeypatch.setattr(datasheet, "search", fake_search)
    monkeypatch.setattr(datasheet, "fetch_pdf", fake_fetch_pdf)
    results = datasheet.hunt_datasheets("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert len(results) == 1
    assert "good.pdf" in results[0]["url"]


def test_hunt_datasheets_falls_back_to_product_page_when_no_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(
            query,
            lang,
            hits=[
                _hit("https://elbitsystems.com/product/spectro-maritime/", "SPECTRO Maritime"),
                _hit("https://instro.com/spectro-xr", "Instro SPECTRO XR"),
            ],
        )

    def fake_fetch_remote(url: str) -> dict[str, Any]:
        return {"url": url, "title": "SPECTRO product page", "text": "InSb detector 1280x1024 sensor payload"}

    monkeypatch.setattr(datasheet, "search", fake_search)

    import eoa.fetch.remote as remote_module

    monkeypatch.setattr(remote_module, "fetch_remote", fake_fetch_remote)
    results = datasheet.hunt_datasheets("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com")
    assert len(results) >= 1
    assert results[0]["kind"] == "page"
    assert "InSb" in results[0]["text"]


def test_hunt_datasheets_returns_empty_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        return SearchResponse(query, lang, hits=[])

    monkeypatch.setattr(datasheet, "search", fake_search)
    assert datasheet.hunt_datasheets("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com") == []


def test_hunt_datasheets_empty_product_name_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, lang: str, **kwargs: Any) -> SearchResponse:
        raise AssertionError("should never be called for an empty product name")

    monkeypatch.setattr(datasheet, "search", fake_search)
    assert datasheet.hunt_datasheets("", "Elbit Systems", vendor_domain="elbitsystems.com") == []


def test_hunt_datasheets_swallows_gather_candidates_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_gather(*a: Any, **kw: Any) -> list[dict[str, str]]:
        raise RuntimeError("boom")

    monkeypatch.setattr(datasheet, "gather_candidates", failing_gather)
    assert datasheet.hunt_datasheets("SPECTRO XR", "Elbit Systems", vendor_domain="elbitsystems.com") == []
