"""Unit tests for eoa.investigations.links (R10-links): provenance between a deep-search
investigation, the item that triggered it, its rerun/expansion lineage, and the reports whose
"חקירות עומק" section cites it.

Every DB access goes through this module's own `_fetchone`/`_fetchall` (or, for the two
`_job_row`/`_children` helpers used by the lineage walk) -- monkeypatched here with small
in-memory fakes rather than a real Postgres connection, matching the existing project convention
(see e.g. tests/unit/test_investigate_item_idempotent.py monkeypatching eoa.api.services._fetchone).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_investigation_links.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.investigations import links

UTC = dt.UTC


def _job_row(
    job_id: int,
    *,
    payload: dict | None = None,
    result: dict | None = None,
    state: str = "done",
    finished_at: dt.datetime | None = None,
) -> dict:
    return {
        "job_id": job_id,
        "payload": payload or {},
        "result": result or {},
        "state": state,
        "started_at": None,
        "finished_at": finished_at,
        "error": None,
    }


# --------------------------------------------------------------------------
# investigation_provenance
# --------------------------------------------------------------------------


def test_provenance_none_for_unknown_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_job_row", lambda job_id: None)
    assert links.investigation_provenance(999) is None


def test_provenance_trigger_item_none_for_freestanding_question(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job_row(10, payload={"question": "מה המצב בשוק?"})
    monkeypatch.setattr(links, "_job_row", lambda job_id: job if job_id == 10 else None)
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: pytest.fail("no item_id -> no item lookup"))
    monkeypatch.setattr(links, "investigation_lineage", lambda job_id: [])
    monkeypatch.setattr(links, "_reports_covering", lambda finished_at, item_id: [])

    out = links.investigation_provenance(10)
    assert out["trigger_item"] is None
    assert out["job"]["job_id"] == 10
    assert out["job"]["question"] == "מה המצב בשוק?"


def test_provenance_includes_trigger_item_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    job = _job_row(11, payload={"question": "q", "item_id": 42})
    monkeypatch.setattr(links, "_job_row", lambda job_id: job)

    def fake_fetchone(query, params=None):
        assert "FROM items i LEFT JOIN sources" in query
        assert params == (42,)
        return {
            "id": 42,
            "title": "כותרת",
            "url": "https://x",
            "source_name": "מקור",
            "published_at": dt.datetime(2026, 9, 1, tzinfo=UTC),
        }

    monkeypatch.setattr(links, "_fetchone", fake_fetchone)
    monkeypatch.setattr(links, "investigation_lineage", lambda job_id: [])
    monkeypatch.setattr(links, "_reports_covering", lambda finished_at, item_id: [])

    out = links.investigation_provenance(11)
    assert out["trigger_item"] == {
        "id": 42,
        "title": "כותרת",
        "url": "https://x",
        "source_name": "מקור",
        "published_at": dt.datetime(2026, 9, 1, tzinfo=UTC),
    }


def test_provenance_passes_finished_at_and_item_id_to_reports_covering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finished = dt.datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    job = _job_row(12, payload={"question": "q", "item_id": 7}, finished_at=finished)
    monkeypatch.setattr(links, "_job_row", lambda job_id: job)
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: None)
    monkeypatch.setattr(links, "investigation_lineage", lambda job_id: [])

    seen = {}

    def fake_reports_covering(f_at, item_id):
        seen["finished_at"] = f_at
        seen["item_id"] = item_id
        return [{"id": 1}]

    monkeypatch.setattr(links, "_reports_covering", fake_reports_covering)

    out = links.investigation_provenance(12)
    assert seen == {"finished_at": finished, "item_id": 7}
    assert out["reports"] == [{"id": 1}]


# --------------------------------------------------------------------------
# investigation_lineage
# --------------------------------------------------------------------------


def test_lineage_empty_for_unknown_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_job_row", lambda job_id: None)
    assert links.investigation_lineage(999) == []


def test_lineage_single_job_is_original(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = dt.datetime(2026, 9, 1, tzinfo=UTC)
    root = _job_row(
        1, payload={"question": "q"}, result={"outcome": "found", "confidence": 0.9}, finished_at=finished
    )
    monkeypatch.setattr(links, "_job_row", lambda job_id: root if job_id == 1 else None)
    monkeypatch.setattr(links, "_children", lambda job_id: [])

    out = links.investigation_lineage(1)
    assert out == [
        {"job_id": 1, "outcome": "found", "confidence": 0.9, "finished_at": finished, "kind": "original"}
    ]


def test_lineage_walks_ancestor_via_rerun_of(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    t1 = dt.datetime(2026, 9, 2, tzinfo=UTC)
    origin = _job_row(1, payload={"question": "q"}, finished_at=t0)
    rerun = _job_row(2, payload={"question": "q", "rerun_of_job_id": 1}, finished_at=t1)

    rows = {1: origin, 2: rerun}
    monkeypatch.setattr(links, "_job_row", lambda job_id: rows.get(job_id))
    monkeypatch.setattr(links, "_children", lambda job_id: [])

    out = links.investigation_lineage(2)
    kinds = {e["job_id"]: e["kind"] for e in out}
    assert kinds == {1: "original", 2: "rerun"}
    # oldest-first
    assert [e["job_id"] for e in out] == [1, 2]


def test_lineage_walks_descendant_children(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = dt.datetime(2026, 9, 1, tzinfo=UTC)
    t1 = dt.datetime(2026, 9, 2, tzinfo=UTC)
    root = _job_row(1, payload={"question": "q"}, finished_at=t0)
    child = _job_row(2, payload={"question": "q", "expanded_from_job_id": 1}, finished_at=t1)

    rows = {1: root, 2: child}
    monkeypatch.setattr(links, "_job_row", lambda job_id: rows.get(job_id))
    monkeypatch.setattr(links, "_children", lambda job_id: [child] if job_id == 1 else [])

    out = links.investigation_lineage(1)
    kinds = {e["job_id"]: e["kind"] for e in out}
    assert kinds == {1: "original", 2: "expansion"}


def test_lineage_expansion_kind_from_expanded_from_job_id(monkeypatch: pytest.MonkeyPatch) -> None:
    origin = _job_row(5, payload={"question": "q"})
    expanded = _job_row(6, payload={"question": "q2", "expanded_from_job_id": 5})
    rows = {5: origin, 6: expanded}
    monkeypatch.setattr(links, "_job_row", lambda job_id: rows.get(job_id))
    monkeypatch.setattr(links, "_children", lambda job_id: [])

    out = links.investigation_lineage(6)
    entry = next(e for e in out if e["job_id"] == 6)
    assert entry["kind"] == "expansion"


def test_lineage_does_not_infinite_loop_on_self_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    # Malformed data: a job whose own rerun_of_job_id points at itself must not hang the walk.
    weird = _job_row(3, payload={"question": "q", "rerun_of_job_id": 3})
    monkeypatch.setattr(links, "_job_row", lambda job_id: weird if job_id == 3 else None)
    monkeypatch.setattr(links, "_children", lambda job_id: [])

    out = links.investigation_lineage(3)
    assert [e["job_id"] for e in out] == [3]


# --------------------------------------------------------------------------
# _reports_covering
# --------------------------------------------------------------------------


def _report_row(
    report_id: int,
    kind: str,
    period_start: dt.date,
    period_end: dt.date,
    items_included: list[int] | None = None,
) -> dict:
    return {
        "id": report_id,
        "kind": kind,
        "period_start": period_start,
        "period_end": period_end,
        "territory": None,
        "items_included": items_included or [],
        "path_docx": None,
        "path_md": None,
        "path_html": f"reports/{report_id}.html",
        "created_at": dt.datetime(2026, 9, 5, tzinfo=UTC),
        "qa_report": {},
    }


def test_reports_covering_none_when_finished_at_missing() -> None:
    assert links._reports_covering(None, 5) == []


def test_reports_covering_matches_window_and_item(monkeypatch: pytest.MonkeyPatch) -> None:
    d = dt.date(2026, 9, 3)
    row = _report_row(100, "daily", d, d, items_included=[7, 8])
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [row])

    finished_at = dt.datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    out = links._reports_covering(finished_at, 7)
    assert len(out) == 1
    assert out[0]["id"] == 100
    assert out[0]["path_html"] == "reports/100.html"


def test_reports_covering_excludes_when_item_not_included(monkeypatch: pytest.MonkeyPatch) -> None:
    d = dt.date(2026, 9, 3)
    row = _report_row(100, "daily", d, d, items_included=[8, 9])
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [row])

    finished_at = dt.datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    assert links._reports_covering(finished_at, 7) == []


def test_reports_covering_excludes_out_of_window(monkeypatch: pytest.MonkeyPatch) -> None:
    d = dt.date(2026, 9, 3)
    row = _report_row(100, "daily", d, d, items_included=[7])
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [row])

    finished_at = dt.datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    assert links._reports_covering(finished_at, 7) == []


def test_reports_covering_freestanding_question_ignores_item_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    d = dt.date(2026, 9, 3)
    row = _report_row(100, "weekly", d, d, items_included=[])
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [row])

    finished_at = dt.datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    out = links._reports_covering(finished_at, None)
    assert len(out) == 1


# --------------------------------------------------------------------------
# item_investigations / reports_for_item
# --------------------------------------------------------------------------


def test_item_investigations_none_for_missing_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: None)
    assert links.item_investigations(404) is None


def test_item_investigations_returns_outcome_confidence_and_lineage_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: {"id": 7})
    job = {
        "job_id": 21,
        "payload": {"question": "q", "item_id": 7, "rerun_of_job_id": 20},
        "result": {"outcome": "found", "confidence": 0.8},
        "state": "done",
        "started_at": None,
        "finished_at": None,
        "error": None,
    }
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [job])

    out = links.item_investigations(7)
    assert out == [
        {
            "job_id": 21,
            "question": "q",
            "state": "done",
            "error": None,
            "outcome": "found",
            "confidence": 0.8,
            "started_at": None,
            "finished_at": None,
            "rerun_of_job_id": 20,
            "expanded_from_job_id": None,
        }
    ]


def test_reports_for_item_none_for_missing_item(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: None)
    assert links.reports_for_item(404) is None


def test_reports_for_item_dedupes_across_multiple_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: {"id": 7})
    monkeypatch.setattr(
        links,
        "_fetchall",
        lambda *_a, **_kw: [
            {"finished_at": dt.datetime(2026, 9, 1, tzinfo=UTC)},
            {"finished_at": dt.datetime(2026, 9, 2, tzinfo=UTC)},
        ],
    )
    same_report = {
        "id": 100,
        "kind": "daily",
        "title_he": "x",
        "period_end": None,
        "territory": None,
        "path_html": None,
    }
    monkeypatch.setattr(links, "_reports_covering", lambda finished_at, item_id: [same_report])

    out = links.reports_for_item(7)
    assert out == [same_report]  # not duplicated even though two jobs both matched it


def test_reports_for_item_empty_when_no_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: {"id": 7})
    monkeypatch.setattr(links, "_fetchall", lambda *_a, **_kw: [])
    assert links.reports_for_item(7) == []


# --------------------------------------------------------------------------
# investigations_for_report / reports_for_investigation
# --------------------------------------------------------------------------


def test_investigations_for_report_none_for_missing_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_fetchone", lambda *_a, **_kw: None)
    assert links.investigations_for_report(999) is None


def test_investigations_for_report_empty_for_non_rendering_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        links,
        "_fetchone",
        lambda *_a, **_kw: {
            "id": 1,
            "kind": "bd_territory",
            "period_start": None,
            "period_end": None,
            "items_included": [],
        },
    )
    assert links.investigations_for_report(1) == []


def test_investigations_for_report_delegates_to_collect_deep_search(monkeypatch: pytest.MonkeyPatch) -> None:
    import eoa.report.daily as daily_mod

    d = dt.date(2026, 9, 3)
    monkeypatch.setattr(
        links,
        "_fetchone",
        lambda *_a, **_kw: {
            "id": 5,
            "kind": "daily",
            "period_start": d,
            "period_end": d,
            "items_included": [1, 2],
        },
    )

    calls = {}

    def fake_collect(period_start, period_end):
        calls["collect_args"] = (period_start, period_end)
        return [{"job_id": 1, "trigger_item_id": 1}, {"job_id": 2, "trigger_item_id": 99}]

    def fake_filter(entries, items):
        calls["filter_items"] = items
        included_ids = {it["id"] for it in items}
        return [e for e in entries if e.get("trigger_item_id") in included_ids]

    monkeypatch.setattr(daily_mod, "collect_deep_search", fake_collect)
    monkeypatch.setattr(daily_mod, "_filter_deep_search_to_items_included", fake_filter)

    out = links.investigations_for_report(5)
    assert calls["collect_args"] == (d, d)
    assert calls["filter_items"] == [{"id": 1}, {"id": 2}]
    assert out == [{"job_id": 1, "trigger_item_id": 1}]


def test_reports_for_investigation_none_for_unknown_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(links, "_job_row", lambda job_id: None)
    assert links.reports_for_investigation(999) is None


def test_reports_for_investigation_uses_job_finished_at_and_item_id(monkeypatch: pytest.MonkeyPatch) -> None:
    finished = dt.datetime(2026, 9, 1, tzinfo=UTC)
    job = _job_row(30, payload={"question": "q", "item_id": 3}, finished_at=finished)
    monkeypatch.setattr(links, "_job_row", lambda job_id: job)

    seen = {}

    def fake_reports_covering(f_at, item_id):
        seen["args"] = (f_at, item_id)
        return [{"id": 55}]

    monkeypatch.setattr(links, "_reports_covering", fake_reports_covering)
    out = links.reports_for_investigation(30)
    assert seen["args"] == (finished, 3)
    assert out == [{"id": 55}]


# --------------------------------------------------------------------------
# _report_period_window / _lineage_kind (small pure helpers)
# --------------------------------------------------------------------------


def test_report_period_window_matches_jerusalem_day_bounds() -> None:
    d = dt.date(2026, 9, 3)
    start, end = links._report_period_window(d, d)
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start < end
    assert (end - start) < dt.timedelta(days=1, hours=1)


def test_lineage_kind_original_when_no_lineage_keys() -> None:
    assert links._lineage_kind({}) == "original"


def test_lineage_kind_rerun_takes_precedence_over_expansion() -> None:
    # Malformed/unexpected data with both keys set: rerun_of_job_id wins (checked first).
    assert links._lineage_kind({"rerun_of_job_id": 1, "expanded_from_job_id": 2}) == "rerun"
