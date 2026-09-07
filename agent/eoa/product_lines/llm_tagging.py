"""LLM-assisted product-line tagging fallback (R8-tagging, 2026-09-07).

The deterministic tagger (:mod:`eoa.product_lines.tagging`) is precise but necessarily incomplete
-- a genuinely relevant item that never uses one of the configured keyword strings verbatim (a
paraphrase, a translated headline, a subdomain the classifier missed) gets no tag at all. This
module is the fallback for exactly that gap: one structured light-role call per batch of ~15
in-scope items with no deterministic tag, asking the model to pick zero or more product lines from
the closed six-id catalog for each item's title/summary alone.

Gated by ``config/product_lines.yaml``'s top-level ``llm_tagging`` key
(:func:`eoa.product_lines.registry.llm_tagging_enabled`) -- callers must check that themselves
before calling :func:`llm_tag_batch` (this module doesn't check it internally, so a caller that
wants to force a run for testing/debugging still can). Two call sites:

- ``eoa.pipeline.analyze``'s post-analysis hook -- one item at a time (a batch of 1 is still a
  valid ``chat_structured_batch`` call), only when the deterministic pass found nothing.
- ``scripts/backfill_product_lines.py --llm`` -- real batches of ~15 over every in-scope item still
  untagged after its own deterministic sweep.

Never raises: any LLM/schema/network failure degrades to ``{}`` (no tags), the same fail-open
convention ``eoa.product_lines.tagging.tag_product_lines`` documents for a bad config file --
a missed LLM-assisted tag is a normal, honest "we don't know" outcome, not a pipeline-breaking
error.
"""

from __future__ import annotations

import structlog

from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured_batch, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.product_line import ProductLineTagResult
from eoa.product_lines.registry import product_line_defs

log = structlog.get_logger(__name__)

#: Only a per-item result at or above this confidence is accepted -- a lower-confidence guess is
#: treated the same as "no tag" (see the module docstring).
LLM_TAG_MIN_CONFIDENCE = 0.6

#: Batch size the two call sites chunk their own item lists to before calling
#: :func:`llm_tag_batch` -- "batch of ~15" per the R8-tagging brief.
BATCH_SIZE = 15

MAX_CHARS = 800  # title + summary only -- this is a short classification call, not a full analyze


def _system() -> str:
    return render("system_analyst", data_guard=DATA_GUARD_SYSTEM)


def _catalog_text() -> str:
    """One line per configured product line: id, both display names, and a handful of its own
    keywords/exemplar systems as disambiguating context -- built fresh on every call (not cached at
    import time) so a config edit is picked up without a process restart, same convention as
    ``eoa.product_lines.registry.product_line_defs`` itself."""
    lines = []
    for pl in product_line_defs():
        hints = list(pl.keywords_en[:2]) + list(pl.exemplar_systems[:3])
        hint_text = ", ".join(hints) if hints else "—"
        lines.append(f'- id="{pl.id}" -- {pl.name_he} / {pl.name_en} (למשל: {hint_text})')
    return "\n".join(lines)


def _valid_ids() -> set[str]:
    return {pl.id for pl in product_line_defs()}


def _item_prompt(item: dict) -> str:
    title = item.get("title") or ""
    summary = item.get("summary_he") or item.get("summary") or ""
    return render(
        "product_line_tagging",
        product_lines_catalog=_catalog_text(),
        title=wrap_data(title[:MAX_CHARS], item.get("id"), item.get("url") or ""),
        summary=(summary or "")[:MAX_CHARS],
    )


def llm_tag_batch(items: list[dict], *, role: str = "light") -> dict[int, list[str]]:
    """``items`` is a list of dicts each with at least ``id`` and ``title``/``summary_he`` (or
    ``summary``) -- returns ``{item_id: [line_ids]}`` for every item that got a
    ``confidence >= LLM_TAG_MIN_CONFIDENCE`` result with at least one valid line id. An item that
    fails validation, comes back low-confidence, empty, or is simply absent from the model's
    response (``chat_structured_batch``'s own convention) is silently omitted from the returned
    dict -- never a KeyError for the caller to guard against.

    Never raises -- any failure (LLM chain exhausted, schema validation failure after
    ``chat_structured``'s own one retry, etc.) logs and returns ``{}`` for the whole batch, so one
    bad batch never aborts a caller's larger loop over many batches.
    """
    if not items:
        return {}
    valid_ids = _valid_ids()
    if not valid_ids:
        return {}
    prompts = [(it["id"], _item_prompt(it)) for it in items if it.get("id") is not None]
    if not prompts:
        return {}
    try:
        raw = chat_structured_batch(
            role, ProductLineTagResult, prompts, system=_system(), task="product_line_tagging"
        )
    except Exception as exc:
        log.warning("product_lines_llm_tag_batch_failed", n_items=len(prompts), error=str(exc)[:200])
        return {}

    result: dict[int, list[str]] = {}
    for item_id, tag in raw.items():
        if tag.confidence < LLM_TAG_MIN_CONFIDENCE:
            continue
        line_ids = [lid for lid in tag.line_ids if lid in valid_ids]
        if line_ids:
            result[item_id] = line_ids
    log.info(
        "product_lines_llm_tagged",
        n_items=len(prompts),
        n_tagged=len(result),
        n_no_result=len(prompts) - len(raw),
    )
    return result
