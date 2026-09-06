"""A17 report hook: a cited Hebrew markdown table of the latest EO payload reference prices.

Deliberately a single pure function taking an already-open ``conn`` (never opens its own
connection/transaction) so a report builder that already holds a connection for the rest of its
data collection can call this inline without a second round-trip to the pool. Per the task's
ownership split, this module is NOT wired into ``eoa.report.bd_territory`` here (that file is
owned by another engineer) -- see ``docs/MODULES.md``'s "A17 payloads" section for the documented
one-line call site that engineer can add when ready.
"""

from __future__ import annotations

from typing import Any

from eoa.pipeline.entity_normalize import resolve_canonical
from eoa.report.geography import normalize_country

TITLE_HE = 'מחירי ייחוס למטע"דים אלקטרו-אופטיים'
_NO_DATA_HE = 'לא נאספו עדיין מחירי ייחוס מתועדים למטע"דים'


def _latest_price_refs(conn: Any, *, limit: int) -> list[dict[str, Any]]:
    query = """
        SELECT DISTINCT ON (r.payload_id)
            r.id, r.payload_id, r.price_usd, r.currency, r.original_amount, r.quantity,
            r.unit_price_usd, r.price_kind, r.date, r.buyer, r.programme, r.source_url,
            p.canonical_name, p.vendor_entity_name, p.category
        FROM payload_price_refs r
        JOIN payloads p ON p.id = r.payload_id
        ORDER BY r.payload_id, r.date DESC, r.id DESC
    """
    with conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
    rows.sort(key=lambda r: (r.get("date") is None, r.get("date")), reverse=True)
    return rows[:limit]


def _matches_territory(row: dict[str, Any], code: str) -> bool:
    vendor = row.get("vendor_entity_name")
    if not vendor:
        return False
    canonical = resolve_canonical(vendor)
    country = (canonical or {}).get("country") or ""
    return bool(country) and normalize_country(country) == code


def _fmt_price(row: dict[str, Any]) -> str:
    if row.get("unit_price_usd") is not None:
        return f"${row['unit_price_usd']:,.0f} (יחידה)"
    if row.get("price_usd") is not None:
        kind_he = {"unit": "יחידה", "contract": "חוזה", "estimate": "הערכה"}.get(
            row.get("price_kind") or "", ""
        )
        return f"${row['price_usd']:,.0f} ({kind_he})" if kind_he else f"${row['price_usd']:,.0f}"
    if row.get("original_amount") is not None:
        currency = row.get("currency") or "?"
        return f"{row['original_amount']:,.0f} {currency}"
    return "—"


def payload_price_table_md(territory: str | None, conn: Any, *, limit: int = 20) -> str:
    """Hebrew markdown table (with a numbered source footnote list) of the latest reference price
    on record for each payload, optionally restricted to payloads whose vendor is headquartered in
    ``territory`` (normalized via ``eoa.report.geography.normalize_country``, same convention
    ``eoa.patents.report_section.collect_patents_bd`` uses). Never raises -- any DB error yields
    the honest "no data" table body rather than breaking the caller's report."""
    try:
        rows = _latest_price_refs(conn, limit=limit * 3 if territory else limit)
    except Exception:
        rows = []

    if territory:
        code = normalize_country(territory)
        rows = [r for r in rows if _matches_territory(r, code)]
    rows = rows[:limit]

    if not rows:
        heading = f"{TITLE_HE} -- {territory}" if territory else TITLE_HE
        return f"## {heading}\n\n{_NO_DATA_HE}.\n"

    lines: list[str] = []
    heading = f"{TITLE_HE} -- {territory}" if territory else TITLE_HE
    lines.append(f"## {heading}")
    lines.append("")
    lines.append('| # | מטע"ד | יצרן | קטגוריה | מחיר ייחוס | תאריך | מקור |')
    lines.append("|---|---|---|---|---|---|---|")
    footnotes: list[str] = []
    for i, row in enumerate(rows, start=1):
        date_str = (
            row["date"].isoformat() if hasattr(row.get("date"), "isoformat") else (row.get("date") or "—")
        )
        lines.append(
            f"| {i} | {row.get('canonical_name') or '—'} | {row.get('vendor_entity_name') or '—'} | "
            f"{row.get('category') or '—'} | {_fmt_price(row)} | {date_str} | [{i}] |"
        )
        footnotes.append(f"[{i}] {row.get('source_url') or 'ללא קישור מקור'}")
    lines.append("")
    lines.extend(footnotes)
    lines.append("")
    return "\n".join(lines)
