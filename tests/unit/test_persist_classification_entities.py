"""Regression test (ii) for the 2026-09-06 root-cause investigation (docs/qa entity 394
'Israel'/'ישראל' mismatch): ``eoa.pipeline.classify.persist_classification`` must store the
CANONICAL entity name in ``items.entities_mentioned``, not the raw as-extracted one -- otherwise a
Hebrew alias like "ישראל" ends up in ``entities_mentioned`` even though the matching ``entities``
row is correctly canonicalised to "Israel", and anything that re-derives an entity's identity from
``entities_mentioned`` via an exact ``entities.name = ...`` lookup (e.g.
``eoa.pipeline.israel_focus.score_and_persist_entity_israeli``) silently no-ops on that mismatch.

Kept in a standalone file rather than added to an existing classify test module, several of which
were being concurrently edited by other agents at the time of this investigation.
"""

from __future__ import annotations

import eoa.pipeline.classify as classify
import eoa.pipeline.israel_focus as israel_focus
from eoa.llm.schemas.analysis import ClassifyOut, EntityMention


def _classify_out(name: str, kind: str) -> ClassifyOut:
    return ClassifyOut(
        domain="c_uas",
        subdomain="",
        dimensions=[],
        tags=[],
        report_kind="verified_report",
        entities=[EntityMention(name=name, kind=kind)],
        one_line_he="בדיקה",
    )


def _patch_common(monkeypatch, *, upsert_return: int | None, captured_fields: dict) -> list[dict]:
    captured_upsert: list[dict] = []
    monkeypatch.setattr(
        classify,
        "upsert_entity",
        lambda **kw: (captured_upsert.append(kw), upsert_return)[1],
    )
    monkeypatch.setattr(
        classify,
        "update_item_fields",
        lambda item_id, **fields: captured_fields.update(fields),
    )
    # israel_focus.score_and_persist_entity_israeli does real DB I/O (best-effort, swallows its
    # own errors) -- stub it out so this test never depends on a live DB connection.
    monkeypatch.setattr(israel_focus, "score_and_persist_entity_israeli", lambda name: None)
    return captured_upsert


def test_persist_classification_stores_canonical_name_for_hebrew_country_alias(monkeypatch):
    captured_fields: dict = {}
    captured_upsert = _patch_common(monkeypatch, upsert_return=394, captured_fields=captured_fields)

    out = _classify_out(name="ישראל", kind="company")
    classify.persist_classification({"id": 1}, out)

    # upsert_entity still receives the raw name (it canonicalises internally) -- this test is
    # about what persist_classification itself appends to entities_mentioned.
    assert captured_upsert[0]["name"] == "ישראל"
    assert captured_fields["entities_mentioned"] == ["Israel"]


def test_persist_classification_stores_canonical_name_for_watchlist_alias(monkeypatch):
    captured_fields: dict = {}
    _patch_common(monkeypatch, upsert_return=1, captured_fields=captured_fields)

    out = _classify_out(name="Elbit Systems", kind="company")
    classify.persist_classification({"id": 2}, out)

    assert captured_fields["entities_mentioned"] == ["Elbit"]


def test_persist_classification_skips_rejected_junk_entity(monkeypatch):
    captured_fields: dict = {}
    _patch_common(monkeypatch, upsert_return=None, captured_fields=captured_fields)

    out = _classify_out(name="image captioning", kind="system")
    classify.persist_classification({"id": 3}, out)

    assert captured_fields["entities_mentioned"] == []
