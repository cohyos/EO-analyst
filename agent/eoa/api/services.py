"""DB-backed services for the web API routers.

Every function here is a plain, synchronous, parameterised-SQL call (never
string-formatted with user input) built on top of `eoa.db.connection()`, or a
thin wrapper over the shared `eoa.memory.*` / `eoa.llm.ollama_client` /
`eoa.resources.gate` modules. Routes call into this module (never `eoa.db`
directly) so tests can monkeypatch a single, request-shaped surface.

For features owned by concurrently-developed modules that are not yet
implemented (`eoa.search` deep-search internals, `eoa.orchestrator`
scheduler), functions here try an optional import and fall back to an
honest stub (`{"error": {"code": "not_implemented", ...}}` or an empty
list) -- never fabricated data.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
import yaml

from eoa import config as eoa_config
from eoa import db
from eoa.config import CONFIG_DIR, REPO_ROOT, ModelsRegistry
from eoa.config import Settings as EOASettings
from eoa.feedback import surveys as feedback_surveys
from eoa.llm import ollama_client
from eoa.memory import graph, relational, vector
from eoa.resources.gate import gate

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------
# generic DB helpers
# --------------------------------------------------------------------------


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _execute(query: str, params: Any = None) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def _json_safe_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Coerce a raw DB row's `datetime`/`Decimal` values to JSON-native types.

    `db.connection()` uses psycopg's `dict_row` factory, which returns
    native `datetime.datetime`/`decimal.Decimal` objects for
    `timestamptz`/`numeric` columns. Those rows are pushed straight over
    `WS /ws/status` (`pipeline_status()`'s `current_job`, `run_log_since()`'s
    rows); `WebSocket.send_json` calls plain `json.dumps` with no
    `default=`, so an unconverted value there raises `TypeError` and kills
    the socket outright (see `agent/eoa/api/routes/status.py`).
    """
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, dt.datetime | dt.date):
            out[key] = value.isoformat()
        elif isinstance(value, decimal.Decimal):
            out[key] = float(value)
        else:
            out[key] = value
    return out


def _http_reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        r = httpx.head(url, timeout=timeout)
        if r.status_code < 500:
            return True
    except Exception:
        pass
    try:
        r = httpx.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def services_status() -> dict[str, bool]:
    """Health of the four backing services shown on the status panel."""
    s = eoa_config.settings()
    return {
        "postgres": db.ping(),
        "ollama": ollama_client.ping(),
        "searxng": _http_reachable(s.searxng.url),
        "ntfy": _http_reachable(s.notify.url),
    }


def latest_run_log_id() -> int:
    row = _fetchone("SELECT COALESCE(max(id), 0) AS m FROM run_log")
    return row["m"] if row else 0


def run_log_since(last_id: int) -> tuple[list[dict[str, Any]], int]:
    rows = _fetchall("SELECT * FROM run_log WHERE id > %s ORDER BY id ASC LIMIT 200", (last_id,))
    new_last = rows[-1]["id"] if rows else last_id
    return [row for row in (_json_safe_row(r) for r in rows) if row is not None], new_last


def _next_night_window_start() -> dt.datetime:
    from zoneinfo import ZoneInfo

    s = eoa_config.settings()
    tz = ZoneInfo(s.timezone)
    now = dt.datetime.now(tz=tz)
    h, m = (int(x) for x in s.schedule.night_window.start.split(":"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += dt.timedelta(days=1)
    return candidate


def _last_run() -> dict[str, Any] | None:
    row = _fetchone(
        "SELECT * FROM jobs WHERE kind IN ('daily_run', 'weekly_run') "
        "AND state IN ('done', 'failed', 'partial') "
        "ORDER BY finished_at DESC NULLS LAST LIMIT 1"
    )
    if not row:
        return None
    stage_rows = _fetchall(
        "SELECT stage, count(*) AS events, max(heartbeat_at) AS last_at, "
        "(array_agg(event ORDER BY id DESC))[1] AS last_event "
        "FROM run_log WHERE job_id = %s GROUP BY stage",
        (row["id"],),
    )
    stages = {
        (r["stage"] or ""): {
            "events": r["events"],
            "last_event": r["last_event"],
            "last_at": r["last_at"].isoformat() if r["last_at"] else None,
        }
        for r in stage_rows
    }
    return {
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "state": row["state"],
        "stages": stages,
    }


def pipeline_status() -> dict[str, Any]:
    current_job = _fetchone(
        "SELECT * FROM jobs WHERE state = 'running' ORDER BY started_at DESC NULLS LAST LIMIT 1"
    )
    queue_depth = _fetchone("SELECT count(*) AS n FROM jobs WHERE state = 'queued'")["n"]
    stage = None
    if current_job:
        row = _fetchone(
            "SELECT stage FROM run_log WHERE job_id = %s ORDER BY id DESC LIMIT 1", (current_job["id"],)
        )
        stage = row["stage"] if row else None
    return {
        "current_job": _json_safe_row(current_job),
        "queue_depth": queue_depth,
        "stage": stage,
        "night_window": gate().is_batch_window(),
        "next_run_at": _next_night_window_start().isoformat(),
        "last_run": _last_run(),
    }


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------

_ITEM_SORT_COLUMNS = {"score": "score", "published_at": "published_at"}


def _item_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row.get("title"),
        "url": row.get("url"),
        "source_name": row.get("source_name"),
        "published_at": row.get("published_at"),
        "lang": row.get("lang"),
        "domain": row.get("domain"),
        "subdomain": row.get("subdomain"),
        "report_kind": row.get("report_kind"),
        "trl": row.get("trl"),
        "geography": row.get("geography"),
        "score": row.get("score"),
        "level": row.get("level"),
        "triage_reason": row.get("triage_reason"),
        "summary_he": row.get("summary_he"),
        "so_what_he": row.get("so_what_he"),
        "entities_mentioned": row.get("entities_mentioned") or [],
        "tags": row.get("tags") or [],
        "security_status": row.get("security_status"),
        "dedup_of": row.get("dedup_of"),
        "key_facts": row.get("key_facts") or [],
        "uncertainty_he": row.get("uncertainty_he"),
    }


def list_items(
    *,
    level: str | None = None,
    domain: str | None = None,
    since: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "score",
) -> tuple[int, list[dict[str, Any]]]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    sort_col = _ITEM_SORT_COLUMNS.get(sort, "score")

    where = ["1 = 1"]
    params: dict[str, Any] = {}
    if level:
        levels = [x.strip() for x in level.split(",") if x.strip()]
        if levels:
            where.append("i.level = ANY(%(levels)s)")
            params["levels"] = levels
    if domain:
        where.append("i.domain = %(domain)s")
        params["domain"] = domain
    if since:
        where.append("COALESCE(i.published_at, i.fetched_at) >= %(since)s")
        params["since"] = since
    if q:
        where.append("(i.title ILIKE %(q)s OR i.summary_he ILIKE %(q)s OR i.so_what_he ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    where_sql = " AND ".join(where)

    total_row = _fetchone(f"SELECT count(*) AS n FROM items i WHERE {where_sql}", params)
    total = total_row["n"] if total_row else 0

    params = {**params, "limit": page_size, "offset": (page - 1) * page_size}
    rows = _fetchall(
        f"""
        SELECT i.*, s.name AS source_name
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE {where_sql}
        ORDER BY i.{sort_col} DESC NULLS LAST, i.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    return total, [_item_card(r) for r in rows]


def get_item(item_id: int) -> dict[str, Any] | None:
    row = _fetchone(
        "SELECT i.*, s.name AS source_name FROM items i LEFT JOIN sources s ON s.id = i.source_id WHERE i.id = %s",
        (item_id,),
    )
    if row is None:
        return None
    card = _item_card(row)
    card["clean_text"] = row.get("clean_text")
    card["events"] = _fetchall(
        "SELECT * FROM events WHERE item_id = %s ORDER BY date NULLS LAST, id", (item_id,)
    )
    card["edges"] = _item_edges(item_id, card.get("entities_mentioned") or [])
    card["investigations"] = _fetchall(
        "SELECT j.id AS job_id, j.state, j.payload->>'question' AS question, j.started_at, j.finished_at "
        "FROM jobs j WHERE j.kind = 'deep_search' AND (j.payload->>'item_id')::bigint = %s "
        "ORDER BY j.created_at DESC",
        (item_id,),
    )
    return card


def _item_edges(item_id: int, entities_mentioned: list[str]) -> list[dict[str, Any]]:
    """Graph edges evidenced by `item_id`: every `entities_mentioned` name is resolved to its
    `entities.id`, then `eoa.memory.graph.edges_of` is walked for each and filtered down to the
    edges this item actually evidenced (`edge.item_id == item_id`)."""
    if not entities_mentioned:
        return []
    rows = _fetchall("SELECT id FROM entities WHERE name = ANY(%(names)s)", {"names": entities_mentioned})
    seen: set[tuple[int, int, str]] = set()
    edges: list[dict[str, Any]] = []
    for row in rows:
        try:
            edge_rows = graph.edges_of(row["id"], depth=1)
        except Exception as exc:
            log.warning("graph.edges_of_failed", entity_id=row["id"], item_id=item_id, error=str(exc))
            continue
        for e in edge_rows:
            if e.item_id != item_id:
                continue
            key = (e.src_entity_id, e.dst_entity_id, e.label)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "src": e.src_entity_id,
                    "dst": e.dst_entity_id,
                    "label": e.label,
                    "item_id": e.item_id,
                    "evidence": e.evidence,
                }
            )
    return edges


def item_feedback(item_id: int, user_level: str, comment: str | None) -> dict[str, Any] | None:
    current = _fetchone("SELECT level FROM items WHERE id = %s", (item_id,))
    if current is None:
        return None
    _execute(
        "INSERT INTO triage_feedback (item_id, user_level, agent_level, comment) VALUES (%s, %s, %s, %s)",
        (item_id, user_level, current["level"], comment),
    )
    relational.update_item_fields(item_id, level=user_level)
    return get_item(item_id)


def investigate_item(item_id: int, question: str | None) -> int | None:
    exists = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
    if exists is None:
        return None
    return relational.enqueue_job("deep_search", {"item_id": item_id, "question": question}, priority=0)


# --------------------------------------------------------------------------
# entities / graph
# --------------------------------------------------------------------------


def _entity_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "country": row.get("country"),
        "aliases": row.get("aliases") or [],
        "focus": row.get("focus") or [],
        "item_count": row.get("item_count") or 0,
        "last_seen": row.get("last_seen"),
    }


def list_entities(*, q: str | None = None, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if q:
        where.append(
            "(e.name ILIKE %(q)s OR EXISTS (SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) a WHERE a ILIKE %(q)s))"
        )
        params["q"] = f"%{q}%"
    if kind:
        where.append("e.kind = %(kind)s")
        params["kind"] = kind
    where_sql = " AND ".join(where)
    rows = _fetchall(
        f"""
        SELECT e.*,
            (SELECT count(*) FROM items i WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))) AS item_count,
            (SELECT max(COALESCE(i.published_at, i.fetched_at)) FROM items i
                WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))) AS last_seen
        FROM entities e
        WHERE {where_sql}
        ORDER BY last_seen DESC NULLS LAST
        LIMIT %(limit)s
        """,
        params,
    )
    return [_entity_card(r) for r in rows]


def _timeline_sort_key(row: dict[str, Any]) -> str:
    v = row.get("date") or row.get("published_at")
    if v is None:
        return ""
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def get_entity(entity_id: int) -> dict[str, Any] | None:
    row = _fetchone("SELECT * FROM entities WHERE id = %s", (entity_id,))
    if row is None:
        return None
    item_count = _fetchone(
        "SELECT count(*) AS n FROM items WHERE %s = ANY(COALESCE(entities_mentioned, '{}'))", (row["name"],)
    )["n"]
    last_seen = _fetchone(
        "SELECT max(COALESCE(published_at, fetched_at)) AS m FROM items "
        "WHERE %s = ANY(COALESCE(entities_mentioned, '{}'))",
        (row["name"],),
    )["m"]
    card = _entity_card({**row, "item_count": item_count, "last_seen": last_seen})

    events = graph.entity_timeline(entity_id)
    items = _fetchall(
        "SELECT id, title, url, published_at, level FROM items "
        "WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) "
        "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 50",
        (row["name"],),
    )
    combined = [{"type": "event", **e} for e in events] + [{"type": "item", **i} for i in items]
    combined.sort(key=_timeline_sort_key, reverse=True)
    card["timeline"] = combined

    try:
        card["neighbors"] = graph.neighbors(entity_id)
    except Exception as exc:
        log.warning("entity.neighbors_failed", entity_id=entity_id, error=str(exc))
        card["neighbors"] = []
    return card


def build_graph(entity_id: int, *, depth: int = 1, labels: str | None = None) -> dict[str, Any]:
    center = _fetchone("SELECT id, name, kind, country FROM entities WHERE id = %s", (entity_id,))
    if center is None:
        return {"nodes": [], "edges": []}

    label_list = (
        [x.strip().upper() for x in labels.split(",") if x.strip()] if labels else sorted(graph.EDGE_LABELS)
    )
    unknown = [label for label in label_list if label not in graph.EDGE_LABELS]
    if unknown:
        raise ValueError(f"unknown edge label(s): {unknown}")

    nodes: dict[int, dict[str, Any]] = {entity_id: dict(center)}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str, int | None]] = set()
    for label in label_list:
        try:
            edge_rows = graph.edges_of(entity_id, label=label, depth=depth)
        except Exception as exc:
            log.warning("graph.edges_failed", entity_id=entity_id, label=label, error=str(exc))
            continue
        for e in edge_rows:
            key = (e.src_entity_id, e.dst_entity_id, e.label, e.item_id)
            if key in seen:
                continue
            seen.add(key)
            # Nodes discovered only through an edge (not the center entity)
            # carry a name but no kind/country -- edges_of() doesn't fetch
            # those, and inventing them would violate "never invent".
            nodes.setdefault(
                e.src_entity_id, {"id": e.src_entity_id, "name": e.src_name, "kind": None, "country": None}
            )
            nodes.setdefault(
                e.dst_entity_id, {"id": e.dst_entity_id, "name": e.dst_name, "kind": None, "country": None}
            )
            edges.append(
                {
                    "src": e.src_entity_id,
                    "dst": e.dst_entity_id,
                    "label": e.label,
                    "item_id": e.item_id,
                    "evidence": e.evidence,
                }
            )

    return {"nodes": list(nodes.values()), "edges": edges}


_GRAPH_QUERIES = {
    "partners_of_competitors": lambda arg: graph.partners_of_competitors(arg or ""),
    "suppliers_of_program_bidders": lambda arg: graph.suppliers_of_program_bidders(arg or ""),
    "startups_linked_to_majors": lambda arg: graph.startups_linked_to_majors(int(arg) if arg else 2),
}


def run_named_graph_query(name: str, arg: str | None) -> list[dict[str, Any]]:
    fn = _GRAPH_QUERIES.get(name)
    if fn is None:
        raise KeyError(name)
    return fn(arg)


# --------------------------------------------------------------------------
# reports / morning
# --------------------------------------------------------------------------


def _report_card(row: dict[str, Any]) -> dict[str, Any]:
    included = row.get("items_included") or []
    return {
        "id": row["id"],
        "kind": row.get("kind"),
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "path_docx": row.get("path_docx"),
        "path_md": row.get("path_md"),
        "path_html": row.get("path_html"),
        "qa_passed": row.get("qa_passed"),
        "created_at": row.get("created_at"),
        "headline_count": len(included),
    }


def list_reports(*, kind: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    where = "kind = %(kind)s" if kind else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200)}
    if kind:
        params["kind"] = kind
    rows = _fetchall(f"SELECT * FROM reports WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
    return [_report_card(r) for r in rows]


def _resolve_repo_path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def get_report(report_id: int) -> dict[str, Any] | None:
    row = _fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
    if row is None:
        return None
    card = _report_card(row)
    html: str | None = None
    path_html = row.get("path_html")
    if path_html:
        p = _resolve_repo_path(path_html)
        if p.exists():
            try:
                html = p.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("report.html_read_failed", report_id=report_id, error=str(exc))
    card["html"] = html
    card["open_points"] = _fetchall(
        "SELECT * FROM clarifications WHERE kind = 'report_open_point' AND answer IS NULL ORDER BY asked_at DESC"
    )
    card["items_included"] = row.get("items_included") or []
    return card


def report_file_path(report_id: int, fmt: str) -> Path | None:
    col = {"docx": "path_docx", "md": "path_md", "html": "path_html"}.get(fmt)
    if col is None:
        return None
    row = _fetchone(f"SELECT {col} AS path FROM reports WHERE id = %s", (report_id,))
    if row is None or not row.get("path"):
        return None
    p = _resolve_repo_path(row["path"])
    return p if p.exists() else None


def morning() -> dict[str, Any]:
    reports = list_reports(kind="daily", limit=1)
    report = get_report(reports[0]["id"]) if reports else None

    headlines: list[dict[str, Any]] = []
    if report:
        ids = report.get("items_included") or []
        if ids:
            rows = _fetchall(
                "SELECT id, title, level, summary_he, url FROM items WHERE id = ANY(%(ids)s) "
                "ORDER BY array_position(%(ids)s, id)",
                {"ids": ids},
            )
            headlines = [
                {
                    "item_id": r["id"],
                    "title": r["title"],
                    "level": r["level"],
                    "summary_he": r["summary_he"],
                    "url": r["url"],
                }
                for r in rows
            ]

    open_points = _fetchall(
        "SELECT id, question, options, answer, assumed FROM clarifications "
        "WHERE answer IS NULL ORDER BY asked_at DESC LIMIT 20"
    )
    return {
        "report": report,
        "headlines": headlines,
        "open_points": open_points,
        "night_summary": _night_summary(),
    }


def _night_summary() -> dict[str, Any] | None:
    job = _fetchone(
        "SELECT * FROM jobs WHERE kind = 'daily_run' AND state IN ('done', 'failed', 'partial') "
        "ORDER BY finished_at DESC NULLS LAST LIMIT 1"
    )
    if job is None:
        # No completed nightly run yet: return an explicit all-zero summary (never null) so the UI renders.
        return {
            "items_ingested": 0,
            "classified": 0,
            "red": 0,
            "orange": 0,
            "deep_searches": 0,
            "duration_min": None,
            "errors": 0,
            "state": "none",
        }
    result = job.get("result") or {}
    started, finished = job.get("started_at"), job.get("finished_at")
    duration_min = round((finished - started).total_seconds() / 60, 1) if started and finished else None

    def _item_count(extra_where: str = "") -> int:
        if not (started and finished):
            return 0
        row = _fetchone(
            f"SELECT count(*) AS n FROM items WHERE fetched_at BETWEEN %s AND %s {extra_where}",
            (started, finished),
        )
        return row["n"] if row else 0

    deep_searches = 0
    if started and finished:
        row = _fetchone(
            "SELECT count(*) AS n FROM jobs WHERE kind = 'deep_search' AND created_at BETWEEN %s AND %s",
            (started, finished),
        )
        deep_searches = row["n"] if row else 0

    errors_row = _fetchone(
        "SELECT count(*) AS n FROM run_log WHERE job_id = %s AND event ILIKE %s", (job["id"], "%error%")
    )
    errors = errors_row["n"] if errors_row else 0

    return {
        "items_ingested": result.get("items_ingested", _item_count()),
        "classified": result.get(
            "classified", _item_count("AND 'classify' = ANY(COALESCE(processed_stages, '{}'))")
        ),
        "red": result.get("red", _item_count("AND level = 'red'")),
        "orange": result.get("orange", _item_count("AND level = 'orange'")),
        "deep_searches": result.get("deep_searches", deep_searches),
        "duration_min": result.get("duration_min", duration_min),
        "errors": result.get("errors", errors),
    }


# --------------------------------------------------------------------------
# investigations (deep search)
# --------------------------------------------------------------------------


def _investigation_aggregate(job_id: int) -> dict[str, Any]:
    row = _fetchone(
        "SELECT count(DISTINCT round) AS rounds, count(*) AS queries, COALESCE(sum(pages_read), 0) AS pages_read, "
        "(array_agg(outcome ORDER BY id DESC))[1] AS outcome FROM investigation_log WHERE job_id = %s",
        (job_id,),
    )
    return row or {"rounds": 0, "queries": 0, "pages_read": 0, "outcome": None}


def list_investigations(*, limit: int = 20) -> list[dict[str, Any]]:
    jobs = _fetchall(
        "SELECT * FROM jobs WHERE kind = 'deep_search' ORDER BY created_at DESC LIMIT %s",
        (min(max(limit, 1), 200),),
    )
    out = []
    for j in jobs:
        agg = _investigation_aggregate(j["id"])
        payload = j.get("payload") or {}
        out.append(
            {
                "job_id": j["id"],
                "item_id": payload.get("item_id"),
                "question": payload.get("question"),
                "state": j["state"],
                "rounds": agg["rounds"] or 0,
                "queries": agg["queries"] or 0,
                "pages_read": agg["pages_read"] or 0,
                "outcome": agg["outcome"],
                "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"),
            }
        )
    return out


def _deep_search_answer(job: dict[str, Any]) -> dict[str, Any]:
    """Thin adapter over the concurrently-developed `eoa.search` deep-search module.

    Falls back to the job's own `result` payload, then to an honest
    `not_implemented` stub. Never fabricates an investigation outcome.
    """
    deep_search = None
    try:
        from eoa.search import deep_search as deep_search  # type: ignore[assignment]
    except ImportError:
        pass
    if deep_search is not None and hasattr(deep_search, "load_answer"):
        try:
            return deep_search.load_answer(job["id"])
        except Exception as exc:
            log.warning("deep_search.load_answer_failed", job_id=job["id"], error=str(exc))
    if job.get("result"):
        return job["result"]
    return {
        "error": {"code": "not_implemented", "message_he": "מודול החיפוש המעמיק טרם מומש", "detail": None}
    }


def get_investigation(job_id: int) -> dict[str, Any] | None:
    j = _fetchone("SELECT * FROM jobs WHERE id = %s AND kind = 'deep_search'", (job_id,))
    if j is None:
        return None
    payload = j.get("payload") or {}
    log_rows = _fetchall(
        "SELECT * FROM investigation_log WHERE job_id = %s ORDER BY round NULLS LAST, id", (job_id,)
    )
    agg = _investigation_aggregate(job_id)
    return {
        "job_id": j["id"],
        "item_id": payload.get("item_id"),
        "question": payload.get("question"),
        "state": j["state"],
        "rounds": agg["rounds"] or 0,
        "queries": agg["queries"] or 0,
        "pages_read": agg["pages_read"] or 0,
        "outcome": agg["outcome"],
        "started_at": j.get("started_at"),
        "finished_at": j.get("finished_at"),
        "log": log_rows,
        "answer": _deep_search_answer(j),
    }


def stop_investigation(job_id: int) -> bool:
    row = _fetchone("SELECT id FROM jobs WHERE id = %s AND kind = 'deep_search'", (job_id,))
    if row is None:
        return False
    _execute(
        "UPDATE jobs SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), '{stop}', 'true', true) WHERE id = %s",
        (job_id,),
    )
    return True


def latest_investigation_log_id(job_id: int) -> int:
    row = _fetchone("SELECT COALESCE(max(id), 0) AS m FROM investigation_log WHERE job_id = %s", (job_id,))
    return row["m"] if row else 0


def investigation_log_since(job_id: int, last_id: int) -> tuple[list[dict[str, Any]], int]:
    rows = _fetchall(
        "SELECT * FROM investigation_log WHERE job_id = %s AND id > %s ORDER BY id ASC LIMIT 200",
        (job_id, last_id),
    )
    new_last = rows[-1]["id"] if rows else last_id
    return rows, new_last


# --------------------------------------------------------------------------
# ask (RAG)
# --------------------------------------------------------------------------


def ask_retrieve(
    question: str, context_item_ids: list[int] | None, context_entity_ids: list[int] | None
) -> list[dict[str, Any]]:
    """Return up to 8 items nearest the question embedding, plus any explicit context items/entities."""
    items: dict[int, dict[str, Any]] = {}

    for iid in context_item_ids or []:
        row = _fetchone("SELECT id, title, url, clean_text, summary_he FROM items WHERE id = %s", (iid,))
        if row:
            items[row["id"]] = row

    for eid in context_entity_ids or []:
        erow = _fetchone("SELECT name FROM entities WHERE id = %s", (eid,))
        if not erow:
            continue
        rows = _fetchall(
            "SELECT id, title, url, clean_text, summary_he FROM items "
            "WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 5",
            (erow["name"],),
        )
        for r in rows:
            items.setdefault(r["id"], r)

    try:
        vec = ollama_client.embed([question])[0]
        for item_id, _similarity in vector.nearest(vec, limit=8):
            if item_id in items:
                continue
            row = _fetchone(
                "SELECT id, title, url, clean_text, summary_he FROM items WHERE id = %s", (item_id,)
            )
            if row:
                items[item_id] = row
    except Exception as exc:
        log.warning("ask.retrieve_embedding_failed", error=str(exc))

    return list(items.values())[:12]


def ask_build_messages(
    question: str, history: list[dict[str, str]] | None, retrieved: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the RAG chat messages plus the `[n]`-indexed citation list they reference."""
    from eoa.llm import prompts
    from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, wrap_data

    system = prompts.render("system_analyst", data_guard=DATA_GUARD_SYSTEM)
    system += (
        "\n\nענה על שאלת המשתמש בהתבסס אך ורק על הפריטים הממוספרים שסופקו לך כ-DATA למטה. "
        "כל משפט עובדתי חייב להסתמך על פריט ולסמן אותו בסימון [n]. "
        "אם התשובה אינה נמצאת בפריטים -- כתוב במפורש שלא נמצא מידע, ואל תמציא."
    )

    citations: list[dict[str, Any]] = []
    blocks: list[str] = []
    for i, row in enumerate(retrieved, start=1):
        text = row.get("clean_text") or row.get("summary_he") or ""
        blocks.append(
            f"[{i}] {row.get('title') or ''}\n{wrap_data(text[:4000], row['id'], src=row.get('url') or '')}"
        )
        citations.append({"n": i, "item_id": row["id"], "title": row.get("title"), "url": row.get("url")})
    context_block = "\n\n".join(blocks) if blocks else "(לא נמצאו פריטים רלוונטיים)"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in history or []:
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f"שאלה: {question}\n\nמקורות:\n{context_block}"})
    return messages, citations


# --------------------------------------------------------------------------
# conferences (FR-12: rolling conference tracker -- eoa.conferences)
# --------------------------------------------------------------------------


def _conference_rows(date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    start = date_from or dt.date.today().isoformat()
    end = date_to or (dt.date.today() + dt.timedelta(days=730)).isoformat()
    return _fetchall(
        "SELECT * FROM conferences WHERE status != 'cancelled' "
        "AND COALESCE(end_date, start_date) >= %(start)s AND COALESCE(start_date, end_date) <= %(end)s "
        "ORDER BY start_date ASC NULLS LAST, id",
        {"start": start, "end": end},
    )


def list_conferences(date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    """Conferences whose span overlaps [date_from, date_to] (default: today .. +24 months),
    sorted by start_date, each row including `changes` vs `prev_snapshot` (docs/API.md)."""
    from eoa.conferences.tracker import conference_card

    return [conference_card(r) for r in _conference_rows(date_from, date_to)]


def conferences_ical() -> bytes:
    """The full (non-cancelled) horizon as a `text/calendar` payload (FR-12.7)."""
    from eoa.conferences.ical import build_ical
    from eoa.conferences.tracker import conference_card

    rows = _fetchall(
        "SELECT * FROM conferences WHERE status != 'cancelled' ORDER BY start_date ASC NULLS LAST"
    )
    return build_ical([conference_card(r) for r in rows]).encode("utf-8")


# --------------------------------------------------------------------------
# tenders / forecasts (section 5.2 / FR-5.2 -- eoa.tenders)
# --------------------------------------------------------------------------


def _tender_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row.get("source"),
        "external_ref": row.get("external_ref"),
        "title": row.get("title"),
        "agency": row.get("agency"),
        "country": row.get("country"),
        "published_at": row.get("published_at"),
        "deadline": row.get("deadline"),
        "url": row.get("url"),
        "cpv_naics": row.get("cpv_naics") or [],
        "summary_he": row.get("summary_he"),
        "relevance": row.get("relevance"),
        "matched_terms": row.get("matched_terms") or [],
        "entities": row.get("entities") or [],
        "status": row.get("status"),
        "item_id": row.get("item_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_tenders(
    *, status: str | None = None, country: str | None = None, q: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if status:
        where.append("status = %(status)s")
        params["status"] = status
    if country:
        where.append("country = %(country)s")
        params["country"] = country
    if q:
        where.append("(title ILIKE %(q)s OR summary_he ILIKE %(q)s OR agency ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    where_sql = " AND ".join(where)
    rows = _fetchall(
        f"SELECT * FROM tenders WHERE {where_sql} "
        "ORDER BY deadline ASC NULLS LAST, relevance DESC NULLS LAST, id DESC LIMIT %(limit)s",
        params,
    )
    return [_tender_card(r) for r in rows]


def _forecast_card(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "platform": row.get("platform"),
        "buyer_country": row.get("buyer_country"),
        "trigger_event_id": row.get("trigger_event_id"),
        "trigger_item_id": row.get("trigger_item_id"),
        "payload_need": row.get("payload_need"),
        "candidate_vendors": row.get("candidate_vendors") or [],
        "likelihood": row.get("likelihood"),
        "window_from": row.get("window_from"),
        "window_to": row.get("window_to"),
        "rationale_he": row.get("rationale_he"),
        "sources": row.get("sources") or [],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_tender_forecasts(*, limit: int = 100) -> list[dict[str, Any]]:
    rows = _fetchall(
        "SELECT * FROM tender_forecasts ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {"limit": min(max(limit, 1), 500)},
    )
    return [_forecast_card(r) for r in rows]


# --------------------------------------------------------------------------
# clarifications
# --------------------------------------------------------------------------


def list_clarifications(open_only: bool) -> list[dict[str, Any]]:
    if open_only:
        return _fetchall("SELECT * FROM clarifications WHERE answer IS NULL ORDER BY asked_at DESC")
    return _fetchall("SELECT * FROM clarifications ORDER BY asked_at DESC")


def answer_clarification(clarification_id: int, answer: str) -> dict[str, Any] | None:
    row = _fetchone("SELECT id FROM clarifications WHERE id = %s", (clarification_id,))
    if row is None:
        return None
    _execute(
        "UPDATE clarifications SET answer = %s, answered_at = now(), assumed = false WHERE id = %s",
        (answer, clarification_id),
    )
    return _fetchone("SELECT * FROM clarifications WHERE id = %s", (clarification_id,))


# --------------------------------------------------------------------------
# surveys (FR-11: question bank, rotation, and answer ingestion live in
# `eoa.feedback.surveys` -- this is now a thin passthrough so routes/surveys.py
# doesn't need to know that module exists).
# --------------------------------------------------------------------------


def latest_survey() -> dict[str, Any]:
    latest_report = _fetchone("SELECT id FROM reports ORDER BY created_at DESC LIMIT 1")
    report_id = latest_report["id"] if latest_report else None
    return feedback_surveys.create_for_report(report_id)


def submit_survey_answers(survey_id: int, answers: dict[str, Any]) -> dict[str, Any] | None:
    return feedback_surveys.ingest_answers(survey_id, answers)


# --------------------------------------------------------------------------
# lessons
# --------------------------------------------------------------------------


def list_lessons() -> list[dict[str, Any]]:
    return _fetchall("SELECT * FROM lessons ORDER BY created_at DESC")


def create_lesson(kind: str, text: str) -> dict[str, Any] | None:
    lesson_id = relational.add_lesson(kind, text)
    return _fetchone("SELECT * FROM lessons WHERE id = %s", (lesson_id,))


def deactivate_lesson(lesson_id: int) -> bool:
    row = _fetchone("SELECT id FROM lessons WHERE id = %s", (lesson_id,))
    if row is None:
        return False
    _execute("UPDATE lessons SET active = false WHERE id = %s", (lesson_id,))
    return True


# --------------------------------------------------------------------------
# jobs & control
# --------------------------------------------------------------------------

RUN_SCOPE_TO_KIND = {"daily": "daily_run", "ingest": "ingest", "report": "report", "weekly": "weekly_run"}


def list_jobs(*, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = "state = %(state)s" if state else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if state:
        params["state"] = state
    return _fetchall(f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)


def enqueue_run(scope: str, mode: str) -> int:
    kind = RUN_SCOPE_TO_KIND.get(scope)
    if kind is None:
        raise ValueError(f"unknown scope: {scope}")
    return relational.enqueue_job(kind, {"mode": mode}, priority=5)


def cancel_job(job_id: int) -> dict[str, Any] | None:
    row = _fetchone("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if row is None:
        return None
    if row["state"] == "queued":
        _execute(
            "UPDATE jobs SET state = 'failed', error = 'cancelled_by_user', finished_at = now() WHERE id = %s",
            (job_id,),
        )
    else:
        _execute(
            "UPDATE jobs SET payload = jsonb_set(COALESCE(payload, '{}'::jsonb), '{stop}', 'true', true) WHERE id = %s",
            (job_id,),
        )
    return _fetchone("SELECT * FROM jobs WHERE id = %s", (job_id,))


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

# The only five settings files the API will ever read or write. `name` is
# validated against exactly these literal values -- never interpolated,
# joined, or otherwise used to build a filesystem path -- closing off the
# path-segment tricks (e.g. a Windows backslash inside a URL path segment)
# a fully free-form `name: str` would invite (finding #18 in
# output/reviews/codex_security_review.md).
SettingsName = Literal["config", "sources", "watchlist", "taxonomy", "models"]

SETTINGS_FILES: dict[SettingsName, str] = {
    "config": "config.yaml",
    "sources": "sources.yaml",
    "watchlist": "watchlist.yaml",
    "taxonomy": "taxonomy.yaml",
    "models": "models.yaml",
}

# Fixed absolute paths, resolved once from CONFIG_DIR -- `_settings_path` is
# a plain dict lookup keyed by the literal `name`, never a join.
_SETTINGS_PATHS: dict[str, Path] = {name: CONFIG_DIR / fname for name, fname in SETTINGS_FILES.items()}

MAX_SETTINGS_BYTES = 256 * 1024  # request-size cap (finding #19)
_MAX_YAML_NODES = 20_000
_MAX_YAML_DEPTH = 12


class SettingsConflict(Exception):
    """The caller's `If-Match`/`revision` precondition didn't match the file on disk (-> HTTP 409)."""

    def __init__(self, current_revision: str | None) -> None:
        self.current_revision = current_revision
        super().__init__("settings file changed since it was last read")


class _YamlLimitError(ValueError):
    """A parsed YAML document exceeds the node-count or nesting-depth budget (finding #19)."""


def _settings_path(name: str) -> Path:
    path = _SETTINGS_PATHS.get(name)
    if path is None:
        raise KeyError(name)
    return path


def read_settings_yaml(name: str) -> str:
    return _settings_path(name).read_text(encoding="utf-8")


def settings_revision(name: str) -> str:
    """sha256 hex digest of the current on-disk bytes for settings `name`.

    Returned by `GET /api/settings/{name}` as `revision` and accepted back
    by `PUT` (via the `If-Match` header or the body's `revision` field) as
    an optimistic-concurrency precondition -- a `PUT` whose `revision`
    doesn't match the file's current bytes is rejected with 409 instead of
    silently clobbering a concurrent edit (finding #19).
    """
    return hashlib.sha256(_settings_path(name).read_bytes()).hexdigest()


def _count_yaml_nodes(obj: Any, depth: int, seen: set[int], counter: list[int]) -> None:
    """Recursively count nodes, raising `_YamlLimitError` past the node/depth budget.

    Cycle-safe: `seen` tracks object ids already fully counted, so a
    self-referential or repeatedly-aliased YAML anchor is counted once (its
    re-encounters return immediately) rather than recursing forever or
    blowing up the count -- `yaml.safe_load` never executes code, but an
    alias-heavy or self-referential document can still be an effective
    memory/CPU bomb without this guard.
    """
    if depth > _MAX_YAML_DEPTH:
        raise _YamlLimitError(f"עומק ה-YAML חורג מהמותר (מקסימום {_MAX_YAML_DEPTH})")
    counter[0] += 1
    if counter[0] > _MAX_YAML_NODES:
        raise _YamlLimitError(f"מספר הצמתים ב-YAML חורג מהמותר (מקסימום {_MAX_YAML_NODES})")

    if isinstance(obj, dict):
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        for k, v in obj.items():
            _count_yaml_nodes(k, depth + 1, seen, counter)
            _count_yaml_nodes(v, depth + 1, seen, counter)
    elif isinstance(obj, list):
        oid = id(obj)
        if oid in seen:
            return
        seen.add(oid)
        for item in obj:
            _count_yaml_nodes(item, depth + 1, seen, counter)


def _check_yaml_limits(parsed: Any) -> None:
    _count_yaml_nodes(parsed, 0, set(), [0])


def _validate_settings_payload(name: str, parsed: Any) -> list[str]:
    if not isinstance(parsed, dict):
        return ["הקובץ חייב להיות מיפוי (mapping) בפורמט YAML"]
    try:
        if name == "config":
            registry = ModelsRegistry(
                **(yaml.safe_load((CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")) or {})
            )
            taxonomy = yaml.safe_load((CONFIG_DIR / "taxonomy.yaml").read_text(encoding="utf-8")) or {}
            watchlist = yaml.safe_load((CONFIG_DIR / "watchlist.yaml").read_text(encoding="utf-8")) or {}
            EOASettings(registry=registry, taxonomy=taxonomy, watchlist=watchlist, **parsed)
        elif name == "models":
            ModelsRegistry(**parsed)
        elif name == "sources":
            from eoa.fetch.sources_loader import Source

            for entry in parsed.get("sources", []):
                Source.model_validate(entry)
        elif name == "watchlist":
            if "companies" in parsed and not isinstance(parsed["companies"], list):
                return ["watchlist.companies חייב להיות רשימה"]
        elif name == "taxonomy":
            if "domains" not in parsed or not isinstance(parsed["domains"], dict):
                return ["taxonomy חייב להכיל מיפוי domains"]
        else:
            return [f"שם הגדרות לא ידוע: {name}"]
    except Exception as exc:
        return [str(exc)]
    return []


def write_settings_yaml(name: str, yaml_text: str, *, expected_revision: str | None = None) -> list[str]:
    """Validate `yaml_text` for settings `name`; write atomically on success.

    Order: resolve the fixed path for `name` (`KeyError` for an unknown
    name -- never a filesystem path built from `name`) -> enforce the
    `MAX_SETTINGS_BYTES` request-size cap -> if `expected_revision` was
    supplied, compare it against the current file's sha256 and raise
    `SettingsConflict` on a mismatch -> `yaml.safe_load` -> node-count/depth
    limits (`_check_yaml_limits`) -> full typed validation
    (`_validate_settings_payload`) -> atomic write-then-replace.

    Returns validation errors as a list of Hebrew messages (empty on
    success). Raises `KeyError` for an unknown `name` and `SettingsConflict`
    for a revision mismatch -- both translated to HTTP errors by the route.
    """
    path = _settings_path(name)

    raw_bytes = yaml_text.encode("utf-8")
    if len(raw_bytes) > MAX_SETTINGS_BYTES:
        return [f"קובץ ההגדרות חורג מהגודל המרבי המותר ({MAX_SETTINGS_BYTES // 1024} KB)"]

    if expected_revision is not None:
        current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != expected_revision:
            raise SettingsConflict(current)

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return [f"YAML לא תקין: {exc}"]

    try:
        _check_yaml_limits(parsed)
    except _YamlLimitError as exc:
        return [str(exc)]

    errors = _validate_settings_payload(name, parsed)
    if errors:
        return errors

    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(yaml_text)
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    eoa_config.settings.cache_clear()
    return []
