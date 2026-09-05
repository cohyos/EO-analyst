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
import html as html_lib
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
import yaml

from eoa import config as eoa_config
from eoa import db
from eoa.config import CONFIG_DIR, REPO_ROOT, ChainEntryCfg, ModelsRegistry
from eoa.config import Settings as EOASettings
from eoa.feedback import surveys as feedback_surveys
from eoa.llm import ollama_client
from eoa.memory import graph, relational, vector
from eoa.report import geography
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
    """Health of the four backing services shown on the status panel.

    The "searxng" key is kept for web UI compatibility even though, since migration step 1b
    (docs/PLAN_WINDOWS_NATIVE.md, docs/MODULES.md search/ note 2026-09-05), it now reflects
    whichever backend `settings().search.provider` selects (ddgs by default has no container
    to reach; `eoa.search.provider.ping()` runs a lightweight query instead).
    """
    from eoa.search.provider import ping as search_ping

    s = eoa_config.settings()
    return {
        "postgres": db.ping(),
        "ollama": ollama_client.ping(),
        "searxng": search_ping(),
        "ntfy": _http_reachable(os.environ.get("NTFY_URL", s.notify.url)),  # env override like notify/ntfy.py
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


# The pipeline order `eoa.orchestrator.jobs.run_daily()` actually runs stages in -- note this is
# NOT literally `jobs.STAGE_ORDER` (that list omits "dedup_xlang", which `run_daily()` runs as a
# real stage between "classify" and "triage" regardless). Kept as a local copy, not an import of
# that module: it sets `EOA_PIPELINE`/routes cloud-LLM behavior as an import-time/process-wide
# side effect meant to hold only for the orchestrator/worker process, never for the API process
# this module also runs in.
_DAILY_RUN_STAGE_ORDER = (
    "ingest",
    "embed_dedup",
    "classify",
    "dedup_xlang",
    "triage",
    "deep_search",
    "analyze",
    "tenders",
    "post_tenders_catchup",
    "report",
    "export_backup",
    "notify",
)

# `run_log.event` values that terminate a stage (see `eoa.orchestrator.jobs._run_stage`), mapped
# to the outcome the UI shows. `start` is deliberately absent -- a stage whose only event is
# `start` is still `running` (see `_stage_status_from_events`).
_STAGE_TERMINAL_STATUS = {
    "done": "done",
    "error": "failed",
    "deferred": "skipped",
    "deadline": "skipped",
    "skipped_no_time": "skipped",
    "skipped_circuit_open": "skipped",
}


def _stage_timeline_from_log(job_id: int, job_state: str) -> dict[str, dict[str, Any]]:
    """Per-stage outcome for one job's `run_log` rows (F12): `status`/`minutes` reflect that
    stage's own terminal event -- not a raw count of heartbeat rows, which is why the old
    timeline showed the same "2" (one `start` + one `done` heartbeat) for nearly every stage
    regardless of how much work it actually did."""
    rows = _fetchall(
        "SELECT stage, event, detail, heartbeat_at FROM run_log WHERE job_id = %s ORDER BY id ASC",
        (job_id,),
    )
    stages: dict[str, dict[str, Any]] = {}
    for r in rows:
        stage = r.get("stage") or ""
        if not stage:
            continue
        detail = r.get("detail") or {}
        entry = stages.setdefault(
            stage, {"status": "pending", "minutes": None, "last_event": None, "last_at": None, "detail": {}}
        )
        entry["last_event"] = r.get("event")
        entry["last_at"] = r["heartbeat_at"].isoformat() if r.get("heartbeat_at") else entry["last_at"]
        terminal = _STAGE_TERMINAL_STATUS.get(r.get("event") or "")
        if terminal:
            entry["status"] = terminal
            entry["minutes"] = detail.get("minutes", entry["minutes"])
            entry["detail"] = {k: v for k, v in detail.items() if k not in {"minutes", "stage"}}
        elif r.get("event") == "start":
            entry["status"] = "running"
    if job_state != "running":
        # A job that's no longer running can't have a stage stuck "running" or a stage that
        # never even started -- either it finished (terminal event just wasn't logged for some
        # reason) or the job ended before reaching it.
        for entry in stages.values():
            if entry["status"] == "running":
                entry["status"] = "done"
    return stages


def _last_run() -> dict[str, Any] | None:
    row = _fetchone(
        "SELECT * FROM jobs WHERE kind IN ('daily_run', 'weekly_run') "
        "AND state IN ('done', 'failed', 'partial') "
        "ORDER BY finished_at DESC NULLS LAST LIMIT 1"
    )
    if not row:
        return None
    stages = _stage_timeline_from_log(row["id"], row["state"])
    # Known stages first in pipeline order (even if this run skipped/never reached one -- it then
    # shows as "pending"), then any unrecognized stage key the log happens to carry, oldest first.
    ordered: dict[str, dict[str, Any]] = {}
    for stage in _DAILY_RUN_STAGE_ORDER:
        ordered[stage] = stages.get(
            stage, {"status": "pending", "minutes": None, "last_event": None, "last_at": None, "detail": {}}
        )
    for stage, info in stages.items():
        if stage not in ordered:
            ordered[stage] = info
    return {
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "state": row["state"],
        "stages": ordered,
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
    country: str | None = None,
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
    # U7a: additive country/geography filter -- `country` is one or more
    # ISO-2/region codes (comma-separated, same convention as `level`);
    # matched against the *raw* free-text `geography` values known to
    # normalize to that code (`eoa.report.geography.raw_values_for_country`)
    # so callers never need to know how the data is actually spelled.
    if country:
        codes = [c.strip() for c in country.split(",") if c.strip()]
        if codes:
            raws: set[str] = set()
            for code in codes:
                raws.update(v.upper() for v in geography.raw_values_for_country(code))
            where.append("UPPER(i.geography) = ANY(%(country_raws)s)")
            params["country_raws"] = list(raws)
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


def items_by_country_groups(
    *, level: str | None = None, domain: str | None = None, since: str | None = None
) -> list[dict[str, Any]]:
    """U7a/U7c: per-country item counts (+ level breakdown), for the
    additive `GET /api/items?group_by=country` field and the dedicated
    `GET /api/items/by-country` endpoint. Thin adapter over
    `eoa.report.geography.items_by_country` -- splits the comma-separated
    `level` string the same way `list_items` does."""
    levels = [x.strip() for x in level.split(",") if x.strip()] if level else None
    return geography.items_by_country(level=levels, domain=domain, since=since)


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


def start_investigation(question: str, item_id: int | None = None) -> int | None:
    """U12 "חקירה חדשה": launch a free-standing deep-search investigation from a typed question,
    not necessarily tied to a feed item (docs/REVIEW_2026-09-05.md U12)."""
    if not question or not question.strip():
        return None
    if item_id is not None:
        exists = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
        if exists is None:
            return None
    return relational.enqueue_job("deep_search", {"item_id": item_id, "question": question}, priority=0)


def expand_investigation(job_id: int) -> int | None:
    """U12 "הרחב חקירה (תקציב נוסף)": re-run a finished investigation with double the search
    budget and its prior findings folded into context -- replaces the old unexplained "המשך
    חקירה" button, which silently re-ran the identical question from scratch
    (docs/REVIEW_2026-09-05.md U12)."""
    job = _fetchone("SELECT * FROM jobs WHERE id = %s AND kind = 'deep_search'", (job_id,))
    if job is None:
        return None
    payload = job.get("payload") or {}
    prior_result = job.get("result") or {}
    prior_bits: list[str] = []
    if prior_result.get("answer_he"):
        prior_bits.append(f"תשובה קודמת: {prior_result['answer_he']}")
    if prior_result.get("key_facts"):
        prior_bits.append("עובדות שנמצאו: " + "; ".join(prior_result["key_facts"]))
    if prior_result.get("what_was_tried_he"):
        prior_bits.append(f"מה כבר נוסה קודם: {prior_result['what_was_tried_he']}")
    return relational.enqueue_job(
        "deep_search",
        {
            "item_id": payload.get("item_id"),
            "question": payload.get("question"),
            "context_he": payload.get("context_he", ""),
            "prior_findings_he": "\n".join(prior_bits),
            "budget_multiplier": 2.0,
            "expanded_from_job_id": job_id,
        },
        priority=0,
    )


# --------------------------------------------------------------------------
# entities / graph
# --------------------------------------------------------------------------


_ENTITY_SORT_COLUMNS = {
    "last_seen": "last_seen",
    "mentions_7d": "mentions_7d",
    "mentions_30d": "mentions_30d",
    "name": "e.name",
}

# U10/F15: default view hides noise entities (see eoa.pipeline.entity_relevance);
# "הצג הכל" flips `list_entities(show_all=True)` to bypass this filter.
_DEFAULT_MIN_RELEVANCE = 0.4


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
        "relevance": row.get("relevance") if row.get("relevance") is not None else 0.0,
        "is_watchlist": bool(row.get("is_watchlist")),
        "mentions_7d": row.get("mentions_7d") or 0,
        "mentions_30d": row.get("mentions_30d") or 0,
    }


def list_entities(
    *,
    q: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    watchlist: bool = False,
    show_all: bool = False,
    sort: str = "last_seen",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """U10 entity list: search + kind/country/watchlist facets, relevance-filtered by
    default (F15), sorted by recency or recent-mention volume."""
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
    if country:
        where.append("e.country = %(country)s")
        params["country"] = country
    if watchlist:
        where.append("e.is_watchlist = true")
    if not show_all:
        where.append("e.relevance >= %(min_relevance)s")
        params["min_relevance"] = _DEFAULT_MIN_RELEVANCE
    where_sql = " AND ".join(where)
    sort_col = _ENTITY_SORT_COLUMNS.get(sort, "last_seen")
    rows = _fetchall(
        f"""
        SELECT e.*,
            (SELECT count(*) FROM items i WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))) AS item_count,
            (SELECT max(COALESCE(i.published_at, i.fetched_at)) FROM items i
                WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))) AS last_seen,
            (SELECT count(*) FROM items i WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))
                AND COALESCE(i.published_at, i.fetched_at) >= now() - interval '7 days') AS mentions_7d,
            (SELECT count(*) FROM items i WHERE e.name = ANY(COALESCE(i.entities_mentioned, '{{}}'))
                AND COALESCE(i.published_at, i.fetched_at) >= now() - interval '30 days') AS mentions_30d
        FROM entities e
        WHERE {where_sql}
        ORDER BY {sort_col} DESC NULLS LAST, e.name ASC
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


def _event_counterpart(ev: dict[str, Any], entity_name: str) -> str | None:
    """First named party/customer/program on a business event that isn't the entity itself."""
    candidates = [*(ev.get("parties") or []), ev.get("customer"), ev.get("program")]
    for c in candidates:
        if c and c != entity_name:
            return c
    return None


def _business_event_card(ev: dict[str, Any], entity_name: str) -> dict[str, Any]:
    return {
        "id": ev.get("id"),
        "item_id": ev.get("item_id"),
        "kind": ev.get("kind"),
        "date": ev.get("date"),
        "amount_usd": ev.get("amount_usd"),
        "currency": ev.get("currency"),
        "counterpart": _event_counterpart(ev, entity_name),
        "summary_he": ev.get("summary_he"),
    }


def get_entity(entity_id: int) -> dict[str, Any] | None:
    row = _fetchone("SELECT * FROM entities WHERE id = %s", (entity_id,))
    if row is None:
        return None
    name = row["name"]
    item_count = _fetchone(
        "SELECT count(*) AS n FROM items WHERE %s = ANY(COALESCE(entities_mentioned, '{}'))", (name,)
    )["n"]
    last_seen = _fetchone(
        "SELECT max(COALESCE(published_at, fetched_at)) AS m FROM items "
        "WHERE %s = ANY(COALESCE(entities_mentioned, '{}'))",
        (name,),
    )["m"]
    mentions_7d = _fetchone(
        "SELECT count(*) AS n FROM items WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) "
        "AND COALESCE(published_at, fetched_at) >= now() - interval '7 days'",
        (name,),
    )["n"]
    mentions_30d = _fetchone(
        "SELECT count(*) AS n FROM items WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) "
        "AND COALESCE(published_at, fetched_at) >= now() - interval '30 days'",
        (name,),
    )["n"]
    card = _entity_card(
        {
            **row,
            "item_count": item_count,
            "last_seen": last_seen,
            "mentions_7d": mentions_7d,
            "mentions_30d": mentions_30d,
        }
    )

    # "ציר זמן" -- items mentioning the entity (U10: title links to /items/:id, source domain,
    # date, level badge). Kept separate from business events (below), unlike the old combined
    # `timeline` this replaces, which lost per-event fields (amount/parties/counterpart) by
    # spreading events and items into one undifferentiated list with a frontend-only
    # `occurred_at` field neither row ever actually carried (U10 "the links don't work").
    items = _fetchall(
        "SELECT i.id, i.title, i.url, i.published_at, i.level, s.name AS source_name "
        "FROM items i LEFT JOIN sources s ON s.id = i.source_id "
        "WHERE %s = ANY(COALESCE(i.entities_mentioned, '{}')) "
        "ORDER BY COALESCE(i.published_at, i.fetched_at) DESC LIMIT 50",
        (name,),
    )
    card["timeline"] = [
        {
            "item_id": it["id"],
            "title": it["title"],
            "url": it["url"],
            "source_name": it.get("source_name"),
            "published_at": it["published_at"],
            "level": it["level"],
        }
        for it in items
    ]

    # "אירועים עסקיים" -- kind/date/amount/counterpart, from `graph.entity_timeline` (a plain
    # relational join on events.parties/customer/program, see that function's docstring).
    events = graph.entity_timeline(entity_id)
    card["business_events"] = [_business_event_card(e, name) for e in events]

    level_counts = _fetchall(
        "SELECT COALESCE(level, 'unclassified') AS level, count(*) AS n FROM items "
        "WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) GROUP BY 1",
        (name,),
    )
    card["kpis"] = {
        "mentions_7d": mentions_7d,
        "mentions_30d": mentions_30d,
        "events_count": len(events),
        "related_items_by_level": {r["level"]: r["n"] for r in level_counts},
    }

    # "קשרים" -- edges touching this entity directly, grouped by label, counterpart resolved
    # to a name + id so the UI can link straight to the counterpart's own entity page.
    edge_groups: dict[str, list[dict[str, Any]]] = {}
    seen_counterparts: set[tuple[str, int]] = set()
    try:
        for e in graph.edges_of(entity_id, depth=1):
            if e.src_entity_id == entity_id:
                cp_id, cp_name = e.dst_entity_id, e.dst_name
            else:
                cp_id, cp_name = e.src_entity_id, e.src_name
            key = (e.label, cp_id)
            if key in seen_counterparts:
                continue
            seen_counterparts.add(key)
            edge_groups.setdefault(e.label, []).append({"entity_id": cp_id, "entity_name": cp_name})
    except Exception as exc:
        log.warning("entity.edges_failed", entity_id=entity_id, error=str(exc))
    card["edge_groups"] = [
        {"label": label, "counterparts": counterparts} for label, counterparts in sorted(edge_groups.items())
    ]

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
        # A11: only populated for kind='bd_territory' -- None for every other report kind.
        "territory": row.get("territory"),
    }


def list_reports(*, kind: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    where = "kind = %(kind)s" if kind else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200)}
    if kind:
        params["kind"] = kind
    rows = _fetchall(f"SELECT * FROM reports WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
    return [_report_card(r) for r in rows]


def _resolve_repo_path(raw: str) -> Path:
    """Resolve a stored report path. Rows written while the app ran in Docker carry the container
    prefix ``/app/...`` (ADR-004: the same tree is now ``REPO_ROOT``), so that prefix is remapped."""
    text = str(raw).replace("\\", "/")
    if text.startswith("/app/"):
        return REPO_ROOT / text[len("/app/"):]
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


# `[n]` markers in a rendered report can reference two kinds of registry entries
# (`eoa.report.daily._extend_citation_registry`): the report's own `items` list (1-based index
# into `reports.items_included`, resolved directly against the DB below) and citations added only
# because a business event pointed at an item that wasn't already in that list -- those don't
# appear in `items_included` at all. The daily/weekly/monthly report renderers
# (`eoa.report.docx_builder.render_html`) always emit one "נספח מקורות" (sources appendix) table
# row per registry entry, `<tr id="src-{n}">`, regardless of which kind it is -- so parsing that
# appendix out of the already-persisted `path_html` is how the extended entries are recovered
# without a schema change to `reports` or touching the report builder.
_CITATION_ROW_RE = re.compile(
    r'<tr id="src-(?P<n>\d+)">\s*<td>\d+</td>\s*<td>(?P<title>.*?)</td>\s*<td>(?P<source>.*?)</td>\s*'
    r"<td>(?P<date>.*?)</td>\s*<td>(?P<link>.*?)</td>\s*</tr>",
    re.DOTALL,
)
_HREF_RE = re.compile(r'href="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(fragment: str) -> str | None:
    text = html_lib.unescape(_TAG_RE.sub("", fragment)).strip()
    return text if text and text != "—" else None


def report_citations(report_id: int) -> dict[str, Any] | None:
    """U3 (docs/REVIEW_2026-09-05.md): `n -> {item_id, url, title}` for every `[n]` citation marker
    a report's html/exec-summary can contain, so the UI can resolve a click to `/items/{id}` (or,
    failing that, the raw source URL) instead of the tooltip-only behaviour it had before."""
    row = _fetchone("SELECT items_included, path_html FROM reports WHERE id = %s", (report_id,))
    if row is None:
        return None

    items_included = row.get("items_included") or []
    citations: dict[str, dict[str, Any]] = {}
    if items_included:
        db_rows = _fetchall("SELECT id, title, url FROM items WHERE id = ANY(%(ids)s)", {"ids": items_included})
        by_id = {r["id"]: r for r in db_rows}
        for i, item_id in enumerate(items_included, start=1):
            it = by_id.get(item_id)
            citations[str(i)] = {
                "item_id": item_id,
                "url": it.get("url") if it else None,
                "title": it.get("title") if it else None,
            }

    path_html = row.get("path_html")
    if path_html:
        p = _resolve_repo_path(path_html)
        html_text = ""
        if p.exists():
            try:
                html_text = p.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("report.citations_html_read_failed", report_id=report_id, error=str(exc))
        for m in _CITATION_ROW_RE.finditer(html_text):
            n = m.group("n")
            if n in citations:
                continue
            title = _strip_html(m.group("title"))
            href = _HREF_RE.search(m.group("link"))
            url = html_lib.unescape(href.group(1)) if href else None
            item_id = None
            if url:
                found = _fetchone("SELECT id FROM items WHERE url = %s LIMIT 1", (url,))
                item_id = found["id"] if found else None
            citations[n] = {"item_id": item_id, "url": url, "title": title}

    return {"report_id": report_id, "citations": citations}


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
        "recent_errors": recent_errors(),
    }


def recent_errors(hours: int = 24, limit: int = 20) -> list[dict[str, Any]]:
    """U2: backs the Morning "שגיאות אחרונות" drawer -- every `run_log` error event in the last
    `hours`, newest first, with a short human-readable message extracted from that event's
    `detail` (never the raw traceback -- see `eoa.orchestrator.jobs._run_stage`'s `error` event)."""
    window_start, window_end = _kpi_window(hours)
    rows = _fetchall(
        "SELECT id, job_id, stage, event, detail, COALESCE(heartbeat_at, created_at) AS at "
        "FROM run_log WHERE event ILIKE %(pat)s AND COALESCE(heartbeat_at, created_at) BETWEEN %(start)s AND %(end)s "
        "ORDER BY id DESC LIMIT %(limit)s",
        {"pat": "%error%", "start": window_start, "end": window_end, "limit": limit},
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        detail = r.get("detail") or {}
        message = detail.get("error") or detail.get("message") or r.get("event") or "שגיאה"
        out.append(
            {
                "id": r["id"],
                "job_id": r.get("job_id"),
                "stage": r.get("stage"),
                "message": str(message)[:300],
                "at": r["at"].isoformat() if r.get("at") else None,
            }
        )
    return out


def _kpi_window(hours: int = 24) -> tuple[dt.datetime, dt.datetime]:
    """The `[start, now]` window the Morning KPI cards are computed over.

    F12 (docs/REVIEW_2026-09-05.md): the old `_night_summary()` scoped every count to the last
    completed `daily_run` job's own `[started_at, finished_at]` window -- a run that starts at
    01:00 and finishes at 01:10 only "sees" items ingested in those 10 minutes, so a KPI card
    reading "5 items" while 50 came in that day was not a bug in the count, it was the wrong
    window. KPIs are a rolling last-24h view of the DB, independent of any one job's runtime.
    """
    now = dt.datetime.now(tz=dt.UTC)
    return now - dt.timedelta(hours=hours), now


def _night_summary() -> dict[str, Any] | None:
    """Morning KPI cards (F12): every count is a rolling last-24h aggregate straight from the DB
    (never a job's own `result` blob, which only ever covered that job's own runtime window).
    `duration_min`/`state` are the exception -- they describe *the last completed nightly run*
    specifically (there's no other sensible meaning for "how long did the run take"), not the
    24h window."""
    window_start, window_end = _kpi_window()

    last_job = _fetchone(
        "SELECT * FROM jobs WHERE kind = 'daily_run' AND state IN ('done', 'failed', 'partial') "
        "ORDER BY finished_at DESC NULLS LAST LIMIT 1"
    )
    duration_min: float | None = None
    state = "none"
    if last_job is not None:
        started, finished = last_job.get("started_at"), last_job.get("finished_at")
        if started and finished:
            duration_min = round((finished - started).total_seconds() / 60, 1)
        state = last_job.get("state") or "none"

    def _item_count(extra_where: str = "", params: dict[str, Any] | None = None) -> int:
        row = _fetchone(
            "SELECT count(*) AS n FROM items "
            f"WHERE COALESCE(fetched_at, created_at) BETWEEN %(start)s AND %(end)s {extra_where}",
            {"start": window_start, "end": window_end, **(params or {})},
        )
        return row["n"] if row else 0

    items_ingested = _item_count()
    # "classified in-scope": the classify stage has run AND the item cleared triage (any level
    # other than 'archive'/unclassified) -- an item classified but binned as out-of-scope
    # shouldn't inflate the headline KPI the analyst reads as "how much did I get today".
    classified = _item_count(
        "AND 'classify' = ANY(COALESCE(processed_stages, '{}')) "
        "AND level IS NOT NULL AND level NOT IN ('archive', 'unclassified')"
    )
    red = _item_count("AND level = 'red'")
    orange = _item_count("AND level = 'orange'")

    deep_searches_row = _fetchone(
        "SELECT count(*) AS n FROM jobs WHERE kind = 'deep_search' AND created_at BETWEEN %(start)s AND %(end)s",
        {"start": window_start, "end": window_end},
    )
    deep_searches = deep_searches_row["n"] if deep_searches_row else 0

    errors_row = _fetchone(
        "SELECT count(*) AS n FROM run_log WHERE event ILIKE %(pat)s "
        "AND COALESCE(heartbeat_at, created_at) BETWEEN %(start)s AND %(end)s",
        {"pat": "%error%", "start": window_start, "end": window_end},
    )
    errors = errors_row["n"] if errors_row else 0

    tenders_open_row = _fetchone("SELECT count(*) AS n FROM tenders WHERE status = 'open'")
    tenders_unknown_row = _fetchone("SELECT count(*) AS n FROM tenders WHERE status = 'unknown'")
    new_forecasts_row = _fetchone(
        "SELECT count(*) AS n FROM tender_forecasts WHERE created_at BETWEEN %(start)s AND %(end)s",
        {"start": window_start, "end": window_end},
    )

    return {
        "items_ingested": items_ingested,
        "classified": classified,
        "red": red,
        "orange": orange,
        "deep_searches": deep_searches,
        "duration_min": duration_min,
        "errors": errors,
        "state": state,
        "tenders_open": tenders_open_row["n"] if tenders_open_row else 0,
        "tenders_unknown": tenders_unknown_row["n"] if tenders_unknown_row else 0,
        "new_forecasts": new_forecasts_row["n"] if new_forecasts_row else 0,
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


_ASK_ITEM_FIELDS = "id, title, url, clean_text, summary_he, key_facts, security_status, domain"

# U9 (docs/REVIEW_2026-09-05.md): tokens that mix letters and digits (program/model names like
# "XM30", "F-35") are exactly the kind of rare, specific term vector similarity blurs past --
# hybrid retrieval boosts them with a plain ILIKE match so a question like "מה זה XM30?" can't come
# back empty just because the embedding didn't rank the right item in the top few neighbours.
_RARE_TOKEN_RE = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9-]{2,}\b")


def _rare_tokens(question: str) -> list[str]:
    return list(dict.fromkeys(_RARE_TOKEN_RE.findall(question)))[:5]


# U9: live-verified against the running DB (2026-09-05) -- several Safran press-room fetches were
# actually Cloudflare/anti-bot challenge pages ("This website is using a security service to
# protect itself...") stored as a non-empty `clean_text` with `security_status='clean'` (the guard
# has no reason to quarantine a challenge page -- it isn't an injection). A fetch failure like this
# never reaches the analyze stage, so `summary_he` stays NULL; that gap is the reliable signal, not
# "any text at all". This is exactly what U9's repro cited as a blocked-page "answer".
_FETCH_FAILURE_MARKERS = (
    "this website is using a security service",
    "attention required",
    "enable javascript and cookies to continue",
    "checking your browser before accessing",
    "403 forbidden",
    "access denied",
)


def _looks_like_fetch_failure(row: dict[str, Any]) -> bool:
    blob = f"{row.get('title') or ''} {(row.get('clean_text') or '')[:300]}".lower()
    return any(marker in blob for marker in _FETCH_FAILURE_MARKERS)


def _ask_item_retrievable(row: dict[str, Any] | None) -> bool:
    """U9: retrieval (unlike explicitly-attached context) must never surface a quarantined,
    out-of-scope, unsummarized, or fetch-failure item -- e.g. a blocked/403 page that a security
    guard would not flag (it isn't an injection, just useless), which is exactly what U9's repro
    cited as its "answer". Only items that actually completed analysis (have a real `summary_he`)
    are eligible."""
    if not row:
        return False
    if row.get("security_status") not in (None, "clean"):
        return False
    if row.get("domain") == "out_of_scope":
        return False
    if not row.get("summary_he"):
        return False
    return not _looks_like_fetch_failure(row)


def ask_retrieve(
    question: str, context_item_ids: list[int] | None, context_entity_ids: list[int] | None
) -> list[dict[str, Any]]:
    """Return the explicitly-attached context items (always included, full row, regardless of
    security status -- the user attached them on purpose) followed by up to 8 retrieved items from
    a hybrid search: keyword/ILIKE on rare tokens plus vector-nearest on the question embedding,
    both filtered to clean, in-scope, non-empty items (U9, docs/REVIEW_2026-09-05.md).

    Each returned row carries `_is_context` (True for explicit attachments) so
    :func:`ask_build_messages` can cite them first and render them with full detail.
    """
    context: dict[int, dict[str, Any]] = {}
    retrieved: dict[int, dict[str, Any]] = {}

    for iid in context_item_ids or []:
        row = _fetchone(f"SELECT {_ASK_ITEM_FIELDS} FROM items WHERE id = %s", (iid,))
        if row:
            context[row["id"]] = row

    for eid in context_entity_ids or []:
        erow = _fetchone("SELECT name FROM entities WHERE id = %s", (eid,))
        if not erow:
            continue
        rows = _fetchall(
            f"SELECT {_ASK_ITEM_FIELDS} FROM items "
            "WHERE %s = ANY(COALESCE(entities_mentioned, '{}')) "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 5",
            (erow["name"],),
        )
        for r in rows:
            if r["id"] not in context:
                context.setdefault(r["id"], r)

    # hybrid retrieval 1/2: exact-token keyword match (tried first so it always outranks vector noise).
    for token in _rare_tokens(question):
        rows = _fetchall(
            f"SELECT {_ASK_ITEM_FIELDS} FROM items WHERE title ILIKE %(t)s OR clean_text ILIKE %(t)s "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT 5",
            {"t": f"%{token}%"},
        )
        for r in rows:
            if r["id"] not in context and r["id"] not in retrieved and _ask_item_retrievable(r):
                retrieved[r["id"]] = r

    # hybrid retrieval 2/2: vector-nearest on the question embedding.
    try:
        vec = ollama_client.embed([question])[0]
        for item_id, _similarity in vector.nearest(vec, limit=16):
            if item_id in context or item_id in retrieved:
                continue
            if len(retrieved) >= 8:
                break
            row = _fetchone(f"SELECT {_ASK_ITEM_FIELDS} FROM items WHERE id = %s", (item_id,))
            if _ask_item_retrievable(row):
                retrieved[item_id] = row  # type: ignore[assignment]
    except Exception as exc:
        log.warning("ask.retrieve_embedding_failed", error=str(exc))

    for row in context.values():
        row["_is_context"] = True
    for row in list(retrieved.values())[:8]:
        row["_is_context"] = False

    return list(context.values()) + list(retrieved.values())[:8]


def ask_build_messages(
    question: str, history: list[dict[str, str]] | None, retrieved: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the RAG chat messages plus the `[n]`-indexed citation list they reference.

    U9 (docs/REVIEW_2026-09-05.md): items explicitly attached to the chat context (`_is_context`)
    are cited first and rendered with full detail -- summary_he + key_facts + up to ~1500 chars of
    clean_text -- so an attached item's content always reaches the model even when it is short or
    the corpus-wide retrieval would have ranked it low. Retrieved-only items keep the shorter
    excerpt. The system prompt tells the model to answer from the attached items first and to say
    explicitly when it falls back to general knowledge instead of the corpus.
    """
    from eoa.llm import prompts
    from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, wrap_data

    system = prompts.render("system_analyst", data_guard=DATA_GUARD_SYSTEM)
    system += (
        "\n\nענה על שאלת המשתמש. סדר עדיפויות: (1) פריטים המסומנים 'הקשר מצורף' -- אלה צורפו "
        "לשיחה במפורש על ידי המשתמש; אם הם רלוונטיים לשאלה, חובה להתבסס עליהם ולצטט אותם ראשונים, "
        "גם אם השם/המונח שבשאלה אינו מוכר לך ממקורות אחרים. (2) פריטים המסומנים 'מהמאגר' -- נמצאו "
        "על ידי חיפוש ויש להשתמש בהם כתמיכה נוספת. כל משפט עובדתי המבוסס על פריט חייב לסמן אותו "
        "ב-[n]. אם התשובה אינה נמצאת באף פריט מסופק, מותר להיעזר בידע כללי -- אך יש לציין זאת "
        "במפורש ('בהתבסס על ידע כללי, לא מהמאגר'), ולעולם לא להציג ידע כללי כאילו מקורו בפריטים. "
        "אם גם בפריטים וגם בידע הכללי אין מענה -- כתוב זאת בפירוש ואל תמציא."
    )

    ordered = sorted(retrieved, key=lambda r: 0 if r.get("_is_context") else 1)

    citations: list[dict[str, Any]] = []
    blocks: list[str] = []
    for i, row in enumerate(ordered, start=1):
        is_context = bool(row.get("_is_context"))
        if is_context:
            key_facts = row.get("key_facts") or []
            facts_block = "\nעובדות מפתח:\n" + "\n".join(f"- {f}" for f in key_facts) if key_facts else ""
            body = (
                f"תקציר: {row.get('summary_he') or ''}{facts_block}\n\n"
                f"טקסט מלא (קטע):\n{(row.get('clean_text') or '')[:1500]}"
            )
            label = 'הקשר מצורף (צוין ע"י המשתמש)'
        else:
            body = (row.get("clean_text") or row.get("summary_he") or "")[:4000]
            label = "מהמאגר (אוחזר לפי השאלה)"
        blocks.append(
            f"[{i}] ({label}) {row.get('title') or ''}\n{wrap_data(body, row['id'], src=row.get('url') or '')}"
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


# A row can only reach the DB with relevance <= 2 if it was inserted before the LLM-relevance
# gate existed (eoa.tenders.scan.scan_tenders) or the LLM was never available to score it down --
# the API's default view hides these rather than trusting every historical/ungated row.
DEFAULT_MIN_RELEVANCE = 3


def list_tenders(
    *,
    status: str | None = None,
    country: str | None = None,
    q: str | None = None,
    min_relevance: int | None = DEFAULT_MIN_RELEVANCE,
    limit: int = 100,
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
    if min_relevance is not None:
        where.append("relevance >= %(min_relevance)s")
        params["min_relevance"] = min_relevance
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

# U4/F17 (docs/REVIEW_2026-09-05.md): kinds that count as "the same effective run" for
# idempotency -- requesting a fresh daily run while a weekly run (which performs the full daily
# pipeline first, see `eoa.orchestrator.jobs.run_weekly`) is already in flight is still a
# duplicate from the user's point of view, not a second independent run.
_RUN_IDEMPOTENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "daily_run": ("daily_run", "weekly_run"),
    "report": ("report", "daily_run", "weekly_run"),
}


class RunAlreadyActive(Exception):
    """An equivalent run is already queued/running; the route surfaces this as HTTP 409."""

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        super().__init__(f"a {job.get('kind')} job is already {job.get('state')} (id={job.get('id')})")


def list_jobs(*, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = "state = %(state)s" if state else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if state:
        params["state"] = state
    return _fetchall(f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)


def enqueue_run(scope: str, mode: str) -> int:
    """U4/F17: idempotent -- if an equivalent job is already `queued`/`running`, raises
    `RunAlreadyActive(job)` instead of enqueueing a second one (repro: the "הרץ עכשיו" button gave
    no feedback, got double-clicked, and enqueued two overlapping daily runs)."""
    kind = RUN_SCOPE_TO_KIND.get(scope)
    if kind is None:
        raise ValueError(f"unknown scope: {scope}")
    equivalent_kinds = list(_RUN_IDEMPOTENCY_GROUPS.get(kind, (kind,)))
    existing = _fetchone(
        "SELECT * FROM jobs WHERE kind = ANY(%(kinds)s) AND state IN ('queued', 'running') "
        "ORDER BY created_at DESC LIMIT 1",
        {"kinds": equivalent_kinds},
    )
    if existing is not None:
        raise RunAlreadyActive(_json_safe_row(existing) or {})
    return relational.enqueue_job(kind, {"mode": mode}, priority=5)


# --------------------------------------------------------------------------
# run progress (U4/F17): GET /api/runs/current
# --------------------------------------------------------------------------

# Jobs the "run now" button (or the scheduler) can start that the analyst thinks of as "a run" --
# excludes `deep_search` (consumed piecemeal from inside `daily_run`'s own stage, or triggered
# individually from the UI/chat -- see `other_running` below).
_PRIMARY_RUN_KINDS = ("daily_run", "weekly_run", "monthly_run", "report", "ingest", "tender_scan", "conference_scan")


def _stage_progress_for_job(job_id: int, job_state: str) -> dict[str, Any]:
    stages_log = _stage_timeline_from_log(job_id, job_state)
    ordered: list[dict[str, Any]] = []
    current_stage: str | None = None
    for stage in _DAILY_RUN_STAGE_ORDER:
        info = stages_log.get(stage, {"status": "pending", "minutes": None})
        ordered.append({"stage": stage, "status": info["status"], "minutes": info.get("minutes")})
        if info["status"] == "running" and current_stage is None:
            current_stage = stage
    for stage, info in stages_log.items():
        if stage not in _DAILY_RUN_STAGE_ORDER:
            ordered.append({"stage": stage, "status": info["status"], "minutes": info.get("minutes")})
    if current_stage is None:
        current_stage = next((s["stage"] for s in ordered if s["status"] == "pending"), None)
    return {"stages": ordered, "current_stage": current_stage}


def _historical_stage_minutes(stage: str, limit: int = 5) -> float | None:
    """Average `minutes` of the last `limit` completed runs of `stage`, for the ETA estimate."""
    rows = _fetchall(
        "SELECT (detail->>'minutes')::float AS minutes FROM run_log "
        "WHERE stage = %(stage)s AND event = 'done' AND detail ? 'minutes' "
        "ORDER BY id DESC LIMIT %(limit)s",
        {"stage": stage, "limit": limit},
    )
    values = [r["minutes"] for r in rows if r.get("minutes") is not None]
    return round(sum(values) / len(values), 1) if values else None


def _eta_minutes(stages: list[dict[str, Any]]) -> float | None:
    """Remaining-time estimate: historical average minutes per not-yet-finished stage, falling
    back to that stage's configured budget (`config.yaml` `stages:`) when there's no history yet."""
    if not stages:
        return None
    budgets = eoa_config.settings().stages
    remaining = 0.0
    pending_any = False
    for entry in stages:
        if entry["status"] in ("done", "failed", "skipped"):
            continue
        pending_any = True
        hist = _historical_stage_minutes(entry["stage"])
        remaining += hist if hist is not None else float(budgets.get(entry["stage"], 15))
    return round(remaining, 1) if pending_any else 0.0


def current_run_progress() -> dict[str, Any]:
    """U4/F17: what "run now" (or the scheduler) currently has in flight, with per-stage
    progress/ETA, plus any other job a separate worker has claimed concurrently (F17: a
    `deep_search` job ran to completion without the analyst ever seeing it)."""
    primary = _fetchone(
        "SELECT * FROM jobs WHERE kind = ANY(%(kinds)s) AND state IN ('running', 'queued') "
        "ORDER BY (state = 'running') DESC, started_at DESC NULLS LAST, created_at DESC LIMIT 1",
        {"kinds": list(_PRIMARY_RUN_KINDS)},
    )
    current: dict[str, Any] | None = None
    if primary is not None:
        started = primary.get("started_at")
        elapsed_min = (
            round((dt.datetime.now(tz=dt.UTC) - started).total_seconds() / 60, 1) if started else None
        )
        if primary["state"] == "running":
            progress = _stage_progress_for_job(primary["id"], primary["state"])
        else:
            progress = {"stages": [], "current_stage": None}
        current = {
            "job_id": primary["id"],
            "kind": primary["kind"],
            "state": primary["state"],
            "current_stage": progress["current_stage"],
            "stages": progress["stages"],
            "started_at": started.isoformat() if started else None,
            "elapsed_min": elapsed_min,
            "eta_min": _eta_minutes(progress["stages"]),
        }

    exclude_id = primary["id"] if primary else -1
    other_rows = _fetchall(
        "SELECT id, kind, started_at FROM jobs WHERE state = 'running' AND id != %(exclude)s "
        "ORDER BY started_at DESC NULLS LAST",
        {"exclude": exclude_id},
    )
    other_running = [
        {
            "job_id": r["id"],
            "kind": r["kind"],
            "started_at": r["started_at"].isoformat() if r.get("started_at") else None,
        }
        for r in other_rows
    ]
    return {"current": current, "other_running": other_running}


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


# --------------------------------------------------------------------------
# U8: LLM provider routing (docs/adr/005-cloud-llm-cli.md)
# --------------------------------------------------------------------------

_PROVIDER_LABELS: dict[str, str] = {
    "ollama": "מקומי (Ollama)",
    "agy": "Gemini (Antigravity CLI)",
    "claude": "Claude (Claude Code CLI)",
    "codex": "Codex (Codex CLI)",
}


_API_PROVIDER_LABELS: dict[str, str] = {
    "anthropic": "Anthropic (API)",
    "gemini": "Gemini (API)",
    "openai": "OpenAI (API)",
}
_API_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def list_llm_providers() -> dict[str, Any]:
    """`GET /api/llm/providers`: availability + model list per provider, plus the current
    default/kill-switch/global mode, so the chat's ModelPicker and the Settings "מודלים" card can
    render without parsing config.yaml themselves.

    U8-ו (Revision 2026-09-06): a direct-API provider's ``available`` is ONLY ever a boolean
    derived from whether its env var is set ("מוגדר / לא מוגדר") -- the key itself never appears
    in this response, is never logged, and is never accepted by any write endpoint.
    """
    from eoa.llm.providers.cli import CliProvider
    from eoa.llm.providers.ollama import OllamaProvider

    s = eoa_config.settings()
    cfg = s.llm_providers
    providers: list[dict[str, Any]] = []

    ollama = OllamaProvider()
    providers.append(
        {
            "id": "ollama",
            "label": _PROVIDER_LABELS["ollama"],
            "kind": "local",
            "available": ollama.is_available(),
            "models": ollama.list_models(),
        }
    )
    if cfg.allow_cloud:
        for kind in ("agy", "claude", "codex"):
            cli = CliProvider(kind)
            cli_cfg = cfg.cli.get(kind)
            providers.append(
                {
                    "id": kind,
                    "label": _PROVIDER_LABELS[kind],
                    "kind": "cloud",
                    "available": cli.is_available(),
                    "models": cli.list_models(),
                    # U8-ג: effort/reasoning levels this CLI accepts (see CliProviderCfg).
                    "power_levels": list(cli_cfg.power_levels) if cli_cfg else ["low", "medium", "high"],
                }
            )
        from eoa.llm.providers.api import get_api_provider

        for kind in ("anthropic", "gemini", "openai"):
            api_cfg = cfg.api.get(kind)
            client = get_api_provider(kind)
            providers.append(
                {
                    "id": kind,
                    "label": _API_PROVIDER_LABELS[kind],
                    "kind": "api",
                    "key_env": _API_PROVIDER_KEY_ENV[kind],
                    "available": client.is_available(),
                    "models": client.list_models(),
                    "power_levels": list(api_cfg.power_levels) if api_cfg else ["low", "medium", "high"],
                }
            )
    return {
        "mode": cfg.mode,
        "allow_cloud": cfg.allow_cloud,
        "interactive_default": cfg.interactive_default,
        "chains": {role: [e.model_dump() for e in chain] for role, chain in cfg.chains.items()},
        "providers": providers,
    }


def summarize_llm_calls(since_hours: int = 24) -> dict[str, Any]:
    """`GET /api/llm/calls?since=24h` (U8-4): thin pass-through to
    ``eoa.memory.relational.summarize_llm_calls`` -- kept here so the route module never talks to
    the DB layer directly, matching every other endpoint in this module."""
    from eoa.memory.relational import summarize_llm_calls as _summarize

    return _summarize(since_hours=since_hours)


def _patch_yaml_scalar(text: str, key: str, replacement_value: str) -> str:
    """Replace the value of a single top-level-unique ``key: ...`` line in ``text``.

    Used only for the two ``llm_providers`` keys the Settings "מודלים" card can toggle
    (``interactive_default``, ``allow_cloud``) -- both names are unique across config.yaml, so a
    plain line-anchored regex is safe and (unlike a full yaml.safe_load + yaml.dump round-trip)
    never disturbs any other line's formatting or comments.
    """
    pattern = re.compile(rf"(?m)^([ \t]*{re.escape(key)}:)[ \t]*.*$")
    new_text, n = pattern.subn(lambda m: f"{m.group(1)} {replacement_value}", text, count=1)
    if n == 0:
        raise KeyError(key)
    return new_text


_KNOWN_CHAIN_ROLES = {"resident", "investigator", "light", "report"}
_KNOWN_CHAIN_PROVIDER_IDS = {"ollama", "agy", "claude", "codex", "anthropic", "gemini", "openai"}


def _chain_provider_power_levels() -> dict[str, list[str]]:
    """provider id -> the power/effort levels it accepts, for chain-entry validation.

    Sourced from the live ``eoa_config.settings().llm_providers`` config (``cli``/``api`` maps),
    not hardcoded, so a future provider or a locally-edited power-level list is honored. ``ollama``
    has no power levels (the local model has no effort/thinking knob).
    """
    cfg = eoa_config.settings().llm_providers
    levels: dict[str, list[str]] = {"ollama": []}
    for kind, cli_cfg in cfg.cli.items():
        levels[kind] = list(cli_cfg.power_levels)
    for kind, api_cfg in cfg.api.items():
        levels[kind] = list(api_cfg.power_levels)
    return levels


def _validate_chains(chains: dict[str, list[ChainEntryCfg]]) -> list[str]:
    """Business-rule validation for a `PUT /api/llm/settings` ``chains`` payload, beyond the
    structural typing pydantic's ``ChainEntryCfg`` already enforces: known role names, known
    provider ids, a non-empty ``model`` on every non-``ollama`` step, and ``power`` (when given)
    must be one of that provider's own ``power_levels``. Returns Hebrew error messages (empty on
    success) -- the file is never touched when this list is non-empty.
    """
    errors: list[str] = []
    power_levels = _chain_provider_power_levels()
    for role, chain in chains.items():
        if role not in _KNOWN_CHAIN_ROLES:
            errors.append(f"תפקיד לא ידוע בשרשרת: {role!r} (מותר: {', '.join(sorted(_KNOWN_CHAIN_ROLES))})")
            continue
        for i, entry in enumerate(chain):
            if entry.provider not in _KNOWN_CHAIN_PROVIDER_IDS:
                errors.append(f"{role}[{i}]: ספק לא ידוע: {entry.provider!r}")
                continue
            if entry.provider != "ollama" and not (entry.model and entry.model.strip()):
                errors.append(f"{role}[{i}]: יש לבחור מודל עבור ספק {entry.provider!r}")
            if entry.power is not None:
                allowed = power_levels.get(entry.provider, [])
                if entry.power not in allowed:
                    errors.append(
                        f"{role}[{i}]: רמת עוצמה לא נתמכת עבור {entry.provider!r}: {entry.power!r} "
                        f"(מותר: {', '.join(allowed) or 'אין'})"
                    )
    return errors


def _with_terminal_ollama(chain: list[ChainEntryCfg]) -> list[ChainEntryCfg]:
    """Append the local ``ollama`` terminal step when a chain doesn't already end with one --
    same rule `Settings.llm_providers.effective_chain` enforces at read time, applied here too so
    the persisted config.yaml itself always shows the terminal step (matching what the fixed,
    non-removable "מקומי (Ollama)" row in the UI's ChainsEditor implies is always true)."""
    if not chain or chain[-1].provider != "ollama":
        return [*chain, ChainEntryCfg(provider="ollama")]
    return chain


def _render_chains_yaml_block(chains: dict[str, list[ChainEntryCfg]]) -> str:
    """Render ``  chains: ...`` (2-space indented, nested under ``llm_providers:``) as a
    self-contained block of text ending in a newline, for `_patch_yaml_chains_block` to splice
    into config.yaml. An empty map renders as the same one-line ``chains: {}`` the shipped
    config.yaml uses when no chains are configured yet."""
    if not chains:
        return "  chains: {}\n"
    payload = {
        role: [entry.model_dump(exclude_none=True) for entry in entries] for role, entries in chains.items()
    }
    dumped = yaml.safe_dump({"chains": payload}, default_flow_style=False, sort_keys=False, allow_unicode=True)
    lines = dumped.rstrip("\n").split("\n")
    return "\n".join(f"  {line}" if line else line for line in lines) + "\n"


def _patch_yaml_chains_block(text: str, block_text: str) -> str:
    """Replace the ``  chains:`` block (the key line plus every more-deeply-indented line that
    follows it) under ``llm_providers:`` with ``block_text``. Unlike `_patch_yaml_scalar` (a
    single-line replace), ``chains`` can grow from a one-line ``{}`` into a multi-line nested
    block, so the whole block's extent has to be found, not just one line.

    Falls back to inserting ``block_text`` right after the ``llm_providers:`` line when no
    ``chains:`` key exists yet at all (e.g. a minimal test fixture's config.yaml) -- so this
    always succeeds as long as ``llm_providers:`` itself is present, same contract as
    `_patch_yaml_scalar` raising only when the anchor is entirely missing.
    """
    existing = re.compile(r"(?m)^  chains:.*$(?:\n  [ \t]+.*$)*\n?")
    if existing.search(text):
        return existing.sub(lambda m: block_text, text, count=1)
    anchor = re.compile(r"(?m)^llm_providers:.*$\n?")
    match = anchor.search(text)
    if not match:
        raise KeyError("llm_providers")
    return text[: match.end()] + block_text + text[match.end() :]


def patch_llm_provider_settings(
    *,
    interactive_default: str | None,
    allow_cloud: bool | None,
    mode: str | None = None,
    chains: dict[str, list[ChainEntryCfg]] | None = None,
    expected_revision: str | None = None,
) -> tuple[bool, list[str], str | None]:
    """`PUT /api/llm/settings`: update just ``interactive_default``/``allow_cloud``/``mode``/
    ``chains`` in config.yaml, going through the exact same validated atomic write as the generic
    settings editor (`write_settings_yaml`) -- this is a convenience for the friendly "מודלים"
    card, not a second write path with weaker guarantees.

    ``mode`` (U8-א, Revision 2026-09-06) is the global local/cloud switch; only "local"/"cloud"
    are accepted -- anything else is rejected via the returned ``errors`` list, matching this
    endpoint's existing validation contract, without ever touching the file.

    ``chains`` (per-role fallback chains, the Settings ChainsEditor) replaces the *entire*
    ``llm_providers.chains`` map when given -- a role missing from the payload simply keeps
    whatever role-name/mode based default `effective_chain` would already fall back to, it isn't
    an error. Each role's chain is validated (`_validate_chains`: known role/provider ids,
    non-empty model on a non-ollama step, power within that provider's own power_levels) *before*
    the file is touched -- any error rejects the whole write, none of the fields in the same
    request are applied, matching ``mode``'s existing "reject without touching the file" contract.
    The local `ollama` terminal step is appended to any role's chain that doesn't already end with
    one (`_with_terminal_ollama`), so the persisted YAML always shows what `effective_chain` would
    resolve to anyway.

    Returns ``(ok, errors, new_revision)``. Raises ``SettingsConflict`` on a stale
    ``expected_revision``, same as `write_settings_yaml`.
    """
    if mode is not None and mode not in ("local", "cloud"):
        return False, [f"מצב לא תקין: {mode!r} (מותר local/cloud)"], None

    if chains is not None:
        chain_errors = _validate_chains(chains)
        if chain_errors:
            return False, chain_errors, None

    text = read_settings_yaml("config")
    if interactive_default is not None:
        escaped = interactive_default.replace("\\", "\\\\").replace('"', '\\"')
        text = _patch_yaml_scalar(text, "interactive_default", f'"{escaped}"')
    if allow_cloud is not None:
        text = _patch_yaml_scalar(text, "allow_cloud", "true" if allow_cloud else "false")
    if mode is not None:
        text = _patch_yaml_scalar(text, "mode", mode)
    if chains is not None:
        normalized = {role: _with_terminal_ollama(chain) for role, chain in chains.items()}
        text = _patch_yaml_chains_block(text, _render_chains_yaml_block(normalized))

    errors = write_settings_yaml("config", text, expected_revision=expected_revision)
    new_revision = settings_revision("config") if not errors else None
    return (not errors, errors, new_revision)


# =====================================================================================
# A8: MCP (Model Context Protocol) tool sources (docs/adr/006-mcp-sources.md).
# =====================================================================================


class McpServerNotFound(Exception):
    """`{server_id}` isn't in `config/mcp.yaml` -- routes turn this into HTTP 404, matching the
    `SettingsConflict` -> 409 pattern above."""


def _mcp_key_configured(server: Any) -> bool | None:
    """`None` when the server needs no key at all (nothing to report); otherwise whether every
    named env var is actually set -- never the values themselves."""
    if not server.env:
        return None
    return all(bool(os.environ.get(name)) for name in server.env)


def list_mcp_servers() -> dict[str, Any]:
    """`GET /api/mcp/servers`: every configured server's static config plus a live connectivity
    check (tool count / ok / last error) for servers this project can actually reach -- an
    `inherit_cli_only` server (reachable only via a cloud CLI's own MCP config, docs/adr/006's
    point 3) is listed with its config but never dialed directly."""
    from eoa.mcp.registry import ping_server

    cfg = eoa_config.settings().mcp
    servers: list[dict[str, Any]] = []
    for server in cfg.servers:
        status: dict[str, Any] = {"tool_count": None, "ok": None, "error": None, "latency_ms": None, "tools": []}
        if cfg.enabled and server.enabled and not server.inherit_cli_only:
            try:
                result = ping_server(server)
                status = {
                    "tool_count": result.tool_count,
                    "ok": result.ok,
                    "error": result.error,
                    "latency_ms": result.latency_ms,
                    "tools": result.tools,
                }
            except Exception as exc:  # a broken server must never break the whole listing
                status["error"] = str(exc)[:300]
        servers.append(
            {
                "id": server.id,
                "label": server.label or server.id,
                "transport": server.transport,
                "enabled": server.enabled,
                "inherit_cli_only": server.inherit_cli_only,
                "key_configured": _mcp_key_configured(server),
                "key_env": list(server.env) or None,
                **status,
            }
        )
    return {"mcp_enabled": cfg.enabled, "servers": servers}


def ping_mcp_server(server_id: str) -> dict[str, Any]:
    """`POST /api/mcp/servers/{id}/ping` ("בדוק חיבור"): connect, list tools, disconnect."""
    from eoa.mcp.registry import ping_server

    cfg = eoa_config.settings().mcp
    server = cfg.server(server_id)
    if server is None:
        raise McpServerNotFound(server_id)
    if server.inherit_cli_only:
        return {
            "id": server.id,
            "ok": False,
            "error": "server is inherit_cli_only -- reachable only via a cloud CLI's own MCP config, not directly",
            "tool_count": 0,
            "tools": [],
            "latency_ms": 0,
        }
    result = ping_server(server)
    return {
        "id": result.id,
        "ok": result.ok,
        "error": result.error,
        "tool_count": result.tool_count,
        "tools": result.tools,
        "latency_ms": result.latency_ms,
    }


def summarize_mcp_calls(since_hours: int = 24) -> dict[str, Any]:
    """`GET /api/mcp/calls?since=24h`: thin pass-through to
    `eoa.memory.relational.summarize_mcp_calls`, matching `summarize_llm_calls` above."""
    from eoa.memory.relational import summarize_mcp_calls as _summarize

    return _summarize(since_hours=since_hours)
