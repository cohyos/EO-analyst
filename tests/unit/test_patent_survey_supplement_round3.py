"""Round-3 (surveys 45/46, 2026-09-06): a keyless-search outage must not empty a patent survey —
stored patents matching the topic are pulled in."""

from __future__ import annotations

from eoa.patents import survey as mod


def test_topic_keywords_drop_stopwords_and_short_tokens() -> None:
    assert mod._topic_keywords("Anduril Lattice counter-UAS EO/IR optical tracking patents") == [
        "anduril",
        "lattice",
        "counter-uas",
        "optical",
        "tracking",
    ]
    assert mod._topic_keywords("FPA עם פיקסל דיגיטלי (DROIC)") == ["fpa", "פיקסל", "דיגיטלי", "droic"]
    assert mod._topic_keywords("של עם") == []


def test_stored_ids_prefer_multi_keyword_hits(monkeypatch) -> None:
    rows = [{"id": i, "hits": 2} for i in range(1, 7)] + [{"id": 99, "hits": 1}]

    class _Cur:
        def execute(self, sql, params):
            assert "hits > 0" in sql and params["kw0"] == "%anduril%"

        def fetchall(self):
            return rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(mod, "connection", lambda: _Conn())
    assert mod._stored_patent_ids_for_topic("Anduril lattice") == [1, 2, 3, 4, 5, 6]


def test_stored_ids_fall_back_to_single_hits_when_few_strong(monkeypatch) -> None:
    rows = [{"id": 1, "hits": 2}, {"id": 2, "hits": 1}, {"id": 3, "hits": 1}]

    class _Cur:
        def execute(self, sql, params):
            return None

        def fetchall(self):
            return rows

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    monkeypatch.setattr(mod, "connection", lambda: _Conn())
    assert mod._stored_patent_ids_for_topic("droic readout") == [1, 2, 3]
