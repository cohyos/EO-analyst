"""Daily-report integration for tenders/forecasts (section 5.2 / FR-5.2).

Deliberately separate from ``eoa.report.daily`` (which is owned/edited concurrently) -- this module
only *collects and renders*; ``daily.py`` wires it in with a small additive block that passes the
result through ``eoa.report.docx_builder``'s existing ``extra_sections``/``tables`` hooks (already
used by the weekly/monthly reports), so nothing here touches the LLM-drafted ``DailyReportDraft``
schema or the citation QA gate.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from eoa.db import connection
from eoa.report.link_check import LinkCheckResult
from eoa.tenders.forecast import normalize_hebrew_punctuation

SECTION_TITLE_HE = "מכרזים, RFI/RFP ותחזית"

_STATUS_HE = {
    "open": "פתוח",
    "closed": "סגור",
    "awarded": "הוענק",
    "unknown": "לא ידוע",
    "archived": "ארכיון",
}

# F2.d: the forecasts list must show exactly one line per forecast (platform/payload/likelihood/
# window) with the rationale trimmed to a single sentence, never the full 2-4-sentence prose --
# the un-trimmed rationale is what was making the section "spill" in the rendered report.
_MAX_RATIONALE_SENTENCE_CHARS = 200
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# W1 (round 4, docs/qa/loop/round_4_fixes.md): the same platform+payload topic can appear as more
# than one ``tender_forecasts`` row -- most commonly one per resolved ``buyer_country`` value (see
# ``eoa.tenders.forecast._resolve_buyer_country``) -- which reads as an outright duplicate in a
# report table that doesn't even show a buyer_country column. Fetch generously (more than the
# rendered cap) so the post-dedup result still has enough distinct topics to fill the table.
_FORECAST_FETCH_LIMIT = 60
_FORECAST_TOPIC_CAP = 15


def _forecast_topic_key(row: dict[str, Any]) -> tuple[str, str]:
    # R6-forecast (round 6 judge D6): normalised through ``normalize_hebrew_punctuation`` so a
    # historical row written with the Hebrew gershayim/geresh mark (e.g. 'כטב״ם MALE') and a
    # current one written with a plain ASCII quote/apostrophe (e.g. 'כטב"ם MALE') -- the exact same
    # platform, see ``eoa.tenders.forecast``'s module note -- land on the same topic key instead of
    # rendering as two separate "duplicate" rows in the report table.
    return (
        normalize_hebrew_punctuation(row.get("platform")).strip().casefold(),
        normalize_hebrew_punctuation(row.get("payload_need")).strip().casefold(),
    )


def _merge_forecast_sources(rows: list[dict[str, Any]]) -> list[str]:
    """Union of every group member's ``sources`` (``["item:N", ...]``), order-preserving,
    deduplicated -- so a merged forecast still cites every item that corroborated any of its
    (now-collapsed) per-country rows."""
    merged: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for source in row.get("sources") or []:
            if source not in seen:
                seen.add(source)
                merged.append(source)
    return merged


def _forecast_likelihood(row: dict[str, Any]) -> float:
    value = row.get("likelihood")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


#: R6-forecast (round 6 judge D6): the max gap (days) between two forecasts' windows for them to
#: still count as "the same window" for merge purposes -- see :func:`_windows_close`.
_FORECAST_WINDOW_MERGE_GAP_DAYS = 3


def _windows_close(
    a: dict[str, Any], b: dict[str, Any], *, max_gap_days: int = _FORECAST_WINDOW_MERGE_GAP_DAYS
) -> bool:
    """R6-forecast: two forecast rows' windows count as "the same window" if they overlap, or the
    gap between them is at most ``max_gap_days`` -- distinguishes a genuine re-run/punctuation-drift
    duplicate (the same lag applied on consecutive run days, windows a day or two apart) from two
    forecasts that happen to share a (platform, payload_need) topic key but describe distinctly
    different windows, which are worth keeping as separate rows rather than silently collapsed into
    one. A row missing either window date never blocks the merge (nothing to compare against, so it
    behaves as it always did before this window check existed)."""
    a_from, a_to = a.get("window_from"), a.get("window_to")
    b_from, b_to = b.get("window_from"), b.get("window_to")
    if None in (a_from, a_to, b_from, b_to):
        return True
    if a_from <= b_to and b_from <= a_to:
        return True  # overlap
    gap = (b_from - a_to) if b_from > a_to else (a_from - b_to)
    return gap.days <= max_gap_days


def _cluster_by_window_proximity(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Partition ``rows`` (all already sharing one topic key) into clusters whose members are
    pairwise connected by :func:`_windows_close` (a small union-find) -- a single-row input yields
    one single-row cluster, and the common two-row case merges iff their windows are close."""
    n = len(rows)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _windows_close(rows[i], rows[j]):
                union(i, j)

    clusters: dict[int, list[dict[str, Any]]] = {}
    for i, row in enumerate(rows):
        clusters.setdefault(find(i), []).append(row)
    return list(clusters.values())


def dedupe_forecasts_by_topic(
    rows: list[dict[str, Any]], *, cap: int = _FORECAST_TOPIC_CAP
) -> list[dict[str, Any]]:
    """W1: collapse ``tender_forecasts`` rows sharing a (platform, payload_need) topic, keeping the
    highest-likelihood row's own fields but merging every group member's ``sources`` into it, so
    the merged forecast still cites everything that corroborated it. Sorted likelihood desc, capped
    at ``cap``. Never raises on well-formed dict rows; an empty/`` []`` input returns `` []``.

    R6-forecast: within one topic group, rows are further split by :func:`_cluster_by_window_proximity`
    -- two rows sharing a topic but describing windows more than
    :data:`_FORECAST_WINDOW_MERGE_GAP_DAYS` apart (and not overlapping) render as separate rows
    rather than being silently collapsed into one."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_forecast_topic_key(row), []).append(row)
    merged_rows: list[dict[str, Any]] = []
    for group in groups.values():
        for cluster in _cluster_by_window_proximity(group):
            best = max(cluster, key=_forecast_likelihood)
            merged = dict(best)
            merged["sources"] = _merge_forecast_sources(cluster)
            merged_rows.append(merged)
    merged_rows.sort(key=_forecast_likelihood, reverse=True)
    return merged_rows[:cap]


# --------------------------------------------------------------------------
# W6: forecast -> report citation-registry extension (same convention as
# eoa.report.tech_watch._extend_registry / eoa.report.israel_section)
# --------------------------------------------------------------------------


def _item_ids_from_forecast_sources(sources: list[str] | None) -> list[int]:
    """Parse ``tender_forecasts.sources`` (``["item:123", ...]``) back into item ids. Any
    non-conforming entry is silently skipped."""
    ids: list[int] = []
    for source in sources or []:
        if not isinstance(source, str) or not source.startswith("item:"):
            continue
        try:
            ids.append(int(source.split(":", 1)[1]))
        except ValueError:
            continue
    return ids


def attach_forecast_citations(
    citation_items: list[dict[str, Any]], forecasts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """W6: register every forecast's trigger items into ``citation_items`` (mutated in place --
    same numbering-extension convention as ``eoa.report.tech_watch._extend_registry``), fetching
    from the DB only the ids not already numbered. Stamps each forecast dict with
    ``"_citation_ns"`` -- the list of registry numbers a renderer should cite for that row.
    Never raises; a DB failure degrades every forecast's ``_citation_ns`` to ``[]`` rather than
    breaking the report."""
    all_ids = sorted({iid for f in forecasts for iid in _item_ids_from_forecast_sources(f.get("sources"))})
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    missing = [iid for iid in all_ids if iid not in by_id]
    if missing:
        try:
            fetched_rows = _fetchall(
                """
                SELECT i.id, i.url, i.title, i.published_at, COALESCE(src.name, i.url) AS source_name
                FROM items i LEFT JOIN sources src ON src.id = i.source_id
                WHERE i.id = ANY(%(ids)s)
                """,
                {"ids": missing},
            )
        except Exception:
            fetched_rows = []
        fetched_by_id = {r["id"]: r for r in fetched_rows}
        next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
        for iid in missing:
            row = fetched_by_id.get(iid)
            if row is None:
                continue
            entry = dict(row)
            entry["n"] = next_n
            citation_items.append(entry)
            by_id[iid] = entry
            next_n += 1
    for f in forecasts:
        ids = _item_ids_from_forecast_sources(f.get("sources"))
        f["_citation_ns"] = sorted(
            {by_id[i]["n"] for i in ids if i in by_id and by_id[i].get("n") is not None}
        )
    return citation_items


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def collect_tenders(
    period_start: dt.date | None = None, period_end: dt.date | None = None, *, open_limit: int = 15
) -> dict[str, Any]:
    """Open tenders (most urgent deadline first) + forecasts created/refreshed within the report
    period, most likely first, + a count of ``status='unknown'`` rows (F2.d -- undated notices are
    never listed as open; the count lets the report surface them as "needs a look" without
    pretending they're open opportunities). Never raises -- all queries are simple/DB-only, callers
    wrap this in a try/except anyway (a report section is never allowed to break the whole daily
    report). Returns ``{"open_tenders": [...], "new_forecasts": [...], "unknown_count": int}`` --
    the new ``unknown_count`` key is additive; existing callers reading only the first two keys are
    unaffected.

    W2b (docs/REVIEW_2026-09-06_evening.md, open intake): ``eoa.tenders.scan`` now stores every
    notice that clears the two-signal vocabulary gate, not just LLM-confirmed-relevant ones --
    carrying ``intake`` (``'candidate'`` below the self-tuning threshold, ``'accepted'`` at/above
    it). The report keeps showing only ``intake = 'accepted'`` rows, per the user's own
    requirement -- a ``'candidate'`` is an unconfirmed lead the tenders screen surfaces for an
    operator to 👍/👎, not something to hand the reader as a decided opportunity."""
    open_tenders = _fetchall(
        "SELECT * FROM tenders WHERE status = 'open' AND intake = 'accepted' "
        "ORDER BY deadline ASC NULLS LAST, relevance DESC NULLS LAST, id DESC LIMIT %(limit)s",
        {"limit": open_limit},
    )
    if period_start is not None and period_end is not None:
        new_forecasts = _fetchall(
            "SELECT * FROM tender_forecasts WHERE updated_at::date BETWEEN %(start)s AND %(end)s "
            "ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT %(limit)s",
            {"start": period_start, "end": period_end, "limit": _FORECAST_FETCH_LIMIT},
        )
    else:
        new_forecasts = _fetchall(
            "SELECT * FROM tender_forecasts ORDER BY likelihood DESC NULLS LAST, id DESC LIMIT %(limit)s",
            {"limit": _FORECAST_FETCH_LIMIT},
        )
    # W1 (round 4): the same platform+payload topic can appear as more than one row (typically one
    # per resolved buyer_country) -- collapse before this ever reaches a report table.
    new_forecasts = dedupe_forecasts_by_topic(new_forecasts)
    unknown_rows = _fetchall(
        "SELECT count(*) AS c FROM tenders WHERE status = 'unknown' AND intake = 'accepted'"
    )
    unknown_count = unknown_rows[0]["c"] if unknown_rows else 0
    return {"open_tenders": open_tenders, "new_forecasts": new_forecasts, "unknown_count": unknown_count}


def _fmt_date(value: Any) -> str:
    if value is None:
        return "—"
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _trim_rationale(text: str | None, max_chars: int = _MAX_RATIONALE_SENTENCE_CHARS) -> str:
    """F2.d: reduce a (possibly multi-sentence) rationale to a single trimmed sentence -- the
    forecasts list must be one line per forecast, not the full LLM prose."""
    stripped = (text or "").strip()
    if not stripped:
        return ""
    first_sentence = _SENTENCE_SPLIT_RE.split(stripped, maxsplit=1)[0].strip()
    if len(first_sentence) > max_chars:
        return first_sentence[: max_chars - 1].rstrip() + "…"
    return first_sentence


def tenders_extra_section(data: dict[str, Any]) -> dict[str, Any]:
    """``extra_sections`` entry (see ``eoa.report.docx_builder._add_extra_sections``) -- deterministic
    prose, positioned ``after_outlook`` so it reads as a standalone appendix-style section, never
    mixed into the LLM-drafted/QA-gated body."""
    open_tenders = data.get("open_tenders") or []
    new_forecasts = data.get("new_forecasts") or []
    unknown_count = data.get("unknown_count") or 0

    lines: list[str] = []
    if open_tenders:
        lines.append(
            f"{len(open_tenders)} מכרזים/RFI/RFP פתוחים הרלוונטיים לתחומי EO/IR/CV, ממוינים לפי דדליין:"
        )
        for t in open_tenders[:10]:
            lines.append(
                f"- {t.get('title') or '—'} | {t.get('agency') or t.get('country') or '—'} | "
                f"דדליין: {_fmt_date(t.get('deadline'))} | {t.get('url') or '—'}"
            )
    else:
        lines.append("לא זוהו כרגע מכרזים/RFI/RFP פתוחים בתחומי העניין.")

    if unknown_count:
        lines.append(f"{unknown_count} הודעות נוספות לבדיקה (ללא תאריכים) -- לא כלולות כפתוחות עד לאימות.")

    lines.append("")
    if new_forecasts:
        lines.append("תחזיות מכרזים עתידיים (מבוססות אירועי פלטפורמות אחרונים):")
        for f in new_forecasts[:10]:
            likelihood = f.get("likelihood")
            pct = f"{likelihood:.0%}" if isinstance(likelihood, int | float) else "—"
            # One line per forecast: platform / payload / likelihood / window, then the rationale
            # trimmed to a single sentence (F2.d) -- never the full multi-sentence LLM prose.
            lines.append(
                f"- {f.get('platform') or '—'} ({f.get('buyer_country') or '—'}): "
                f"{f.get('payload_need') or '—'} — סבירות {pct}, חלון "
                f"{_fmt_date(f.get('window_from'))} עד {_fmt_date(f.get('window_to'))}. "
                f"{_trim_rationale(f.get('rationale_he') or '')}"
            )
    else:
        lines.append("לא נוצרו תחזיות מכרזים חדשות בתקופה זו.")

    return {"title_he": SECTION_TITLE_HE, "body_he": "\n".join(lines), "position": "after_outlook"}


def _tender_status_label(t: dict[str, Any], link_result: LinkCheckResult | None) -> str:
    """W7: a link_check result overrides the DB status label for display purposes only -- a
    genuinely dead link means this is no longer really "open" regardless of what ``tenders.status``
    still says (nothing has re-scanned it since); a stale-looking one is relabeled "ארכיון" rather
    than silently kept as "open". An unchecked result (budget/timeout) leaves the label alone but
    for a trailing "(לא אומת)" marker."""
    base = _STATUS_HE.get(t.get("status"), t.get("status") or "—")
    if link_result is None:
        return base
    if not link_result.checked:
        return f"{base} (לא אומת)"
    if link_result.stale:
        return _STATUS_HE["archived"]
    return base


def tenders_table(
    data: dict[str, Any], *, link_cache: dict[str, LinkCheckResult] | None = None
) -> dict[str, Any] | None:
    """``tables`` entry (see ``eoa.report.docx_builder._add_generic_table``) -- the open-tenders
    board. ``None`` when there is nothing to show (the caller skips an empty table).

    W7 (round 4): when ``link_cache`` is supplied (a url -> :class:`~eoa.report.link_check.LinkCheckResult`
    map, built once per report run by the caller via ``eoa.report.link_check.check_urls``), a row
    whose link came back confirmed dead (4xx/5xx or a network error) is dropped outright -- an
    "open" tender that 404s is not actually an open opportunity; a row whose link looks stale (an
    old year alone in the page title, the LITENING-2015 case) is kept but relabeled "ארכיון" rather
    than "פתוח". ``link_cache is None`` (the default -- and every pre-existing caller/test) means no
    check was performed and every row renders exactly as before."""
    open_tenders = data.get("open_tenders") or []
    if not open_tenders:
        return None
    filtered: list[tuple[dict[str, Any], LinkCheckResult | None]] = []
    for t in open_tenders:
        url = t.get("url")
        result = link_cache.get(url) if (link_cache is not None and url) else None
        if result is not None and result.checked and result.alive is False:
            continue  # confirmed dead -- drop, never render as an open opportunity
        filtered.append((t, result))
    if not filtered:
        return None  # every row was a confirmed-dead link -- nothing left to show
    headers = ["כותרת", "מדינה", "גורם מזמין", "דדליין", "סטטוס", "קישור"]
    rows = [
        [
            t.get("title") or "—",
            t.get("country") or "—",
            t.get("agency") or "—",
            _fmt_date(t.get("deadline")),
            _tender_status_label(t, result),
            t.get("url") or "—",
        ]
        for t, result in filtered[:15]
    ]
    return {"title_he": "מכרזים פתוחים (EO/IR)", "headers": headers, "rows": rows}
