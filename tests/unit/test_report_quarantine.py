import datetime as dt

from scripts.quarantine_report_artifacts import missing_citations, plan

from eoa.api import services
from eoa.report.artifacts import VISIBLE_REPORT_SQL, versioned_paths


def test_missing_citation_targets_are_not_validated_by_other_sources():
    assert missing_citations('<a href="#src-2">[2]</a><tr id="src-1"></tr>') == ["src-2"]
    assert missing_citations('<a href="#src-2">[2]</a><tr id="src-2"></tr>') == []


def test_same_day_builds_cannot_overwrite_each_other(tmp_path):
    original = tuple(tmp_path / f"daily_2026-09-13.{ext}" for ext in ("docx", "md", "html"))
    first, second = versioned_paths(*original), versioned_paths(*original)
    assert not set(first) & set(second)
    assert len({p.parent for p in first}) == 1
    for paths, content in ((first, "first"), (second, "second")):
        paths[0].parent.mkdir(parents=True)
        for path in paths:
            path.write_text(content)
    assert all(p.read_text() == "first" for p in first)


def test_quarantine_retains_identity_and_distinguishes_missing_files(tmp_path):
    paths = {}
    for ext in ("docx", "md", "html"):
        path = tmp_path / f"report.{ext}"
        path.write_text("valid content")
        paths[f"path_{ext}"] = str(path)
    base = {**paths, "created_at": dt.datetime(2026, 9, 13), "qa_passed": True}
    rows = [
        {**base, "id": 1},
        {**base, "id": 2},
        {**base, "id": 3, "path_md": None, "path_docx": None, "path_html": None},
        {**base, "id": 4, "path_md": None, "path_docx": None, "path_html": None},
    ]
    archived, kept = plan(rows)
    assert archived[1] == {"reason": "artifact_overwritten", "superseded_by": 2}
    assert archived[3] == archived[4] == {"reason": "missing_artifact"}
    assert [r["id"] for r in kept] == [2]
    assert rows[0]["path_md"] == paths["path_md"]  # planning never mutates evidence


def test_failed_quality_is_not_promoted(tmp_path):
    row = {"id": 9, "created_at": dt.datetime(2026, 9, 13), "qa_passed": False}
    archived, kept = plan([row])
    assert archived == {9: {"reason": "failed_quality_check"}}
    assert kept == []


def test_report_lists_exclude_quarantine_in_sql(monkeypatch):
    seen = []
    monkeypatch.setattr(services, "_fetchall", lambda sql, params: seen.append(sql) or [])
    services.list_reports(limit=200)
    services.list_bd_reports()
    services.list_product_line_reports("mws_eo")
    assert len(seen) == 3
    assert all(VISIBLE_REPORT_SQL in sql for sql in seen)


def test_distinct_products_remain_distinct_report_families():
    for kind in ("product_line", "product_dossier"):
        a = {"kind": kind, "territory": "alpha"}
        b = {"kind": kind, "territory": "beta"}
        assert services._report_group_key(a, None) != services._report_group_key(b, None)
        sql, params = services._report_group_where(a)
        assert "territory" in sql and params == {"kind": kind, "territory": "alpha"}


def test_archive_detail_never_reads_original_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "wrong.html"
    artifact.write_text("WRONG CONTENT")
    row = {
        "id": 1,
        "kind": "daily",
        "qa_report": {"archive": {"reason": "artifact_overwritten"}},
        "path_md": str(artifact),
        "path_html": str(artifact),
        "created_at": dt.datetime(2026, 9, 13),
    }
    monkeypatch.setattr(services, "_fetchone", lambda *args: row)
    monkeypatch.setattr(services, "_fetchall", lambda *args: [])
    card = services.get_report(1)
    assert "הסגר" in card["html"]
    assert card["path_md"] is None
    assert card["preview_he"] is None
    assert not card["qa_passed"] and not card["is_latest"]
    assert "WRONG CONTENT" not in card["html"]
