"""R10-links: provenance queries linking a deep-search investigation (``jobs.kind='deep_search'``)
to (a) the item that triggered it, (b) its rerun/expansion lineage, and (c) the reports whose
"חקירות עומק" section cites it -- in both directions (investigation -> reports, item -> its
investigations, report -> the investigations it cites).

Read-only against the shared DB pool (``eoa.db.connection``, same ``dict_row`` cursor
``eoa.api.services`` uses). Nothing here writes to the DB.

**Report linkage is reconstructed, not stored.** No column on ``jobs`` or ``reports`` records
which report a given investigation ended up cited in. Instead, ``eoa.report.daily.collect_deep_search``
includes a job in a report's "חקירות עומק" section exactly when: the job's ``finished_at`` falls
inside that report's collection window (``eoa.report.daily._period`` applied to
``reports.period_start``/``period_end``, the same Jerusalem-day -> UTC-timestamp rule the report
builder used at build time), AND (the job has no trigger item -- a free-standing question, always
kept -- OR its trigger item's id is in that report's ``items_included`` array). This module
reapplies exactly that rule after the fact. Two known gaps from doing it this way (see
docs/qa/loop/round_10_fixes.md "R10-links status" for the full writeup):

1. It does not replay ``eoa.report.daily.reconcile_deep_search_reruns``'s rerun-group-collapsing
   or its ``dedup_of`` item folding (Pass 3) -- a job whose *own* trigger item is a plain
   ``dedup_of`` duplicate of the item actually listed in ``items_included`` will not be matched
   here even though the live report's reconciliation would have folded it into the same entry.
2. It only considers report kinds that actually render a "חקירות עומק" section
   (``daily``/``weekly``/``monthly`` -- the only three that call ``collect_deep_search``, per
   ``eoa.report.weekly``/``eoa.report.monthly``); ``bd_territory``/``patent_survey``/
   ``product_line`` reports can enqueue a deep-search job but never render one, so they are
   correctly excluded, not missed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.db import connection

#: Report kinds whose renderer actually includes a "חקירות עומק" (deep-search) section --
#: see the module docstring point 2.
_DEEP_SEARCH_REPORT_KINDS = ("daily", "weekly", "monthly")

#: Payload keys that link a rerun/expansion job back to the job it re-investigates -- mirrors
#: ``eoa.report.daily._RERUN_LINEAGE_KEYS`` exactly (duplicated, not imported, since that name is
#: private to a module owned by another engineer this round; keep the two in sync by hand if the
#: set of lineage keys ever changes).
_RERUN_LINEAGE_KEYS = ("rerun_of_job_id", "expanded_from_job_id")


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _report_period_window(
    period_start: dt.date | None, period_end: dt.date | None
) -> tuple[dt.datetime, dt.datetime]:
    """The UTC timestamp window a ``reports`` row's Jerusalem-calendar-date
    ``period_start``/``period_end`` covers -- the exact rule
    ``eoa.report.daily.collect_deep_search`` applies (via its own call to ``_period``) when it
    decides whether a job's ``finished_at`` falls inside a report's collection window.

    Deliberately reimplemented here (not imported from ``eoa.report.daily``, whose module docstring
    reserves it to another engineer this round for anything beyond the ``collect_deep_search`` entry
    dict shape) -- this is a pure, side-effect-free date/timezone calculation, not report-building
    logic, so duplicating it is safer than adding a new cross-scope import.
    """
    jerusalem = dt.timezone(dt.timedelta(hours=2))  # matches eoa.report.daily.JERUSALEM's offset
    try:
        from zoneinfo import ZoneInfo

        jerusalem = ZoneInfo("Asia/Jerusalem")
    except Exception:
        pass
    end_date = period_end or period_start
    start_date = period_start or end_date
    if start_date is None or end_date is None:
        raise ValueError("period_start and period_end cannot both be None")
    start_ts = dt.datetime.combine(start_date, dt.time.min, tzinfo=jerusalem)
    end_ts = dt.datetime.combine(end_date, dt.time.max, tzinfo=jerusalem)
    return start_ts.astimezone(dt.UTC), end_ts.astimezone(dt.UTC)


def _job_row(job_id: int) -> dict[str, Any] | None:
    return _fetchone(
        "SELECT id AS job_id, payload, result, state, started_at, finished_at, error "
        "FROM jobs WHERE id = %s AND kind = 'deep_search'",
        (job_id,),
    )


def _lineage_kind(payload: dict[str, Any]) -> str:
    if payload.get("rerun_of_job_id") is not None:
        return "rerun"
    if payload.get("expanded_from_job_id") is not None:
        return "expansion"
    return "original"


def _lineage_entry(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    result = row.get("result") or {}
    return {
        "job_id": row["job_id"],
        "outcome": result.get("outcome") or row.get("state"),
        "confidence": result.get("confidence"),
        "finished_at": row.get("finished_at"),
        "kind": _lineage_kind(payload),
    }


def _children(job_id: int) -> list[dict[str, Any]]:
    """Direct reruns/expansions of ``job_id`` -- jobs whose own payload names it as their
    ``rerun_of_job_id``/``expanded_from_job_id`` origin."""
    return _fetchall(
        "SELECT id AS job_id, payload, result, state, started_at, finished_at, error "
        "FROM jobs WHERE kind = 'deep_search' AND ("
        "  (payload->>'rerun_of_job_id')::bigint = %(id)s OR "
        "  (payload->>'expanded_from_job_id')::bigint = %(id)s"
        ")",
        {"id": job_id},
    )


def investigation_lineage(job_id: int) -> list[dict[str, Any]]:
    """The full rerun/expansion chain ``job_id`` belongs to, oldest-first: every job reachable by
    walking ``rerun_of_job_id``/``expanded_from_job_id`` pointers in either direction (ancestors --
    what this job re-investigates -- and descendants -- later reruns/expansions of this job or of
    any of its ancestors). ``[]`` when ``job_id`` isn't a known deep-search job.

    Each entry is ``{job_id, outcome, confidence, finished_at, kind}`` with
    ``kind in ('rerun', 'expansion', 'original')`` describing how *that* job relates to its own
    immediate predecessor (not to ``job_id``) -- exactly one entry in a non-trivial chain is
    ``'original'``: the job with neither lineage key set.
    """
    root = _job_row(job_id)
    if root is None:
        return []

    chain: dict[int, dict[str, Any]] = {root["job_id"]: _lineage_entry(root)}
    visited_ancestors: set[int] = {root["job_id"]}

    # Walk ancestors: this job's own origin, then that job's origin, etc.
    cur = root
    while True:
        payload = cur.get("payload") or {}
        origin_id = payload.get("rerun_of_job_id") or payload.get("expanded_from_job_id")
        if origin_id is None or origin_id in visited_ancestors:
            break
        parent = _job_row(origin_id)
        if parent is None:
            break
        chain[parent["job_id"]] = _lineage_entry(parent)
        visited_ancestors.add(parent["job_id"])
        cur = parent

    # Walk descendants breadth-first from every node discovered so far (job_id and every
    # ancestor), so a sibling rerun of an ancestor also surfaces in the same chain.
    queue = list(chain.keys())
    while queue:
        current_id = queue.pop(0)
        for child in _children(current_id):
            child_id = child["job_id"]
            if child_id in chain:
                continue
            chain[child_id] = _lineage_entry(child)
            queue.append(child_id)

    def _sort_key(entry: dict[str, Any]) -> tuple[bool, Any]:
        finished_at = entry.get("finished_at")
        return (finished_at is None, finished_at)

    return sorted(chain.values(), key=_sort_key)


def _reports_covering(finished_at: dt.datetime | None, item_id: int | None) -> list[dict[str, Any]]:
    """Reports (``daily``/``weekly``/``monthly`` only -- the kinds that render a "חקירות עומק"
    section) whose collection window covers ``finished_at`` and, when ``item_id`` is not ``None``,
    whose ``items_included`` contains it -- exactly ``eoa.report.daily.collect_deep_search`` +
    ``_filter_deep_search_to_items_included``'s own inclusion rule (module docstring point 1 notes
    the one respect in which this is a simplification: it does not also try each `dedup_of`
    ancestor/descendant of ``item_id``)."""
    if finished_at is None:
        return []
    candidates = _fetchall(
        "SELECT id, kind, period_start, period_end, territory, items_included, "
        "path_docx, path_md, path_html, created_at, qa_report "
        "FROM reports WHERE kind = ANY(%(kinds)s) "
        "AND period_start IS NOT NULL AND period_end IS NOT NULL "
        "ORDER BY created_at DESC",
        {"kinds": list(_DEEP_SEARCH_REPORT_KINDS)},
    )
    out: list[dict[str, Any]] = []
    for r in candidates:
        try:
            start_ts, end_ts = _report_period_window(r.get("period_start"), r.get("period_end"))
        except ValueError:
            continue
        if not (start_ts <= finished_at <= end_ts):
            continue
        if item_id is not None and item_id not in (r.get("items_included") or []):
            continue
        out.append(_report_summary(r))
    return out


_REPORT_KIND_LABEL_HE = {
    "daily": "דוח יומי",
    "weekly": "דוח שבועי",
    "monthly": "דוח חודשי",
}


def _report_summary(r: dict[str, Any]) -> dict[str, Any]:
    kind = r.get("kind")
    period_end = r.get("period_end")
    label = _REPORT_KIND_LABEL_HE.get(kind or "", kind or "דוח")
    date_str = period_end.strftime("%d.%m.%Y") if hasattr(period_end, "strftime") else None
    title_he = f"{label} — {date_str}" if date_str else label
    return {
        "id": r["id"],
        "kind": kind,
        "period_end": period_end,
        "territory": r.get("territory"),
        "title_he": title_he,
        "path_html": r.get("path_html"),
    }


def investigation_provenance(job_id: int) -> dict[str, Any] | None:
    """``{job, trigger_item, lineage, reports}`` for one investigation -- ``None`` when ``job_id``
    isn't a known deep-search job.

    - ``job``: ``{job_id, state, question, started_at, finished_at}``.
    - ``trigger_item``: ``{id, title, url, source_name, published_at}`` or ``None`` for a
      free-standing question.
    - ``lineage``: see :func:`investigation_lineage`.
    - ``reports``: see :func:`_reports_covering` -- reports whose "חקירות עומק" section this
      investigation appears in (module docstring: reconstructed from the period-window +
      ``items_included`` rule, not read from a stored back-reference).
    """
    job = _job_row(job_id)
    if job is None:
        return None
    payload = job.get("payload") or {}
    item_id = payload.get("item_id")
    trigger_item = None
    if item_id is not None:
        trigger_item = _fetchone(
            "SELECT i.id, i.title, i.url, s.name AS source_name, "
            "COALESCE(i.published_at, i.fetched_at) AS published_at "
            "FROM items i LEFT JOIN sources s ON s.id = i.source_id WHERE i.id = %s",
            (item_id,),
        )
    return {
        "job": {
            "job_id": job["job_id"],
            "state": job.get("state"),
            "question": payload.get("question"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
        },
        "trigger_item": trigger_item,
        "lineage": investigation_lineage(job_id),
        "reports": _reports_covering(job.get("finished_at"), item_id),
    }


def item_investigations(item_id: int) -> list[dict[str, Any]] | None:
    """Every deep-search investigation triggered by ``item_id``, newest first, with outcome/
    confidence/lineage-count -- ``None`` when the item itself doesn't exist, ``[]`` when it exists
    but has never been investigated."""
    item = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
    if item is None:
        return None
    jobs = _fetchall(
        "SELECT id AS job_id, payload, result, state, started_at, finished_at, error "
        "FROM jobs WHERE kind = 'deep_search' AND (payload->>'item_id')::bigint = %s "
        "ORDER BY created_at DESC",
        (item_id,),
    )
    out: list[dict[str, Any]] = []
    for j in jobs:
        payload = j.get("payload") or {}
        result = j.get("result") or {}
        out.append(
            {
                "job_id": j["job_id"],
                "question": payload.get("question"),
                "state": j.get("state"),
                "error": j.get("error"),
                "outcome": result.get("outcome"),
                "confidence": result.get("confidence"),
                "started_at": j.get("started_at"),
                "finished_at": j.get("finished_at"),
                "rerun_of_job_id": payload.get("rerun_of_job_id"),
                "expanded_from_job_id": payload.get("expanded_from_job_id"),
            }
        )
    return out


def reports_for_item(item_id: int) -> list[dict[str, Any]] | None:
    """Every report whose "חקירות עומק" section cites an investigation triggered by ``item_id`` --
    ``None`` when the item doesn't exist, ``[]`` when it exists but no such report was found (either
    no investigation ran, or none finished inside a rendered report's window)."""
    item = _fetchone("SELECT id FROM items WHERE id = %s", (item_id,))
    if item is None:
        return None
    jobs = _fetchall(
        "SELECT finished_at FROM jobs WHERE kind = 'deep_search' AND state IN ('done', 'partial') "
        "AND finished_at IS NOT NULL AND (payload->>'item_id')::bigint = %s",
        (item_id,),
    )
    seen_report_ids: set[int] = set()
    out: list[dict[str, Any]] = []
    for j in jobs:
        for report in _reports_covering(j.get("finished_at"), item_id):
            if report["id"] in seen_report_ids:
                continue
            seen_report_ids.add(report["id"])
            out.append(report)
    return out


def investigations_for_report(report_id: int) -> list[dict[str, Any]] | None:
    """The investigations a report's own "חקירות עומק" section actually shows -- ``None`` when
    ``report_id`` doesn't exist, ``[]`` for a report kind that never renders that section
    (``bd_territory``/``patent_survey``/``product_line``) or one that does but had no entries.

    Reuses ``eoa.report.daily.collect_deep_search`` + ``_filter_deep_search_to_items_included``
    directly, against this report's own ``period_start``/``period_end``/``items_included`` -- the
    exact same call the report builder itself made, so the result matches what the reader actually
    saw in the rendered report (including ``reconcile_deep_search_reruns``'s rerun-collapsing),
    rather than :func:`_reports_covering`'s after-the-fact reconstruction used by the other
    functions in this module."""
    report = _fetchone(
        "SELECT id, kind, period_start, period_end, items_included FROM reports WHERE id = %s",
        (report_id,),
    )
    if report is None:
        return None
    if report.get("kind") not in _DEEP_SEARCH_REPORT_KINDS:
        return []
    from eoa.report.daily import _filter_deep_search_to_items_included, collect_deep_search

    items = [{"id": iid} for iid in (report.get("items_included") or [])]
    deep_search = collect_deep_search(report.get("period_start"), report.get("period_end"))
    return _filter_deep_search_to_items_included(deep_search, items)


def reports_for_investigation(job_id: int) -> list[dict[str, Any]] | None:
    """The reports a single investigation's own job appears in (module-level helper mirroring
    ``investigation_provenance(job_id)["reports"]`` for callers that only need this one field) --
    ``None`` when ``job_id`` isn't a known deep-search job."""
    job = _job_row(job_id)
    if job is None:
        return None
    payload = job.get("payload") or {}
    return _reports_covering(job.get("finished_at"), payload.get("item_id"))
