"""Round-6 data-repair tests (docs/qa/loop/round_5_judge.md D1/D2/D3/D9).

No DB: every ``eoa.db.connection`` (or ``scripts.repair_round6.connection``) call is monkeypatched
with a fake cursor/connection pair, mirroring ``tests/unit/test_relational_stage_filter.py`` and
``tests/unit/test_so_what_repair_round3.py``.

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_round6_data.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scripts.repair_round6 as repair_mod

from eoa.llm.schemas.analysis import SoWhatRepairOut
from eoa.memory import relational
from eoa.pipeline import analyze as analyze_mod
from eoa.tenders import scan as scan_mod

# ---------------------------------------------------------------------------
# fake DB plumbing (no real connection ever opens)
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, responder, recorder):
        self._responder = responder
        self._recorder = recorder
        self._rows: list = []

    def execute(self, sql, params=None):
        params = params or {}
        self._recorder.append((sql, params))
        self._rows = self._responder(sql, params) or []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, responder, recorder):
        self._responder = responder
        self._recorder = recorder

    def cursor(self, row_factory=None):
        return _FakeCursor(self._responder, self._recorder)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _connection_stub(responder, recorder):
    """Returns a zero-arg callable usable as a drop-in replacement for ``eoa.db.connection`` --
    every call opens a fresh ``_FakeConn`` sharing the same responder/recorder."""

    def _factory():
        return _FakeConn(responder, recorder)

    return _factory


def _sql_router(table_rows: dict[str, list]):
    """Builds a responder that returns ``table_rows[key]`` for the first key whose substring is
    found in the executed SQL (checked in insertion order) -- good enough for these scripts' small,
    single-purpose queries."""

    def _respond(sql, params):
        for key, rows in table_rows.items():
            if key in sql:
                return rows
        return []

    return _respond


# ---------------------------------------------------------------------------
# task 4 -- eoa.memory.relational.get_items_for_stage analyze-only scope filter (D9)
# ---------------------------------------------------------------------------


def test_analyze_stage_excludes_out_of_scope_and_archive(monkeypatch):
    recorder: list = []
    monkeypatch.setattr(
        relational, "connection", _connection_stub(_sql_router({"FROM items": [{"id": 1}]}), recorder)
    )

    relational.get_items_for_stage("analyze", limit=10)

    sql, _params = recorder[-1]
    assert "domain IS DISTINCT FROM 'out_of_scope'" in sql
    assert "level IS DISTINCT FROM 'archive'" in sql


def test_classify_and_triage_stages_are_not_scope_filtered(monkeypatch):
    recorder: list = []
    monkeypatch.setattr(
        relational, "connection", _connection_stub(_sql_router({"FROM items": [{"id": 1}]}), recorder)
    )

    relational.get_items_for_stage("classify", limit=10)
    relational.get_items_for_stage("triage", limit=10)

    for sql, _params in recorder:
        assert "out_of_scope" not in sql
        assert "level IS DISTINCT FROM" not in sql


# ---------------------------------------------------------------------------
# task 1 -- eoa.pipeline.analyze.repair_so_what_text (D2)
# ---------------------------------------------------------------------------

_ITEM = {"id": 42, "title": "t"}


def test_repair_so_what_text_accepts_valid_rewrite(monkeypatch):
    monkeypatch.setattr(
        analyze_mod,
        "chat_structured",
        lambda *a, **k: SoWhatRepairOut(
            so_what_he="להערכתנו, Ophir נכנסת לנישה שבה Controp מובילה; הלקוח ההודי מקבל חלופה זולה."
        ),
    )
    text = analyze_mod.repair_so_what_text(
        _ITEM,
        so_what_he='להערכתנו, ההשקה מחזקת את מעמדה של תע"א.',
        summary_he="חברת X השיקה עדשה חדשה.",
        phrase="מחזקת את מעמד",
    )
    assert text is not None
    assert text.startswith("להערכתנו")


def test_repair_so_what_text_rejects_missing_prefix(monkeypatch):
    monkeypatch.setattr(
        analyze_mod, "chat_structured", lambda *a, **k: SoWhatRepairOut(so_what_he="זה משפט בלי הקידומת.")
    )
    text = analyze_mod.repair_so_what_text(_ITEM, so_what_he="so", summary_he="sum", phrase="phrase")
    assert text is None


def test_repair_so_what_text_rejects_empty(monkeypatch):
    monkeypatch.setattr(analyze_mod, "chat_structured", lambda *a, **k: SoWhatRepairOut(so_what_he="   "))
    text = analyze_mod.repair_so_what_text(_ITEM, so_what_he="so", summary_he="sum", phrase="phrase")
    assert text is None


def test_repair_so_what_text_survives_llm_failure(monkeypatch):
    def boom(*a, **k):
        raise analyze_mod.LLMOutputError("bad json")

    monkeypatch.setattr(analyze_mod, "chat_structured", boom)
    text = analyze_mod.repair_so_what_text(_ITEM, so_what_he="so", summary_he="sum", phrase="phrase")
    assert text is None


# ---------------------------------------------------------------------------
# task 1 -- scripts.repair_round6 so_what detection + validation
# ---------------------------------------------------------------------------


def test_find_banned_so_what_items_matches_broad_phrase_list(monkeypatch):
    rows = [
        {"id": 1, "so_what_he": "להערכתנו, זה מהווה צעד נוסף בהתבססות החברה.", "summary_he": "s"},
        {"id": 2, "so_what_he": "להערכתנו, Ophir תיקח נתח שוק מ-Controp.", "summary_he": "s"},
        {"id": 3, "so_what_he": "להערכתנו, זה מעיד על מגמה רחבה יותר בענף.", "summary_he": "s"},
    ]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )

    matched = repair_mod.find_banned_so_what_items()

    assert sorted(r["id"] for r in matched) == [1, 3]


def test_repair_so_what_dry_run_does_not_call_llm_or_write(monkeypatch):
    rows = [{"id": 1, "so_what_he": "להערכתנו, מהווה צעד נוסף.", "summary_he": "s"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )
    monkeypatch.setattr(
        repair_mod,
        "repair_so_what_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")),
    )
    monkeypatch.setattr(
        repair_mod,
        "update_item_fields",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not write")),
    )

    report = repair_mod.repair_so_what(apply=False, budget=repair_mod.LLMBudget(25))

    assert report["matched_ids"] == [1]
    assert report["repaired"] == []


def test_repair_so_what_apply_rejects_still_generic_rewrite(monkeypatch):
    rows = [{"id": 1, "so_what_he": "להערכתנו, מהווה צעד נוסף.", "summary_he": "s"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "title": "t"})
    monkeypatch.setattr(
        repair_mod, "repair_so_what_text", lambda *a, **k: "להערכתנו, זה עדיין מהווה צעד נוסף."
    )
    calls = []
    monkeypatch.setattr(repair_mod, "update_item_fields", lambda *a, **k: calls.append((a, k)))

    report = repair_mod.repair_so_what(apply=True, budget=repair_mod.LLMBudget(25))

    assert report["repaired"] == []
    assert report["rejected"][0]["reason"] == "still_generic"
    assert calls == []


def test_repair_so_what_apply_rejects_too_many_sentences(monkeypatch):
    rows = [{"id": 1, "so_what_he": "להערכתנו, מהווה צעד נוסף.", "summary_he": "s"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "title": "t"})
    four_sentences = "להערכתנו, א. ב. ג. ד."
    monkeypatch.setattr(repair_mod, "repair_so_what_text", lambda *a, **k: four_sentences)
    calls = []
    monkeypatch.setattr(repair_mod, "update_item_fields", lambda *a, **k: calls.append((a, k)))

    report = repair_mod.repair_so_what(apply=True, budget=repair_mod.LLMBudget(25))

    assert report["repaired"] == []
    assert report["rejected"][0]["reason"].startswith("sentence_count=")
    assert calls == []


def test_repair_so_what_apply_writes_valid_rewrite(monkeypatch):
    rows = [{"id": 1, "so_what_he": "להערכתנו, מהווה צעד נוסף.", "summary_he": "s"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "title": "t"})
    good_text = "להערכתנו, Ophir נכנסת לנישה חדשה; הלקוח מקבל חלופה זולה יותר."
    monkeypatch.setattr(repair_mod, "repair_so_what_text", lambda *a, **k: good_text)
    calls = []
    monkeypatch.setattr(repair_mod, "update_item_fields", lambda *a, **k: calls.append((a, k)))

    report = repair_mod.repair_so_what(apply=True, budget=repair_mod.LLMBudget(25))

    assert report["repaired"] == [{"id": 1, "phrase": "מהווה צעד נוסף", "so_what_he": good_text}]
    assert calls == [((1,), {"so_what_he": good_text})]


def test_repair_so_what_respects_llm_budget(monkeypatch):
    rows = [
        {"id": 1, "so_what_he": "להערכתנו, מהווה צעד נוסף.", "summary_he": "s"},
        {"id": 2, "so_what_he": "להערכתנו, מהווה צעד חשוב.", "summary_he": "s"},
    ]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM items": rows}), recorder)
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "title": "t"})
    monkeypatch.setattr(repair_mod, "repair_so_what_text", lambda *a, **k: "להערכתנו, טקסט תקין לגמרי כאן.")
    monkeypatch.setattr(repair_mod, "update_item_fields", lambda *a, **k: None)

    budget = repair_mod.LLMBudget(1)
    report = repair_mod.repair_so_what(apply=True, budget=budget)

    assert len(report["repaired"]) == 1
    assert report["skipped_budget"] == [2]
    assert budget.used == 1


# ---------------------------------------------------------------------------
# task 2 -- scripts.repair_round6 entities_mentioned events/graph_edges fallback (D3)
# ---------------------------------------------------------------------------


def test_entities_from_events_unions_parties_and_edge_endpoints(monkeypatch):
    recorder: list = []

    def _respond(sql, params):
        if "FROM events" in sql:
            return [{"parties": ["TC-Next", "GraphCast"]}, {"parties": ["TC-Next", "WeatherNext"]}]
        if "FROM graph_edges" in sql:
            return [{"src_name": "AIM-120 AMRAAM", "dst_name": "NASAMS"}]
        return []

    monkeypatch.setattr(repair_mod, "connection", _connection_stub(_respond, recorder))

    names = repair_mod._entities_from_events(22)

    assert names == ["TC-Next", "GraphCast", "WeatherNext", "AIM-120 AMRAAM", "NASAMS"]


def test_entities_from_events_empty_when_nothing_recorded(monkeypatch):
    recorder: list = []
    monkeypatch.setattr(repair_mod, "connection", _connection_stub(lambda sql, params: [], recorder))

    assert repair_mod._entities_from_events(999) == []


def test_repair_entities_apply_falls_back_to_events_when_reanalysis_stays_empty(monkeypatch):
    monkeypatch.setattr(
        repair_mod, "find_empty_entities_items", lambda cap: {"mandatory": 22, "other_targets": []}
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "entities_mentioned": []})
    monkeypatch.setattr(repair_mod, "analyze_item", lambda item, **k: object())
    monkeypatch.setattr(repair_mod, "persist_analysis", lambda item, out: None)
    monkeypatch.setattr(repair_mod, "_entities_from_events", lambda item_id: ["Ukraine", "Russia"])
    calls = []
    monkeypatch.setattr(repair_mod, "update_item_fields", lambda *a, **k: calls.append((a, k)))

    report = repair_mod.repair_entities(apply=True, budget=repair_mod.LLMBudget(25))

    assert report["repaired"] == [{"id": 22, "entities_mentioned": ["Ukraine", "Russia"]}]
    assert calls == [((22,), {"entities_mentioned": ["Ukraine", "Russia"]})]


def test_repair_entities_apply_rejected_when_events_fallback_also_empty(monkeypatch):
    monkeypatch.setattr(
        repair_mod, "find_empty_entities_items", lambda cap: {"mandatory": 22, "other_targets": []}
    )
    monkeypatch.setattr(repair_mod, "_fetch_item", lambda item_id: {"id": item_id, "entities_mentioned": []})
    monkeypatch.setattr(repair_mod, "analyze_item", lambda item, **k: object())
    monkeypatch.setattr(repair_mod, "persist_analysis", lambda item, out: None)
    monkeypatch.setattr(repair_mod, "_entities_from_events", lambda item_id: [])
    monkeypatch.setattr(
        repair_mod, "update_item_fields", lambda *a, **k: (_ for _ in ()).throw(AssertionError)
    )

    report = repair_mod.repair_entities(apply=True, budget=repair_mod.LLMBudget(25))

    assert report["rejected"] == [{"id": 22, "reason": "still_empty_after_reanalysis"}]


# ---------------------------------------------------------------------------
# task 3 -- scripts.repair_round6._consistency_snapshot (D1)
# ---------------------------------------------------------------------------


def test_consistency_snapshot_flags_item_5604_shape():
    # Real shape from the live DB (docs brief): score=6, level='archive', reason_he never states
    # an explicit "level:" conclusion -- so no textual conflict, but level_for(6) == 'orange' !=
    # the stored 'archive'.
    item = {
        "score": 6,
        "level": "archive",
        "triage_reason": (
            "הפריט עוסק במערכת EO/IR טקטית חדשה (core_relevance=5) שהיא חלק מ-RFI משמעותי "
            "(magnitude=3) אך אינה חידוש טכנולוגי או זכייה ראשונה מסוגה (novelty=3)."
        ),
    }
    snap = repair_mod._consistency_snapshot(item)
    assert snap["expected_level_from_score"] == "orange"
    assert snap["reason_conflicting_level"] is None
    assert snap["consistent"] is False


def test_consistency_snapshot_passes_when_aligned():
    item = {"score": 6, "level": "orange", "triage_reason": "נימוק ללא מסקנת רמה מפורשת."}
    snap = repair_mod._consistency_snapshot(item)
    assert snap["consistent"] is True


def test_consistency_snapshot_neutral_when_not_yet_triaged():
    item = {"score": None, "level": None, "triage_reason": None}
    snap = repair_mod._consistency_snapshot(item)
    assert snap["consistent"] is False
    assert snap["expected_level_from_score"] is None


# ---------------------------------------------------------------------------
# task 4 -- scripts.repair_round6 out-of-scope event listing/deletion (D9)
# ---------------------------------------------------------------------------


def test_find_out_of_scope_events(monkeypatch):
    rows = [{"id": 257, "item_id": 2463, "kind": "regulation", "title": "סגירת מחסן כימי אומתילה"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM events": rows}), recorder)
    )

    found = repair_mod.find_out_of_scope_events()

    assert found == rows


def test_repair_events_apply_deletes_found_ids(monkeypatch):
    rows = [{"id": 257, "item_id": 2463, "kind": "regulation", "title": "t"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM events": rows}), recorder)
    )

    report = repair_mod.repair_events(apply=True)

    assert report["deleted_count"] == 1
    delete_sql, delete_params = recorder[-1]
    assert "DELETE FROM events" in delete_sql
    assert delete_params["ids"] == [257]


def test_repair_events_dry_run_does_not_delete(monkeypatch):
    rows = [{"id": 257, "item_id": 2463, "kind": "regulation", "title": "t"}]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM events": rows}), recorder)
    )

    repair_mod.repair_events(apply=False)

    assert all("DELETE" not in sql for sql, _ in recorder)


# ---------------------------------------------------------------------------
# task 5 -- eoa.tenders.scan candidate dedupe helpers (D9)
# ---------------------------------------------------------------------------


def test_normalize_tender_title_collapses_whitespace_and_casefolds():
    assert (
        scan_mod._normalize_tender_title("  Electro   Optical  and Infrared Sensors | Northrop Grumman ")
        == "electro optical and infrared sensors | northrop grumman"
    )


def test_notice_portal_extracts_lowercased_host():
    url = "https://www.NorthropGrumman.com/what-we-do/mission-solutions/eo-ir"
    assert scan_mod._notice_portal(url) == "www.northropgrumman.com"


def test_notice_portal_empty_for_missing_url():
    assert scan_mod._notice_portal(None) == ""
    assert scan_mod._notice_portal("") == ""


def test_candidate_duplicate_exists_true_for_same_title_and_portal(monkeypatch):
    recorder: list = []
    monkeypatch.setattr(
        scan_mod,
        "connection",
        _connection_stub(
            _sql_router(
                {
                    "FROM tenders": [
                        {"url": "https://www.northropgrumman.com/what-we-do/mission-solutions/eo-ir"}
                    ]
                }
            ),
            recorder,
        ),
    )
    exists = scan_mod._candidate_duplicate_exists(
        "electro optical and infrared sensors | northrop grumman", "www.northropgrumman.com"
    )
    assert exists is True


def test_candidate_duplicate_exists_false_for_different_portal(monkeypatch):
    recorder: list = []
    monkeypatch.setattr(
        scan_mod,
        "connection",
        _connection_stub(
            _sql_router({"FROM tenders": [{"url": "https://www.example.com/some-other-page"}]}),
            recorder,
        ),
    )
    exists = scan_mod._candidate_duplicate_exists("same title", "www.northropgrumman.com")
    assert exists is False


def test_candidate_duplicate_exists_short_circuits_without_portal():
    assert scan_mod._candidate_duplicate_exists("some title", "") is False
    assert scan_mod._candidate_duplicate_exists("", "www.example.com") is False


def test_candidate_duplicate_exists_reads_dict_rows_and_skips_null_urls(monkeypatch):
    """Regression for run_errors 279 (nightly 2026-09-08 01:31): the pool yields ``dict_row``
    rows, and the helper used to index them with ``r[0]`` -> ``KeyError: 0``, which crashed the
    whole tenders stage whenever a candidate title collided with an existing row. Rows are
    keyed by column name; a NULL url is skipped rather than blowing up in ``_notice_portal``."""
    recorder: list = []
    monkeypatch.setattr(
        scan_mod,
        "connection",
        _connection_stub(
            _sql_router(
                {
                    "FROM tenders": [
                        {"url": None},
                        {"url": "https://www.example.com/unrelated"},
                        {"url": "https://WWW.NorthropGrumman.com/another-page"},
                    ]
                }
            ),
            recorder,
        ),
    )
    assert scan_mod._candidate_duplicate_exists("same title", "www.northropgrumman.com") is True
    sql, params = recorder[-1]
    assert "intake = 'candidate'" in sql
    assert params == {"t": "same title"}


# ---------------------------------------------------------------------------
# task 5 -- scripts.repair_round6 tender dedupe grouping (D9)
# ---------------------------------------------------------------------------


def _tender_row(id_, title, url, status="unknown"):
    return {
        "id": id_,
        "title": title,
        "url": url,
        "status": status,
        "intake": "candidate",
        "created_at": None,
    }


def test_find_duplicate_tenders_groups_by_title_and_portal(monkeypatch):
    ng_url = "https://www.northropgrumman.com/what-we-do/mission-solutions/eo-ir"
    ng_title = "Electro Optical and Infrared Sensors | Northrop Grumman"
    uas_url = "https://www.unmannedairspace.info/category/counter-uas-systems-tenders/"
    uas_title = "Counter UAS systems tenders - Unmanned airspace"
    rows = [
        _tender_row(34, "Expert / Coach Transformatie", "https://www.tenderned.nl/x", status="open"),
        _tender_row(35, ng_title, ng_url),
        _tender_row(36, ng_title, ng_url),
        _tender_row(37, ng_title, ng_url),
        _tender_row(38, uas_title, uas_url),
        _tender_row(39, ng_title, ng_url),
        _tender_row(40, ng_title, ng_url),
        _tender_row(41, uas_title, uas_url),
    ]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM tenders": rows}), recorder)
    )

    duplicates = repair_mod.find_duplicate_tenders()

    ng_deletes = sorted(d["delete_id"] for d in duplicates if d["portal"] == "www.northropgrumman.com")
    uas_deletes = sorted(d["delete_id"] for d in duplicates if d["portal"] == "www.unmannedairspace.info")
    assert ng_deletes == [36, 37, 39, 40]
    assert uas_deletes == [41]
    assert all(d["keep_id"] == 35 for d in duplicates if d["portal"] == "www.northropgrumman.com")
    assert all(d["keep_id"] == 38 for d in duplicates if d["portal"] == "www.unmannedairspace.info")
    # id 34 (unique title/portal) never appears as a duplicate.
    assert all(d["delete_id"] != 34 and d["keep_id"] != 34 for d in duplicates)


def test_repair_tenders_apply_only_deletes_candidate_intake(monkeypatch):
    ng_url = "https://www.northropgrumman.com/eo-ir"
    ng_title = "Electro Optical and Infrared Sensors | Northrop Grumman"
    rows = [_tender_row(35, ng_title, ng_url), _tender_row(36, ng_title, ng_url)]
    recorder: list = []
    monkeypatch.setattr(
        repair_mod, "connection", _connection_stub(_sql_router({"FROM tenders": rows}), recorder)
    )

    report = repair_mod.repair_tenders(apply=True)

    assert report["deleted_count"] == 1
    delete_sql, delete_params = recorder[-1]
    assert "DELETE FROM tenders" in delete_sql
    assert "intake = 'candidate'" in delete_sql
    assert delete_params["ids"] == [36]


# ---------------------------------------------------------------------------
# LLMBudget
# ---------------------------------------------------------------------------


def test_llm_budget_exhausts_at_limit():
    budget = repair_mod.LLMBudget(2)
    assert not budget.exhausted
    budget.use()
    assert not budget.exhausted
    budget.use()
    assert budget.exhausted
