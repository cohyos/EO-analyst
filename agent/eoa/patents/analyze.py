"""Stage: patent analysis (A14) -- LLM claims summary/subdomain/so-what, plus the deterministic
Israel-relevance signal and assignee->entity linking.

Mirrors ``eoa.tenders.scan``'s LLM-classification shape: a small, capped LLM call per record
(``chat_structured`` against the ``resident``/``light`` role, per docs/CONVENTIONS.md's "small
limits" instruction for survey/claim summaries), best-effort -- a failed/unavailable call never
blocks the scan pipeline; the row simply stays unanalyzed until the next run.

Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md / CR-factcheck.md): a confirmed content
defect -- US10506436B1 ("Lattice mesh", a real Anduril patent) had a stored ``abstract`` that is
literally a USPTO *assignment-transfer notice* ("2019-03-07 Assigned to Anduril Industries
Inc...") with zero technical content, yet the LLM-generated ``claims_summary_he``/``so_what_he``
described a fully invented optical lens/mirror/fiber array. Two independent guards now stand
between a thin/absent source text and a fabricated-sounding Hebrew description:

1. :func:`_has_sufficient_technical_text` -- a *pre-call* gate. A record whose ``abstract`` (+ any
   ``raw`` claims text, when a future scan source ever populates one) carries fewer than
   :data:`_MIN_TECHNICAL_WORDS` words never reaches the LLM at all -- :func:`analyze_patents`
   writes the fixed, honest :data:`_NO_TEXT_SUMMARY_HE`/:data:`_NO_TEXT_SO_WHAT_HE` placeholders
   and stamps ``raw.text_available = false`` instead. This is what would have stopped the
   US10506436B1 case outright: its 18-word assignment notice never clears the bar.
2. :func:`ground_generated_text` -- a *post-call* check, for the (more common) case where the
   abstract does carry real text but the model still pads a sentence with a technical noun,
   number, or component name that appears nowhere in the source. Every sentence of a freshly
   generated ``claims_summary_he``/``so_what_he`` is checked in isolation; a sentence naming a
   technical token (an English word/acronym or a >=2-digit number -- Hebrew free prose itself is
   not tokenised, matching the CR's own reproduction: the fabrication named untranslated component
   nouns) that cannot be found (case-insensitively) anywhere in the patent's own title/abstract/
   assignees/CPC codes/pub_number is dropped, and ``patent.ungrounded_description_removed`` is
   logged. A field left with nothing ungrounded to say falls back to
   :data:`_INSUFFICIENT_AFTER_GROUNDING_HE`.
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

# --------------------------------------------------------------------------
# Round 14 text-grounding guards (see module docstring above).
# --------------------------------------------------------------------------

#: Below this many words of combined abstract+claims text, a record is "text-less" -- the LLM is
#: never asked to describe the technology at all (module docstring, guard 1). ~40 words is roughly
#: one real sentence of technical prose; US10506436B1's actual stored abstract is 18 words and is
#: pure assignment-notice boilerplate, CN112074705A's is 40 words of genuine (if fragmentary)
#: technical text -- the threshold was picked to keep the latter analyzable while gating the
#: former.
_MIN_TECHNICAL_WORDS = 40

_NO_TEXT_SUMMARY_HE = "אין תקציר או תביעות זמינים למסמך זה; לא ניתן לתאר את הטכנולוגיה."
_NO_TEXT_SO_WHAT_HE = "אין תקציר או תביעות זמינים למסמך זה; לא ניתן להעריך משמעות עסקית/טכנולוגית."
#: Fallback when a generated field's every sentence gets stripped by :func:`ground_generated_text`
#: (source text existed and cleared the word-count bar, but nothing the model wrote about it could
#: be verified against it) -- reuses the same honest phrasing the prompt itself already asks the
#: model to use for a genuinely thin abstract (``agent/eoa/llm/prompts/patent_claims.md``).
_INSUFFICIENT_AFTER_GROUNDING_HE = (
    "התקציר אינו מספק מספיק מידע לניתוח תביעות מלא (פרטים שנוצרו לא אומתו מול המקור והוסרו)."
)

_SENTENCE_SPLIT_RE_G = re.compile(r"(?<=[.!?])\s+")
#: A "technical token" for grounding purposes: an English word/acronym (>=2 letters) or a number
#: carrying >=2 digits, optionally trailed by more alphanumerics/hyphens (model numbers, CPC-style
#: codes, part numbers -- e.g. "CTR50", "lcmxo3lft-2100e-5UWG49", "G01S13"). Deliberately narrow --
#: this is a heuristic, defence-in-depth check, not a full noun-phrase extractor (see the module
#: docstring: guard 1, the pre-call word-count gate, is what actually stops a pure-boilerplate
#: source like US10506436B1's from ever reaching the model in the first place).
_TECH_TOKEN_RE = re.compile(r"[A-Za-z]{2,}[A-Za-z0-9\-]*|\d{2,}[A-Za-z0-9\-]*")


def _technical_word_count(abstract: str | None, raw: dict[str, Any] | None) -> int:
    """Word count of ``abstract`` plus any claims-style text a future scan source might stash in
    ``raw`` (``raw.get("claims_text")`` -- no current scan source populates this, but the check is
    written to honor it if one ever does, per the task's "no abstract and no claims text" wording).
    """
    raw = raw or {}
    claims_text = raw.get("claims_text") or ""
    text = f"{abstract or ''} {claims_text}".strip()
    return len(text.split())


def _has_sufficient_technical_text(abstract: str | None, raw: dict[str, Any] | None) -> bool:
    """Guard 1 (module docstring): ``False`` when there is nothing resembling real technical prose
    to analyze -- the caller must not send this record to the LLM at all."""
    return _technical_word_count(abstract, raw) >= _MIN_TECHNICAL_WORDS


def _grounding_source_pool(row: dict[str, Any]) -> str:
    """The lower-cased text a generated sentence's technical tokens are checked against: title +
    abstract + any ``raw`` claims text (the CR's own "abstract/claims/title" wording) plus the
    already-verified, DB-stored assignees/CPC codes/pub_number -- the latter three are never
    something the model could have fabricated (they are handed to it as given facts, not inferred
    from free text), so excluding them would only produce false-positive strips of perfectly
    legitimate sentences that simply name the patent's own known assignee or CPC class."""
    raw = row.get("raw") or {}
    parts = [
        row.get("title") or "",
        row.get("abstract") or "",
        raw.get("claims_text") or "",
        " ".join(row.get("assignees") or []),
        " ".join(row.get("cpc") or []),
        row.get("pub_number") or "",
    ]
    return " ".join(parts).casefold()


#: Standing project/domain vocabulary (docs/CONVENTIONS.md, config/taxonomy.yaml's own "tech_dev"
#: domain, and this prompt's own field description for ``so_what_he`` -- "מה המשמעות עבור מוצרי
#: EO/IR ועבור מיצובה של התעשייה הביטחונית הישראלית" literally *asks* the model to use "EO/IR" in
#: every ``so_what_he``) -- always treated as grounded regardless of whether the specific patent's
#: own title/abstract happens to spell it out. Without this allowlist, guard 2 flags the industry-
#: standard framing every legitimate ``so_what_he`` is instructed to use as if it were a fabricated,
#: patent-specific technical claim (confirmed live 2026-09-07: a full-corpus dry run of
#: ``scripts/repair_round14_patent_text.py`` stripped "EO"/"IR" out of the overwhelming majority of
#: stored ``so_what_he`` sentences before this allowlist was added -- a false-positive, not a real
#: fabrication catch). Casefolded token comparison, same as the rest of this check.
_DOMAIN_VOCAB = frozenset(
    t.casefold() for t in ("EO", "IR", "ISR", "UAS", "C-UAS", "SNR", "HEL", "DEW")
)


def ground_generated_text(
    text: str, source_pool_lower: str, *, pub_number: str, field_name: str
) -> str:
    """Guard 2 (module docstring): drop every sentence of ``text`` that names a technical token
    (:data:`_TECH_TOKEN_RE`) not found anywhere in ``source_pool_lower`` (already lower-cased --
    see :func:`_grounding_source_pool`) and not in the standing :data:`_DOMAIN_VOCAB` allowlist,
    logging ``patent.ungrounded_description_removed`` once per dropped sentence. A sentence with no
    technical tokens at all (e.g. a purely qualitative "אין מספיק מידע" hedge) is never dropped by
    this check -- there is nothing in it to verify. Returns
    :data:`_INSUFFICIENT_AFTER_GROUNDING_HE` if every sentence was stripped.

    Known limitation (documented rather than silently over-claimed, docs/qa/content_review/CR-
    patents-2.md): this is a lexical substring check, not semantic grounding -- a sentence that
    introduces its *own* abbreviation of a term that genuinely is spelled out in the source (e.g.
    writing "RIC" for a "Readout Integrated Circuit" the abstract only ever spells out in full) is
    still flagged as ungrounded even though the underlying fact is real. This trades recall for
    precision on the flagship bug case (guard 1's pre-call word-count gate is the actual primary
    defense there, see the module docstring) and is intentionally conservative for everything else."""
    if not text or not text.strip():
        return text
    sentences = [s for s in _SENTENCE_SPLIT_RE_G.split(text.strip()) if s.strip()]
    kept: list[str] = []
    for sentence in sentences:
        tokens = _TECH_TOKEN_RE.findall(sentence)
        ungrounded = [
            t for t in tokens if t.casefold() not in source_pool_lower and t.casefold() not in _DOMAIN_VOCAB
        ]
        if ungrounded:
            log.info(
                "patent.ungrounded_description_removed",
                pub_number=pub_number,
                field=field_name,
                token=ungrounded[0],
                sentence=sentence[:200],
            )
            continue
        kept.append(sentence)
    if not kept:
        return _INSUFFICIENT_AFTER_GROUNDING_HE
    return " ".join(kept)

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
    #: Round 14 guard 1: rows never sent to the LLM at all because their abstract+claims text fell
    #: below :data:`_MIN_TECHNICAL_WORDS` -- given the fixed placeholder instead.
    text_unavailable: int = 0
    #: Round 14 guard 2: individual generated sentences dropped by :func:`ground_generated_text`
    #: across every analyzed row this run (not a row count -- one row can contribute more than one).
    ungrounded_sentences_removed: int = 0


def _rows_missing_analysis(limit: int) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pub_number, title, abstract, assignees, cpc, jurisdictions, url, raw "
            "FROM patents WHERE claims_summary_he IS NULL ORDER BY id LIMIT %(limit)s",
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


def _persist_no_text_analysis(patent_id: int, israel_relevance: float, entity_ids: list[int]) -> None:
    """Round 14 guard 1's write path: the fixed placeholder text (never an LLM call), plus
    ``raw.text_available = false`` so any later pass (a repair script, a future re-scan) can tell
    at a glance that this row's claims_summary_he was never actually attempted, not just short."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE patents SET
                claims_summary_he = %(claims_summary_he)s,
                subdomain = NULL,
                so_what_he = %(so_what_he)s,
                israel_relevance = %(israel_relevance)s,
                entity_ids = %(entity_ids)s,
                raw = COALESCE(raw, '{}'::jsonb) || jsonb_build_object('text_available', false)
            WHERE id = %(id)s
            """,
            {
                "claims_summary_he": _NO_TEXT_SUMMARY_HE,
                "so_what_he": _NO_TEXT_SO_WHAT_HE,
                "israel_relevance": israel_relevance,
                "entity_ids": entity_ids or None,
                "id": patent_id,
            },
        )


def _sentence_count(text: str) -> int:
    return len([s for s in _SENTENCE_SPLIT_RE_G.split((text or "").strip()) if s.strip()])


@dataclass
class _RowAnalysisResult:
    outcome: str  # "text_unavailable" | "analyzed" | "llm_deferred" | "llm_failed" | "persist_failed"
    ungrounded_sentences_removed: int = 0
    out: PatentClaimsOut | None = None


def analyze_one_patent_row(
    row: dict[str, Any], subdomain_keys: list[str], *, role: str = "resident", interactive: bool = False
) -> _RowAnalysisResult:
    """The full per-patent analysis pipeline for one already-fetched ``patents`` row (``id``,
    ``pub_number``, ``title``, ``abstract``, ``assignees``, ``cpc``, ``jurisdictions``, ``url``,
    ``raw`` -- the exact shape :func:`_rows_missing_analysis` selects): guard 1's text-availability
    gate, the LLM call (skipped entirely when guard 1 fails), guard 2's grounding strip, and the
    final DB persist. Extracted out of :func:`analyze_patents` (round 14, 2026-09-07) so
    ``scripts/repair_round14_patent_text.py`` can re-run the *exact* same pipeline for a single row
    right after fetching that row a fresh, real detail-page abstract -- without duplicating any of
    this logic. A single row's LLM failure never raises (docs/CONVENTIONS.md rule 9) -- the outcome
    is reported back via :class:`_RowAnalysisResult` instead."""
    assignees = row.get("assignees") or []

    # Round 14 guard 1 (module docstring): a record with no usable abstract/claims text is never
    # sent to the LLM at all -- there is nothing for it to legitimately describe, and asking anyway
    # is exactly how US10506436B1's fabricated optical-lens description happened.
    if not _has_sufficient_technical_text(row.get("abstract"), row.get("raw")):
        relevance = _israel_relevance(
            row.get("title") or "", row.get("abstract") or "", assignees, row.get("jurisdictions") or []
        )
        entity_ids = _resolve_entity_ids(assignees)
        try:
            _persist_no_text_analysis(row["id"], relevance, entity_ids)
        except Exception as exc:
            log.warning("patents_analyze_persist_failed", patent_id=row["id"], error=str(exc)[:200])
            return _RowAnalysisResult(outcome="persist_failed")
        return _RowAnalysisResult(outcome="text_unavailable")

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
        return _RowAnalysisResult(outcome="llm_deferred")
    except LLMOutputError as exc:
        log.warning("patents_analyze_llm_failed", pub_number=row["pub_number"], error=str(exc)[:200])
        return _RowAnalysisResult(outcome="llm_failed")
    except Exception as exc:
        log.warning("patents_analyze_unexpected_error", pub_number=row["pub_number"], error=str(exc)[:200])
        return _RowAnalysisResult(outcome="llm_failed")

    if out.subdomain and out.subdomain not in subdomain_keys:
        out.subdomain = ""  # never store an invented subdomain key

    # Round 14 guard 2 (module docstring): strip any sentence the model wrote whose technical
    # tokens cannot be verified against the record's own title/abstract/assignees/CPC/pub number --
    # defense-in-depth for a record that *did* clear guard 1's word-count bar but whose generated
    # text still padded in an unverifiable technical detail.
    source_pool = _grounding_source_pool(row)
    before_sentences = _sentence_count(out.claims_summary_he) + _sentence_count(out.so_what_he)
    out.claims_summary_he = ground_generated_text(
        out.claims_summary_he, source_pool, pub_number=row["pub_number"], field_name="claims_summary_he"
    )
    out.so_what_he = ground_generated_text(
        out.so_what_he, source_pool, pub_number=row["pub_number"], field_name="so_what_he"
    )
    after_sentences = _sentence_count(out.claims_summary_he) + _sentence_count(out.so_what_he)
    # A field entirely replaced by _INSUFFICIENT_AFTER_GROUNDING_HE still counts as 1 sentence
    # post-strip, so this slightly undercounts a fully-emptied field's true removed-sentence count
    # -- fine for a coarse stats counter (the per-sentence detail is in the log line).
    ungrounded_sentences_removed = max(0, before_sentences - after_sentences)

    relevance = _israel_relevance(
        row.get("title") or "", row.get("abstract") or "", assignees, row.get("jurisdictions") or []
    )
    entity_ids = _resolve_entity_ids(assignees)
    try:
        _persist_analysis(row["id"], out, relevance, entity_ids)
    except Exception as exc:
        log.warning("patents_analyze_persist_failed", patent_id=row["id"], error=str(exc)[:200])
        return _RowAnalysisResult(
            outcome="persist_failed", ungrounded_sentences_removed=ungrounded_sentences_removed, out=out
        )
    return _RowAnalysisResult(
        outcome="analyzed", ungrounded_sentences_removed=ungrounded_sentences_removed, out=out
    )


_OUTCOME_TO_STATS_FIELD = {
    "text_unavailable": "text_unavailable",
    "analyzed": "analyzed",
    "llm_deferred": "llm_deferred",
    "llm_failed": "llm_failed",
}


def analyze_patents(
    limit: int = 10, *, role: str = "resident", interactive: bool = False
) -> PatentAnalyzeStats:
    """A14 step 3: analyze up to ``limit`` patents still missing ``claims_summary_he``. A single
    row's LLM failure never blocks the others (docs/CONVENTIONS.md rule 9). Thin loop over
    :func:`analyze_one_patent_row` -- see that function's own docstring for the actual per-row
    pipeline (round 14, 2026-09-07: extracted so the repair script can reuse it directly)."""
    stats = PatentAnalyzeStats()
    subdomain_keys = _tech_dev_subdomain_keys()

    for row in _rows_missing_analysis(limit):
        result = analyze_one_patent_row(row, subdomain_keys, role=role, interactive=interactive)
        field = _OUTCOME_TO_STATS_FIELD.get(result.outcome)
        if field:
            setattr(stats, field, getattr(stats, field) + 1)
        stats.ungrounded_sentences_removed += result.ungrounded_sentences_removed

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
