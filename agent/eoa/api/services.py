"""DB-backed services for the web API routers.

Every function here is a plain, synchronous, parameterised-SQL call (never
string-formatted with user input) built on top of `eoa.db.connection()`, or a
thin wrapper over the shared `eoa.memory.*` / `eoa.llm.ollama_client` /
`eoa.resources.gate` modules. Routes call into this module (never `eoa.db`
directly) so tests can monkeypatch a single, request-shaped surface.

For features owned by concurrently-developed modules that are not yet
implemented (`eoa.search` deep-search internals, `eoa.orchestrator`
scheduler, conferences), functions here try an optional import and fall back
to an honest stub (`{"error": {"code": "not_implemented", ...}}` or an empty
list) -- never fabricated data.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml
from psycopg.types.json import Json

from eoa import config as eoa_config
from eoa import db
from eoa.config import CONFIG_DIR, REPO_ROOT, ModelsRegistry
from eoa.config import Settings as EOASettings
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
    return rows, new_last


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
        "current_job": current_job,
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
    # `eoa.memory.graph` exposes no "edges evidenced by this item_id" lookup
    # yet (only entity-keyed traversal) -- honest empty stub, not fabricated.
    card["edges"] = []
    card["investigations"] = _fetchall(
        "SELECT j.id AS job_id, j.state, j.payload->>'question' AS question, j.started_at, j.finished_at "
        "FROM jobs j WHERE j.kind = 'deep_search' AND (j.payload->>'item_id')::bigint = %s "
        "ORDER BY j.created_at DESC",
        (item_id,),
    )
    return card


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
    for label in label_list:
        try:
            neighbors = graph.neighbors(entity_id, label=label, depth=depth)
        except Exception as exc:
            log.warning("graph.neighbors_failed", entity_id=entity_id, label=label, error=str(exc))
            continue
        for n in neighbors:
            nid = n.get("entity_id")
            if nid is None:
                continue
            nodes.setdefault(
                nid, {"id": nid, "name": n.get("name"), "kind": n.get("kind"), "country": n.get("country")}
            )
            # The public `eoa.memory.graph` API exposes neighbor vertices but
            # not per-edge properties (item_id/evidence) -- real edge and
            # label, evidence left null rather than invented.
            edges.append({"src": entity_id, "dst": nid, "label": label, "item_id": None, "evidence": None})

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
        return None
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
# conferences (phase C stub)
# --------------------------------------------------------------------------


def list_conferences(date_from: str | None, date_to: str | None) -> list[dict[str, Any]]:
    """Phase-C feature; conferences pipeline not implemented yet. Honest stub per docs/API.md."""
    return []


def conferences_ical() -> bytes:
    from icalendar import Calendar

    cal = Calendar()
    cal.add("prodid", "-//EO-Analyst//conferences//")
    cal.add("version", "2.0")
    return cal.to_ical()


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
# surveys
# --------------------------------------------------------------------------

# 12 rotating Hebrew questions; roughly 70/30 closed-vs-open per docs/API.md
# (8 choice/scale here, 4 open -- close to that split while keeping every
# open question genuinely open-ended).
SURVEY_QUESTION_BANK: list[dict[str, Any]] = [
    {
        "id": "q1",
        "type": "scale",
        "text_he": "עד כמה הדוח היום היה רלוונטי לתחומי המעקב שלך?",
        "options": ["1", "2", "3", "4", "5"],
    },
    {
        "id": "q2",
        "type": "scale",
        "text_he": "עד כמה הציונים (red/orange/yellow) תאמו את השיפוט שלך?",
        "options": ["1", "2", "3", "4", "5"],
    },
    {
        "id": "q3",
        "type": "choice",
        "text_he": "האם היו כתבות שסווגו red/orange שהיו צריכות רמה נמוכה יותר?",
        "options": ["כן, הרבה", "כן, מעט", "לא"],
    },
    {
        "id": "q4",
        "type": "choice",
        "text_he": "האם היו כתבות רלוונטיות שהיו צריכות סיווג גבוה יותר?",
        "options": ["כן, הרבה", "כן, מעט", "לא"],
    },
    {
        "id": "q5",
        "type": "choice",
        "text_he": "האם רשימת החברות למעקב (watchlist) עדכנית?",
        "options": ["כן", "חסרות חברות", "יש חברות מיותרות"],
    },
    {
        "id": "q6",
        "type": "scale",
        "text_he": "עד כמה הסיכומים בעברית (summary/so-what) היו ברורים ומדויקים?",
        "options": ["1", "2", "3", "4", "5"],
    },
    {
        "id": "q7",
        "type": "choice",
        "text_he": "האם תדירות הדוחות (יומי/שבועי) מתאימה?",
        "options": ["מתאימה", "יותר מדי", "פחות מדי"],
    },
    {
        "id": "q8",
        "type": "scale",
        "text_he": "עד כמה החקירות המעמיקות (deep search) הביאו ערך מוסף?",
        "options": ["1", "2", "3", "4", "5"],
    },
    {
        "id": "q9",
        "type": "open",
        "text_he": "אילו נושאים או חברות היית רוצה שנעקוב אחריהם ועדיין לא עוקבים?",
        "options": None,
    },
    {"id": "q10", "type": "open", "text_he": "האם היו טעויות עובדתיות בדוח? אם כן, פרט/י.", "options": None},
    {"id": "q11", "type": "open", "text_he": "מה היה הכי שימושי בדוח היום/השבוע?", "options": None},
    {"id": "q12", "type": "open", "text_he": "הערות חופשיות נוספות לשיפור המערכת.", "options": None},
]


def _rotating_subset(seed: int, k: int = 6) -> list[dict[str, Any]]:
    n = len(SURVEY_QUESTION_BANK)
    start = seed % n
    return [SURVEY_QUESTION_BANK[(start + i) % n] for i in range(k)]


def latest_survey() -> dict[str, Any]:
    latest_report = _fetchone("SELECT id FROM reports ORDER BY created_at DESC LIMIT 1")
    report_id = latest_report["id"] if latest_report else None

    if report_id is not None:
        existing = _fetchone(
            "SELECT * FROM feedback_surveys WHERE report_id = %s ORDER BY created_at DESC LIMIT 1",
            (report_id,),
        )
    else:
        existing = _fetchone(
            "SELECT * FROM feedback_surveys WHERE report_id IS NULL ORDER BY created_at DESC LIMIT 1"
        )

    if existing:
        return {
            "id": existing["id"],
            "report_id": existing.get("report_id"),
            "questions": existing.get("questions") or [],
            "answers": existing.get("answers") or {},
        }

    seed = report_id if report_id is not None else int(dt.datetime.now(tz=dt.UTC).timestamp())
    questions = _rotating_subset(seed)
    row = _fetchone(
        "INSERT INTO feedback_surveys (report_id, questions, answers) VALUES (%s, %s, %s) RETURNING *",
        (report_id, Json(questions), Json({})),
    )
    return {"id": row["id"], "report_id": row.get("report_id"), "questions": questions, "answers": {}}


def submit_survey_answers(survey_id: int, answers: dict[str, Any]) -> dict[str, Any] | None:
    row = _fetchone("SELECT * FROM feedback_surveys WHERE id = %s", (survey_id,))
    if row is None:
        return None
    _execute(
        "UPDATE feedback_surveys SET answers = %s, answered_at = now() WHERE id = %s",
        (Json(answers), survey_id),
    )

    questions_by_id = {q["id"]: q for q in (row.get("questions") or [])}
    for qid, value in answers.items():
        question = questions_by_id.get(qid)
        if question and question.get("type") == "open" and isinstance(value, str) and value.strip():
            relational.add_lesson("decision", value.strip(), source_ref=f"survey:{survey_id}:{qid}")

    return _fetchone("SELECT * FROM feedback_surveys WHERE id = %s", (survey_id,))


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

SETTINGS_FILES = {
    "config": "config.yaml",
    "sources": "sources.yaml",
    "watchlist": "watchlist.yaml",
    "taxonomy": "taxonomy.yaml",
    "models": "models.yaml",
}


def read_settings_yaml(name: str) -> str:
    fname = SETTINGS_FILES.get(name)
    if fname is None:
        raise KeyError(name)
    return (CONFIG_DIR / fname).read_text(encoding="utf-8")


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


def write_settings_yaml(name: str, yaml_text: str) -> list[str]:
    """Validate `yaml_text` for settings `name`; write atomically on success. Returns validation errors (empty on success)."""
    fname = SETTINGS_FILES.get(name)
    if fname is None:
        raise KeyError(name)

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return [f"YAML לא תקין: {exc}"]

    errors = _validate_settings_payload(name, parsed)
    if errors:
        return errors

    path = CONFIG_DIR / fname
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{fname}.", suffix=".tmp")
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
