"""Stage: classify + entity extraction (resident or light model, JSON schema output)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog
import yaml

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost, LLMOutputError, ResourceUnavailable
from eoa.execution import checkpoint
from eoa.llm.ollama_client import (
    DATA_GUARD_SYSTEM,
    chat_structured,
    chat_structured_batch,
    is_cloud_batch_mode,
    wrap_data,
)
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import ClassifyOut
from eoa.memory.relational import (
    get_items_for_stage,
    get_items_stuck_unclassified,
    mark_stage,
    update_item_fields,
    upsert_entity,
)
from eoa.pipeline.opportunity_signals import TAG as PLATFORM_OPPORTUNITY_TAG
from eoa.pipeline.opportunity_signals import PlatformOpportunityHint, detect_platform_opportunity

log = structlog.get_logger(__name__)

STAGE = "classify"
MAX_CHARS = 9000
BATCH_SIZE = 25  # U8-6 (Revision 2026-09-06): "classify ... run in batches of up to 25 items"

# ---------------------------------------------------------------------------------------------
# Q3-2 (docs/qa/findings_Q3_r1.md): deterministic "no EO/IR/CV vocabulary" gate. item 117 (an
# AI/deepfake news story with no EO/IR/CV content at all) was classified c_uas/"c_ua_0", score 10,
# red, with zero extracted entities -- there was no floor preventing an in-scope domain when
# nothing in the text, the extracted entities, or the watchlist actually ties it to the domain.
# A curated bilingual term list, independent of the LLM's own judgement, closes that gap.
# ---------------------------------------------------------------------------------------------
_EOIR_KEYWORDS_EN = (
    "eo/ir", "eo-ir", "electro-optic", "electro-optical", "infrared", "thermal imaging",
    "thermal camera", "flir", "targeting pod", "gimbal", "seeker", "laser designator",
    "swir", "lwir", "mwir", "fpa", "focal plane array", "sensor payload", "camera payload",
    "optics", "optronic", "surveillance payload", "counter-uas", "c-uas", "radar-optical",
    "night vision", "imaging sensor", "atr", "computer vision", "image processing",
    "lidar", "dazzler", "microbolometer", "hyperspectral",
)  # fmt: skip
_EOIR_KEYWORDS_HE = (
    "אלקטרו-אופטי", "אלקטרואופטי", "אינפרה אדום", "אינפרה-אדום", "תרמי", "פוד כיוון",
    "ג'ימבל", "ראש ביות", 'מטע"ד', "מטעד", "חיישן", "מצלמת", "אופטיקה", "אופטרוני",
    "מטען תצפית", 'נגד כטב"ם', "לייזר", "ראייה ממוחשבת", "בינה חזותית", "מיקרו-בולומטר",
    "היפרספקטרלי",
    # Round 4b (W21/W24, 2026-09-06): the taxonomy label moved from "פודי כיוון" to the
    # professional "פודי ציון מטרות" (alias "פוד תקיפה"); the old phrase stays because it still
    # occurs in real text. HEL/DEW terms join the list with the new `directed_energy` domain --
    # specific multi-word phrases only ("לייזר" alone was already here); "אנרגיה" or "אלומה" on
    # their own would be false-positive magnets.
    "פודי ציון מטרות", "פוד ציון מטרות", "ציון מטרות", "פוד תקיפה",
    "אנרגיה מכוונת", "מכוון אלומה", "לייזר רב-עוצמה", "לייזר רב עוצמה", "נשק לייזר",
)  # fmt: skip


def _taxonomy_glossary_terms() -> list[str]:
    """English glossary terms (>=4 chars, to avoid short-acronym false positives like "SoC"
    matching inside "social") mined from the parenthesised part of every ``taxonomy.yaml``
    domain/sub-domain label (e.g. "פודי כיוון (Targeting Pods)" -> "targeting pods") --
    supplements the curated list above with whatever the taxonomy itself names."""
    tax = settings().taxonomy.get("domains", {}) or {}
    terms: set[str] = set()
    for d in tax.values():
        labels = [d.get("label", ""), *d.get("sub", {}).values()]
        for label in labels:
            for group in re.findall(r"\(([^)]+)\)", label):
                for part in re.split(r"[,/]", group):
                    part = part.strip().lower()
                    if len(part) >= 4:
                        terms.add(part)
    return sorted(terms)


def _watchlist_names() -> list[str]:
    wl = settings().watchlist
    names: list[str] = []
    for c in wl.get("companies", []) + wl.get("programs", []):
        names.append(c.get("name", ""))
        names.extend(c.get("aliases") or [])
    return [n for n in names if n]


def _word_boundary_pattern(terms: list[str]) -> re.Pattern[str] | None:
    """A single case-insensitive, whole-word-or-phrase regex for ``terms`` -- ``\\b`` around each
    alternative so a short term (e.g. "atr") never false-positives inside an unrelated longer word
    (e.g. "matrix"). Returns ``None`` for an empty list."""
    escaped = [re.escape(t) for t in terms if t]
    if not escaped:
        return None
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


def _has_eoir_vocabulary(text: str) -> bool:
    """True if ``text`` contains any curated or taxonomy-derived EO/IR/CV term (English or
    Hebrew), matched on whole-word/phrase boundaries so a short acronym never fires inside an
    unrelated word."""
    if not text:
        return False
    for pattern in (
        _word_boundary_pattern(list(_EOIR_KEYWORDS_EN)),
        _word_boundary_pattern(list(_EOIR_KEYWORDS_HE)),
        _word_boundary_pattern(_taxonomy_glossary_terms()),
    ):
        if pattern is not None and pattern.search(text):
            return True
    return False


def _watchlist_alias_hit(text: str) -> bool:
    if not text:
        return False
    pattern = _word_boundary_pattern(_watchlist_names())
    return bool(pattern and pattern.search(text))


def apply_no_eoir_gate(item: dict, out: ClassifyOut) -> ClassifyOut:
    """Q3-2(b): if the item shows **zero** EO/IR/CV vocabulary hit, **and** the LLM extracted no
    entities, **and** no watchlist company/program alias appears anywhere in the raw text, force
    ``domain=out_of_scope`` regardless of what the LLM decided -- a watchlist company being merely
    *mentioned* (an engine-nozzle story, a personnel appointment, a macro export statistic, an
    RF/anti-radiation-missile story) must never alone justify an in-scope technical subdomain.
    Mutates and returns ``out``; a no-op when any of the three "in-scope" signals is present."""
    if out.domain == "out_of_scope":
        return out
    if out.entities:
        return out
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    if _has_eoir_vocabulary(text) or _watchlist_alias_hit(text):
        return out
    log.info("classify_gate_no_eoir_vocabulary", item_id=item.get("id"), previous_domain=out.domain)
    out.domain = "out_of_scope"
    out.subdomain = ""
    out.relevance_note = "gate:no_eoir_vocabulary"
    return out


# ---------------------------------------------------------------------------------------------
# Goal 3 (2026-09-06, docs/qa/STATUS.md r3): a 06:27 daily report leaked a generic "AI/hi-tech
# jobs market" opinion piece (Israel Defense Hebrew: "אילו מקצועות יהפכו מבוקשים בעידן ה-AI") into
# a report section under a fabricated "תחומים משיקים (Secondary)" heading, with zero genuine
# EO/IR/CV content. ``apply_no_eoir_gate`` above didn't catch it because the article likely
# mentioned a watchlist company name or extracted a generic entity in passing -- neither of those
# signals is a real defense-technical one when the *only* on-topic-looking vocabulary in the text
# is generic AI/ML/hi-tech-market language (an industry/labor-market trend piece), not an actual
# EO/IR/CV-for-defense term. This second, narrower gate closes that hole.
#
# Calibration note: an early version of this gate fired on *any* generic AI/tech vocabulary hit
# with no curated EO/IR term present, regardless of what the item was actually about. Tested
# against the live DB, that over-triggered on genuine defense-tech company/product news whose
# phrasing doesn't happen to hit the curated EO/IR list (e.g. a funding-round or drone-company
# valuation story that describes its product in AI/robotics terms rather than literal "computer
# vision"/"targeting pod" language) -- exactly the kind of real reporting this pipeline exists to
# track. The actual leaked item, by contrast, was specifically a labor-market/industry-trends
# piece (about professions, hiring, layoffs -- not about any product, company deal, or system at
# all). The gate is therefore scoped to require BOTH signals together: generic AI/tech vocabulary
# AND an explicit labor-market/industry-trend frame -- not either alone.
# ---------------------------------------------------------------------------------------------
_GENERIC_AI_TECH_KEYWORDS_EN = (
    "artificial intelligence", "machine learning", "deep learning", "generative ai",
    "large language model", "llm", "chatgpt", "high-tech", "hi-tech", "high tech",
    "tech industry", "tech sector", "tech jobs", "ai jobs", "ai market", "ai skills",
    "labor market", "job market", "startup ecosystem", "venture capital",
)  # fmt: skip
_GENERIC_AI_TECH_KEYWORDS_HE = (
    "בינה מלאכותית", "למידת מכונה", "למידה עמוקה", "היי-טק", "הייטק", "שוק ההיי-טק",
    "שוק ההייטק", "תעשיית ההייטק", "שוק העבודה", "סטארטאפ", "הון סיכון", "ה-ai",
)  # fmt: skip
# The labor-market/industry-trend "frame" signal -- deliberately NOT including a word like
# "workforce" on its own: tested against the live DB, it false-positived on "autonomous robotic
# workforce" (a real drone/robotics story, not a jobs-market piece).
_AI_MARKET_LABOR_SIGNAL_EN = (
    "job market", "jobs market", "labor market", "hiring", "layoffs",
    "in-demand skills", "in-demand jobs", "career path", "professions",
)  # fmt: skip
_AI_MARKET_LABOR_SIGNAL_HE = (
    "שוק העבודה", "מקצועות מבוקשים", "מקצועות", "משרות", "גיוס עובדים", "פיטורים",
    "כישורים נדרשים", "כוח אדם",
)  # fmt: skip


def _has_generic_ai_tech_market_vocabulary(text: str) -> bool:
    """True if ``text`` contains generic AI/ML/hi-tech-*market* vocabulary (jobs, skills, industry
    trends) -- calibration companion to :func:`_has_eoir_vocabulary`: a piece about the AI jobs
    market or the broad hi-tech sector is not, by itself, defense EO/IR/CV content, even though a
    shallow keyword match on bare "AI" might otherwise treat it as in-scope computer-vision
    signal."""
    if not text:
        return False
    pattern = _word_boundary_pattern(list(_GENERIC_AI_TECH_KEYWORDS_EN) + list(_GENERIC_AI_TECH_KEYWORDS_HE))
    return bool(pattern and pattern.search(text))


def _has_ai_market_labor_signal(text: str) -> bool:
    """True if ``text`` explicitly frames itself as being about the labor market/hiring/professions
    -- see the calibration note above: this is what actually distinguishes an off-topic "AI jobs
    market" piece from a genuine defense-tech company/product story that merely uses AI/ML/hi-tech
    vocabulary to describe its product."""
    if not text:
        return False
    pattern = _word_boundary_pattern(list(_AI_MARKET_LABOR_SIGNAL_EN) + list(_AI_MARKET_LABOR_SIGNAL_HE))
    return bool(pattern and pattern.search(text))


def apply_generic_ai_market_gate(item: dict, out: ClassifyOut) -> ClassifyOut:
    """Goal 3: an item classified in-scope (any domain other than ``out_of_scope``) whose text
    contains BOTH generic AI/ML/hi-tech-market vocabulary AND an explicit labor-market/industry-
    trend frame (jobs, hiring, layoffs, professions -- see the calibration note above), but **no**
    genuine EO/IR/CV-for-defense term anywhere (title + clean_text), is forced to ``out_of_scope``
    -- regardless of extracted entities or a watchlist alias hit, unlike :func:`apply_no_eoir_gate`
    above, since a defense company can legitimately appear in a generic labor-market/industry-
    trends listicle with zero EO/IR content. A no-op when the item already carries a genuine
    EO/IR/CV term anywhere (real in-scope signal no matter what other vocabulary also appears), or
    already out_of_scope."""
    if out.domain == "out_of_scope":
        return out
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    if _has_eoir_vocabulary(text):
        return out
    if not (_has_generic_ai_tech_market_vocabulary(text) and _has_ai_market_labor_signal(text)):
        return out
    log.info("classify_gate_generic_ai_market", item_id=item.get("id"), previous_domain=out.domain)
    out.domain = "out_of_scope"
    out.subdomain = ""
    out.relevance_note = "gate:generic_ai_market_vocabulary"
    return out


# ---------------------------------------------------------------------------------------------
# CR-platform-opportunity (2026-09-16, docs/qa/content_review/CR-platform-opportunity.md): the
# mirror-image gate to the two above -- those two only ever MOVE an item TOWARD out_of_scope; this
# one is the one deterministic override in this module that can move an item BACK from
# out_of_scope, when the platform-vs-EO/IR rule in classify.md (the "כלל פלטפורמה מול מטע\"ד" rule)
# archived a platform-integration story that eoa.pipeline.opportunity_signals.
# detect_platform_opportunity independently confirms states/implies an open external EO/IR/sensor/
# pod slot on a named platform -- the live miss this closes: items 22760/23252 (Anduril's YFQ-44A
# Fury CCA "fit checked" with a targeting-pod integration plan), archived by the model as pure
# platform-weapons stories with "no substantive EO/IR/CV payload detail", which is a defensible
# read of the source text's OWN technical content but misses that the story is itself a genuine
# business-development signal regardless of technical depth.
#
# Runs LAST (after both gates above) precisely because it is the one allowed to reverse an
# out_of_scope verdict -- apply_no_eoir_gate/apply_generic_ai_market_gate may have just set
# out_of_scope on this same call; detect_platform_opportunity's own two-signal requirement (a named
# platform AND an opportunity term, see that module's docstring) is what keeps this override
# narrow, not "runs after the other gates".
# ---------------------------------------------------------------------------------------------
def apply_platform_opportunity_gate(item: dict, out: ClassifyOut) -> ClassifyOut:
    """When :func:`eoa.pipeline.opportunity_signals.detect_platform_opportunity` finds a hit on
    this item's title+clean_text, tag it ``platform_integration_opportunity`` and ensure
    ``business`` is one of its ``dimensions`` -- regardless of what domain the model (or the two
    gates above) landed on. If the model's own domain is ``out_of_scope``, additionally force it
    in-scope: domain/subdomain set to the matched product line's own primary taxonomy subdomain
    (``airborne_pods.<sub>`` for every currently-configured line with ``platforms``/
    ``opportunity_signals``), so the item proceeds to ``triage``/``analyze``/product-line tagging
    instead of being archived before it ever reaches them (see
    ``eoa.memory.relational._ANALYZE_STAGE_SCOPE_FILTER``). An item already in scope keeps the
    model's own domain/subdomain untouched -- only the tag/dimension are added. A no-op (returns
    ``out`` unchanged) when there is no hint at all."""
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    hint = detect_platform_opportunity(item.get("title"), text)
    if hint is None:
        return out
    was_out_of_scope = out.domain == "out_of_scope"
    if was_out_of_scope:
        from eoa.product_lines.registry import get_product_line

        primary_line = get_product_line(hint.line_ids[0])
        if primary_line is not None and primary_line.subdomains:
            domain, _, subdomain = primary_line.subdomains[0].partition(".")
            out.domain = domain  # type: ignore[assignment]
            out.subdomain = subdomain
        else:
            out.domain = "airborne_pods"  # type: ignore[assignment]
            out.subdomain = ""
        note = f"platform_integration_opportunity: {', '.join(hint.platforms)} x {', '.join(hint.signals)}"
        out.relevance_note = note[:200]
    if "business" not in out.dimensions:
        out.dimensions = [*out.dimensions, "business"]
    if PLATFORM_OPPORTUNITY_TAG not in out.tags:
        # ClassifyOut.tags is max_length=8 -- truncate the EXISTING tags, not the newly-added one,
        # so this business-opportunity tag is never itself the casualty of a full tag list.
        out.tags = [*out.tags[:7], PLATFORM_OPPORTUNITY_TAG]
    log.info(
        "classify_gate_platform_opportunity",
        item_id=item.get("id"),
        was_out_of_scope=was_out_of_scope,
        line_ids=hint.line_ids,
        platforms=hint.platforms,
        signals=hint.signals,
    )
    return out


def _opportunity_hint_for_prompt(item: dict) -> PlatformOpportunityHint | None:
    return detect_platform_opportunity(item.get("title"), item.get("clean_text"))


@dataclass
class ClassifyStats:
    done: int = 0
    out_of_scope: int = 0
    failed: int = 0


def _taxonomy_text() -> str:
    tax = settings().taxonomy.get("domains", {})
    lines = []
    for key, d in tax.items():
        subs = ", ".join(f"{k}" for k in d.get("sub", {}))
        lines.append(f"- {key}: {d.get('label', '')} → [{subs}]")
    return "\n".join(lines)


def _system() -> str:
    return render("system_analyst", data_guard=DATA_GUARD_SYSTEM)


def _classify_prompt(item: dict) -> str:
    hint = _opportunity_hint_for_prompt(item)
    return render(
        "classify",
        taxonomy=_taxonomy_text(),
        title=item.get("title") or "",
        source=item.get("source_name") or item.get("url") or "",
        published_at=item.get("published_at") or "לא ידוע",
        lang=item.get("lang") or "?",
        opportunity_hint=hint.prompt_text_he() if hint is not None else "אין",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )


def classify_item(item: dict, *, role: str = "resident", interactive: bool = False) -> ClassifyOut:
    """Classify one ``items`` row (does not persist)."""
    return chat_structured(
        role,
        ClassifyOut,
        [
            {"role": "system", "content": _system()},
            {"role": "user", "content": _classify_prompt(item)},
        ],
        task="classify",
        interactive=interactive,
    )


def classify_batch(items: list[dict], *, role: str = "resident") -> dict[int, ClassifyOut]:
    """U8-6 batch mode (Revision 2026-09-06): classify up to ``BATCH_SIZE`` items in one cloud
    call instead of one call per item; returns ``{item_id: ClassifyOut}``. Only used by
    ``run_classify`` when ``is_cloud_batch_mode()`` is true -- the local/default path is
    ``classify_item`` above, unchanged."""
    prompts = [(it["id"], _classify_prompt(it)) for it in items]
    return chat_structured_batch(role, ClassifyOut, prompts, system=_system(), task="classify")


def persist_classification(item: dict, out: ClassifyOut) -> None:
    """Write classification fields + entities to the DB."""
    item_id = item["id"]
    names = []
    for ent in out.entities:
        try:
            entity_id = upsert_entity(name=ent.name, kind=ent.kind, first_seen_item=item_id)
            if entity_id is not None:  # Q3-13: None means rejected (technique-like name), not stored
                # Q3-13 r4 (root-cause investigation, 2026-09-06): store the CANONICAL name here,
                # not the raw as-extracted one -- upsert_entity resolves ent.name/ent.kind through
                # entity_normalize.canonical_name_and_kind internally before writing the entities
                # row, so a raw alias/Hebrew-country-name (e.g. "ישראל") previously ended up in
                # items.entities_mentioned even though the row itself landed under "Israel". That
                # mismatch is exactly what breaks anything doing an exact `entities.name = ...`
                # lookup against entities_mentioned (e.g. israel_focus.score_and_persist_entity_
                # israeli), so this mirrors upsert_entity's own resolution here.
                from eoa.pipeline.entity_normalize import canonical_name_and_kind

                canonical_name, _ = canonical_name_and_kind(ent.name, ent.kind)
                names.append(canonical_name)
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.debug("entity_upsert_failed", name=ent.name, error=str(exc)[:120])

    # --- A13 (מיקוד תעשייה ישראלית, 2026-09-06) -- BEGIN ------------------------------------
    # Deterministic (no LLM) Israeli-industry relevance score, computed from this item's own
    # text + the entities just resolved above (before the watchlist-alias backfill that
    # eoa.pipeline.analyze may still add later -- analyze.py's own "# --- A13" block refreshes
    # this score once that backfill has landed, so a classify-stage miss self-heals downstream).
    # See eoa.pipeline.israel_focus module docstring for the four scored signals.
    israel_score: dict = {"score": 0.0, "reasons": []}
    try:
        from eoa.pipeline.israel_focus import israel_relevance, score_and_persist_entity_israeli

        text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
        israel_score = israel_relevance(text, names, lang=item.get("lang"), geography=out.geography)
        for name in names:
            score_and_persist_entity_israeli(name)
    except (DeadlineExceeded, LeaseLost):
        raise
    except Exception as exc:
        log.debug("israel_relevance_scoring_failed", item_id=item_id, error=str(exc)[:120])
    # --- A13 -- END --------------------------------------------------------------------------

    extra_fields: dict = {}
    # CR-platform-opportunity (2026-09-16): the deterministic pre-check's matched product line(s)
    # are written straight to `items.product_lines` here, at classify time -- not left to wait for
    # eoa.pipeline.analyze's own tag_product_lines (which only runs at the LATER analyze stage, and
    # matches against the LLM-authored summary_he/so_what_he text, which may not repeat the exact
    # configured keyword) -- see apply_platform_opportunity_gate's docstring: "tag the matching
    # product line(s) even when the LLM is conservative" means this must not depend on the analyze
    # stage's own text match succeeding. eoa.pipeline.analyze._merge_product_lines (analyze.py)
    # unions with, rather than overwrites, whatever is written here.
    if PLATFORM_OPPORTUNITY_TAG in out.tags:
        hint = detect_platform_opportunity(item.get("title"), item.get("clean_text"))
        if hint is not None:
            extra_fields["product_lines"] = list(hint.line_ids)

    update_item_fields(
        item_id,
        domain=out.domain,
        subdomain=out.subdomain or None,
        dimensions=list(out.dimensions),
        tags=list(out.tags),
        report_kind=out.report_kind,
        trl=out.trl,
        geography=out.geography,
        entities_mentioned=names,
        summary_he=out.one_line_he,
        israel_relevance=israel_score["score"],
        israel_reasons=israel_score["reasons"],
        **extra_fields,
    )


def run_classify(
    limit: int = 300, role: str = "resident", *, item_ids: list[int] | None = None
) -> ClassifyStats:
    """Classify all items that passed dedup and are not duplicates or quarantined.

    F22: ``item_ids`` (optional, additive) scopes this run to just those ids -- see
    ``eoa.memory.relational.get_items_for_stage``."""
    checkpoint()
    stats = ClassifyStats()
    items = get_items_for_stage(STAGE, limit, item_ids=item_ids)
    if item_ids is None:
        # Round-3 D1 self-heal: items whose classify stage is "done" but domain is NULL never
        # reach triage (it skips domain NULL) and never come back here (stage done) -- append
        # them so a persist failure is retried instead of stranding the item forever.
        seen = {it["id"] for it in items}
        try:
            stuck = [it for it in get_items_stuck_unclassified(limit) if it["id"] not in seen]
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:  # a maintenance tail must never abort the main batch
            log.warning("classify_stuck_lookup_failed", error=str(exc)[:160])
            stuck = []
        if stuck:
            log.info("classify_reclassifying_stuck_items", n=len(stuck), ids=[it["id"] for it in stuck][:20])
            items = [*items, *stuck][:limit]
    eligible: list[dict] = []
    for it in items:
        checkpoint()
        if it.get("security_status") in ("quarantined", "blocked") or it.get("dedup_of"):
            mark_stage(it["id"], STAGE)
            continue
        eligible.append(it)

    # --- U8-6 batch mode (Revision 2026-09-06): cloud mode classifies BATCH_SIZE items per call.
    # Persistence/side-effects below are identical to the per-item path; only how ClassifyOut is
    # obtained differs. Local mode (default) never enters this branch. -----------------------
    if is_cloud_batch_mode():
        batch_size = min(BATCH_SIZE, settings().llm_providers.cloud_batch_size)
        for i in range(0, len(eligible), batch_size):
            checkpoint()
            chunk = eligible[i : i + batch_size]
            try:
                results = classify_batch(chunk, role=role)
            except ResourceUnavailable:
                log.warning("classify_batch_deferred_resources", n=len(chunk))
                raise
            except LLMOutputError as exc:
                log.error("classify_batch_bad_output", n=len(chunk), error=str(exc)[:200])
                stats.failed += len(chunk)
                continue
            for it in chunk:
                checkpoint()
                out = results.get(it["id"])
                if out is None:
                    log.error("classify_batch_missing_item", item_id=it["id"])
                    stats.failed += 1
                    continue
                try:
                    out = apply_no_eoir_gate(it, out)
                    out = apply_generic_ai_market_gate(it, out)
                    out = apply_platform_opportunity_gate(it, out)
                    persist_classification(it, out)
                    if out.domain == "out_of_scope":
                        update_item_fields(
                            it["id"], level="archive", score=1, triage_reason=out.relevance_note[:400]
                        )
                        stats.out_of_scope += 1
                    mark_stage(it["id"], STAGE)
                    stats.done += 1
                except (DeadlineExceeded, LeaseLost):
                    raise
                except Exception as exc:
                    log.error("classify_persist_failed", item_id=it["id"], error=str(exc)[:200])
                    stats.failed += 1
        log.info("classify_done", **stats.__dict__)
        return stats
    # --- end U8-6 batch mode -------------------------------------------------------------------

    for it in eligible:
        checkpoint()
        try:
            out = classify_item(it, role=role)
            out = apply_no_eoir_gate(it, out)
            out = apply_generic_ai_market_gate(it, out)
            out = apply_platform_opportunity_gate(it, out)
            persist_classification(it, out)
            if out.domain == "out_of_scope":
                update_item_fields(it["id"], level="archive", score=1, triage_reason=out.relevance_note[:400])
                stats.out_of_scope += 1
            mark_stage(it["id"], STAGE)
            stats.done += 1
        except ResourceUnavailable:
            log.warning("classify_deferred_resources", item_id=it["id"])
            raise
        except LLMOutputError as exc:
            log.error("classify_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.error("classify_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("classify_done", **stats.__dict__)
    return stats


def taxonomy_yaml() -> str:
    """Full taxonomy as YAML (for the UI settings screen)."""
    return yaml.safe_dump(settings().taxonomy, allow_unicode=True, sort_keys=False)
