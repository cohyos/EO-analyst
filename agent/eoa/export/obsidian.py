"""FR-6.5: export the relational/graph memory into a plain-Markdown Obsidian vault.

Entry point: :func:`export_vault`. Reads `config.export.obsidian` (see
`eoa.config.ObsidianExportCfg`) for the vault path and per-section toggles.

Layout written under `vault_dir`:

- `Entities/<Name>.md` -- one note per `entities` row: YAML frontmatter
  (kind/country/aliases/focus/tags), then "ציר זמן" (events from
  `eoa.memory.graph.entity_timeline` plus items whose `entities_mentioned`
  names the entity, newest first), "קשרים" (neighbor discovery from
  `eoa.memory.graph.neighbors`, one call per edge label, with the real
  evidencing item linked per edge via `eoa.memory.graph.edges_of`),
  "מקורות" (every item referenced above).
- `Items/<id> <slug>.md` -- one note per exported item: frontmatter
  (url/source/published_at/domain/level/score/entities), then
  `summary_he`, `so_what_he`, `key_facts`, `uncertainty_he`, and wikilinks
  to mentioned entities.
- `Reports/<kind>_<date>.md` -- a copy of `reports.path_md`'s content (if the
  file exists on disk) plus a pointer to `reports.path_docx`.
- `Daily/<date>.md` -- a MOC (map of content) listing that day's red/orange
  items, always independent of `min_level`.
- `_index.md` -- counts and a short summary of the run.

All writes are atomic (temp file + `os.replace`) and idempotent: re-running
overwrites the same paths by their deterministic filenames, and nothing
under `vault_dir` is ever deleted, so files a user adds by hand in Obsidian
survive re-export. Cross-links are plain `[[wikilinks]]` -- Obsidian derives
backlinks and the graph view itself; this module never maintains its own
backlink index.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.errors import ConfigError
from eoa.memory.graph import EDGE_LABELS, edges_of, entity_timeline, neighbors

log = structlog.get_logger(__name__)

LEVEL_ORDER: tuple[str, ...] = ("red", "orange", "yellow", "archive")

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}


class ExportStats(BaseModel):
    """Summary of one `export_vault()` run."""

    vault_dir: str
    since_days: int | None
    min_level: str
    entities_written: int = 0
    items_written: int = 0
    reports_written: int = 0
    daily_written: int = 0
    started_at: dt.datetime
    finished_at: dt.datetime


# --------------------------------------------------------------------------
# filenames / links
# --------------------------------------------------------------------------


def slugify(name: str, *, max_len: int = 80) -> str:
    """Turn `name` into a Windows-safe filename fragment (Hebrew/Unicode preserved).

    Strips ``/\\:*?"<>|``, collapses whitespace, trims trailing dots/spaces
    (illegal at the end of a Windows filename), dodges the reserved DOS
    device names (`CON`, `NUL`, `COM1`, ...), and truncates to `max_len`
    characters.
    """
    cleaned = _UNSAFE_FILENAME_RE.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(". ")
    if not cleaned:
        cleaned = "untitled"
    if cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _item_stub(item_id: int, title: str | None) -> str:
    return f"{item_id} {slugify(title or 'untitled', max_len=70)}"


def _item_link(item_id: int, title: str | None) -> str:
    return f"[[Items/{_item_stub(item_id, title)}]]"


def _entity_link(name: str) -> str:
    return f"[[Entities/{slugify(name)}]]"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return str(value)


def _frontmatter(data: dict[str, Any]) -> str:
    clean = {k: v for k, v in data.items() if v not in (None, [], "")}
    body = yaml.safe_dump(clean, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"---\n{body}---\n"


def _atomic_write(path: Path, content: str) -> None:
    """Write `content` to `path` atomically (temp file in the same dir + `os.replace`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_export_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _levels_at_or_above(min_level: str) -> list[str]:
    """Every level from `red` down through `min_level` (inclusive), per `LEVEL_ORDER`."""
    if min_level not in LEVEL_ORDER:
        raise ConfigError(f"export.obsidian.min_level must be one of {LEVEL_ORDER}, got {min_level!r}")
    idx = LEVEL_ORDER.index(min_level)
    return list(LEVEL_ORDER[: idx + 1])


def _level_label(level: str | None) -> str:
    levels = settings().taxonomy.get("triage_levels", {})
    entry = levels.get(level or "", {})
    emoji = entry.get("emoji", "")
    label = entry.get("label", level or "")
    return f"{emoji} {label}".strip()


# --------------------------------------------------------------------------
# read helpers (plain SQL; not Cypher, so allowed outside eoa.memory.graph)
# --------------------------------------------------------------------------


def _list_entities() -> list[dict[str, Any]]:
    sql = "SELECT id, name, kind, country, aliases, focus, notes FROM entities ORDER BY name"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def _list_items(levels: list[str], since_days: int | None) -> list[dict[str, Any]]:
    since_clause = ""
    params: dict[str, Any] = {"levels": levels}
    if since_days is not None:
        since_clause = "AND COALESCE(published_at, created_at) >= now() - (%(since_days)s || ' days')::interval"
        params["since_days"] = since_days
    sql = f"""
        SELECT id, url, title, lang, published_at, fetched_at, domain, subdomain,
               level, score, summary_he, so_what_he, key_facts, uncertainty_he,
               entities_mentioned, source_name
        FROM items
        WHERE security_status = 'clean'
          AND dedup_of IS NULL
          AND level = ANY(%(levels)s)
          {since_clause}
        ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST, id DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _list_reports(since_days: int | None) -> list[dict[str, Any]]:
    since_clause = ""
    params: dict[str, Any] = {}
    if since_days is not None:
        since_clause = "WHERE created_at >= now() - (%(since_days)s || ' days')::interval"
        params["since_days"] = since_days
    sql = f"""
        SELECT id, kind, period_start, period_end, path_docx, path_md, path_html, created_at
        FROM reports
        {since_clause}
        ORDER BY created_at DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _items_mentioning_entity(name: str) -> list[dict[str, Any]]:
    sql = """
        SELECT id, title, url, published_at, fetched_at, summary_he, level
        FROM items
        WHERE %(name)s = ANY(COALESCE(entities_mentioned, '{}'))
          AND security_status = 'clean'
        ORDER BY COALESCE(published_at, created_at) DESC NULLS LAST
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"name": name})
        return cur.fetchall()


def _items_by_ids(ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    sql = "SELECT id, title FROM items WHERE id = ANY(%(ids)s)"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"ids": ids})
        return cur.fetchall()


# --------------------------------------------------------------------------
# per-entity graph/timeline assembly
# --------------------------------------------------------------------------


def _entity_timeline_lines(
    entity: dict[str, Any], id_title_map: dict[int, str]
) -> tuple[list[str], list[int]]:
    """Return (rendered "ציר זמן" lines, sorted unique source item ids) for `entity`.

    Combines `eoa.memory.graph.entity_timeline` (events) with items whose
    `entities_mentioned` names this entity, newest first. `id_title_map` is
    both read and updated in place so repeated calls across entities share
    the same item-title cache instead of re-querying.
    """
    entity_id = entity["id"]
    name = entity["name"]

    try:
        events = entity_timeline(entity_id)
    except Exception as exc:  # pragma: no cover - defensive: graph backend may be unavailable
        log.warning("obsidian.entity_timeline_failed", entity_id=entity_id, error=str(exc))
        events = []

    mentions = _items_mentioning_entity(name)

    needed_ids = {ev["item_id"] for ev in events if ev.get("item_id") is not None}
    needed_ids |= {row["id"] for row in mentions}
    missing = sorted(i for i in needed_ids if i not in id_title_map)
    if missing:
        for row in _items_by_ids(missing):
            id_title_map[row["id"]] = row.get("title") or ""

    entries: list[tuple[dt.date | None, int, str]] = []
    for ev in events:
        item_id = ev.get("item_id")
        if item_id is None:
            continue
        text = ev.get("summary_he") or ev.get("title") or ""
        entries.append((ev.get("date"), item_id, text))
    for row in mentions:
        item_id = row["id"]
        raw_date = row.get("published_at") or row.get("fetched_at")
        date = raw_date.date() if isinstance(raw_date, dt.datetime) else raw_date
        text = row.get("summary_he") or row.get("title") or ""
        entries.append((date, item_id, text))

    seen: set[tuple[int, str]] = set()
    deduped: list[tuple[dt.date | None, int, str]] = []
    for date, item_id, text in entries:
        key = (item_id, text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((date, item_id, text))
    deduped.sort(key=lambda e: e[0] or dt.date.min, reverse=True)

    lines: list[str] = []
    source_ids: list[int] = []
    for date, item_id, text in deduped:
        date_str = date.isoformat() if date else "לא ידוע"
        title = id_title_map.get(item_id, "")
        lines.append(f"- {date_str} — {_item_link(item_id, title)} — {text}")
        if item_id not in source_ids:
            source_ids.append(item_id)
    return lines, sorted(source_ids)


def _entity_neighbor_lines(entity: dict[str, Any], id_title_map: dict[int, str] | None = None) -> list[str]:
    """Render "קשרים" lines for `entity`, with the real evidencing item linked per edge.

    Neighbor discovery still goes through `eoa.memory.graph.neighbors()`
    (one call per edge label, unchanged) for the entity/kind list; the
    evidencing `item_id`/`evidence` for each edge is layered on top via
    `eoa.memory.graph.edges_of()`, matched by `(label, other entity id)`, so
    the מקור line now links the real source item instead of an unresolved
    placeholder. If the provenance lookup itself fails (e.g. graph backend
    unavailable), each line still renders with an explicit "not available"
    placeholder -- never an invented source, per `docs/CONVENTIONS.md` rule 5
    ("never invent").
    """
    entity_id = entity["id"]
    id_title_map = id_title_map if id_title_map is not None else {}

    provenance: dict[tuple[str, Any], tuple[int | None, str | None]] = {}
    try:
        for e in edges_of(entity_id, depth=1):
            other_id = e.dst_entity_id if e.src_entity_id == entity_id else e.src_entity_id
            provenance[(e.label, other_id)] = (e.item_id, e.evidence)
    except Exception as exc:  # pragma: no cover - defensive: graph backend may be unavailable
        log.warning("obsidian.edges_of_failed", entity_id=entity_id, error=str(exc))

    needed_item_ids = sorted(
        {iid for iid, _ev in provenance.values() if iid is not None and iid not in id_title_map}
    )
    if needed_item_ids:
        for row in _items_by_ids(needed_item_ids):
            id_title_map[row["id"]] = row.get("title") or ""

    lines: list[str] = []
    for label in sorted(EDGE_LABELS):
        try:
            related = neighbors(entity_id, label=label, depth=1)
        except Exception as exc:  # pragma: no cover - defensive: graph backend may be unavailable
            log.warning("obsidian.neighbors_failed", entity_id=entity_id, label=label, error=str(exc))
            continue
        for vertex in related:
            if not isinstance(vertex, dict):
                continue
            # Tolerant of both the real AGE agtype shape (nested "properties")
            # and an already-flattened dict -- see `eoa.memory.graph._vertex_fields`.
            props = vertex.get("properties", vertex)
            if not isinstance(props, dict):
                continue
            other_name = props.get("name")
            other_id = props.get("entity_id")
            if not other_name:
                continue
            item_id, _evidence = provenance.get((label, other_id), (None, None))
            source = _item_link(item_id, id_title_map.get(item_id, "")) if item_id is not None else "לא זמין"
            lines.append(f"- {_entity_link(other_name)} — {label} (מקור: {source})")
    return lines


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_entity_md(
    entity: dict[str, Any],
    timeline_lines: list[str],
    neighbor_lines: list[str],
    source_ids: list[int],
    id_title_map: dict[int, str],
) -> str:
    fm = {
        "kind": entity.get("kind"),
        "country": entity.get("country"),
        "aliases": entity.get("aliases") or [],
        "focus": entity.get("focus") or [],
        "tags": ["entity", entity.get("kind") or "unknown"],
    }
    lines = [_frontmatter(fm).rstrip("\n"), "", f"# {entity['name']}", ""]
    lines.append("## ציר זמן")
    lines.append("")
    lines.extend(timeline_lines or ["_אין אירועים ידועים._"])
    lines.append("")
    lines.append("## קשרים")
    lines.append("")
    lines.extend(neighbor_lines or ["_אין קשרים ידועים בגרף._"])
    lines.append("")
    lines.append("## מקורות")
    lines.append("")
    if source_ids:
        lines.extend(f"- {_item_link(i, id_title_map.get(i, ''))}" for i in source_ids)
    else:
        lines.append("_אין מקורות ידועים._")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_item_md(item: dict[str, Any]) -> str:
    entities = item.get("entities_mentioned") or []
    fm = {
        "url": item.get("url"),
        "source": item.get("source_name"),
        "published_at": _iso(item.get("published_at")),
        "domain": item.get("domain"),
        "level": item.get("level"),
        "score": item.get("score"),
        "entities": entities,
    }
    title = item.get("title") or item.get("url") or "ללא כותרת"
    lines = [_frontmatter(fm).rstrip("\n"), "", f"# {title}", ""]
    if item.get("summary_he"):
        lines.extend([item["summary_he"], ""])
    if item.get("so_what_he"):
        lines.extend(["## למה זה חשוב", item["so_what_he"], ""])
    key_facts = item.get("key_facts") or []
    if key_facts:
        lines.append("## עובדות מפתח")
        lines.extend(f"- {fact}" for fact in key_facts)
        lines.append("")
    if item.get("uncertainty_he"):
        lines.extend(["## אי-ודאות", item["uncertainty_he"], ""])
    if entities:
        lines.append("## ישויות")
        lines.append(" ".join(_entity_link(e) for e in entities))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_report_md(report: dict[str, Any]) -> str:
    fm = {
        "kind": report.get("kind"),
        "period_start": _iso(report.get("period_start")),
        "period_end": _iso(report.get("period_end")),
    }
    date_part = _iso(report.get("period_end")) or _iso(report.get("created_at")) or ""
    lines = [
        _frontmatter(fm).rstrip("\n"),
        "",
        f"# דוח {report.get('kind') or ''} — {date_part}".strip(),
        "",
    ]

    body: str | None = None
    md_path = report.get("path_md")
    if md_path:
        candidate = Path(md_path)
        if not candidate.is_absolute():
            candidate = REPO_ROOT / candidate
        if candidate.exists():
            try:
                body = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("obsidian.report_md_read_failed", path=str(candidate), error=str(exc))

    lines.append(body.strip() if body else "_אין תוכן Markdown זמין לדוח זה._")
    lines.append("")
    docx_path = report.get("path_docx")
    if docx_path:
        lines.append(f"**Word:** `{docx_path}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_daily_md(date: dt.date, items: list[dict[str, Any]]) -> str:
    lines = [f"# {date.isoformat()}", ""]
    for item in items:
        link = _item_link(item["id"], item.get("title"))
        lines.append(f"- {_level_label(item.get('level'))} {link}")
    return "\n".join(lines).rstrip() + "\n"


def _render_index_md(stats: ExportStats) -> str:
    lines = [
        "# Obsidian Export — EO-Analyst",
        "",
        f"- ישויות: {stats.entities_written}",
        f"- פריטים: {stats.items_written}",
        f"- דוחות: {stats.reports_written}",
        f"- ימים (Daily): {stats.daily_written}",
        "",
        f"רמת סף מינימלית: `{stats.min_level}`",
    ]
    if stats.since_days is not None:
        lines.append(f"חלון: {stats.since_days} ימים אחרונים")
    lines.append(f"עודכן: {stats.finished_at.isoformat()}")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def export_vault(since_days: int | None = None) -> ExportStats:
    """Write (or refresh) the Obsidian vault under `config.export.obsidian.vault_dir`.

    `since_days=None` exports the full history; otherwise only items/reports
    published/created within the last `since_days` days are included (`Daily`
    MOCs follow the same window). Entity notes are always exported in full
    when `config.export.obsidian.entities` is set -- they are long-lived
    reference pages, not period snapshots. No-op (zero counts) when
    `config.export.obsidian.enabled` is false.
    """
    cfg = settings().export.obsidian
    started_at = dt.datetime.now(dt.UTC)

    vault_dir = Path(cfg.vault_dir)
    if not vault_dir.is_absolute():
        vault_dir = REPO_ROOT / vault_dir

    stats = ExportStats(
        vault_dir=str(vault_dir),
        since_days=since_days,
        min_level=cfg.min_level,
        started_at=started_at,
        finished_at=started_at,
    )

    if not cfg.enabled:
        log.info("obsidian.export_disabled")
        return stats

    levels = _levels_at_or_above(cfg.min_level)

    id_title_map: dict[int, str] = {}
    items: list[dict[str, Any]] = []

    if cfg.items:
        items = _list_items(levels, since_days)
        id_title_map.update({item["id"]: item.get("title") or "" for item in items})

    if cfg.entities:
        for entity in _list_entities():
            timeline_lines, source_ids = _entity_timeline_lines(entity, id_title_map)
            neighbor_lines = _entity_neighbor_lines(entity, id_title_map)
            content = render_entity_md(entity, timeline_lines, neighbor_lines, source_ids, id_title_map)
            path = vault_dir / "Entities" / f"{slugify(entity['name'])}.md"
            _atomic_write(path, content)
            stats.entities_written += 1

    if cfg.items:
        for item in items:
            content = render_item_md(item)
            path = vault_dir / "Items" / f"{_item_stub(item['id'], item.get('title'))}.md"
            _atomic_write(path, content)
            stats.items_written += 1

        daily_source = _list_items(["red", "orange"], since_days)
        by_date: dict[dt.date, list[dict[str, Any]]] = {}
        for item in daily_source:
            raw_date = item.get("published_at") or item.get("fetched_at")
            if raw_date is None:
                continue
            date = raw_date.date() if isinstance(raw_date, dt.datetime) else raw_date
            by_date.setdefault(date, []).append(item)
        for date, day_items in by_date.items():
            content = render_daily_md(date, day_items)
            path = vault_dir / "Daily" / f"{date.isoformat()}.md"
            _atomic_write(path, content)
            stats.daily_written += 1

    if cfg.reports:
        for report in _list_reports(since_days):
            content = render_report_md(report)
            date_part = _iso(report.get("period_end")) or _iso(report.get("created_at")) or "unknown-date"
            filename = slugify(f"{report.get('kind') or 'report'}_{date_part}")
            path = vault_dir / "Reports" / f"{filename}.md"
            _atomic_write(path, content)
            stats.reports_written += 1

    stats.finished_at = dt.datetime.now(dt.UTC)
    _atomic_write(vault_dir / "_index.md", _render_index_md(stats))

    log.info(
        "obsidian.export_completed",
        entities=stats.entities_written,
        items=stats.items_written,
        reports=stats.reports_written,
        daily=stats.daily_written,
        vault_dir=stats.vault_dir,
    )
    return stats
