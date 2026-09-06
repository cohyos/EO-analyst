"""Fixed spec vocabulary + plain dataclasses for A17 (EO payload spec/price documentation).

The ``spec`` JSONB column on ``payload_spec_versions`` always follows the shape below (a subset
may be ``null``/absent -- only fields the source text actually supports are ever filled in, per
docs/CONVENTIONS.md rule 5 "never invent"). This module is deliberately free of any DB/LLM
import so it can be imported by the API layer, the extraction stage, and tests alike without
pulling in either. The one exception (W19b, below) is
``eoa.pipeline.entity_normalize.resolve_canonical`` -- pure text/config lookup over
``config/watchlist.yaml``, not a DB or LLM call, safe to import at module scope per that
module's own docstring.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Literal

from eoa.pipeline.entity_normalize import resolve_canonical

Category = Literal["gimbal", "pod", "thermal_camera", "detector_core", "lrf", "seeker", "other"]
PriceKind = Literal["unit", "contract", "estimate"]

CATEGORIES: tuple[Category, ...] = (
    "gimbal",
    "pod",
    "thermal_camera",
    "detector_core",
    "lrf",
    "seeker",
    "other",
)
PRICE_KINDS: tuple[PriceKind, ...] = ("unit", "contract", "estimate")

# The fixed top-level keys a `spec` JSONB blob may carry. Used by the version-diff comparison
# (``eoa.payloads.extract.spec_differs``) to walk a stable, known key set rather than a raw dict
# equality check that would also trip on incidental key-ordering/serialization differences.
SPEC_KEYS: tuple[str, ...] = (
    "mass_kg",
    "channels",
    "detector",
    "fov",
    "ranges_km",
    "stabilisation_urad",
    "interfaces",
    "trl",
    "other",
)

# Recognized EO/IR channel labels (``spec.channels``) -- informational only, never enforced as a
# hard allow-list (a source may legitimately name a channel not on this short list).
KNOWN_CHANNELS: tuple[str, ...] = ("MWIR", "LWIR", "SWIR", "VIS", "LRF", "LD", "LP")


@dataclass
class PayloadIdentity:
    """The slow-changing identity half of one payload family -- mirrors the ``payloads`` row."""

    canonical_name: str
    vendor_entity_name: str | None = None
    family: str | None = None
    category: Category = "other"
    notes: str | None = None


@dataclass
class SpecVersionRecord:
    """One dated snapshot of a payload's spec, ready to insert as a ``payload_spec_versions`` row
    (``version_no`` assigned by ``eoa.payloads.extract`` after the append-only diff check)."""

    payload_id: int
    effective_date: dt.date
    spec: dict[str, Any]
    source_url: str | None
    source_quote: str
    confidence: float
    source_item_id: int | None = None
    version_no: int | None = None  # filled in by the caller right before INSERT


@dataclass
class PriceRefRecord:
    """One dated, cited reference-price observation, ready to insert as a ``payload_price_refs``
    row. Append-only -- there is no update path for this record shape."""

    payload_id: int
    date: dt.date
    source_quote: str
    price_usd: float | None = None
    currency: str | None = None
    original_amount: float | None = None
    quantity: int | None = None
    unit_price_usd: float | None = None
    price_kind: PriceKind = "estimate"
    buyer: str | None = None
    programme: str | None = None
    source_item_id: int | None = None
    source_url: str | None = None


# Payload-vocabulary trigger words for the extraction stage's item scan (Hebrew + English) --
# an item's title/clean_text must contain at least one of these (case-insensitive) to be a
# candidate for LLM extraction at all, per the A17 spec's scan criterion.
VOCAB_TRIGGERS: tuple[str, ...] = (
    "gimbal",
    "pod",
    "eo/ir turret",
    "eo-ir turret",
    "electro-optical turret",
    "thermal camera",
    "infrared camera",
    "detector",
    "focal plane array",
    "lrf",
    "laser range finder",
    "laser rangefinder",
    'מטע"ד',
    "מטען ייעודי",
    "גימבל",
    "פוד",
    "מצלמה תרמית",
    "כדור תצפית",
)


def field_diff(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """List of top-level ``SPEC_KEYS`` whose value differs between ``old`` and ``new`` (``old``
    ``None`` means "no prior version" -- every non-empty key in ``new`` counts as a diff then).
    Plain ``!=`` on the (JSON-serializable) values is enough here since both sides are always
    built from the same fixed vocabulary with the same nesting shape."""
    old = old or {}
    diffs: list[str] = []
    for key in SPEC_KEYS:
        old_val = old.get(key)
        new_val = new.get(key)
        # Treat an empty/falsy value on both sides as equal (None vs {} vs [] vs "" are all
        # "nothing said about this field" and must not register as a spurious new version).
        if not old_val and not new_val:
            continue
        if old_val != new_val:
            diffs.append(key)
    return diffs


# ------------------------------------------------------------------------------------------
# W19b (docs/REVIEW_2026-09-06_evening.md, migration 0024): vendor canonicalisation + a
# deterministic family/variant parser, so the payloads screen can group manufacturers' products
# by family and let the operator drill down instead of flooding them with a flat 62+ row table
# (user requirement, 2026-09-06 21:20 verbatim: "group the manufacturers' products by families
# and allow drill-down, not flooding the operator").
# ------------------------------------------------------------------------------------------

# Curated leading brand token(s) as they actually appear at the start of a `canonical_name` in
# `config/payloads_seed.yaml` -- longest first, so e.g. "Teledyne FLIR" is matched before the
# shorter "FLIR" alias of the same vendor would otherwise steal the first word. This is a
# *display* table only (it never asserts a fact about a product) -- it just tells the parser
# below where the vendor's own brand word ends and the product name proper begins. Extending the
# seed with a new vendor whose canonical names don't start with one of these prefixes still works
# (the parser just treats the whole name as the "rest" -- see :func:`parse_family_variant`).
BRAND_PREFIXES: tuple[str, ...] = (
    "Teledyne FLIR",
    "Lockheed Martin",
    "Northrop Grumman",
    "Collins Aerospace",
    "Silent Sentinel",
    "UAV Vision",
    "TrakkaCam",
    "Trakka",
    "WESCAM",
    "FLIR",
    "Controp",
    "Elbit",
    "Rafael",
    "IAI",
    "Safran",
    "Hensoldt",
    "Leonardo",
    "Aselsan",
    "Opgal",
    "PVP",
    "Thales",
    "Raytheon",
    "NextVision",
    "HGH",
    "SCD",
    "Lynred",
    "Exosens",
)

# A trailing chunk is treated as a *variant designator* (stripped off to derive the shared
# `family`) only when it is short and code-like: all upper-case letters/digits, length 1-4 (e.g.
# "HD", "II", "ATP", "A", "300"). A real word ("Stamp" in "T-Stamp", "Systems") never matches this
# and is left as part of the family name instead of being mistaken for a model-number suffix.
_CODE_SUFFIX_RE = re.compile(r"^[A-Z0-9]{1,4}$")
# A single hyphen-free token with letters followed directly by digits (e.g. "POP300") -- peeled
# the same way a hyphenated token is, so "POP300" and "Mini-POP" both resolve toward "POP".
_TRAILING_DIGITS_RE = re.compile(r"^([A-Za-z]+)(\d[\w]*)$")


def _looks_like_variant_suffix(piece: str) -> bool:
    """True if ``piece`` (a hyphen-split tail, or a whole last whitespace token) reads as a
    model-number/generation suffix rather than a real word -- any digit anywhere, or a short
    all-uppercase-alnum code (see :data:`_CODE_SUFFIX_RE`)."""
    if not piece:
        return False
    if any(ch.isdigit() for ch in piece):
        return True
    return bool(_CODE_SUFFIX_RE.match(piece))


# A handful of canonical names whose correct family grouping is a *prefix* modifier ("Mini-POP")
# rather than a trailing model-number suffix, which the generic right-to-left split below cannot
# tell apart from a real two-part product name on its own. Keyed by the exact `canonical_name`;
# extend this table (never the regex) for the next such exception rather than special-casing the
# parser further.
FAMILY_OVERRIDES: dict[str, str] = {
    "IAI Mini-POP": "POP",
}


def parse_family_variant(canonical_name: str) -> tuple[str, str]:
    """Deterministic ``(family, variant)`` split of a payload's ``canonical_name``.

    ``variant`` is always the canonical name with its leading vendor brand word(s) stripped (see
    :data:`BRAND_PREFIXES`) -- e.g. "WESCAM MX-15" -> "MX-15". ``family`` is that same remainder
    with its trailing model-number/generation designator additionally stripped when one is
    present (:func:`_looks_like_variant_suffix`), so several variants of one product line share a
    family the operator can collapse in the UI (tree: vendor -> family -> variant) -- e.g.
    "WESCAM MX-6"/"MX-15"/"MX-20HD" all get family "MX"; a single, ungenerationed product name
    (e.g. "Toplite", "DCoMPASS", "T-Stamp") gets ``family == variant`` (a single-variant family).

    Never returns an empty string for either half -- a canonical name that is *only* a brand
    word (no seed entry today does this) falls back to the full name for both.
    """
    name = (canonical_name or "").strip()
    if not name:
        return "", ""

    rest = name
    for brand in BRAND_PREFIXES:
        if name == brand:
            rest = name
            break
        if name.startswith(brand + " "):
            rest = name[len(brand) :].strip()
            break
    variant = rest or name

    if name in FAMILY_OVERRIDES:
        return FAMILY_OVERRIDES[name], variant

    tokens = variant.split(" ")
    if len(tokens) > 1:
        last = tokens[-1]
        if _looks_like_variant_suffix(last):
            family = " ".join(tokens[:-1]).strip()
            return (family or variant), variant
        return variant, variant

    # Single token: try a hyphen split first ("MX-15", "T-Stamp", "Mini-POP"), then a bare
    # trailing-digits split with no hyphen at all ("POP300").
    token = tokens[0]
    if "-" in token:
        base, _, suffix = token.rpartition("-")
        if base and _looks_like_variant_suffix(suffix):
            return base, variant
        return variant, variant

    m = _TRAILING_DIGITS_RE.match(token)
    if m:
        return m.group(1), variant
    return variant, variant


def canonical_vendor(vendor_entity_name: str | None) -> str | None:
    """The watchlist canonical company name for ``vendor_entity_name`` (Q3-13's
    ``resolve_canonical``, e.g. "Raytheon" and "Collins Aerospace" both resolve to "RTX", their
    real corporate parent) -- used to group the payloads tree by vendor without collapsing
    sibling brands the watchlist itself doesn't recognise as one company. Falls back to
    ``vendor_entity_name`` unchanged when it isn't (yet) a recognised watchlist alias, per
    docs/CONVENTIONS.md rule 5 "never invent" -- an unrecognised vendor string is never guessed
    at, just passed through."""
    if not vendor_entity_name:
        return vendor_entity_name
    record = resolve_canonical(vendor_entity_name)
    return record["name"] if record else vendor_entity_name


def build_payload_tree(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Group flat ``payloads`` rows (each already carrying ``spec_version_count``/
    ``price_ref_count``/``latest_spec_date``/``latest_price_date`` -- the same shape
    ``eoa.api.routes.payloads.list_payloads`` computes) into
    ``vendor -> family -> variant`` for the collapsed-tree UI (W19b). Pure/DB-free so it is
    independently unit-testable against hand-built row dicts.

    Vendor grouping uses :func:`canonical_vendor` (RTX absorbs Raytheon/Collins Aerospace);
    family/variant grouping uses each row's own stored ``family``/``variant`` columns (already
    backfilled by :func:`parse_family_variant` at seed/extraction time) rather than
    re-parsing here, so a manually-corrected family in the DB is always respected.
    """
    vendors: dict[str, dict[str, Any]] = {}
    for row in rows:
        vendor_key = canonical_vendor(row.get("vendor_entity_name")) or "—"
        family_key = row.get("family") or row.get("canonical_name") or "—"
        variant_label = row.get("variant") or row.get("canonical_name") or "—"

        vendor_node = vendors.setdefault(
            vendor_key,
            {"vendor": vendor_key, "families": {}},
        )
        family_node = vendor_node["families"].setdefault(
            family_key,
            {"family": family_key, "variants": []},
        )
        family_node["variants"].append(
            {
                "id": row.get("id"),
                "canonical_name": row.get("canonical_name"),
                "variant": variant_label,
                "category": row.get("category"),
                "image_url": row.get("image_url"),
                "spec_url": row.get("spec_url"),
                "spec_source": row.get("spec_source"),
                "spec_version_count": row.get("spec_version_count") or 0,
                "price_ref_count": row.get("price_ref_count") or 0,
                "latest_spec_date": row.get("latest_spec_date"),
                "latest_price_date": row.get("latest_price_date"),
            }
        )

    vendor_list: list[dict[str, Any]] = []
    for vendor_key in sorted(vendors.keys()):
        vendor_node = vendors[vendor_key]
        family_list: list[dict[str, Any]] = []
        for family_key in sorted(vendor_node["families"].keys()):
            family_node = vendor_node["families"][family_key]
            variants = sorted(family_node["variants"], key=lambda v: v["canonical_name"] or "")
            family_list.append(
                {
                    "family": family_key,
                    "variant_count": len(variants),
                    "spec_version_count": sum(v["spec_version_count"] for v in variants),
                    "price_ref_count": sum(v["price_ref_count"] for v in variants),
                    "latest_spec_date": max(
                        (v["latest_spec_date"] for v in variants if v["latest_spec_date"]),
                        default=None,
                    ),
                    "latest_price_date": max(
                        (v["latest_price_date"] for v in variants if v["latest_price_date"]),
                        default=None,
                    ),
                    "variants": variants,
                }
            )
        vendor_payload_count = sum(f["variant_count"] for f in family_list)
        vendor_list.append(
            {
                "vendor": vendor_key,
                "family_count": len(family_list),
                "payload_count": vendor_payload_count,
                "spec_version_count": sum(f["spec_version_count"] for f in family_list),
                "price_ref_count": sum(f["price_ref_count"] for f in family_list),
                "families": family_list,
            }
        )

    return {
        "vendors": vendor_list,
        "vendor_count": len(vendor_list),
        "family_count": sum(v["family_count"] for v in vendor_list),
        "payload_count": sum(v["payload_count"] for v in vendor_list),
    }
