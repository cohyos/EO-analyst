"""Unit tests for `eoa.export.obsidian` (FR-6.5).

No DB, no AGE: every function that would otherwise touch `eoa.db.connection()`
or `eoa.memory.graph` is monkeypatched at the `eoa.export.obsidian` module
level, mirroring `tests/unit/test_persist_analysis.py`'s stubbing style.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_obsidian_export.py -q``
"""

from __future__ import annotations

import datetime as dt
import sys
import types

import pytest

# Stub out eoa.db if not importable yet (no psycopg/pool available), so this
# module's `from eoa.db import connection` doesn't fail at import time.
if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.export.obsidian import (
    ExportStats,
    _atomic_write,
    _entity_neighbor_lines,
    _entity_timeline_lines,
    _item_link,
    _item_stub,
    _levels_at_or_above,
    render_daily_md,
    render_entity_md,
    render_item_md,
    render_report_md,
    slugify,
)

# --------------------------------------------------------------------------
# slugify
# --------------------------------------------------------------------------


class TestSlugify:
    def test_plain_ascii(self) -> None:
        assert slugify("Elbit Systems") == "Elbit Systems"

    def test_hebrew_preserved(self) -> None:
        assert slugify("רפאל מערכות לחימה") == "רפאל מערכות לחימה"

    def test_strips_unsafe_chars(self) -> None:
        assert slugify('a/b\\c:d*e?f"g<h>i|j') == "a b c d e f g h i j"

    def test_collapses_whitespace(self) -> None:
        assert slugify("a    b\t\tc") == "a b c"

    def test_strips_trailing_dot_and_space(self) -> None:
        assert slugify("Report v2. ") == "Report v2"

    def test_empty_input_becomes_untitled(self) -> None:
        assert slugify("") == "untitled"
        assert slugify(None) == "untitled"  # type: ignore[arg-type]

    def test_only_unsafe_chars_becomes_untitled(self) -> None:
        assert slugify("///:::") == "untitled"

    def test_max_len_truncation(self) -> None:
        long_name = "א" * 200
        result = slugify(long_name, max_len=80)
        assert len(result) <= 80

    def test_reserved_windows_device_name(self) -> None:
        assert slugify("CON") == "_CON"
        assert slugify("con") == "_con"
        assert slugify("COM1") == "_COM1"
        assert slugify("LPT9") == "_LPT9"

    def test_non_reserved_name_untouched(self) -> None:
        assert slugify("CONTRACT") == "CONTRACT"


# --------------------------------------------------------------------------
# atomic write
# --------------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_content(self, tmp_path) -> None:
        target = tmp_path / "vault" / "Entities" / "Elbit.md"
        _atomic_write(target, "---\nkind: company\n---\n\n# Elbit\n")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "---\nkind: company\n---\n\n# Elbit\n"

    def test_creates_parent_dirs(self, tmp_path) -> None:
        target = tmp_path / "a" / "b" / "c" / "note.md"
        _atomic_write(target, "hello")
        assert target.exists()

    def test_overwrite_is_idempotent(self, tmp_path) -> None:
        target = tmp_path / "note.md"
        _atomic_write(target, "version 1")
        _atomic_write(target, "version 2")
        assert target.read_text(encoding="utf-8") == "version 2"

    def test_no_leftover_temp_files(self, tmp_path) -> None:
        target = tmp_path / "note.md"
        _atomic_write(target, "content")
        leftovers = list(tmp_path.glob(".tmp_export_*"))
        assert leftovers == []

    def test_hebrew_content_round_trips(self, tmp_path) -> None:
        target = tmp_path / "עברית.md"
        _atomic_write(target, "תוכן בעברית")
        assert target.read_text(encoding="utf-8") == "תוכן בעברית"


# --------------------------------------------------------------------------
# level ordering
# --------------------------------------------------------------------------


class TestLevelsAtOrAbove:
    def test_yellow_includes_red_orange_yellow(self) -> None:
        assert _levels_at_or_above("yellow") == ["red", "orange", "yellow"]

    def test_red_is_red_only(self) -> None:
        assert _levels_at_or_above("red") == ["red"]

    def test_archive_includes_everything(self) -> None:
        assert _levels_at_or_above("archive") == ["red", "orange", "yellow", "archive"]

    def test_unknown_level_raises(self) -> None:
        from eoa.errors import ConfigError

        with pytest.raises(ConfigError, match="min_level"):
            _levels_at_or_above("purple")


# --------------------------------------------------------------------------
# item page rendering
# --------------------------------------------------------------------------


class TestRenderItemMd:
    def test_full_item(self) -> None:
        item = {
            "id": 42,
            "url": "https://example.com/a",
            "title": "אלביט זכתה בחוזה",
            "source_name": "Defense News",
            "published_at": dt.datetime(2026, 8, 30, 10, 0, tzinfo=dt.UTC),
            "domain": "airborne_pods",
            "level": "red",
            "score": 9,
            "summary_he": "תקציר האירוע.",
            "so_what_he": "השלכה עסקית.",
            "key_facts": ["עובדה א", "עובדה ב"],
            "uncertainty_he": "לא ברור היקף החוזה.",
            "entities_mentioned": ["Elbit Systems", "IAI"],
        }
        md = render_item_md(item)

        assert "url: https://example.com/a" in md
        assert "source: Defense News" in md
        assert "level: red" in md
        assert "score: 9" in md
        assert "# אלביט זכתה בחוזה" in md
        assert "תקציר האירוע." in md
        assert "## למה זה חשוב" in md
        assert "השלכה עסקית." in md
        assert "## עובדות מפתח" in md
        assert "- עובדה א" in md
        assert "- עובדה ב" in md
        assert "## אי-ודאות" in md
        assert "לא ברור היקף החוזה." in md
        assert "[[Entities/Elbit Systems]]" in md
        assert "[[Entities/IAI]]" in md

    def test_minimal_item_no_optional_sections(self) -> None:
        item = {"id": 1, "url": "https://example.com/b", "title": "כותרת"}
        md = render_item_md(item)
        assert "# כותרת" in md
        assert "## למה זה חשוב" not in md
        assert "## עובדות מפתח" not in md
        assert "## אי-ודאות" not in md
        assert "## ישויות" not in md

    def test_item_link_and_stub_use_same_slug(self) -> None:
        assert _item_link(7, "Some Title") == "[[Items/7 Some Title]]"
        assert _item_stub(7, "Some Title") == "7 Some Title"


# --------------------------------------------------------------------------
# entity page rendering: 2 events + 1 neighbor
# --------------------------------------------------------------------------


class TestEntityTimelineAndNeighbors:
    def test_timeline_combines_events_and_mentions_newest_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entity = {"id": 5, "name": "Elbit Systems", "kind": "company", "country": "IL"}

        events = [
            {"item_id": 101, "date": dt.date(2026, 8, 1), "summary_he": "אירוע ישן", "title": "old"},
            {"item_id": 102, "date": dt.date(2026, 9, 1), "summary_he": "אירוע חדש", "title": "new"},
        ]
        monkeypatch.setattr("eoa.export.obsidian.entity_timeline", lambda entity_id: events)
        monkeypatch.setattr("eoa.export.obsidian._items_mentioning_entity", lambda name: [])
        monkeypatch.setattr(
            "eoa.export.obsidian._items_by_ids",
            lambda ids: [{"id": i, "title": f"item-{i}"} for i in ids],
        )

        lines, source_ids = _entity_timeline_lines(entity, {})

        assert len(lines) == 2
        # Newest first: 2026-09-01 (item 102) before 2026-08-01 (item 101).
        assert lines[0].startswith("- 2026-09-01")
        assert "אירוע חדש" in lines[0]
        assert lines[1].startswith("- 2026-08-01")
        assert "אירוע ישן" in lines[1]
        assert source_ids == [101, 102]

    def test_neighbors_rendered_with_label(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entity = {"id": 5, "name": "Elbit Systems", "kind": "company"}

        def fake_neighbors(entity_id: int, label: str | None = None, depth: int = 1):
            if label == "PARTNER_OF":
                return [{"entity_id": 9, "name": "IAI", "kind": "company"}]
            return []

        monkeypatch.setattr("eoa.export.obsidian.neighbors", fake_neighbors)

        lines = _entity_neighbor_lines(entity)

        assert len(lines) == 1
        assert "[[Entities/IAI]]" in lines[0]
        assert "PARTNER_OF" in lines[0]

    def test_full_entity_page_with_2_events_and_1_neighbor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entity = {
            "id": 5,
            "name": "Elbit Systems",
            "kind": "company",
            "country": "IL",
            "aliases": ["Elbit"],
            "focus": ["EO/IR"],
        }

        events = [
            {"item_id": 101, "date": dt.date(2026, 8, 1), "summary_he": "אירוע ישן"},
            {"item_id": 102, "date": dt.date(2026, 9, 1), "summary_he": "אירוע חדש"},
        ]
        monkeypatch.setattr("eoa.export.obsidian.entity_timeline", lambda entity_id: events)
        monkeypatch.setattr("eoa.export.obsidian._items_mentioning_entity", lambda name: [])
        monkeypatch.setattr(
            "eoa.export.obsidian._items_by_ids",
            lambda ids: [{"id": i, "title": f"item-{i}"} for i in ids],
        )

        def fake_neighbors(entity_id: int, label: str | None = None, depth: int = 1):
            if label == "PARTNER_OF":
                return [{"entity_id": 9, "name": "IAI", "kind": "company"}]
            return []

        monkeypatch.setattr("eoa.export.obsidian.neighbors", fake_neighbors)

        id_title_map: dict[int, str] = {}
        timeline_lines, source_ids = _entity_timeline_lines(entity, id_title_map)
        neighbor_lines = _entity_neighbor_lines(entity)

        md = render_entity_md(entity, timeline_lines, neighbor_lines, source_ids, id_title_map)

        assert "kind: company" in md
        assert "country: IL" in md
        assert "# Elbit Systems" in md
        assert "## ציר זמן" in md
        assert md.count("- 2026-") == 2  # both events rendered
        assert "## קשרים" in md
        assert "[[Entities/IAI]] — PARTNER_OF" in md
        assert "## מקורות" in md
        assert "[[Items/101 item-101]]" in md
        assert "[[Items/102 item-102]]" in md

    def test_no_events_no_neighbors_renders_placeholders(self) -> None:
        entity = {"id": 1, "name": "Empty Co", "kind": "company"}
        md = render_entity_md(entity, [], [], [], {})
        assert "_אין אירועים ידועים._" in md
        assert "_אין קשרים ידועים בגרף._" in md
        assert "_אין מקורות ידועים._" in md


# --------------------------------------------------------------------------
# report / daily rendering (light coverage)
# --------------------------------------------------------------------------


class TestRenderReportAndDaily:
    def test_report_without_md_file_uses_placeholder(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("eoa.export.obsidian.REPO_ROOT", tmp_path)
        report = {
            "kind": "daily",
            "period_start": dt.date(2026, 9, 4),
            "period_end": dt.date(2026, 9, 4),
            "path_md": "output/reports/does_not_exist.md",
            "path_docx": "output/reports/does_not_exist.docx",
        }
        md = render_report_md(report)
        assert "kind: daily" in md
        assert "_אין תוכן Markdown זמין לדוח זה._" in md
        assert "does_not_exist.docx" in md

    def test_report_reads_existing_md_file(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("eoa.export.obsidian.REPO_ROOT", tmp_path)
        report_dir = tmp_path / "output" / "reports"
        report_dir.mkdir(parents=True)
        (report_dir / "daily_2026-09-04.md").write_text("## תוכן הדוח\nשלום", encoding="utf-8")
        report = {
            "kind": "daily",
            "period_end": dt.date(2026, 9, 4),
            "path_md": "output/reports/daily_2026-09-04.md",
        }
        md = render_report_md(report)
        assert "## תוכן הדוח" in md
        assert "שלום" in md

    def test_daily_md_lists_items_with_level_label(self) -> None:
        items = [
            {"id": 1, "title": "פריט אדום", "level": "red"},
            {"id": 2, "title": "פריט כתום", "level": "orange"},
        ]
        md = render_daily_md(dt.date(2026, 9, 4), items)
        assert md.startswith("# 2026-09-04")
        assert "[[Items/1 פריט אדום]]" in md
        assert "[[Items/2 פריט כתום]]" in md


# --------------------------------------------------------------------------
# ExportStats
# --------------------------------------------------------------------------


def test_export_stats_disabled_short_circuit(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """export_vault() is a documented no-op (zero counts) when export.obsidian.enabled is False."""
    from eoa.export.obsidian import export_vault

    class FakeObsidianCfg:
        enabled = False
        vault_dir = str(tmp_path / "vault")
        entities = True
        items = True
        reports = True
        min_level = "yellow"

    class FakeExportCfg:
        obsidian = FakeObsidianCfg()

    class FakeSettings:
        export = FakeExportCfg()

    monkeypatch.setattr("eoa.export.obsidian.settings", lambda: FakeSettings())

    stats = export_vault()

    assert isinstance(stats, ExportStats)
    assert stats.entities_written == 0
    assert stats.items_written == 0
    assert stats.reports_written == 0
    assert stats.daily_written == 0
    assert not (tmp_path / "vault").exists()
