"""Unit tests for A16 (מעקב רכישות ושותפויות, user requirement 2026-09-06):

  - `eoa.pipeline.acquisition` -- pure watchlist lookups + the M&A/investment/partnership
    vocabulary check (`has_ma_signal`).
  - `eoa.pipeline.triage._apply_acquisition_watch_boost` -- the deterministic triage alert.
  - `eoa.report.acquisition_watch.acquisition_watch_section_md` -- the weekly/BD section body,
    against a fake DB connection/cursor (no live DB required).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_acquisition_watch.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.llm.schemas.analysis import TriageOut
from eoa.pipeline import acquisition as acq
from eoa.pipeline import triage
from eoa.report import acquisition_watch as aw_report

# --------------------------------------------------------------------------
# eoa.pipeline.acquisition -- watchlist lookups
# --------------------------------------------------------------------------


class TestWatchlistLookups:
    def test_acquisition_watch_names_include_seeded_companies(self) -> None:
        names = acq.acquisition_watch_names()
        assert "Teledyne" in names
        assert "PVP Advanced EO Systems" in names
        assert "Opgal" in names

    def test_is_acquisition_watch_name_true_for_canonical_name(self) -> None:
        assert acq.is_acquisition_watch_name("Teledyne") is True

    def test_is_acquisition_watch_name_true_for_companies_yaml_alias(self) -> None:
        # "Teledyne Technologies" is a regular `aliases` entry of the watchlist's "Teledyne" record.
        assert acq.is_acquisition_watch_name("Teledyne Technologies") is True
        assert acq.canonicalize_name("Teledyne Technologies") == "Teledyne"

    def test_is_acquisition_watch_name_case_insensitive(self) -> None:
        assert acq.is_acquisition_watch_name("teledyne") is True

    def test_is_acquisition_watch_name_false_for_unrelated_company(self) -> None:
        assert acq.is_acquisition_watch_name("Northrop Grumman") is False
        assert acq.is_acquisition_watch_name(None) is False

    def test_strict_alias_alone_does_not_count(self) -> None:
        # "PVP" is PVP Advanced EO Systems' `strict_aliases` entry, not a regular alias -- must not
        # resolve on its own (mirrors config/watchlist.yaml's collision-risk convention).
        assert acq.canonicalize_name("PVP") is None

    def test_regular_alias_of_pvp_resolves(self) -> None:
        assert acq.canonicalize_name("PVP Advanced EO") == "PVP Advanced EO Systems"

    def test_peers_of_pvp_includes_its_direct_competitors(self) -> None:
        peers = acq.peers_of("PVP Advanced EO Systems")
        assert "Opgal" in peers
        assert "Elbit" in peers
        assert "Lynred" in peers

    def test_peers_of_unwatched_company_is_empty(self) -> None:
        assert acq.peers_of("Northrop Grumman") == []

    def test_all_watch_and_peer_names_includes_peers_not_separately_watched(self) -> None:
        names = acq.all_watch_and_peer_names()
        assert "Teledyne" in names  # a watch company itself
        assert "Elbit" in names  # only present as one of PVP's peers_of, not its own watch entry
        assert "Controp" in names

    def test_watch_or_peer_hit_finds_peer_by_alias(self) -> None:
        # "Elbit Systems" is Elbit's own companies.yaml alias.
        assert acq.watch_or_peer_hit(["Elbit Systems"]) == "Elbit"

    def test_acquisition_watch_hit_ignores_non_watch_entities(self) -> None:
        assert acq.acquisition_watch_hit(["Northrop Grumman", "Thales"]) is None

    def test_acquisition_watch_hit_returns_first_match(self) -> None:
        assert acq.acquisition_watch_hit(["Northrop Grumman", "Teledyne Technologies"]) == "Teledyne"


# --------------------------------------------------------------------------
# eoa.pipeline.acquisition -- has_ma_signal
# --------------------------------------------------------------------------


class TestHasMaSignal:
    @pytest.mark.parametrize(
        "text",
        [
            "Teledyne announced the acquisition of a small EO startup.",
            "The company completed a merger with its main rival.",
            "Investors are eyeing a buyout of the thermal-imaging unit.",
            "The firm raised a new funding round from strategic investors.",
            "Both sides agreed to a joint venture for detector manufacturing.",
            "PVP took a minority stake in the supplier.",
        ],
    )
    def test_english_keywords_detected(self, text: str) -> None:
        assert acq.has_ma_signal(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "החברה הודיעה על רכישת יצרן חיישנים קטן.",
            "בוצע מיזוג בין שני מתחרים בתחום האופטרוניקה.",
            "הוכרז על סבב גיוס הון חדש למימון הפיתוח.",
            "הצדדים חתמו על הסכם שותפות אסטרטגית.",
            "החברה ביצעה השקעה משמעותית בספק המשנה.",
        ],
    )
    def test_hebrew_keywords_detected(self, text: str) -> None:
        assert acq.has_ma_signal(text) is True

    def test_plain_business_text_has_no_signal(self) -> None:
        assert acq.has_ma_signal("The company delivered its quarterly thermal cameras on schedule.") is False

    def test_empty_text_has_no_signal(self) -> None:
        assert acq.has_ma_signal(None) is False
        assert acq.has_ma_signal("") is False

    def test_event_kind_alone_is_a_signal(self) -> None:
        assert acq.has_ma_signal("", event_kinds={"m_and_a"}) is True
        assert acq.has_ma_signal("", event_kinds={"investment"}) is True
        assert acq.has_ma_signal("", event_kinds={"partnership"}) is True

    def test_unrelated_event_kind_is_not_a_signal(self) -> None:
        assert acq.has_ma_signal("", event_kinds={"contract_award"}) is False

    def test_word_boundary_avoids_false_positive_substring(self) -> None:
        # "investment" must not fire on an unrelated word that merely contains similar letters.
        assert acq.has_ma_signal("The instrument was recalibrated.") is False


# --------------------------------------------------------------------------
# eoa.pipeline.triage._apply_acquisition_watch_boost -- the deterministic triage alert
# --------------------------------------------------------------------------


def _triage_out(score: int = 4, reason_he: str = "נימוק מקורי.") -> TriageOut:
    return TriageOut(
        score=score, level="yellow", novelty=2, magnitude=2, core_relevance=2, reason_he=reason_he
    )


class TestAcquisitionWatchTriageBoost:
    def test_watch_company_without_ma_signal_is_unchanged(self) -> None:
        item = {
            "id": 1,
            "entities_mentioned": ["Teledyne"],
            "title": "Teledyne delivers new thermal cameras to a customer",
            "summary_he": "טלדיין מספקת מצלמות תרמיות ללקוח קיים.",
            "so_what_he": "",
            "clean_text": "",
        }
        out = _triage_out(score=4)
        result = triage._apply_acquisition_watch_boost(item, out)
        assert result.score == 4
        assert not result.reason_he.startswith("מעקב רכישות:")

    def test_ma_signal_without_watch_company_is_unchanged(self) -> None:
        item = {
            "id": 2,
            "entities_mentioned": ["Northrop Grumman"],
            "title": "Northrop Grumman announces acquisition of a small startup",
            "summary_he": "",
            "so_what_he": "",
            "clean_text": "",
        }
        out = _triage_out(score=4)
        result = triage._apply_acquisition_watch_boost(item, out)
        assert result.score == 4
        assert not result.reason_he.startswith("מעקב רכישות:")

    def test_watch_company_and_ma_signal_raises_to_red_threshold_with_prefix(self) -> None:
        item = {
            "id": 3,
            "entities_mentioned": ["Teledyne"],
            "title": "Teledyne announces the acquisition of a thermal-imaging supplier",
            "summary_he": "",
            "so_what_he": "",
            "clean_text": "",
        }
        out = _triage_out(score=4)
        result = triage._apply_acquisition_watch_boost(item, out)
        assert result.score >= 8  # config.yaml triage.levels.red
        assert result.reason_he.startswith("מעקב רכישות: Teledyne")
        assert "נימוק מקורי." in result.reason_he

    def test_never_lowers_an_already_higher_score(self) -> None:
        item = {
            "id": 4,
            "entities_mentioned": ["Teledyne"],
            "title": "Teledyne announces a major acquisition",
            "summary_he": "",
            "so_what_he": "",
            "clean_text": "",
        }
        out = _triage_out(score=10)
        result = triage._apply_acquisition_watch_boost(item, out)
        assert result.score == 10

    def test_no_entities_leaves_item_unchanged(self) -> None:
        item = {"id": 5, "entities_mentioned": [], "title": "acquisition talks", "summary_he": ""}
        out = _triage_out(score=4)
        result = triage._apply_acquisition_watch_boost(item, out)
        assert result.score == 4

    def test_disabled_config_leaves_item_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.config import settings

        cfg = settings()
        original_enabled = cfg.acquisition_watch.enabled
        cfg.acquisition_watch.enabled = False
        try:
            item = {
                "id": 6,
                "entities_mentioned": ["Teledyne"],
                "title": "Teledyne announces the acquisition of a rival",
                "summary_he": "",
                "so_what_he": "",
                "clean_text": "",
            }
            out = _triage_out(score=4)
            result = triage._apply_acquisition_watch_boost(item, out)
            assert result.score == 4
        finally:
            cfg.acquisition_watch.enabled = original_enabled

    def test_triage_item_integration_sets_red_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        model_out = TriageOut(
            score=4,
            level="yellow",
            novelty=1,
            magnitude=2,
            core_relevance=1,
            reason_he="נימוק כלשהו.",
        )
        monkeypatch.setattr(triage, "chat_structured", lambda *a, **k: model_out)
        item = {
            "id": 7,
            "title": "Teledyne to acquire a thermal-imaging startup",
            "domain": "secondary",
            "clean_text": "Teledyne to acquire a thermal-imaging startup",
            "entities_mentioned": ["Teledyne"],
        }
        out = triage.triage_item(item)
        assert out.level == "red"
        assert out.score >= 8
        assert out.reason_he.startswith("מעקב רכישות: Teledyne")


# --------------------------------------------------------------------------
# eoa.report.acquisition_watch -- fake-cursor DB tests
# --------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_query: tuple | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: dict | None = None) -> None:
        self.last_query = (sql, params)

    def fetchall(self) -> list[dict]:
        return self._rows


class FakeConn:
    """Hands out one `FakeCursor` per `cursor()` call, in the order queued -- matches
    `acquisition_watch_section_md`'s fixed call order (events query, then patents query)."""

    def __init__(self, cursor_rows: list[list[dict]]) -> None:
        self._queue = list(cursor_rows)

    def cursor(self) -> FakeCursor:
        rows = self._queue.pop(0) if self._queue else []
        return FakeCursor(rows)


def _event_row(**overrides) -> dict:
    base = dict(
        id=1,
        item_id=100,
        kind="m_and_a",
        date=dt.date(2026, 9, 3),
        amount_usd=50_000_000,
        currency="USD",
        parties=["Teledyne", "PVP Advanced EO Systems"],
        item_url="https://example.com/a",
        item_title="Teledyne acquires PVP",
        published_at=dt.datetime(2026, 9, 3),
        source_name="Example News",
    )
    base.update(overrides)
    return base


class TestAcquisitionWatchSectionMd:
    def test_returns_empty_string_when_disabled(self) -> None:
        from eoa.config import settings

        cfg = settings()
        original_enabled = cfg.acquisition_watch.enabled
        cfg.acquisition_watch.enabled = False
        try:
            conn = FakeConn([[], []])
            body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
            assert body == ""
        finally:
            cfg.acquisition_watch.enabled = original_enabled

    def test_event_row_builds_table_and_extends_registry(self) -> None:
        conn = FakeConn([[_event_row()], []])
        registry: list[dict] = []
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), registry
        )
        assert "| תאריך | חברה |" in body
        assert "Teledyne" in body
        assert "[1]" in body
        assert registry == [
            {
                "id": 100,
                "n": 1,
                "title": "Teledyne acquires PVP",
                "source_name": "Example News",
                "url": "https://example.com/a",
                "published_at": dt.datetime(2026, 9, 3),
            }
        ]

    def test_no_events_shows_placeholder_line(self) -> None:
        conn = FakeConn([[], []])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        assert "לא זוהו אירועי רכישה" in body

    def test_zero_activity_company_gets_a_line(self) -> None:
        # Only "Teledyne" has an event -- every other acquisition_watch company should get its own
        # "לא זוהתה פעילות" line.
        conn = FakeConn([[_event_row(parties=["Teledyne"])], []])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        assert "Opgal: לא זוהתה פעילות." in body
        assert "Teledyne: לא זוהתה פעילות." not in body

    def test_patent_proxy_line_included_when_data_present(self) -> None:
        conn = FakeConn([[], [{"assignees": ["Teledyne"], "value_score": 70}]])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        assert "Teledyne: ציון-ערך פרוקסי ממוצע 70" in body

    def test_patent_proxy_omitted_when_no_data(self) -> None:
        conn = FakeConn([[], []])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        assert "ציון-ערך פרוקסי" not in body

    def test_registry_not_duplicated_when_item_already_present(self) -> None:
        registry = [{"id": 100, "n": 5, "title": "x", "source_name": "s", "url": "u", "published_at": None}]
        conn = FakeConn([[_event_row(item_id=100)], []])
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), registry
        )
        assert "[5]" in body
        assert len(registry) == 1
