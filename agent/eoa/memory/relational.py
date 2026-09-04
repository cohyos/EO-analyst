"""Typed synchronous helpers over the relational schema (psycopg3, dict rows).

Every function opens a connection via ``eoa.db.connection()`` and relies on
that context manager's standard psycopg3/psycopg_pool semantics: the
transaction is committed on clean exit and rolled back on exception. No
function here calls ``conn.commit()`` / ``conn.rollback()`` directly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import structlog
from psycopg import sql
from psycopg.types.json import Json

from eoa.db import connection

log = structlog.get_logger(__name__)

# Columns on `items` that update_item_fields() is allowed to touch. Kept as an
# explicit allow-list so field names (which become SQL identifiers) can never
# come from untrusted input.
_ITEM_UPDATABLE_FIELDS = {
    "source_id",
    "canonical_url",
    "title",
    "lang",
    "published_at",
    "raw_text",
    "clean_text",
    "text_hash",
    "summary_he",
    "so_what_he",
    "domain",
    "subdomain",
    "dimensions",
    "tags",
    "geography",
    "report_kind",
    "trl",
    "entities_mentioned",
    "score",
    "level",
    "triage_reason",
    "dedup_of",
    "key_facts",
    "uncertainty_he",
    "source_name",
    "security_status",
    "classification",
}


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


def upsert_source(
    *,
    name: str,
    url: str | None = None,
    kind: str,
    lang: str | None = None,
    reliability: int = 3,
    active: bool = True,
) -> int:
    """Insert or update a source keyed by its unique `name`, returning its id."""
    query = """
        INSERT INTO sources (name, url, kind, lang, reliability, active)
        VALUES (%(name)s, %(url)s, %(kind)s, %(lang)s, %(reliability)s, %(active)s)
        ON CONFLICT (name) DO UPDATE SET
            url = EXCLUDED.url,
            kind = EXCLUDED.kind,
            lang = EXCLUDED.lang,
            reliability = EXCLUDED.reliability,
            active = EXCLUDED.active
        RETURNING id
    """
    params = {
        "name": name,
        "url": url,
        "kind": kind,
        "lang": lang,
        "reliability": reliability,
        "active": active,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        source_id: int = cur.fetchone()["id"]
    log.info("source.upserted", source_id=source_id, name=name, kind=kind)
    return source_id


# --------------------------------------------------------------------------
# items
# --------------------------------------------------------------------------


def insert_item(
    *,
    source_id: int | None,
    url: str,
    canonical_url: str | None = None,
    title: str | None = None,
    lang: str | None = None,
    published_at: dt.datetime | None = None,
    fetched_at: dt.datetime | None = None,
    raw_text: str | None = None,
    clean_text: str | None = None,
    text_hash: str | None = None,
    domain: str | None = None,
    subdomain: str | None = None,
    geography: str | None = None,
    report_kind: str | None = None,
    trl: str | None = None,
) -> int:
    """Insert a new item; on a `url` conflict just refresh `fetched_at`. Returns the item id."""
    query = """
        INSERT INTO items (
            source_id, url, canonical_url, title, lang, published_at, fetched_at,
            raw_text, clean_text, text_hash, domain, subdomain, geography, report_kind, trl
        )
        VALUES (
            %(source_id)s, %(url)s, %(canonical_url)s, %(title)s, %(lang)s, %(published_at)s,
            COALESCE(%(fetched_at)s, now()), %(raw_text)s, %(clean_text)s, %(text_hash)s,
            %(domain)s, %(subdomain)s, %(geography)s, %(report_kind)s, %(trl)s
        )
        ON CONFLICT (url) DO UPDATE SET fetched_at = COALESCE(EXCLUDED.fetched_at, now())
        RETURNING id
    """
    params = {
        "source_id": source_id,
        "url": url,
        "canonical_url": canonical_url,
        "title": title,
        "lang": lang,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "raw_text": raw_text,
        "clean_text": clean_text,
        "text_hash": text_hash,
        "domain": domain,
        "subdomain": subdomain,
        "geography": geography,
        "report_kind": report_kind,
        "trl": trl,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        item_id: int = cur.fetchone()["id"]
    log.info("item.inserted", item_id=item_id, url=url, source_id=source_id)
    return item_id


def get_items_for_stage(stage: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return up to `limit` clean items that have not yet completed pipeline `stage`."""
    query = """
        SELECT * FROM items
        WHERE security_status = 'clean'
          AND NOT (%(stage)s = ANY(COALESCE(processed_stages, '{}')))
        ORDER BY fetched_at NULLS LAST, id
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"stage": stage, "limit": limit})
        rows = cur.fetchall()
    return rows


def mark_stage(item_id: int, stage: str) -> None:
    """Append `stage` to items.processed_stages for `item_id`, if not already present."""
    query = """
        UPDATE items
        SET processed_stages = CASE
            WHEN %(stage)s = ANY(COALESCE(processed_stages, '{}')) THEN processed_stages
            ELSE array_append(COALESCE(processed_stages, '{}'), %(stage)s)
        END
        WHERE id = %(item_id)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"stage": stage, "item_id": item_id})
    log.debug("item.stage_marked", item_id=item_id, stage=stage)


def update_item_fields(item_id: int, **fields: Any) -> None:
    """Update an allow-listed subset of `items` columns for `item_id`."""
    unknown = set(fields) - _ITEM_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"cannot update unknown item fields: {sorted(unknown)}")
    if not fields:
        return
    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(key), sql.Placeholder(key)) for key in fields
    )
    query = sql.SQL("UPDATE items SET {assignments} WHERE id = %(item_id)s").format(assignments=assignments)
    params: dict[str, Any] = {**fields, "item_id": item_id}
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
    log.debug("item.fields_updated", item_id=item_id, fields=sorted(fields))


# --------------------------------------------------------------------------
# events / entities
# --------------------------------------------------------------------------


def insert_event(
    *,
    item_id: int,
    kind: str,
    title: str | None = None,
    date: dt.date | None = None,
    amount_usd: float | None = None,
    currency: str | None = None,
    parties: list[str] | None = None,
    customer: str | None = None,
    program: str | None = None,
    summary_he: str | None = None,
    confidence: float | None = None,
) -> int:
    """Insert an event row derived from `item_id`, returning its id."""
    query = """
        INSERT INTO events (
            item_id, kind, title, date, amount_usd, currency, parties, customer, program,
            summary_he, confidence
        )
        VALUES (
            %(item_id)s, %(kind)s, %(title)s, %(date)s, %(amount_usd)s, %(currency)s, %(parties)s,
            %(customer)s, %(program)s, %(summary_he)s, %(confidence)s
        )
        RETURNING id
    """
    params = {
        "item_id": item_id,
        "kind": kind,
        "title": title,
        "date": date,
        "amount_usd": amount_usd,
        "currency": currency,
        "parties": parties,
        "customer": customer,
        "program": program,
        "summary_he": summary_he,
        "confidence": confidence,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        event_id: int = cur.fetchone()["id"]
    log.info("event.inserted", event_id=event_id, item_id=item_id, kind=kind)
    return event_id


def upsert_entity(
    *,
    name: str,
    kind: str,
    country: str | None = None,
    aliases: list[str] | None = None,
    focus: list[str] | None = None,
    notes: str | None = None,
    first_seen_item: int | None = None,
) -> int:
    """Insert or update an entity keyed by its unique `name`, returning its id."""
    query = """
        INSERT INTO entities (name, kind, country, aliases, focus, notes, first_seen_item)
        VALUES (%(name)s, %(kind)s, %(country)s, %(aliases)s, %(focus)s, %(notes)s, %(first_seen_item)s)
        ON CONFLICT (name) DO UPDATE SET
            kind = EXCLUDED.kind,
            country = COALESCE(EXCLUDED.country, entities.country),
            aliases = CASE WHEN EXCLUDED.aliases IS NULL OR EXCLUDED.aliases = '{}'
                           THEN entities.aliases ELSE EXCLUDED.aliases END,
            focus = CASE WHEN EXCLUDED.focus IS NULL OR EXCLUDED.focus = '{}'
                         THEN entities.focus ELSE EXCLUDED.focus END,
            notes = COALESCE(EXCLUDED.notes, entities.notes),
            first_seen_item = COALESCE(entities.first_seen_item, EXCLUDED.first_seen_item)
        RETURNING id
    """
    params = {
        "name": name,
        "kind": kind,
        "country": country,
        "aliases": aliases,
        "focus": focus,
        "notes": notes,
        "first_seen_item": first_seen_item,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        entity_id: int = cur.fetchone()["id"]
    log.info("entity.upserted", entity_id=entity_id, name=name, kind=kind)
    return entity_id


# --------------------------------------------------------------------------
# resource gate / security
# --------------------------------------------------------------------------


def record_resource_decision(
    *,
    decision: str,
    model: str | None = None,
    vram_free_mb: int | None = None,
    gpu_util: int | None = None,
    gpu_temp: int | None = None,
    ram_free_mb: int | None = None,
    wait_ms: int | None = None,
) -> int:
    """Insert a row into resource_log describing one resource-gate decision, returning its id."""
    query = """
        INSERT INTO resource_log (decision, model, vram_free_mb, gpu_util, gpu_temp, ram_free_mb, wait_ms)
        VALUES (%(decision)s, %(model)s, %(vram_free_mb)s, %(gpu_util)s, %(gpu_temp)s, %(ram_free_mb)s, %(wait_ms)s)
        RETURNING id
    """
    params = {
        "decision": decision,
        "model": model,
        "vram_free_mb": vram_free_mb,
        "gpu_util": gpu_util,
        "gpu_temp": gpu_temp,
        "ram_free_mb": ram_free_mb,
        "wait_ms": wait_ms,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        log_id: int = cur.fetchone()["id"]
    return log_id


def log_security(
    *,
    item_id: int | None = None,
    source_id: int | None = None,
    layer: str,
    verdict: str,
    score: float | None = None,
    excerpt: str | None = None,
    action: str,
) -> int:
    """Insert a row into security_log, returning its id."""
    query = """
        INSERT INTO security_log (item_id, source_id, layer, verdict, score, excerpt, action)
        VALUES (%(item_id)s, %(source_id)s, %(layer)s, %(verdict)s, %(score)s, %(excerpt)s, %(action)s)
        RETURNING id
    """
    params = {
        "item_id": item_id,
        "source_id": source_id,
        "layer": layer,
        "verdict": verdict,
        "score": score,
        "excerpt": excerpt,
        "action": action,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        log_id: int = cur.fetchone()["id"]
    log.warning("security.logged", item_id=item_id, source_id=source_id, layer=layer, action=action)
    return log_id


# --------------------------------------------------------------------------
# jobs / run_log
# --------------------------------------------------------------------------


def enqueue_job(
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 5,
    not_before: dt.datetime | None = None,
) -> int:
    """Insert a new queued job, returning its id."""
    query = """
        INSERT INTO jobs (kind, payload, priority, not_before, state)
        VALUES (%(kind)s, %(payload)s, %(priority)s, %(not_before)s, 'queued')
        RETURNING id
    """
    params = {
        "kind": kind,
        "payload": Json(payload) if payload is not None else None,
        "priority": priority,
        "not_before": not_before,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        job_id: int = cur.fetchone()["id"]
    log.info("job.enqueued", job_id=job_id, kind=kind, priority=priority)
    return job_id


def claim_next_job(
    kinds: Sequence[str] | None = None,
    worker_id: str | None = None,
    *,
    lease_seconds: int = 900,
) -> dict[str, Any] | None:
    """Atomically claim the next eligible job (`FOR UPDATE SKIP LOCKED`) and mark it running.

    Eligible jobs are `queued`, or `deferred` whose `not_before` has passed (a resource-failure
    retry, see `finish_job`). Sets `worker_id` and a `lease_expires_at` `lease_seconds` in the
    future so a crashed worker's job can be detected and reaped by `reap_stale_jobs` instead of
    sitting `running` forever."""
    where_kind = "AND kind = ANY(%(kinds)s)" if kinds else ""
    query = f"""
        WITH next_job AS (
            SELECT id FROM jobs
            WHERE state IN ('queued', 'deferred')
              AND (not_before IS NULL OR not_before <= now())
              {where_kind}
            ORDER BY priority ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE jobs
        SET state = 'running', started_at = now(), attempts = jobs.attempts + 1,
            worker_id = %(worker_id)s,
            lease_expires_at = now() + (%(lease_seconds)s || ' seconds')::interval
        FROM next_job
        WHERE jobs.id = next_job.id
        RETURNING jobs.*
    """
    params: dict[str, Any] = {"worker_id": worker_id, "lease_seconds": lease_seconds}
    if kinds:
        params["kinds"] = list(kinds)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    if row:
        log.info("job.claimed", job_id=row["id"], kind=row["kind"], worker_id=worker_id)
    return row


def finish_job(
    job_id: int,
    state: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    not_before: dt.datetime | None = None,
    worker_id: str | None = None,
) -> None:
    """Mark a job finished with a terminal `state` (`done`/`failed`/`partial`) or requeue it
    (`deferred`/`queued`, optionally with `not_before` for a delayed retry).

    Only updates a row currently `running`, `deferred`, or `queued` — a job already reaped as
    `failed(error='stale lease')` by `reap_stale_jobs`, or otherwise finished by someone else,
    is left alone so a late-arriving result from a stale worker cannot clobber it. If `worker_id`
    is given and differs from the lease holder recorded by `claim_next_job`, this is logged as a
    warning (the write still proceeds — this is a best-effort ownership check, not a hard lock)."""
    query = """
        UPDATE jobs
        SET state = %(state)s, finished_at = now(), result = %(result)s, error = %(error)s,
            not_before = COALESCE(%(not_before)s, not_before)
        WHERE id = %(job_id)s AND state IN ('running', 'deferred', 'queued')
        RETURNING worker_id AS prior_worker_id
    """
    params = {
        "state": state,
        "result": Json(result) if result is not None else None,
        "error": error,
        "not_before": not_before,
        "job_id": job_id,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    if row is None:
        log.warning("job.finish_no_matching_row", job_id=job_id, state=state)
    elif worker_id is not None and row["prior_worker_id"] not in (None, worker_id):
        log.warning(
            "job.finish_worker_mismatch",
            job_id=job_id,
            lease_worker_id=row["prior_worker_id"],
            finishing_worker_id=worker_id,
        )
    log.info("job.finished", job_id=job_id, state=state, error=error)


def heartbeat(
    job_id: int, stage: str, event: str, detail: dict[str, Any] | None = None, *, lease_seconds: int = 900
) -> None:
    """Record a heartbeat/progress row in run_log for `job_id`, and extend that job's lease so a
    long-running stage is not mistaken for a crashed worker by `reap_stale_jobs`."""
    query = """
        INSERT INTO run_log (job_id, stage, event, detail, heartbeat_at)
        VALUES (%(job_id)s, %(stage)s, %(event)s, %(detail)s, now())
    """
    params = {
        "job_id": job_id,
        "stage": stage,
        "event": event,
        "detail": Json(detail) if detail is not None else None,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        cur.execute(
            "UPDATE jobs SET lease_expires_at = now() + (%(lease_seconds)s || ' seconds')::interval "
            "WHERE id = %(job_id)s AND state = 'running'",
            {"job_id": job_id, "lease_seconds": lease_seconds},
        )
    log.debug("job.heartbeat", job_id=job_id, stage=stage, event=event)


def reap_stale_jobs(max_age_hours: int = 6) -> int:
    """Mark `running` jobs whose lease has expired as `failed(error='stale lease')`, returning the
    number reaped. A row with no lease (pre-migration data, or a claim made without `worker_id`
    wiring) falls back to `started_at` older than `max_age_hours`. Call at Worker start and in
    `pre_flight()` so a crashed process's job does not block retries or a reaper-requeue race
    forever."""
    query = """
        UPDATE jobs
        SET state = 'failed', finished_at = now(), error = 'stale lease'
        WHERE state = 'running'
          AND (
              (lease_expires_at IS NOT NULL AND lease_expires_at < now())
              OR (
                  lease_expires_at IS NULL AND started_at IS NOT NULL
                  AND started_at < now() - (%(max_age_hours)s || ' hours')::interval
              )
          )
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"max_age_hours": max_age_hours})
        rows = cur.fetchall()
    if rows:
        log.warning("jobs.reaped_stale", count=len(rows), job_ids=[r["id"] for r in rows])
    return len(rows)


# --------------------------------------------------------------------------
# investigation / lessons / feedback
# --------------------------------------------------------------------------


def insert_investigation_log(
    *,
    job_id: int | None = None,
    trigger_item: int | None = None,
    round: int | None = None,
    lang: str | None = None,
    query: str | None = None,
    engine: str | None = None,
    results_n: int | None = None,
    pages_read: int | None = None,
    outcome: str,
    notes: str | None = None,
) -> int:
    """Insert a deep-search investigation_log row, returning its id."""
    sql_query = """
        INSERT INTO investigation_log (
            job_id, trigger_item, round, lang, query, engine, results_n, pages_read, outcome, notes
        )
        VALUES (
            %(job_id)s, %(trigger_item)s, %(round)s, %(lang)s, %(query)s, %(engine)s,
            %(results_n)s, %(pages_read)s, %(outcome)s, %(notes)s
        )
        RETURNING id
    """
    params = {
        "job_id": job_id,
        "trigger_item": trigger_item,
        "round": round,
        "lang": lang,
        "query": query,
        "engine": engine,
        "results_n": results_n,
        "pages_read": pages_read,
        "outcome": outcome,
        "notes": notes,
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_query, params)
        log_id: int = cur.fetchone()["id"]
    log.info("investigation.logged", job_id=job_id, trigger_item=trigger_item, outcome=outcome)
    return log_id


def get_lessons(kind: str | None = None) -> list[dict[str, Any]]:
    """Return active lessons, optionally filtered by `kind`."""
    query = "SELECT * FROM lessons WHERE active = true"
    params: dict[str, Any] = {}
    if kind is not None:
        query += " AND kind = %(kind)s"
        params["kind"] = kind
    query += " ORDER BY created_at DESC"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def add_lesson(kind: str, text: str, source_ref: str | None = None) -> int:
    """Insert a new active lesson, returning its id."""
    query = """
        INSERT INTO lessons (kind, text, source_ref, active)
        VALUES (%(kind)s, %(text)s, %(source_ref)s, true)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"kind": kind, "text": text, "source_ref": source_ref})
        lesson_id: int = cur.fetchone()["id"]
    log.info("lesson.added", lesson_id=lesson_id, kind=kind)
    return lesson_id


def recent_feedback(days: int) -> list[dict[str, Any]]:
    """Return triage_feedback rows created within the last `days` days."""
    query = """
        SELECT * FROM triage_feedback
        WHERE created_at >= now() - (%(days)s || ' days')::interval
        ORDER BY created_at DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"days": days})
        return cur.fetchall()
