"""`GET /api/payloads`, `/api/payloads/{id}`, `/api/payloads/{id}/diff`, `/api/payloads/export.csv`
-- A17 EO payload spec/price documentation (eoa.payloads).

Self-contained (queries the DB directly, mirrors `eoa.api.routes.patents`) -- read-only: this
router never inserts/updates/deletes anything, per the A17 spec ("read-only endpoints only").
Extraction/persistence lives entirely in `eoa.payloads.extract`.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from eoa.db import connection
from eoa.payloads.models import build_payload_tree, field_diff

log = structlog.get_logger(__name__)

router = APIRouter(tags=["payloads"])


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


# Shared by `list_payloads` and `payloads_tree` (W19b) -- every consumer of a `payloads` row
# needs the same version/price rollup columns `eoa.payloads.models.build_payload_tree` expects
# (`spec_version_count`/`price_ref_count`/`latest_spec_date`/`latest_price_date`), so this is the
# one place that SQL is written.
_PAYLOAD_ROWS_WITH_COUNTS_SQL = """
    SELECT p.*,
        (SELECT count(*) FROM payload_spec_versions v WHERE v.payload_id = p.id) AS spec_version_count,
        (SELECT count(*) FROM payload_price_refs r WHERE r.payload_id = p.id) AS price_ref_count,
        (SELECT max(v.effective_date) FROM payload_spec_versions v WHERE v.payload_id = p.id) AS latest_spec_date,
        (SELECT max(r.date) FROM payload_price_refs r WHERE r.payload_id = p.id) AS latest_price_date
    FROM payloads p
    WHERE {where}
    ORDER BY p.canonical_name
"""


def _payload_rows_with_counts(where: list[str], params: dict[str, Any]) -> list[dict[str, Any]]:
    return _fetchall(_PAYLOAD_ROWS_WITH_COUNTS_SQL.format(where=" AND ".join(where)), params)


@router.get("/payloads")
def list_payloads(
    category: str | None = Query(None),
    vendor: str | None = Query(None),
    family: str | None = Query(None, description="W19b: exact/partial match against payloads.family"),
    q: str | None = Query(None, description="free-text match against canonical_name/family/notes"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    where = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if category:
        where.append("p.category = %(category)s")
        params["category"] = category
    if vendor:
        where.append("p.vendor_entity_name ILIKE %(vendor)s")
        params["vendor"] = f"%{vendor}%"
    if family:
        where.append("p.family ILIKE %(family)s")
        params["family"] = f"%{family}%"
    if q:
        where.append("(p.canonical_name ILIKE %(q)s OR p.family ILIKE %(q)s OR p.notes ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    where_sql = " AND ".join(where)
    rows = _fetchall(
        _PAYLOAD_ROWS_WITH_COUNTS_SQL.format(where=where_sql) + " LIMIT %(limit)s",
        params,
    )
    # F39 (SOL-AUDIT-2026-09-24.md): the count used to ignore every filter above (`category`,
    # `vendor`, `family`, `q`) and always report the WHOLE table's size -- reusing the same
    # WHERE/params as the row query is the only way `total` and `payloads` agree.
    total_row = _fetchone(f"SELECT count(*) AS c FROM payloads p WHERE {where_sql}", params)
    return {"payloads": rows, "total": (total_row or {}).get("c", len(rows))}


@router.get("/payloads/facets")
def payload_facets(category: str | None = Query(None)) -> dict[str, Any]:
    """F33 (SOL-AUDIT-2026-09-24 review): the vendor-dropdown facet list previously came from a
    capped (`limit=500`) baseline `GET /api/payloads` fetch -- a vendor whose only rows sat outside
    that cap could never appear as a filter option, even after `vendor` itself was fixed to filter
    server-side. Uncapped, set-based DISTINCT queries -- no row cap to defeat. `category` narrows
    the vendor list the same way the UI's category-scoped facet fetch used to (picking a category
    should not surface a vendor with zero rows in it)."""
    vendor_where = ["vendor_entity_name IS NOT NULL"]
    params: dict[str, Any] = {}
    if category:
        vendor_where.append("category = %(category)s")
        params["category"] = category
    vendor_rows = _fetchall(
        f"SELECT DISTINCT vendor_entity_name FROM payloads WHERE {' AND '.join(vendor_where)} "
        "ORDER BY vendor_entity_name",
        params,
    )
    category_rows = _fetchall(
        "SELECT DISTINCT category FROM payloads WHERE category IS NOT NULL ORDER BY category"
    )
    return {
        "vendors": [r["vendor_entity_name"] for r in vendor_rows],
        "categories": [r["category"] for r in category_rows],
    }


@router.get("/payloads/tree")
def payloads_tree() -> dict[str, Any]:
    """W19b (docs/REVIEW_2026-09-06_evening.md; user requirement 2026-09-06 21:20): every payload
    grouped vendor -> family -> variant, with counts + latest spec/price dates at each level --
    `eoa.payloads.models.build_payload_tree`, fed every row in the table (unfiltered; the UI's own
    search/category filters narrow the tree client-side against this same shape, see
    `@/lib/payloadFamilies`)."""
    rows = _payload_rows_with_counts(["1=1"], {})
    return build_payload_tree(rows)


@router.get("/payloads/export.csv")
def export_payloads_csv() -> StreamingResponse:
    """CSV export of the latest spec (one flattened row per payload) + its latest price ref."""
    payloads = _fetchall("SELECT * FROM payloads ORDER BY canonical_name")
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "canonical_name",
            "vendor_entity_name",
            "family",
            "category",
            "spec_version_no",
            "spec_effective_date",
            "mass_kg",
            "channels",
            "detector_type",
            "detector_resolution",
            "detector_pitch_um",
            "fov_wide_deg",
            "fov_narrow_deg",
            "range_detect_km",
            "range_recognize_km",
            "range_identify_km",
            "stabilisation_urad",
            "interfaces",
            "trl",
            "spec_source_url",
            "price_date",
            "price_kind",
            "price_usd",
            "unit_price_usd",
            "currency",
            "original_amount",
            "quantity",
            "buyer",
            "programme",
            "price_source_url",
        ]
    )
    # Efficiency (SOL-AUDIT-2026-09-24.md #4, "Batch related-row lookups for exports and
    # citations"): this used to run 2 queries per exported payload (2N+1 total for N payloads --
    # `latest_spec`/`latest_price` each individually). `DISTINCT ON` pulls every payload's latest
    # spec version / latest price ref in one query apiece, matching each table's own
    # "latest" ordering (`version_no DESC` / `date DESC, id DESC`).
    payload_ids = [p["id"] for p in payloads]
    latest_spec_by_id: dict[int, dict[str, Any]] = {}
    latest_price_by_id: dict[int, dict[str, Any]] = {}
    if payload_ids:
        for row in _fetchall(
            "SELECT DISTINCT ON (payload_id) * FROM payload_spec_versions "
            "WHERE payload_id = ANY(%(ids)s) ORDER BY payload_id, version_no DESC",
            {"ids": payload_ids},
        ):
            latest_spec_by_id[row["payload_id"]] = row
        for row in _fetchall(
            "SELECT DISTINCT ON (payload_id) * FROM payload_price_refs "
            "WHERE payload_id = ANY(%(ids)s) ORDER BY payload_id, date DESC, id DESC",
            {"ids": payload_ids},
        ):
            latest_price_by_id[row["payload_id"]] = row

    for p in payloads:
        latest_spec = latest_spec_by_id.get(p["id"])
        latest_price = latest_price_by_id.get(p["id"])
        spec = (latest_spec or {}).get("spec") or {}
        detector = spec.get("detector") or {}
        fov = spec.get("fov") or {}
        ranges = spec.get("ranges_km") or {}
        writer.writerow(
            [
                p.get("canonical_name"),
                p.get("vendor_entity_name"),
                p.get("family"),
                p.get("category"),
                (latest_spec or {}).get("version_no"),
                (latest_spec or {}).get("effective_date"),
                spec.get("mass_kg"),
                ";".join(spec.get("channels") or []),
                detector.get("type"),
                detector.get("resolution"),
                detector.get("pitch_um"),
                fov.get("wide_deg"),
                fov.get("narrow_deg"),
                ranges.get("detect"),
                ranges.get("recognize"),
                ranges.get("identify"),
                spec.get("stabilisation_urad"),
                ";".join(spec.get("interfaces") or []),
                spec.get("trl"),
                (latest_spec or {}).get("source_url"),
                (latest_price or {}).get("date"),
                (latest_price or {}).get("price_kind"),
                (latest_price or {}).get("price_usd"),
                (latest_price or {}).get("unit_price_usd"),
                (latest_price or {}).get("currency"),
                (latest_price or {}).get("original_amount"),
                (latest_price or {}).get("quantity"),
                (latest_price or {}).get("buyer"),
                (latest_price or {}).get("programme"),
                (latest_price or {}).get("source_url"),
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payloads_latest.csv"},
    )


@router.get("/payloads/{payload_id}")
def get_payload(payload_id: int) -> dict[str, Any]:
    payload = _fetchone("SELECT * FROM payloads WHERE id = %(id)s", {"id": payload_id})
    if payload is None:
        raise HTTPException(status_code=404, detail='המטע"ד לא נמצא')
    versions = _fetchall(
        "SELECT * FROM payload_spec_versions WHERE payload_id = %(id)s ORDER BY version_no DESC",
        {"id": payload_id},
    )
    price_refs = _fetchall(
        "SELECT * FROM payload_price_refs WHERE payload_id = %(id)s ORDER BY date DESC, id DESC",
        {"id": payload_id},
    )
    return {"payload": payload, "spec_versions": versions, "price_refs": price_refs}


@router.get("/payloads/{payload_id}/diff")
def diff_payload_versions(
    payload_id: int,
    a: int = Query(..., description="older version_no"),
    b: int = Query(..., description="newer version_no"),
) -> dict[str, Any]:
    va = _fetchone(
        "SELECT * FROM payload_spec_versions WHERE payload_id = %(id)s AND version_no = %(v)s",
        {"id": payload_id, "v": a},
    )
    vb = _fetchone(
        "SELECT * FROM payload_spec_versions WHERE payload_id = %(id)s AND version_no = %(v)s",
        {"id": payload_id, "v": b},
    )
    if va is None or vb is None:
        raise HTTPException(status_code=404, detail="גרסה לא נמצאה")
    changed_keys = field_diff(va.get("spec"), vb.get("spec") or {})
    return {
        "payload_id": payload_id,
        "a": va,
        "b": vb,
        "changed_fields": changed_keys,
    }
