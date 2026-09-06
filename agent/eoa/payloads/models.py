"""Fixed spec vocabulary + plain dataclasses for A17 (EO payload spec/price documentation).

The ``spec`` JSONB column on ``payload_spec_versions`` always follows the shape below (a subset
may be ``null``/absent -- only fields the source text actually supports are ever filled in, per
docs/CONVENTIONS.md rule 5 "never invent"). This module is deliberately free of any DB/LLM
import so it can be imported by the API layer, the extraction stage, and tests alike without
pulling in either.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

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
