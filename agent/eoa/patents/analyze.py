"""Stage: patent analysis (A14) -- LLM claims summary/subdomain/so-what, plus the deterministic
Israel-relevance signal and assignee->entity linking.

Mirrors ``eoa.tenders.scan``'s LLM-classification shape: a small, capped LLM call per record
(``chat_structured`` against the ``resident``/``light`` role, per docs/CONVENTIONS.md's "small
limits" instruction for survey/claim summaries), best-effort -- a failed/unavailable call never
blocks the scan pipeline; the row simply stays unanalyzed until the next run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from eoa.config import settings
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.patents import PatentClaimsOut
from eoa.memory.relational import upsert_entity
from eoa.pipeline.entity_normalize import resolve_canonical, resolve_company_country

log = structlog.get_logger(__name__)


def _tech_dev_subdomain_keys() -> list[str]:
    taxonomy = settings().taxonomy or {}
    sub = (((taxonomy.get("domains") or {}).get("tech_dev") or {}).get("sub")) or {}
    return list(sub.keys())


def _israel_relevance(title: str, abstract: str, assignees: list[str], jurisdictions: list[str]) -> float:
    """Deterministic Israel-relevance for one patent, reusing ``eoa.pipeline.israel_focus`` when
    available (guarded import -- IL1 may still be landing that module in a concurrently-edited
    checkout); falls back to a bare assignee-country check on ``resolve_company_country``/the
    watchlist when the helper module is missing."""
    text = f"{title}\n{abstract}"
    geography = jurisdictions[0] if jurisdictions else None
    try:
        from eoa.pipeline.israel_focus import israel_relevance as _score

        return float(_score(text, assignees, geography=geography).get("score") or 0.0)
    except ImportError:
        pass
    for name in assignees:
        canonical = resolve_canonical(name)
        if canonical and (canonical.get("country") or "").upper() == "IL":
            return 0.8
        if (resolve_company_country(name) or "").upper() == "IL":
            return 0.6
    return 0.0


def _resolve_entity_ids(assignees: list[str]) -> list[int]:
    """Assignee -> ``entities.id`` linking (per the A14 spec): a watchlist-known or Israeli
    assignee gets/creates a real entity row (:func:`eoa.memory.relational.upsert_entity`, which
    itself normalizes name/kind/country via ``eoa.pipeline.entity_normalize``); anything else is
    only *linked* to an entity row that already exists under that exact/case-insensitive name --
    never created just because a patent happens to name it."""
    ids: list[int] = []
    for name in assignees:
        if not name or not name.strip():
            continue
        canonical = resolve_canonical(name)
        # An assignee is always a company -- never accept a watchlist "program" or a curated
        # org/country match here (e.g. "NATO", "Europe") just because the name happens to appear
        # in the alias table for unrelated report-entity-extraction purposes.
        canonical_company = canonical if canonical and canonical.get("kind") == "company" else None
        is_israeli = (
            bool(canonical_company and (canonical_company.get("country") or "").upper() == "IL")
            or (resolve_company_country(name) or "").upper() == "IL"
        )
        if canonical_company or is_israeli:
            entity_id = upsert_entity(name=name, kind="company")
            if entity_id is not None:
                ids.append(entity_id)
            continue
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM entities WHERE lower(name) = lower(%s) LIMIT 1", (name,))
            row = cur.fetchone()
        if row:
            ids.append(row["id"])
    return ids


@dataclass
class PatentAnalyzeStats:
    analyzed: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0


def _rows_missing_analysis(limit: int) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pub_number, title, abstract, assignees, jurisdictions, url FROM patents "
            "WHERE claims_summary_he IS NULL ORDER BY id LIMIT %(limit)s",
            {"limit": limit},
        )
        return cur.fetchall()


def _persist_analysis(
    patent_id: int, out: PatentClaimsOut, israel_relevance: float, entity_ids: list[int]
) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE patents SET
                claims_summary_he = %(claims_summary_he)s,
                subdomain = NULLIF(%(subdomain)s, ''),
                so_what_he = %(so_what_he)s,
                israel_relevance = %(israel_relevance)s,
                entity_ids = %(entity_ids)s
            WHERE id = %(id)s
            """,
            {
                "claims_summary_he": out.claims_summary_he,
                "subdomain": out.subdomain,
                "so_what_he": out.so_what_he,
                "israel_relevance": israel_relevance,
                "entity_ids": entity_ids or None,
                "id": patent_id,
            },
        )


def analyze_patents(
    limit: int = 10, *, role: str = "resident", interactive: bool = False
) -> PatentAnalyzeStats:
    """A14 step 3: analyze up to ``limit`` patents still missing ``claims_summary_he``. A single
    row's LLM failure never blocks the others (docs/CONVENTIONS.md rule 9)."""
    stats = PatentAnalyzeStats()
    subdomain_keys = _tech_dev_subdomain_keys()

    for row in _rows_missing_analysis(limit):
        assignees = row.get("assignees") or []
        prompt = render(
            "patent_claims",
            pub_number=row["pub_number"],
            assignees=", ".join(assignees) or "לא ידוע",
            subdomain_keys=", ".join(subdomain_keys),
            data=wrap_data(
                f"{row.get('title') or ''}\n\n{row.get('abstract') or ''}",
                row["pub_number"],
                row.get("url") or "",
            ),
        )
        try:
            out = chat_structured(
                role,
                PatentClaimsOut,
                [
                    {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                    {"role": "user", "content": prompt},
                ],
                task="summarize",
                interactive=interactive,
            )
        except ResourceUnavailable:
            stats.llm_deferred += 1
            continue
        except LLMOutputError as exc:
            log.warning("patents_analyze_llm_failed", pub_number=row["pub_number"], error=str(exc)[:200])
            stats.llm_failed += 1
            continue
        except Exception as exc:
            log.warning(
                "patents_analyze_unexpected_error", pub_number=row["pub_number"], error=str(exc)[:200]
            )
            stats.llm_failed += 1
            continue

        if out.subdomain and out.subdomain not in subdomain_keys:
            out.subdomain = ""  # never store an invented subdomain key
        relevance = _israel_relevance(
            row.get("title") or "", row.get("abstract") or "", assignees, row.get("jurisdictions") or []
        )
        entity_ids = _resolve_entity_ids(assignees)
        try:
            _persist_analysis(row["id"], out, relevance, entity_ids)
            stats.analyzed += 1
        except Exception as exc:
            log.warning("patents_analyze_persist_failed", patent_id=row["id"], error=str(exc)[:200])

    log.info("patents_analyze_done", **vars(stats))
    return stats
