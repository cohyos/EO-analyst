"""Structured LLM schema for the ``tech_daily`` report's second-pass layer classification
(``eoa.report.tech_daily`` -- items the deterministic keyword pass in
``eoa.report.tech_supply_chain.assign_layers_keyword`` left unmatched go through one
``chat_structured_batch`` call using this schema, see ``eoa.llm.prompts.tech_daily.md``).

``relevance`` (coordinator fix 2026-09-17, "אין חדש = אין חדש" strictness): a layer hit is
``core`` only when the item reports an actual development IN that layer's own technology (a new
detector, a new lens, a new scanner/FSM, a new algorithm); ``tangential`` when the layer's
component merely appears inside an unrelated platform/deal story (e.g. a drone WITH a gimbal is
not itself mirrors_scanning news). Only ``core`` hits feed a layer's "מה חדש" drafting --
``eoa.report.tech_daily`` filters on this field before building the drafting prompt."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from eoa.report.tech_supply_chain import layer_keys

Relevance = Literal["core", "tangential"]


class LayerRelevance(BaseModel):
    layer: str = Field(description="מפתח שכבה (layer key) מתוך רשימת השכבות שסופקה")
    relevance: Relevance = Field(
        description=(
            'core = הפריט מדווח על התפתחות ממשית בטכנולוגיית השכבה עצמה; '
            'tangential = רכיב השכבה רק מוזכר בתוך סיפור פלטפורמה/עסקה שאינו עוסק בו ישירות'
        )
    )


class TechLayerAssignment(BaseModel):
    """One item's layer assignment. ``eoa.llm.ollama_client.chat_structured_batch`` adds/strips
    ``item_id`` itself (see its own docstring) -- this schema only carries the per-item payload."""

    layers: list[LayerRelevance] = Field(
        default_factory=list,
        description=(
            "שכבות (layer key + relevance) מתוך רשימת השכבות שסופקה שמתאימות לתוכן הפריט; "
            "רשימה ריקה אם אף שכבה לא רלוונטית -- אסור להמציא מפתח שלא נמצא ברשימה"
        ),
    )

    @field_validator("layers")
    @classmethod
    def _validate_layers(cls, v: list[LayerRelevance]) -> list[LayerRelevance]:
        valid = set(layer_keys())
        # Fail-safe (never raises on a bad model output): an unknown/hallucinated layer key is
        # silently dropped rather than rejecting the whole structured response -- consistent with
        # this codebase's "a config-driven gate degrades, it never crashes the caller" convention
        # (e.g. eoa.product_lines.tagging.tag_product_lines on a bad config).
        return [item for item in v if item.layer in valid]


__all__ = ["LayerRelevance", "Relevance", "TechLayerAssignment"]
