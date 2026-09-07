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
    "corroborate",
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
        # A12 (מעקב טכנולוגי): additive, only ever non-null for domain == "tech_dev".
        "tech_maturity": row.get("tech_maturity"),
        "tech_actor_kind": row.get("tech_actor_kind"),
        "tech_readiness_note_he": row.get("tech_readiness_note_he"),
        # A13 (מיקוד תעשייה ישראלית, 2026-09-06): additive, see migration 0017 and
        # eoa.pipeline.israel_focus.
        "israel_relevance": row.get("israel_relevance"),
        "israel_reasons": row.get("israel_reasons") or [],
        # Cross-source corroboration (2026-09-07 user requirement): default "never checked" shape
        # per the frozen API contract -- `_attach_corroboration` overwrites this for every id that
        # actually has an `item_corroboration` row (see eoa.pipeline.corroboration).
        "corroboration": {"status": "unknown", "count": 0, "sources": [], "checked_at": None},
        # PL-backend (2026-09-07): additive, see migration 0027 and eoa.product_lines.tagging.
        "product_lines": row.get("product_lines") or [],
    }


def _attach_corroboration(cards: list[dict[str, Any]]) -> None:
    """Fill in the real ``corroboration`` object (in place) for every card whose id has an
    ``item_corroboration`` row -- cards with none keep ``_item_card``'s ``unknown`` default. A
    lookup failure (e.g. migration 0026 not yet applied on an older DB) must never break the item
    list/detail endpoints -- degrade to the default instead."""
    ids = [c["id"] for c in cards if c.get("id") is not None]
    if not ids:
        return
    try:
        from eoa.pipeline.corroboration import corroboration_payload_map

        found = corroboration_payload_map(ids)
    except Exception as exc:
        log.warning("corroboration_lookup_failed", error=str(exc)[:200])
        return
    for card in cards:
        payload = found.get(card.get("id"))
        if payload is not None:
            card["corroboration"] = payload


def list_items(
    *,
    level: str | None = None,
    domain: str | None = None,
    since: str | None = None,
    q: str | None = None,
    country: str | None = None,
    israel: bool = False,
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
        # Q5-10 (docs/qa/findings_Q5_r2.md): the Morning KPI cards (`_night_summary`) count items
        # by `COALESCE(fetched_at, created_at)` over a rolling last-24h window. This filter used to
        # key off `COALESCE(published_at, fetched_at)` instead -- a different field (and different
        # default) that made the KPI card's number and the feed count of the page it deep-links to
        # (`/feed?since=24h`, `/feed?level=red&since=24h`) disagree. Same expression on both sides
        # now so "23" on the card always means "23" items in the feed.
        where.append("COALESCE(i.fetched_at, i.created_at) >= %(since)s")
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
    # A13 (מיקוד תעשייה ישראלית, 2026-09-06): additive "🇮🇱 ישראל" feed chip -- items with a
    # meaningful deterministic Israeli-industry relevance score (eoa.pipeline.israel_focus).
    if israel:
        where.append("COALESCE(i.israel_relevance, 0) >= 0.5")
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
    cards = [_item_card(r) for r in rows]
    _attach_corroboration(cards)
    return total, cards


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
    _attach_corroboration([card])
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


def recompute_corroboration(item_id: int) -> dict[str, Any] | None:
    """`POST /api/items/{id}/corroborate` (2026-09-07 user requirement): re-runs
    `eoa.pipeline.corroboration.compute_for_item` for one item on demand, returning the same
    ``corroboration`` object shape the item list/detail endpoints embed. ``None`` if the item
    doesn't exist (the route maps that to 404)."""
    from eoa.pipeline.corroboration import compute_for_item, corroboration_payload

    record = compute_for_item(item_id)
    if record is None:
        return None
    return corroboration_payload(item_id)


class InvestigationAlreadyActive(Exception):
    """A deep_search job for this item is already queued/running; the route surfaces this as HTTP
    409 (Q5-3, docs/qa/findings_Q5_r1.md -- mirrors `RunAlreadyActive`/"הרץ עכשיו" above)."""

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        super().__init__(
            f"a deep_search job for this item is already {job.get('state')} (id={job.get('id')})"
        )


def investigate_item(item_id: int, question: str | None) -> dict[str, Any] | None:
    """U3/Q5-3 (docs/qa/findings_Q5_r1.md): the "I" feed shortcut used to fire-and-forget a new
    `deep_search` job every press -- a double-press queued two overlapping investigations of the
    same item, and an already-running or recently-finished investigation was never checked at all.
    Now: `None` if the item doesn't exist; raises `InvestigationAlreadyActive` if one is already
    queued/running for this item (-> HTTP 409); returns `{"job_id", "existing": True}` if a `done`
    investigation for this item finished within the last 24h (no new job enqueued); otherwise
    enqueues a new job and returns `{"job_id", "existing": False}`."""
    exists = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
    if exists is None:
        return None
    active = _fetchone(
        "SELECT * FROM jobs WHERE kind = 'deep_search' AND (payload->>'item_id')::bigint = %(item_id)s "
        "AND state IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
        {"item_id": item_id},
    )
    if active is not None:
        raise InvestigationAlreadyActive(_json_safe_row(active) or {})
    recent_done = _fetchone(
        "SELECT * FROM jobs WHERE kind = 'deep_search' AND (payload->>'item_id')::bigint = %(item_id)s "
        "AND state = 'done' AND finished_at >= now() - interval '24 hours' "
        "ORDER BY finished_at DESC LIMIT 1",
        {"item_id": item_id},
    )
    if recent_done is not None:
        return {"job_id": recent_done["id"], "existing": True}
    # 2026-09-06 (user report): the "I" shortcut sends no question, and rows with a null question
    # rendered as empty lines in the investigations list. Derive a default question from the item
    # and carry the item title in the payload so the list always has text.
    item = _fetchone("SELECT title, so_what_he FROM items WHERE id = %s", (item_id,)) or {}
    title = (item.get("title") or "").strip()
    if not (question or "").strip():
        question = default_investigation_question(title, item.get("so_what_he"))
    job_id = relational.enqueue_job(
        "deep_search", {"item_id": item_id, "question": question, "item_title": title}, priority=0
    )
    return {"job_id": job_id, "existing": False}


def default_investigation_question(title: str, so_what_he: str | None = None) -> str:
    """Self-contained Hebrew research question for an item-triggered investigation with no explicit
    question: verify the reported event and expand on it (parties, customer, amount, timeline,
    competitors and the implication for Israeli EO/IR industry).

    2026-09-06 (job 86 regression fix, point 3): the Israeli-industry clause is phrased as a
    SUBORDINATE closing addendum ("ובנוסף, בקצרה: ...") rather than folded into the main question's
    own "ומה המשמעות ל..." clause -- keeps it structurally last and clearly secondary, matching the
    same subordination applied to the A13 sub-question in `eoa.pipeline.triage`.
    """
    base = title or "הפריט"
    hint = (so_what_he or "").strip()
    hint_part = f" בהקשר: {hint[:160]}" if hint else ""
    return (
        f'אמת והרחב את הדיווח "{base}": מי הצדדים, הלקוח, היקף/סכום, לוח זמנים ומתחרים, '
        f"ומה המשמעות למוצרי EO/IR.{hint_part} ובנוסף, בקצרה: מה המשמעות לתעשייה הישראלית?"
    )


def start_investigation(question: str, item_id: int | None = None) -> int | None:
    """U12 "חקירה חדשה": launch a free-standing deep-search investigation from a typed question,
    not necessarily tied to a feed item (docs/REVIEW_2026-09-05.md U12)."""
    if not question or not question.strip():
        return None
    if item_id is not None:
        exists = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
        if exists is None:
            return None
    from eoa.pipeline.investigation_context import item_context_he_from_db

    payload: dict[str, Any] = {"item_id": item_id, "question": question}
    context_he = item_context_he_from_db(item_id)
    if context_he:
        payload["context_he"] = context_he
    return relational.enqueue_job("deep_search", payload, priority=0)


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
        # A13 (מיקוד תעשייה ישראלית, 2026-09-06): additive, see migration 0017 and
        # eoa.pipeline.israel_focus.score_and_persist_entity_israeli.
        "is_israeli": bool(row.get("is_israeli")),
    }


def list_entities(
    *,
    q: str | None = None,
    kind: str | None = None,
    country: str | None = None,
    watchlist: bool = False,
    israel: bool = False,
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
    if israel:
        where.append("e.is_israeli = true")
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


def _resolve_repo_path(raw: str) -> Path:
    """Resolve a stored report path. Rows written while the app ran in Docker carry the container
    prefix ``/app/...`` (ADR-004: the same tree is now ``REPO_ROOT``), so that prefix is remapped."""
    text = str(raw).replace("\\", "/")
    if text.startswith("/app/"):
        return REPO_ROOT / text[len("/app/") :]
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


# W14 (docs/REVIEW_2026-09-06_evening.md, user finding 2026-09-06 19:10): the reports list used to
# show nothing but "<kind> — <date>" -- e.g. "patent_survey — 06.09.2026" x7 for two different
# survey topics -- with no way to tell rows apart or preview what's inside before opening one.
# `_report_card` below adds, additively (every pre-existing field is unchanged):
#   - title_he / subject_he / built_at: a descriptive Hebrew title built from kind + subject
#     (territory for bd_territory, the survey topic for patent_survey -- never invented; daily/
#     weekly/monthly have no subject, so subject_he is None there) + build time, verified below
#     against every kind actually present in the live `reports` table.
#   - preview_he / source_count / qa_issues: a short content preview and counters read from the
#     already-rendered `path_md` file -- never re-derived from the LLM (docs/CONVENTIONS.md rule
#     5: never invent).
#   - group_key: kind+subject (kind+period for daily/weekly/monthly) so `list_reports` can mark
#     only the newest row per group as `is_latest`, letting the UI fold older re-runs of the same
#     survey/territory/period behind a "גרסאות קודמות" expander instead of listing all of them flat.
_REPORT_KIND_LABEL_HE = {
    "daily": "דוח יומי",
    "weekly": "דוח שבועי",
    "monthly": "דוח חודשי",
    "adhoc": "דוח אד-הוק",
    "bd_territory": "דוח פיתוח עסקי",
    "patent_survey": "סקר פטנטים",
}

# A small local Hebrew country-name table for `bd_territory`'s `subject_he`/`title_he`
# (docs/CONVENTIONS.md rule 6: a local copy, not `eoa.report.geography`'s private `_ALIASES`) --
# covers every territory code actually seen in the live `reports` table (US/IL/EU/GB/IN/KR/GR) plus
# a few obvious others. An unrecognized code falls back to the raw code itself -- never guessed.
_TERRITORY_LABEL_HE = {
    "US": 'ארה"ב',
    "IL": "ישראל",
    "GB": "בריטניה",
    "EU": "האיחוד האירופי",
    "DE": "גרמניה",
    "FR": "צרפת",
    "IT": "איטליה",
    "TR": "טורקיה",
    "KR": "קוריאה הדרומית",
    "IN": "הודו",
    "GR": "יוון",
    "JP": "יפן",
    "AU": "אוסטרליה",
    "CA": "קנדה",
    "NATO": 'נאט"ו',
    "UN": 'האו"ם',
}


def _territory_label_he(territory: str | None) -> str | None:
    if not territory:
        return None
    return _TERRITORY_LABEL_HE.get(territory.upper(), territory)


def _report_subject_he(kind: str | None, territory: str | None, qa_report: dict[str, Any]) -> str | None:
    if kind == "patent_survey":
        topic = qa_report.get("topic")
        return topic.strip() if isinstance(topic, str) and topic.strip() else None
    if kind == "bd_territory":
        return _territory_label_he(territory)
    return None


def _report_title_he(
    kind: str | None,
    subject_he: str | None,
    territory: str | None,
    period_start: Any,
    period_end: Any,
    created_at: Any,
) -> str:
    label = _REPORT_KIND_LABEL_HE.get(kind or "", kind or "דוח")
    built = created_at.strftime("%d.%m %H:%M") if hasattr(created_at, "strftime") else None
    if kind == "daily":
        anchor = period_end if hasattr(period_end, "strftime") else period_start
        date_str = anchor.strftime("%d.%m") if hasattr(anchor, "strftime") else None
        return f"{label} — {date_str}" if date_str else label
    if kind == "weekly":
        anchor = period_end if hasattr(period_end, "isocalendar") else period_start
        week_no = anchor.isocalendar()[1] if hasattr(anchor, "isocalendar") else None
        start_str = period_start.strftime("%d.%m") if hasattr(period_start, "strftime") else "?"
        end_str = period_end.strftime("%d.%m") if hasattr(period_end, "strftime") else "?"
        week_part = f"שבוע {week_no} " if week_no else ""
        return f"{label} — {week_part}({start_str}–{end_str})"
    if kind == "monthly":
        anchor = period_end if hasattr(period_end, "strftime") else period_start
        month_str = anchor.strftime("%m.%Y") if hasattr(anchor, "strftime") else None
        return f"{label} — {month_str}" if month_str else label
    if kind == "bd_territory":
        subject = subject_he or _territory_label_he(territory) or "—"
        return f"{label} — {subject} — {built}" if built else f"{label} — {subject}"
    if kind == "patent_survey":
        subject = subject_he or "נושא לא ידוע"
        return f"{label}: {subject} — {built}" if built else f"{label}: {subject}"
    # adhoc / any future kind -- still descriptive rather than a bare kind string.
    return f"{label} — {built}" if built else label


def _report_group_key(row: dict[str, Any], subject_he: str | None) -> str:
    kind = row.get("kind") or ""
    if kind == "patent_survey":
        return f"patent_survey:{(subject_he or '').strip().lower()}"
    if kind == "bd_territory":
        return f"bd_territory:{(row.get('territory') or '').strip().upper()}"
    start, end = row.get("period_start"), row.get("period_end")
    start_s = start.isoformat() if hasattr(start, "isoformat") else str(start)
    end_s = end.isoformat() if hasattr(end, "isoformat") else str(end)
    return f"{kind}:{start_s}_{end_s}"


# --- content preview / counters, read from the rendered report file ------------------------

# The executive summary always sits in the file's first heading section (right after the title/
# date lines) -- 6 KB comfortably covers it even for a verbose patent-survey exec summary, without
# reading the whole (sometimes tens-of-KB) report just to show two sentences in a list row.
_REPORT_PREVIEW_READ_BYTES = 6 * 1024
# `source_count` needs the sources appendix, which sits at the *end* of the file. Every report this
# project renders is a few KB to low tens-of-KB of Markdown (the largest observed live is ~42 KB),
# so this cap is generous enough to reach the appendix in practice while still bounding worst-case
# I/O -- and it is only ever paid once per report *version*, see `_REPORT_PREVIEW_CACHE` below.
_REPORT_FILE_READ_CAP = 128 * 1024

_MD_HEADING_RE = re.compile(r"^##\s+.+$", re.MULTILINE)
_MD_CITED_MARKER_RE = re.compile(r"\[\d+\](?:\(#src-\d+\))?")
_MD_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_APPENDIX_ROW_RE = re.compile(r'<a id="src-\d+">')
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_EXEC_SUMMARY_HEADING_RE = re.compile(r"^##\s*תקציר מנהלים\s*$", re.MULTILINE)

# Keyed by (report_id, created_at.isoformat()) -- a `reports` row is immutable once written (a
# re-run always inserts a brand new row, never an UPDATE), so this cache never goes stale.
_REPORT_PREVIEW_CACHE: dict[tuple[int, str], dict[str, Any]] = {}


def _clean_md_inline(text: str) -> str:
    """Strip citation markers / bold / link syntax from a chunk of report Markdown, leaving plain
    Hebrew prose. Used only for the reports list's short content preview -- the actual report body
    (`ReportBody.tsx`) always renders from `path_html`'s real, clickable citation chips."""
    text = _MD_CITED_MARKER_RE.sub("", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _report_preview_from_text(md_text: str) -> str | None:
    """First two sentences of the "תקציר מנהלים" (executive summary) section, as plain Hebrew
    text. Blockquoted asides (e.g. a patent survey's per-patent "התקדמות פטנט [n]" lines) are
    dropped -- they are supplementary detail, not the summary itself."""
    m = _EXEC_SUMMARY_HEADING_RE.search(md_text)
    if not m:
        return None
    rest = md_text[m.end() :]
    next_heading = _MD_HEADING_RE.search(rest)
    section = rest[: next_heading.start()] if next_heading else rest
    lines = [ln for ln in section.splitlines() if ln.strip() and not ln.strip().startswith(">")]
    text = _clean_md_inline(" ".join(lines))
    if not text:
        return None
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    return " ".join(sentences[:2]) or None


def _report_file_stats(report_id: int, created_at: Any, path_md: str | None) -> dict[str, Any]:
    """``{"preview_he", "source_count"}`` for one report, cached per ``(id, created_at)``."""
    cache_key = (report_id, created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at))
    cached = _REPORT_PREVIEW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    result: dict[str, Any] = {"preview_he": None, "source_count": 0}
    if path_md:
        try:
            p = _resolve_repo_path(path_md)
            if p.exists():
                with p.open(encoding="utf-8", errors="replace") as f:
                    text = f.read(_REPORT_FILE_READ_CAP)
                preview = _report_preview_from_text(text[:_REPORT_PREVIEW_READ_BYTES])
                result["preview_he"] = preview if preview is not None else _report_preview_from_text(text)
                result["source_count"] = len(_MD_APPENDIX_ROW_RE.findall(text))
        except OSError as exc:
            log.warning("report.preview_read_failed", report_id=report_id, error=str(exc))
    _REPORT_PREVIEW_CACHE[cache_key] = result
    return result


def _report_card(row: dict[str, Any]) -> dict[str, Any]:
    included = row.get("items_included") or []
    kind = row.get("kind")
    territory = row.get("territory")
    qa_report = row.get("qa_report") or {}
    subject_he = _report_subject_he(kind, territory, qa_report)
    created_at = row.get("created_at")
    stats = _report_file_stats(row["id"], created_at, row.get("path_md"))
    errors = qa_report.get("errors") or []
    return {
        "id": row["id"],
        "kind": kind,
        "period_start": row.get("period_start"),
        "period_end": row.get("period_end"),
        "path_docx": row.get("path_docx"),
        "path_md": row.get("path_md"),
        "path_html": row.get("path_html"),
        "qa_passed": row.get("qa_passed"),
        "created_at": created_at,
        "headline_count": len(included),
        # A11: only populated for kind='bd_territory' -- None for every other report kind.
        "territory": territory,
        # W14 additive fields (see module note above _REPORT_KIND_LABEL_HE).
        "title_he": _report_title_he(
            kind, subject_he, territory, row.get("period_start"), row.get("period_end"), created_at
        ),
        "subject_he": subject_he,
        "built_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        "preview_he": stats["preview_he"],
        "source_count": stats["source_count"],
        "qa_issues": len(errors),
        "group_key": _report_group_key(row, subject_he),
    }


def list_reports(*, kind: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    where = "kind = %(kind)s" if kind else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200)}
    if kind:
        params["kind"] = kind
    rows = _fetchall(f"SELECT * FROM reports WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
    cards = [_report_card(r) for r in rows]
    # W14 point 3: the newest row per group_key (rows already arrive created_at DESC) is the one
    # the UI shows by default; every older row in the same group is folded behind its expander.
    seen_groups: set[str] = set()
    for card in cards:
        key = card["group_key"]
        card["is_latest"] = key not in seen_groups
        seen_groups.add(key)
    return cards


def _report_group_where(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """SQL predicate (without the ``created_at`` bound) matching every row in ``row``'s own
    version group -- used by :func:`get_report` to answer "is this the latest version" for a
    single report, mirroring :func:`_report_group_key`'s own grouping rules."""
    kind = row.get("kind")
    if kind == "patent_survey":
        topic = (row.get("qa_report") or {}).get("topic") or ""
        return "kind = 'patent_survey' AND COALESCE(qa_report->>'topic', '') = %(topic)s", {"topic": topic}
    if kind == "bd_territory":
        return "kind = 'bd_territory' AND COALESCE(territory, '') = %(territory)s", {
            "territory": row.get("territory") or ""
        }
    return (
        "kind = %(kind)s AND period_start IS NOT DISTINCT FROM %(start)s "
        "AND period_end IS NOT DISTINCT FROM %(end)s",
        {"kind": kind, "start": row.get("period_start"), "end": row.get("period_end")},
    )


def _is_latest_report(row: dict[str, Any]) -> bool:
    where_sql, params = _report_group_where(row)
    params["created_at"] = row["created_at"]
    newer = _fetchone(
        f"SELECT 1 AS x FROM reports WHERE {where_sql} AND created_at > %(created_at)s LIMIT 1", params
    )
    return newer is None


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
    card["is_latest"] = _is_latest_report(row)
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
        db_rows = _fetchall(
            "SELECT id, title, url FROM items WHERE id = ANY(%(ids)s)", {"ids": items_included}
        )
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
                "item_title": payload.get("item_title"),
                "error": j.get("error"),
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


_ASK_ITEM_FIELDS = (
    "i.id, i.title, i.url, i.clean_text, i.summary_he, i.key_facts, i.security_status, "
    "i.domain, i.level, i.report_kind, i.entities_mentioned, s.name AS source_name"
)
# U11 (2026-09-06 answer-format rewrite): the sources footer needs a triage level + a
# human-readable source name (not just the item's own scope-taxonomy `domain`), so every
# `_ASK_ITEM_FIELDS` query now joins `sources` the same way `services.get_item`/`list_items` do.
# Round 2 (docs/qa/loop/round_2_chat_fixes.md): also carries `report_kind` (verified_report /
# company_pr / academic / tender / patent / regulatory / science, config/taxonomy.yaml) and
# `entities_mentioned` so `ask_build_messages` can (a) label each source's kind for the model --
# an arXiv paper must never be presented as a government RFI, D5 finding Q4/Q7 -- and (b) inject
# the canonical entity names actually present in the retrieved corpus, to guard against the model
# substituting a similar-sounding system for the real one (D5 Q2: Iron Beam <-> Iron Dome/Tamir).
_ASK_ITEM_JOIN = "items i LEFT JOIN sources s ON s.id = i.source_id"

# Round 2 D5 fix (docs/qa/loop/round_2_chat_fixes.md): human-readable Hebrew/English source-type
# labels for `items.report_kind` (config/taxonomy.yaml `report_kinds`), shown in the `[n]` source
# header the model sees -- so it states plainly whether a source is a verified report, a company
# PR, an academic paper, a tender/RFI, a patent, etc., rather than presenting one as another.
_REPORT_KIND_LABELS: dict[str, str] = {
    "verified_report": "דיווח מאומת",
    "company_pr": "הודעת חברה (PR)",
    "rumor_speculation": "שמועה/ספקולציה",
    "academic": "מאמר אקדמי/arXiv",
    "tender": "מכרז/RFI ממשלתי",
    "patent": "פטנט",
    "regulatory": "רגולציה",
    "science": "מדע/מחקר",
}


def _report_kind_label(report_kind: str | None) -> str:
    if not report_kind:
        return "לא מסווג"
    return _REPORT_KIND_LABELS.get(report_kind, report_kind)


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
        row = _fetchone(f"SELECT {_ASK_ITEM_FIELDS} FROM {_ASK_ITEM_JOIN} WHERE i.id = %s", (iid,))
        if row:
            context[row["id"]] = row

    for eid in context_entity_ids or []:
        erow = _fetchone("SELECT name FROM entities WHERE id = %s", (eid,))
        if not erow:
            continue
        rows = _fetchall(
            f"SELECT {_ASK_ITEM_FIELDS} FROM {_ASK_ITEM_JOIN} "
            "WHERE %s = ANY(COALESCE(i.entities_mentioned, '{}')) "
            "ORDER BY COALESCE(i.published_at, i.fetched_at) DESC LIMIT 5",
            (erow["name"],),
        )
        for r in rows:
            if r["id"] not in context:
                context.setdefault(r["id"], r)

    # hybrid retrieval 1/2: exact-token keyword match (tried first so it always outranks vector noise).
    for token in _rare_tokens(question):
        rows = _fetchall(
            f"SELECT {_ASK_ITEM_FIELDS} FROM {_ASK_ITEM_JOIN} "
            "WHERE i.title ILIKE %(t)s OR i.clean_text ILIKE %(t)s "
            "ORDER BY COALESCE(i.published_at, i.fetched_at) DESC LIMIT 5",
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
            row = _fetchone(f"SELECT {_ASK_ITEM_FIELDS} FROM {_ASK_ITEM_JOIN} WHERE i.id = %s", (item_id,))
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

    U11 (2026-09-06 answer-format rewrite): the system prompt also now demands ONE synthesized
    analyst answer -- direct answer, key facts (each [n]-cited), "הערכת האנליסט", then gaps --
    never the old per-source "מקור 1 / הערת איכות / ציטוט מדויק" dump (`ask_answer_format.md`
    carries the exact rules and the `===SOURCES_JSON===` sentinel format for the model's optional
    per-source relevance notes; `ask.py`'s SSE generator peels that trailing block off the stream
    before it ever reaches the client, see `_split_sources_json`/`_parse_source_notes` there).
    Citations here additionally carry `level`/`source_name` (now selected by `ask_retrieve`) so
    the UI's sources footer never needs a second round-trip just to render a badge.

    Round 2 (docs/qa/loop/round_2_chat_fixes.md, D5 conflation/hallucination fixes): the system
    prompt is further extended with (a) the canonical entity names actually present in the
    retrieved corpus -- so the model cannot silently substitute a similar-sounding system for the
    real one (e.g. מגן אור/Iron Beam vs כיפת ברזל/Iron Dome, live-found in Q2) -- (b) each `[n]`
    source block now states its `report_kind` in plain Hebrew (verified report / company PR /
    academic paper / tender-RFI / ...), so an arXiv paper can never be read back as a government
    RFI (live-found in Q4/Q7) -- and (c) an explicit compound-premise-verification rule: root-caused
    live against golden Q3 ("עסקת ה-LORA היוונית (Greece)") after `ask.py`'s deterministic
    anchor guard (see its own docstrings) still couldn't reliably catch this case -- the DB
    genuinely holds real LORA items (about Germany) AND a real Greek air-defense item (unrelated to
    LORA) side by side in the same retrieval; the model was never missing LORA context, it just
    silently answered only from the more prominent Greek item and synthesized a "LORA deal with
    Greece" story neither source actually supports. (c) tells the model to verify that a source
    actually connects the question's combined terms before answering them as one story, and to
    name the gap explicitly (what was found separately) instead of merging unrelated sources.
    """
    from eoa.llm import prompts
    from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, wrap_data

    system = prompts.render("system_analyst", data_guard=DATA_GUARD_SYSTEM)

    ordered = sorted(retrieved, key=lambda r: 0 if r.get("_is_context") else 1)

    canonical_entities: list[str] = []
    seen_entities: set[str] = set()
    for row in ordered:
        for name in row.get("entities_mentioned") or []:
            key = (name or "").strip()
            if key and key.casefold() not in seen_entities:
                seen_entities.add(key.casefold())
                canonical_entities.append(key)
    if canonical_entities:
        system += (
            "\n\nשמות הישויות/המערכות הבאים מופיעים במפורש במקורות שסופקו לך בשיחה זו -- "
            "השתמש בשמות הישויות **בדיוק** כפי שהם מופיעים במקורות; אסור להחליף מערכת במערכת "
            "דומה (למשל מגן אור ≠ כיפת ברזל; Iron Beam ≠ Iron Dome/Tamir). רשימת השמות: "
            + ", ".join(canonical_entities)
        )

    system += (
        "\n\nענה על שאלת המשתמש. סדר עדיפויות: (1) פריטים המסומנים 'הקשר מצורף' -- אלה צורפו "
        "לשיחה במפורש על ידי המשתמש; אם הם רלוונטיים לשאלה, חובה להתבסס עליהם ולצטט אותם ראשונים, "
        "גם אם השם/המונח שבשאלה אינו מוכר לך ממקורות אחרים. (2) פריטים המסומנים 'מהמאגר' -- נמצאו "
        "על ידי חיפוש ויש להשתמש בהם כתמיכה נוספת. כל משפט עובדתי המבוסס על פריט חייב לסמן אותו "
        "ב-[n]. אם התשובה אינה נמצאת באף פריט מסופק, מותר להיעזר בידע כללי -- אך יש לציין זאת "
        "במפורש ('בהתבסס על ידע כללי, לא מהמאגר'), ולעולם לא להציג ידע כללי כאילו מקורו בפריטים. "
        "אם גם בפריטים וגם בידע הכללי אין מענה -- כתוב זאת בפירוש ואל תמציא. שים לב לסוג כל מקור "
        "(מצוין ליד מספרו) -- לעולם אל תציג מאמר אקדמי כאילו הוא מכרז/RFI ממשלתי או להיפך. "
        "אם השאלה משלבת כמה מונחים ספציפיים יחד (למשל שם מערכת/תוכנית מסוימת יחד עם מדינה או "
        "גורם מסוים) -- ודא שקיים מקור המקשר ביניהם בפועל לפני שאתה עונה עליהם כסיפור אחד. אם "
        "המונחים מופיעים רק במקורות נפרדים ובלתי-קשורים (למשל מקור אחד עוסק במערכת X מול מדינה "
        "א', ומקור אחר עוסק בעסקה שונה לגמרי מול מדינה ב') -- אסור לשלב אותם לכדי סיפור אחד "
        "מומצא; יש לציין זאת במפורש כפער ('לא נמצא מקור המקשר בין X למדינה ב'; נמצאו בנפרד: ...') "
        "ולפרט מה כן נמצא בכל מקור בנפרד.\n\n" + prompts.render("ask_answer_format")
    )

    citations: list[dict[str, Any]] = []
    blocks: list[str] = []
    for i, row in enumerate(ordered, start=1):
        is_context = bool(row.get("_is_context"))
        kind_label = _report_kind_label(row.get("report_kind"))
        if is_context:
            key_facts = row.get("key_facts") or []
            facts_block = "\nעובדות מפתח:\n" + "\n".join(f"- {f}" for f in key_facts) if key_facts else ""
            body = (
                f"תקציר: {row.get('summary_he') or ''}{facts_block}\n\n"
                f"טקסט מלא (קטע):\n{(row.get('clean_text') or '')[:1500]}"
            )
            label = f'הקשר מצורף (צוין ע"י המשתמש) | סוג מקור: {kind_label}'
        else:
            body = (row.get("clean_text") or row.get("summary_he") or "")[:4000]
            label = f"מהמאגר (אוחזר לפי השאלה) | סוג מקור: {kind_label}"
        blocks.append(
            f"[{i}] ({label}) {row.get('title') or ''}\n{wrap_data(body, row['id'], src=row.get('url') or '')}"
        )
        citations.append(
            {
                "n": i,
                "item_id": row["id"],
                "title": row.get("title"),
                "url": row.get("url"),
                "level": row.get("level"),
                "source_name": row.get("source_name"),
                "report_kind": row.get("report_kind"),
            }
        )
    context_block = "\n\n".join(blocks) if blocks else "(לא נמצאו פריטים רלוונטיים)"

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in history or []:
        role, content = turn.get("role"), turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": f"שאלה: {question}\n\nמקורות:\n{context_block}"})
    return messages, citations


# Round 2 (docs/qa/loop/round_2_chat_fixes.md, D5 P2 citation fix): live-verified 2026-09-06 that
# 3 of 5 cleanly-completed chat answers carried zero inline `[n]` citations despite a populated
# sources array. `ask.py`'s SSE generator runs this as a one-shot, non-streamed corrective pass
# (short `num_predict`) when the finished answer has no `[n]` at all but at least one source was
# retrieved -- reusing the exact same system+sources context the original answer saw, so the
# rewrite has everything it needs to attach citations without re-retrieving anything.
_CITATION_REPAIR_INSTRUCTION = (
    "התשובה שכתבת למעלה אינה מכילה אף סימון [n] אחד, למרות שסופקו לך מקורות ממוספרים. כתוב "
    "מחדש בדיוק את אותה תשובה -- זהה בתוכן, במבנה ובאורך -- אך הוסף סימון [n] בסוף כל משפט "
    "עובדתי המבוסס על אחד המקורות שסופקו למעלה (לפי מספורם [1]/[2]/וכו'). אם משפט מסוים אינו "
    "מבוסס על אף מקור (למשל פרשנות אנליטית), השאר אותו ללא [n]. הסימון [n] חייב להיות מספר ממשי "
    "תואם מקור (למשל [1] או [3]) -- לעולם לא `[n]`, `[n=5]`, `{n}` או placeholder אחר לא ממומש. "
    "אל תוסיף הקדמות, הערות או הסברים -- החזר אך ורק את גוף התשובה המתוקן."
)


def ask_citation_repair_messages(messages: list[dict[str, Any]], answer_text: str) -> list[dict[str, Any]]:
    """Build the one-shot corrective-pass messages for the zero-citation guard above: the exact
    system+history+sources messages the original answer was built from, plus that answer as an
    assistant turn, plus an instruction to rewrite it with `[n]` citations attached."""
    return [
        *messages,
        {"role": "assistant", "content": answer_text},
        {"role": "user", "content": _CITATION_REPAIR_INSTRUCTION},
    ]


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
        # W2b (additive): relevance_score (0-1, self-tuning signal) + intake
        # ('candidate'/'accepted'/'rejected-by-user') -- see eoa.tenders.scan/feedback.
        "relevance_score": row.get("relevance_score"),
        "intake": row.get("intake"),
        "matched_terms": row.get("matched_terms") or [],
        "entities": row.get("entities") or [],
        "status": row.get("status"),
        "item_id": row.get("item_id"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        # PL-backend (2026-09-07): additive, see migration 0027 and eoa.product_lines.tagging.
        "product_lines": row.get("product_lines") or [],
    }


# A row can only reach the DB with relevance <= 2 if it was inserted before the LLM-relevance
# gate existed (eoa.tenders.scan.scan_tenders) or the LLM was never available to score it down --
# the API's default view hides these rather than trusting every historical/ungated row.
DEFAULT_MIN_RELEVANCE = 3

# F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): the tenders board's default view is "open and
# recently-seen unknown" tenders only -- an explicit `status=` (or `include_closed`/
# `include_archived`) is required to see anything else. `awarded` is intentionally excluded from
# the default set too: it means "already decided", not "worth an analyst's attention as an open
# opportunity" (see eoa.tenders.scan's F24 gate, which now rejects newly-scanned awarded notices
# outright -- any 'awarded' row left in the DB predates that fix).
DEFAULT_STATUSES: tuple[str, ...] = ("open", "unknown")
DEFAULT_SINCE_DAYS = 90


def list_tenders(
    *,
    status: str | None = None,
    country: str | None = None,
    q: str | None = None,
    min_relevance: int | None = DEFAULT_MIN_RELEVANCE,
    since_days: int | None = None,
    include_closed: bool = False,
    include_archived: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """F24: the default view (no explicit ``status``) is ``DEFAULT_STATUSES`` ('open'/'unknown')
    within the last ``since_days`` days (by ``COALESCE(deadline, published_at, created_at)`` --
    whichever date the row actually has); ``include_closed``/``include_archived`` widen that
    default set. An explicit ``status=`` always wins outright and ignores ``since_days``/
    ``include_*`` -- an operator who asks for e.g. ``status=closed`` wants every closed tender, not
    just recent ones.

    Q5-11 (docs/qa/findings_Q5_r2.md): ``since_days`` here is ``None`` unless the caller passed one
    explicitly (the route's own default is likewise ``None``, not ``DEFAULT_SINCE_DAYS`` -- see
    ``routes/tenders.py``). Closed/archived tenders are old by definition, so when the caller asks
    to see them (``include_closed``/``include_archived``) *without* also pinning an explicit
    ``since_days``, the 90-day window is lifted entirely rather than silently re-hiding the very
    rows the toggle was meant to reveal. An explicit ``since_days`` (whatever its value) always
    applies, include_* or not -- the operator asked for a specific window and gets it.

    Returns both the (possibly capped) tender list AND a status -> count summary (honoring
    ``country``/``q`` but not the status/since_days/include_* narrowing) for the UI's header chips,
    since those need the true totals regardless of what the list itself shows.

    W2b: every notice that clears the two-signal vocabulary gate is now stored (open intake, see
    ``eoa.tenders.scan``), carrying ``intake`` -- ``'candidate'`` (below the learned relevance
    threshold) or ``'accepted'`` (at/above it) both show here, ``'accepted'`` sorted first within
    each status tier, so an operator scanning the board sees the confident rows before the
    uncertain ones; only ``'rejected-by-user'`` (an explicit 👎) is hidden, always, per the user's
    own requirement."""
    where = ["intake != 'rejected-by-user'"]
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}

    if status:
        where.append("status = %(status)s")
        params["status"] = status
    else:
        statuses = list(DEFAULT_STATUSES)
        if include_closed:
            statuses.append("closed")
        if include_archived:
            statuses.append("archived")
        where.append("status = ANY(%(statuses)s)")
        params["statuses"] = statuses
        effective_since_days = since_days
        if effective_since_days is None and not (include_closed or include_archived):
            effective_since_days = DEFAULT_SINCE_DAYS
        if effective_since_days is not None:
            where.append(
                "COALESCE(deadline, published_at::date, created_at::date) >= (CURRENT_DATE - %(since_days)s)"
            )
            params["since_days"] = effective_since_days

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
    # Round-3 (D9 finding 4c, docs/qa/loop/round_1_judge.md): open rows first (earliest deadline
    # first), then unknown rows (most-recently-published first, "unknown-recent"), then anything
    # else (awarded/closed/archived, only reachable via an explicit `status=`/`include_*`) --
    # closed/archived stay hidden from the default view entirely (see the WHERE-clause branch
    # above), this ordering only matters once a caller explicitly asks to see them too.
    rows = _fetchall(
        f"SELECT * FROM tenders WHERE {where_sql} "
        "ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'unknown' THEN 1 "
        "WHEN 'awarded' THEN 2 WHEN 'closed' THEN 3 WHEN 'archived' THEN 4 ELSE 5 END, "
        "CASE WHEN intake = 'accepted' THEN 0 ELSE 1 END, "
        "CASE WHEN status = 'open' THEN deadline END ASC NULLS LAST, "
        "CASE WHEN status = 'unknown' THEN published_at END DESC NULLS LAST, "
        "relevance DESC NULLS LAST, id DESC LIMIT %(limit)s",
        params,
    )

    count_where = ["intake != 'rejected-by-user'"]
    count_params: dict[str, Any] = {}
    if country:
        count_where.append("country = %(country)s")
        count_params["country"] = country
    if q:
        count_where.append("(title ILIKE %(q)s OR summary_he ILIKE %(q)s OR agency ILIKE %(q)s)")
        count_params["q"] = f"%{q}%"
    count_rows = _fetchall(
        f"SELECT status, count(*) AS n FROM tenders WHERE {' AND '.join(count_where)} GROUP BY status",
        count_params,
    )
    counts = {r["status"]: r["n"] for r in count_rows}

    return {"tenders": [_tender_card(r) for r in rows], "counts": counts}


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
# A15 (docs/TENDER_PORTALS.md): source coverage panel -- read-only, config + DB, no writes.
# --------------------------------------------------------------------------


def _tender_source_status(kind: str, verified: bool, needs_key_env_var: str | None) -> str:
    """One of ``"integrated_keyless"`` (a real, currently-polled, keyless integration --
    ``kind: rss``/``api_json`` with ``verified: true``, or ``kind: search`` which always rides the
    already-verified SearXNG client), ``"waiting_for_key"`` (``verified: false`` with a known env
    var name -- see ``TenderSource.needs_key_env_var``), or ``"not_integrated"`` (documented dead
    end/deliberately out of scope: bot-blocked, wrong endpoint, JS-hydrated shell, ...).

    ``kind: html`` is always ``"not_integrated"`` regardless of ``verified`` -- ``eoa.tenders.scan``
    unconditionally skips every ``html`` source (``if src.kind == "html": continue``), so a
    ``verified: true`` ``html`` entry (e.g. ``canada_buys`` -- the page itself loads fine, it's just
    never scraped directly) still contributes nothing to ingestion and must not be counted as an
    active integration."""
    if kind == "html":
        return "not_integrated"
    if kind == "search":
        return "integrated_keyless"
    if verified:
        return "integrated_keyless"
    if needs_key_env_var:
        return "waiting_for_key"
    return "not_integrated"


def tender_source_coverage() -> dict[str, Any]:
    """A15: per-region source coverage for the tenders page's "כיסוי מקורות" panel -- combines
    ``config/tenders.yaml`` (via ``eoa.tenders.scan.load_tender_sources``, the portal registry) with
    the ``tenders`` table (notices actually stored per ``source`` id, and the most recent one's
    ``created_at`` as a proxy "last successful fetch" -- there is no separate per-source fetch-log
    table, so a source that has never yet produced a stored notice shows ``last_fetch_at: null``
    even if it has been polled/scanned many times with zero matches).

    Read-only; never raises on a config load hiccup (falls back to an empty source list, same
    "a broken section never breaks the page" spirit as the rest of this module) -- the DB query
    itself is allowed to raise (a genuine DB outage should surface as a 500, same as every other
    endpoint in this module).

    W2b (additive): each source also carries ``priority_decrement`` -- the self-tuning scan-order
    hint from ``eoa.tenders.feedback.get_source_priorities`` (0 for a source that hasn't earned
    one; never disables a source, only nudges ``scan_tenders``'s pass order later for it).
    """
    from eoa.tenders.feedback import get_source_priorities
    from eoa.tenders.scan import TenderSource, load_tender_sources

    try:
        sources: list[TenderSource] = load_tender_sources()
    except Exception as exc:  # pragma: no cover -- config load should never actually fail in prod
        log.warning("tender_source_coverage_config_load_failed", error=str(exc)[:200])
        sources = []

    stats_rows = _fetchall(
        "SELECT source, count(*) AS n, max(created_at) AS last_created_at FROM tenders GROUP BY source"
    )
    stats_by_source = {r["source"]: r for r in stats_rows}
    try:
        priorities = get_source_priorities()
    except Exception as exc:  # pragma: no cover -- defensive, get_source_priorities already catches
        log.debug("tender_source_priorities_unavailable", error=str(exc)[:200])
        priorities = {}

    by_region: dict[str, list[dict[str, Any]]] = {}
    totals = {"integrated_keyless": 0, "waiting_for_key": 0, "search_only": 0, "not_integrated": 0}
    for s in sources:
        status = _tender_source_status(s.kind, s.verified, s.needs_key_env_var)
        # "search_only" is reported as its own bucket in the summary totals (coordinator
        # requirement) even though it's a sub-case of "integrated_keyless" for the per-source
        # `status` field above (a search source IS keyless/integrated, just via SearXNG rather than
        # a direct feed) -- avoids double-counting while still giving the UI/report the distinction.
        totals["search_only" if s.kind == "search" else status] += 1
        row = stats_by_source.get(s.id)
        by_region.setdefault(s.country, []).append(
            {
                "id": s.id,
                "name": s.name,
                "kind": s.kind,
                "country": s.country,
                "status": status,
                "verified": s.verified,
                "needs_key_env_var": s.needs_key_env_var,
                "notices_stored": (row["n"] if row else 0),
                "last_fetch_at": (row["last_created_at"] if row else None),
                "priority_decrement": priorities.get(s.id, 0),
            }
        )

    regions = [
        {"region": region, "sources": sorted(rows, key=lambda r: r["id"])}
        for region, rows in sorted(by_region.items())
    ]
    return {"regions": regions, "totals": totals, "source_count": len(sources)}


# --------------------------------------------------------------------------
# W2b: tender relevance feedback (eoa.tenders.feedback) -- the self-tuning loop
# --------------------------------------------------------------------------


def record_tender_feedback(tender_id: int, verdict: str, reason: str | None) -> dict[str, Any] | None:
    """``POST /api/tenders/{id}/feedback``: records one 👍/👎, flips the tender's own ``intake``,
    and (best-effort, inside ``eoa.tenders.feedback.record_feedback`` itself) recomputes the
    learned relevance threshold + this tender's source's scan priority. Returns ``None`` when
    ``tender_id`` doesn't exist (the route turns that into a 404)."""
    from eoa.tenders.feedback import record_feedback

    return record_feedback(tender_id, verdict, reason)  # type: ignore[arg-type]


def list_tender_feedback(tender_id: int) -> list[dict[str, Any]]:
    """``GET /api/tenders/{id}/feedback``: the full 👍/👎 history for one tender, most recent first."""
    from eoa.tenders.feedback import list_feedback_for_tender

    return list_feedback_for_tender(tender_id)


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


# W20 (docs/REVIEW_2026-09-06_evening.md): the jobs table only ever showed the generic `kind`
# (e.g. "deep_search" for every deep search, indistinguishable from one another) -- `subject_he`
# reads the same per-kind identifying field the job actually enqueued with (never invented, per
# docs/CONVENTIONS.md rule 5): `deep_search`'s own question, `bd_report`'s territory (rendered
# through the same `_territory_label_he` table the reports list already uses), `patent_survey`'s
# topic, and for the period-based runs (no per-job subject field at all) the job's own
# `created_at` date so at least two jobs of the same kind on different days are distinguishable.
_JOB_SUBJECT_MAX_LEN = 80
_JOB_DATE_SUBJECT_KINDS = ("daily_run", "weekly_run", "monthly_run", "ingest", "report")


def _job_subject_he(kind: str | None, payload: dict[str, Any] | None, created_at: Any) -> str | None:
    payload = payload or {}
    if kind == "deep_search":
        question = payload.get("question")
        question = question.strip() if isinstance(question, str) else ""
        return question[:_JOB_SUBJECT_MAX_LEN] if question else None
    if kind == "bd_report":
        territory = payload.get("territory")
        return _territory_label_he(territory) if territory else None
    if kind == "patent_survey":
        topic = payload.get("topic")
        return topic.strip() if isinstance(topic, str) and topic.strip() else None
    if kind in _JOB_DATE_SUBJECT_KINDS:
        return created_at.strftime("%d.%m.%Y") if hasattr(created_at, "strftime") else None
    return None


def _job_card(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["subject_he"] = _job_subject_he(row.get("kind"), row.get("payload"), row.get("created_at"))
    return row


def list_jobs(*, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    where = "state = %(state)s" if state else "1 = 1"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 500)}
    if state:
        params["state"] = state
    rows = _fetchall(f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC LIMIT %(limit)s", params)
    return [_job_card(row) for row in rows]


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
_PRIMARY_RUN_KINDS = (
    "daily_run",
    "weekly_run",
    "monthly_run",
    "report",
    "ingest",
    "tender_scan",
    "conference_scan",
)


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
    dumped = yaml.safe_dump(
        {"chains": payload}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
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
        status: dict[str, Any] = {
            "tool_count": None,
            "ok": None,
            "error": None,
            "latency_ms": None,
            "tools": [],
        }
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
    """`POST /api/mcp/servers/{id}/ping` ("בדוק חיבור"): connect, list tools, disconnect.

    Q2-16 (2026-09-06): must respect the global `mcp.enabled` kill switch exactly like
    `list_mcp_servers` above (`if cfg.enabled and server.enabled and not server.inherit_cli_only`)
    -- the old version skipped that check and dialed the server regardless of the switch, the one
    path in this module that could still reach an MCP server after an operator had globally
    disabled MCP.
    """
    from eoa.mcp.registry import ping_server

    cfg = eoa_config.settings().mcp
    server = cfg.server(server_id)
    if server is None:
        raise McpServerNotFound(server_id)
    if not cfg.enabled:
        return {
            "id": server.id,
            "ok": False,
            "error": "not_enabled",
            "tool_count": 0,
            "tools": [],
            "latency_ms": 0,
        }
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


# --------------------------------------------------------------------------
# A11: business-development-by-territory reports (eoa.report.bd_territory)
# --------------------------------------------------------------------------

# Long enough that a quiet territory (small item/event set, a fast resident-model draft) usually
# finishes inside the same request; report generation itself always runs in the orchestrator's
# Worker process (the `bd_report` job kind, same as `weekly_run`/`monthly_run`) -- this is a poll
# loop, never an in-process build.
_BD_SYNC_WAIT_SECONDS = 55.0
_BD_POLL_INTERVAL_SECONDS = 1.0


def list_bd_reports(*, territory: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    where = ["kind = 'bd_territory'"]
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200)}
    if territory:
        where.append("territory = %(territory)s")
        params["territory"] = geography.normalize_country(territory)
    rows = _fetchall(
        f"SELECT * FROM reports WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT %(limit)s", params
    )
    return [_report_card(r) for r in rows]


def bd_territories() -> list[dict[str, Any]]:
    """`GET /api/bd/territories`: candidate territories for the BD report's selector -- the
    configured default set (`config/config.yaml` `bd_report.territories`) plus any other
    territory with market activity, each with item/tender/forecast counts (item counts scoped to
    the configured `bd_report.lookback_days` window) so the UI can rank/suggest, most active
    first."""
    cfg = eoa_config.settings().bd_report
    configured = {geography.normalize_country(t) for t in cfg.territories}

    since = dt.date.today() - dt.timedelta(days=max(cfg.lookback_days, 1))
    item_rows = _fetchall(
        "SELECT geography FROM items WHERE security_status='clean' AND dedup_of IS NULL "
        "AND level = ANY(%(levels)s) AND COALESCE(published_at, fetched_at, created_at)::date >= %(since)s",
        {"levels": ["red", "orange", "yellow"], "since": since},
    )
    tender_rows = _fetchall("SELECT country FROM tenders WHERE status IN ('open', 'unknown')")
    forecast_rows = _fetchall("SELECT buyer_country FROM tender_forecasts")

    def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            code = geography.normalize_country(r.get(key))
            out[code] = out.get(code, 0) + 1
        return out

    item_counts = _counts(item_rows, "geography")
    tender_counts = _counts(tender_rows, "country")
    forecast_counts = _counts(forecast_rows, "buyer_country")

    codes = (configured | set(item_counts) | set(tender_counts) | set(forecast_counts)) - {
        geography.UNKNOWN_COUNTRY
    }
    out = [
        {
            "territory": code,
            "items": item_counts.get(code, 0),
            "tenders": tender_counts.get(code, 0),
            "forecasts": forecast_counts.get(code, 0),
            "configured": code in configured,
        }
        for code in sorted(codes)
    ]
    out.sort(key=lambda t: t["items"] + t["tenders"] + t["forecasts"], reverse=True)
    return out


def enqueue_bd_report(territory: str, lookback_days: int) -> int:
    """Enqueue a `bd_report` job for one territory -- picked up by the orchestrator's Worker
    (`eoa.orchestrator.jobs.HANDLERS["bd_report"]`), same as any other job kind."""
    code = geography.normalize_country(territory)
    if code == geography.UNKNOWN_COUNTRY:
        raise ValueError(f"unrecognized territory: {territory!r}")
    return relational.enqueue_job(
        "bd_report", {"territory": code, "lookback_days": lookback_days}, priority=4
    )


def build_or_enqueue_bd_report(territory: str, lookback_days: int = 90) -> dict[str, Any]:
    """`POST /api/bd/reports`: enqueue, then poll for up to `_BD_SYNC_WAIT_SECONDS` -- returns
    `{"report": <report card with html>, "job_id": <id>}` if the job finished in time, else
    `{"job_id": <id>, "status": "queued"}` for the client to poll `GET /api/bd/reports?territory=`
    (or retry this same endpoint's `job_id` via `GET /api/jobs`)."""
    import time

    job_id = enqueue_bd_report(territory, lookback_days)
    deadline = time.monotonic() + _BD_SYNC_WAIT_SECONDS
    while time.monotonic() < deadline:
        job = _fetchone("SELECT state, result, error FROM jobs WHERE id = %s", (job_id,))
        if job is None:
            break
        state = job.get("state")
        if state in ("done", "partial"):
            result = job.get("result") or {}
            report_id = (result.get("bd_report") or {}).get("report_id")
            if report_id is not None:
                report = get_report(report_id)
                if report is not None:
                    return {"report": report, "job_id": job_id}
            break
        if state == "failed":
            return {"job_id": job_id, "status": "failed", "error": job.get("error")}
        time.sleep(_BD_POLL_INTERVAL_SECONDS)
    return {"job_id": job_id, "status": "queued"}


# --------------------------------------------------------------------------
# product lines / קווי מוצר (PL-backend, user request 2026-09-07) -- frozen contract per
# web/src/types/api.ts ProductLine/ProductLineDetail/ProductLineReportCreateResponse and
# web/src/api/real.ts getProductLines/getProductLine/postProductLineReport. A generated report is
# a normal `reports` row (`kind='product_line'`, the line id in the existing `territory` column --
# see eoa.report.product_line's module docstring), so its full detail/download/citations continue
# to be served by the existing generic GET /api/reports/{id} etc.
# --------------------------------------------------------------------------


def _product_line_card(pl: Any, stats: dict[str, Any]) -> dict[str, Any]:
    latest = _fetchone(
        "SELECT id, created_at, qa_passed, path_html FROM reports "
        "WHERE kind = 'product_line' AND territory = %(line)s ORDER BY created_at DESC LIMIT 1",
        {"line": pl.id},
    )
    latest_report = None
    if latest is not None:
        latest_report = {
            "id": latest["id"],
            "created_at": latest["created_at"],
            "qa_passed": bool(latest.get("qa_passed")),
            "path_html": latest.get("path_html"),
        }
    return {
        "id": pl.id,
        "name_he": pl.name_he,
        "name_en": pl.name_en,
        "subdomains": list(pl.subdomains),
        "exemplar_systems": list(pl.exemplar_systems),
        "competitors": list(pl.competitors),
        "stats": stats,
        "latest_report": latest_report,
    }


def list_product_lines() -> list[dict[str, Any]]:
    """`GET /api/product-lines`: every configured product line (``config/product_lines.yaml``)
    with its deterministic stats and latest report ref."""
    from eoa.product_lines.registry import product_line_defs
    from eoa.product_lines.stats import product_line_stats

    return [_product_line_card(pl, product_line_stats(pl.id)) for pl in product_line_defs()]


def list_product_line_reports(line_id: str, *, limit: int = 30) -> list[dict[str, Any]]:
    rows = _fetchall(
        "SELECT * FROM reports WHERE kind = 'product_line' AND territory = %(line)s "
        "ORDER BY created_at DESC LIMIT %(limit)s",
        {"line": line_id, "limit": min(max(limit, 1), 200)},
    )
    return [_report_card(r) for r in rows]


def product_line_detail(line_id: str) -> dict[str, Any] | None:
    """`GET /api/product-lines/{id}`: the list card plus ``recent_items`` (the same ItemCard shape
    the feed uses, last 30 days), ``open_tenders`` (the tenders page shape) and ``reports`` (the
    report card shape) -- ``None`` when ``line_id`` isn't one of the configured product lines (the
    route raises 404)."""
    from eoa.product_lines.registry import get_product_line
    from eoa.product_lines.stats import product_line_stats

    pl = get_product_line(line_id)
    if pl is None:
        return None
    card = _product_line_card(pl, product_line_stats(line_id))
    since = dt.date.today() - dt.timedelta(days=30)
    recent_rows = _fetchall(
        "SELECT i.*, s.name AS source_name FROM items i LEFT JOIN sources s ON s.id = i.source_id "
        "WHERE i.product_lines @> ARRAY[%(line)s]::text[] AND i.security_status = 'clean' "
        "AND i.dedup_of IS NULL AND COALESCE(i.published_at, i.fetched_at, i.created_at)::date >= %(since)s "
        "ORDER BY COALESCE(i.score, 0) DESC, COALESCE(i.published_at, i.fetched_at) DESC LIMIT 30",
        {"line": line_id, "since": since},
    )
    recent_items = [_item_card(r) for r in recent_rows]
    _attach_corroboration(recent_items)
    today = dt.date.today()
    tender_rows = _fetchall(
        "SELECT * FROM tenders WHERE product_lines @> ARRAY[%(line)s]::text[] "
        "AND status IN ('open', 'unknown') AND (deadline IS NULL OR deadline >= %(today)s) "
        "ORDER BY deadline ASC NULLS LAST LIMIT 30",
        {"line": line_id, "today": today},
    )
    card["recent_items"] = recent_items
    card["open_tenders"] = [_tender_card(r) for r in tender_rows]
    card["reports"] = list_product_line_reports(line_id)
    return card


def enqueue_product_line_report(line_id: str) -> dict[str, Any]:
    """`POST /api/product-lines/{id}/report`: enqueue the ``product_line_report`` job kind
    (``eoa.orchestrator.jobs.HANDLERS``) -- unlike ``build_or_enqueue_bd_report`` this never waits
    synchronously (frozen contract: ``ProductLineReportCreateResponse`` is ``{job_id}`` only), the
    client polls ``GET /api/product-lines/{id}`` for ``reports``/``latest_report`` to update."""
    from eoa.product_lines.registry import get_product_line

    if get_product_line(line_id) is None:
        raise ValueError(f"unrecognized product line: {line_id!r}")
    job_id = relational.enqueue_job("product_line_report", {"line_id": line_id}, priority=4)
    return {"job_id": job_id}


# --------------------------------------------------------------------------
# tech watch / רדאר טכנולוגי (A12, 2026-09-06 -- eoa.pipeline.tech_watch, eoa.report.tech_watch)
# --------------------------------------------------------------------------

_TECH_DOMAIN = "tech_dev"
_TECH_MATURITIES = ("lab", "prototype", "qualified", "fielded")


def tech_radar(weeks: int = 12) -> dict[str, Any]:
    """subdomain x maturity item-count matrix over the last `weeks` weeks, plus a per-subdomain
    4-week momentum sparkline -- backs the "רדאר טכנולוגי" UI (`GET /api/tech/radar`)."""
    weeks = max(1, min(weeks, 52))
    since = dt.datetime.now(dt.UTC) - dt.timedelta(weeks=weeks)
    sub_labels = dict(eoa_config.settings().taxonomy.get("domains", {}).get(_TECH_DOMAIN, {}).get("sub", {}))

    rows = _fetchall(
        """
        SELECT subdomain, tech_maturity, count(*) AS n
        FROM items
        WHERE domain = %(domain)s AND security_status = 'clean' AND dedup_of IS NULL
          AND COALESCE(published_at, fetched_at, created_at) >= %(since)s
        GROUP BY subdomain, tech_maturity
        """,
        {"domain": _TECH_DOMAIN, "since": since},
    )
    matrix: dict[str, dict[str, int]] = {sub: {m: 0 for m in _TECH_MATURITIES} for sub in sub_labels}
    for row in rows:
        sub = row.get("subdomain") or ""
        bucket = matrix.setdefault(sub, {m: 0 for m in _TECH_MATURITIES})
        maturity = row.get("tech_maturity")
        if maturity in _TECH_MATURITIES:
            bucket[maturity] += row["n"]
        else:
            bucket["unknown"] = bucket.get("unknown", 0) + row["n"]

    # 4-point weekly sparkline per subdomain (most recent week last).
    sparkline_rows = _fetchall(
        """
        SELECT subdomain, date_trunc('week', COALESCE(published_at, fetched_at, created_at)) AS wk,
               count(*) AS n
        FROM items
        WHERE domain = %(domain)s AND security_status = 'clean' AND dedup_of IS NULL
          AND COALESCE(published_at, fetched_at, created_at) >= %(since4)s
        GROUP BY subdomain, wk
        ORDER BY wk ASC
        """,
        {"domain": _TECH_DOMAIN, "since4": dt.datetime.now(dt.UTC) - dt.timedelta(weeks=4)},
    )
    sparkline: dict[str, list[int]] = {sub: [] for sub in sub_labels}
    for row in sparkline_rows:
        sub = row.get("subdomain") or ""
        sparkline.setdefault(sub, []).append(row["n"])

    subdomains_out = [
        {
            "subdomain": sub,
            "label_he": label,
            "counts": matrix.get(sub, {m: 0 for m in _TECH_MATURITIES}),
            "total": sum(matrix.get(sub, {}).values()),
            "sparkline": sparkline.get(sub, []),
        }
        for sub, label in sub_labels.items()
    ]
    return {"weeks": weeks, "maturities": list(_TECH_MATURITIES), "subdomains": subdomains_out}


def list_tech_items(
    *,
    subdomain: str | None = None,
    maturity: str | None = None,
    actor_kind: str | None = None,
    since: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """`tech_dev` items, optionally filtered by `subdomain`/`tech_maturity`/`tech_actor_kind`/
    `since` (period) -- backs `GET /api/tech/items`, the radar's click-through list (reuses
    `_item_card`, same shape as `list_items`)."""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    where = ["i.domain = %(domain)s"]
    params: dict[str, Any] = {"domain": _TECH_DOMAIN}
    if subdomain:
        where.append("i.subdomain = %(subdomain)s")
        params["subdomain"] = subdomain
    if maturity:
        where.append("i.tech_maturity = %(maturity)s")
        params["maturity"] = maturity
    if actor_kind:
        where.append("i.tech_actor_kind = %(actor_kind)s")
        params["actor_kind"] = actor_kind
    if since:
        where.append("COALESCE(i.published_at, i.fetched_at) >= %(since)s")
        params["since"] = since
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
        ORDER BY i.score DESC NULLS LAST, i.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    cards = [_item_card(r) for r in rows]
    _attach_corroboration(cards)
    return total, cards
