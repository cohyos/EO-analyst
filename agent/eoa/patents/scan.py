"""Stage: patent discovery (A14, docs/PLAN_WINDOWS_NATIVE.md row A14).

For each configured watch topic (``config/patents.yaml``'s ``watch_topics``) and each configured
assignee (``assignees``): query EPO OPS / USPTO ODP **in-process** (importing the tool functions
straight out of ``eoa.mcp_servers.patents`` rather than spawning the stdio server) via the
module's own SSRF-guarded ``_common`` HTTP helpers. ``EPO_OPS_KEY``/``EPO_OPS_SECRET`` are
configured and verified live as of 2026-09-08 (docs/qa/content_review/PATENTS-OPS.md) -- EPO OPS
is therefore the primary structured source; a topic/assignee query only ever falls through to the
keyless Google Patents search fallback (``eoa.search.provider``, ``site:patents.google.com
<query>``) when neither EPO OPS nor USPTO ODP is configured, or when a configured provider's own
call for that specific query returned nothing. The fallback parses each hit's URL for a
publication number and its title/snippet for an assignee (via
``eoa.pipeline.entity_normalize``'s watchlist alias matching -- "assignee if present"); EPO OPS's
own top hits are additionally enriched with a per-hit biblio fetch (see :func:`_epo_records`) for
real title/abstract/applicants/CPC/dates/family_id, not just a bare publication reference.

Records are deduped by ``pub_number`` (``ON CONFLICT ... DO NOTHING`` -- a repeat scan hit never
duplicates a row) and, best-effort, by ``family_id`` when both sides carry one. Window: the first
scan (no existing ``patents`` rows) looks back ``config/patents.yaml``'s ``first_run_since_days``
(default 90); every later scan uses a much shorter window (``_SUBSEQUENT_SCAN_SINCE_DAYS``) since
dedup-by-``pub_number`` already makes a wider window merely redundant work, not a correctness
issue. A single query/source failing never stops the others (docs/CONVENTIONS.md rule 9).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eoa.config import CONFIG_DIR
from eoa.db import connection
from eoa.fetch.remote import fetch_raw_remote
from eoa.patents.models import PatentRecord
from eoa.pipeline.entity_normalize import find_watchlist_company_names_in_text
from eoa.search.provider import search

log = structlog.get_logger(__name__)

PATENTS_YAML = CONFIG_DIR / "patents.yaml"

DEFAULT_FIRST_RUN_SINCE_DAYS = 90
# A later scan relies on pub_number dedup (ON CONFLICT DO NOTHING) for correctness; this window
# just bounds how much redundant search/API traffic a routine weekly re-scan generates.
_SUBSEQUENT_SCAN_SINCE_DAYS = 21

_GOOGLE_PATENTS_URL_RE = re.compile(r"patents\.google\.com/patent/([A-Za-z]{2}[A-Za-z0-9]+)")

#: Per-assignee scan's keyword set (2026-09-08, replacing the old bare free-text string
#: ``f"{assignee} electro-optical infrared imaging patent"`` -- which was never restricted to the
#: assignee as an actual applicant on any provider, and the trailing "patent" was pure noise).
#: These are also the exact keywords the task's own live-verification round used against applicant
#: "Elbit Systems" -- docs/qa/content_review/PATENTS-OPS.md.
_ASSIGNEE_SCAN_KEYWORDS = ["electro-optical", "infrared", "gimbal", "payload"]


class WatchTopic(BaseModel):
    name_he: str
    query: str
    cpc: list[str] = Field(default_factory=list)


def _load_yaml(path: str | Path | None = None) -> dict[str, Any]:
    file_path = Path(path) if path is not None else PATENTS_YAML
    with file_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_cpc_codes(path: str | Path | None = None) -> list[str]:
    return list(_load_yaml(path).get("cpc") or [])


def load_watch_topics(path: str | Path | None = None) -> list[WatchTopic]:
    raw = _load_yaml(path)
    return [WatchTopic.model_validate(t) for t in raw.get("watch_topics", [])]


def load_assignees(path: str | Path | None = None) -> list[str]:
    return list(_load_yaml(path).get("assignees") or [])


def first_run_since_days(path: str | Path | None = None) -> int:
    return int(_load_yaml(path).get("first_run_since_days") or DEFAULT_FIRST_RUN_SINCE_DAYS)


# --------------------------------------------------------------------------
# EPO OPS / USPTO ODP (in-process, only when configured)
# --------------------------------------------------------------------------


def structured_sources_configured() -> bool:
    """Public wrapper for :func:`_structured_sources_available` -- used by ``eoa.patents.survey``
    and the API layer to decide whether to show the "search-only mode" banner."""
    return _structured_sources_available()


def _structured_sources_available() -> bool:
    """True if either structured provider has credentials configured (see
    ``eoa.mcp_servers.patents.ping``) -- checked once per scan so an unconfigured dev machine
    (the common case) skips straight to the search fallback for every query instead of making a
    doomed HTTP call per topic/assignee."""
    import os

    return bool(
        (os.environ.get("EPO_OPS_KEY") and os.environ.get("EPO_OPS_SECRET"))
        or os.environ.get("USPTO_ODP_API_KEY")
    )


#: EPO OPS's "retrieval" fair-use quota is separate from -- and, per its own throttling headers,
#: tighter than -- the "search" quota (eoa.mcp_servers.patents's own X-Throttling-Control note); a
#: biblio fetch is one retrieval call each, so only the top N hits per query get one, keeping a
#: routine scan's total extra-call volume small and predictable rather than one-per-hit.
_EPO_BIBLIO_ENRICH_CAP = 5


def _epo_records(query: str, limit: int, *, biblio_cap: int = _EPO_BIBLIO_ENRICH_CAP) -> list[PatentRecord]:
    """EPO OPS published-data search, parsed into :class:`PatentRecord` rows.

    The search endpoint alone (``eoa.mcp_servers.patents.epo_ops_search``) only returns bare
    publication references (country/doc-number/kind) -- no title/abstract/assignee/CPC without a
    further per-document biblio fetch. Live-verified 2026-09-08 (``EPO_OPS_KEY``/``EPO_OPS_SECRET``
    now configured, see docs/qa/content_review/PATENTS-OPS.md): the top ``biblio_cap`` hits are each
    enriched with ``eoa.mcp_servers.patents.epo_ops_biblio`` (title/abstract/applicants/CPC/IPC/
    dates/family_id) right here, so a scan's own EPO OPS records carry real content rather than
    only a deduped identifier for the first time. A hit beyond the cap, or a biblio fetch/parse
    failure for one hit (never aborts the batch, docs/CONVENTIONS.md rule 9), still yields the same
    title-less record the pre-biblio version of this function always produced -- still a real,
    deduped ``pub_number`` the existing Google-Patents-detail-page enrichment pass
    (:func:`enrich_stored_patents_missing_assignee`) can pick up later."""
    from eoa.mcp_servers.patents import epo_ops_biblio, epo_ops_search

    try:
        raw = json.loads(epo_ops_search(query, limit=limit))
    except Exception as exc:
        log.debug("patents_epo_search_failed", query=query[:80], error=str(exc)[:200])
        return []
    if raw.get("error"):
        return []
    out: list[PatentRecord] = []
    for i, r in enumerate(raw.get("results") or []):
        country, doc_number, kind = r.get("country"), r.get("doc_number"), r.get("kind")
        if not doc_number:
            continue
        pub_number = f"{country or ''}{doc_number}{kind or ''}"
        biblio: dict[str, Any] | None = None
        if i < biblio_cap:
            try:
                parsed = json.loads(epo_ops_biblio(pub_number))
            except Exception as exc:
                log.debug("patents_epo_biblio_failed", pub_number=pub_number, error=str(exc)[:200])
                parsed = None
            if parsed and not parsed.get("error"):
                biblio = parsed
        if biblio:
            out.append(
                PatentRecord(
                    pub_number=biblio.get("pub_number") or pub_number,
                    kind=biblio.get("kind") or kind,
                    title=biblio.get("title") or "",
                    abstract=biblio.get("abstract") or "",
                    assignees=list(biblio.get("assignees") or []),
                    inventors=list(biblio.get("inventors") or []),
                    cpc=list(biblio.get("cpc") or []),
                    priority_date=_parse_date(biblio.get("priority_date")),
                    filing_date=_parse_date(biblio.get("filing_date")),
                    publication_date=_parse_date(biblio.get("publication_date")),
                    family_id=biblio.get("family_id"),
                    jurisdictions=[country] if country else [],
                    url=biblio.get("url"),
                    source="epo_ops",
                    raw=biblio,
                )
            )
        else:
            out.append(
                PatentRecord(
                    pub_number=pub_number,
                    kind=kind,
                    jurisdictions=[country] if country else [],
                    source="epo_ops",
                    raw=r,
                )
            )
    return out


def _uspto_odp_records(query: str, assignee: str, limit: int) -> list[PatentRecord]:
    """USPTO Open Data Portal search (``eoa.mcp_servers.patents.uspto_odp_search``, replaced
    PatentsView 2026-09-07), parsed into :class:`PatentRecord` rows. Unlike the EPO biblio search
    the ODP rows already carry title/assignees/CPC/dates, so nothing needs a second fetch."""
    from eoa.mcp_servers.patents import uspto_odp_search

    try:
        raw = json.loads(uspto_odp_search(query, assignee=assignee, limit=limit))
    except Exception as exc:
        log.debug("patents_uspto_odp_search_failed", query=query[:80], error=str(exc)[:200])
        return []
    if raw.get("error"):
        return []
    out: list[PatentRecord] = []
    for p in raw.get("results") or []:
        pub_number = p.get("pub_number")
        if not pub_number:
            continue
        out.append(
            PatentRecord(
                pub_number=pub_number,
                kind=p.get("kind"),
                title=p.get("title") or "",
                assignees=list(p.get("assignees") or []),
                inventors=list(p.get("inventors") or []),
                cpc=list(p.get("cpc") or []),
                filing_date=_parse_date(p.get("filing_date")),
                publication_date=_parse_date(p.get("publication_date")),
                grant_date=_parse_date(p.get("grant_date")),
                jurisdictions=[p.get("country") or "US"],
                url=p.get("url"),
                source="uspto_odp",
                raw=p,
            )
        )
    return out


def _parse_date(raw: Any) -> dt.date | None:
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Google Patents search fallback (keyless)
# --------------------------------------------------------------------------


def _extract_pub_number(url: str) -> str | None:
    m = _GOOGLE_PATENTS_URL_RE.search(url or "")
    return m.group(1) if m else None


def _clean_search_title(title: str) -> str:
    """Google Patents result titles are usually ``"<title> - Google Patents"`` -- strip that
    fixed suffix so the stored title is just the patent's own title."""
    return re.sub(r"\s*-\s*Google Patents\s*$", "", title or "", flags=re.IGNORECASE).strip()


def _assignee_candidates_in_text(text: str) -> list[str]:
    """Best-effort assignee extraction from a search hit's title+snippet: every watchlist
    *company*-name hit from the assignee-safe matcher (round 14, 2026-09-07,
    docs/qa/content_review/CR-patents.md --
    :func:`eoa.pipeline.entity_normalize.find_watchlist_company_names_in_text`), which already
    restricts to ``kind == "company"`` records, never a product/system alias (a patent id 64,
    CN112074705A, was previously misattributed to Anduril via its own "Lattice" product alias
    naming an unrelated FPGA component), and never a match embedded in a longer/unrelated
    organisation name or citation/component mention. This function is now a thin wrapper kept for
    call-site stability and its own docstring context; the real logic (and its "never a program/
    org/country" guarantee) lives in the shared, assignee-safe matcher."""
    return find_watchlist_company_names_in_text(text)


def _google_patents_records(query: str, *, lang: str = "en", max_results: int = 10) -> list[PatentRecord]:
    """Keyless fallback: a plain web search scoped to ``site:patents.google.com``, parsed into
    minimal records -- ``pub_number`` from the result URL, ``title`` from the search hit, and
    ``assignees`` best-effort via a watchlist-company-alias text match against the title+snippet
    (F2: never invents an assignee not actually named in the visible text)."""
    resp = search(f"site:patents.google.com {query}", lang=lang, max_results=max_results)
    if resp.error:
        log.debug("patents_google_search_failed", query=query[:80], error=resp.error)
        return []
    out: list[PatentRecord] = []
    seen: set[str] = set()
    for hit in resp.hits:
        pub_number = _extract_pub_number(hit.url)
        if not pub_number or pub_number in seen:
            continue
        seen.add(pub_number)
        text = f"{hit.title}\n{hit.snippet}"
        assignees = _assignee_candidates_in_text(text)
        # Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md): an assignee inferred from a
        # bare search snippet (never a detail page or a structured EPO OPS/USPTO ODP record) is
        # marked low-confidence via raw.assignee_source -- eoa.patents.scan
        # .enrich_stored_patents_missing_assignee (and scripts/repair_round14_patents.py for the
        # historical backlog) treats this row as still needing confirmation and lets a genuine
        # Google-Patents detail-page assignee overwrite it outright, unlike the routine
        # never-overwrite-a-populated-field backfill path.
        raw: dict[str, Any] = {"snippet": hit.snippet, "engine": hit.engine}
        if assignees:
            raw["assignee_source"] = "snippet"
        out.append(
            PatentRecord(
                pub_number=pub_number,
                title=_clean_search_title(hit.title),
                abstract=hit.snippet or "",
                assignees=assignees,
                url=hit.url,
                source="google_patents_search",
                raw=raw,
            )
        )
    return out


# --------------------------------------------------------------------------
# dedupe / persistence
# --------------------------------------------------------------------------


def _patent_exists(pub_number: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM patents WHERE pub_number = %s", (pub_number,))
        return cur.fetchone() is not None


def _any_patents_exist() -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM patents LIMIT 1")
        return cur.fetchone() is not None


def _backfill_patent_fields(
    pub_number: str,
    rec: PatentRecord,
    *,
    overwrite_assignees: bool = False,
    overwrite_abstract: bool = False,
) -> None:
    """Goal (2026-09-06, user feedback re: mostly-empty assignee/CPC columns): a non-destructive
    backfill for an *existing* ``patents`` row whose ``assignees``/``cpc``/``priority_date`` are
    still empty -- ``_insert_patent``'s ``ON CONFLICT (pub_number) DO NOTHING`` means a repeat scan
    of the same ``pub_number`` (e.g. the routine watch-topic scan re-touching a row a keyless
    on-demand survey first inserted, or vice versa -- round 6: or the Google-Patents-detail-page
    enrichment pass below, :func:`enrich_stored_patents_missing_assignee`) would otherwise never
    get a chance to fill in a field the first scan happened not to find. Never overwrites a field
    that is already non-empty (``priority_date`` is a plain scalar column, so a bare
    ``COALESCE(priority_date, ...)`` is enough -- no ``NULLIF`` empty-sentinel needed the way the
    two array columns require) -- *unless* ``overwrite_assignees``/``overwrite_abstract`` is set:

    - ``overwrite_assignees`` (round 14, 2026-09-07, docs/qa/content_review/CR-patents.md): a
      Google-Patents *detail-page* assignee always wins over whatever is currently stored, since
      the caller (:func:`enrich_stored_patents_missing_assignee`) only ever sets this for a row it
      just re-confirmed against the patent's own detail page -- replacing either an empty value or
      a previously low-confidence, search-snippet-derived one (``raw.assignee_source ==
      "snippet"``). The overwrite also stamps ``raw.assignee_source = "detail_page"`` so a later
      pass never re-flags this row as still needing confirmation.
    - ``overwrite_abstract`` (round 14, 2026-09-07, docs/qa/content_review/CR-patents.md's text-
      grounding rule): the keyless-search fallback's ``abstract`` is only ever a *search-result
      snippet* -- for a record like US10506436B1 that snippet is a bare USPTO assignment notice,
      not the patent's own abstract. A caller (``scripts/repair_round14_patent_text.py``) that just
      fetched the patent's real detail-page abstract (:func:`_parse_abstract_from_detail_html`)
      sets this to replace that low-quality snippet outright, stamping
      ``raw.abstract_source = 'detail_page'`` for the same "never re-flag as unconfirmed" reason.
    """
    if not rec.assignees and not rec.cpc and not rec.priority_date and not rec.abstract:
        return
    sets: list[str] = []
    params: dict[str, Any] = {"p": pub_number}
    if rec.assignees:
        if overwrite_assignees:
            sets.append("assignees = %(assignees)s")
            sets.append(
                "raw = COALESCE(raw, '{}'::jsonb) "
                "|| jsonb_build_object('assignee_source', 'detail_page')"
            )
        else:
            sets.append("assignees = COALESCE(NULLIF(assignees, ARRAY[]::text[]), %(assignees)s)")
        params["assignees"] = rec.assignees
    if rec.cpc:
        sets.append("cpc = COALESCE(NULLIF(cpc, ARRAY[]::text[]), %(cpc)s)")
        params["cpc"] = rec.cpc
    if rec.priority_date:
        sets.append("priority_date = COALESCE(priority_date, %(priority_date)s)")
        params["priority_date"] = rec.priority_date
    if rec.abstract:
        if overwrite_abstract:
            sets.append("abstract = %(abstract)s")
            sets.append(
                "raw = COALESCE(raw, '{}'::jsonb) "
                "|| jsonb_build_object('abstract_source', 'detail_page')"
            )
        else:
            sets.append("abstract = COALESCE(NULLIF(abstract, ''), %(abstract)s)")
        params["abstract"] = rec.abstract
    if not sets:
        return
    with connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE patents SET {', '.join(sets)} WHERE pub_number = %(p)s", params)


def _set_entity_ids(pub_number: str, entity_ids: list[int]) -> None:
    """Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md rule d): ``entity_ids`` must
    always be derived from a patent's *final* ``assignees`` -- called right after
    :func:`_backfill_patent_fields` overwrites ``assignees`` with a detail-page value for a row
    that was already analyzed (``eoa.patents.analyze.analyze_patents`` only (re)computes
    ``entity_ids`` for a row still missing ``claims_summary_he``, so an already-analyzed row would
    otherwise keep stale ``entity_ids`` pointing at the old, wrong assignee's entity forever)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE patents SET entity_ids = %(entity_ids)s WHERE pub_number = %(p)s",
            {"entity_ids": entity_ids or None, "p": pub_number},
        )


# --------------------------------------------------------------------------
# Round 6 D8 finding 1 (2026-09-06/07, docs/qa/loop/round_5_judge.md D8): the keyless
# Google-Patents-search fallback (:func:`_google_patents_records` above) only carries a search
# hit's title+snippet, so ``assignee``/``cpc`` land empty for most gathered records -- downstream
# (``eoa.patents.survey``) that yields a 0%-assignee-coverage survey with no assignee profiles and
# no CPC x assignee matrix (both gated on a non-empty top-assignees list). This section adds a
# best-effort, on-demand enrichment pass: for a capped number of a survey's own patents that still
# have no assignee on record, fetch that patent's own **individual** Google Patents detail page
# (not a search result -- a full page, keyed by ``pub_number``, that carries real structured
# bibliographic ``<meta>``/``<time>``/``<span>`` markup Google Patents does not put in its search
# results) and parse assignee/CPC codes/priority date out of it. Uses
# ``eoa.fetch.remote.fetch_raw_remote`` (the project's shared SSRF-guarded HTTP client -- see that
# module's ``assert_public_http_url``) rather than a raw ``requests``/``httpx`` call. Confirmed live
# 2026-09-06/07 against a handful of real ``patents.google.com/patent/<pub>/en`` pages pulled from
# this project's own ``patents`` table (design/verification only, well under a dozen fetches).
# --------------------------------------------------------------------------

_ENRICH_ASSIGNEE_CAP = 25
_ENRICH_SLEEP_S = 2.0
_GOOGLE_PATENT_DETAIL_URL_TMPL = "https://patents.google.com/patent/{pub}/en"

# "<meta name="DC.contributor" content="Xidrone Systems Inc" scheme="assignee">" -- an inventor
# carries the exact same DC.contributor tag with scheme="inventor" instead, so the scheme attribute
# is what actually distinguishes an assignee from an inventor on this page.
_DC_CONTRIBUTOR_ASSIGNEE_RE = re.compile(r'<meta name="DC\.contributor" content="([^"]*)" scheme="assignee">')
# The page's "Classifications" section lists every level of each CPC code's own hierarchy as its
# own <li> (class "G", subclass "G01", ..., down to a specific subgroup like "G01S13/02") -- only
# the IsCPC=true entries are genuinely CPC (Google Patents lists the legacy US classification the
# same way, IsCPC=false). :func:`_normalize_cpc_codes` below reduces a subgroup code to its
# class+subclass+main-group prefix (the part before the "/", e.g. "G01S13" from "G01S13/00") to
# match this project's own coarser config/patents.yaml convention (e.g. "G01J5") rather than
# storing every individual subgroup.
_CPC_CODE_ITEM_RE = re.compile(
    r'<li itemprop="classifications" itemscope repeat>\s*'
    r'<span itemprop="Code">([^<]+)</span>.*?'
    r'<meta itemprop="IsCPC" content="true">',
    re.DOTALL,
)
_PRIORITY_DATE_RE = re.compile(r'<time itemprop="priorityDate" datetime="([^"]+)">')
_FILING_DATE_RE = re.compile(r'<time itemprop="filingDate" datetime="([^"]+)">')
_PUBLICATION_DATE_RE = re.compile(r'<time itemprop="publicationDate" datetime="([^"]+)">')
# Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md rule "try to fetch the real abstract
# for text-less records via the existing detail-page enrichment"): the keyless Google-Patents
# *search* fallback only ever stores a search-result snippet as ``abstract`` -- for US10506436B1
# ("Lattice mesh") that snippet is a bare USPTO assignment-transfer notice with zero technical
# content, which is exactly what let the LLM invent a plausible-sounding description from nothing.
# The patent's own *detail page*, unlike the search snippet, does carry a real abstract in a
# ``<meta name="DC.description" content="...">`` tag (confirmed live 2026-09-07 against
# US10506436B1: a genuine ~120-word "lattice mesh" networking/registration abstract, nothing to do
# with optics). ``re.DOTALL`` because the content attribute's value legitimately spans multiple
# lines in the page's own HTML formatting.
_DC_DESCRIPTION_RE = re.compile(r'<meta name="DC\.description" content="(.*?)"\s*/?>', re.DOTALL)


def _parse_abstract_from_detail_html(html_text: str) -> str | None:
    """The patent's real abstract from its own Google Patents detail page (``None`` if the page
    carries no ``DC.description`` meta tag at all -- never guessed). HTML entities unescaped and
    interior whitespace/newlines collapsed to single spaces, matching how every other text field
    already stored on a ``patents`` row is shaped."""
    import html as _html_mod

    m = _DC_DESCRIPTION_RE.search(html_text)
    if not m:
        return None
    text = " ".join(_html_mod.unescape(m.group(1)).split())
    return text or None


def _normalize_cpc_codes(raw_codes: list[str]) -> list[str]:
    """Every ``raw_codes`` entry that carries a "/" (an actual group-level CPC code, not a bare
    class/subclass like "G" or "G01S") reduced to its class+subclass+main-group prefix (the part
    before the "/"), deduped, order preserved -- e.g. ``["G01S13/00", "G01S13/02", "G01S3/00"]`` ->
    ``["G01S13", "G01S3"]``. A bare class/subclass entry with no "/" at all is dropped -- too coarse
    to be a useful CPC value next to config/patents.yaml's own codes."""
    out: list[str] = []
    for code in raw_codes:
        code = code.strip()
        if "/" not in code:
            continue
        main = code.split("/", 1)[0]
        if main and main not in out:
            out.append(main)
    return out


def _parse_google_patent_detail_html(html: str) -> dict[str, Any]:
    """Best-effort parse of one ``patents.google.com/patent/<pub>/en`` detail page into
    ``{"assignees", "cpc", "priority_date", "filing_date", "publication_date", "abstract"}``. Every
    value is ``[]``/``None`` when the corresponding markup was not found on the page -- never
    guessed. ``abstract`` (round 14, 2026-09-07) is the patent's own real abstract
    (:func:`_parse_abstract_from_detail_html`) -- never the keyless search snippet a
    ``google_patents_search``-sourced row's ``abstract`` column may currently hold."""
    assignees = [a.strip() for a in _DC_CONTRIBUTOR_ASSIGNEE_RE.findall(html) if a.strip()]
    cpc = _normalize_cpc_codes(_CPC_CODE_ITEM_RE.findall(html))

    def _first_date(pattern: re.Pattern[str]) -> dt.date | None:
        m = pattern.search(html)
        return _parse_date(m.group(1)) if m else None

    return {
        "assignees": assignees,
        "cpc": cpc,
        "priority_date": _first_date(_PRIORITY_DATE_RE),
        "filing_date": _first_date(_FILING_DATE_RE),
        "publication_date": _first_date(_PUBLICATION_DATE_RE),
        "abstract": _parse_abstract_from_detail_html(html),
    }


def _fetch_patent_detail_html(pub_number: str) -> str | None:
    """One politeness-unaware fetch of ``pub_number``'s own Google Patents detail page (the
    caller, :func:`enrich_stored_patents_missing_assignee`, is what actually spaces calls out) --
    ``None`` on any fetch failure (SSRF-guard rejection, network error, non-2xx, ...), never raises
    (docs/CONVENTIONS.md rule 9: one bad fetch must not abort the batch)."""
    url = _GOOGLE_PATENT_DETAIL_URL_TMPL.format(pub=pub_number)
    try:
        resp = fetch_raw_remote(url)
    except Exception as exc:
        log.debug("patents_detail_fetch_failed", pub_number=pub_number, error=str(exc)[:200])
        return None
    text = resp.get("text") if isinstance(resp, dict) else None
    return text if isinstance(text, str) and text else None


def _patents_missing_assignee(patent_ids: list[int], limit: int) -> list[dict[str, Any]]:
    """Up to ``limit`` ``{id, pub_number}`` rows among ``patent_ids`` that still need a confirmed
    assignee right now -- either the ``assignees`` column is still empty (``NULL`` or ``{}``), or
    it was only ever filled from a search snippet (round 14, 2026-09-07,
    docs/qa/content_review/CR-patents.md rule c: ``raw.assignee_source = "snippet"`` is a
    low-confidence value that must still be replaced by a genuine detail-page assignee whenever one
    becomes available). The DB is the source of truth here (not the in-memory records the caller
    may also be holding), since ``patent_ids`` mixes freshly-inserted and supplemented-from-store
    patents alike."""
    if not patent_ids:
        return []
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, pub_number FROM patents "
            "WHERE id = ANY(%(ids)s) AND ("
            "  assignees IS NULL OR assignees = ARRAY[]::text[]"
            "  OR raw->>'assignee_source' = 'snippet'"
            ") "
            "ORDER BY id LIMIT %(limit)s",
            {"ids": patent_ids, "limit": limit},
        )
        return cur.fetchall()


def enrich_stored_patents_missing_assignee(
    patent_ids: list[int], *, cap: int = _ENRICH_ASSIGNEE_CAP, sleep_s: float = _ENRICH_SLEEP_S
) -> int:
    """Round 6 D8 finding 1: for up to ``cap`` of ``patent_ids`` that currently have no assignee on
    record in the ``patents`` table (fresh-this-run and supplemented-from-store alike -- called
    once, after both are merged, from ``eoa.patents.survey.build_patent_survey``), fetch that
    patent's own Google Patents detail page (politely, ``sleep_s`` seconds apart -- never in
    parallel, never before the first fetch) and backfill assignee/CPC/priority-date straight onto
    the stored row via :func:`_backfill_patent_fields` (never overwrites a field already
    populated). A fetch or parse failure for one patent is logged and skipped -- it never aborts
    the rest of the batch (docs/CONVENTIONS.md rule 9). Returns how many rows were actually
    updated (at least one new field found and written)."""
    targets = _patents_missing_assignee(patent_ids, cap)
    enriched = 0
    for i, row in enumerate(targets):
        if i:
            time.sleep(sleep_s)
        html = _fetch_patent_detail_html(row["pub_number"])
        if not html:
            continue
        try:
            detail = _parse_google_patent_detail_html(html)
        except Exception as exc:
            log.debug("patents_detail_parse_failed", pub_number=row["pub_number"], error=str(exc)[:200])
            continue
        if not (detail["assignees"] or detail["cpc"] or detail["priority_date"]):
            continue
        rec = PatentRecord(
            pub_number=row["pub_number"],
            assignees=detail["assignees"],
            cpc=detail["cpc"],
            priority_date=detail["priority_date"],
        )
        try:
            # Round 14 (2026-09-07, docs/qa/content_review/CR-patents.md rule c): a genuine
            # detail-page assignee always wins over whatever is currently stored -- whether that
            # was empty or a low-confidence search-snippet guess -- so this pass, unlike the
            # routine non-destructive scan-time backfill, overwrites outright.
            _backfill_patent_fields(row["pub_number"], rec, overwrite_assignees=bool(detail["assignees"]))
        except Exception as exc:
            log.warning("patents_enrich_backfill_failed", pub_number=row["pub_number"], error=str(exc)[:200])
            continue
        enriched += 1
        if detail["assignees"]:
            # Rule (d): entity_ids must always be derived from the final assignees. An
            # already-analyzed row's entity_ids were computed once, at analyze time, off the
            # (possibly wrong or still-empty) assignee then on record; eoa.patents.analyze
            # .analyze_patents never revisits a row once claims_summary_he is populated, so this is
            # the only place a later assignee correction re-syncs entity_ids for such a row. A
            # failure here is logged and skipped on its own (never rolls back the assignee/cpc
            # backfill just applied, never aborts the rest of the batch, docs/CONVENTIONS.md rule
            # 9) -- the row still counts as "enriched" since its assignee/cpc did get corrected.
            try:
                _set_entity_ids(row["pub_number"], _entity_ids_for_assignees(detail["assignees"]))
            except Exception as exc:
                log.warning(
                    "patents_enrich_entity_ids_failed", pub_number=row["pub_number"], error=str(exc)[:200]
                )
    return enriched


def _entity_ids_for_assignees(assignees: list[str]) -> list[int]:
    """Round 14 rule (d): resolve ``entity_ids`` from a *final* assignees list, reusing
    ``eoa.patents.analyze``'s own assignee->entity resolution (``_resolve_entity_ids``) -- a
    read-only import, not an edit to that module (other agents are concurrently editing it).
    Imported lazily (inside this function, not at module scope) to avoid a hard import-time
    dependency between the two sibling patents modules."""
    from eoa.patents.analyze import _resolve_entity_ids

    return _resolve_entity_ids(assignees)


def upsert_records(records: list[PatentRecord]) -> dict[str, int]:
    """Insert every record in ``records`` not already present (dedup by ``pub_number``), returning
    ``{pub_number: patent_id}`` for every record now on record (whether inserted just now or
    already existing) -- used by ``eoa.patents.survey`` to turn a gathered sample straight into
    real ``patents.id`` values for its citation registry/tables. An already-existing row gets a
    best-effort, non-destructive :func:`_backfill_patent_fields` pass first."""
    ids: dict[str, int] = {}
    for rec in records:
        if _patent_exists(rec.pub_number):
            try:
                _backfill_patent_fields(rec.pub_number, rec)
            except Exception as exc:
                log.warning("patents_backfill_failed", pub_number=rec.pub_number, error=str(exc)[:200])
            with connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM patents WHERE pub_number = %(p)s", {"p": rec.pub_number})
                row = cur.fetchone()
            if row:
                ids[rec.pub_number] = row["id"]
            continue
        try:
            new_id = _insert_patent(rec)
        except Exception as exc:
            log.warning("patents_upsert_records_failed", pub_number=rec.pub_number, error=str(exc)[:200])
            continue
        if new_id is not None:
            ids[rec.pub_number] = new_id
    return ids


def _insert_patent(rec: PatentRecord) -> int | None:
    """Insert one deduped :class:`PatentRecord`; ``None`` if a concurrent scan already inserted
    the same ``pub_number`` (``ON CONFLICT DO NOTHING``)."""
    query = """
        INSERT INTO patents (
            pub_number, kind, title, abstract, assignees, inventors, cpc,
            priority_date, filing_date, publication_date, grant_date, family_id,
            jurisdictions, forward_citations, backward_citations, url, source, raw
        )
        VALUES (
            %(pub_number)s, %(kind)s, %(title)s, %(abstract)s, %(assignees)s, %(inventors)s, %(cpc)s,
            %(priority_date)s, %(filing_date)s, %(publication_date)s, %(grant_date)s, %(family_id)s,
            %(jurisdictions)s, %(forward_citations)s, %(backward_citations)s, %(url)s, %(source)s, %(raw)s
        )
        ON CONFLICT (pub_number) DO NOTHING
        RETURNING id
    """
    params = {
        "pub_number": rec.pub_number,
        "kind": rec.kind,
        "title": rec.title or None,
        "abstract": rec.abstract or None,
        "assignees": rec.assignees or None,
        "inventors": rec.inventors or None,
        "cpc": rec.cpc or None,
        "priority_date": rec.priority_date,
        "filing_date": rec.filing_date,
        "publication_date": rec.publication_date,
        "grant_date": rec.grant_date,
        "family_id": rec.family_id,
        "jurisdictions": rec.jurisdictions or None,
        "forward_citations": rec.forward_citations,
        "backward_citations": rec.backward_citations,
        "url": rec.url,
        "source": rec.source,
        "raw": Json(rec.raw),
    }
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return row["id"] if row else None


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


@dataclass
class PatentScanStats:
    topics_scanned: int = 0
    assignees_scanned: int = 0
    queries_failed: int = 0
    records_fetched: int = 0
    duplicates: int = 0
    inserted: int = 0
    structured_sources_used: bool = False


def _within_window(rec: PatentRecord, since_days: int, today: dt.date) -> bool:
    """Same convention as ``eoa.tenders.scan._within_window``: a record with no publication date
    at all (typical for a search-hit-derived record) is never filtered out on age -- only a
    record that actually carries an old date is."""
    if rec.publication_date is None:
        return True
    return (today - rec.publication_date).days <= since_days


def _records_for_query(
    query: str, *, assignee: str | None, structured_ok: bool, epo_keywords: list[str] | None = None
) -> list[PatentRecord]:
    """``query`` is the free-text portion passed to USPTO ODP's ``q`` and (when no structured
    source has anything, or none is configured) the keyless Google Patents fallback -- both accept
    a plain natural-language string. EPO OPS's CQL does not (its own quoting rules are stricter, see
    ``eoa.mcp_servers.patents.build_epo_query``'s docstring): when ``assignee`` is set, the EPO call
    is built from structured parts (``pa=<assignee>`` AND'd with ``epo_keywords`` OR'd together,
    default: ``[query]`` treated as one term) instead of passing ``query`` through as-is."""
    if structured_ok:
        from eoa.mcp_servers.patents import build_epo_query

        records: list[PatentRecord] = []
        if assignee:
            epo_query = build_epo_query(applicant=assignee, keywords=epo_keywords or [query])
            records += _epo_records(epo_query, limit=20)
        else:
            records += _epo_records(query, limit=20)
        records += _uspto_odp_records(query, assignee or "", limit=20)
        if records:
            return records
    fallback_query = f"{assignee} {query}".strip() if assignee else query
    return _google_patents_records(fallback_query)


def search_records(query: str, limit: int = 100) -> list[PatentRecord]:
    """Public, DB-free gather: every record the configured sources return for ``query`` (EPO
    OPS/USPTO ODP when configured, else the Google Patents search fallback), deduped by
    ``pub_number`` and capped at ``limit``. Used by ``eoa.patents.survey`` to gather a deeper,
    on-demand sample for one free-text topic without going through the whole watch-topic/assignee
    scan loop (or its DB side effects) in :func:`scan_patents`."""
    structured_ok = _structured_sources_available()
    records = _records_for_query(query, assignee=None, structured_ok=structured_ok)
    if not structured_ok:
        # ddgs can return more than the default page in one call; ask for up to `limit` directly
        # rather than settling for the small default used by the routine per-topic scan above.
        records = _google_patents_records(query, max_results=min(limit, 100))
    seen: set[str] = set()
    out: list[PatentRecord] = []
    for rec in records:
        if rec.pub_number in seen:
            continue
        seen.add(rec.pub_number)
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def search_records_for_applicant(
    applicant: str, keywords: list[str], *, limit: int = 20
) -> list[PatentRecord]:
    """Public, DB-free gather scoped to one applicant (a product's vendor/alias) + a set of
    free-text keywords (product name + product-line terms) -- EPO OPS ``pa=``/USPTO ODP
    ``assignee=`` when configured, else the keyless Google Patents fallback (``"<applicant>
    <keywords>"``). Added 2026-09-08 for ``eoa.dossier.corpus``: an on-demand dossier product that
    predates/falls outside the periodic watch-topic scan (e.g. SPECTRO XR) otherwise has an empty
    ``data->'patents'`` section, since :func:`search_records` above has no applicant-restriction
    parameter of its own. Deduped by ``pub_number``, capped at ``limit``."""
    structured_ok = _structured_sources_available()
    free_text = " ".join(k for k in keywords if k and k.strip())
    records = _records_for_query(
        free_text, assignee=applicant, structured_ok=structured_ok, epo_keywords=keywords
    )
    if not structured_ok:
        records = _google_patents_records(f"{applicant} {free_text}".strip(), max_results=min(limit, 100))
    seen: set[str] = set()
    out: list[PatentRecord] = []
    for rec in records:
        if rec.pub_number in seen:
            continue
        seen.add(rec.pub_number)
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def scan_patents(
    since_days: int | None = None,
    *,
    topics: list[WatchTopic] | None = None,
    assignees: list[str] | None = None,
    max_inserted: int | None = None,
) -> PatentScanStats:
    """A14 entry point: scan every configured watch topic + assignee, dedupe by ``pub_number``,
    insert new ``patents`` rows. ``since_days`` overrides the first-run/subsequent-run default
    window (mainly useful for tests and the CLI's ``--topic`` one-off mode). ``max_inserted``
    (2026-09-08, added for a bounded one-off verification/rescan run): once ``stats.inserted``
    reaches this cap, the scan stops issuing further topic/assignee queries -- a query already in
    flight still finishes and its own records are still fully ingested (a single query's hits are
    never partially inserted), so the final ``stats.inserted`` can land slightly above the cap, not
    below it. ``None`` (the default) means unbounded, same as before this parameter existed."""
    stats = PatentScanStats()
    today = dt.date.today()
    structured_ok = _structured_sources_available()
    stats.structured_sources_used = structured_ok

    if since_days is None:
        since_days = first_run_since_days() if not _any_patents_exist() else _SUBSEQUENT_SCAN_SINCE_DAYS

    seen_pub_numbers: set[str] = set()

    for topic in topics if topics is not None else load_watch_topics():
        if max_inserted is not None and stats.inserted >= max_inserted:
            break
        try:
            records = _records_for_query(topic.query, assignee=None, structured_ok=structured_ok)
        except Exception as exc:
            log.warning("patents_topic_scan_failed", topic=topic.name_he, error=str(exc)[:200])
            stats.queries_failed += 1
            continue
        stats.topics_scanned += 1
        _ingest_records(records, topic.cpc, since_days, today, seen_pub_numbers, stats)

    for assignee in assignees if assignees is not None else load_assignees():
        if max_inserted is not None and stats.inserted >= max_inserted:
            break
        query = " ".join(_ASSIGNEE_SCAN_KEYWORDS)
        try:
            records = _records_for_query(
                query, assignee=assignee, structured_ok=structured_ok, epo_keywords=_ASSIGNEE_SCAN_KEYWORDS
            )
        except Exception as exc:
            log.warning("patents_assignee_scan_failed", assignee=assignee, error=str(exc)[:200])
            stats.queries_failed += 1
            continue
        stats.assignees_scanned += 1
        # apply_window=False (2026-09-08): an assignee query builds that company's patent
        # *portfolio*, not a novelty signal -- unlike the topic loop above. Before EPO OPS biblio
        # enrichment existed, every EPO record here carried no publication_date at all, so
        # _within_window's own "no date -> never filtered" rule silently exempted the whole
        # assignee loop from age filtering anyway; now that a biblio-enriched hit carries its real
        # (often years-old) publication_date, applying the window here would have the *enriched*,
        # highest-quality hits -- the ones biblio enrichment exists to add -- systematically
        # dropped while the un-enriched, title-less hits for the very same patents sailed through
        # unfiltered (verified live 2026-09-08: every one of a query's first 5 biblio-enriched
        # results was silently excluded this way before this fix). Correctness still comes from
        # pub_number dedup (ON CONFLICT DO NOTHING / seen_pub_numbers), exactly as this module's
        # own docstring already argues for the window being a traffic bound, not a correctness
        # mechanism.
        _ingest_records(records, [], since_days, today, seen_pub_numbers, stats, apply_window=False)

    log.info("patents_scan_done", **vars(stats))
    return stats


def _ingest_records(
    records: list[PatentRecord],
    default_cpc: list[str],
    since_days: int,
    today: dt.date,
    seen_pub_numbers: set[str],
    stats: PatentScanStats,
    *,
    apply_window: bool = True,
) -> None:
    for rec in records:
        if rec.pub_number in seen_pub_numbers:
            continue
        seen_pub_numbers.add(rec.pub_number)
        if apply_window and not _within_window(rec, since_days, today):
            continue
        stats.records_fetched += 1
        if not rec.cpc and default_cpc:
            rec.cpc = list(default_cpc)
        if _patent_exists(rec.pub_number):
            stats.duplicates += 1
            continue
        try:
            new_id = _insert_patent(rec)
        except Exception as exc:
            log.warning("patents_insert_failed", pub_number=rec.pub_number, error=str(exc)[:200])
            continue
        if new_id is None:
            stats.duplicates += 1
        else:
            stats.inserted += 1
