"""Typed synchronous helpers over the relational schema (psycopg3, dict rows).

Every function opens a connection via ``eoa.db.connection()`` and relies on
that context manager's standard psycopg3/psycopg_pool semantics: the
transaction is committed on clean exit and rolled back on exception. No
function here calls ``conn.commit()`` / ``conn.rollback()`` directly.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from collections.abc import Sequence
from typing import Any, cast

import structlog
from psycopg import sql
from psycopg.rows import dict_row
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
    # A12 (מעקב טכנולוגי, 2026-09-06): additive tech-watch fields on `items`, see migration 0011.
    "tech_maturity",
    "tech_actor_kind",
    "tech_readiness_note_he",
    # Q3-10 (docs/qa/findings_Q3_r1.md): 'full' | 'partial' | 'stub', see migration 0015 and
    # eoa.fetch.content_quality.assess / eoa.pipeline.analyze's pre-check.
    "content_status",
    # A13 (מיקוד תעשייה ישראלית, 2026-09-06): additive, see migration 0017 and
    # eoa.pipeline.israel_focus.israel_relevance() / classify.py + analyze.py's "# --- A13" blocks.
    "israel_relevance",
    "israel_reasons",
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        source_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    log.info("source.upserted", source_id=source_id, name=name, kind=kind)
    return source_id


_sources_columns_cache: set[str] | None = None


def _existing_sources_columns(cur: Any) -> set[str]:
    """Same schema-drift guard as :func:`_existing_items_columns`, for ``sources`` -- ``last_ok_at``
    (migration 0019, D9 round-1 fix) may not exist yet on a DB behind ``HEAD``."""
    global _sources_columns_cache
    if _sources_columns_cache is None:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'sources'")
        _sources_columns_cache = {row["column_name"] for row in cur.fetchall()}
    return _sources_columns_cache


def touch_source_fetched(source_id: int, ok: bool) -> None:
    """D9 round-1 fix (docs/qa/loop/round_1_fixes.md, ``sources_recently_fetched``): record that
    ``source_id`` was just attempted -- called once per source per ``run_ingest`` attempt
    (``eoa.fetch.service._ingest_one_source``), success or failure, so
    ``eoa.qa.d9_tenders_conferences``'s "every active source fetched within 7 days" check reflects
    reality instead of every source's ``last_fetched_at`` sitting at ``NULL`` forever (round-0: 0/60).

    Always bumps ``last_fetched_at = now()``. On success (``ok=True``): resets ``fail_count`` to 0
    and, if the column exists (migration 0019 -- a DB behind ``HEAD`` simply skips it, matching
    :func:`update_item_fields`'s guard), sets ``last_ok_at = now()``. On failure: increments
    ``fail_count`` (superseding the old name-keyed ``eoa.fetch.service._bump_fail_count`` for any
    caller that has the row's id, which every ``run_ingest`` caller does)."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        columns = _existing_sources_columns(cur)
        sets = ["last_fetched_at = now()"]
        if ok:
            sets.append("fail_count = 0")
            if "last_ok_at" in columns:
                sets.append("last_ok_at = now()")
        else:
            sets.append("fail_count = fail_count + 1")
        query = sql.SQL("UPDATE sources SET {sets} WHERE id = %(source_id)s").format(
            sets=sql.SQL(", ").join(sql.SQL(s) for s in sets)
        )
        cur.execute(query, {"source_id": source_id})
    log.debug("source.fetch_touched", source_id=source_id, ok=ok)


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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        item_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    log.info("item.inserted", item_id=item_id, url=url, source_id=source_id)
    return item_id


def get_items_for_stage(
    stage: str, limit: int = 50, *, item_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Return up to `limit` clean items that have not yet completed pipeline `stage`.

    F22 (docs/REVIEW_2026-09-05.md): ``item_ids``, when given, additionally restricts the result to
    those specific ids -- used by ``orchestrator.jobs``'s ``post_tenders_catchup`` mini-stage to
    embed/classify/triage only the handful of tender-derived items created by the ``tenders`` stage
    this run, instead of sweeping the whole stage backlog (which is ordered oldest-first and would
    likely not even reach today's newest rows within a short budget)."""
    query = """
        SELECT * FROM items
        WHERE security_status = 'clean'
          AND NOT (%(stage)s = ANY(COALESCE(processed_stages, '{}')))
          AND (%(item_ids)s IS NULL OR id = ANY(%(item_ids)s))
        ORDER BY fetched_at NULLS LAST, id
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"stage": stage, "limit": limit, "item_ids": item_ids})
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"stage": stage, "item_id": item_id})
    log.debug("item.stage_marked", item_id=item_id, stage=stage)


_items_columns_cache: set[str] | None = None


def _existing_items_columns(cur: Any) -> set[str]:
    """``items`` columns that actually exist on the connected DB, cached for the process lifetime
    (the schema doesn't change mid-run). D1 round-1 fix (docs/qa/loop/round_1_fixes.md): a DB can
    sit behind ``HEAD`` (see docs/MODULES.md) and be missing an additive column a later migration
    adds (e.g. ``tech_maturity``/``tech_actor_kind``/``tech_readiness_note_he``, migration 0011,
    or ``israel_relevance``/``israel_reasons``, migration 0017) -- ``persist_analysis`` and the
    A13 block always pass those keys, so a plain ``UPDATE`` crashed outright on such a DB instead
    of degrading to "just skip the column that isn't there yet"."""
    global _items_columns_cache
    if _items_columns_cache is None:
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'items'")
        _items_columns_cache = {row["column_name"] for row in cur.fetchall()}
    return _items_columns_cache


def update_item_fields(item_id: int, **fields: Any) -> None:
    """Update an allow-listed subset of `items` columns for `item_id`. A field naming a column
    that doesn't exist on this DB (schema behind HEAD, see :func:`_existing_items_columns`) is
    silently dropped rather than raising -- every other field in the same call still gets written."""
    unknown = set(fields) - _ITEM_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"cannot update unknown item fields: {sorted(unknown)}")
    if not fields:
        return
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        present = _existing_items_columns(cur)
        missing = set(fields) - present
        if missing:
            log.debug("item.fields_skipped_missing_columns", item_id=item_id, fields=sorted(missing))
        fields = {k: v for k, v in fields.items() if k in present}
        if not fields:
            return
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(key), sql.Placeholder(key)) for key in fields
        )
        query = sql.SQL("UPDATE items SET {assignments} WHERE id = %(item_id)s").format(
            assignments=assignments
        )
        params: dict[str, Any] = {**fields, "item_id": item_id}
        cur.execute(query, params)
    log.debug("item.fields_updated", item_id=item_id, fields=sorted(fields))


# --------------------------------------------------------------------------
# events / entities
# --------------------------------------------------------------------------

#: Q3-6b (docs/qa/findings_Q3_r2.md): when two events for the same item are near-duplicates that
#: differ only by `kind` (e.g. item 70: `contract_award` vs `test`, same underlying fact read two
#: different ways), the more *specific* kind wins the merge. Anything not listed here (including
#: the free-text "other") ranks lowest.
EVENT_KIND_PRIORITY: dict[str, int] = {
    "contract_award": 5,
    "acquisition": 4,
    "partnership": 3,
    "deployment": 2,
    "test": 1,
}

_WS_RE = re.compile(r"\s+", re.UNICODE)

#: Q3-6b: two event titles for the same item at or above this similarity are treated as the same
#: underlying event (re-extracted with a different `kind`), not two distinct events.
EVENT_TITLE_DEDUP_THRESHOLD = 0.9


def _normalize_title_for_similarity(title: str | None) -> str:
    return _WS_RE.sub(" ", (title or "").strip().casefold())


def event_title_similarity(a: str | None, b: str | None) -> float:
    """Similarity of two event titles (Q3-6b), in ``[0, 1]``. Empty/``None`` titles never match
    anything (returns ``0.0``) -- there's no title to compare identity on.

    Uses :class:`difflib.SequenceMatcher` (character-level) rather than token-Jaccard: this
    corpus's titles are short Hebrew sentences (5-7 words), where token-Jaccard is too brittle --
    e.g. item 70's real near-duplicate, "זכייה במכרז **ל**פיתוח פלטפורמת פיקוד ושליטה" (contract_
    award) vs "זכייה במכרז פיתוח פלטפורמת פיקוד ושליטה" (test), differ by a single one-letter
    prefix ("ל") on one word -- but because that turns it into a wholly different *token*,
    token-Jaccard only scores ~0.71 (5 shared / 7 total tokens) on a 6-7-token title, well under
    :data:`EVENT_TITLE_DEDUP_THRESHOLD`, while ``SequenceMatcher`` correctly scores ~0.99."""
    na, nb = _normalize_title_for_similarity(a), _normalize_title_for_similarity(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def more_specific_event_kind(a: str, b: str) -> str:
    """The more specific of two event `kind` values per :data:`EVENT_KIND_PRIORITY` (Q3-6b) --
    ties (including two kinds neither of which is in the priority table) keep `a`."""
    return b if EVENT_KIND_PRIORITY.get(b, 0) > EVENT_KIND_PRIORITY.get(a, 0) else a


def _find_near_duplicate_event(item_id: int, kind: str, title: str) -> dict[str, Any] | None:
    """Q3-6b: an existing event for `item_id`, with a *different* `kind`, whose title is a
    near-duplicate (:data:`EVENT_TITLE_DEDUP_THRESHOLD`) of `title` -- the best (highest-
    similarity) match, or ``None``. Exact-same-`kind` near-duplicates are left to the
    `(item_id, kind, lower(title))` unique-index upsert below; this only catches the "same event,
    different kind" case that index can't."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, kind, title, date, amount_usd, currency, parties, customer, program, "
            "summary_he, confidence FROM events WHERE item_id = %(item_id)s AND kind <> %(kind)s "
            "AND title IS NOT NULL",
            {"item_id": item_id, "kind": kind},
        )
        candidates = cur.fetchall()
    best: dict[str, Any] | None = None
    best_score = 0.0
    for cand in candidates:
        score = event_title_similarity(title, cand["title"])
        if score >= EVENT_TITLE_DEDUP_THRESHOLD and score > best_score:
            best, best_score = cand, score
    return best


def _merge_into_existing_event(existing: dict[str, Any], *, kind: str, **fields: Any) -> int:
    """Q3-6b: merge a new extraction's fields into `existing` (a near-duplicate event with a
    different `kind`), keeping the more specific `kind` and the same non-null-wins/richer-parties/
    max-confidence policy as the exact-match upsert in :func:`insert_event`."""
    merged_kind = more_specific_event_kind(existing["kind"], kind)
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE events SET
                kind = %(kind)s,
                date = COALESCE(events.date, %(date)s),
                amount_usd = COALESCE(events.amount_usd, %(amount_usd)s),
                currency = COALESCE(events.currency, %(currency)s),
                parties = CASE
                    WHEN events.parties IS NULL OR array_length(events.parties, 1) IS NULL
                    THEN %(parties)s ELSE events.parties
                END,
                customer = COALESCE(events.customer, %(customer)s),
                program = COALESCE(events.program, %(program)s),
                summary_he = COALESCE(events.summary_he, %(summary_he)s),
                confidence = GREATEST(COALESCE(events.confidence, 0), COALESCE(%(confidence)s, 0)),
                updated_at = now()
            WHERE id = %(id)s
            """,
            {"id": existing["id"], "kind": merged_kind, **fields},
        )
    log.info(
        "event.merged_near_duplicate",
        event_id=existing["id"],
        kind=merged_kind,
        previous_kind=existing["kind"],
    )
    return existing["id"]


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
    """Upsert an event row derived from `item_id` (Q3-5/Q3-6, docs/qa/findings_Q3_r1.md), returning
    its id.

    Logical identity is `(item_id, kind, lower(title))` -- enforced by the unique index
    `ux_events_item_kind_title` (``db/migrations/versions/0016_events_dedup_unique_index.py``).
    Re-processing an item (or a model re-extracting a slightly different reading of the same
    underlying fact) previously always inserted a fresh row, producing duplicate events for the
    same (item, kind, title) -- e.g. item 70's investment event, inserted twice, once without an
    amount and once with. Now a repeat insert merges into the existing row instead: `date`,
    `amount_usd`, `currency`, `customer`, `program`, and `summary_he` keep whichever of the two
    values is non-null (preferring the value already on record when both are present); `parties`
    keeps the existing list unless it was empty; `confidence` keeps the higher of the two.

    A `title=None` event never conflicts with anything (`lower(NULL)` is `NULL`, and Postgres
    unique indexes treat `NULL` as distinct from every other `NULL`) -- there's no title to key
    identity on, so every such row is inserted as new, exactly as before this change.

    Q3-6b (docs/qa/findings_Q3_r2.md): before the exact-match upsert below, also checks for an
    *existing* event of the same item with a *different* `kind` whose title is a near-duplicate
    (:func:`event_title_similarity` >= :data:`EVENT_TITLE_DEDUP_THRESHOLD`) of `title` -- e.g. the same
    underlying fact re-extracted once as `contract_award` and once as `test`. When found, merges
    into that row instead of inserting a second one, keeping the more specific `kind`
    (:data:`EVENT_KIND_PRIORITY`) rather than creating a same-item near-duplicate that the exact
    `(item_id, kind, lower(title))` unique index can't catch.
    """
    fields = {
        "date": date,
        "amount_usd": amount_usd,
        "currency": currency,
        "parties": parties,
        "customer": customer,
        "program": program,
        "summary_he": summary_he,
        "confidence": confidence,
    }
    if title and title.strip():
        near_dup = _find_near_duplicate_event(item_id, kind, title)
        if near_dup is not None:
            return _merge_into_existing_event(near_dup, kind=kind, **fields)

    query = """
        INSERT INTO events (
            item_id, kind, title, date, amount_usd, currency, parties, customer, program,
            summary_he, confidence
        )
        VALUES (
            %(item_id)s, %(kind)s, %(title)s, %(date)s, %(amount_usd)s, %(currency)s, %(parties)s,
            %(customer)s, %(program)s, %(summary_he)s, %(confidence)s
        )
        ON CONFLICT (item_id, kind, (lower(title)))
        DO UPDATE SET
            date = COALESCE(events.date, EXCLUDED.date),
            amount_usd = COALESCE(events.amount_usd, EXCLUDED.amount_usd),
            currency = COALESCE(events.currency, EXCLUDED.currency),
            parties = CASE
                WHEN events.parties IS NULL OR array_length(events.parties, 1) IS NULL
                THEN EXCLUDED.parties ELSE events.parties
            END,
            customer = COALESCE(events.customer, EXCLUDED.customer),
            program = COALESCE(events.program, EXCLUDED.program),
            summary_he = COALESCE(events.summary_he, EXCLUDED.summary_he),
            confidence = GREATEST(COALESCE(events.confidence, 0), COALESCE(EXCLUDED.confidence, 0)),
            updated_at = now()
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        event_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    log.info("event.inserted", event_id=event_id, item_id=item_id, kind=kind)
    return event_id


def _find_case_insensitive_existing_name(name: str) -> str | None:
    """Q3-13: an existing `entities.name` differing from `name` only by case (e.g. "elbit
    systems" already on record when this call spells it "Elbit Systems") -- used so `upsert_entity`
    reuses that row's exact spelling instead of creating a case-variant duplicate. Returns `None`
    on no case-insensitive match (including when `name` itself is already the exact match)."""
    query = "SELECT name FROM entities WHERE lower(name) = lower(%(name)s) AND name <> %(name)s LIMIT 1"
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"name": name})
        row = cur.fetchone()
    return row["name"] if row else None


def upsert_entity(
    *,
    name: str,
    kind: str,
    country: str | None = None,
    aliases: list[str] | None = None,
    focus: list[str] | None = None,
    notes: str | None = None,
    first_seen_item: int | None = None,
) -> int | None:
    """Insert or update an entity keyed by its unique `name`, returning its id.

    Q3-13 (docs/qa/findings_Q3_r1.md/r2.md, ``eoa.pipeline.entity_normalize``): before writing,
    ``name``/``kind`` are resolved through the watchlist and the curated defense-org/country
    tables (an alias like "Elbit Systems UK" maps to the canonical "Elbit"; "צבא ארה\"ב" maps to
    "US Army"; "איראן" maps to kind "country"; ``kind`` is normalised onto the ``entities``
    table's actually allowed values -- e.g. the schema's "country" `EntityMention.kind`, which the
    table's CHECK constraint does not accept, maps to "org"), a watchlist-known
    `country`/`aliases`/`focus` backfills whatever the caller didn't supply (falling back to the
    static non-watchlist company->country map for a well-known company not on the watchlist), and
    a case-insensitive match against an existing row reuses that row's exact spelling instead of
    creating a duplicate. A "junk" name -- a technique/algorithm masquerading as an entity (e.g.
    "image captioning") or a generic Hebrew concept/market/category phrase (e.g. "השוק הביטחוני",
    "תעשייה") -- is rejected outright: **not stored**, and this returns ``None`` instead of an id
    -- every current caller (``classify.persist_classification``, ``analyze.persist_analysis``'s
    edge writer) already treats "this entity didn't get an id" as "skip it", so this is a safe
    additive contract change.
    """
    from eoa.pipeline.entity_normalize import (
        canonical_name_and_kind,
        is_junk_entity,
        resolve_canonical,
        resolve_company_country,
    )

    if is_junk_entity(name):
        log.info("entity.rejected_junk", name=name)
        return None

    canonical = resolve_canonical(name)
    name, kind = canonical_name_and_kind(name, kind)
    if canonical:
        country = country or canonical.get("country")
        aliases = aliases or (canonical.get("aliases") or None)
        focus = focus or (canonical.get("focus") or None)
    elif kind == "company":
        country = country or resolve_company_country(name)

    existing_name = _find_case_insensitive_existing_name(name)
    if existing_name is not None:
        name = existing_name

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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        entity_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        log_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    return log_id


def log_llm_call(
    *,
    provider: str,
    model: str,
    prompt_chars: int,
    duration_ms: int,
    attempt_no: int | None = None,
    fell_back_from: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    est_cost_usd: float = 0.0,
    batch_size: int = 1,
    role: str | None = None,
    error: str | None = None,
) -> int:
    """Insert a row into ``llm_calls`` (U8 privacy log; migration 0009 added the chain/cost
    columns): provider/model/size/duration -- and, since Revision 2026-09-06, per-attempt chain
    accounting -- only. Never the prompt or response text. Returns the new row's id."""
    query = """
        INSERT INTO llm_calls (
            provider, model, prompt_chars, duration_ms,
            attempt_no, fell_back_from, prompt_tokens, completion_tokens,
            est_cost_usd, batch_size, role, error
        )
        VALUES (
            %(provider)s, %(model)s, %(prompt_chars)s, %(duration_ms)s,
            %(attempt_no)s, %(fell_back_from)s, %(prompt_tokens)s, %(completion_tokens)s,
            %(est_cost_usd)s, %(batch_size)s, %(role)s, %(error)s
        )
        RETURNING id
    """
    params = {
        "provider": provider,
        "model": model,
        "prompt_chars": prompt_chars,
        "duration_ms": duration_ms,
        "attempt_no": attempt_no,
        "fell_back_from": fell_back_from,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "est_cost_usd": est_cost_usd,
        "batch_size": batch_size,
        "role": role,
        "error": error,
    }
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        row_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    return row_id


def summarize_llm_calls(since_hours: int = 24) -> dict[str, Any]:
    """``GET /api/llm/calls?since=24h`` (U8-4): per-provider calls/failures/fallbacks/tokens/cost
    over the last ``since_hours`` hours, plus a total row. A row is a "failure" when ``error`` is
    not NULL; a "fallback" is a row whose ``fell_back_from`` is not NULL (i.e. it was only
    attempted because an earlier chain entry failed)."""
    query = """
        SELECT
            provider,
            count(*)                                   AS calls,
            count(*) FILTER (WHERE error IS NOT NULL)  AS failures,
            count(*) FILTER (WHERE fell_back_from IS NOT NULL) AS fallbacks,
            COALESCE(sum(prompt_tokens), 0)             AS prompt_tokens,
            COALESCE(sum(completion_tokens), 0)         AS completion_tokens,
            COALESCE(sum(est_cost_usd), 0)              AS est_cost_usd
        FROM llm_calls
        WHERE created_at >= now() - (%(hours)s || ' hours')::interval
        GROUP BY provider
        ORDER BY provider
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"hours": since_hours})
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["est_cost_usd"] = float(r["est_cost_usd"])  # NUMERIC -> Decimal by default; JSON-unsafe
    totals = {
        "calls": sum(r["calls"] for r in rows),
        "failures": sum(r["failures"] for r in rows),
        "fallbacks": sum(r["fallbacks"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "est_cost_usd": float(sum(r["est_cost_usd"] for r in rows)),
        "cloud_calls": sum(r["calls"] for r in rows if r["provider"] != "ollama"),
    }
    return {"since_hours": since_hours, "providers": rows, "totals": totals}


def log_mcp_call(
    *,
    server: str,
    tool: str,
    args_hash: str,
    chars: int,
    duration_ms: int,
    verdict: str,
    error: str | None = None,
) -> int:
    """Insert a row into ``mcp_calls`` (A8, migration 0010): server/tool/duration/output size and
    the guard verdict for one MCP tool call. Never the arguments or the tool's output text -- only
    a hash of the arguments, matching ``llm_calls``' "never the prompt/response body" convention."""
    query = """
        INSERT INTO mcp_calls (server, tool, args_hash, chars, duration_ms, verdict, error)
        VALUES (%(server)s, %(tool)s, %(args_hash)s, %(chars)s, %(duration_ms)s, %(verdict)s, %(error)s)
        RETURNING id
    """
    params = {
        "server": server,
        "tool": tool,
        "args_hash": args_hash,
        "chars": chars,
        "duration_ms": duration_ms,
        "verdict": verdict,
        "error": error,
    }
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        row_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    return row_id


def summarize_mcp_calls(since_hours: int = 24) -> dict[str, Any]:
    """``GET /api/mcp/calls?since=24h``: per-server-and-tool call counts/failures/avg duration
    over the last ``since_hours`` hours, plus a total row. A row is a "failure" when ``error`` is
    not NULL (connection failure, tool-reported error, or guard quarantine)."""
    query = """
        SELECT
            server,
            tool,
            count(*)                                     AS calls,
            count(*) FILTER (WHERE error IS NOT NULL)    AS failures,
            count(*) FILTER (WHERE verdict != 'clean')    AS flagged,
            COALESCE(avg(duration_ms), 0)                 AS avg_duration_ms,
            COALESCE(sum(chars), 0)                       AS total_chars
        FROM mcp_calls
        WHERE created_at >= now() - (%(hours)s || ' hours')::interval
        GROUP BY server, tool
        ORDER BY server, tool
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"hours": since_hours})
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["avg_duration_ms"] = float(r["avg_duration_ms"])
    totals = {
        "calls": sum(r["calls"] for r in rows),
        "failures": sum(r["failures"] for r in rows),
        "flagged": sum(r["flagged"] for r in rows),
    }
    return {"since_hours": since_hours, "calls": rows, "totals": totals}


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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        log_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        job_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql_query, params)
        log_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def add_lesson(kind: str, text: str, source_ref: str | None = None) -> int:
    """Insert a new active lesson, returning its id."""
    query = """
        INSERT INTO lessons (kind, text, source_ref, active)
        VALUES (%(kind)s, %(text)s, %(source_ref)s, true)
        RETURNING id
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"kind": kind, "text": text, "source_ref": source_ref})
        lesson_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    log.info("lesson.added", lesson_id=lesson_id, kind=kind)
    return lesson_id


def recent_feedback(days: int) -> list[dict[str, Any]]:
    """Return triage_feedback rows created within the last `days` days."""
    query = """
        SELECT * FROM triage_feedback
        WHERE created_at >= now() - (%(days)s || ' days')::interval
        ORDER BY created_at DESC
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"days": days})
        return cur.fetchall()
