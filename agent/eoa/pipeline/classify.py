"""Stage: classify + entity extraction (resident or light model, JSON schema output)."""

from __future__ import annotations

from dataclasses import dataclass

import structlog
import yaml

from eoa.config import settings
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import ClassifyOut
from eoa.memory.relational import get_items_for_stage, mark_stage, update_item_fields, upsert_entity

log = structlog.get_logger(__name__)

STAGE = "classify"
MAX_CHARS = 9000


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


def classify_item(item: dict, *, role: str = "resident", interactive: bool = False) -> ClassifyOut:
    """Classify one ``items`` row (does not persist)."""
    prompt = render(
        "classify",
        taxonomy=_taxonomy_text(),
        title=item.get("title") or "",
        source=item.get("source_name") or item.get("url") or "",
        published_at=item.get("published_at") or "לא ידוע",
        lang=item.get("lang") or "?",
        data=wrap_data((item.get("clean_text") or "")[:MAX_CHARS], item["id"], item.get("url") or ""),
    )
    return chat_structured(
        role,
        ClassifyOut,
        [
            {"role": "system", "content": _system()},
            {"role": "user", "content": prompt},
        ],
        task="classify",
        interactive=interactive,
    )


def persist_classification(item_id: int, out: ClassifyOut) -> None:
    """Write classification fields + entities to the DB."""
    names = []
    for ent in out.entities:
        try:
            upsert_entity(name=ent.name, kind=ent.kind, first_seen_item=item_id)
            names.append(ent.name)
        except Exception as exc:
            log.debug("entity_upsert_failed", name=ent.name, error=str(exc)[:120])
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
    )


def run_classify(limit: int = 300, role: str = "resident") -> ClassifyStats:
    """Classify all items that passed dedup and are not duplicates or quarantined."""
    stats = ClassifyStats()
    items = get_items_for_stage(STAGE, limit)
    for it in items:
        if it.get("security_status") == "quarantined" or it.get("dedup_of"):
            mark_stage(it["id"], STAGE)
            continue
        try:
            out = classify_item(it, role=role)
            persist_classification(it["id"], out)
            if out.domain == "out_of_scope":
                update_item_fields(it["id"], level="archive", score=1, triage_reason=out.relevance_note[:400])
                stats.out_of_scope += 1
            mark_stage(it["id"], STAGE)
            stats.done += 1
        except ResourceUnavailable:
            log.warning("classify_deferred_resources", item_id=it["id"])
            break
        except LLMOutputError as exc:
            log.error("classify_bad_output", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
        except Exception as exc:
            log.error("classify_failed", item_id=it["id"], error=str(exc)[:200])
            stats.failed += 1
    log.info("classify_done", **stats.__dict__)
    return stats


def taxonomy_yaml() -> str:
    """Full taxonomy as YAML (for the UI settings screen)."""
    return yaml.safe_dump(settings().taxonomy, allow_unicode=True, sort_keys=False)
