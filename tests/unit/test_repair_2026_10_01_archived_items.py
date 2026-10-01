"""Unit tests for `scripts/repair_2026_10_01_archived_items.py`'s pure `compute_changes` plan
(R4, 2026-10-01). No DB: rows are plain dicts shaped like `_fetch_candidates`'s result, using the
two incident items (69207 -- Aug-2013 FBO notice on an archive mirror; 66855 -- undated Defense
Industry Daily evergreen page mentioning only 2008)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_2026_10_01_archived_items.py"
_spec = importlib.util.spec_from_file_location("repair_2026_10_01_archived_items", _PATH)
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)  # type: ignore[union-attr]

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _row(**overrides) -> dict:
    base = {
        "id": 1,
        "url": "https://example.com/some-article",
        "title": "Some article",
        "published_at": None,
        "created_at": NOW,
        "level": "orange",
        "domain": "airborne_pods",
        "report_kind": "verified_report",
        "clean_text": "No years mentioned.",
        "tender_id": None,
        "tender_status": None,
    }
    base.update(overrides)
    return base


def _by_id(changes: list[dict]) -> dict[int, dict]:
    return {c["id"]: c for c in changes}


def test_incident_item_69207_archive_mirror_tender_is_dated_archived_and_tender_closed() -> None:
    row = _row(
        id=69207,
        url="https://ns1.ld.com/archive/2013/08-August/30-Aug-2013/FBO-03166032.htm",
        title="Long Range Multi-Sensor EO/IR Stabilized Gimbal with Multi ...",
        report_kind="tender",
        level="orange",
        tender_id=201,
        tender_status="unknown",
    )
    change = _by_id(repair.compute_changes([row], now=NOW))[69207]
    assert change["published_at_new"] == datetime(2013, 8, 30, tzinfo=UTC)
    assert change["date_source"] == "url"
    assert change["level_new"] == "archive"
    assert change["tender_id"] == 201
    assert change["tender_status_new"] == "closed"


def test_incident_item_66855_undated_evergreen_gets_jan_1_of_its_only_year() -> None:
    row = _row(
        id=66855,
        url="https://www.defenseindustrydaily.com/atp-se-litening-strikes-as-usaf-splits-future-targeting-pod-orders-06614/",
        title="ATP-SE: LITENING Targeting Pods Now Feature 'Gen-5'",
        clean_text="Work on an RFP that could result in a new competitive landscape for targeting pods began in April 2008.",
    )
    change = _by_id(repair.compute_changes([row], now=NOW))[66855]
    assert change["published_at_new"] == datetime(2008, 1, 1, tzinfo=UTC)
    assert change["date_source"] == "text_year"
    # a non-tender item is never level-archived or tender-closed by this repair
    assert "level_new" not in change
    assert "tender_status_new" not in change


def test_undated_item_mentioning_the_current_year_is_left_alone() -> None:
    row = _row(clean_text="Awarded in 2008 and extended in 2026.")
    assert repair.compute_changes([row], now=NOW) == []


def test_url_date_fixes_a_suspiciously_later_published_at_but_not_a_close_one() -> None:
    later = _row(
        id=2, url="https://example.com/2025/12/story-slug/", published_at=datetime(2026, 9, 22, tzinfo=UTC)
    )
    close = _row(
        id=3, url="https://example.com/2026/09/story-slug/", published_at=datetime(2026, 9, 10, tzinfo=UTC)
    )
    changes = _by_id(repair.compute_changes([later, close], now=NOW))
    assert changes[2]["published_at_new"] == datetime(2025, 12, 1, tzinfo=UTC)
    assert 3 not in changes


def test_dated_item_is_never_aged_by_its_text_year() -> None:
    row = _row(published_at=datetime(2026, 9, 20, tzinfo=UTC), clean_text="A look back at 2008.")
    assert repair.compute_changes([row], now=NOW) == []


def test_old_tender_with_existing_date_is_archived_but_awarded_tender_status_is_kept() -> None:
    row = _row(
        id=5,
        report_kind="tender",
        published_at=datetime(2024, 1, 5, tzinfo=UTC),
        level="orange",
        tender_id=9,
        tender_status="awarded",
    )
    change = _by_id(repair.compute_changes([row], now=NOW))[5]
    assert change["level_new"] == "archive"
    assert "tender_status_new" not in change  # an awarded tender is a deliberate terminal status


def test_recent_tender_is_untouched() -> None:
    row = _row(
        id=6,
        report_kind="tender",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        tender_id=9,
        tender_status="open",
    )
    assert repair.compute_changes([row], now=NOW) == []


def test_already_archived_old_tender_only_closes_the_tender_row() -> None:
    row = _row(
        id=7,
        report_kind="tender",
        published_at=datetime(2019, 1, 1, tzinfo=UTC),
        level="archive",
        tender_id=11,
        tender_status="open",
    )
    change = _by_id(repair.compute_changes([row], now=NOW))[7]
    assert "level_new" not in change
    assert change["tender_status_new"] == "closed"
