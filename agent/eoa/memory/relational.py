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


def deactivate_orphaned_sources(current_names: set[str]) -> int:
    """Round-3 (D9 finding 5, docs/qa/loop/round_1_judge.md): mark ``active = false`` on every
    ``sources`` row whose ``name`` is not among ``current_names`` (the full, current
    ``config/sources.yaml`` name set -- every configured source, enabled or not).

    Covers two ways a row goes orphaned: (1) a source *renamed* in config -- ``upsert_source`` is
    keyed by ``name``, so a rename creates a brand-new row rather than updating the old one, and
    the old row is never touched again (confirmed live: ids 1562-1565, "arXiv ... (keyword
    query)", orphaned by a rename to "... (keyword-filtered)" at ids 1657-1660 -- the old rows sat
    at ``active=true``, ``last_fetched_at=NULL`` forever, exactly the D9
    ``sources_enabled_fetched_recently`` failure this fixes); (2) a source *removed* from config
    outright. Never deletes a row (F1: no fetched-content-adjacent row is ever destroyed), only
    flips ``active``; a row already ``active=false`` is left alone (no needless write). Guarded
    against an empty/falsy ``current_names`` (a bad or empty config load must never wipe every
    source's ``active`` flag)."""
    if not current_names:
        log.warning("source.deactivate_orphaned_skipped_empty_names")
        return 0
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "UPDATE sources SET active = false WHERE active = true AND name != ALL(%(names)s) RETURNING id",
            {"names": list(current_names)},
        )
        rows = cur.fetchall()
    if rows:
        log.info("source.deactivated_orphaned", count=len(rows), ids=[r["id"] for r in rows])
    return len(rows)


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


#: Round-6 D9 fix (docs/qa/loop/round_5_judge.md D9, the "owl leak" -- event 257 on item 2463, an
#: archived out_of_scope wildlife story): items that must never reach the ``analyze`` stage, even
#: when some earlier bug or manual backfill left ``processed_stages`` without ``'analyze'`` on
#: them. ``run_analyze`` (eoa.pipeline.analyze) already applies an equivalent filter in Python
#: (skips/mark-only for ``domain is None`` and any ``level`` below its own ``min_level``, which
#: defaults to excluding 'archive'), but that app-level filter is only as good as every call site
#: remembering to apply it -- this SQL-level filter is a second, unconditional guard specifically
#: for the ``analyze`` stage, the one whose output (events/edges/entities) is the most expensive to
#: silently mis-scope. Deliberately scoped to ``stage == 'analyze'`` only: ``classify`` must still
#: see every item (domain isn't set yet), and ``triage`` must still see every classified item
#: (level isn't set yet) -- gating either of those the same way would starve the pipeline.
_ANALYZE_STAGE_SCOPE_FILTER = (
    "AND domain IS DISTINCT FROM 'out_of_scope' AND level IS DISTINCT FROM 'archive'"
)


def get_items_for_stage(
    stage: str, limit: int = 50, *, item_ids: list[int] | None = None
) -> list[dict[str, Any]]:
    """Return up to `limit` clean items that have not yet completed pipeline `stage`.

    F22 (docs/REVIEW_2026-09-05.md): ``item_ids``, when given, additionally restricts the result to
    those specific ids -- used by ``orchestrator.jobs``'s ``post_tenders_catchup`` mini-stage to
    embed/classify/triage only the handful of tender-derived items created by the ``tenders`` stage
    this run, instead of sweeping the whole stage backlog (which is ordered oldest-first and would
    likely not even reach today's newest rows within a short budget).

    Round-6 D9: for ``stage == "analyze"`` only, also excludes ``domain = 'out_of_scope'`` and
    ``level = 'archive'`` items at the SQL level (see :data:`_ANALYZE_STAGE_SCOPE_FILTER`)."""
    extra_filter = _ANALYZE_STAGE_SCOPE_FILTER if stage == "analyze" else ""
    query = f"""
        SELECT * FROM items
        WHERE security_status = 'clean'
          AND NOT (%(stage)s = ANY(COALESCE(processed_stages, '{{}}')))
          AND (%(item_ids)s::bigint[] IS NULL OR id = ANY(%(item_ids)s::bigint[]))
          {extra_filter}
        ORDER BY fetched_at NULLS LAST, id
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"stage": stage, "limit": limit, "item_ids": item_ids})
        rows = cur.fetchall()
    return rows


def get_items_stuck_unclassified(limit: int = 50) -> list[dict[str, Any]]:
    """Round-3 D1 (docs/qa/loop/round_2_judge.md, items 52/56/57 and 15 more): clean, non-duplicate
    items whose ``classify`` stage is marked done but whose ``domain`` is still NULL -- an earlier
    persist failure or a later reset left them invisible to both ``run_classify`` (stage done) and
    ``run_triage`` (skips domain NULL), so they never moved again. Returned oldest-first so
    ``run_classify`` can re-classify them as a self-healing tail of its normal batch."""
    query = """
        SELECT * FROM items
        WHERE security_status = 'clean'
          AND dedup_of IS NULL
          AND domain IS NULL
          AND 'classify' = ANY(COALESCE(processed_stages, '{}'))
        ORDER BY fetched_at NULLS LAST, id
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"limit": limit})
        return cur.fetchall()


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

#: Round-3 (docs/qa/loop/round_2_judge.md D3, item 81): two events of the same item AND the same
#: `kind` at or above this :func:`event_semantic_similarity` are the same underlying fact re-worded
#: by a later analyze pass ("מינוי אמיתי נורקין לראש פעילות אנדוריל בישראל" vs "מינוי עמירם נורקין
#: למנהל הפעילות הישראלית של אנדוריל" -- three spellings of one appointment became three rows).
#: The `(item_id, kind, lower(title))` unique index only catches verbatim repeats; the 0.9
#: character threshold above only catches typo-level rewording. Same kind is a much stronger prior
#: for "same event" than a different kind, so the bar is lower -- but a same-kind candidate whose
#: parties *conflict* (both non-empty, nothing in common) is never merged, whatever the titles say.
EVENT_SAME_KIND_DEDUP_THRESHOLD = 0.6

#: Same-kind "weak" merge: a pair that clears neither the typo-level character bar
#: (:data:`EVENT_TITLE_DEDUP_THRESHOLD`) nor the token bar above may still be the same event
#: when *both* signals are moderately high at once -- item 81's "מינוי אמיתי נורקין לראש פעילות
#: אנדוריל בישראל" vs "מינוי עמירם נורקין למנהל הפעילות הישראלית של אנדוריל" shares 4 of 7 content
#: words (token 0.49) and 66% of its characters. Either signal alone at these levels is not
#: enough: short Hebrew sentences that merely share a frame ("השלכות על שוק ההגנה האווירית" vs
#: "השלכות על תעשיות ישראליות") reach 0.60 character similarity with one shared word.
EVENT_SAME_KIND_WEAK_TOKEN = 0.45
EVENT_SAME_KIND_WEAK_CHAR = 0.55

_HE_PREFIXES = ("וש", "וב", "ול", "ומ", "וה", "וכ", "ש", "ב", "ל", "מ", "ה", "כ", "ו")
_TITLE_STOPWORDS = frozenset(
    [
        "של",
        "עם",
        "בין",
        "את",
        "על",
        "ידי",
        "אל",
        "אצל",
        "לפי",
        "או",
        "גם",
        "כי",
        "אם",
        "the",
        "of",
        "to",
        "for",
        "and",
        "a",
        "an",
        "with",
        "by",
        "in",
        "on",
        "at",
        "from",
        "as",
        "is",
    ]
)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")
_NUMBER_RE = re.compile(r"(?<![\w.])\d[\d.,-]*(?![\w])")
_TOKEN_RE = re.compile(r"[\w'\"״׳-]+", re.UNICODE)
_TOKEN_STRIP = "'\"״׳-"


def _title_tokens(title: str) -> set[str]:
    """Content tokens of an event title for :func:`event_semantic_similarity`: casefolded,
    stop-words dropped, and a single Hebrew clitic prefix (ו/ה/ב/ל/מ/ש/כ, or ו+one of them)
    stripped from tokens long enough to survive it, so "לאנדוריל" and "אנדוריל" (or "הפעילות" and
    "פעילות") count as the same word."""
    out: set[str] = set()
    for tok in _TOKEN_RE.findall(_normalize_title_for_similarity(title)):
        tok = tok.strip(_TOKEN_STRIP)
        if not tok or tok in _TITLE_STOPWORDS:
            continue
        for pref in _HE_PREFIXES:
            if tok.startswith(pref) and len(tok) - len(pref) >= 3:
                tok = tok[len(pref) :].strip(_TOKEN_STRIP)
                break
        if tok and tok not in _TITLE_STOPWORDS:
            out.add(tok)
    return out


def _token_score(a: str | None, b: str | None) -> float:
    """``(jaccard + containment) / 2`` over :func:`_title_tokens`; ``0.0`` when either side has no
    content tokens."""
    ta, tb = _title_tokens(a or ""), _title_tokens(b or "")
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    return (shared / len(ta | tb) + shared / min(len(ta), len(tb))) / 2


def same_kind_duplicate(a: str | None, b: str | None) -> bool:
    """Round-3 decision rule for two titles of the same item and the same `kind`: a typo-level
    character match (:data:`EVENT_TITLE_DEDUP_THRESHOLD`), a strong content-word overlap
    (:data:`EVENT_SAME_KIND_DEDUP_THRESHOLD`), or both signals moderately high at once
    (:data:`EVENT_SAME_KIND_WEAK_TOKEN` / :data:`EVENT_SAME_KIND_WEAK_CHAR`). Blocked outright when
    the titles name different numbers ("אופק 19" vs "דור 1", "T-REX 25-2" vs "T-REX 2026") or
    different Latin proper nouns (:func:`_distinct_proper_nouns`)."""
    if _distinct_numbers(a, b) or _distinct_proper_nouns(a, b):
        return False
    char = event_title_similarity(a, b)
    if char >= EVENT_TITLE_DEDUP_THRESHOLD:
        return True
    tok = _token_score(a, b)
    if tok >= EVENT_SAME_KIND_DEDUP_THRESHOLD:
        return True
    return tok >= EVENT_SAME_KIND_WEAK_TOKEN and char >= EVENT_SAME_KIND_WEAK_CHAR


def _distinct_numbers(a: str | None, b: str | None) -> bool:
    """True when each title carries a number the other lacks -- two satellites, two exercise
    years, two contract values are two events even if every other word matches."""
    na = {t.strip(".,-") for t in _NUMBER_RE.findall(a or "")}
    nb = {t.strip(".,-") for t in _NUMBER_RE.findall(b or "")}
    na.discard("")
    nb.discard("")
    return bool(na - nb) and bool(nb - na)


def event_semantic_similarity(a: str | None, b: str | None) -> float:
    """Round-3 event-identity similarity in ``[0, 1]``: the greater of the character-level
    :func:`event_title_similarity` and a token score ``(jaccard + containment) / 2`` over
    :func:`_title_tokens`. The token half is what recognises a *re-worded* title -- same content
    words in a different sentence -- while the containment term keeps a short title that is
    wholly contained in a longer one ("שיתוף פעולה עם אלביט מערכות" in "שיתוף פעולה בין אנדוריל
    לאלביט מערכות") from being diluted by the longer one's extra words. Averaging with Jaccard
    stops containment alone from merging two different products that share a brand word."""
    return max(event_title_similarity(a, b), _token_score(a, b))


def _distinct_proper_nouns(a: str | None, b: str | None) -> bool:
    """True when *each* title names a capitalised Latin-script token (a product/designation/
    company) the other lacks -- "השקת Nexus Observer" vs "השקת Nexus Sentinel" are two launches,
    however similar the surrounding words. A one-sided difference (one title just adds a name) is
    not a conflict; neither is a difference in lowercase words ("production" vs "laser")."""
    na = {t.casefold() for t in _PROPER_NOUN_RE.findall(a or "")}
    nb = {t.casefold() for t in _PROPER_NOUN_RE.findall(b or "")}
    return bool(na - nb) and bool(nb - na)


def _parties_conflict(a: Sequence[str] | None, b: Sequence[str] | None) -> bool:
    """True only when both party lists are non-empty and share no name (casefolded, whitespace-
    normalised; a name that is a prefix/suffix of the other -- "Elbit" / "Elbit Systems" -- counts
    as shared). An empty side never conflicts: the model often omits parties on a re-extraction."""
    na = {_normalize_title_for_similarity(x) for x in (a or []) if x and x.strip()}
    nb = {_normalize_title_for_similarity(x) for x in (b or []) if x and x.strip()}
    if not na or not nb:
        return False
    for x in na:
        for y in nb:
            if x == y or x in y or y in x:
                return False
    return True


def _union_parties(existing: Sequence[str] | None, new: Sequence[str] | None) -> list[str]:
    """Existing parties first, then any new name not already present (casefold/prefix-aware)."""
    out: list[str] = [p for p in (existing or []) if p and p.strip()]
    for cand in new or []:
        if not cand or not cand.strip():
            continue
        c = _normalize_title_for_similarity(cand)
        if any(
            c == _normalize_title_for_similarity(p) or c in _normalize_title_for_similarity(p) for p in out
        ):
            continue
        out.append(cand)
    return out


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


def _find_near_duplicate_event(
    item_id: int, kind: str, title: str, parties: Sequence[str] | None = None
) -> dict[str, Any] | None:
    """The best existing event of `item_id` that is the same underlying fact as `(kind, title)`,
    or ``None``. Two bars (see the threshold constants): a candidate with a *different* `kind`
    must be a character-level near-duplicate (Q3-6b, :data:`EVENT_TITLE_DEDUP_THRESHOLD`); a
    candidate with the *same* `kind` only needs :func:`event_semantic_similarity` >=
    :data:`EVENT_SAME_KIND_DEDUP_THRESHOLD` and non-conflicting parties (round-3, item 81). Verbatim
    same-kind repeats are still handled by the `(item_id, kind, lower(title))` upsert in
    :func:`insert_event`; this is the layer for everything that index can't see."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, kind, title, date, amount_usd, currency, parties, customer, program, "
            "summary_he, confidence FROM events WHERE item_id = %(item_id)s AND title IS NOT NULL",
            {"item_id": item_id},
        )
        candidates = cur.fetchall()
    return _best_duplicate_candidate(kind, title, parties, candidates)


def _best_duplicate_candidate(
    kind: str, title: str, parties: Sequence[str] | None, candidates: Sequence[dict[str, Any]]
) -> dict[str, Any] | None:
    """Pure part of :func:`_find_near_duplicate_event` (unit-testable without a DB)."""
    best: dict[str, Any] | None = None
    best_score = 0.0
    for cand in candidates:
        if cand["kind"] == kind:
            if _normalize_title_for_similarity(cand["title"]) == _normalize_title_for_similarity(title):
                continue  # the unique-index upsert handles the verbatim case
            if _parties_conflict(parties, cand.get("parties")):
                continue
            if not same_kind_duplicate(title, cand["title"]):
                continue
            score = event_semantic_similarity(title, cand["title"])
            threshold = 0.0  # the decision was made by same_kind_duplicate; score only ranks
        else:
            score = event_title_similarity(title, cand["title"])
            threshold = EVENT_TITLE_DEDUP_THRESHOLD
        if score >= threshold and score > best_score:
            best, best_score = cand, score
    return best


def _merge_into_existing_event(existing: dict[str, Any], *, kind: str, **fields: Any) -> int:
    """Q3-6b: merge a new extraction's fields into `existing` (a near-duplicate event with a
    different `kind`), keeping the more specific `kind` and the same non-null-wins/richer-parties/
    max-confidence policy as the exact-match upsert in :func:`insert_event`."""
    merged_kind = more_specific_event_kind(existing["kind"], kind)
    # Round-3: parties are unioned (existing order first) rather than "keep existing unless empty"
    # -- a re-extraction that adds a genuinely new party (item 50: Palantir) must not be dropped.
    fields = {**fields, "parties": _union_parties(existing.get("parties"), fields.get("parties"))}
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            UPDATE events SET
                kind = %(kind)s,
                date = COALESCE(events.date, %(date)s),
                amount_usd = COALESCE(events.amount_usd, %(amount_usd)s),
                currency = COALESCE(events.currency, %(currency)s),
                parties = %(parties)s,
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


def merge_duplicate_events(*, item_id: int | None = None, dry_run: bool = True) -> list[dict[str, Any]]:
    """Round-3 maintenance (docs/qa/loop/round_2_judge.md D3): collapse *already stored* duplicate
    events with the same rule :func:`insert_event` now applies on the way in. Per item, events are
    scanned in id order; each one is compared (:func:`_best_duplicate_candidate`) against the
    survivors kept so far and, on a match, merged into that survivor -- non-null fields kept from
    the survivor first, parties unioned, the higher confidence, the more specific kind -- and the
    duplicate row deleted. Returns one record per merge ``{item_id, kept_id, removed_id, score,
    kept_title, removed_title}``; with ``dry_run=True`` (the default) nothing is written."""
    where = "WHERE item_id = %(item_id)s" if item_id is not None else ""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, item_id, kind, title, date, amount_usd, currency, parties, customer, program, "
            f"summary_he, confidence FROM events {where} ORDER BY item_id, id",
            {"item_id": item_id},
        )
        rows = cur.fetchall()
    by_item: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        by_item.setdefault(int(r["item_id"]), []).append(r)
    merges: list[dict[str, Any]] = []
    for iid, evs in by_item.items():
        survivors: list[dict[str, Any]] = []
        for ev in evs:
            match = None
            if ev.get("title"):
                match = _best_duplicate_candidate(ev["kind"], ev["title"], ev.get("parties"), survivors)
                if match is None:
                    # verbatim same-kind title repeats can also exist historically (pre-0016 rows)
                    for cand in survivors:
                        if cand["kind"] == ev["kind"] and _normalize_title_for_similarity(
                            cand["title"]
                        ) == _normalize_title_for_similarity(ev["title"]):
                            match = cand
                            break
            if match is None:
                survivors.append(dict(ev))
                continue
            merged_kind = more_specific_event_kind(match["kind"], ev["kind"])
            for f in ("date", "amount_usd", "currency", "customer", "program", "summary_he"):
                if match.get(f) in (None, "") and ev.get(f) not in (None, ""):
                    match[f] = ev[f]
            match["parties"] = _union_parties(match.get("parties"), ev.get("parties"))
            match["confidence"] = max(float(match.get("confidence") or 0), float(ev.get("confidence") or 0))
            match["kind"] = merged_kind
            merges.append(
                {
                    "item_id": iid,
                    "kept_id": match["id"],
                    "removed_id": ev["id"],
                    "score": round(event_semantic_similarity(match["title"], ev["title"]), 3),
                    "kept_title": match["title"],
                    "removed_title": ev["title"],
                }
            )
            if not dry_run:
                with connection() as conn, conn.cursor() as cur:
                    cur.execute(
                        "UPDATE events SET kind=%(kind)s, date=%(date)s, amount_usd=%(amount_usd)s, "
                        "currency=%(currency)s, parties=%(parties)s, customer=%(customer)s, "
                        "program=%(program)s, summary_he=%(summary_he)s, confidence=%(confidence)s, "
                        "updated_at=now() WHERE id=%(id)s",
                        {
                            k: match.get(k)
                            for k in (
                                "id",
                                "kind",
                                "date",
                                "amount_usd",
                                "currency",
                                "parties",
                                "customer",
                                "program",
                                "summary_he",
                                "confidence",
                            )
                        },
                    )
                    cur.execute("DELETE FROM events WHERE id = %(id)s", {"id": ev["id"]})
                log.info("event.duplicate_merged", item_id=iid, kept_id=match["id"], removed_id=ev["id"])
    return merges


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
        near_dup = _find_near_duplicate_event(item_id, kind, title, parties)
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


# --------------------------------------------------------------------------
# Round-6 D9 continuation (docs/qa/loop/round_6_fixes.md, "R6-entities" task 3): a small,
# conservative, deterministic junk-name-*shape* filter applied at persistence time, kept as a
# local duplicate of `eoa.pipeline.analyze.is_junk_candidate_entity_name` (not imported -- this
# module's convention for a cross-module-boundary helper, e.g. `analyze._event_dedup_key`
# mirroring `report.daily._normalize_event_key`) rather than this round also owning
# `entity_normalize.py`. Catches junk shapes observed live in the out-of-scope-entity population
# that `eoa.pipeline.entity_normalize.is_junk_entity`'s broader technique/generic-concept checks
# miss: a bare plural of a platform/weapon designation ("F-16s"), a generic "who talked" two-word
# phrase ("Western Partners"), or a wildlife/nature word in a name typed kind='company'
# ("Western Burrowing Owl", entity 1208).
# --------------------------------------------------------------------------
_PLATFORM_DESIGNATION_PLURAL_RE = re.compile(r"^[A-Z]{1,3}-?\d{1,3}[A-Za-z]?s$")
_GENERIC_TWO_WORD_STOPLIST = frozenset(
    {
        "western partners",
        "local partners",
        "industry partners",
        "defense officials",
        "government officials",
    }
)
_WILDLIFE_NATURE_WORDS = ("owl", "eagle", "habitat", "wildlife", "conservation")


def _is_junk_shaped_entity_name(name: str | None, kind: str | None = None) -> bool:
    """See the module note above. Mirrors `eoa.pipeline.analyze.is_junk_candidate_entity_name`."""
    if not name or not name.strip():
        return False
    n = name.strip()
    if _PLATFORM_DESIGNATION_PLURAL_RE.match(n):
        return True
    if n.casefold() in _GENERIC_TWO_WORD_STOPLIST:
        return True
    return kind == "company" and any(w in n.casefold() for w in _WILDLIFE_NATURE_WORDS)


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
    if _is_junk_shaped_entity_name(name, kind):
        log.info("entity.rejected_junk_shape", name=name, kind=kind)
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


# --------------------------------------------------------------------------
# cross-source corroboration (2026-09-07 user requirement) -- db/migrations/versions/0026_...,
# eoa.pipeline.corroboration. Every function below is a thin, parameterised-SQL wrapper; the
# actual corroboration logic (candidate matching, official-source detection, status derivation)
# lives entirely in eoa.pipeline.corroboration, which is the only caller of these helpers.
# --------------------------------------------------------------------------


def get_item_for_corroboration(item_id: int) -> dict[str, Any] | None:
    """The subset of an ``items`` row `eoa.pipeline.corroboration` needs for one item: identity,
    the fields the deterministic matcher keys on, and the joined source name/url (an item's own
    ``sources`` row -- distinct from ``items.url``, which is the article's own URL)."""
    query = """
        SELECT i.id, i.url, i.published_at, i.fetched_at, i.created_at, i.domain, i.level, i.security_status,
               i.entities_mentioned, i.dedup_of, i.source_id,
               s.name AS source_name, s.url AS source_url
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.id = %(item_id)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_id": item_id})
        return cur.fetchone()


def get_corroboration_candidates(item_id: int, start: Any, end: Any) -> list[dict[str, Any]]:
    """Other clean, non-duplicate items published within ``[start, end]`` (the caller's own
    ±7-day window around the subject item's ``published_at``) -- the raw candidate pool
    `eoa.pipeline.corroboration` then filters in Python (different registrable domain, shared
    distinctive entities, matching events, ...). Excludes ``item_id`` itself."""
    query = """
        SELECT i.id, i.url, i.published_at, i.fetched_at, i.created_at, i.domain, i.entities_mentioned, i.dedup_of,
               i.source_id, s.name AS source_name, s.url AS source_url
        FROM items i
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE i.id != %(item_id)s
          AND i.security_status = 'clean'
          AND i.published_at IS NOT NULL
          AND i.published_at BETWEEN %(start)s AND %(end)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_id": item_id, "start": start, "end": end})
        return cur.fetchall()


def get_dedup_linked_item_ids(item_id: int, dedup_of: int | None) -> list[int]:
    """Every other item id logically linked to ``item_id`` by dedup: its own ``dedup_of`` target
    (if any), every item pointing at ``item_id`` itself, and -- when ``item_id`` is itself a
    dedup child -- every sibling pointing at the same canonical. Used by
    `eoa.pipeline.corroboration` to classify a candidate as ``kind='duplicate'`` without
    re-deriving dedup logic that already lives in `eoa.pipeline.dedup`."""
    query = """
        SELECT id FROM items
        WHERE dedup_of = %(item_id)s
           OR id = %(dedup_of)s
           OR (%(dedup_of)s IS NOT NULL AND dedup_of = %(dedup_of)s AND id != %(item_id)s)
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_id": item_id, "dedup_of": dedup_of})
        return [row["id"] for row in cur.fetchall()]


def get_events_for_item(item_id: int) -> list[dict[str, Any]]:
    """All ``events`` rows for one item -- used by `eoa.pipeline.corroboration`'s same-event
    matcher (kind + customer/program/amount comparison against a candidate item's own events)."""
    query = """
        SELECT id, item_id, kind, title, date, amount_usd, currency, parties, customer, program
        FROM events
        WHERE item_id = %(item_id)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_id": item_id})
        return cur.fetchall()


def upsert_item_corroboration(
    *,
    item_id: int,
    status: str,
    count: int,
    sources: list[dict[str, Any]],
    method: str | None,
) -> None:
    """Insert or replace the single ``item_corroboration`` row for ``item_id`` (primary key is
    ``item_id`` itself -- a re-check always fully replaces the prior finding, it never merges)."""
    query = """
        INSERT INTO item_corroboration (item_id, status, count, sources, method, checked_at)
        VALUES (%(item_id)s, %(status)s, %(count)s, %(sources)s, %(method)s, now())
        ON CONFLICT (item_id) DO UPDATE SET
            status = EXCLUDED.status,
            count = EXCLUDED.count,
            sources = EXCLUDED.sources,
            method = EXCLUDED.method,
            checked_at = EXCLUDED.checked_at
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            query,
            {
                "item_id": item_id,
                "status": status,
                "count": count,
                "sources": Json(sources),
                "method": method,
            },
        )
    log.debug("item_corroboration.upserted", item_id=item_id, status=status, count=count)


def get_item_corroboration(item_id: int) -> dict[str, Any] | None:
    """The stored corroboration record for one item, or ``None`` if it was never checked (the API
    layer maps that to the contract's ``{"status": "unknown", "count": 0, "sources": [],
    "checked_at": null}`` default -- see ``eoa.api.services``)."""
    query = "SELECT item_id, status, count, sources, method, checked_at FROM item_corroboration WHERE item_id = %(item_id)s"
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_id": item_id})
        return cur.fetchone()


def get_item_corroboration_map(
    item_ids: list[int], *, timeout: float | None = None
) -> dict[int, dict[str, Any]]:
    """Bulk form of :func:`get_item_corroboration` for a batch of ids (item list/report
    rendering) -- one query instead of N. Ids never checked are simply absent from the result.

    ``timeout`` (seconds) overrides the connection pool's own default wait -- passed by every
    best-effort caller (``eoa.api.services._attach_corroboration``,
    ``eoa.report.daily._corroboration_payload_map_safe``) via
    ``eoa.pipeline.corroboration.corroboration_payload_map``'s own short default, so an item list/
    report render degrades to "no markers" quickly instead of blocking on the pool's much longer
    default wait when the DB is briefly unreachable."""
    if not item_ids:
        return {}
    query = (
        "SELECT item_id, status, count, sources, method, checked_at FROM item_corroboration "
        "WHERE item_id = ANY(%(item_ids)s)"
    )
    with connection(timeout=timeout) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"item_ids": item_ids})
        return {row["item_id"]: row for row in cur.fetchall()}


def get_recent_in_scope_item_ids(days: int = 7) -> list[int]:
    """In-scope (``level`` red/orange/yellow), clean, non-duplicate item ids from the last
    ``days`` days -- the nightly re-check population (corroboration arrives late: a corroborating
    second article can be ingested days after the original) and the QA D1
    ``corroboration_populated_for_recent_in_scope`` check's denominator."""
    query = """
        SELECT id FROM items
        WHERE level IN ('red', 'orange', 'yellow')
          AND security_status = 'clean'
          AND dedup_of IS NULL
          AND COALESCE(published_at, fetched_at, created_at) >= now() - (%(days)s || ' days')::interval
        ORDER BY id
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, {"days": days})
        return [row["id"] for row in cur.fetchall()]
