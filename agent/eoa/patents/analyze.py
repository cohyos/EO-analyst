"""Stage: patent analysis (A14) -- LLM claims summary/subdomain/so-what, plus the deterministic
Israel-relevance signal and assignee->entity linking.

Mirrors ``eoa.tenders.scan``'s LLM-classification shape: a small, capped LLM call per record
(``chat_structured`` against the ``resident``/``light`` role, per docs/CONVENTIONS.md's "small
limits" instruction for survey/claim summaries), best-effort -- a failed/unavailable call never
blocks the scan pipeline; the row simply stays unanalyzed until the next run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from eoa.config import settings
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.patents import PatentAdvanceOut, PatentClaimsOut
from eoa.memory.relational import upsert_entity
from eoa.pipeline.entity_normalize import resolve_canonical, resolve_company_country

log = structlog.get_logger(__name__)

# A14b point 4 (2026-09-06): the per-patent "advance" line/footnote is intentionally cheap --
# generated (when it can't just be derived from an existing claims_summary_he, see
# generate_advance_descriptions's own docstring) against the light role with a small predict cap,
# never the full resident-role analysis pass this module otherwise uses.
_ADVANCE_NUM_PREDICT = 220
_NO_ADVANCE_HE = "תיאור התקדמות לא זמין (הפטנט טרם נותח והמודל המקומי אינו נגיש כרגע)."


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


# --------------------------------------------------------------------------
# per-patent "advance" description (A14b point 4, 2026-09-06): "לכל פטנט מצוטט: תיאור קצר (2-3
# משפטים בעברית) של ההתקדמות המתוארת -- מה הבעיה, מה הפתרון, מה חדש". Reused for the survey's
# appendix "התקדמות" column and its md/html footnote-style first-citation line
# (eoa.patents.render.inject_advance_footnotes_md/html).
# --------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def derive_advance_from_claims(claims_summary_he: str, *, max_sentences: int = 3) -> str:
    """A patent that already has a ``claims_summary_he`` (``eoa.patents.analyze.analyze_patents``'s
    own 3-5-sentence legal-protection summary) never needs a fresh LLM call for its shorter
    2-3-sentence "advance" line -- this just takes its first ``max_sentences`` sentences verbatim
    (no re-synthesis, so nothing new can be invented here)."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(claims_summary_he.strip()) if s.strip()]
    return " ".join(sentences[:max_sentences]) or claims_summary_he.strip()


def _advance_prompt(row: dict[str, Any]) -> str:
    return render(
        "patent_advance",
        pub_number=row.get("pub_number") or "",
        assignees=", ".join(row.get("assignees") or []) or "לא ידוע",
        data=wrap_data(
            f"{row.get('title') or ''}\n\n{row.get('abstract') or ''}",
            row.get("pub_number") or "",
            row.get("url") or "",
        ),
    )


def generate_advance_descriptions(
    rows: list[dict[str, Any]], *, role: str = "light", interactive: bool = False, llm_limit: int = 15
) -> dict[Any, str]:
    """``{patent_id: advance_he}`` for every row in ``rows`` (each carrying at least ``id``;
    ``claims_summary_he``/``title``/``abstract``/``assignees``/``pub_number``/``url`` used when
    present). A row that already has ``claims_summary_he`` gets :func:`derive_advance_from_claims`
    (free, deterministic, no LLM call). A row still missing it gets one small, capped LLM call
    against ``role`` (``"light"`` by default -- this is meant to be cheap and bulk, not the full
    per-patent analysis pass) -- but only for the first ``llm_limit`` such rows, to bound a large
    survey's LLM cost; any row beyond that limit (or whose call fails/is deferred) gets the honest
    :data:`_NO_ADVANCE_HE` placeholder rather than inventing a description (docs/CONVENTIONS.md
    rule 5)."""
    out: dict[Any, str] = {}
    llm_calls_made = 0
    for row in rows:
        claims = row.get("claims_summary_he")
        if claims:
            out[row["id"]] = derive_advance_from_claims(claims)
            continue
        if llm_calls_made >= llm_limit:
            out[row["id"]] = _NO_ADVANCE_HE
            continue
        llm_calls_made += 1
        try:
            result = chat_structured(
                role,
                PatentAdvanceOut,
                [
                    {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                    {"role": "user", "content": _advance_prompt(row)},
                ],
                task="summarize",
                interactive=interactive,
                options={"temperature": 0.2, "num_predict": _ADVANCE_NUM_PREDICT},
            )
            out[row["id"]] = result.advance_he
        except ResourceUnavailable:
            out[row["id"]] = _NO_ADVANCE_HE
        except LLMOutputError as exc:
            log.warning("patents_advance_llm_failed", pub_number=row.get("pub_number"), error=str(exc)[:200])
            out[row["id"]] = _NO_ADVANCE_HE
        except Exception as exc:
            log.warning(
                "patents_advance_unexpected_error", pub_number=row.get("pub_number"), error=str(exc)[:200]
            )
            out[row["id"]] = _NO_ADVANCE_HE
    return out
