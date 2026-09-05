"""Unit tests for eoa.fetch.sources_loader: YAML validation and the `enabled` kill switch.

No DB/network access -- writes a temp YAML file and loads it directly.
"""

from __future__ import annotations

from eoa.fetch.sources_loader import Source, load_sources


def _write_sources_yaml(tmp_path, entries: list[dict]) -> str:
    import yaml

    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": entries}), encoding="utf-8")
    return str(path)


class TestSourceEnabledField:
    def test_defaults_to_enabled_true_when_key_absent(self) -> None:
        """Q4-2/Q4-3: every pre-existing entry (no `enabled:` key) must be unaffected."""
        source = Source.model_validate(
            {
                "id": "example",
                "name": "Example",
                "url": "https://example.com/feed",
                "kind": "rss",
                "lang": "en",
                "reliability": 3,
            }
        )
        assert source.enabled is True

    def test_enabled_false_is_respected(self) -> None:
        source = Source.model_validate(
            {
                "id": "example",
                "name": "Example",
                "url": "https://example.com/feed",
                "kind": "rss",
                "lang": "en",
                "reliability": 3,
                "enabled": False,
            }
        )
        assert source.enabled is False

    def test_load_sources_preserves_enabled_flag(self, tmp_path) -> None:
        path = _write_sources_yaml(
            tmp_path,
            [
                {
                    "id": "a",
                    "name": "A",
                    "url": "https://a.example/feed",
                    "kind": "rss",
                    "lang": "en",
                    "reliability": 3,
                },
                {
                    "id": "b",
                    "name": "B",
                    "url": "https://b.example/feed",
                    "kind": "rss",
                    "lang": "en",
                    "reliability": 3,
                    "enabled": False,
                },
            ],
        )
        sources = load_sources(path)
        by_id = {s.id: s for s in sources}
        assert by_id["a"].enabled is True
        assert by_id["b"].enabled is False

    def test_disabled_sources_can_be_filtered_out(self, tmp_path) -> None:
        """Mirrors the filter `run_ingest` applies before upserting/fetching."""
        path = _write_sources_yaml(
            tmp_path,
            [
                {
                    "id": "aviation_week_defense",
                    "name": "Aviation Week",
                    "url": "https://aviationweek.com/rss.xml",
                    "kind": "rss",
                    "lang": "en",
                    "reliability": 5,
                    "enabled": False,
                },
                {
                    "id": "the_war_zone",
                    "name": "TWZ",
                    "url": "https://www.twz.com/feed",
                    "kind": "rss",
                    "lang": "en",
                    "reliability": 4,
                },
            ],
        )
        enabled_ids = [s.id for s in load_sources(path) if s.enabled]
        assert enabled_ids == ["the_war_zone"]
