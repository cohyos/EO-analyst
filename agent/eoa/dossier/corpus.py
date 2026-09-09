"""Stage 1 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.1): gather everything the DB already holds
for one product's name/vendor/aliases -- items, events, patents, tenders/forecasts, entities +
graph edges, and the previous dossier of the same ``product_key`` -- and build the citation
registry's DB half from it (numbered ``n``, same flat-registry convention as
``eoa.patents.survey``/``eoa.report.product_line``: every existing DB record gets a number first;
``eoa.dossier.plan`` continues that same numbering sequence for the web sources it reads).

No LLM calls here. Every collector except one is pure SQL + deterministic Python -- the one
exception is :func:`collect_patents_ops` (2026-09-08): a live EPO OPS/USPTO ODP (else keyless
Google Patents fallback) query per product, added because :func:`collect_patents` alone only reads
whatever the periodic watch-topic/assignee scan (``eoa.patents.scan``, ``config/patents.yaml``)
already happened to put in the ``patents`` table -- a product that scan never surfaced (e.g. Elbit
Systems' SPECTRO XR) otherwise gets an empty patents section. :func:`build_corpus` calls it by
default; pass ``include_live_patents_ops=False`` for the old, fully offline/dry-run behavior (a
network-touching HTTP call happening during "prove the queries work" is exactly what that flag is
for) -- keeping the task brief's live-dry-run promise available, just no longer the unconditional
default.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings
from eoa.db import connection
from eoa.pipeline import entity_normalize, text_match

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


#: PD-vocab-extract (2026-09-09): the real implementation moved to eoa.pipeline.text_match (a
#: shared, non-private location -- eoa.dossier.vocabulary's synonym matcher reuses it too, see that
#: module's own docstring for why importing a `_`-prefixed name across packages was never an
#: option). Re-exported under the original private name so every call site in this file (and this
#: module's own public surface, unchanged) keeps working verbatim.
_word_present = text_match.word_present


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


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): a patent row must actually be about THIS product/vendor, not just
# a generic-keyword ILIKE hit. The live SPECTRO XR dossier's patents section had 8 rows -- every one
# a generic "pod"/"target" hit with an EMPTY assignee, and the extraction model's own `relevance_he`
# already admitted "אין אישור במקורות לקשר" (no confirmed link in the sources) for them, yet nothing
# upstream ever dropped them. Rule (applies to both the DB-table match, :func:`collect_patents`, and
# the live OPS/keyless-fallback query, :func:`collect_patents_ops` -- one shared gate, so neither
# path can reintroduce the same defect): a row with NO assignee at all is dropped outright; among
# rows that DO carry an assignee, it is kept only when that assignee matches the vendor/an alias
# (substring either way, on the same normalized key ``eoa.pipeline.entity_normalize`` uses
# everywhere else) OR the product name itself (whole word) appears in the patent's own title/
# abstract. :func:`build_corpus` applies this once, after merging both patent sources, and caps the
# survivors at :data:`_PATENT_RELEVANCE_CAP`, assignee-matched rows ranked ahead of product-name-only
# matches (a stable sort, so each group keeps its own publication_date-DESC order).
# --------------------------------------------------------------------------

_PATENT_RELEVANCE_CAP = 15


def patent_assignee_matches_vendor(
    assignees: list[str] | None, vendor: str | None, aliases: list[str]
) -> str | None:
    """The first assignee that matches the vendor or an alias (substring either way on the
    normalized-name key), or ``None`` when none do. ``assignees`` empty/``None`` always -> ``None``
    (an empty assignee is never treated as a match by omission)."""
    if not assignees:
        return None
    candidates = [c for c in ([vendor, *aliases]) if c and c.strip()]
    if not candidates:
        return None
    candidate_keys = [(c, entity_normalize.normalize_name_key(c)) for c in candidates]
    for a in assignees:
        a_key = entity_normalize.normalize_name_key(a)
        if not a_key:
            continue
        for _c, c_key in candidate_keys:
            if c_key and (c_key in a_key or a_key in c_key):
                return a
    return None


def patent_relevance_he(
    *,
    assignees: list[str] | None,
    title: str | None,
    source_text: str,
    product_name: str,
    vendor: str | None,
    aliases: list[str],
) -> str | None:
    """The one concrete, checkable reason to keep this patent row -- or ``None`` when neither check
    succeeds (caller drops the row). Deliberately states only the grounded link found, never a
    paraphrase/summary of the patent itself -- exactly what item 1 asks the replacement
    ``relevance_he`` to do.

    ``assignees`` empty/``None`` is an absolute gate -- ``None`` unconditionally, never falling
    through to the product-name-in-title check below -- per the rule's own explicit "rows with
    empty assignee are dropped" clause (the live SPECTRO XR defect: 8 generic keyword hits, every
    one with an empty assignee; a title/abstract match alone is not trusted as a substitute for
    SOME structured attribution existing on the row at all)."""
    if not assignees:
        return None
    matched_assignee = patent_assignee_matches_vendor(assignees, vendor, aliases)
    if matched_assignee:
        vendor_label = vendor or (aliases[0] if aliases else matched_assignee)
        return f"מבקש/בעל הפטנט ({matched_assignee}) תואם ליצרן {vendor_label}."
    text = f"{title or ''} {source_text or ''}"
    if product_name and _word_present(text, product_name):
        return f"שם המוצר '{product_name}' מופיע בכותרת/בתקציר הפטנט."
    return None


def _patent_is_relevant(
    row: dict[str, Any], *, product_name: str, vendor: str | None, aliases: list[str]
) -> bool:
    return (
        patent_relevance_he(
            assignees=row.get("assignees"),
            title=row.get("title"),
            source_text=row.get("abstract") or "",
            product_name=product_name,
            vendor=vendor,
            aliases=aliases,
        )
        is not None
    )


def _filter_and_cap_patents(
    patents: list[dict[str, Any]], *, product_name: str, vendor: str | None, aliases: list[str]
) -> list[dict[str, Any]]:
    relevant = [
        p for p in patents if _patent_is_relevant(p, product_name=product_name, vendor=vendor, aliases=aliases)
    ]
    # Stable sort: assignee-matched rows (rank 0) ahead of product-name-only matches (rank 1); each
    # rank group keeps whatever order it already had (both collect_patents/collect_patents_ops emit
    # publication_date DESC).
    ranked = sorted(
        relevant,
        key=lambda p: 0 if patent_assignee_matches_vendor(p.get("assignees"), vendor, aliases) else 1,
    )
    return ranked[:_PATENT_RELEVANCE_CAP]


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


#: Cap on how many OPS/ODP (or keyless-fallback) records one dossier's live patent query gathers
#: before upserting -- kept small since this runs once per dossier build, on the product's live
#: request path, not a batch job.
_PATENTS_OPS_LIMIT = 10


def collect_patents_ops(
    product_name: str,
    *,
    vendor: str | None = None,
    aliases: list[str] | None = None,
    product_line: str | None = None,
    limit: int = _PATENTS_OPS_LIMIT,
) -> list[dict[str, Any]]:
    """Live EPO OPS/USPTO ODP (else the keyless Google Patents fallback) query for this product's
    own patents -- ``pa=<applicant>`` (vendor, falling back to the first alias when no vendor is
    given) AND'd with the product name + ``product_line`` as keywords
    (``eoa.patents.scan.search_records_for_applicant``) -- upserted into the ``patents`` table
    (real ``id``s, the same gather -> upsert -> id flow ``eoa.patents.survey.build_patent_survey``
    already uses) and returned in the exact row shape :func:`collect_patents` emits, so
    ``build_corpus`` can merge the two lists (pre-scanned DB match + this live query) into one
    ``patents`` section.

    Added 2026-09-08 (user finding): a product whose own patents were never sitting in the
    periodically-scanned ``patents`` table -- e.g. Elbit Systems' SPECTRO XR, whose
    ``product_dossiers`` row had ``data->'patents'`` completely empty because
    ``config/patents.yaml``'s watch topics/assignee list never happened to surface it -- otherwise
    gets an empty patents section from :func:`collect_patents` alone, since that function only ever
    reads what a *different*, earlier scan already put in the DB. This function is the dossier's
    own, product-scoped patent search, run at dossier-build time.

    Grounded by construction: every returned row's ``pub_number``/``title``/``assignees``/``cpc``
    is exactly what the live OPS/ODP record (or Google Patents search hit) carried -- nothing here
    is generated or inferred beyond the existing, already-reviewed assignee-safe matching
    (``eoa.pipeline.entity_normalize.find_watchlist_company_names_in_text``, CR-patents/CR-patents-2
    text-grounding rules) the shared ``eoa.patents.scan`` gather path already applies to a
    keyless-fallback hit. A network/DB failure at any step is caught and logged -- returns ``[]``
    rather than ever failing the dossier build (docs/CONVENTIONS.md rule 9), same discipline as
    every other DB-touching helper in this module."""
    applicant = (vendor or "").strip() or next((a for a in (aliases or []) if a and a.strip()), "")
    if not applicant or not product_name.strip():
        return []
    keywords = [k for k in (product_name, product_line) if k and k.strip()]
    if not keywords:
        return []
    from eoa.patents.scan import search_records_for_applicant, upsert_records

    try:
        records = search_records_for_applicant(applicant, keywords, limit=limit)
    except Exception as exc:
        log.warning("dossier_patents_ops_query_failed", product_name=product_name, error=str(exc)[:200])
        return []
    if not records:
        return []
    try:
        ids_by_pub = upsert_records(records)
    except Exception as exc:
        log.warning("dossier_patents_ops_upsert_failed", product_name=product_name, error=str(exc)[:200])
        return []
    ids = list(ids_by_pub.values())
    if not ids:
        return []
    return _fetchall(
        """
        SELECT id, pub_number, title, abstract, assignees, cpc, publication_date, filing_date, url, value_score
        FROM patents
        WHERE id = ANY(%(ids)s)
        ORDER BY publication_date DESC NULLS LAST, id DESC
        """,
        {"ids": ids},
    )


def collect_tenders(
    terms: list[str],
    *,
    product_name: str = "",
    vendor: str | None = None,
    aliases: list[str] | None = None,
    limit: int = _TENDERS_LIMIT,
) -> list[dict[str, Any]]:
    """TENDERS-SAM (2026-09-08, docs/qa/content_review/TENDERS-SAM.md item 3): restricted to
    ``intake = 'accepted'`` -- the same relevance-gate philosophy already applied to the daily/
    weekly report's own tender table (``eoa.tenders.report_section.collect_tenders``'s
    ``"status = 'open' AND intake = 'accepted'"`` clause). A dossier's corpus is a citation source
    for the reader, exactly like a report -- a ``'candidate'`` row (below the self-tuning relevance
    threshold, or demoted by ``eoa.tenders.scan``'s negative-keyword/defence-context check) is an
    unconfirmed lead, not something to cite as fact in a product dossier either. The whole-word
    alias precision filter below (``_filter_precise``, PD-fix 2026-09-08) is unaffected -- this is
    an independent, additive restriction on top of it."""
    if not terms:
        return []
    clause = _ilike_any_clause(
        "(COALESCE(title, '') || ' ' || COALESCE(summary_he, ''))", len(terms), param_prefix="t"
    )
    params: dict[str, Any] = {**_term_params(terms, prefix="t"), "limit": limit}
    rows = _fetchall(
        f"""
        SELECT id, title, agency, country, deadline, status, url, summary_he, relevance_score, intake
        FROM tenders
        WHERE intake = 'accepted' AND ({clause})
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
    #: PD-vocab-extract (2026-09-09): carried through from build_corpus's own `product_line` param
    #: (previously used only for OPS-patent keywords + persistence -- eoa.dossier.report) so
    #: eoa.dossier.extract/vocabulary can resolve this run's effective specification vocabulary
    #: (eoa.dossier.vocabulary.effective_vocabulary) without report.py needing to thread a new
    #: parameter through build_dossier's own call signature.
    product_line: str | None = None
    # -- LESSONS-1 (PD-datasheet, 2026-09-09): populated by eoa.dossier.plan.run_plan (the network-
    # touching stage), not by build_corpus itself -- see docs/qa/content_review/LESSONS-1.md for the
    # full interface this hands off to the LESSONS-2 (extract/report) lane.
    #: eoa.dossier.datasheet.hunt_datasheets's own results, one per PDF/product-page actually read:
    #: {"url", "title", "text", "pages", "kind", "n"} -- "n" is the citation registry number
    #: run_plan assigned it (same row also lives in `registry`, kind="web", source_kind="datasheet").
    datasheets: list[dict[str, Any]] = field(default_factory=list)
    #: eoa.dossier.programs.parse_programme_deals's own results, one row per platform+monetary
    #: sentence found: {"platform", "customer", "date", "amount_text", "amount_value", "currency",
    #: "cites", "note_he", "component_of_package": True}.
    programme_deals: list[dict[str, Any]] = field(default_factory=list)
    #: The named competitor products (config/product_lines.yaml's `competitor_products`, resolved
    #: for this run's own `product_line`) the "competitors" topic was explicitly pointed at:
    #: [{"name", "vendor"}, ...] -- see eoa.dossier.plan._resolve_competitor_seeds.
    competitor_seeds: list[dict[str, Any]] = field(default_factory=list)
    #: eoa.dossier.gaps.gap_status's own output for every gap that got a follow-up topic this run:
    #: [{"gap", "status": "closed"|"open", "cites"}, ...]. Never carries a "new"-status row (see
    #: eoa.dossier.gaps's own module docstring for why that classification is out of this lane's
    #: reach) -- the LESSONS-2 lane merges eoa.dossier.gaps.diff_new_gaps's own output in alongside
    #: this before persisting `data.meta.gaps` for the next run.
    gap_status: list[dict[str, Any]] = field(default_factory=list)

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
    include_live_patents_ops: bool = True,
) -> CorpusResult:
    """Gather the DB's own knowledge of ``product_name``/``vendor``/``aliases`` and seed the
    citation registry from it (items -> events -> patents -> tenders -> forecasts, in that order,
    matching every other report module's flat-numbering convention). ``product_line`` feeds
    :func:`collect_patents_ops`'s keyword set (in addition to being carried through for persistence,
    ``eoa.dossier.report``) -- otherwise not itself a search term. ``include_live_patents_ops``
    (default ``True``): whether to also run :func:`collect_patents_ops`'s live EPO OPS/USPTO
    ODP/Google-Patents-fallback query for this product, merged into ``patents`` (deduped by
    ``pub_number``, the DB-table match from :func:`collect_patents` always wins on a collision)
    before the citation registry is built. Pass ``False`` to keep this stage fully offline (the old
    behavior, still exercised by the task brief's "prove the queries work" dry run)."""
    aliases = aliases or []
    cap = max_sources or settings().dossier.max_sources
    product_key = slugify_product_key(vendor, product_name)
    terms = _search_terms(product_name, vendor, aliases)

    items = collect_items(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    events = collect_events(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    patents = collect_patents(terms, product_name=product_name, vendor=vendor, aliases=aliases)
    if include_live_patents_ops:
        seen_pub_numbers = {p.get("pub_number") for p in patents}
        ops_patents = collect_patents_ops(
            product_name, vendor=vendor, aliases=aliases, product_line=product_line
        )
        for p in ops_patents:
            pub_number = p.get("pub_number")
            if pub_number and pub_number not in seen_pub_numbers:
                seen_pub_numbers.add(pub_number)
                patents.append(p)
    # PD-fix-3 item 1: one shared relevance gate over the merged DB-table + live-OPS patent list --
    # drops any row with no assignee at all, and any row whose assignee doesn't match the vendor/an
    # alias AND whose title/abstract doesn't literally name the product -- then caps the survivors,
    # assignee-matched rows ranked first. See `_filter_and_cap_patents`'s own docstring.
    patents = _filter_and_cap_patents(patents, product_name=product_name, vendor=vendor, aliases=aliases)
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
        product_line=product_line,
    )


__all__ = [
    "CorpusResult",
    "build_corpus",
    "collect_entities_and_edges",
    "collect_events",
    "collect_forecasts",
    "collect_items",
    "collect_patents",
    "collect_patents_ops",
    "collect_tenders",
    "previous_dossier",
    "slugify_product_key",
]
