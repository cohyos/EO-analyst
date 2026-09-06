"""Stage: report/deltas — "מה השתנה מאז המהדורה הקודמת" (Round 5 P2: D4, D5-adjacent, W3, B4).

Deterministic (no LLM) comparison of the report currently being built against the same-``kind``
(and, for a territory report, same-``territory``) *previous* report's persisted
``reports.report_state`` (migration ``0023``): new items since the last issue, items that rose in
triage level, and — weekly only — trends that appeared/strengthened/weakened/vanished. Renders as
one additive ``extra_sections`` entry (``position="after_summary"``, so it lands immediately after
the executive summary, per docs/REPORT_TEMPLATE_BENCHMARK.md §3.1 row 3 / §3.2 row 4) wired into
``eoa.report.daily.build_daily`` / ``eoa.report.weekly.build_weekly`` — same additive-hook
convention ``eoa.report.israel_section``/``eoa.report.tech_watch`` already use, extending the
caller's citation registry in place so every ``[n]`` printed here also resolves in the "נספח
מקורות" appendix.

Deliberately kind-agnostic (``kind``/``territory`` are plain strings, no daily/weekly-specific
import) so a future BD-territory report build (``kind='bd_territory'``, one ``territory`` per
report) can call :func:`compute_deltas`/:func:`build_report_state` unchanged — see B4 in
docs/PLAN_ROUND5_REPORTS.md's "גל ב'" wave; wiring that call site into
``eoa.report.bd_territory`` is explicitly out of this package's file scope for this round.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)

SECTION_TITLE_HE = "מה השתנה מאז הדוח הקודם"

_TOP_NEW_ITEMS = 3

#: Triage-level ordering used to detect a "rose in level" item (yellow -> orange -> red only; an
#: unrecognised/missing level never counts as a rise in either direction).
_LEVEL_RANK = {"yellow": 1, "orange": 2, "red": 3}

_LEVEL_LABEL_HE = {"red": "אדום", "orange": "כתום", "yellow": "צהוב"}

_STATUS_LABELS_HE = {
    "appeared": "מגמה חדשה",
    "strengthened": "התחזקה",
    "weakened": "נחלשה",
    "vanished": "נעלמה",
}

_NO_PREVIOUS_TEXT_HE = (
    "זהו הדוח הראשון מסוג זה שנבנה עבור {label} — אין דוח קודם להשוואה, ולכן לא ניתן להציג דלתא."
)

_KIND_LABELS_HE = {"daily": "יומי", "weekly": "שבועי", "bd_territory": "מיקוד טריטוריאלי"}


@dataclass
class ItemDelta:
    """Item-level half of a :class:`DeltaResult` (D4)."""

    new_count: int = 0
    #: Up to :data:`_TOP_NEW_ITEMS` of the new items themselves (already carrying a registry ``n``).
    new_top_items: list[dict[str, Any]] = field(default_factory=list)
    #: Each entry is the item dict plus ``from_level``/``to_level``.
    risen_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TrendDelta:
    """One trend's status change vs. the previous issue (W3, weekly only)."""

    title_he: str
    status: str  # appeared | strengthened | weakened | vanished
    previous_strength: int | None = None
    current_strength: int | None = None
    cites: list[int] = field(default_factory=list)


@dataclass
class DeltaResult:
    has_previous: bool
    previous_report_id: int | None
    item_delta: ItemDelta
    trend_deltas: list[TrendDelta]
    summary_he: str


# --------------------------------------------------------------------------
# previous-report lookup / this-report's own persisted state
# --------------------------------------------------------------------------


def previous_report_state(
    kind: str,
    *,
    before_period_end: dt.date,
    territory: str | None = None,
) -> tuple[int, dict[str, Any]] | None:
    """The most recent past report of ``kind`` (optionally scoped to ``territory``, for
    ``bd_territory``) whose ``period_end`` is strictly before ``before_period_end`` and which
    carries a non-``NULL`` ``report_state`` — the report this build's delta is computed against.
    ``before_period_end`` matters for a manual rebuild of an *old* date after newer issues already
    exist (F4-style rebuild): without it, "most recent row of this kind" would pick a report from
    the future relative to the one being rebuilt. Returns ``None`` when there is no such report
    (first issue of this kind/territory, or every prior issue predates this column)."""
    sql = """
        SELECT id, report_state
        FROM reports
        WHERE kind = %(kind)s
          AND report_state IS NOT NULL
          AND period_end < %(before)s
          AND (%(territory)s::text IS NULL OR territory = %(territory)s)
        ORDER BY period_end DESC NULLS LAST, created_at DESC
        LIMIT 1
    """
    # F4-style optional-section convention (see eoa.db.connection's own docstring and
    # eoa.report.bd_territory's identical `connection(timeout=5)` calls): this delta section is
    # decorative, never load-bearing, so an unreachable/slow DB must fail fast into the caller's
    # `except` instead of blocking the whole report build.
    with connection(timeout=5) as conn, conn.cursor() as cur:
        cur.execute(sql, {"kind": kind, "before": before_period_end, "territory": territory})
        row = cur.fetchone()
    if row is None or not row.get("report_state"):
        return None
    return row["id"], row["report_state"]


def build_report_state(
    items: list[dict[str, Any]],
    *,
    trends: list[dict[str, Any]] | None = None,
    indicator_ids: list[int] | None = None,
) -> dict[str, Any]:
    """The JSON payload to persist into this report's own ``reports.report_state`` — the raw
    material the *next* same-kind report's delta will be computed against.

    ``items`` is the report's citation-registry item list (each entry carries at least ``id``;
    ``level`` when known). ``trends`` (weekly only) is ``eoa.report.trends.detect_trends``'s own
    return shape (each entry carries ``title_he``/``strength``) — omitted for daily/BD, which have
    no trend concept. ``indicator_ids`` are this issue's still-tracked (``open`` or newly-``open``)
    ``indicator_watchlist`` row ids (see ``eoa.report.indicators``)."""
    item_ids = [it["id"] for it in items if it.get("id") is not None]
    item_levels = {
        str(it["id"]): it.get("level") for it in items if it.get("id") is not None and it.get("level")
    }
    trend_titles = [
        {"title_he": t.get("title_he"), "strength": t.get("strength")}
        for t in (trends or [])
        if t.get("title_he")
    ]
    return {
        "item_ids": item_ids,
        "item_levels": item_levels,
        "trend_titles": trend_titles,
        "indicator_ids": [int(i) for i in (indicator_ids or [])],
    }


# --------------------------------------------------------------------------
# item deltas (D4)
# --------------------------------------------------------------------------


def compute_item_deltas(current_items: list[dict[str, Any]], previous_state: dict[str, Any]) -> ItemDelta:
    """New items (not in the previous issue's ``item_ids``) and items whose ``level`` rose
    relative to the previous issue's ``item_levels`` snapshot. ``current_items`` is expected in
    the report's own natural (score-desc) order, so the first :data:`_TOP_NEW_ITEMS` new items
    encountered are already the most important ones."""
    previous_ids = set(previous_state.get("item_ids") or [])
    previous_levels: dict[str, Any] = previous_state.get("item_levels") or {}
    new_top: list[dict[str, Any]] = []
    new_count = 0
    risen: list[dict[str, Any]] = []
    for it in current_items:
        item_id = it.get("id")
        if item_id is None:
            continue
        if item_id not in previous_ids:
            new_count += 1
            if len(new_top) < _TOP_NEW_ITEMS:
                new_top.append(it)
            continue
        prev_level = previous_levels.get(str(item_id))
        cur_level = it.get("level")
        if prev_level and cur_level and _LEVEL_RANK.get(cur_level, 0) > _LEVEL_RANK.get(prev_level, 0):
            risen.append({**it, "from_level": prev_level, "to_level": cur_level})
    return ItemDelta(new_count=new_count, new_top_items=new_top, risen_items=risen)


# --------------------------------------------------------------------------
# trend deltas (W3, weekly only)
# --------------------------------------------------------------------------


def _normalize_trend_title(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def compute_trend_deltas(
    current_trends: list[dict[str, Any]],
    previous_trend_titles: list[dict[str, Any]],
    *,
    id_to_n: dict[int, int] | None = None,
) -> list[TrendDelta]:
    """Trends that appeared, strengthened, weakened, or vanished vs. the previous issue's
    ``trend_titles`` snapshot. ``current_trends`` is
    ``eoa.report.trends.detect_trends``'s own shape (``title_he``/``strength``/
    ``evidence_item_ids``); matching is by normalised (whitespace-collapsed, casefolded)
    ``title_he`` — trend titles are short, deterministic taxonomy-driven Hebrew phrases (see
    ``eoa.report.trends``), so an exact-after-normalisation match is the right granularity (no
    fuzzy-match risk of conflating two different trends). A trend present in both issues at the
    same strength is not reported (no interesting change). ``id_to_n`` (this issue's citation
    registry) lets an "appeared"/"strengthened"/"weakened" row cite its own evidence items — a
    "vanished" trend has none this issue, since its evidence is (by definition) not in it."""
    id_to_n = id_to_n or {}
    prev_by_title = {
        _normalize_trend_title(t.get("title_he")): (t.get("title_he"), t.get("strength"))
        for t in previous_trend_titles
        if t.get("title_he")
    }
    cur_by_title = {_normalize_trend_title(t.get("title_he")): t for t in current_trends if t.get("title_he")}
    deltas: list[TrendDelta] = []
    for norm, cur in cur_by_title.items():
        title_he = cur.get("title_he")
        cur_strength = cur.get("strength")
        cites = sorted({id_to_n[i] for i in cur.get("evidence_item_ids", []) if i in id_to_n})
        if norm not in prev_by_title:
            deltas.append(
                TrendDelta(title_he=title_he, status="appeared", current_strength=cur_strength, cites=cites)
            )
            continue
        _, prev_strength = prev_by_title[norm]
        if not isinstance(cur_strength, int | float) or not isinstance(prev_strength, int | float):
            continue
        if cur_strength > prev_strength:
            deltas.append(
                TrendDelta(
                    title_he=title_he,
                    status="strengthened",
                    previous_strength=prev_strength,
                    current_strength=cur_strength,
                    cites=cites,
                )
            )
        elif cur_strength < prev_strength:
            deltas.append(
                TrendDelta(
                    title_he=title_he,
                    status="weakened",
                    previous_strength=prev_strength,
                    current_strength=cur_strength,
                    cites=cites,
                )
            )
    for norm, (title_he, prev_strength) in prev_by_title.items():
        if norm not in cur_by_title:
            deltas.append(TrendDelta(title_he=title_he, status="vanished", previous_strength=prev_strength))
    return deltas


# --------------------------------------------------------------------------
# orchestration + rendering
# --------------------------------------------------------------------------


def _build_summary_he(kind: str, item_delta: ItemDelta, trend_deltas: list[TrendDelta]) -> str:
    parts = [f"{item_delta.new_count} פריטים חדשים" if item_delta.new_count else "אין פריטים חדשים"]
    if item_delta.risen_items:
        parts.append(f"{len(item_delta.risen_items)} פריטים עלו ברמת חשיבות")
    if trend_deltas:
        appeared = sum(1 for t in trend_deltas if t.status == "appeared")
        strengthened = sum(1 for t in trend_deltas if t.status == "strengthened")
        weakened = sum(1 for t in trend_deltas if t.status == "weakened")
        vanished = sum(1 for t in trend_deltas if t.status == "vanished")
        trend_bits = []
        if appeared:
            trend_bits.append(f"{appeared} מגמות חדשות")
        if strengthened:
            trend_bits.append(f"{strengthened} התחזקו")
        if weakened:
            trend_bits.append(f"{weakened} נחלשו")
        if vanished:
            trend_bits.append(f"{vanished} נעלמו")
        if trend_bits:
            parts.append("; ".join(trend_bits))
    kind_label = _KIND_LABELS_HE.get(kind, kind)
    return f"לעומת הדוח ה{kind_label} הקודם: " + "; ".join(parts) + "."


def compute_deltas(
    kind: str,
    current_items: list[dict[str, Any]],
    *,
    before_period_end: dt.date,
    territory: str | None = None,
    current_trends: list[dict[str, Any]] | None = None,
    id_to_n: dict[int, int] | None = None,
) -> DeltaResult:
    """Full delta computation for one report build: looks up the previous same-``kind``/
    ``territory`` report's persisted state (:func:`previous_report_state`), then computes item and
    (when ``current_trends`` is given — weekly/BD) trend deltas against it. ``current_items``
    should be the report's numbered citation-registry item list (each carrying ``id``/``level``/
    ``n``). Returns a ``has_previous=False`` result (D4/W3's "no previous report" honest line) when
    this is the first issue of its kind."""
    found = previous_report_state(kind, before_period_end=before_period_end, territory=territory)
    if found is None:
        kind_label = _KIND_LABELS_HE.get(kind, kind)
        return DeltaResult(
            has_previous=False,
            previous_report_id=None,
            item_delta=ItemDelta(),
            trend_deltas=[],
            summary_he=_NO_PREVIOUS_TEXT_HE.format(label=kind_label),
        )
    previous_report_id, state = found
    item_delta = compute_item_deltas(current_items, state)
    trend_deltas = (
        compute_trend_deltas(current_trends, state.get("trend_titles") or [], id_to_n=id_to_n)
        if current_trends is not None
        else []
    )
    summary_he = _build_summary_he(kind, item_delta, trend_deltas)
    return DeltaResult(
        has_previous=True,
        previous_report_id=previous_report_id,
        item_delta=item_delta,
        trend_deltas=trend_deltas,
        summary_he=summary_he,
    )


_RAW_SUBDOMAIN_IN_TITLE_RE = re.compile(r'בתת-התחום "([a-z][a-z0-9_]*)"')


def _label_raw_subdomain_keys(title: str) -> str:
    """Trend titles stored in report_state by runs before 2026-09-07 carry raw subdomain keys
    ('בתת-התחום "atr"'); map them to the taxonomy label at render time."""
    from eoa.report.trends import _subdomain_label

    return _RAW_SUBDOMAIN_IN_TITLE_RE.sub(
        lambda m: f'בתת-התחום "{_subdomain_label(m.group(1))}"', title or ""
    )


def render_delta_section_he(result: DeltaResult) -> str:
    """Deterministic Hebrew body for the "מה השתנה מאז הדוח הקודם" section — plain paragraphs plus
    "- " bullet runs (``eoa.report.docx_builder._md_blocks`` renders each as its own block in all
    three output formats). ``[n]`` markers here are real registry numbers taken straight from the
    already-numbered item dicts in ``result`` — never invented, per docs/CONVENTIONS.md rule 4."""
    if not result.has_previous:
        return result.summary_he

    lines: list[str] = [result.summary_he, ""]

    item_delta = result.item_delta
    if item_delta.new_count:
        lines.append(f"פריטים חדשים מאז הדוח הקודם: {item_delta.new_count}.")
        for it in item_delta.new_top_items:
            n = it.get("n")
            marker = f" [{n}]" if n is not None else ""
            lines.append(f"- {it.get('title') or '—'}{marker}")
    else:
        lines.append("לא זוהו פריטים חדשים מאז הדוח הקודם.")

    if item_delta.risen_items:
        lines.append("")
        lines.append("פריטים שעלו ברמת חשיבות מאז הדוח הקודם:")
        for it in item_delta.risen_items:
            n = it.get("n")
            marker = f" [{n}]" if n is not None else ""
            from_label = _LEVEL_LABEL_HE.get(it["from_level"], it["from_level"])
            to_label = _LEVEL_LABEL_HE.get(it["to_level"], it["to_level"])
            lines.append(f"- {it.get('title') or '—'}: {from_label} ← {to_label}{marker}")

    if result.trend_deltas:
        lines.append("")
        lines.append("שינויים במגמות לעומת הדוח הקודם:")
        for td in result.trend_deltas:
            marker = "".join(f"[{n}]" for n in td.cites)
            marker = f" {marker}" if marker else ""
            status_he = _STATUS_LABELS_HE.get(td.status, td.status)
            if td.status in ("strengthened", "weakened"):
                lines.append(
                    f"- {_label_raw_subdomain_keys(td.title_he)}: {status_he} ({td.previous_strength} ← {td.current_strength}){marker}"
                )
            elif td.status == "appeared":
                lines.append(
                    f"- {_label_raw_subdomain_keys(td.title_he)}: {status_he} (עוצמה {td.current_strength}){marker}"
                )
            else:  # vanished
                lines.append(
                    f"- {_label_raw_subdomain_keys(td.title_he)}: {status_he} (הייתה בעוצמה {td.previous_strength}){marker}"
                )

    return "\n".join(lines)


def delta_extra_section(result: DeltaResult) -> dict[str, Any]:
    """The ready ``extra_sections`` entry (``position="after_summary"``) — always returned (never
    ``None``): even the no-previous-report case renders one honest line under the same heading,
    per the D4/W3 spec, rather than silently omitting the section on a report's first issue."""
    return {
        "title_he": SECTION_TITLE_HE,
        "body_he": render_delta_section_he(result),
        "position": "after_summary",
    }


__all__ = [
    "SECTION_TITLE_HE",
    "DeltaResult",
    "ItemDelta",
    "TrendDelta",
    "build_report_state",
    "compute_deltas",
    "compute_item_deltas",
    "compute_trend_deltas",
    "delta_extra_section",
    "previous_report_state",
    "render_delta_section_he",
]
