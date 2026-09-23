"""Unit tests for eoa.fetch.rss — pure parsing, no network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eoa.fetch.rss import FeedEntry, parse_feed

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "feeds" / "sample.xml"


def _load_fixture() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_parse_feed_returns_all_entries_without_since_days() -> None:
    entries = parse_feed(_load_fixture())

    assert len(entries) == 4
    assert all(isinstance(e, FeedEntry) for e in entries)


def test_parse_feed_extracts_expected_fields() -> None:
    entries = parse_feed(_load_fixture())
    first = entries[0]

    assert first.url == "https://example.com/news/ir-targeting-pod"
    assert first.title == "New IR Targeting Pod Enters Service"
    assert first.summary is not None
    assert "electro-optical targeting pod" in first.summary
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None
    assert first.published_at.year == 2026
    assert first.published_at.month == 9
    assert first.published_at.day == 1


def test_parse_feed_falls_back_to_feed_level_language() -> None:
    entries = parse_feed(_load_fixture())
    # <language>en-us</language> at the channel level, no per-item <language>.
    assert entries[0].lang == "en"


def test_parse_feed_since_days_drops_old_entries_but_keeps_undated() -> None:
    reference_now = datetime(2026, 9, 4, tzinfo=UTC)
    entries = parse_feed(_load_fixture(), since_days=3, now=reference_now)

    urls = {e.url for e in entries}
    assert "https://example.com/news/ir-targeting-pod" in urls  # 3 days old, kept
    assert "https://example.com/news/undated-item" in urls  # no date, always kept
    assert "https://example.com/news/counter-uas-fusion" not in urls  # 8 days old, dropped
    assert "https://example.com/news/older-item" not in urls  # months old, dropped
    assert len(entries) == 2


def test_parse_feed_handles_malformed_xml_without_raising() -> None:
    malformed = "<rss><channel><title>Broken<item><link>not-closed"
    entries = parse_feed(malformed)

    # feedparser is tolerant of malformed XML; the call must not raise, and
    # may recover zero or more entries depending on how much it can salvage.
    assert isinstance(entries, list)


def test_parse_feed_handles_empty_input_without_raising() -> None:
    entries = parse_feed("")
    assert entries == []


def test_parse_feed_skips_entries_without_a_resolvable_url() -> None:
    feed_without_link = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>No Links</title>
      <item><title>Has no link or guid-url</title><description>orphan</description></item>
    </channel></rss>"""

    entries = parse_feed(feed_without_link)
    assert entries == []


def test_parse_feed_atom_updated_used_when_published_missing() -> None:
    atom_feed = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Atom Fixture</title>
      <entry>
        <title>Atom Entry</title>
        <link href="https://example.com/atom/entry-1" />
        <id>https://example.com/atom/entry-1</id>
        <updated>2026-08-30T10:00:00Z</updated>
        <summary>Atom entries use &lt;updated&gt; instead of pubDate.</summary>
      </entry>
    </feed>"""

    entries = parse_feed(atom_feed)
    assert len(entries) == 1
    assert entries[0].url == "https://example.com/atom/entry-1"
    assert entries[0].published_at is not None
    assert entries[0].published_at.month == 8
    assert entries[0].published_at.day == 30


def test_struct_time_to_datetime_uses_utc_not_local_mktime(monkeypatch) -> None:
    """F13 (SOL-AUDIT-2026-09-24): `time.mktime` interprets its input as LOCAL wall-clock time --
    applying it to feedparser's already-UTC `*_parsed` struct_time (feedparser normalizes every
    feed's timezone on parse) silently shifted every entry's timestamp by the process's local UTC
    offset (e.g. this deployment's own Asia/Jerusalem) before the result was labelled `tz=UTC`.
    Proven here without depending on the test machine's own timezone (Windows has no
    `time.tzset` to fake one): `time.mktime` is monkeypatched to raise, so this fails loudly if
    the fix (`calendar.timegm`, which treats its input as UTC and never touches local time) ever
    regresses back to `time.mktime`.
    """
    import calendar
    import time as time_mod

    from eoa.fetch.rss import _struct_time_to_datetime

    struct = time_mod.struct_time((2026, 9, 1, 12, 0, 0, 1, 244, 0))
    expected = datetime.fromtimestamp(calendar.timegm(struct), tz=UTC)

    def _mktime_must_not_be_called(_value):
        raise AssertionError("time.mktime must not be used -- it applies the LOCAL UTC offset")

    monkeypatch.setattr(time_mod, "mktime", _mktime_must_not_be_called)

    result = _struct_time_to_datetime(struct)
    assert result == expected
    assert result.hour == 12  # the UTC hour carried straight from the struct_time, unshifted
