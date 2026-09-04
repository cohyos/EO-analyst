"""Unit tests for `eoa.memory.graph._parse_agtype`.

`eoa.memory.graph` imports `eoa.db` (owned by another agent building the
config/db layer concurrently). If that module isn't available yet in this
checkout, we install a minimal stand-in in `sys.modules` before importing, so
this pure-parsing test can run in isolation without a real database layer.
"""

from __future__ import annotations

import sys
import types

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.memory.graph import _parse_agtype


def test_parse_none_returns_none() -> None:
    assert _parse_agtype(None) is None


def test_parse_non_string_passthrough() -> None:
    assert _parse_agtype(5) == 5
    assert _parse_agtype(3.14) == 3.14
    assert _parse_agtype({"already": "parsed"}) == {"already": "parsed"}


def test_parse_plain_scalar_json() -> None:
    assert _parse_agtype("5") == 5
    assert _parse_agtype('"hello"') == "hello"
    assert _parse_agtype("true") is True


def test_parse_vertex_strips_suffix_and_tags_kind() -> None:
    raw = '{"id": 844424930131969, "label": "Entity", "properties": {"entity_id": 5, "name": "RTX"}}::vertex'
    parsed = _parse_agtype(raw)
    assert parsed["_agtype"] == "vertex"
    assert parsed["label"] == "Entity"
    assert parsed["properties"] == {"entity_id": 5, "name": "RTX"}
    assert parsed["id"] == 844424930131969


def test_parse_edge_strips_suffix_and_tags_kind() -> None:
    raw = (
        '{"id": 1, "label": "COMPETITOR_OF", "start_id": 2, "end_id": 3, "properties": {"item_id": 42}}::edge'
    )
    parsed = _parse_agtype(raw)
    assert parsed["_agtype"] == "edge"
    assert parsed["label"] == "COMPETITOR_OF"
    assert parsed["properties"] == {"item_id": 42}


def test_parse_edge_suffix_with_trailing_whitespace() -> None:
    raw = '{"id": 1, "label": "PARTNER_OF"}::edge  \n'
    parsed = _parse_agtype(raw)
    assert parsed["_agtype"] == "edge"


def test_parse_path_suffix() -> None:
    raw = '["a", "b"]::path'
    parsed = _parse_agtype(raw)
    # non-dict values keep their shape; the ::path suffix is stripped but no
    # `_agtype` key can be attached to a non-dict result.
    assert parsed == ["a", "b"]


def test_parse_unparsable_text_returned_unchanged() -> None:
    raw = "not json at all"
    assert _parse_agtype(raw) == raw


def test_parse_does_not_mutate_input_string() -> None:
    raw = '{"a": 1}::vertex'
    parsed = _parse_agtype(raw)
    assert isinstance(parsed, dict)
    assert raw == '{"a": 1}::vertex'
