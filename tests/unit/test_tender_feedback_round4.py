"""W2b (docs/REVIEW_2026-09-06_evening.md, user requirement 2026-09-06 18:55, verbatim: "be open --
and through the relevance feedback given to each tender, the system tunes itself"). Pure logic
only: no real DB/LLM/network (mirrors tests/unit/test_tenders_scan.py's own monkeypatch-everything
style). Covers:

- open intake: a notice the LLM scores as "not relevant" is stored (as a candidate), not dropped;
- hard rejections (status_hint / denylisted domain / dead link) still drop a notice outright;
- `eoa.tenders.feedback.record_feedback` updates the tender's `intake` and recomputes both
  self-tuning values;
- `recompute_relevance_threshold` on a synthetic feedback set;
- `recompute_source_priority`;
- the tender_extract prompt actually receives the lessons text.
"""

from __future__ import annotations

from unittest.mock import patch

from eoa.llm.schemas.tenders import TenderExtract
from eoa.tenders import feedback as fb
from eoa.tenders.scan import NoticeRaw, TenderSource, scan_tenders

DOMAIN_KEYWORDS = ["electro-optical", "infrared", "targeting pod"]


def _search_source(**overrides) -> TenderSource:
    base = dict(
        id="rfi_rfp_news",
        name="RFI/RFP news",
        kind="search",
        country="other",
        queries=["request for information electro-optical defense"],
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


def _extract(**overrides) -> TenderExtract:
    base = dict(
        relevant=True,
        relevance=6,
        summary_he="סיכום",
        matched_terms=["infrared"],
        entities=[],
        confidence=0.8,
        notice_type="tender",
    )
    base.update(overrides)
    return TenderExtract(**base)


def _scan_patches():
    """Every DB-touching helper scan_tenders() would otherwise call, stubbed -- mirrors
    tests/unit/test_tenders_scan.py's own `_common_patches`."""
    return (
        patch("eoa.tenders.scan._tender_exists", return_value=False),
        # R6-data title+portal dedupe is also a DB read; unstubbed it hits the real pool
        # (mirrors tests/unit/test_tenders_scan.py's own _common_patches).
        patch("eoa.tenders.scan._candidate_duplicate_exists", return_value=False),
        patch("eoa.tenders.scan._transition_closed", return_value=0),
        patch("eoa.tenders.scan._archive_stale_closed", return_value=0),
        patch("eoa.tenders.scan.redrive_all_tender_statuses", return_value=0),
        patch("eoa.tenders.scan.get_relevance_threshold", return_value=0.6),
        patch("eoa.tenders.scan.get_source_priorities", return_value={}),
    )


# --------------------------------------------------------------------------
# open intake: a candidate is stored, not dropped -- hard rejections still drop
# --------------------------------------------------------------------------


class TestOpenIntakeStoresLlmRejectedCandidate:
    def test_llm_rejected_notice_is_stored_as_candidate_with_its_score(self):
        """The core W2b behavior: the LLM says relevant=False/relevance=0 -- under the old F24
        gate this notice was dropped outright; now it is stored, carrying its own (low)
        relevance_score, as an inspectable 'candidate' rather than lost data."""
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="Electro-Optic/Infrared Sight System - Sources Sought - N0016426SNB35",
            url="https://govtribe.com/opportunity/eo-ir-sight-system",
        )
        patches = _scan_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch(
                "eoa.tenders.scan._llm_classify",
                return_value=(_extract(relevant=False, relevance=0), True),
            ),
            patch("eoa.tenders.scan._insert_tender_and_item", return_value=(1, 2)) as mock_insert,
        ):
            stats = scan_tenders(sources=[src])

        mock_insert.assert_called_once()
        _, kwargs = mock_insert.call_args
        assert kwargs["relevance_score"] == 0.0
        assert kwargs["intake"] == "candidate"
        assert stats.inserted == 1
        assert stats.candidates == 1
        assert stats.gate_rejected == 0


class TestHardRejectionsStillDrop:
    def test_denylisted_domain_still_dropped(self):
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFP for infrared targeting pod",
            url="https://www.scribd.com/document/1/x",
        )
        patches = _scan_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1
        assert stats.inserted == 0

    def test_awarded_status_hint_still_dropped(self):
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id,
            external_ref=f"{src.id}:1",
            title="RFP for infrared targeting pod",
            url="https://example.gov/n/1",
            status_hint="awarded",
        )
        patches = _scan_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1

    def test_dead_link_no_url_still_dropped(self):
        src = _search_source()
        notice = NoticeRaw(
            source_id=src.id, external_ref=f"{src.id}:1", title="RFP for infrared targeting pod"
        )
        assert notice.url is None
        patches = _scan_patches()
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            patches[4],
            patches[5],
            patches[6],
            patch("eoa.tenders.scan._collect_source_notices", return_value=[notice]),
            patch("eoa.tenders.scan._llm_classify", return_value=(_extract(), True)),
            patch("eoa.tenders.scan._insert_tender_and_item") as mock_insert,
        ):
            stats = scan_tenders(sources=[src])
        mock_insert.assert_not_called()
        assert stats.gate_rejected == 1


# --------------------------------------------------------------------------
# fake DB layer for eoa.tenders.feedback's own functions
# --------------------------------------------------------------------------


class _FakeCursor:
    """Matches each `execute()` call against an ordered list of (predicate, result) rules the test
    supplies; the first rule whose predicate returns True on the executed SQL wins. Every executed
    statement is logged in `.executed` for assertions. `result` is either a list of dict rows
    (`fetchall`) or a single dict/`None` (`fetchone`)."""

    def __init__(self, rules):
        self.rules = rules
        self.executed: list[tuple[str, dict | None]] = []
        self._last = None

    def execute(self, query, params=None):
        self.executed.append((query, params))
        for predicate, result in self.rules:
            if predicate(query, params):
                self._last = result
                return self
        self._last = None
        return self

    def fetchone(self):
        if isinstance(self._last, list):
            return self._last[0] if self._last else None
        return self._last

    def fetchall(self):
        return self._last if self._last is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _contains(*fragments):
    def predicate(query, params):
        return all(f in query for f in fragments)

    return predicate


# --------------------------------------------------------------------------
# feedback endpoint updates intake
# --------------------------------------------------------------------------


class TestRecordFeedbackUpdatesIntake:
    def test_thumbs_up_sets_intake_accepted(self, monkeypatch):
        cur = _FakeCursor(
            [
                (
                    _contains("SELECT source, country, matched_terms FROM tenders"),
                    {"source": "rfi_rfp_news", "country": "US", "matched_terms": ["infrared"]},
                ),
                (
                    _contains("INSERT INTO tender_feedback"),
                    {
                        "id": 1,
                        "tender_id": 42,
                        "verdict": "relevant",
                        "reason": "real Navy RFI",
                        "source": "rfi_rfp_news",
                        "territory": "US",
                        "matched_terms": ["infrared"],
                        "created_at": "2026-09-06T00:00:00Z",
                    },
                ),
                (_contains("UPDATE tenders SET intake"), None),
                # threshold recompute below min_samples -- no-op
                (_contains("FROM tender_feedback tf", "JOIN tenders t"), []),
                # source priority: no recent tenders for this source in this fake -- no-op
                (_contains("SELECT id FROM tenders WHERE source"), []),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        row = fb.record_feedback(42, "relevant", "real Navy RFI")

        assert row is not None
        assert row["verdict"] == "relevant"
        update_calls = [q for q, p in cur.executed if "UPDATE tenders SET intake" in q]
        assert len(update_calls) == 1
        _, update_params = next(c for c in cur.executed if "UPDATE tenders SET intake" in c[0])
        assert update_params == {"intake": "accepted", "id": 42}

    def test_thumbs_down_sets_intake_rejected_by_user(self, monkeypatch):
        cur = _FakeCursor(
            [
                (
                    _contains("SELECT source, country, matched_terms FROM tenders"),
                    {"source": "rfi_rfp_news", "country": "US", "matched_terms": []},
                ),
                (_contains("INSERT INTO tender_feedback"), {"id": 2, "verdict": "irrelevant"}),
                (_contains("UPDATE tenders SET intake"), None),
                (_contains("FROM tender_feedback tf", "JOIN tenders t"), []),
                (_contains("SELECT id FROM tenders WHERE source"), []),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        row = fb.record_feedback(7, "irrelevant", None)

        assert row is not None
        _, update_params = next(c for c in cur.executed if "UPDATE tenders SET intake" in c[0])
        assert update_params == {"intake": "rejected-by-user", "id": 7}

    def test_unknown_tender_returns_none(self, monkeypatch):
        cur = _FakeCursor([(_contains("SELECT source, country, matched_terms FROM tenders"), None)])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        assert fb.record_feedback(999, "relevant") is None
        # no INSERT/UPDATE ever attempted once the tender doesn't exist
        assert not any("INSERT INTO tender_feedback" in q for q, _ in cur.executed)


# --------------------------------------------------------------------------
# threshold learning on a synthetic feedback set
# --------------------------------------------------------------------------


class TestRecomputeRelevanceThreshold:
    def test_below_min_samples_is_a_no_op(self, monkeypatch):
        rows = [{"score": 0.8, "verdict": "relevant"}] * 3
        cur = _FakeCursor([(_contains("FROM tender_feedback tf", "JOIN tenders t"), rows)])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        result = fb.recompute_relevance_threshold(min_samples=10)

        assert result is None
        assert not any("tender_relevance_state" in q for q, _ in cur.executed)

    def test_finds_the_separating_threshold(self, monkeypatch):
        # A clean synthetic split: every 👍 scored >= 0.7, every 👎 scored <= 0.4 -- the best
        # threshold is anywhere in (0.4, 0.7]; the midpoint search lands on 0.55.
        positives = [{"score": s, "verdict": "relevant"} for s in (0.7, 0.75, 0.8, 0.9, 0.95, 1.0)]
        negatives = [{"score": s, "verdict": "irrelevant"} for s in (0.0, 0.1, 0.2, 0.3, 0.35, 0.4)]
        rows = positives + negatives
        assert len(rows) >= fb.MIN_SAMPLES_FOR_THRESHOLD

        cur = _FakeCursor(
            [
                (_contains("FROM tender_feedback tf", "JOIN tenders t"), rows),
                (_contains("INSERT INTO tender_relevance_state"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        threshold = fb.recompute_relevance_threshold()

        assert threshold == 0.55
        _, persisted_params = next(c for c in cur.executed if "tender_relevance_state" in c[0])
        assert persisted_params["threshold"] == 0.55

    def test_clamped_to_bounds_on_a_pathological_all_negative_batch(self, monkeypatch):
        # Every sample is 👎 -- the "best" raw threshold would be 1.0 (reject everything), but the
        # user's own requirement clamps this to THRESHOLD_MAX (0.8) so a bad batch can never make
        # the system stop accepting anything at all.
        rows = [
            {"score": s, "verdict": "irrelevant"} for s in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
        ]
        cur = _FakeCursor(
            [
                (_contains("FROM tender_feedback tf", "JOIN tenders t"), rows),
                (_contains("INSERT INTO tender_relevance_state"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        threshold = fb.recompute_relevance_threshold()

        assert threshold == fb.THRESHOLD_MAX

    def test_clamped_to_bounds_on_a_pathological_all_positive_batch(self, monkeypatch):
        rows = [
            {"score": s, "verdict": "relevant"} for s in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
        ]
        cur = _FakeCursor(
            [
                (_contains("FROM tender_feedback tf", "JOIN tenders t"), rows),
                (_contains("INSERT INTO tender_relevance_state"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        threshold = fb.recompute_relevance_threshold()

        assert threshold == fb.THRESHOLD_MIN


class TestGetRelevanceThreshold:
    def test_reads_persisted_value(self, monkeypatch):
        cur = _FakeCursor([(_contains("SELECT relevance_threshold"), {"relevance_threshold": 0.42})])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))
        assert fb.get_relevance_threshold() == 0.42

    def test_falls_back_to_default_on_missing_row(self, monkeypatch):
        cur = _FakeCursor([(_contains("SELECT relevance_threshold"), None)])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))
        assert fb.get_relevance_threshold() == fb.DEFAULT_RELEVANCE_THRESHOLD

    def test_falls_back_to_default_on_db_error(self, monkeypatch):
        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(fb, "connection", _raise)
        assert fb.get_relevance_threshold() == fb.DEFAULT_RELEVANCE_THRESHOLD


# --------------------------------------------------------------------------
# source priority decrement
# --------------------------------------------------------------------------


class TestRecomputeSourcePriority:
    def test_source_with_feedback_but_never_a_thumbs_up_gets_decremented(self, monkeypatch):
        cur = _FakeCursor(
            [
                (_contains("SELECT id FROM tenders WHERE source"), [{"id": i} for i in range(1, 6)]),
                (_contains("FROM tender_feedback WHERE tender_id"), [{"verdict": "irrelevant", "n": 4}]),
                (_contains("INSERT INTO tender_source_priority"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        decrement = fb.recompute_source_priority("rfi_rfp_news")

        assert decrement == fb.SOURCE_PRIORITY_DECREMENT
        _, persisted = next(c for c in cur.executed if "tender_source_priority" in c[0])
        assert persisted == {"source_id": "rfi_rfp_news", "decrement": fb.SOURCE_PRIORITY_DECREMENT}

    def test_source_with_at_least_one_thumbs_up_stays_at_baseline(self, monkeypatch):
        cur = _FakeCursor(
            [
                (_contains("SELECT id FROM tenders WHERE source"), [{"id": i} for i in range(1, 6)]),
                (
                    _contains("FROM tender_feedback WHERE tender_id"),
                    [{"verdict": "irrelevant", "n": 3}, {"verdict": "relevant", "n": 1}],
                ),
                (_contains("INSERT INTO tender_source_priority"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        assert fb.recompute_source_priority("ted_eu") == 0

    def test_source_with_no_feedback_at_all_stays_at_baseline(self, monkeypatch):
        """No evidence either way yet -- never guess a decrement just because a source is new."""
        cur = _FakeCursor(
            [
                (_contains("SELECT id FROM tenders WHERE source"), [{"id": 1}]),
                (_contains("FROM tender_feedback WHERE tender_id"), []),
                (_contains("INSERT INTO tender_source_priority"), None),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        assert fb.recompute_source_priority("brand_new_source") == 0

    def test_source_with_no_stored_tenders_at_all_short_circuits(self, monkeypatch):
        cur = _FakeCursor([(_contains("SELECT id FROM tenders WHERE source"), [])])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        assert fb.recompute_source_priority("never_matched_source") == 0
        # never even queries feedback/writes a priority row for a source with zero stored notices
        assert not any("tender_source_priority" in q for q, _ in cur.executed)

    def test_get_source_priorities_returns_map(self, monkeypatch):
        cur = _FakeCursor(
            [
                (
                    _contains("SELECT source_id, priority_decrement"),
                    [{"source_id": "rfi_rfp_news", "priority_decrement": -1}],
                )
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))
        assert fb.get_source_priorities() == {"rfi_rfp_news": -1}

    def test_get_source_priorities_never_raises(self, monkeypatch):
        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(fb, "connection", _raise)
        assert fb.get_source_priorities() == {}


# --------------------------------------------------------------------------
# lessons injected into the prompt
# --------------------------------------------------------------------------


class TestTenderLessonsText:
    def test_formats_positive_and_negative_examples(self, monkeypatch):
        cur = _FakeCursor(
            [
                (
                    lambda q, p: bool(p) and p.get("verdict") == "relevant",
                    [{"verdict": "relevant", "reason": "real Navy RFI", "title": "EOIR Sight System RFI"}],
                ),
                (
                    lambda q, p: bool(p) and p.get("verdict") == "irrelevant",
                    [
                        {
                            "verdict": "irrelevant",
                            "reason": "roof-window tender, camera mentioned in passing",
                            "title": "Municipal building RFP",
                        }
                    ],
                ),
            ]
        )
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))

        text = fb.tender_lessons_text()

        assert "EOIR Sight System RFI" in text
        assert "real Navy RFI" in text
        assert "👍" in text
        assert "Municipal building RFP" in text
        assert "👎" in text

    def test_no_feedback_yet_returns_hebrew_placeholder(self, monkeypatch):
        cur = _FakeCursor([(lambda q, p: True, [])])
        monkeypatch.setattr(fb, "connection", lambda: _FakeConnection(cur))
        text = fb.tender_lessons_text()
        assert "אין עדיין משוב" in text

    def test_never_raises_on_db_error(self, monkeypatch):
        def _raise():
            raise RuntimeError("db down")

        monkeypatch.setattr(fb, "connection", _raise)
        assert fb.tender_lessons_text() == "אין עדיין משוב רלוונטיות קודם מהמשתמש."


class TestLessonsReachTheTenderExtractPrompt:
    def test_llm_classify_passes_lessons_into_render(self):
        """scan._llm_classify must actually forward eoa.tenders.feedback.tender_lessons_text()'s
        output into the tender_extract prompt's {lessons} placeholder -- not just compute it and
        drop it -- so operator feedback genuinely reshapes future LLM classifications, not only
        the stored intake."""
        from eoa.tenders import scan as scan_mod

        notice = NoticeRaw(
            source_id="rfi_rfp_news",
            external_ref="rfi_rfp_news:1",
            title="RFI for infrared sensor",
            url="https://example.gov/n/1",
        )
        captured_kwargs = {}
        real_render = scan_mod.render

        def spy_render(name, **kwargs):
            if name == "tender_extract":
                captured_kwargs.update(kwargs)
            return real_render(name, **kwargs)

        with (
            patch("eoa.tenders.scan.tender_lessons_text", return_value="- [👍 רלוונטי] דוגמה קודמת"),
            patch("eoa.tenders.scan.render", side_effect=spy_render),
            patch("eoa.tenders.scan._fetch_notice_text", return_value=("some text", True)),
            patch("eoa.tenders.scan.chat_structured", return_value=_extract()),
        ):
            scan_mod._llm_classify(notice, role="resident", interactive=False, src_kind="search")

        assert captured_kwargs.get("lessons") == "- [👍 רלוונטי] דוגמה קודמת"

    def test_prompt_template_has_a_lessons_placeholder(self):
        """The template file itself must actually declare {lessons} -- otherwise passing the
        kwarg above would silently do nothing (eoa.llm.prompts.render leaves unknown/unused
        placeholders untouched, but a missing {lessons} in the template means the value is simply
        never inserted anywhere)."""
        from eoa.llm.prompts import load

        assert "{lessons}" in load("tender_extract")
