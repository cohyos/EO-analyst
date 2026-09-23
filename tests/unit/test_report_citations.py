"""Tests for `eoa.api.services.report_citations` (U3, docs/REVIEW_2026-09-05.md).

U3: clicking a `[n]` citation chip in the embedded report / Morning executive summary should
navigate to `/items/:id` when it resolves to a real item, or open the source URL otherwise --
today it only shows a hover tooltip. `report_citations()` builds the `n -> {item_id, url, title}`
map the frontend needs: directly from `reports.items_included` for the base registry, and by
parsing the rendered report's "נספח מקורות" (sources appendix) for any extended citation that
`eoa.report.daily._extend_citation_registry` added only because a business event referenced an
item outside `items_included`.

F19 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): the appendix parser used to be a fixed-cell
regex that assumed exactly five `<td>`s (#, title, source, date, link). `docx_builder.render_html`
now emits SIX (an `אמינות`/reliability column between source and date), which silently broke every
extended-citation match. `_appendix_html` below always renders six cells -- the current real
shape -- and `report_citations` now parses actual HTML elements (`lxml.html`), so title is read
from the row's 2nd `<td>` and the link from its LAST `<td>` regardless of how many columns sit
between them. Extended-citation URL resolution is also now one batched `WHERE url = ANY(...)`
query instead of one `SELECT` per row (see `test_multiple_extended_citations_resolve_in_one_query`).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_citations.py -q``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eoa.api import services


def _appendix_html(rows: list[tuple[int, str, str, str]]) -> str:
    """`rows` = (n, title, url, source_label). Six `<td>`s per row -- #, title, source,
    אמינות (reliability), date, link -- matching `docx_builder.render_html`'s current shape."""
    body = "".join(
        f'<tr id="src-{n}"><td>{n}</td><td>{title}</td><td>{source}</td><td>גבוהה</td>'
        f'<td>1.1.2026</td><td><a href="{url}">קישור</a></td></tr>'
        for n, title, url, source in rows
    )
    return (
        "<!doctype html><html><body>"
        f'<h2 id="sec-x">נספח מקורות</h2><table><tbody>{body}</tbody></table>'
        "</body></html>"
    )


class TestReportCitations:
    def test_missing_report_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *_a, **_kw: None)
        assert services.report_citations(999) is None

    def test_base_items_resolved_from_items_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return {"items_included": [10, 20], "path_html": None}
            raise AssertionError(query)

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM items WHERE id = ANY" in query:
                assert params == {"ids": [10, 20]}
                return [
                    {"id": 10, "title": "Item A", "url": "https://a.example/"},
                    {"id": 20, "title": "Item B", "url": "https://b.example/"},
                ]
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.report_citations(1)
        assert result is not None
        assert result["citations"]["1"] == {"item_id": 10, "url": "https://a.example/", "title": "Item A"}
        assert result["citations"]["2"] == {"item_id": 20, "url": "https://b.example/", "title": "Item B"}

    def test_extended_citation_beyond_items_included_parsed_from_appendix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        html_path = tmp_path / "daily_2026-09-05.html"
        html_path.write_text(
            _appendix_html(
                [
                    (1, "Item A", "https://a.example/", "Source A"),
                    # n=2 is the extended, event-only entry -- not in items_included at all.
                    (2, "Rheinmetall XM30 award", "https://c.example/press", "Source C"),
                ]
            ),
            encoding="utf-8",
        )

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return {"items_included": [10], "path_html": str(html_path)}
            raise AssertionError(query)

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM items WHERE id = ANY" in query:
                return [{"id": 10, "title": "Item A", "url": "https://a.example/"}]
            if "FROM items WHERE url = ANY" in query:
                assert params == {"urls": ["https://c.example/press"]}
                return [{"id": 77, "url": "https://c.example/press"}]
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.report_citations(1)
        assert result is not None
        cit = result["citations"]
        assert cit["1"]["item_id"] == 10
        assert cit["2"] == {
            "item_id": 77,
            "url": "https://c.example/press",
            "title": "Rheinmetall XM30 award",
        }

    def test_extended_citation_with_no_matching_item_falls_back_to_url_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        html_path = tmp_path / "daily.html"
        html_path.write_text(
            _appendix_html([(1, "Stale press release", "https://gone.example/", "Old Source")]),
            encoding="utf-8",
        )

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return {"items_included": [], "path_html": str(html_path)}
            raise AssertionError(query)

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM items WHERE url = ANY" in query:
                return []  # no item row matches this URL any more
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.report_citations(1)
        assert result is not None
        assert result["citations"]["1"] == {
            "item_id": None,
            "url": "https://gone.example/",
            "title": "Stale press release",
        }

    def test_multiple_extended_citations_resolve_in_one_query(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Efficiency (SOL-AUDIT-2026-09-24.md #4): N extended citations resolve their item_ids
        through exactly one batched `WHERE url = ANY(...)` call, not one query per row."""
        html_path = tmp_path / "daily.html"
        html_path.write_text(
            _appendix_html(
                [
                    (1, "First", "https://a.example/", "Source A"),
                    (2, "Second", "https://b.example/", "Source B"),
                    (3, "Third", "https://c.example/", "Source C"),
                ]
            ),
            encoding="utf-8",
        )

        url_lookup_calls = 0

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return {"items_included": [], "path_html": str(html_path)}
            raise AssertionError(f"report_citations must not use _fetchone for url lookups: {query}")

        def fetchall(query: str, params: Any = None) -> Any:
            nonlocal url_lookup_calls
            if "FROM items WHERE url = ANY" in query:
                url_lookup_calls += 1
                assert set(params["urls"]) == {"https://a.example/", "https://b.example/", "https://c.example/"}
                return [
                    {"id": 1, "url": "https://a.example/"},
                    {"id": 2, "url": "https://b.example/"},
                    {"id": 3, "url": "https://c.example/"},
                ]
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.report_citations(1)
        assert result is not None
        assert url_lookup_calls == 1
        assert result["citations"]["1"]["item_id"] == 1
        assert result["citations"]["2"]["item_id"] == 2
        assert result["citations"]["3"]["item_id"] == 3

    def test_appendix_row_with_reliability_column_parses_via_elements(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """F19 repro: the old 5-cell regex never matched a real (6-cell) appendix row at all. This
        asserts the current render shape resolves title/url correctly regardless of cell count."""
        html_path = tmp_path / "daily.html"
        html_path.write_text(
            '<!doctype html><html><body><table><tbody>'
            '<tr id="src-1"><td>1</td><td>Some Title</td><td>Some Source</td>'
            '<td>בינונית</td><td>2.2.2026</td><td><a href="https://x.example/a">קישור</a></td></tr>'
            "</tbody></table></body></html>",
            encoding="utf-8",
        )

        def fetchone(query: str, params: Any = None) -> Any:
            if "FROM reports WHERE id" in query:
                return {"items_included": [], "path_html": str(html_path)}
            raise AssertionError(query)

        def fetchall(query: str, params: Any = None) -> Any:
            if "FROM items WHERE url = ANY" in query:
                return [{"id": 5, "url": "https://x.example/a"}]
            raise AssertionError(query)

        monkeypatch.setattr(services, "_fetchone", fetchone)
        monkeypatch.setattr(services, "_fetchall", fetchall)

        result = services.report_citations(1)
        assert result is not None
        assert result["citations"]["1"] == {
            "item_id": 5,
            "url": "https://x.example/a",
            "title": "Some Title",
        }
