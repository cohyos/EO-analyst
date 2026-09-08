"""Stage 1 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.1): gather everything the DB already holds
for one product's name/vendor/aliases -- items, events, patents, tenders/forecasts, entities +
graph edges, and the previous dossier of the same ``product_key`` -- and build the citation
registry's DB half from it (numbered ``n``, same flat-registry convention as
``eoa.patents.survey``/``eoa.report.product_line``: every existing DB record gets a number first;
``eoa.dossier.plan`` continues that same numbering sequence for the web sources it reads).

No LLM/network calls here -- this stage is pure SQL + deterministic Python, which is also why the
task brief allows a live dry run of just this stage (plus ``plan``) without running the full
multi-topic investigation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings
from eoa.db import connection
from eoa.pipeline import entity_normalize

log = structlog.get_logger(__name__)

_ITEMS_LIMIT = 60
_EVENTS_LIMIT = 40
_PATENTS_LIMIT = 30
_TENDERS_LIMIT = 20
_FORECASTS_LIMIT = 20


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params or {})
        return cur.fetchall()


def _fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
    rows = _fetchall(query, params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# product_key
# --------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _slug_part(text: str) -> str:
    """ASCII-fold (best-effort) + lowercase + dash-join -- a Hebrew or otherwise non-Latin ``text``
    collapses to an empty string here (``product_key`` is meant to be a stable ASCII identifier;
    the human-readable ``product_name``/``vendor`` are kept verbatim in their own columns)."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP_RE.sub("-", ascii_only.lower()).strip("-")


def slugify_product_key(vendor: str | None, product_name: str) -> str:
    """``product_key`` = slug of vendor+name (frozen contract, section 5): e.g.
    ``("Elbit Systems", "SPECTRO XR")`` -> ``"elbit-systems-spectro-xr"``. When the vendor/name
    contain no ASCII-representable characters at all (an all-Hebrew name with no vendor given),
    falls back to a short deterministic hash suffix so the key is never empty."""
    parts = [p for p in (_slug_part(vendor or ""), _slug_part(product_name)) if p]
    key = "-".join(parts)
    if key:
        return key[:120]
    import hashlib

    digest = hashlib.sha1(f"{vendor or ''}|{product_name}".encode()).hexdigest()[:10]
    return f"product-{digest}"


# --------------------------------------------------------------------------
# search terms
# --------------------------------------------------------------------------


def _search_terms(product_name: str, vendor: str | None, aliases: list[str]) -> list[str]:
    terms = [product_name, *(aliases or [])]
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        t = (t or "").strip()
        if len(t) < 2:
            continue
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _ilike_any_clause(column: str, n: int, *, param_prefix: str) -> str:
    return " OR ".join(f"{column} ILIKE %({param_prefix}{i})s" for i in range(n))


def _term_params(terms: list[str], *, prefix: str) -> dict[str, str]:
    return {f"{prefix}{i}": f"%{t}%" for i, t in enumerate(terms)}


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 1): alias-matching precision. The broad ``ILIKE %term%`` clauses above
# stay as the (cheap, recall-favoring) SQL-side candidate filter -- but a short alias like "Spectro"
# substring-matches unrelated words ("spectroscopy"), which is exactly how the corpus dry run pulled
# in "Infrared spectroscopy - Wikipedia" (docs/qa/content_review/PD-backend.md's "Open item"). Every
# candidate row is re-checked in Python against a precise rule before it is kept: the product name OR
# a >=8-char alias must match as a whole word (``\b``-delimited, case-insensitive); an alias shorter
# than 8 chars only counts together with the vendor name also appearing (both as whole words) --
# never the short alias alone. A Wikipedia/general-reference-domain item additionally requires the
# full product name itself (not just an alias) to appear, per the same item's own false-positive.
# --------------------------------------------------------------------------

_SHORT_ALIAS_LEN = 8

#: General-reference / encyclopedia domains -- never enough on their own to justify a match on a
#: short alias; only the literal product name earns them a place in the corpus.
_GENERAL_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "wiktionary.org",
    "britannica.com",
    "dictionary.com",
    "investopedia.com",
}


def _word_present(text: str, term: str) -> bool:
    term = (term or "").strip()
    if not term:
        return False
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text or "", re.IGNORECASE) is not None


def _matches_product_precisely(
    text: str, product_name: str, vendor: str | None, aliases: list[str]
) -> bool:
    """The precision gate itself (product name, or a long alias, always qualify on their own; a
    short alias only together with the vendor name)."""
    if _word_present(text, product_name):
        return True
    for alias in aliases:
        if len(alias) >= _SHORT_ALIAS_LEN:
            if _word_present(text, alias):
                return True
        elif vendor and _word_present(text, alias) and _word_present(text, vendor):
            return True
    return False


def _is_general_reference_domain(domain: str | None) -> bool:
    d = (domain or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    if not d:
        return False
    return any(d == ref or d.endswith("." + ref) for ref in _GENERAL_REFERENCE_DOMAINS)


def _filter_precise(
    rows: list[dict[str, Any]],
    *,
    product_name: str,
    vendor: str | None,
    aliases: list[str],
    text_fn: Any,
    domain_fn: Any = None,
) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        text = text_fn(row) or ""
        if not _matches_product_precisely(text, product_name, vendor, aliases):
            continue
        if domain_fn is not None:
            domain = domain_fn(row)
            if _is_general_reference_domain(domain) and not _word_present(text, product_name):
                continue
        out.append(row)
    return out


def _item_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(v) for v in (row.get("title"), row.get("summary_he"), row.get("so_what_he")) if v
    )


def _event_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(v) for v in (row.get("title"), row.get("program"), row.get("summary_he")) if v
    )


def _patent_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(v)
        for v in (row.get("title"), row.get("abstract"), " ".join(row.get("assignees") or []))
        if v
    )


def _tender_text(row: dict[str, Any]) -> str:
    return " ".join(str(v) for v in (row.get("title"), row.get("summary_he")) if v)


def _forecast_text(row: dict[str, Any]) -> str:
    return " ".join(
        str(v) for v in (row.get("platform"), row.get("payload_need"), row.get("rationale_he")) if v
    )


# --------------------------------------------------------------------------
# collectors
# --------------------------------------------------------------------------


def collect_items(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _ITEMS_LIMIT,
) -> list[dict[str, Any]]:
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(title || ' ' || COALESCE(summary_he, '') || ' ' || COALESCE(so_what_he, ''))",
        len(terms),
        param_prefix="t",
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT id, title, url, source_name, published_at, summary_he, so_what_he, key_facts,
               entities_mentioned, domain, subdomain
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND ({clause})
        ORDER BY COALESCE(published_at, fetched_at, created_at) DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return _filter_precise(
        rows,
        product_name=product_name,
        vendor=vendor,
        aliases=aliases or [],
        text_fn=_item_text,
        domain_fn=lambda r: r.get("domain") or r.get("subdomain"),
    )


def collect_events(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _EVENTS_LIMIT,
) -> list[dict[str, Any]]:
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(e.title || ' ' || COALESCE(e.program, '') || ' ' || COALESCE(e.summary_he, ''))",
        len(terms),
        param_prefix="t",
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT e.id, e.item_id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties,
               e.customer, e.program, e.summary_he,
               i.title AS item_title, i.url AS item_url, i.source_name, i.published_at
        FROM events e
        LEFT JOIN items i ON i.id = e.item_id
        WHERE {clause}
        ORDER BY e.date DESC NULLS LAST, e.created_at DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return _filter_precise(
        rows, product_name=product_name, vendor=vendor, aliases=aliases or [], text_fn=_event_text
    )


def collect_patents(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _PATENTS_LIMIT,
) -> list[dict[str, Any]]:
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(COALESCE(title, '') || ' ' || COALESCE(abstract, '') || ' ' || COALESCE(array_to_string(assignees, ' '), ''))",
        len(terms),
        param_prefix="t",
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT id, pub_number, title, abstract, assignees, cpc, publication_date, filing_date, url, value_score
        FROM patents
        WHERE {clause}
        ORDER BY publication_date DESC NULLS LAST, id DESC
        LIMIT %(limit)s
        """,
        params,
    )
    return _filter_precise(
        rows, product_name=product_name, vendor=vendor, aliases=aliases or [], text_fn=_patent_text
    )


def collect_tenders(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _TENDERS_LIMIT,
) -> list[dict[str, Any]]:
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(COALESCE(title, '') || ' ' || COALESCE(summary_he, ''))", len(terms), param_prefix="t"
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT id, title, agency, country, deadline, status, url, summary_he
        FROM tenders
        WHERE {clause}
        ORDER BY deadline ASC NULLS LAST
        LIMIT %(limit)s
        """,
        params,
    )
    return _filter_precise(
        rows, product_name=product_name, vendor=vendor, aliases=aliases or [], text_fn=_tender_text
    )


def collect_forecasts(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _FORECASTS_LIMIT,
) -> list[dict[str, Any]]:
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(COALESCE(payload_need, '') || ' ' || COALESCE(platform, '') || ' ' || COALESCE(rationale_he, ''))",
        len(terms),
        param_prefix="t",
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT id, platform, buyer_country, payload_need, candidate_vendors, likelihood,
               window_from, window_to, rationale_he
        FROM tender_forecasts
        WHERE {clause}
        ORDER BY likelihood DESC NULLS LAST
        LIMIT %(limit)s
        """,
        params,
    )
    return _filter_precise(
        rows, product_name=product_name, vendor=vendor, aliases=aliases or [], text_fn=_forecast_text
    )


def collect_entities_and_edges(vendor: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The vendor's own canonical ``entities`` row (if resolvable via the watchlist/curated-org
    table) plus every ``graph_edges`` row touching it -- a best-effort context block, not a
    citable fact source in its own right (edges carry no ``n`` of their own; a claim resting on one
    must instead cite the ``item_id`` event/item the edge was built from, same discipline as
    ``eoa.patents.survey._verify_relationship_edges``)."""
    if not vendor:
        return [], []
    canonical = entity_normalize.resolve_canonical(vendor)
    name = canonical.get("name") if canonical else vendor
    entity_rows = _fetchall("SELECT * FROM entities WHERE name = %(name)s", {"name": name})
    if not entity_rows:
        return [], []
    entity_id = entity_rows[0]["id"]
    edges = _fetchall(
        """
        SELECT ge.*, es.name AS src_name, ed.name AS dst_name
        FROM graph_edges ge
        JOIN entities es ON es.id = ge.src_entity_id
        JOIN entities ed ON ed.id = ge.dst_entity_id
        WHERE ge.src_entity_id = %(id)s OR ge.dst_entity_id = %(id)s
        ORDER BY ge.updated_at DESC
        LIMIT 30
        """,
        {"id": entity_id},
    )
    return entity_rows, edges


def previous_dossier(product_key: str) -> dict[str, Any] | None:
    return _fetchone(
        "SELECT * FROM product_dossiers WHERE product_key = %(key)s ORDER BY created_at DESC LIMIT 1",
        {"key": product_key},
    )


# --------------------------------------------------------------------------
# registry assembly
# --------------------------------------------------------------------------


@dataclass
class CorpusResult:
    product_key: str
    product_name: str
    vendor: str | None
    aliases: list[str]
    terms: list[str]
    items: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    patents: list[dict[str, Any]] = field(default_factory=list)
    tenders: list[dict[str, Any]] = field(default_factory=list)
    forecasts: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    previous: dict[str, Any] | None = None
    registry: list[dict[str, Any]] = field(default_factory=list)

    @property
    def next_n(self) -> int:
        return (max((r.get("n") or 0) for r in self.registry) + 1) if self.registry else 1

    def summary_he(self) -> str:
        """A compact Hebrew context block fed as ``context_he`` to every research-topic
        ``investigate()`` call (``eoa.dossier.plan``) -- what the DB already knows, so a topic's
        investigation is additive rather than starting cold."""
        lines = [
            f"מוצר: {self.product_name}" + (f" | יצרן: {self.vendor}" if self.vendor else ""),
            f"כינויים: {', '.join(self.aliases) or '—'}",
            f"נמצאו במאגר: {len(self.items)} פריטים, {len(self.events)} אירועים עסקיים, "
            f"{len(self.patents)} פטנטים, {len(self.tenders)} מכרזים, {len(self.forecasts)} תחזיות רכש.",
        ]
        if self.previous:
            lines.append(f"קיימת סקירה קודמת של מוצר זה מתאריך {self.previous.get('created_at')}.")
        for r in self.registry[:25]:
            title = r.get("title") or "—"
            lines.append(f"[{r['n']}] ({r['kind']}) {title} | {r.get('source_name') or r.get('url') or '—'}")
        return "\n".join(lines)


def _add_registry_row(registry: list[dict[str, Any]], *, kind: str, **fields: Any) -> dict[str, Any]:
    n = (max((r.get("n") or 0) for r in registry) + 1) if registry else 1
    row = {"n": n, "kind": kind, **fields}
    registry.append(row)
    return row


def build_corpus(
    product_name: str,
    vendor: str | None = None,
    aliases: list[str] | None = None,
    *,
    product_line: str | None = None,
    max_sources: int | None = None,
) -> CorpusResult:
    """Gather the DB's own knowledge of ``product_name``/``vendor``/``aliases`` and seed the
    citation registry from it (items -> events -> patents -> tenders -> forecasts, in that order,
    matching every other report module's flat-numbering convention). ``product_line`` is currently
    only carried through for persistence (``eoa.dossier.report``); it is not itself a search term."""
    aliases = aliases or []
    cap = max_sources or settings().dossier.max_sources
    product_key = slugify_product_key(vendor, product_name)
    terms = _search_terms(product_name, vendor, aliases)

    items = collect_items(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    events = collect_events(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    patents = collect_patents(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    tenders = collect_tenders(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    forecasts = collect_forecasts(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    entities, edges = collect_entities_and_edges(vendor)
    prev = previous_dossier(product_key)

    registry: list[dict[str, Any]] = []
    for it in items:
        if len(registry) >= cap:
            break
        _add_registry_row(
            registry,
            kind="item",
            id=it["id"],
            title=it.get("title"),
            url=it.get("url"),
            source_name=it.get("source_name"),
            published_at=it.get("published_at"),
        )
    for ev in events:
        if len(registry) >= cap:
            break
        _add_registry_row(
            registry,
            kind="event",
            id=ev["id"],
            title=ev.get("title") or ev.get("program"),
            url=ev.get("item_url"),
            source_name=ev.get("source_name"),
            published_at=ev.get("published_at") or ev.get("date"),
        )
    for p in patents:
        if len(registry) >= cap:
            break
        _add_registry_row(
            registry,
            kind="patent",
            id=p["id"],
            title=p.get("title") or p.get("pub_number"),
            url=p.get("url"),
            source_name="patent:" + (p.get("pub_number") or ""),
            published_at=p.get("publication_date") or p.get("filing_date"),
        )
    for t in tenders:
        if len(registry) >= cap:
            break
        _add_registry_row(
            registry,
            kind="tender",
            id=t["id"],
            title=t.get("title"),
            url=t.get("url"),
            source_name=t.get("agency"),
            published_at=t.get("deadline"),
        )
    for f in forecasts:
        if len(registry) >= cap:
            break
        _add_registry_row(
            registry,
            kind="forecast",
            id=f["id"],
            title=f"{f.get('platform') or '—'}: {f.get('payload_need') or '—'}",
            url=None,
            source_name="tender_forecast",
            published_at=f.get("window_from"),
        )
    if prev is not None and len(registry) < cap:
        _add_registry_row(
            registry,
            kind="previous_dossier",
            id=prev["id"],
            title=f"סקירה קודמת של {product_name}",
            url=None,
            source_name="product_dossiers",
            published_at=prev.get("created_at"),
        )

    return CorpusResult(
        product_key=product_key,
        product_name=product_name,
        vendor=vendor,
        aliases=aliases,
        terms=terms,
        items=items,
        events=events,
        patents=patents,
        tenders=tenders,
        forecasts=forecasts,
        entities=entities,
        edges=edges,
        previous=prev,
        registry=registry,
    )


__all__ = [
    "CorpusResult",
    "build_corpus",
    "collect_entities_and_edges",
    "collect_events",
    "collect_forecasts",
    "collect_items",
    "collect_patents",
    "collect_tenders",
    "previous_dossier",
    "slugify_product_key",
]
