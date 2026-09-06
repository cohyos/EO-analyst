"""Stage: EO payload spec/price extraction (A17, docs/PLAN_WINDOWS_NATIVE.md row A17).

Scans triaged, in-scope items whose text mentions payload vocabulary (gimbal, pod, EO/IR turret,
thermal camera, detector, LRF, מטע"ד, מטען ייעודי, גימבל, פוד -- see
``eoa.payloads.models.VOCAB_TRIGGERS``), asks the LLM for one structured
:class:`~eoa.llm.schemas.payloads.PayloadExtractOut` reading per item, deterministically verifies
every numeric value it returned actually occurs verbatim in the item's own text (rejecting any
field that doesn't -- docs/CONVENTIONS.md rule 5, "never invent"), then persists via the
append-only rules the user asked for: a new ``payload_spec_versions`` row only when at least one
spec field actually differs from the payload's latest version; a new ``payload_price_refs`` row
for every price mention (prices are always append-only observations, never compared/deduped
against a "latest" value). Nothing here ever runs an ``UPDATE``/``DELETE`` against either child
table.

A single item's LLM failure or a single verbatim-check rejection never stops the batch
(docs/CONVENTIONS.md rule 9) -- the item is simply left unmarked for this stage and can be
retried next run.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.payloads import PayloadExtractOut
from eoa.memory.relational import mark_stage
from eoa.payloads.models import CATEGORIES, VOCAB_TRIGGERS, field_diff
from eoa.pipeline.analyze import normalize_amount_from_source

log = structlog.get_logger(__name__)

STAGE_NAME = "payload_extract"
_TRIGGER_LOWER = tuple(t.lower() for t in VOCAB_TRIGGERS)


# --------------------------------------------------------------------------
# item scan
# --------------------------------------------------------------------------


def _item_text(item: dict[str, Any]) -> str:
    return " ".join(filter(None, [item.get("title"), item.get("clean_text"), item.get("raw_text")]))


def scan_candidate_items(limit: int = 50, *, item_ids: list[int] | None = None) -> list[dict[str, Any]]:
    """Triaged, in-scope, security-clean items whose text mentions payload vocabulary and have not
    yet completed the ``payload_extract`` stage. "In-scope" here mirrors the rest of the pipeline's
    convention: any triaged level (``red``/``orange``/``yellow``) counts; ``archive`` does not, and
    an item still awaiting triage (``level IS NULL``) is left for the triage stage to reach first."""
    query = """
        SELECT id, title, clean_text, raw_text, url, published_at
        FROM items
        WHERE security_status = 'clean'
          AND level IN ('red', 'orange', 'yellow')
          AND NOT (%(stage)s = ANY(COALESCE(processed_stages, '{}')))
          AND (%(item_ids)s::bigint[] IS NULL OR id = ANY(%(item_ids)s::bigint[]))
        ORDER BY fetched_at NULLS LAST, id
        LIMIT %(scan_limit)s
    """
    # Scan a wider window than `limit` since most triaged items won't mention payload vocabulary
    # at all -- `limit` bounds how many *matching* items are returned, not how many rows are read.
    scan_limit = max(limit * 20, 200)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"stage": STAGE_NAME, "item_ids": item_ids, "scan_limit": scan_limit})
        rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        text = _item_text(row).lower()
        if any(trigger in text for trigger in _TRIGGER_LOWER):
            out.append(row)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------
# deterministic verbatim-number check (docs/CONVENTIONS.md rule 5)
# --------------------------------------------------------------------------


def _number_variants(value: float) -> list[str]:
    if float(value).is_integer():
        i = int(value)
        return [str(i), f"{i:,}"]
    base = f"{value:.4f}".rstrip("0").rstrip(".")
    return [base, base.replace(".", ",")]


def _number_in_text(value: float | int | None, text: str) -> bool:
    if value is None:
        return True
    return any(v in text for v in _number_variants(float(value)))


@dataclass
class VerbatimCheckResult:
    spec: dict[str, Any]
    rejected_fields: list[str] = field(default_factory=list)
    price_ok: bool = True
    price_rejected_fields: list[str] = field(default_factory=list)


def verify_numbers_verbatim(out: PayloadExtractOut, item_text: str) -> VerbatimCheckResult:
    """Null out any numeric spec/price field whose value does not literally occur (in some plain
    numeric rendering) in ``item_text`` -- rather than discarding the whole extraction, since one
    unverifiable number (e.g. a rounded/derived figure the model computed) should not also erase
    the rest of an otherwise-grounded reading. Every rejection is logged and returned so the
    caller can decide whether to persist at all (an extraction that had to reject its *only*
    field means there is nothing left worth storing)."""
    result = VerbatimCheckResult(spec={})
    s = out.spec
    rejected: list[str] = []

    def check(val: float | int | None, name: str) -> float | int | None:
        if val is not None and not _number_in_text(val, item_text):
            rejected.append(name)
            return None
        return val

    mass_kg = check(s.mass_kg, "mass_kg")
    pitch_um = check(s.detector.pitch_um, "detector.pitch_um")
    wide_deg = check(s.fov.wide_deg, "fov.wide_deg")
    narrow_deg = check(s.fov.narrow_deg, "fov.narrow_deg")
    detect = check(s.ranges_km.detect, "ranges_km.detect")
    recognize = check(s.ranges_km.recognize, "ranges_km.recognize")
    identify = check(s.ranges_km.identify, "ranges_km.identify")
    stabilisation = check(s.stabilisation_urad, "stabilisation_urad")

    spec: dict[str, Any] = {
        "mass_kg": mass_kg,
        "channels": list(s.channels) or None,
        "detector": (
            {"type": s.detector.type, "resolution": s.detector.resolution, "pitch_um": pitch_um}
            if (s.detector.type or s.detector.resolution or pitch_um is not None)
            else None
        ),
        "fov": (
            {"wide_deg": wide_deg, "narrow_deg": narrow_deg}
            if (wide_deg is not None or narrow_deg is not None)
            else None
        ),
        "ranges_km": (
            {
                "detect": detect,
                "recognize": recognize,
                "identify": identify,
                "target_class": s.ranges_km.target_class,
            }
            if (
                detect is not None
                or recognize is not None
                or identify is not None
                or s.ranges_km.target_class
            )
            else None
        ),
        "stabilisation_urad": stabilisation,
        "interfaces": list(s.interfaces) or None,
        "trl": s.trl or None,
        "other": dict(s.other) or None,
    }
    result.spec = {k: v for k, v in spec.items() if v}
    result.rejected_fields = rejected

    if out.price is not None:
        price_rejected: list[str] = []
        amount = out.price.amount
        if amount is not None and not _number_in_text(amount, item_text):
            price_rejected.append("price.amount")
        quantity = out.price.quantity
        if quantity is not None and not _number_in_text(quantity, item_text):
            price_rejected.append("price.quantity")
        result.price_rejected_fields = price_rejected

    return result


# --------------------------------------------------------------------------
# payload identity resolution (canonical name upsert)
# --------------------------------------------------------------------------


def _resolve_payload_id(
    *, canonical_name: str, vendor: str | None, family: str | None, category: str, today: dt.date
) -> int:
    """Get-or-create the ``payloads`` row for ``canonical_name`` (case-insensitive match), bumping
    ``last_seen``/backfilling ``vendor_entity_name``/``family`` non-destructively (never overwrites
    an already-set value -- identity fields are curated, not re-derived every extraction run)."""
    category = category if category in CATEGORIES else "other"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM payloads WHERE lower(canonical_name) = lower(%(name)s)", {"name": canonical_name}
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO payloads (canonical_name, vendor_entity_name, family, category, first_seen, last_seen)
                VALUES (%(name)s, %(vendor)s, %(family)s, %(category)s, %(today)s, %(today)s)
                ON CONFLICT (canonical_name) DO UPDATE SET last_seen = EXCLUDED.last_seen
                RETURNING id
                """,
                {
                    "name": canonical_name,
                    "vendor": vendor,
                    "family": family,
                    "category": category,
                    "today": today,
                },
            )
            return cur.fetchone()["id"]
        payload_id = row["id"]
        cur.execute(
            """
            UPDATE payloads SET
                last_seen = %(today)s,
                vendor_entity_name = COALESCE(vendor_entity_name, %(vendor)s),
                family = COALESCE(family, %(family)s)
            WHERE id = %(id)s
            """,
            {"id": payload_id, "today": today, "vendor": vendor, "family": family},
        )
        return payload_id


def _latest_spec_version(payload_id: int) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT version_no, spec FROM payload_spec_versions WHERE payload_id = %(id)s "
            "ORDER BY version_no DESC LIMIT 1",
            {"id": payload_id},
        )
        return cur.fetchone()


def _insert_spec_version(
    payload_id: int,
    *,
    version_no: int,
    effective_date: dt.date,
    spec: dict[str, Any],
    source_item_id: int | None,
    source_url: str | None,
    source_quote: str,
    confidence: float,
) -> int:
    from psycopg.types.json import Json

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payload_spec_versions
                (payload_id, version_no, effective_date, spec, source_item_id, source_url, source_quote, confidence)
            VALUES (%(payload_id)s, %(version_no)s, %(effective_date)s, %(spec)s, %(source_item_id)s,
                    %(source_url)s, %(source_quote)s, %(confidence)s)
            RETURNING id
            """,
            {
                "payload_id": payload_id,
                "version_no": version_no,
                "effective_date": effective_date,
                "spec": Json(spec),
                "source_item_id": source_item_id,
                "source_url": source_url,
                "source_quote": source_quote,
                "confidence": confidence,
            },
        )
        return cur.fetchone()["id"]


def _insert_price_ref(
    payload_id: int,
    *,
    date: dt.date,
    price_usd: float | None,
    currency: str | None,
    original_amount: float | None,
    quantity: int | None,
    unit_price_usd: float | None,
    price_kind: str,
    buyer: str | None,
    programme: str | None,
    source_item_id: int | None,
    source_url: str | None,
    source_quote: str,
) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payload_price_refs
                (payload_id, price_usd, currency, original_amount, quantity, unit_price_usd, price_kind,
                 date, buyer, programme, source_item_id, source_url, source_quote)
            VALUES (%(payload_id)s, %(price_usd)s, %(currency)s, %(original_amount)s, %(quantity)s,
                    %(unit_price_usd)s, %(price_kind)s, %(date)s, %(buyer)s, %(programme)s,
                    %(source_item_id)s, %(source_url)s, %(source_quote)s)
            RETURNING id
            """,
            {
                "payload_id": payload_id,
                "price_usd": price_usd,
                "currency": currency,
                "original_amount": original_amount,
                "quantity": quantity,
                "unit_price_usd": unit_price_usd,
                "price_kind": price_kind,
                "date": date,
                "buyer": buyer,
                "programme": programme,
                "source_item_id": source_item_id,
                "source_url": source_url,
                "source_quote": source_quote,
            },
        )
        return cur.fetchone()["id"]


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _parse_price_date(date_text: str | None, published_at: Any, today: dt.date) -> dt.date:
    """Best-effort date for a price observation: a 4-digit year in ``date_text`` -> Jan 1 of that
    year; else the item's own ``published_at``; else today. Never raises."""
    if date_text:
        m = _YEAR_RE.search(date_text)
        if m:
            try:
                return dt.date(int(m.group(0)), 1, 1)
            except ValueError:
                pass
    if published_at is not None:
        try:
            return (
                published_at.date()
                if hasattr(published_at, "date")
                else dt.date.fromisoformat(str(published_at)[:10])
            )
        except (ValueError, TypeError):
            pass
    return today


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


@dataclass
class PayloadExtractStats:
    scanned: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    not_found: int = 0
    spec_versions_created: int = 0
    spec_unchanged: int = 0
    price_refs_created: int = 0
    numeric_fields_rejected: int = 0


def _extract_one(item: dict[str, Any], *, role: str, interactive: bool) -> PayloadExtractOut:
    text = _item_text(item)
    prompt = render(
        "payload_extract",
        data=wrap_data(text, item["id"], item.get("url") or ""),
    )
    return chat_structured(
        role,
        PayloadExtractOut,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="extract",
        interactive=interactive,
    )


def run_payload_extract(
    limit: int = 20, *, item_ids: list[int] | None = None, role: str = "resident", interactive: bool = False
) -> PayloadExtractStats:
    """A17 entry point: scan up to ``limit`` payload-vocabulary candidate items, extract spec/price
    per :class:`~eoa.llm.schemas.payloads.PayloadExtractOut`, verify every number verbatim against
    the item's own text, then persist per the append-only rules (new spec version only on an
    actual diff; every price mention always appended). Marks ``payload_extract`` done on every
    item this stage looked at, whether or not it yielded a persisted row, so a re-run never
    re-processes the same item indefinitely."""
    stats = PayloadExtractStats()
    today = dt.date.today()

    for item in scan_candidate_items(limit, item_ids=item_ids):
        stats.scanned += 1
        item_text = _item_text(item)
        try:
            out = _extract_one(item, role=role, interactive=interactive)
        except ResourceUnavailable:
            stats.llm_deferred += 1
            continue
        except LLMOutputError as exc:
            log.warning("payload_extract_llm_failed", item_id=item["id"], error=str(exc)[:200])
            stats.llm_failed += 1
            continue
        except Exception as exc:
            log.warning("payload_extract_unexpected_error", item_id=item["id"], error=str(exc)[:200])
            stats.llm_failed += 1
            continue

        if not out.found or not out.payload_name.strip():
            stats.not_found += 1
            mark_stage(item["id"], STAGE_NAME)
            continue

        check = verify_numbers_verbatim(out, item_text)
        stats.numeric_fields_rejected += len(check.rejected_fields) + len(check.price_rejected_fields)
        if check.rejected_fields:
            log.warning(
                "payload_extract_fields_rejected_not_verbatim",
                item_id=item["id"],
                payload=out.payload_name,
                fields=check.rejected_fields,
            )

        try:
            payload_id = _resolve_payload_id(
                canonical_name=out.payload_name.strip(),
                vendor=(out.vendor or "").strip() or None,
                family=(out.family or "").strip() or None,
                category=out.category,
                today=today,
            )

            if check.spec:
                latest = _latest_spec_version(payload_id)
                diffs = field_diff((latest or {}).get("spec"), check.spec)
                if diffs or latest is None:
                    next_version = (latest["version_no"] + 1) if latest else 1
                    _insert_spec_version(
                        payload_id,
                        version_no=next_version,
                        effective_date=today,
                        spec=check.spec,
                        source_item_id=item["id"],
                        source_url=item.get("url"),
                        source_quote=out.source_quote,
                        confidence=out.confidence,
                    )
                    stats.spec_versions_created += 1
                else:
                    stats.spec_unchanged += 1

            if out.price is not None and "price.amount" not in check.price_rejected_fields:
                amount = out.price.amount
                normalized_amount, _ = (
                    normalize_amount_from_source(amount, item_text) if amount is not None else (None, None)
                )
                price_usd = (
                    normalized_amount
                    if (out.price.currency or "").upper() in ("USD", "US$", "$", "")
                    else None
                )
                quantity = out.price.quantity if "price.quantity" not in check.price_rejected_fields else None
                unit_price_usd = None
                if price_usd is not None and quantity and quantity > 0 and out.price.price_kind == "contract":
                    unit_price_usd = round(price_usd / quantity, 2)
                elif price_usd is not None and out.price.price_kind == "unit":
                    unit_price_usd = price_usd
                _insert_price_ref(
                    payload_id,
                    date=_parse_price_date(out.price.date_text, item.get("published_at"), today),
                    price_usd=price_usd,
                    currency=out.price.currency,
                    original_amount=amount,
                    quantity=quantity,
                    unit_price_usd=unit_price_usd,
                    price_kind=out.price.price_kind,
                    buyer=out.price.buyer,
                    programme=out.price.programme,
                    source_item_id=item["id"],
                    source_url=item.get("url"),
                    source_quote=out.source_quote,
                )
                stats.price_refs_created += 1
        except Exception as exc:
            log.warning("payload_extract_persist_failed", item_id=item["id"], error=str(exc)[:200])
        finally:
            mark_stage(item["id"], STAGE_NAME)

    log.info("payload_extract_done", **vars(stats))
    return stats
