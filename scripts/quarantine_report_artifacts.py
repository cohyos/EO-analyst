"""Quarantine overwritten/failed report artifacts; dry-run unless --apply.

Keeps report identities, evidence, feedback and foreign keys. Before changing any
row, saves all report metadata and byte-for-byte artifact backups. Valid survivors
receive independent copies so an already-running old worker cannot overwrite them.
Loads only DATABASE_URL from the native runtime file; never runs inference.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
PATH_KEYS = ("path_docx", "path_md", "path_html")


class CitationTargets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.references: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        href = values.get("href") or ""
        if href.startswith("#src-"):
            self.references.add(href[1:])


def missing_citations(text: str) -> list[str]:
    parser = CitationTargets()
    parser.feed(text)
    return sorted(parser.references - parser.ids)


def resolve(value: str) -> Path:
    if value.startswith("/app/"):
        return (ROOT / value[5:]).resolve()
    path = Path(value)
    return (path if path.is_absolute() else ROOT / path).resolve()


def plan(rows: list[dict]) -> tuple[dict[int, dict], list[dict]]:
    groups = defaultdict(list)
    archived = {}
    for row in rows:
        if (row.get("qa_report") or {}).get("archive"):
            continue
        paths = tuple(str(resolve(row[k])).casefold() if row.get(k) else None for k in PATH_KEYS)
        # Never treat unrelated missing artifacts as duplicate versions.
        groups[paths if any(paths) else (row["id"],)].append(row)
    survivors = []
    for group in groups.values():
        ordered = sorted(group, key=lambda r: (r["created_at"], r["id"]))
        latest = ordered[-1]
        for old in ordered[:-1]:
            archived[old["id"]] = {"reason": "artifact_overwritten", "superseded_by": latest["id"]}
        if not latest.get("qa_passed"):
            archived[latest["id"]] = {"reason": "failed_quality_check"}
        elif any(not latest.get(k) or not resolve(latest[k]).is_file() for k in PATH_KEYS):
            archived[latest["id"]] = {"reason": "missing_artifact"}
        else:
            survivors.append(latest)
    return archived, survivors


def verified_copy(source: Path, target: Path) -> str:
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    after = hashlib.sha256(source.read_bytes()).hexdigest()
    copied = hashlib.sha256(target.read_bytes()).hexdigest()
    if before != after or before != copied:
        raise RuntimeError(f"Artifact changed during backup: {source}")
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    values = dict(
        line.split("=", 1)
        for line in (ROOT / "runtime/eoa.env").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    )
    with psycopg.connect(values["DATABASE_URL"].strip(), row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM reports ORDER BY created_at, id").fetchall()
        conn.commit()
        archived, survivors = plan(rows)
        checked = []
        for row in survivors:
            missing = missing_citations(resolve(row["path_html"]).read_text(encoding="utf-8"))
            if missing:
                archived[row["id"]] = {"reason": "broken_source_references", "missing": missing}
            else:
                checked.append(row)
        survivors = checked
        summary = {
            "scanned": len(rows),
            "quarantined": len(archived),
            "retained": len(survivors),
            "reasons": {
                reason: sum(a["reason"] == reason for a in archived.values())
                for reason in sorted({a["reason"] for a in archived.values()})
            },
        }
        print(json.dumps(summary))
        if not args.apply or not archived:
            return
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = ROOT / "runtime/backups" / f"report-quarantine-{stamp}"
        backup.mkdir(parents=True, exist_ok=False)
        (backup / "reports.json").write_text(
            json.dumps(rows, default=str, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {}
        for row in rows:
            for key in PATH_KEYS:
                if row.get(key):
                    source = resolve(row[key])
                    if source.is_file() and str(source) not in manifest:
                        target = backup / "files" / str(row["id"]) / source.name
                        manifest[str(source)] = {
                            "backup": str(target),
                            "sha256": verified_copy(source, target),
                        }
        replacements = {}
        for row in survivors:
            replacements[row["id"]] = {}
            for key in PATH_KEYS:
                source = resolve(row[key])
                saved = Path(manifest[str(source)]["backup"])
                target = ROOT / "output/reports/versions" / f"recovered-{stamp}-{row['id']}" / source.name
                verified_copy(saved, target)
                replacements[row["id"]][key] = str(target)
        (backup / "manifest.json").write_text(
            json.dumps({"files": manifest, "archive": archived, "replacements": replacements}, indent=2),
            encoding="utf-8",
        )
        affected = set(archived) | set(replacements)
        before = {r["id"]: r for r in rows}
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout = '3s'")
            locked = conn.execute(
                "SELECT * FROM reports WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (sorted(affected),)
            ).fetchall()
            if len(locked) != len(affected) or any(r != before[r["id"]] for r in locked):
                raise RuntimeError("Reports changed since audit; aborted without data changes")
            for row in locked:
                rid = row["id"]
                qa = dict(row.get("qa_report") or {})
                if rid in archived:
                    qa["archive"] = {**archived[rid], "at": stamp, "backup": str(backup)}
                    paths = {k: None for k in PATH_KEYS}
                else:
                    paths = replacements[rid]
                conn.execute(
                    "UPDATE reports SET path_docx=%s, path_md=%s, path_html=%s, qa_report=%s WHERE id=%s",
                    (paths["path_docx"], paths["path_md"], paths["path_html"], Jsonb(qa), rid),
                )
        (backup / "applied.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Applied; reversible backup: {backup}")


if __name__ == "__main__":
    main()
