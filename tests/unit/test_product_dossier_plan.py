"""Plan-stage tests for the product dossier (PD-fix, 2026-09-08): ``eoa.dossier.plan``'s web-source
hygiene (item 2 -- dedupe by normalized URL, title capture, kind/reliability classification,
irrelevant-page drop), per-topic progress reporting (item 5), and the per-topic time-cap wiring
(item 6) -- ``investigate()`` itself is monkeypatched throughout (no network/LLM).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_plan.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.dossier import plan as dossier_plan
from eoa.dossier.corpus import CorpusResult
from eoa.llm.schemas.analysis import InvestigationOut
from eoa.search.deep_search import Investigation


def _corpus(**kwargs: Any) -> CorpusResult:
    defaults: dict[str, Any] = {
        "product_key": "elbit-systems-spectro-xr",
        "product_name": "SPECTRO XR",
        "vendor": "Elbit Systems",
        "aliases": ["Spectro"],
        "terms": ["SPECTRO XR", "Spectro"],
        "registry": [],
    }
    defaults.update(kwargs)
    return CorpusResult(**defaults)


def _inv(*, read_sources: list[dict[str, Any]], read_summaries: list[dict[str, Any]]) -> Investigation:
    inv = Investigation(job_id=None, item_id=None, question="q")
    inv.read_sources = read_sources
    inv.read_summaries = read_summaries
    inv.result = InvestigationOut(outcome="found", answer_he="תשובה.", confidence=0.7, sources=[])
    return inv


# --------------------------------------------------------------------------
# classify_web_source / normalize_url
# --------------------------------------------------------------------------


def test_classify_web_source_vendor_domain_is_primary() -> None:
    assert dossier_plan.classify_web_source("https://www.elbitsystems.com/press/x", "Elbit Systems") == (
        "vendor_official",
        "primary",
    )


def test_classify_web_source_reference_domain() -> None:
    assert dossier_plan.classify_web_source("https://en.wikipedia.org/wiki/x", None) == ("reference", "low")


def test_classify_web_source_forum_domain() -> None:
    assert dossier_plan.classify_web_source("https://www.wetransfer.com/downloads/x", None) == ("forum", "low")


def test_classify_web_source_trade_press_domain() -> None:
    assert dossier_plan.classify_web_source("https://www.defensenews.com/x", None) == (
        "trade_press",
        "secondary",
    )


def test_classify_web_source_default_press() -> None:
    assert dossier_plan.classify_web_source("https://www.somenewsblog.com/x", "Elbit Systems") == (
        "press",
        "secondary",
    )


def test_normalize_url_ignores_scheme_www_trailing_slash_and_query() -> None:
    a = dossier_plan.normalize_url("http://WWW.Example.com/a/b/")
    b = dossier_plan.normalize_url("https://example.com/a/b?utm_source=x#frag")
    assert a == b == "https://example.com/a/b"


# --------------------------------------------------------------------------
# run_plan: dedupe by normalized URL
# --------------------------------------------------------------------------


def test_run_plan_dedupes_same_url_across_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls["n"] += 1
        # Both topics "read" the exact same page (different URL spelling -- with/without trailing
        # slash/query) -- must still collapse to ONE registry row.
        url = "https://example.com/a" if calls["n"] == 1 else "https://example.com/a/?ref=x"
        return _inv(
            read_sources=[{"url": url, "title": "Page A"}],
            read_summaries=[{"url": url, "title": "Page A", "summary": "מאמר על SPECTRO XR."}],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    result = dossier_plan.run_plan(corpus, max_topics=2)
    web_rows = [r for r in corpus.registry if r["kind"] == "web"]
    assert len(web_rows) == 1
    # Both topic findings must still reference the (single) existing source row.
    assert len(result.findings[0].source_ns) == 1
    assert len(result.findings[1].source_ns) == 1
    assert result.findings[0].source_ns[0]["n"] == result.findings[1].source_ns[0]["n"]


def test_run_plan_drops_irrelevant_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page whose read summary never mentions the product/vendor/alias at all (e.g. the live
    French WeTransfer forum thread) is dropped -- never appended to the registry, never citable."""

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(
            read_sources=[{"url": "https://wetransfer.com/downloads/xyz", "title": "Fichier partagé"}],
            read_summaries=[
                {
                    "url": "https://wetransfer.com/downloads/xyz",
                    "title": "Fichier partagé",
                    "summary": "Un fichier a été partagé avec vous.",
                }
            ],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert [r for r in corpus.registry if r["kind"] == "web"] == []


def test_run_plan_classifies_and_titles_web_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(
            read_sources=[{"url": "https://www.elbitsystems.com/press/spectro", "title": ""}],
            read_summaries=[
                {
                    "url": "https://www.elbitsystems.com/press/spectro",
                    "title": "Elbit unveils SPECTRO XR",
                    "summary": "אלביט מציגה את SPECTRO XR.",
                }
            ],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    web_rows = [r for r in corpus.registry if r["kind"] == "web"]
    assert len(web_rows) == 1
    row = web_rows[0]
    # PD-fix item 2: title captured from read_summaries when read_sources' own title is blank.
    assert row["title"] == "Elbit unveils SPECTRO XR"
    assert row["source_kind"] == "vendor_official"
    assert row["reliability"] == "primary"
    assert row["accessed_at"]


# --------------------------------------------------------------------------
# progress callback (item 5)
# --------------------------------------------------------------------------


def test_run_plan_reports_progress_pending_then_done(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    snapshots: list[list[dict[str, Any]]] = []
    dossier_plan.run_plan(corpus, max_topics=2, on_progress=lambda p: snapshots.append(p))

    # Called once up front (all pending) and once per topic status change (running, then done).
    assert snapshots[0][0]["status"] == "pending"
    assert snapshots[0][1]["status"] == "pending"
    final = snapshots[-1]
    assert [e["status"] for e in final] == ["done", "done"]
    assert all(e["seconds"] is not None for e in final)


def test_run_plan_progress_marks_failed_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_investigate(question: str, **kwargs: Any) -> Investigation:
        raise RuntimeError("boom")

    monkeypatch.setattr(dossier_plan, "investigate", failing_investigate)
    corpus = _corpus()
    snapshots: list[list[dict[str, Any]]] = []
    dossier_plan.run_plan(corpus, max_topics=1, on_progress=lambda p: snapshots.append(p))
    assert snapshots[-1][0]["status"] == "failed"


def test_run_plan_progress_callback_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    def bad_callback(_progress: list[dict[str, Any]]) -> None:
        raise ValueError("callback exploded")

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    # Must not raise -- a progress-reporting failure never breaks the dossier build.
    result = dossier_plan.run_plan(corpus, max_topics=1, on_progress=bad_callback)
    assert len(result.findings) == 1


# --------------------------------------------------------------------------
# item 6: per-topic time cap forwarded to investigate()
# --------------------------------------------------------------------------


def test_run_plan_forwards_topic_time_cap_as_deadline_s(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.update(kwargs)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert captured["deadline_s"] == dossier_plan.settings().dossier.topic_time_cap_s
