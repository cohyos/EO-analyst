"""Stage: patent discovery (A14, docs/PLAN_WINDOWS_NATIVE.md row A14).

For each configured watch topic (``config/patents.yaml``'s ``watch_topics``) and each configured
assignee (``assignees``): query EPO OPS / PatentsView **in-process** (importing the tool functions
straight out of ``eoa.mcp_servers.patents`` rather than spawning the stdio server -- both keys are
unconfigured on this machine, so those calls degrade to ``not_configured`` and this always falls
through to the second path) via the module's own SSRF-guarded ``_common`` HTTP helpers, else the
keyless Google Patents search fallback (``eoa.search.provider``, ``site:patents.google.com
<query>``), parsing each hit's URL for a publication number and its title/snippet for an assignee
(via ``eoa.pipeline.entity_normalize``'s watchlist alias matching -- "assignee if present").

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eoa.config import CONFIG_DIR
from eoa.db import connection
from eoa.patents.models import PatentRecord
from eoa.pipeline.entity_normalize import find_watchlist_aliases_in_text, resolve_canonical
from eoa.search.provider import search

log = structlog.get_logger(__name__)

PATENTS_YAML = CONFIG_DIR / "patents.yaml"

DEFAULT_FIRST_RUN_SINCE_DAYS = 90
# A later scan relies on pub_number dedup (ON CONFLICT DO NOTHING) for correctness; this window
# just bounds how much redundant search/API traffic a routine weekly re-scan generates.
_SUBSEQUENT_SCAN_SINCE_DAYS = 21

_GOOGLE_PATENTS_URL_RE = re.compile(r"patents\.google\.com/patent/([A-Za-z]{2}[A-Za-z0-9]+)")


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
# EPO OPS / PatentsView (in-process, only when configured)
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
        or os.environ.get("PATENTSVIEW_API_KEY")
    )


def _epo_records(query: str, limit: int) -> list[PatentRecord]:
    """EPO OPS published-data search, parsed into minimal :class:`PatentRecord` rows.

    The biblio-search endpoint (``eoa.mcp_servers.patents.epo_ops_search``) only returns
    publication references (country/doc-number/kind) -- no title/abstract/assignee without a
    further per-document biblio fetch, which this module deliberately does not add (EPO_OPS is
    unconfigured on this dev machine and unverified live, per that module's own docstring); a
    title-less record here is still useful as a real, deduped ``pub_number`` that
    ``eoa.patents.analyze`` can later enrich once biblio detail is worth adding."""
    from eoa.mcp_servers.patents import epo_ops_search

    try:
        raw = json.loads(epo_ops_search(query, limit=limit))
    except Exception as exc:
        log.debug("patents_epo_search_failed", query=query[:80], error=str(exc)[:200])
        return []
    if raw.get("error"):
        return []
    out: list[PatentRecord] = []
    for r in raw.get("results") or []:
        country, doc_number, kind = r.get("country"), r.get("doc_number"), r.get("kind")
        if not doc_number:
            continue
        pub_number = f"{country or ''}{doc_number}{kind or ''}"
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


def _patentsview_records(query: str, assignee: str, limit: int) -> list[PatentRecord]:
    from eoa.mcp_servers.patents import patentsview_search

    try:
        raw = json.loads(patentsview_search(query, assignee=assignee, limit=limit))
    except Exception as exc:
        log.debug("patents_patentsview_search_failed", query=query[:80], error=str(exc)[:200])
        return []
    if raw.get("error"):
        return []
    out: list[PatentRecord] = []
    for p in raw.get("patents") or []:
        patent_id = p.get("patent_id")
        if not patent_id:
            continue
        out.append(
            PatentRecord(
                pub_number=f"US{patent_id}",
                title=p.get("patent_title") or "",
                publication_date=_parse_date(p.get("patent_date")),
                assignees=[assignee] if assignee else [],
                jurisdictions=["US"],
                source="patentsview",
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
    """Best-effort assignee extraction from a search hit's title+snippet: every watchlist-alias
    hit (:func:`find_watchlist_aliases_in_text`), restricted to names that actually resolve to a
    ``kind == "company"`` canonical record. The alias table also carries curated government/org
    and country entries (e.g. "NATO", "Europe"/"אירופה") for unrelated report-entity-extraction
    purposes -- those are never real patent assignees, so a bare text mention of "Europe" must not
    turn into a fabricated assignee here."""
    return [name for name in find_watchlist_aliases_in_text(text) if (resolve_canonical(name) or {}).get("kind") == "company"]


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
        out.append(
            PatentRecord(
                pub_number=pub_number,
                title=_clean_search_title(hit.title),
                abstract=hit.snippet or "",
                assignees=_assignee_candidates_in_text(text),
                url=hit.url,
                source="google_patents_search",
                raw={"snippet": hit.snippet, "engine": hit.engine},
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


def _backfill_patent_fields(pub_number: str, rec: PatentRecord) -> None:
    """Goal (2026-09-06, user feedback re: mostly-empty assignee/CPC columns): a non-destructive
    backfill for an *existing* ``patents`` row whose ``assignees``/``cpc`` are still empty --
    ``_insert_patent``'s ``ON CONFLICT (pub_number) DO NOTHING`` means a repeat scan of the same
    ``pub_number`` (e.g. the routine watch-topic scan re-touching a row a keyless on-demand survey
    first inserted, or vice versa) would otherwise never get a chance to fill in a field the first
    scan happened not to find. Never overwrites a field that is already non-empty."""
    if not rec.assignees and not rec.cpc:
        return
    sets: list[str] = []
    params: dict[str, Any] = {"p": pub_number}
    if rec.assignees:
        sets.append("assignees = COALESCE(NULLIF(assignees, ARRAY[]::text[]), %(assignees)s)")
        params["assignees"] = rec.assignees
    if rec.cpc:
        sets.append("cpc = COALESCE(NULLIF(cpc, ARRAY[]::text[]), %(cpc)s)")
        params["cpc"] = rec.cpc
    with connection() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE patents SET {', '.join(sets)} WHERE pub_number = %(p)s", params)


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


def _records_for_query(query: str, *, assignee: str | None, structured_ok: bool) -> list[PatentRecord]:
    if structured_ok:
        records: list[PatentRecord] = []
        records += _epo_records(query, limit=20)
        records += _patentsview_records(query, assignee or "", limit=20)
        if records:
            return records
    return _google_patents_records(query)


def search_records(query: str, limit: int = 100) -> list[PatentRecord]:
    """Public, DB-free gather: every record the configured sources return for ``query`` (EPO
    OPS/PatentsView when configured, else the Google Patents search fallback), deduped by
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


def scan_patents(
    since_days: int | None = None,
    *,
    topics: list[WatchTopic] | None = None,
    assignees: list[str] | None = None,
) -> PatentScanStats:
    """A14 entry point: scan every configured watch topic + assignee, dedupe by ``pub_number``,
    insert new ``patents`` rows. ``since_days`` overrides the first-run/subsequent-run default
    window (mainly useful for tests and the CLI's ``--topic`` one-off mode)."""
    stats = PatentScanStats()
    today = dt.date.today()
    structured_ok = _structured_sources_available()
    stats.structured_sources_used = structured_ok

    if since_days is None:
        since_days = first_run_since_days() if not _any_patents_exist() else _SUBSEQUENT_SCAN_SINCE_DAYS

    seen_pub_numbers: set[str] = set()

    for topic in topics if topics is not None else load_watch_topics():
        try:
            records = _records_for_query(topic.query, assignee=None, structured_ok=structured_ok)
        except Exception as exc:
            log.warning("patents_topic_scan_failed", topic=topic.name_he, error=str(exc)[:200])
            stats.queries_failed += 1
            continue
        stats.topics_scanned += 1
        _ingest_records(records, topic.cpc, since_days, today, seen_pub_numbers, stats)

    for assignee in assignees if assignees is not None else load_assignees():
        query = f"{assignee} electro-optical infrared imaging patent"
        try:
            records = _records_for_query(query, assignee=assignee, structured_ok=structured_ok)
        except Exception as exc:
            log.warning("patents_assignee_scan_failed", assignee=assignee, error=str(exc)[:200])
            stats.queries_failed += 1
            continue
        stats.assignees_scanned += 1
        _ingest_records(records, [], since_days, today, seen_pub_numbers, stats)

    log.info("patents_scan_done", **vars(stats))
    return stats


def _ingest_records(
    records: list[PatentRecord],
    default_cpc: list[str],
    since_days: int,
    today: dt.date,
    seen_pub_numbers: set[str],
    stats: PatentScanStats,
) -> None:
    for rec in records:
        if rec.pub_number in seen_pub_numbers:
            continue
        seen_pub_numbers.add(rec.pub_number)
        if not _within_window(rec, since_days, today):
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
