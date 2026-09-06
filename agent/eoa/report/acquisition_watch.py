"""A16 (מעקב רכישות ושותפויות, user requirement 2026-09-06): report-layer rendering for the
"מעקב רכישות ושותפויות" section -- mirrors ``eoa.patents.report_section``'s shape: a deliberately
separate, deterministic (no LLM) collect+render helper that ``eoa.report.weekly`` wires in with one
additive call, through the same ``extra_sections`` hook ``eoa.report.tech_watch`` /
``eoa.report.israel_section`` / ``eoa.patents.report_section`` already use -- never touching the
report builder's own LLM-drafted/QA-gated sections.

Unlike those modules, :func:`acquisition_watch_section_md` takes an already-open ``conn`` (rather
than opening its own via ``eoa.db.connection``) so ``eoa.report.bd_territory`` -- which already
holds its own connection open per report -- can call it unchanged; see this module's own docstring
and ``docs/MODULES.md``'s "A16" section for the exact call site to add there.

Contents:

  - **Events table**: every ``m_and_a``/``acquisition``/``investment``/``partnership`` event in the
    report window whose ``parties`` names an ``acquisition_watch`` company or one of its peers
    (``eoa.pipeline.acquisition.all_watch_and_peer_names``) -- date, matched company, event kind,
    counterparty, amount, and a ``[n]`` citation into ``registry`` (extended in place, same
    convention as ``eoa.report.weekly``'s own registry-extension helpers).
  - **Zero-activity lines**: one deterministic line per ``acquisition_watch`` company (the primary
    list, not its peers) that had no matching event this window -- "לא זוהתה פעילות".
  - **Patent-proxy value**: one line per ``acquisition_watch`` company that has at least one
    ``patents`` row with a ``value_score`` (``eoa.patents.valuation``) -- omitted entirely (no
    placeholder) for a company with no patent data.

Returns a single Markdown-table string (GFM syntax) fit to use as an ``extra_sections`` entry's
``body_he`` -- renders as a real table in this report's ``.md``/``.html`` output, and as readable
pipe-separated text in ``.docx`` (the ``extra_sections`` hook only ever renders plain paragraphs
there -- the same limitation every other deterministic section in this codebase already has).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from eoa.config import settings
from eoa.pipeline.acquisition import (
    acquisition_watch_names,
    all_watch_and_peer_names,
    canonicalize_watch_or_peer_name,
    watch_or_peer_hit,
)
from eoa.report.docx_builder import fmt_amount, fmt_date
from eoa.report.geography import normalize_country

log = structlog.get_logger(__name__)

SECTION_TITLE_HE = "מעקב רכישות ושותפויות"

#: Local copy of ``eoa.pipeline.acquisition``'s own (private) M&A-signal event-kind set, per this
#: codebase's "no cross-module private-name imports" convention (see e.g.
#: ``eoa.report.weekly``/``eoa.patents.report_section``'s own small local copies). Must stay in
#: sync with ``eoa.pipeline.acquisition._MA_SIGNAL_EVENT_KINDS``.
_MA_SIGNAL_EVENT_KINDS: frozenset[str] = frozenset({"m_and_a", "acquisition", "investment", "partnership"})

_EVENT_KIND_LABELS_HE: dict[str, str] = {
    "m_and_a": "מיזוג/רכישה",
    "acquisition": "רכישה",
    "investment": "השקעה",
    "partnership": "שותפות",
    # W18 (round 4b, docs/REVIEW_2026-09-06_evening.md): defensive -- a row reclassified by
    # :func:`_reclassified_kind` never actually reaches this section (it is filtered out of
    # :func:`_fetch_watch_events` entirely, since a contract award isn't an M&A/investment
    # signal), but the label is kept in sync with ``eoa.report.docx_builder``'s own map in case
    # this function is ever reused somewhere that doesn't filter first.
    "contract_award": "זכייה בחוזה",
}

_NO_EVENTS_LINE_HE = "לא זוהו אירועי רכישה/השקעה/שותפות בשבוע זה בחברות המעקב ומתחרותיהן."
_TABLE_HEADER_HE = "| תאריך | חברה | סוג אירוע | צד שכנגד | סכום | מקור |"
_TABLE_SEP = "| --- | --- | --- | --- | --- | --- |"


def _extend_registry_for_item(registry: list[dict[str, Any]], ev: dict[str, Any]) -> int | None:
    """Append ``ev``'s source item to ``registry`` (mutated in place) if not already numbered,
    returning its ``n`` -- same convention as ``eoa.report.weekly``'s own
    ``_extend_registry_with_events`` (a small local copy per this codebase's "no cross-module
    private-name imports" convention)."""
    item_id = ev.get("item_id")
    if item_id is None:
        return None
    entry = next((it for it in registry if it.get("id") == item_id), None)
    if entry is None:
        next_n = (max((it.get("n") or 0) for it in registry) + 1) if registry else 1
        entry = {
            "id": item_id,
            "n": next_n,
            "title": ev.get("item_title"),
            "source_name": ev.get("source_name"),
            "url": ev.get("item_url"),
            "published_at": ev.get("published_at"),
        }
        registry.append(entry)
    return entry["n"]


def _reclassified_kind(ev: dict[str, Any]) -> str:
    """W18 (round 4b, docs/REVIEW_2026-09-06_evening.md): a contract/order carrying both an
    amount and a customer is a ``contract_award`` signal, never an ``investment`` one -- observed
    live: "Elbit Systems wins US orders worth $370m" (a $370M order with a named customer) stored
    as ``events.kind = 'investment'`` and rendered here as "השקעה". Deterministic, code-level rule
    applied regardless of what ``events.kind`` says (the events-extraction pipeline's own kind
    classification is owned elsewhere and not touched here); every other kind passes through
    unchanged."""
    kind = ev.get("kind")
    if kind == "investment" and ev.get("amount_usd") is not None and ev.get("customer"):
        return "contract_award"
    return kind


def _fetch_watch_events(
    conn: Any, watch_names: list[str], since: dt.date, until: dt.date
) -> list[dict[str, Any]]:
    """Never raises: a query problem must not break the section (mirrors
    :func:`_patent_proxy_lines`'s own defensiveness) -- the caller (``eoa.report.weekly``) also
    wraps its whole call in ``try``/``except``, but ``eoa.report.bd_territory`` calling this
    directly should get the same guarantee without needing its own wrapper.

    W18: every row is passed through :func:`_reclassified_kind` first; a row reclassified away
    from the M&A/investment/partnership set (e.g. a contract award mislabeled ``investment``) is
    dropped here entirely -- it isn't an acquisition/investment/partnership signal at all, so it
    has no place in this section (a real procurement contract belongs in the BD report's own
    procurement/platform-events table, not here)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.item_id, e.kind, e.date, e.amount_usd, e.currency, e.parties,
                       e.customer, i.url AS item_url, i.title AS item_title, i.published_at,
                       i.geography, COALESCE(src.name, i.url) AS source_name
                FROM events e
                JOIN items i ON i.id = e.item_id
                LEFT JOIN sources src ON src.id = i.source_id
                WHERE e.kind = ANY(%(kinds)s)
                  AND e.parties && %(names)s
                  AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
                      BETWEEN %(start)s AND %(end)s
                ORDER BY e.date DESC NULLS LAST, e.id DESC
                """,
                {
                    "kinds": sorted(_MA_SIGNAL_EVENT_KINDS),
                    "names": watch_names,
                    "start": since,
                    "end": until,
                },
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("acquisition_watch_events_fetch_failed", error=str(exc)[:160])
        return []
    kept: list[dict[str, Any]] = []
    for ev in rows:
        kind = _reclassified_kind(ev)
        if kind not in _MA_SIGNAL_EVENT_KINDS:
            continue
        ev = dict(ev)
        ev["kind"] = kind
        kept.append(ev)
    return kept


def _event_party_names(ev: dict[str, Any]) -> list[str]:
    names = list(ev.get("parties") or [])
    if ev.get("customer"):
        names.append(ev["customer"])
    return names


def _entity_countries(conn: Any, names: list[str]) -> dict[str, str]:
    """``{entity name -> normalized country code}`` for every name in ``names`` that has an
    ``entities`` row with a non-null ``country`` -- used by :func:`_split_events_by_territory` to
    tell whether an event's customer/parties are actually from the territory. Never raises; an
    empty ``names`` list skips the query entirely (no cursor call), which keeps this a no-op for
    every caller that doesn't pass ``territory=`` to :func:`acquisition_watch_section_md`."""
    if not names:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, country FROM entities WHERE name = ANY(%(names)s) AND country IS NOT NULL",
                {"names": names},
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("acquisition_watch_entity_countries_failed", error=str(exc)[:160])
        return {}
    return {r["name"]: normalize_country(r.get("country")) for r in rows if r.get("name")}


def _split_events_by_territory(
    conn: Any, events_rows: list[dict[str, Any]], code: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """W18: this section used to show every M&A-signal event for every watch/peer company
    regardless of the report's own territory (observed live: an Elbit-India order appeared,
    identically, in both the Germany and the Greece BD reports). An event belongs to the
    territory's report when the source item's own ``geography`` normalizes to it, or one of the
    event's customer/parties is headquartered there (:func:`_entity_countries`). An event that
    instead belongs to a watch/peer company's *own* home territory (but not this one) is reported
    separately, as global context, only for that company's own territory report -- everything
    else (e.g. the Elbit/India order under Germany) is dropped from this territory's report
    entirely, never shown as a local signal it isn't."""
    all_names: set[str] = set()
    for ev in events_rows:
        all_names.update(_event_party_names(ev))
        # A raw party string is often a `companies:` alias (e.g. "Elbit Systems") rather than the
        # canonical name the `entities` table itself uses ("Elbit") -- without also looking up the
        # canonical name directly, a watch/peer company with no `entities` row under its exact
        # alias spelling would never resolve via `countries.get(company)` below.
        canonical = watch_or_peer_hit(ev.get("parties") or [])
        if canonical:
            all_names.add(canonical)
    countries = _entity_countries(conn, sorted(all_names))
    territory_events: list[dict[str, Any]] = []
    global_events: list[dict[str, Any]] = []
    for ev in events_rows:
        if normalize_country(ev.get("geography")) == code:
            territory_events.append(ev)
            continue
        party_countries = {countries[n] for n in _event_party_names(ev) if n in countries}
        if code in party_countries:
            territory_events.append(ev)
            continue
        company = watch_or_peer_hit(ev.get("parties") or [])
        if company and countries.get(company) == code:
            global_events.append(ev)
    return territory_events, global_events


def _events_table_rows(
    registry: list[dict[str, Any]], events_rows: list[dict[str, Any]]
) -> tuple[list[str], set[str]]:
    active_companies: set[str] = set()
    lines: list[str] = []
    seen_rows: set[tuple[str, str, str, int]] = set()
    # Round 5 follow-up (2026-09-07, live bd_il): the same Elbit partnership from item [11]
    # rendered twice (once dated, once undated) -- one row per (company, kind, counterparty,
    # source); dated events first so the surviving row is the informative one.
    events_rows = sorted(events_rows, key=lambda e: 0 if (e.get("date") or e.get("published_at")) else 1)
    for ev in events_rows:
        parties = ev.get("parties") or []
        company = watch_or_peer_hit(parties)
        if company is None:
            continue
        active_companies.add(company)
        n = _extend_registry_for_item(registry, ev)
        if n is None:
            continue
        counterparty = ", ".join(p for p in parties if canonicalize_watch_or_peer_name(p) != company) or "—"
        kind = ev.get("kind") or "אחר"
        kind_label = _EVENT_KIND_LABELS_HE.get(kind, kind)
        row_key = (company, kind_label, counterparty, n)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        lines.append(
            "| "
            + " | ".join(
                [
                    fmt_date(ev.get("date") or ev.get("published_at")),
                    company,
                    kind_label,
                    counterparty,
                    fmt_amount(ev),
                    f"[{n}]",
                ]
            )
            + " |"
        )
    return lines, active_companies


def _patent_proxy_lines(conn: Any, watch_names: list[str]) -> list[str]:
    """One line per :func:`acquisition_watch_names` company with at least one ``patents`` row
    carrying a ``value_score`` -- omitted entirely (no placeholder line) for a company with no
    patent data, per the A16 spec. Never raises: a patents-table problem must not break the
    section (the ``patents`` table/columns are owned by A14, developed concurrently)."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT assignees, value_score FROM patents "
                "WHERE assignees && %(names)s AND value_score IS NOT NULL",
                {"names": watch_names},
            )
            rows = cur.fetchall()
    except Exception as exc:
        log.warning("acquisition_watch_patent_proxy_failed", error=str(exc)[:160])
        return []
    by_company: dict[str, list[int]] = {}
    for row in rows:
        for assignee in row.get("assignees") or []:
            company = canonicalize_watch_or_peer_name(assignee)
            if company is None:
                continue
            by_company.setdefault(company, []).append(row["value_score"])
    lines: list[str] = []
    for name in acquisition_watch_names():
        scores = by_company.get(name)
        if not scores:
            continue
        avg = round(sum(scores) / len(scores))
        lines.append(f"- {name}: ציון-ערך פרוקסי ממוצע {avg} (מבוסס על {len(scores)} פטנטים)")
    return lines


def acquisition_watch_section_md(
    conn: Any,
    since: dt.date,
    until: dt.date,
    registry: list[dict[str, Any]],
    *,
    territory: str | None = None,
) -> str:
    """The full "מעקב רכישות ושותפויות" section body for the window ``[since, until]`` (inclusive)
    -- a Markdown string (see module docstring). ``registry`` (the report's citation-item list) is
    extended in place so every ``[n]`` printed here also gets a real numbered entry in the report's
    source appendix. Returns ``""`` when the feature is disabled
    (``config.acquisition_watch.enabled``) or no ``acquisition_watch`` companies are configured --
    the caller should skip adding the section entirely in that case, same convention as every other
    ``[]``-returning deterministic section in this codebase.

    ``territory`` (W18, round 4b): when given (``eoa.report.bd_territory``'s own territory-report
    call site), every event is filtered to that territory first (:func:`_split_events_by_territory`)
    -- a company's activity elsewhere no longer leaks into an unrelated territory's report. Left
    ``None`` (``eoa.report.weekly``'s existing, global call site) reproduces the prior, unfiltered
    behavior exactly."""
    if not settings().acquisition_watch.enabled:
        return ""
    watch_names = all_watch_and_peer_names()
    if not watch_names:
        return ""

    events_rows = _fetch_watch_events(conn, watch_names, since, until)
    if territory:
        code = normalize_country(territory)
        territory_events, global_events = _split_events_by_territory(conn, events_rows, code)
    else:
        code, territory_events, global_events = "", events_rows, []

    table_lines, active_companies = _events_table_rows(registry, territory_events)

    lines: list[str] = []
    if table_lines:
        lines.append(_TABLE_HEADER_HE)
        lines.append(_TABLE_SEP)
        lines.extend(table_lines)
    else:
        lines.append(_NO_EVENTS_LINE_HE)

    if global_events:
        global_lines, global_active = _events_table_rows(registry, global_events)
        if global_lines:
            lines.append("")
            lines.append(f"פעילות גלובלית של חברות מעקב שמקורן ב-{code} (הקשר בלבד, לא ממוקדת בטריטוריה זו):")
            lines.append(_TABLE_HEADER_HE)
            lines.append(_TABLE_SEP)
            lines.extend(global_lines)
            active_companies = active_companies | global_active

    inactive = [name for name in acquisition_watch_names() if name not in active_companies]
    if inactive:
        lines.append("")
        lines.extend(f"- {name}: לא זוהתה פעילות." for name in inactive)

    patent_lines = _patent_proxy_lines(conn, watch_names)
    if patent_lines:
        lines.append("")
        lines.append("ציון-ערך פרוקסי מפטנטים (מדד פרוקסי, לא הערכת שווי כספית):")
        lines.extend(patent_lines)

    return "\n".join(lines)
