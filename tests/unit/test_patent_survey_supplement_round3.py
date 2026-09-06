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


class _Cur:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, "", {}

    def execute(self, sql, params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


def test_generic_only_topic_requires_most_keywords(monkeypatch) -> None:
    cur = _Cur([{"id": 1, "d_hits": 0, "g_hits": 3, "publication_date": None}])
    monkeypatch.setattr(mod, "connection", lambda: _Conn(cur))
    monkeypatch.setattr(mod, "_keyword_document_frequency", lambda kws: {k: 0.4 for k in kws})
    assert mod._stored_patent_ids_for_topic("optical tracking system") == [1]
    assert "g_hits >=" in cur.sql and "d_hits >= 1" not in cur.sql


def test_distinctive_keyword_gates_admission(monkeypatch) -> None:
    cur = _Cur([{"id": 62, "d_hits": 2, "g_hits": 1, "publication_date": None}])
    monkeypatch.setattr(mod, "connection", lambda: _Conn(cur))
    monkeypatch.setattr(
        mod,
        "_keyword_document_frequency",
        lambda kws: {"anduril": 0.02, "lattice": 0.02, "optical": 0.36, "tracking": 0.3},
    )
    assert mod._stored_patent_ids_for_topic("Anduril Lattice optical tracking") == [62]
    assert "d_hits >= 1" in cur.sql
    # generic keywords are ranked, never gated
    assert cur.params["d0"] == "%anduril%" and cur.params["g0"] == "%optical%"


def test_old_patents_dropped_when_newer_exist(monkeypatch) -> None:
    import datetime as dt

    cur = _Cur(
        [
            {"id": 1, "d_hits": 1, "g_hits": 0, "publication_date": dt.date(1964, 1, 1)},
            {"id": 2, "d_hits": 1, "g_hits": 0, "publication_date": dt.date(2021, 1, 1)},
        ]
    )
    monkeypatch.setattr(mod, "connection", lambda: _Conn(cur))
    monkeypatch.setattr(mod, "_keyword_document_frequency", lambda kws: {k: 0.05 for k in kws})
    assert mod._stored_patent_ids_for_topic("droic readout") == [2]
