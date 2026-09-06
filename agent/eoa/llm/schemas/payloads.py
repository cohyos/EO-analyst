"""Pydantic schemas for structured LLM output used by ``eoa.payloads.extract`` (A17: EO payload
spec/price documentation with append-only version history).

Every numeric field here is re-checked deterministically against the source item's own text by
``eoa.payloads.extract._verify_numbers_verbatim`` after the model call returns -- this schema only
enforces *shape*, not the "every number must appear verbatim in the source" rule (docs/
CONVENTIONS.md rule 5), which cannot be checked from inside a single field's own validator (it
needs the source text, not available at validation time).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["gimbal", "pod", "thermal_camera", "detector_core", "lrf", "seeker", "other"]
PriceKind = Literal["unit", "contract", "estimate"]


class DetectorOut(BaseModel):
    type: str | None = Field(
        default=None, description="סוג הגלאי, כפי שמופיע במקור (למשל InSb, MCT, microbolometer)"
    )
    resolution: str | None = Field(default=None, description='רזולוציה כפי שמופיעה במקור, למשל "1280x1024"')
    pitch_um: float | None = Field(default=None, description="גודל פיקסל במיקרומטר, אם מצוין במקור")


class FovOut(BaseModel):
    wide_deg: float | None = Field(default=None, description="שדה ראייה רחב במעלות, אם מצוין")
    narrow_deg: float | None = Field(default=None, description="שדה ראייה צר (טלה) במעלות, אם מצוין")


class RangesKmOut(BaseModel):
    detect: float | None = Field(default=None, description='טווח גילוי בק"מ, אם מצוין')
    recognize: float | None = Field(default=None, description='טווח הכרה בק"מ, אם מצוין')
    identify: float | None = Field(default=None, description='טווח זיהוי בק"מ, אם מצוין')
    target_class: str | None = Field(
        default=None, description='סוג המטרה שאליה מתייחסים טווחי הגילוי/הכרה/זיהוי (למשל "כלי רכב", "אדם")'
    )


class PayloadSpecOut(BaseModel):
    """Fixed-vocabulary technical spec -- mirrors ``eoa.payloads.models.SPEC_KEYS``. Every leaf
    left ``null``/empty simply means the source text did not state it; never invented."""

    mass_kg: float | None = Field(default=None, description='משקל בק"ג, אם מצוין במקור')
    channels: list[str] = Field(
        default_factory=list, description="ערוצים כפי שמצוינים במקור, למשל MWIR/LWIR/SWIR/VIS/LRF/LD/LP"
    )
    detector: DetectorOut = Field(default_factory=DetectorOut)
    fov: FovOut = Field(default_factory=FovOut)
    ranges_km: RangesKmOut = Field(default_factory=RangesKmOut)
    stabilisation_urad: float | None = Field(default=None, description="ייצוב במיקרורדיאן, אם מצוין")
    interfaces: list[str] = Field(
        default_factory=list, description="ממשקים כפי שמצוינים במקור (למשל Ethernet, MIL-STD-1553)"
    )
    trl: str | None = Field(
        default=None, description="רמת בשלות טכנולוגית (TRL) כפי שמצוינת/משתמעת במקור, אם בכלל"
    )
    other: dict[str, str] = Field(
        default_factory=dict,
        description="שדות נוספים שמופיעים במפורש במקור ואינם משתייכים לשדות הקבועים לעיל",
    )


class PayloadPriceOut(BaseModel):
    amount: float | None = Field(
        default=None, description="הסכום כפי שהופיע במקור (המספר עצמו, לפני נרמול סדר גודל)"
    )
    currency: str | None = Field(default=None, description='מטבע, למשל "USD"/"ILS"/"EUR" -- כפי שמצוין במקור')
    quantity: int | None = Field(default=None, description="כמות יחידות, אם מצוינת")
    price_kind: PriceKind = Field(
        default="estimate", description="unit=מחיר יחידה, contract=סכום חוזה כולל, estimate=הערכה"
    )
    buyer: str | None = Field(default=None, description="גורם רוכש, אם מצוין")
    programme: str | None = Field(default=None, description="שם תוכנית/פרויקט, אם מצוין")
    date_text: str | None = Field(
        default=None, description="תאריך/שנה כפי שמופיעים במקור (טקסט חופשי, ינורמל בהמשך)"
    )


class PayloadExtractOut(BaseModel):
    """Stage: ``eoa.payloads.extract.run_payload_extract``. One structured reading of a single
    triaged item's mention of an EO payload's spec and/or reference price. ``found=false`` is the
    honest "not_found" outcome (docs/CONVENTIONS.md rule 5) for an item that only mentions payload
    vocabulary in passing without any concrete, extractable numbers -- the stage must never invent
    a spec/price just because the item was flagged as a candidate by keyword matching."""

    found: bool = Field(description='True אם נמצא מפרט/מחיר מטע"ד ממשי וניתן לחילוץ בפריט זה')
    payload_name: str = Field(
        default="", description='שם המטע"ד/המוצר כפי שמופיע במקור (שם קנוני של דגם, למשל "WESCAM MX-15D")'
    )
    vendor: str | None = Field(default=None, description="שם היצרן/הספק כפי שמופיע במקור")
    family: str | None = Field(default=None, description='משפחת מוצרים/סדרה, אם מצוינת (למשל "MX")')
    category: Category = Field(default="other", description='קטגוריית המטע"ד')
    spec: PayloadSpecOut = Field(default_factory=PayloadSpecOut)
    price: PayloadPriceOut | None = Field(
        default=None, description="מחיר ייחוס, אם מוזכר במקור; None אם לא מוזכר מחיר כלל"
    )
    source_quote: str = Field(
        description="המשפט/המשפטים המדויקים (ציטוט מילולי) מהמקור שמהם נלקחו הערכים המספריים לעיל"
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="רמת ביטחון בחילוץ, 0 עד 1")

    def has_any_spec_field(self) -> bool:
        s = self.spec
        return bool(
            s.mass_kg is not None
            or s.channels
            or s.detector.type
            or s.detector.resolution
            or s.detector.pitch_um is not None
            or s.fov.wide_deg is not None
            or s.fov.narrow_deg is not None
            or s.ranges_km.detect is not None
            or s.ranges_km.recognize is not None
            or s.ranges_km.identify is not None
            or s.stabilisation_urad is not None
            or s.interfaces
            or s.trl
            or s.other
        )
