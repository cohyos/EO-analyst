"""Plan-stage tests for the product dossier (PD-fix, 2026-09-08): ``eoa.dossier.plan``'s web-source
hygiene (item 2 -- dedupe by normalized URL, title capture, kind/reliability classification,
irrelevant-page drop), per-topic progress reporting (item 5), and the per-topic time-cap wiring
(item 6) -- ``investigate()`` itself is monkeypatched throughout (no network/LLM).

PD-datasheet (2026-09-09, LESSONS-1): ``run_plan`` now also runs a datasheet hunt
(``eoa.dossier.datasheet.hunt_datasheets``, real search + PDF download) unconditionally before the
topic loop, same "network-touching stage defaults ON, the test FILE stubs it" convention
``test_product_dossier_corpus.py``'s own ``_no_live_patents_ops`` autouse fixture already
established for ``collect_patents_ops`` -- see :func:`_no_live_datasheet_hunt` below. The dedicated
datasheet/programs/gaps tests further down override it per-test with recorded-shape fixtures.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_plan.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.dossier import plan as dossier_plan
from eoa.dossier.corpus import CorpusResult
from eoa.llm.schemas.analysis import InvestigationOut
from eoa.search.deep_search import Investigation
from eoa.search.provider import SearchHit


@pytest.fixture(autouse=True)
def _no_live_datasheet_hunt(monkeypatch: pytest.MonkeyPatch) -> None:
    """PD-datasheet (2026-09-09): ``run_plan`` calls ``hunt_datasheets`` (real search + PDF
    download) unconditionally, before the topic loop -- stubbed to a no-op here for every test in
    this file that doesn't explicitly exercise that path, preserving this file's own "no real
    network calls" convention. The dedicated datasheet-wiring tests below override this per-test."""
    monkeypatch.setattr(dossier_plan, "hunt_datasheets", lambda *a, **kw: [])


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


def test_run_plan_reuses_item_derived_registry_row_for_same_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """PD-fix-2 item 4: a topic's own web search can re-read a URL the corpus already knows as a
    DB item (e.g. a press item already in ``items`` that a "deals" investigation finds again) --
    that source is item-derived and already carries the item's real title/url from
    ``eoa.dossier.corpus``. The dedup seed must reuse that existing row, not mint a second, weaker
    "web" entry (whose title would fall back to a bare hostname) for the identical page."""
    item_url = "https://www.israeldefense.co.il/en/node/70529"
    corpus = _corpus(
        registry=[
            {
                "n": 1,
                "kind": "item",
                "id": 93,
                "title": "Elbit Systems Lands $270M ISR Deal",
                "url": item_url,
                "source_name": "Israel Defense (English)",
            }
        ]
    )

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(
            read_sources=[{"url": item_url, "title": ""}],
            read_summaries=[{"url": item_url, "title": "", "summary": "עסקה עבור SPECTRO XR."}],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    result = dossier_plan.run_plan(corpus, max_topics=1)

    web_rows = [r for r in corpus.registry if r["kind"] == "web"]
    assert web_rows == []  # no second, weaker entry minted for the same URL
    assert len(corpus.registry) == 1  # the original item row is untouched, not duplicated
    assert len(result.findings[0].source_ns) == 1
    reused = result.findings[0].source_ns[0]
    assert reused["n"] == 1
    assert reused["kind"] == "item"
    assert reused["title"] == "Elbit Systems Lands $270M ISR Deal"
    assert reused["url"] == item_url


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


# --------------------------------------------------------------------------
# llm_leg plumbing (PD-cloud-tools, 2026-09-09)
# --------------------------------------------------------------------------


def test_run_plan_forwards_llm_leg_to_every_topic_investigate_call(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[Any] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(kwargs.get("llm_leg"))
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=3, llm_leg="codex:gpt-6-astra")
    assert captured == ["codex:gpt-6-astra"] * 3


def test_run_plan_llm_leg_none_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.update(kwargs)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert captured["llm_leg"] is None


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09): item A.3 -- negation-aware _mentions_product
# --------------------------------------------------------------------------


def test_mentions_product_true_for_plain_mention() -> None:
    assert dossier_plan._mentions_product("מאמר על SPECTRO XR ומפרטו.", "SPECTRO XR", ["Spectro"])


def test_mentions_product_false_for_explicit_not_relevant_marker() -> None:
    """The exact live-bug pattern (item A.3): a summariser that explains WHY a page is off-topic
    ends up name-dropping the very product it is disclaiming -- an explicit negation/not-relevant
    marker anywhere in the summary must win over any incidental substring match."""
    text = "הדף עוסק בעסקת Watchkeeper X ברומניה; אינו קשור ל-SPECTRO XR."
    assert not dossier_plan._mentions_product(text, "SPECTRO XR", ["Spectro"])


def test_mentions_product_false_for_literal_not_relevant() -> None:
    assert not dossier_plan._mentions_product("לא רלוונטי", "SPECTRO XR", ["Spectro"])


def test_mentions_product_true_when_summary_empty() -> None:
    # No summary at all to judge by -- err on the side of keeping it (summariser gap, not evidence
    # of irrelevance).
    assert dossier_plan._mentions_product("", "SPECTRO XR", ["Spectro"])


def test_run_plan_drops_page_whose_summary_only_mentions_product_via_negation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(
            read_sources=[{"url": "https://www.overtdefense.com/x", "title": "Watchkeeper X"}],
            read_summaries=[
                {
                    "url": "https://www.overtdefense.com/x",
                    "title": "Watchkeeper X",
                    "summary": "עסקת Watchkeeper X ברומניה; אינו קשור ל-SPECTRO XR.",
                }
            ],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert [r for r in corpus.registry if r["kind"] == "web"] == []


# --------------------------------------------------------------------------
# item A.1/A.2: vendor-domain resolution + MUST-READ vendor pages
# --------------------------------------------------------------------------


def test_resolve_vendor_domain_prefers_known_vendor_official_url() -> None:
    registry = [
        {"n": 1, "kind": "web", "source_kind": "vendor_official", "url": "https://www.elbitsystems.com/x"}
    ]
    assert dossier_plan.resolve_vendor_domain("Elbit Systems", registry) == "elbitsystems.com"


def test_resolve_vendor_domain_falls_back_to_hint_map() -> None:
    assert dossier_plan.resolve_vendor_domain("Elbit Systems", []) == "elbitsystems.com"


def test_resolve_vendor_domain_none_for_unknown_vendor() -> None:
    assert dossier_plan.resolve_vendor_domain("Some Unknown Vendor Ltd", []) is None


def test_gather_must_read_urls_from_previous_dossier_vendor_official_source() -> None:
    corpus = _corpus(
        previous={
            "sources": [
                {"n": 1, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/a"},
                {"n": 2, "kind": "web", "source_kind": "press", "url": "https://defensenews.com/b"},
            ]
        }
    )
    urls = dossier_plan.gather_must_read_urls(corpus)
    assert urls == ["https://elbitsystems.com/a"]


def test_gather_must_read_urls_from_registry_vendor_domain_row() -> None:
    corpus = _corpus(
        registry=[
            {"n": 1, "kind": "item", "url": "https://www.elbitsystems.com/press/spectro"},
            {"n": 2, "kind": "item", "url": "https://www.israeldefense.co.il/x"},
        ]
    )
    urls = dossier_plan.gather_must_read_urls(corpus)
    assert urls == ["https://www.elbitsystems.com/press/spectro"]


def test_gather_must_read_urls_dedupes_and_caps() -> None:
    many = [{"n": i, "kind": "item", "url": f"https://elbitsystems.com/p{i}"} for i in range(1, 10)]
    corpus = _corpus(registry=many)
    urls = dossier_plan.gather_must_read_urls(corpus)
    assert len(urls) == dossier_plan._MUST_READ_URL_CAP


def test_gather_must_read_urls_empty_when_no_vendor_domain_known() -> None:
    corpus = _corpus(vendor="Some Unknown Vendor Ltd", registry=[])
    assert dossier_plan.gather_must_read_urls(corpus) == []


# --------------------------------------------------------------------------
# PD-fix-5 (2026-09-09, item 2): a previous dossier can carry more `vendor_official` URLs than
# `_MUST_READ_URL_CAP` allows re-reading -- the live SPECTRO XR previous run (product_dossiers id=8)
# had 8, and n-order put six deal-announcement press releases ahead of the two pages that actually
# carry the full spec table, so the cap silently dropped exactly those two. A URL already known to
# have carried a real spec/performance/overflow VALUE last run must be prioritized ahead of a
# same-domain URL that was never actually the source of a fact.
# --------------------------------------------------------------------------


def test_previous_vendor_official_urls_prioritizes_spec_bearing_page_over_press_release() -> None:
    previous = {
        "sources": [
            {"n": 3, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/news/deal"},
            {"n": 24, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/spec-page"},
        ],
        "data": {
            "specifications": [{"key": "weight", "value": '51 ק"ג', "cites": [24]}],
        },
    }
    urls = dossier_plan._previous_vendor_official_urls(previous)
    assert urls == ["https://elbitsystems.com/spec-page", "https://elbitsystems.com/news/deal"]


def test_previous_vendor_official_urls_spec_bearing_page_survives_the_cap() -> None:
    """The exact live-bug shape: 8 vendor_official URLs, only the LAST one (n=8) actually carried a
    real spec value -- with the fix, it still lands inside the (raised) cap because it sorts first."""
    sources = [
        {"n": i, "kind": "web", "source_kind": "vendor_official", "url": f"https://elbitsystems.com/news/{i}"}
        for i in range(1, 8)
    ]
    sources.append(
        {"n": 8, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/spec-page"}
    )
    previous = {
        "sources": sources,
        "data": {"other_specifications": [{"parameter_he": "משקל", "value": '51 ק"ג', "cites": [8]}]},
    }
    corpus = _corpus(previous=previous)
    urls = dossier_plan.gather_must_read_urls(corpus)
    assert "https://elbitsystems.com/spec-page" in urls[: dossier_plan._MUST_READ_URL_CAP]


def test_previous_vendor_official_urls_stable_order_when_none_are_spec_bearing() -> None:
    """No previous specifications/performance/other_specifications value cited any of these URLs --
    original n-order is preserved (never reshuffled without reason)."""
    previous = {
        "sources": [
            {"n": 3, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/a"},
            {"n": 4, "kind": "web", "source_kind": "vendor_official", "url": "https://elbitsystems.com/b"},
        ],
        "data": {},
    }
    urls = dossier_plan._previous_vendor_official_urls(previous)
    assert urls == ["https://elbitsystems.com/a", "https://elbitsystems.com/b"]


def test_run_must_read_fetches_and_registers_new_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch_remote(url: str) -> dict[str, Any]:
        return {
            "text": "SPECTRO XR carries a 7-inch common spotter.",
            "title": "SPECTRO XR",
            "published_at": None,
        }

    monkeypatch.setattr(dossier_plan, "fetch_remote", fake_fetch_remote)
    corpus = _corpus()
    blocks = dossier_plan.run_must_read(corpus, ["https://elbitsystems.com/product/spectro"])
    web_rows = [r for r in corpus.registry if r["kind"] == "web"]
    assert len(web_rows) == 1
    assert web_rows[0]["source_kind"] == "vendor_official"
    assert web_rows[0]["topic"] == "must_read"
    assert len(blocks) == 1
    assert "7-inch common spotter" in blocks[0]


def test_run_must_read_reuses_existing_row_n_instead_of_duplicating(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL that is already a DB item's own url (e.g. a press item that IS the vendor's own
    announcement page) still gets fetched -- its content wasn't sitting in the registry, just its
    url/title were -- but folds into a context block under that item's OWN ``n``, never a second,
    duplicate registry row for the identical page."""

    def fake_fetch_remote(url: str) -> dict[str, Any]:
        return {"text": "SPECTRO XR carries a Jetson Xavier compute module.", "title": "", "published_at": None}

    monkeypatch.setattr(dossier_plan, "fetch_remote", fake_fetch_remote)
    corpus = _corpus(
        registry=[{"n": 1, "kind": "item", "title": "Elbit unveils SPECTRO XR", "url": "https://elbitsystems.com/x"}]
    )
    blocks = dossier_plan.run_must_read(corpus, ["https://elbitsystems.com/x"])
    assert len(corpus.registry) == 1  # no second row minted
    assert len(blocks) == 1
    assert blocks[0].startswith("[1] ")
    assert "Jetson Xavier" in blocks[0]


def test_run_must_read_swallows_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch_remote(url: str) -> dict[str, Any]:
        raise RuntimeError("network down")

    monkeypatch.setattr(dossier_plan, "fetch_remote", fake_fetch_remote)
    corpus = _corpus()
    blocks = dossier_plan.run_must_read(corpus, ["https://elbitsystems.com/x"])
    assert blocks == []
    assert [r for r in corpus.registry if r["kind"] == "web"] == []


def test_run_plan_folds_must_read_blocks_into_context_he(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(kwargs["context_he"])
        return _inv(read_sources=[], read_summaries=[])

    def fake_fetch_remote(url: str) -> dict[str, Any]:
        return {"text": "עמוד יצרן רשמי עם מפרט.", "title": "Spectro page", "published_at": None}

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    monkeypatch.setattr(dossier_plan, "fetch_remote", fake_fetch_remote)
    corpus = _corpus(registry=[{"n": 1, "kind": "item", "url": "https://elbitsystems.com/x"}])
    dossier_plan.run_plan(corpus, max_topics=1)
    assert "עמודי יצרן שחובה להביא בחשבון" in captured[0]
    assert "Spectro page" in captured[0]


def test_run_plan_prefixes_site_restricted_question_for_spec_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(question)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()  # vendor "Elbit Systems" resolves via the hint map even with no registry
    dossier_plan.run_plan(corpus, max_topics=1)  # topic[0] == "specifications"
    assert captured[0].startswith("חפש תחילה באתר היצרן בלבד (site:elbitsystems.com")


def test_run_plan_does_not_site_restrict_non_spec_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(question)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    deals_idx = [t.key for t in dossier_plan.TOPICS].index("deals")
    assert not captured[deals_idx].startswith("חפש תחילה באתר היצרן")


# --------------------------------------------------------------------------
# item C: read budget (not round budget) per topic
# --------------------------------------------------------------------------


def test_run_plan_retries_topic_when_candidates_found_but_reads_below_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls["n"] += 1
        url = f"https://instro.com/page{calls['n']}"
        inv = _inv(
            read_sources=[{"url": url, "title": "Instro"}],
            read_summaries=[{"url": url, "title": "Instro", "summary": "מפרט SPECTRO XR."}],
        )
        # A non-empty hits_seen signals "search found candidates" -- the read-budget retry only
        # fires when this is populated (an investigation whose search found nothing has no
        # candidates to top up with, and must not be retried -- see the "no retry" test below).
        inv.hits_seen = {url: SearchHit(url=url, title="Instro", snippet="", engine="test")}
        return inv

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    result = dossier_plan.run_plan(corpus, max_topics=1)
    # 1 successful (deduped-relevant) read per call, floor is 3 -> up to 3 attempts total.
    assert calls["n"] == dossier_plan._MAX_TOPIC_READ_ATTEMPTS
    assert len(result.findings[0].source_ns) == dossier_plan._MAX_TOPIC_READ_ATTEMPTS


def test_run_plan_does_not_retry_when_search_found_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls["n"] += 1
        # hits_seen stays empty (the dataclass default) -- search genuinely found nothing.
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert calls["n"] == 1


def test_run_plan_stops_retrying_once_floor_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls["n"] += 1
        # Every call returns 2 fresh reads -- floor of 3 is reached after the 2nd call (4 total),
        # so a 3rd call must never happen.
        urls = [f"https://instro.com/{calls['n']}-{i}" for i in range(2)]
        inv = _inv(
            read_sources=[{"url": u, "title": "Instro"} for u in urls],
            read_summaries=[{"url": u, "title": "Instro", "summary": "מפרט SPECTRO XR."} for u in urls],
        )
        inv.hits_seen = {u: SearchHit(url=u, title="Instro", snippet="", engine="test") for u in urls}
        return inv

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert calls["n"] == 2


def test_run_plan_progress_records_pages_read_with_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(
            read_sources=[{"url": "https://www.elbitsystems.com/x", "title": ""}],
            read_summaries=[
                {"url": "https://www.elbitsystems.com/x", "title": "X", "summary": "מפרט SPECTRO XR."}
            ],
        )

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    snapshots: list[list[dict[str, Any]]] = []
    dossier_plan.run_plan(corpus, max_topics=1, on_progress=lambda p: snapshots.append(p))
    pages_read = snapshots[-1][0]["pages_read"]
    assert len(pages_read) == 1
    assert pages_read[0]["kind"] == "vendor_official"
    assert pages_read[0]["url"] == "https://www.elbitsystems.com/x"


# --------------------------------------------------------------------------
# LESSONS-1 item 1: datasheet hunt wiring
# --------------------------------------------------------------------------


def test_run_plan_folds_datasheet_hunt_into_context_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(kwargs.get("context_he", ""))
        return _inv(read_sources=[], read_summaries=[])

    def fake_hunt_datasheets(product_name: str, vendor: str | None, aliases: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "url": "https://elbitsystems.com/brochure.pdf",
                "title": "SPECTRO XR Brochure",
                "text": "InSb detector 1280x1024 sensor",
                "pages": 4,
                "kind": "pdf",
            }
        ]

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    monkeypatch.setattr(dossier_plan, "hunt_datasheets", fake_hunt_datasheets)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert "InSb" in captured[0]
    datasheet_rows = [r for r in corpus.registry if r.get("source_kind") == "datasheet"]
    assert len(datasheet_rows) == 1
    assert datasheet_rows[0]["reliability"] == "primary"
    assert len(corpus.datasheets) == 1
    assert corpus.datasheets[0]["n"] == datasheet_rows[0]["n"]
    assert "InSb" in corpus.datasheets[0]["text"]


def test_run_plan_datasheet_hunt_failure_does_not_break_build(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    def failing_hunt(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        raise RuntimeError("network down")

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    monkeypatch.setattr(dossier_plan, "hunt_datasheets", failing_hunt)
    corpus = _corpus()
    result = dossier_plan.run_plan(corpus, max_topics=1)
    assert len(result.findings) == 1
    assert corpus.datasheets == []


def test_run_plan_datasheet_reuses_existing_registry_row_for_same_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    def fake_hunt_datasheets(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"url": "https://elbitsystems.com/x", "title": "X", "text": "InSb 1280", "pages": 1, "kind": "page"}]

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    monkeypatch.setattr(dossier_plan, "hunt_datasheets", fake_hunt_datasheets)
    corpus = _corpus(registry=[{"n": 1, "kind": "item", "url": "https://elbitsystems.com/x"}])
    dossier_plan.run_plan(corpus, max_topics=1)
    assert len(corpus.registry) == 1
    assert corpus.registry[0]["source_kind"] == "datasheet"
    assert corpus.datasheets[0]["n"] == 1


# --------------------------------------------------------------------------
# LESSONS-1 item 3: competitor seeds wiring
# --------------------------------------------------------------------------


def test_run_plan_points_competitors_topic_at_named_products(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        captured.append(question)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus(product_line="targeting_pods")
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    competitors_idx = [t.key for t in dossier_plan.TOPICS].index("competitors")
    assert "Sniper ATP" in captured[competitors_idx]
    assert any(c["name"] == "Litening 5" for c in corpus.competitor_seeds)


def test_run_plan_no_competitor_seeds_when_product_line_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=1)
    assert corpus.competitor_seeds == []


# --------------------------------------------------------------------------
# LESSONS-1 item 2: programme-deal search wiring (+ item 5: multilingual)
# --------------------------------------------------------------------------


def test_run_plan_runs_programme_deal_search_for_identified_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    deal_url = "https://overtdefense.com/romania-watchkeeper-x"

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        if "פלטפורמות/כלי טיס" in question:
            inv = Investigation(job_id=None, item_id=None, question=question)
            inv.result = InvestigationOut(
                outcome="found", answer_he="המוצר משולב על גבי Watchkeeper X.", confidence=0.7, sources=[]
            )
            return inv
        if "עבור הפלטפורמה Watchkeeper X" in question:
            inv = Investigation(job_id=None, item_id=None, question=question)
            inv.read_sources = [{"url": deal_url, "title": "Romania buys Watchkeeper X"}]
            inv.read_summaries = [
                {
                    "url": deal_url,
                    "title": "Romania buys Watchkeeper X",
                    # deliberately never names the product itself -- proves the platform's own
                    # name (extra_relevance_terms) is what keeps this page from being dropped.
                    "summary": "רומניה רכשה מערכות Watchkeeper X בעסקה בהיקף משמעותי.",
                }
            ]
            inv.result = InvestigationOut(
                outcome="found",
                answer_he="רומניה חתמה על עסקה בהיקף כ-180 מיליון דולר עבור Watchkeeper X.",
                confidence=0.7,
                sources=[],
            )
            return inv
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    assert corpus.programme_deals
    deal = corpus.programme_deals[0]
    assert deal["platform"] == "Watchkeeper X"
    assert deal["amount_value"] == 180_000_000
    assert deal["component_of_package"] is True
    matching_rows = [r for r in corpus.registry if r.get("url") == deal_url]
    assert len(matching_rows) == 1
    assert matching_rows[0].get("component_of_package") is True


def test_run_plan_no_platforms_identified_means_no_programme_deals(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    assert corpus.programme_deals == []


def test_run_plan_extends_search_languages_after_customer_country_identified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_langs: list[Any] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        seen_langs.append(kwargs.get("langs"))
        if "פלטפורמות/כלי טיס" in question:
            inv = Investigation(job_id=None, item_id=None, question=question)
            inv.result = InvestigationOut(outcome="found", answer_he="Hermes 900 בשימוש.", confidence=0.7, sources=[])
            return inv
        if "עבור הפלטפורמה Hermes 900" in question:
            inv = Investigation(job_id=None, item_id=None, question=question)
            inv.result = InvestigationOut(
                outcome="found", answer_he="רומניה רכשה Hermes 900 ב-72 מיליון דולר.", confidence=0.6, sources=[]
            )
            return inv
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    platforms_idx = [t.key for t in dossier_plan.TOPICS].index("platforms_and_programmes")
    assert seen_langs[platforms_idx] is None
    assert seen_langs[-1] is not None
    assert "ro" in seen_langs[-1]


# --------------------------------------------------------------------------
# LESSONS-1 item 4: gap follow-up topics wiring
# --------------------------------------------------------------------------


def test_run_plan_adds_gap_followup_topics_from_previous_dossier(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls.append(question)
        inv = Investigation(job_id=None, item_id=None, question=question)
        inv.result = InvestigationOut(outcome="found", answer_he="נמצא מחיר: 2 מיליון דולר.", confidence=0.8, sources=[])
        return inv

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    previous = {"id": 1, "data": {"risks_and_gaps_he": ["לא נמצא מחיר רשמי"]}}
    corpus = _corpus(previous=previous)
    result = dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    assert any("לא נמצא מחיר רשמי" in q for q in calls)
    assert corpus.gap_status == [{"gap": "לא נמצא מחיר רשמי", "status": "closed", "cites": []}]
    assert any(f.key == "gap_followup_1" for f in result.findings)


def test_run_plan_gap_followup_stays_open_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        inv = Investigation(job_id=None, item_id=None, question=question)
        inv.result = InvestigationOut(outcome="not_found", answer_he="לא נמצא מידע.", confidence=0.0, sources=[])
        return inv

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    previous = {"id": 1, "data": {"risks_and_gaps_he": ["לא נמצא TRL"]}}
    corpus = _corpus(previous=previous)
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    assert corpus.gap_status == [{"gap": "לא נמצא TRL", "status": "open", "cites": []}]


def test_run_plan_no_previous_dossier_means_no_gap_followup_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_investigate(question: str, **kwargs: Any) -> Investigation:
        calls.append(question)
        return _inv(read_sources=[], read_summaries=[])

    monkeypatch.setattr(dossier_plan, "investigate", fake_investigate)
    corpus = _corpus()
    dossier_plan.run_plan(corpus, max_topics=len(dossier_plan.TOPICS))
    assert corpus.gap_status == []
    assert not any("gap_followup" in q for q in calls)


# --------------------------------------------------------------------------
# TEST-ISO (2026-09-09, docs/qa/content_review/TEST-ISO.md): a run of this file went live (86
# minutes, 4 failures, DuckDuckGo blocked) because nothing stopped an unmocked code path from
# reaching the real network primitives ``eoa.search.provider``/``eoa.search.pdf_reader``/
# ``eoa.fetch.remote``/``eoa.patents.scan`` all eventually funnel through. ``tests/conftest.py``'s
# ``_dossier_network_guard`` autouse fixture now patches those two primitives -- ``httpx.Client.get``
# /``.post`` and ``ddgs.DDGS.text`` -- to raise for every test in this file (and the other three
# owned dossier test files). This is not a test of ``eoa.dossier.plan`` itself: it is a regression
# test for the guard fixture, proving it is actually wired up and active for this module.
# --------------------------------------------------------------------------


def test_network_guard_blocks_httpx_client_get() -> None:
    import httpx

    with httpx.Client() as client, pytest.raises(RuntimeError, match="live network call attempted"):
        client.get("https://example.com")


def test_network_guard_blocks_httpx_client_post() -> None:
    import httpx

    with httpx.Client() as client, pytest.raises(RuntimeError, match="live network call attempted"):
        client.post("https://example.com")


def test_network_guard_blocks_ddgs_text() -> None:
    from ddgs import DDGS

    with DDGS() as ddgs, pytest.raises(RuntimeError, match="live network call attempted"):
        ddgs.text("SPECTRO XR datasheet")
