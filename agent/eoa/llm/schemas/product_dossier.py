"""Pydantic schema for the product dossier ("סקירת שוק עמוקה למוצר", PD-backend, user request
2026-09-08 -- ``docs/PLAN_PRODUCT_DOSSIER.md`` section 3, the frozen contract).

``ProductDossierOut`` is the single structured-extraction output of ``eoa.dossier.extract``: one
LLM call, cloud chain, JSON schema = this class, over the corpus + every deep-search topic finding
(``eoa.dossier.corpus``/``plan``). Every field that carries a factual claim (a number, a date, a
name, a price) also carries ``cites: list[int]`` into the dossier's own citation registry -- the
same numbered-registry convention every other report in this codebase already uses
(``eoa.llm.schemas.analysis.Sentence`` / ``eoa.report.qa_citations``); a field the research could
not establish is left ``null``/empty rather than guessed (``eoa.report.docx_builder``-style
placeholders render it as "לא נמצא במקורות" -- see ``eoa.dossier.report``).

Unlike ``Sentence`` (which requires a *non-empty* ``cites`` -- an unsourced sentence has no
business existing at all, per that class's own docstring), every row model below allows an EMPTY
``cites`` list: the deterministic post-checks in ``eoa.dossier.extract`` (grounding -- every cite
must be a real registry number, every number in a value must appear in the cited source text) drop
an ungrounded field to ``cites=[]`` + a null/placeholder value rather than raising, exactly per the
plan's "deterministic post-checks ... drop the offending field to null" instruction. A *non-empty*
``cites`` list is still required to still validate as non-null after those checks run -- enforced in
``eoa.dossier.extract``, not in this schema (the schema must first accept whatever the model wrote
so the post-check pass can inspect and trim it).

Pricing (``PriceRow``) is the one row kind the plan singles out for extra discipline (section 1 and
6.3): only a figure that appears in a contract award, tender, budget line, FMS notice or a quoted
official is ever extracted at all -- enforced by the extraction prompt
(``agent/eoa/llm/prompts/product_dossier_extract.md``) and, deterministically, by
``eoa.dossier.extract``'s post-checks (a price row with no ``basis_he``/``source_kind`` naming one
of those four source types, or whose ``cites`` don't resolve to a registry entry, is dropped).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from eoa.llm.schemas.analysis import Sentence

ProductStatusHe = Literal["בפיתוח", "בייצור", "מופעל בשטח", "הוצא משימוש", "לא ידוע"]

DealKind = Literal["contract_award", "FMS", "framework", "option", "export_license"]
SourceKind = Literal["datasheet", "brochure", "article", "official", "contract", "tender", "budget", "other"]
PartnerRole = Literal["integrator", "subcontractor", "co-development", "reseller", "other"]


class IdentityBlock(BaseModel):
    """Section 3's ``identity`` block -- the product's own basic identity facts."""

    product_name: str = Field(description="שם המוצר כפי שמופיע במקורות")
    vendor: str = Field(default="", description="היצרן/הספק; מחרוזת ריקה אם לא ידוע")
    product_family: str = Field(default="", description="משפחת מוצרים/פלטפורמה; מחרוזת ריקה אם לא ידוע")
    category_he: str = Field(default="", description="קטגוריה טכנולוגית קצרה בעברית")
    first_announced: str | None = Field(
        default=None, description="תאריך הכרזה ראשונה (ISO אם ידוע), אחרת null"
    )
    status_he: ProductStatusHe = "לא ידוע"
    cites: list[int] = Field(default_factory=list)


class SpecRow(BaseModel):
    """One published specification row -- ``value`` is copied verbatim as published (never
    normalized/converted), per the report-style rule "numbers only from sources".

    PD-vocab-extract (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md): ``key`` is the stable join into
    ``config/spec_vocabulary.yaml`` (``eoa.dossier.vocabulary``) -- set to a vocabulary key VERBATIM
    when the row fills one of that vocabulary's parameters, left ``""`` for a row in
    ``ProductDossierOut.other_specifications`` (a genuine fact matching no vocabulary entry). A row
    with a non-empty ``key`` also has its ``parameter_he`` overwritten to that key's own canonical
    ``label_he`` by ``eoa.dossier.extract``'s post-check -- the model's own copy of the label is
    never trusted, one canonical write path only."""

    parameter_he: str = Field(description="שם הפרמטר בעברית (למשל: טווח זיהוי, משקל, צריכת הספק)")
    key: str = Field(default="", description="מפתח יציב מתוך config/spec_vocabulary.yaml, ריק אם 'אחר'")
    value: str = Field(default="", description="הערך כפי שפורסם, כולל יחידות")
    unit: str = Field(default="", description="יחידת מידה, אם רלוונטי בנפרד מ-value")
    variant: str = Field(default="", description="גרסה/וריאנט שאליו הערך מתייחס, אם צוין")
    source_kind: SourceKind = "other"
    cites: list[int] = Field(default_factory=list)


class VersionRow(BaseModel):
    name: str = Field(description="שם הגרסה/הדגם")
    year: str | None = Field(default=None, description="שנת השקה/עדכון (ISO אם ידוע), אחרת null")
    changes_he: str = Field(default="", description="מה השתנה בגרסה זו לעומת קודמתה, אם ידוע")
    platforms: list[str] = Field(default_factory=list, description="פלטפורמות נשא ידועות לגרסה זו")
    cites: list[int] = Field(default_factory=list)


class PerformanceRow(BaseModel):
    """ "claimed" (יצרן/פרסום) לעומת "demonstrated"/"operational" (ניסוי/הפעלה בפועל) -- לעולם לא
    מוצגים כאותו הדבר; ``tested_or_operational_value`` נשאר ``null`` כשלא ידוע."""

    metric_he: str = Field(description="שם המדד (למשל: טווח זיהוי MWIR, קצב זיהוי)")
    #: PD-vocab-extract (2026-09-09): same key/normalization contract as SpecRow.key -- see that
    #: field's own docstring.
    key: str = Field(default="", description="מפתח יציב מתוך config/spec_vocabulary.yaml, ריק אם 'אחר'")
    claimed_value: str = Field(default="", description="הערך המוצהר על ידי היצרן")
    tested_or_operational_value: str | None = Field(
        default=None, description="ערך שנמדד/הופעל בפועל (ניסוי/שטח), אם דווח בנפרד; אחרת null"
    )
    conditions_he: str = Field(default="", description="תנאי המדידה/ההפעלה, אם צוינו")
    cites: list[int] = Field(default_factory=list)


class MaturityBlock(BaseModel):
    trl: int | None = Field(default=None, ge=1, le=9, description="TRL 1-9 אם ניתן להסיק, אחרת null")
    operational_users: list[str] = Field(default_factory=list, description="מפעילים ידועים")
    platforms_integrated: list[str] = Field(default_factory=list, description="פלטפורמות שבהן שולב")
    first_fielding: str | None = Field(default=None, description="תאריך פריסה מבצעית ראשונה, אם ידוע")
    assessment_he: str = Field(default="", description="הערכת בשלות קצרה בעברית")
    cites: list[int] = Field(default_factory=list)


DealDateKind = Literal["deal", "published"]


class DealRow(BaseModel):
    date: str | None = Field(default=None, description="תאריך העסקה (ISO אם ידוע)")
    #: PD-fix (2026-09-08, item 3): when the deal itself carries no date, ``eoa.dossier.extract``'s
    #: post-check backfills ``date`` from the cited source's own publish date and marks it
    #: ``"published"`` (never silently indistinguishable from an actual deal-closing date).
    date_kind: DealDateKind = "deal"
    #: PD-fix-3 (2026-09-08, item 4): ``None`` (never a placeholder string like ``"—"``/"לא ידוע")
    #: when the customer is unknown -- ``eoa.dossier.extract``'s post-check normalizes whatever
    #: placeholder text the model wrote to ``None`` here, so the persisted/API value is a real null
    #: the UI/renderer can each show their own placeholder for, rather than baking one in.
    customer: str | None = Field(default=None, description="הלקוח/הרוכש; null אם לא ידוע")
    country: str = Field(
        default="",
        description="מדינת הלקוח הספציפית בלבד; אם המקור מציין רק אזור (למשל 'מדינה באסיה-פסיפיק') ולא "
        "מדינה מסוימת, השאר שדה זה ריק ותאר את האזור ב-region_he במקום.",
    )
    #: PD-fix item 3: a source that only names a region ("Asia-Pacific country") rather than a
    #: specific country -- ``country`` stays empty (never a guessed country) and the region text
    #: goes here instead. ``eoa.dossier.extract``'s post-check also reclassifies a `country` value
    #: that reads like a region rather than trusting the model unconditionally.
    region_he: str = Field(default="", description="תיאור אזור, כאשר המקור נוקב אזור ולא מדינה ספציפית")
    kind: DealKind = "contract_award"
    amount: str = Field(default="", description="סכום כפי שפורסם (כולל יחידה/סקאלה), ריק אם לא ידוע")
    #: PD-fix item 3: deterministically parsed from ``amount`` by ``eoa.dossier.extract`` (Hebrew
    #: scale words -- אלף/מיליון/מיליארד -- and currency words/symbols); the model never fills this
    #: itself and never derives it from anything but ``amount``'s own text.
    amount_value: float | None = Field(default=None, description="הסכום כמספר, נגזר אוטומטית מ-amount")
    currency: str = Field(default="", description="מטבע, אם צוין בנפרד מ-amount")
    quantity: str | None = Field(default=None, description="כמות, אם צוינה")
    platform: str | None = Field(default=None, description="פלטפורמת הנשא הרלוונטית, אם צוינה")
    confidence: float = Field(default=0.5, ge=0, le=1)
    cites: list[int] = Field(default_factory=list)


class PriceRow(BaseModel):
    """Section 1/6.3: ONLY a figure grounded in a contract award/tender/budget line/FMS notice/
    quoted official -- ``basis_he``/``source_kind`` are what the post-check gate in
    ``eoa.dossier.extract`` inspects to enforce that; no derived per-unit price is ever computed
    here (a "per unit" ``basis_he`` is allowed only when the source itself states it that way)."""

    figure: str = Field(description="הסכום כפי שפורסם, כולל יחידה/סקאלה")
    currency: str = Field(default="", description="מטבע")
    basis_he: str = Field(default="", description="בסיס הסכום כפי שפורסם: ליחידה / למנה של N / לתוכנית כולה")
    date: str | None = Field(default=None, description="תאריך הפרסום/החוזה (ISO אם ידוע)")
    source_kind: SourceKind = "other"
    cites: list[int] = Field(default_factory=list)


class PartnerRow(BaseModel):
    partner: str = Field(description="שם השותף")
    role_he: PartnerRole = "other"
    since: str | None = Field(default=None, description="תאריך תחילת השותפות, אם ידוע")
    cites: list[int] = Field(default_factory=list)


class CompetitorRow(BaseModel):
    """Only a competitor actually named in a cited source or on the configured watchlist -- an
    invented/inferred competitor is dropped by ``eoa.dossier.extract``'s post-checks."""

    product: str = Field(description="שם המוצר המתחרה")
    vendor: str = Field(default="", description="יצרן המוצר המתחרה")
    comparison_he: str = Field(default="", description="השוואה קצרה, מבוססת מקור")
    cites: list[int] = Field(default_factory=list)


class RegulatoryExportBlock(BaseModel):
    export_regime_he: str = Field(default="", description="משטר הייצוא הרלוונטי (ITAR/EAR/DECA וכו')")
    restrictions_he: str = Field(default="", description="הגבלות ידועות")
    cites: list[int] = Field(default_factory=list)


class DossierPatentRow(BaseModel):
    pub_number: str = Field(default="", description="מספר פרסום הפטנט")
    title: str = Field(default="")
    assignee: str = Field(default="")
    relevance_he: str = Field(default="", description="למה הפטנט רלוונטי למוצר זה")
    cites: list[int] = Field(default_factory=list)


class DossierTenderRow(BaseModel):
    tender_id: str | None = Field(default=None)
    title: str = Field(default="")
    status: str = Field(default="")
    relevance_he: str = Field(default="")
    cites: list[int] = Field(default_factory=list)


class ProductDossierOut(BaseModel):
    """Section 3's full record. Every list defaults empty (a topic the research could not establish
    anything about renders as an honest empty table, never a fabricated row) -- see
    ``eoa.dossier.report`` for the "לא נמצא במקורות" placeholder rendering of an empty/null field."""

    identity: IdentityBlock
    summary_he: list[Sentence] = Field(
        default_factory=list,
        max_length=6,
        description="עד 6 משפטים, כל אחד עם cites: מה המוצר ואיפה הוא עומד",
    )
    specifications: list[SpecRow] = Field(default_factory=list)
    #: PD-vocab-extract (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md section 3.3 item 3): the overflow
    #: bucket for a genuine fact that matches NO vocabulary parameter (by key or by any of its
    #: synonyms) -- a real, unanticipated spec is never silently dropped, it lands here instead with
    #: a model-chosen ``parameter_he`` (exactly like ``specifications`` used to work for everything).
    #: Every row here always has ``key == ""`` (enforced by ``eoa.dossier.extract``'s post-check); a
    #: non-trivial, persistently non-empty list here for a well-covered product line is itself a
    #: signal the vocabulary is missing something real (section 9 item 3), not a steady state.
    other_specifications: list[SpecRow] = Field(default_factory=list)
    variants_and_versions: list[VersionRow] = Field(default_factory=list)
    performance: list[PerformanceRow] = Field(default_factory=list)
    maturity: MaturityBlock = Field(default_factory=MaturityBlock)
    deals: list[DealRow] = Field(default_factory=list)
    pricing: list[PriceRow] = Field(default_factory=list)
    partnerships: list[PartnerRow] = Field(default_factory=list)
    competitors: list[CompetitorRow] = Field(default_factory=list)
    regulatory_export: RegulatoryExportBlock = Field(default_factory=RegulatoryExportBlock)
    patents: list[DossierPatentRow] = Field(default_factory=list)
    tenders_and_forecasts: list[DossierTenderRow] = Field(default_factory=list)
    risks_and_gaps_he: list[Sentence] = Field(
        default_factory=list, description="מה לא ידוע / סתירות בין מקורות, כל משפט עם cites"
    )
    what_changed_he: list[Sentence] | None = Field(
        default=None,
        description="מה השתנה לעומת הסקירה הקודמת של אותו product_key; null אם זו הסקירה הראשונה",
    )
    bd_implications_he: list[Sentence] = Field(
        default_factory=list, description="משמעויות לתפקיד BD, מסויגות, כמות לפני משמעות, כל משפט עם cites"
    )


__all__ = [
    "CompetitorRow",
    "DealRow",
    "DossierPatentRow",
    "DossierTenderRow",
    "IdentityBlock",
    "MaturityBlock",
    "PartnerRow",
    "PerformanceRow",
    "PriceRow",
    "ProductDossierOut",
    "RegulatoryExportBlock",
    "SpecRow",
    "VersionRow",
]
