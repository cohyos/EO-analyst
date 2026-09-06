"""Stage: report/indicators — "מעקב אינדיקטורים" (I&W with status over time; D5).

``indicator_watchlist`` (migration ``0023``) tracks each ``OutlookIndicator.text_he`` raised in a
daily/weekly report's ``outlook`` across issues, instead of it being a one-shot line that vanishes
the moment the next issue is drafted (docs/REPORT_TEMPLATE_BENCHMARK.md D5). Per report build:

1. :func:`check_maturation` — every currently-``open`` row of this ``kind`` is checked against
   *this issue's* items by a deterministic key-term match (:func:`_extract_key_terms`): a match
   matures the indicator (cites the matching item); no match past 30 days (``_DROP_AFTER_DAYS``)
   drops it; otherwise it stays open.
2. This issue's own ``outlook`` indicator texts are dedupe-upserted against whatever stays open
   (normalised-text similarity ≥ 0.85, :data:`_DEDUPE_SIMILARITY`) — a close reword bumps the
   existing row's ``last_seen``; anything new inserts a fresh ``open`` row.
3. :func:`render_watchlist_table` turns the resulting rows into one markdown table (אינדיקטור |
   מאז | סטטוס | ראיה), wired as an additive ``extra_sections`` entry
   (``position="after_outlook"`` — renders immediately after "מבט קדימה", i.e. "in the outlook
   area") into ``eoa.report.daily.build_daily`` / ``eoa.report.weekly.build_weekly``.

The LLM never sees or writes this table: it only ever supplies the ``outlook`` text lines fed into
step 2, exactly as it always has — this module (and the two report builders' additive-hook calls
into it) is the only thing that reads/writes ``indicator_watchlist``.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
from typing import Any

import structlog

from eoa.db import connection
from eoa.report.docx_builder import fmt_date

log = structlog.get_logger(__name__)

SECTION_TITLE_HE = "מעקב אינדיקטורים"

#: Normalised-text similarity (``difflib.SequenceMatcher.ratio``, max of both orderings — see
#: ``eoa.report.daily._domain_similarity`` for why not-quite-symmetric ratios matter) above which
#: an incoming indicator line is treated as a reword of an already-open one, not a new indicator.
_DEDUPE_SIMILARITY = 0.85

#: An open indicator with no matching item for this many days is dropped (D5: "אינדיקטור מאתמול
#: ... בוטל").
_DROP_AFTER_DAYS = 30

#: A "key term" is an English/alphanumeric token of 3+ characters -- per docs/CONVENTIONS.md
#: ("טכניים באנגלית בסוגריים בהופעה ראשונה"), a company/system/programme name in this corpus is
#: almost always the English term inside the Hebrew sentence (e.g. "מערכת ה-DROIC החדשה"), not a
#: distinguishable Hebrew proper noun (Hebrew carries no letter-case). An indicator with no such
#: term simply never matures by this deterministic test and ages out at 30 days instead --
#: consistent with rule 6 ("never invent") rather than guessing a fuzzy Hebrew-text match.
_KEY_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")

_ROW_STATUS_LABELS_HE = {"new": "חדש", "open": "פתוח", "matured": "הבשיל", "dropped": "בוטל"}
_ROW_STATUS_ORDER = {"new": 0, "open": 1, "matured": 2, "dropped": 3}


def _normalize_text(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _similarity(a: str, b: str) -> float:
    return max(
        difflib.SequenceMatcher(None, a, b).ratio(),
        difflib.SequenceMatcher(None, b, a).ratio(),
    )


def extract_key_terms(text_he: str | None) -> set[str]:
    """Company/system/programme tokens in ``text_he`` — see the module-level note on
    :data:`_KEY_TERM_RE`."""
    return {m.casefold() for m in _KEY_TERM_RE.findall(text_he or "")}


def _item_matches_indicator(text_he: str, item: dict[str, Any]) -> bool:
    terms = extract_key_terms(text_he)
    if not terms:
        return False
    haystack = " ".join(
        filter(None, [item.get("title"), item.get("summary_he"), item.get("so_what_he")])
    ).casefold()
    return any(term in haystack for term in terms)


# --------------------------------------------------------------------------
# DB access
# --------------------------------------------------------------------------


#: F4-style optional-section convention (see `eoa.db.connection`'s own docstring and
#: `eoa.report.bd_territory`'s identical `connection(timeout=5)` calls): every DB call in this
#: module is decorative, never load-bearing, so an unreachable/slow DB must fail fast into the
#: caller's `except` instead of blocking the whole report build -- see `_fetch_open_indicators`/
#: `_apply_maturation`/`_upsert_open` below.


def _fetch_open_indicators(kind: str) -> list[dict[str, Any]]:
    sql = """
        SELECT id, text_he, source_report_id, first_seen, last_seen, status, kind
        FROM indicator_watchlist
        WHERE kind = %(kind)s AND status = 'open'
        ORDER BY first_seen ASC
    """
    with connection(timeout=5) as conn, conn.cursor() as cur:
        cur.execute(sql, {"kind": kind})
        return cur.fetchall()


def _apply_maturation(matured: list[dict[str, Any]], dropped: list[dict[str, Any]]) -> None:
    if not matured and not dropped:
        return
    with connection(timeout=5) as conn, conn.cursor() as cur:
        for ind in matured:
            cur.execute(
                """
                UPDATE indicator_watchlist
                SET status = 'matured', matured_evidence_item_id = %(eid)s, last_seen = now()
                WHERE id = %(id)s
                """,
                {"eid": ind["matured_evidence_item_id"], "id": ind["id"]},
            )
        for ind in dropped:
            cur.execute(
                "UPDATE indicator_watchlist SET status = 'dropped', last_seen = now() WHERE id = %(id)s",
                {"id": ind["id"]},
            )


def _upsert_open(
    texts: list[str],
    still_open: list[dict[str, Any]],
    *,
    kind: str,
    source_report_id: int | None,
    now: dt.datetime,
) -> tuple[set[int], list[dict[str, Any]]]:
    """Dedupe-upsert ``texts`` against ``still_open`` (rows that survived
    :func:`check_maturation` this issue) — returns ``(touched_ids, newly_created_rows)``."""
    touched_ids: set[int] = set()
    newly_created: list[dict[str, Any]] = []
    with connection(timeout=5) as conn, conn.cursor() as cur:
        for text in texts:
            norm = _normalize_text(text)
            if not norm:
                continue
            match = next(
                (
                    row
                    for row in still_open
                    if row["id"] not in touched_ids
                    and _similarity(norm, _normalize_text(row["text_he"])) >= _DEDUPE_SIMILARITY
                ),
                None,
            )
            if match is not None:
                cur.execute(
                    "UPDATE indicator_watchlist SET last_seen = %(now)s WHERE id = %(id)s",
                    {"now": now, "id": match["id"]},
                )
                touched_ids.add(match["id"])
                continue
            cur.execute(
                """
                INSERT INTO indicator_watchlist (text_he, source_report_id, first_seen, last_seen, status, kind)
                VALUES (%(text)s, %(source_report_id)s, %(now)s, %(now)s, 'open', %(kind)s)
                RETURNING id, text_he, source_report_id, first_seen, last_seen, status, kind
                """,
                {"text": text, "source_report_id": source_report_id, "now": now, "kind": kind},
            )
            newly_created.append(cur.fetchone())
    return touched_ids, newly_created


# --------------------------------------------------------------------------
# maturation / drop
# --------------------------------------------------------------------------


def check_maturation(
    open_indicators: list[dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    now: dt.datetime | None = None,
    max_age_days: int = _DROP_AFTER_DAYS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition ``open_indicators`` into ``(still_open, matured, dropped)`` against this issue's
    ``items`` — a deterministic key-term containment match (:func:`_item_matches_indicator`); the
    first matching item (in ``items``'s own order) wins when more than one matches. An indicator
    with no match older than ``max_age_days`` (by ``first_seen``) is dropped instead of staying
    open forever."""
    now = now or dt.datetime.now(dt.UTC)
    still_open: list[dict[str, Any]] = []
    matured: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for ind in open_indicators:
        match_item = next(
            (it for it in items if it.get("id") is not None and _item_matches_indicator(ind["text_he"], it)),
            None,
        )
        if match_item is not None:
            matured.append(
                {
                    **ind,
                    "matured_evidence_item_id": match_item["id"],
                    "_evidence_n": match_item.get("n"),
                }
            )
            continue
        first_seen = ind.get("first_seen")
        age_days = (now - first_seen).days if isinstance(first_seen, dt.datetime) else 0
        if age_days > max_age_days:
            dropped.append(ind)
        else:
            still_open.append(ind)
    return still_open, matured, dropped


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def process_indicator_watchlist(
    kind: str,
    outlook_indicator_texts: list[str],
    items: list[dict[str, Any]],
    *,
    source_report_id: int | None = None,
    now: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Full per-report pipeline: maturation/drop of existing ``open`` rows against ``items``
    (:func:`check_maturation`, persisted immediately), then dedupe-upsert of
    ``outlook_indicator_texts`` against whatever stays open. Returns every row relevant to *this*
    issue's table — newly matured/dropped rows (shown once) plus every row now open (existing +
    newly added) — each tagged ``_row_status`` (``new``/``open``/``matured``/``dropped``)."""
    now = now or dt.datetime.now(dt.UTC)
    existing_open = _fetch_open_indicators(kind)
    still_open, matured, dropped = check_maturation(existing_open, items, now=now)
    try:
        _apply_maturation(matured, dropped)
    except Exception as exc:
        log.warning("indicator_watchlist_maturation_persist_failed", error=str(exc)[:160], kind=kind)

    touched_ids, newly_created = _upsert_open(
        outlook_indicator_texts, still_open, kind=kind, source_report_id=source_report_id, now=now
    )

    rows: list[dict[str, Any]] = []
    rows += [{**ind, "_row_status": "matured"} for ind in matured]
    rows += [{**ind, "_row_status": "dropped"} for ind in dropped]
    rows += [{**ind, "_row_status": "open"} for ind in still_open]
    rows += [{**ind, "_row_status": "new"} for ind in newly_created]
    log.info(
        "indicator_watchlist_processed",
        kind=kind,
        matured=len(matured),
        dropped=len(dropped),
        open=len(still_open),
        new=len(newly_created),
        deduped=len(touched_ids),
    )
    return rows


def outlook_indicator_texts(outlook: list[Any]) -> list[str]:
    """``text_he`` of every ``OutlookIndicator`` in ``draft.outlook`` — duck-typed (attribute or
    dict access) so this works for the schema object or a plain dict fixture in tests."""
    texts: list[str] = []
    for ind in outlook or []:
        text = getattr(ind, "text_he", None) if not isinstance(ind, dict) else ind.get("text_he")
        if text:
            texts.append(text)
    return texts


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def render_watchlist_table(
    rows: list[dict[str, Any]], citation_items: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The "מעקב אינדיקטורים" markdown table body — headers אינדיקטור | מאז | סטטוס | ראיה.
    ``citation_items`` is the report's own citation registry (already extended with every
    ``items`` entry passed to :func:`process_indicator_watchlist`); a ``matured`` row's evidence
    item is looked up there by id for its ``[n]`` — never fabricated, since the evidence item is
    always one of the report's own numbered items already. ``None`` when ``rows`` is empty (same
    "nothing to show, render nothing" convention as ``eoa.report.israel_section``)."""
    if not rows:
        return None
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    lines = ["| אינדיקטור | מאז | סטטוס | ראיה |", "|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: _ROW_STATUS_ORDER.get(r.get("_row_status", ""), 9)):
        status_he = _ROW_STATUS_LABELS_HE.get(row.get("_row_status", ""), "—")
        since = fmt_date(row.get("first_seen"))
        evidence = "—"
        eid = row.get("matured_evidence_item_id")
        if eid is not None:
            entry = by_id.get(eid)
            n = entry.get("n") if entry else row.get("_evidence_n")
            if n is not None:
                evidence = f"[{n}]"
        text_cell = (row.get("text_he") or "—").replace("|", "/").replace("\n", " ")
        lines.append(f"| {text_cell} | {since} | {status_he} | {evidence} |")
    return {"title_he": SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def build_indicator_watchlist_section(
    kind: str,
    outlook: list[Any],
    items: list[dict[str, Any]],
    citation_items: list[dict[str, Any]],
    *,
    source_report_id: int | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """One-call convenience wrapper for ``eoa.report.daily``/``eoa.report.weekly``: runs
    :func:`process_indicator_watchlist` on ``draft.outlook``'s own text and renders the table.
    Returns ``(extra_section_or_none, rows)`` — the caller folds ``rows`` (ids tagged ``new``/
    ``open``) into its own ``reports.report_state.indicator_ids`` (see
    ``eoa.report.deltas.build_report_state``)."""
    texts = outlook_indicator_texts(outlook)
    rows = process_indicator_watchlist(kind, texts, items, source_report_id=source_report_id, now=now)
    return render_watchlist_table(rows, citation_items), rows


__all__ = [
    "SECTION_TITLE_HE",
    "build_indicator_watchlist_section",
    "check_maturation",
    "extract_key_terms",
    "outlook_indicator_texts",
    "process_indicator_watchlist",
    "render_watchlist_table",
]
