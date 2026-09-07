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

R8-reports #3/#4 (round-7 judge D6 #7/#8): two more rendering rules, both in
:func:`render_watchlist_table`:

- **Evidence column.** ``[n]`` cites the item(s) whose key terms match the indicator's own text
  (:func:`_evidence_cell`) — not just a row that matured *this issue*; "—" only when truly no
  item in the report matches. A ``dropped`` row (by definition unmatched at drop time) always
  stays "—".
- **Daily per-story cap** (:func:`_cap_watchlist_rows`, ``kind == "daily"`` only): at most 3 rows
  per cluster of rows sharing the same top-2 content tokens (a coarser "same underlying story"
  test than step 2's own reword-collapsing dedupe), preferring a row with evidence and, among
  ties, the most recently seen; the table is then capped at 8 rows total, keeping the
  longest-tracked (oldest ``first_seen``) rows when trimming further. Not applied to the
  weekly/monthly tables, which the round-7 judge did not report as over-crowded.
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


_TOKEN_RE = re.compile(r"[A-Za-z֐-׿0-9][A-Za-z֐-׿0-9\-״\"]{2,}")
_STOP_HE = {
    "להערכתנו",
    "צפויה",
    "צפוי",
    "צפויים",
    "עשויה",
    "עשוי",
    "בהתאם",
    "ממועד",
    "הדיווח",
    "הדוח",
    "תוך",
    "כחודשיים",
    "מה",
    "שמאפשר",
    "לאור",
    "אחר",
    "על",
    "פני",
    "בין",
    "של",
    "את",
    "עד",
    "לא",
    "פחות",
    "יותר",
    "זו",
    "זה",
    "עם",
}


def _content_tokens(text: str) -> set[str]:
    toks = set()
    for t in _TOKEN_RE.findall(text or ""):
        t = t.strip('״"').casefold()
        if t in _STOP_HE or len(t) < 3:
            continue
        # strip the Hebrew definite-article / conjunction prefixes so "הכטב״מים" ~ "כטב״מים"
        for pref in ("וה", "שה", "ה", "ו", "ל", "ב", "מ"):
            if t.startswith(pref) and len(t) - len(pref) >= 3:
                t = t[len(pref) :]
                break
        toks.add(t)
    return toks


def same_indicator(a: str, b: str) -> bool:
    """Round-6 judge (D6 worst #7): 10 watchlist rows for 3 distinct indicators -- the model
    rewords the same indicator each issue ("אספקת 280 הכטב״מים לטייוואן צפויה להתפרס ... עד 2029"
    vs "... להתבצע בהדרגה ... עד 2029"), and a pure character ratio at 0.85 misses that. Two texts
    are the same indicator when their character similarity clears the old threshold OR their
    content-token overlap coefficient (vs the smaller set; Hebrew prefixes stripped, numbers
    included) is >= 0.5, or >= 0.3 when they also share two numbers (amount + year)."""
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return False
    if _similarity(na, nb) >= _DEDUPE_SIMILARITY:
        return True
    ta, tb = _content_tokens(na), _content_tokens(nb)
    if len(ta) < 3 or len(tb) < 3:
        return False
    shared = ta & tb
    overlap = len(shared) / min(len(ta), len(tb))  # overlap coefficient: verbs/adverbs differ, subjects don't
    shared_numbers = sum(1 for t in shared if t.isdigit())
    return overlap >= 0.5 or (shared_numbers >= 2 and overlap >= 0.3)


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
        accepted_this_issue: list[str] = []
        for text in texts:
            norm = _normalize_text(text)
            if not norm:
                continue
            if any(same_indicator(text, prev) for prev in accepted_this_issue):
                continue  # the same issue's outlook restated one indicator twice
            accepted_this_issue.append(text)
            match = next(
                (
                    row
                    for row in still_open
                    if row["id"] not in touched_ids and same_indicator(text, row["text_he"])
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


def _evidence_cell(
    row: dict[str, Any],
    by_id: dict[int, dict[str, Any]],
    items: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> str:
    """R8-reports #3 (round-7 judge D6 #7): the "ראיה" column used to cite ``[n]`` only for a row
    that matured this issue (``matured_evidence_item_id``), leaving every ``open``/``new``/
    ``dropped`` row blank even when this issue's own ``items`` plainly carry a matching story —
    the weekly watchlist's evidence column was "—" on all 13 rows. A ``matured`` row keeps its
    precise, deterministic evidence item first; otherwise this falls back to a fresh
    :func:`_item_matches_indicator` search over ``items`` (up to ``limit`` distinct citations,
    report order) — a ``dropped`` row (by definition unmatched at drop time) and a row with no
    match at all correctly stay "—"."""
    eid = row.get("matured_evidence_item_id")
    if eid is not None:
        entry = by_id.get(eid)
        n = entry.get("n") if entry else row.get("_evidence_n")
        if n is not None:
            return f"[{n}]"
    if row.get("_row_status") == "dropped":
        return "—"
    text = row.get("text_he") or ""
    if not text:
        return "—"
    ns: list[int] = []
    seen_ids: set[int] = set()
    for it in items:
        iid = it.get("id")
        if iid is None or iid in seen_ids:
            continue
        if not _item_matches_indicator(text, it):
            continue
        entry = by_id.get(iid)
        n = entry.get("n") if entry else it.get("n")
        if n is None:
            continue
        seen_ids.add(iid)
        ns.append(n)
        if len(ns) >= limit:
            break
    return "".join(f"[{n}]" for n in ns) if ns else "—"


def _cluster_key(text_he: str) -> tuple[str, str]:
    """A coarse "story" key for :func:`_cap_watchlist_rows`: the two longest content tokens in
    ``text_he`` (:func:`_content_tokens`, tie-broken alphabetically so the key is stable) —
    deliberately coarser than :func:`same_indicator`'s own overlap test (which already collapses
    near-identical rewordings at insert time, see :data:`_DEDUPE_SIMILARITY`): two rows phrased
    distinctly enough to both stay open can still, in substance, be the same underlying story
    (round-7 judge D6 #8: 10 of 11 daily rows resting on just 2 stories)."""
    toks = sorted(_content_tokens(_normalize_text(text_he)), key=lambda t: (-len(t), t))
    return (toks[0], toks[1]) if len(toks) >= 2 else (toks[0], "") if toks else ("", "")


def _cap_watchlist_rows(
    rows: list[dict[str, Any]], *, max_per_cluster: int = 3, max_total: int = 8
) -> list[dict[str, Any]]:
    """R8-reports #4 (round-7 judge D6 #8): even after round-6's ``same_indicator`` reword-
    collapsing (dedupe at *insert* time), distinct-enough phrasings of the same underlying story
    can each still get their own row and crowd the daily table. Two passes, daily table only (see
    :func:`render_watchlist_table`'s ``kind`` gate):

    1. Cluster rows by :func:`_cluster_key`; keep at most ``max_per_cluster`` rows per cluster,
       preferring a row with evidence (``matured`` or a ``matured_evidence_item_id``) and, among
       ties, the most recently seen (``last_seen``, falling back to ``first_seen``).
    2. Cap the surviving rows at ``max_total`` total, keeping the oldest-open ones (ascending
       ``first_seen``) when trimming further — a longer-tracked indicator is more, not less,
       reader-relevant than one just opened.
    """

    def _has_evidence(r: dict[str, Any]) -> bool:
        return r.get("_row_status") == "matured" or r.get("matured_evidence_item_id") is not None

    _epoch = dt.datetime.min.replace(tzinfo=dt.UTC)

    def _recency(r: dict[str, Any]) -> Any:
        return r.get("last_seen") or r.get("first_seen") or _epoch

    clusters: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        clusters.setdefault(_cluster_key(row.get("text_he") or ""), []).append(row)

    kept: list[dict[str, Any]] = []
    for members in clusters.values():
        if len(members) <= max_per_cluster:
            kept.extend(members)
            continue
        ordered = sorted(members, key=lambda r: (_has_evidence(r), _recency(r)), reverse=True)
        kept.extend(ordered[:max_per_cluster])

    if len(kept) <= max_total:
        return kept
    _future = dt.datetime.max.replace(tzinfo=dt.UTC)
    return sorted(kept, key=lambda r: r.get("first_seen") or _future)[:max_total]


def render_watchlist_table(
    rows: list[dict[str, Any]],
    citation_items: list[dict[str, Any]],
    items: list[dict[str, Any]] | None = None,
    *,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """The "מעקב אינדיקטורים" markdown table body — headers אינדיקטור | מאז | סטטוס | ראיה.
    ``citation_items`` is the report's own citation registry (already extended with every
    ``items`` entry passed to :func:`process_indicator_watchlist`); ``items`` (this issue's own
    report items, same list) feeds :func:`_evidence_cell`'s fresh-match fallback. ``None`` when
    ``rows`` is empty (same "nothing to show, render nothing" convention as
    ``eoa.report.israel_section``).

    R8-reports #4: ``kind == "daily"`` additionally runs :func:`_cap_watchlist_rows` first — the
    per-story clustering cap is scoped to the daily table (the round-7 judge's own finding), not
    the weekly/monthly tables, which weren't reported as over-crowded."""
    if not rows:
        return None
    if kind == "daily":
        rows = _cap_watchlist_rows(rows)
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    items = items or []
    lines = ["| אינדיקטור | מאז | סטטוס | ראיה |", "|---|---|---|---|"]
    for row in sorted(rows, key=lambda r: _ROW_STATUS_ORDER.get(r.get("_row_status", ""), 9)):
        status_he = _ROW_STATUS_LABELS_HE.get(row.get("_row_status", ""), "—")
        since = fmt_date(row.get("first_seen"))
        evidence = _evidence_cell(row, by_id, items)
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
    return render_watchlist_table(rows, citation_items, items, kind=kind), rows


__all__ = [
    "SECTION_TITLE_HE",
    "build_indicator_watchlist_section",
    "check_maturation",
    "extract_key_terms",
    "outlook_indicator_texts",
    "process_indicator_watchlist",
    "render_watchlist_table",
]
