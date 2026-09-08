"""MCP server: patent search -- EPO Open Patent Services (OPS) and the USPTO Open Data Portal (ODP)
Patent File Wrapper API (A8, docs/adr/006-mcp-sources.md).

* EPO OPS: OAuth2 client-credentials flow against ``EPO_OPS_KEY``/``EPO_OPS_SECRET`` (confirmed
  live 2026-09-06: ``POST https://ops.epo.org/3.2/auth/accesstoken`` with no credentials returns
  ``401 "Client identifier is required"`` -- the endpoint and auth shape are real; a token is
  cached in-process for its reported lifetime and refreshed on expiry/401).
* USPTO ODP (2026-09-07, replaces PatentsView): ``POST https://api.uspto.gov/api/v1/patent/
  applications/search`` with an ``X-API-KEY`` header. Request/response contract taken from the
  ODP OpenAPI spec the Swagger UI loads (``https://data.uspto.gov/swagger/swagger.yaml`` +
  ``odp-common-base.yaml``, ``PatentSearchRequest``/``PatentDataResponse``/
  ``ApplicationMetaData``/``Assignment`` schemas) and the "API syntax examples" page:

  - request body: ``{"q": <OpenSearch query-string: field:value, AND/OR/NOT, "phrase", wild*,
    [a TO b]>, "filters": [{"name", "value": [...]}], "rangeFilters": [{"field", "valueFrom",
    "valueTo"}], "sort": [{"field", "order": "desc"}], "fields": [...], "pagination":
    {"offset", "limit"}}``;
  - response: ``{"count": int, "patentFileWrapperDataBag": [{"applicationNumberText",
    "applicationMetaData": {"inventionTitle", "filingDate", "grantDate", "patentNumber",
    "earliestPublicationNumber" ("US 2014-0167116 A1"), "earliestPublicationDate",
    "publicationDateBag", "cpcClassificationBag", "firstApplicantName", "applicantBag":
    [{"applicantNameText"}], "inventorBag": [{"inventorNameText"}], ...}, "assignmentBag":
    [{"assigneeBag": [{"assigneeNameText"}], ...}]}]}``;
  - errors: ``404`` = "No matching records found" (an empty result, not a failure), ``413`` =
    response over 6 MB (reduce ``limit``/``fields``), ``429`` = rate limited.

  Verified live 2026-09-07 from this machine *without* a key: an unauthenticated request to the
  search URL returns ``401 {"message": "Unauthorized"}`` and a bogus key ``403 Forbidden``
  (through this project's SSRF-guarded client) -- the host, path and auth gate are
  real. **The authenticated round-trip (query-string field names being searchable as written,
  the normalisation below against real records) is unverified until a ``USPTO_ODP_API_KEY`` is
  registered** (USPTO.gov account + ID.me identity verification; video call for non-US users).

  Rate limits (https://data.uspto.gov/apis/api-rate-limits): 5 M metadata calls/week per key,
  **burst 1 -- no concurrent calls with the same key** (a second in-flight call gets 429),
  4-15 calls/s depending on call type, and USPTO asks for at least a 5 s delay before retrying
  a 429. This module serialises its own ODP calls behind a lock, spaces them
  ``_ODP_MIN_INTERVAL_S`` apart and retries a 429 exactly once after ``_ODP_429_RETRY_DELAY_S``.
  A key unused for 90 days is deleted by USPTO.

* USPTO PatentsView (**retired**): patentsview.org now redirects to the ODP transition guide
  (https://data.uspto.gov/support/transition-guide/patentsview); the PatentSearch API was taken
  down in the March-2026 ODP migration, ``search.patentsview.org`` no longer resolves, and
  PatentsView keys are not valid for ODP. ``PATENTSVIEW_API_KEY``/``PATENTSVIEW_API_BASE`` are
  still *accepted* (a set value logs one deprecation warning per process) but never used;
  :func:`patentsview_search` stays importable and answers ``not_configured`` pointing at
  ``USPTO_ODP_API_KEY``. Until an ODP key exists, US publications are still reachable through EPO
  OPS (Espacenet's worldwide coverage includes US grants and applications).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import threading
import time
from typing import Any

import structlog
from mcp.server.fastmcp import FastMCP

from eoa.mcp_servers._common import http_get_json, http_post_form, http_post_json, json_out, not_configured

log = structlog.get_logger(__name__)
mcp = FastMCP("eoa-patents")

EPO_OPS_TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
EPO_OPS_SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search"

USPTO_ODP_SEARCH_URL = "https://api.uspto.gov/api/v1/patent/applications/search"
#: ODP is a US-only file-wrapper database; every record it returns is a US publication.
USPTO_ODP_COUNTRY = "US"
#: Response fields requested from ODP -- ``applicationMetaData`` carries title/dates/CPC/applicants,
#: ``assignmentBag`` the recorded assignees. Restricting ``fields`` keeps a 100-row page well under
#: ODP's 6 MB response cap (``Status413``).
USPTO_ODP_FIELDS = ["applicationNumberText", "applicationMetaData", "assignmentBag"]
USPTO_ODP_MAX_LIMIT = 100
_ODP_MIN_INTERVAL_S = 0.25  # burst 1, 4-15 req/s: stay comfortably under the lowest tier
_ODP_429_RETRY_DELAY_S = 5.0  # USPTO: never retry a 429 with less than a 5 s delay

_epo_token: str | None = None
_epo_token_expiry: float = 0.0

_odp_lock = threading.Lock()
_odp_last_call_at: float = 0.0
_patentsview_deprecation_logged = False


def _sleep(seconds: float) -> None:  # indirection so tests can stub the throttle
    time.sleep(seconds)


def _odp_api_key() -> str:
    return os.environ.get("USPTO_ODP_API_KEY", "").strip()


def _warn_patentsview_deprecated_once() -> None:
    """``PATENTSVIEW_API_KEY``/``PATENTSVIEW_API_BASE`` are accepted but dead: log once per process
    so an operator who still has them in ``.env`` learns they buy nothing, without log spam."""
    global _patentsview_deprecation_logged
    if _patentsview_deprecation_logged:
        return
    if os.environ.get("PATENTSVIEW_API_KEY") or os.environ.get("PATENTSVIEW_API_BASE"):
        _patentsview_deprecation_logged = True
        log.warning(
            "patentsview_deprecated",
            message=(
                "PATENTSVIEW_API_KEY/PATENTSVIEW_API_BASE are ignored: PatentsView was retired in the "
                "USPTO Open Data Portal migration (March 2026). Set USPTO_ODP_API_KEY instead."
            ),
        )


@mcp.tool()
def ping() -> str:
    """Liveness check: reports whether each provider's credentials are configured (no network call)."""
    _warn_patentsview_deprecated_once()
    return json_out(
        {
            "epo_ops_configured": bool(os.environ.get("EPO_OPS_KEY") and os.environ.get("EPO_OPS_SECRET")),
            "uspto_odp_configured": bool(_odp_api_key()),
            "patentsview_configured": False,  # retired provider; kept so older readers see an honest False
        }
    )


# --------------------------------------------------------------------------
# EPO OPS
# --------------------------------------------------------------------------


def _epo_token_value() -> str | None:
    global _epo_token, _epo_token_expiry
    key, secret = os.environ.get("EPO_OPS_KEY", ""), os.environ.get("EPO_OPS_SECRET", "")
    if not key or not secret:
        return None
    if _epo_token and time.monotonic() < _epo_token_expiry:
        return _epo_token
    basic = base64.b64encode(f"{key}:{secret}".encode()).decode()
    resp = http_post_form(
        EPO_OPS_TOKEN_URL,
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
    )
    # EPO OPS returns XML on auth errors and JSON is not guaranteed by this endpoint; _common's
    # http_post_form still tries json() first and falls back to raw text, handled below.
    data = resp.get("json")
    if resp["status"] != 200 or not isinstance(data, dict) or "access_token" not in data:
        return None
    _epo_token = str(data["access_token"])
    _epo_token_expiry = time.monotonic() + max(60, int(data.get("expires_in", 1200)) - 30)
    return _epo_token


# --------------------------------------------------------------------------
# EPO OPS throttling (X-Throttling-Control) -- verified live 2026-09-08: a real response header
# looks like ``"busy (images=green:100, inpadoc=green:45, other=green:1000, retrieval=green:100,
# search=green:15)"`` -- one ``service=color:value`` triple per OPS-internal service. OPS's own
# fair-use escalation ladder is green (no restriction) -> yellow/amber (approaching the per-minute
# cap) -> red (very close) -> black (blocked for a period OPS does not itself disclose in the
# header). This module tracks the most-recently-seen color per service and adds a short courtesy
# delay before the *next* call to that service when it was not green -- a best-effort avoidance of
# predictably tripping the real limit, not a substitute for it: the authoritative enforcement is
# still whatever HTTP status/quota headers OPS itself returns, and this module never loops or
# retries on its own account.
# --------------------------------------------------------------------------

_THROTTLE_SERVICE_RE = re.compile(r"(\w+)=(\w+):(\d+)")
_THROTTLE_BACKOFF_S = {"green": 0.0, "yellow": 2.0, "amber": 2.0, "red": 8.0, "black": 60.0}

_epo_throttle_lock = threading.Lock()
_epo_throttle_state: dict[str, str] = {}  # service name -> last-seen color


def _parse_throttling_control(header_value: str) -> dict[str, str]:
    """``"busy (search=green:15, retrieval=green:100, ...)"`` -> ``{"search": "green", ...}``.
    Tolerant of a missing/malformed header -- returns ``{}`` rather than raising."""
    return {m.group(1): m.group(2) for m in _THROTTLE_SERVICE_RE.finditer(header_value or "")}


def _record_throttle_state(headers: dict[str, str] | None) -> None:
    """Update the module's last-seen-color-per-service from one response's headers (case-insensitive
    key lookup -- ``httpx`` normalizes to lower-case, but a test-supplied fixture may not)."""
    if not headers:
        return
    raw = headers.get("x-throttling-control") or headers.get("X-Throttling-Control")
    if not raw:
        return
    with _epo_throttle_lock:
        _epo_throttle_state.update(_parse_throttling_control(raw))


def _epo_throttle_wait(service: str) -> None:
    with _epo_throttle_lock:
        color = _epo_throttle_state.get(service, "green")
    delay = _THROTTLE_BACKOFF_S.get(color, 0.0)
    if delay:
        log.debug("epo_ops_throttled", service=service, color=color, delay_s=delay)
        _sleep(delay)


@mcp.tool()
def epo_ops_search(query: str, limit: int = 20) -> str:
    """EPO Open Patent Services published-data search (CQL query syntax). Requires
    ``EPO_OPS_KEY``/``EPO_OPS_SECRET`` (OAuth2 client-credentials app, free registration at
    ops.epo.org). Prefer :func:`build_epo_query` over hand-writing ``query`` -- CQL has a few
    live-verified quoting quirks (see that function's docstring): a quoted phrase whose value
    contains a hyphen (``ti=\"electro-optical\"``) 404s as "no results" even when the identical
    *unquoted* term (``ti=electro-optical``) matches real documents, while an *unquoted* multi-word
    value (``pa=elbit systems``) silently drops every word after the first rather than erroring.
    Known-good field prefixes: ``pa=`` (applicant), ``ti=``/``ab=``/``ta=`` (title / abstract /
    title+abstract combined), ``cpc=``/``ipc=`` (classification prefix, case-insensitive),
    ``and``/``or``/parenthesised groups. A syntactically valid query with zero matches and a
    malformed-query 404 are indistinguishable in OPS's own response (both
    ``SERVER.EntityNotFound``/"No results found") -- this function reports either as
    ``{"total_result_count": 0, "results": []}``, never as an error, since a real caller cannot
    tell them apart from the response alone either."""
    token = _epo_token_value()
    if token is None:
        return not_configured("EPO_OPS_KEY", "EPO_OPS_SECRET")
    _epo_throttle_wait("search")
    resp = http_get_json(
        EPO_OPS_SEARCH_URL,
        params={"q": query, "Range": f"1-{max(1, min(limit, 100))}"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        include_headers=True,
    )
    _record_throttle_state(resp.get("headers"))
    if resp["status"] == 404:
        return json_out({"total_result_count": 0, "results": []})
    if resp["status"] != 200:
        return json_out({"error": f"EPO OPS returned HTTP {resp['status']}", "body": resp.get("text")})
    return json_out(_extract_epo_results(resp["json"] or {}))


def _extract_epo_results(data: dict[str, Any]) -> dict[str, Any]:
    """EPO OPS's JSON is deeply nested (`ops:world-patent-data` -> ... ); pull out the handful of
    fields this project cares about, tolerant of a missing/renamed branch (returns an empty list
    rather than raising) since the exact shape is unverified live from this sandbox.

    Each result carries the raw ``country``/``doc_number``/``kind`` triple plus the same
    ``pub_number``/``url`` keys :func:`uspto_odp_search` emits, so both providers' rows can be
    consumed by one reader (``eoa.patents.scan``)."""
    try:
        biblio = data["ops:world-patent-data"]["ops:biblio-search"]
        total = int(biblio.get("@total-result-count", 0))
        results = biblio.get("ops:search-result", {}).get("ops:publication-reference", [])
        if isinstance(results, dict):
            results = [results]
        docs = []
        for r in results:
            doc_id = r.get("document-id", {})
            country = doc_id.get("country", {}).get("$")
            doc_number = doc_id.get("doc-number", {}).get("$")
            kind = doc_id.get("kind", {}).get("$")
            pub_number = f"{country or ''}{doc_number}{kind or ''}" if doc_number else None
            docs.append(
                {
                    "country": country,
                    "doc_number": doc_number,
                    "kind": kind,
                    "pub_number": pub_number,
                    "url": _google_patents_url(pub_number),
                }
            )
        return {"total_result_count": total, "results": docs}
    except (KeyError, TypeError):
        return {"total_result_count": None, "results": [], "raw": data}


# --------------------------------------------------------------------------
# EPO OPS CQL query construction -- live-verified 2026-09-08 against applicant "Elbit Systems" x
# keywords (electro-optical, payload, gimbal, infrared); see docs/qa/content_review/PATENTS-OPS.md
# for the full verification session. Findings that drive :func:`_cql_term`/:func:`build_epo_query`:
#
#   pa="Elbit Systems" and ti=optical    -> 200, 42 hits  (multi-word applicant phrase, quoted)
#   pa=elbit and ta=infrared             -> 200, 9 hits   (single word, unquoted)
#   pa=elbit and ta=electro-optical      -> 200, 2 hits   (hyphenated compound, left UNQUOTED)
#   pa=elbit and ti="electro-optical"    -> 404 "No results found" -- quoting a hyphenated value is
#                                            a real OPS CQL parser quirk, not a genuine zero-match:
#                                            the identical term unquoted (previous row) matches.
#                                            This is the task brief's "naive query returned No
#                                            results" bug, isolated to its precise trigger.
#   pa=elbit systems and ta=infrared     -> 200, 9 hits   -- the SAME total as `pa=elbit` alone: an
#                                            unquoted multi-word value silently drops every word
#                                            after the first instead of erroring, so this form
#                                            looks like it "works" while quietly ignoring "systems".
#   pa=elbit and ta=(electro-optical or gimbal or payload)  -> 200, 6 hits (parenthesised OR group)
#   pa=elbit and cpc=G02B27              -> 200, 97 hits  (bare class/subclass/main-group prefix,
#                                            case-insensitive; ipc= behaves the same way)
#
# Net rule: quote a value if and only if it is multi-word AND contains no hyphen; a hyphenated
# value is always left bare, regardless of word count.
# --------------------------------------------------------------------------


def _cql_term(value: str) -> str:
    """One CQL field value, quoted/unquoted per the live-verified rule above. A value containing a
    hyphen is always left unquoted (quoting it 404s as a spurious "No results"); a non-hyphenated
    multi-word value is always quoted as an exact phrase (left unquoted, OPS silently drops every
    word after the first); a single word needs no quoting either way."""
    value = value.strip()
    if not value or "-" in value:
        return value
    if " " in value:
        return f'"{value}"'
    return value


def build_epo_query(*, applicant: str = "", keywords: list[str] | None = None, cpc: str = "", field: str = "ta") -> str:
    """Build one CQL query string for :func:`epo_ops_search` from structured parts, applying
    :func:`_cql_term`'s quoting rule to every value so a caller never has to hand-write CQL (or
    rediscover its quoting quirks). ``applicant`` -> ``pa=``; ``keywords`` -> ``field=`` (default
    ``ta``, title+abstract combined) -- OR-ed together in one parenthesised group when there is more
    than one; ``cpc`` -> ``cpc=`` (a class/subclass/main-group prefix, non-alphanumeric characters
    stripped, upper-cased). Every part is optional and simply omitted when empty; present parts are
    AND-ed. Returns ``""`` if nothing was supplied (never a bare ``"and and"``)."""
    clauses: list[str] = []
    if applicant.strip():
        clauses.append(f"pa={_cql_term(applicant)}")
    kw_terms = [_cql_term(k) for k in (keywords or []) if k and k.strip()]
    if len(kw_terms) == 1:
        clauses.append(f"{field}={kw_terms[0]}")
    elif kw_terms:
        clauses.append(f"{field}=({' or '.join(kw_terms)})")
    if cpc.strip():
        code = re.sub(r"[^A-Za-z0-9/]", "", cpc).upper()
        clauses.append(f"cpc={code}")
    return " and ".join(clauses)


# --------------------------------------------------------------------------
# EPO OPS biblio (per-publication title/abstract/applicants/CPC/IPC/dates/family) -- verified live
# 2026-09-08 against US2024220012A1 (docs/qa/content_review/PATENTS-OPS.md carries the full
# response). The task brief's ask ("fetch biblio for the top hits") needs a second call per
# publication beyond the search endpoint, which only returns bare country/doc-number/kind refs.
# --------------------------------------------------------------------------

EPO_OPS_BIBLIO_URL_TMPL = (
    "https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/{cc}.{num}.{kind}/biblio"
)

#: A compact pub_number like "US2024220012A1" -> ("US", "2024220012", "A1"). Verified live
#: 2026-09-08: the *docdb* number form (dot-separated country.doc-number.kind) reliably resolves on
#: this endpoint; the same number in bare ``epodoc`` form with its kind letter glued on
#: (``US20250199318A1``) 404s even though the docdb form of the identical publication
#: (``US.20250199318.A1``) returns 200 -- so this module always builds the docdb form.
_PUB_NUMBER_RE = re.compile(r"^([A-Za-z]{2})(\d+)([A-Za-z]\d*)?$")


def _pub_number_to_docdb(pub_number: str) -> tuple[str, str, str] | None:
    m = _PUB_NUMBER_RE.match((pub_number or "").strip().replace(" ", ""))
    if not m:
        return None
    country, num, kind = m.group(1).upper(), m.group(2), (m.group(3) or "").upper()
    return country, num, kind or "A"  # OPS's docdb path segment always needs *some* kind; "A" is
    # the common default for a number that was stored/typed without one.


def _as_list(value: Any) -> list[Any]:
    """OPS's XML->JSON conversion collapses a single child to a bare object and multiple children
    to a list -- this normalizes either shape (and ``None``) to a list, so every reader below can
    iterate uniformly."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ops_text(node: Any) -> str | None:
    """OPS's ``{"$": "value", ...attrs}`` leaf shape -> the plain string (``None`` if absent/blank).
    Also accepts a bare string, for callers that already unwrapped one level."""
    if isinstance(node, dict):
        v = node.get("$")
        return str(v).strip() if v not in (None, "") else None
    if isinstance(node, str):
        return node.strip() or None
    return None


def _ops_date(node: Any) -> dt.date | None:
    raw = _ops_text(node)
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return dt.date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def _extract_party_names(party_block: dict[str, Any] | None, role_key: str, name_key: str) -> list[str]:
    """``parties.applicants.applicant`` (or ``.inventors.inventor``) -> deduped display names
    (case-insensitively), preferring the ``@data-format == "epodoc"`` entries -- OPS's own
    normalized form (e.g. ``"ELBIT SYSTEMS LTD [IL]"``) -- and falling back to whatever is present
    when no epodoc-format entry exists."""
    if not party_block:
        return []
    entries = [e for e in _as_list(party_block.get(role_key)) if isinstance(e, dict)]
    epodoc = [e for e in entries if e.get("@data-format") == "epodoc"]
    chosen = epodoc or entries
    out: list[str] = []
    seen: set[str] = set()
    for e in chosen:
        name = _ops_text((e.get(name_key) or {}).get("name"))
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def _extract_cpc_codes(bib: dict[str, Any]) -> list[str]:
    """``patent-classifications.patent-classification`` -> compact ``"G02B27/0093"``-style codes
    (section+class+subclass+main-group, ``/``subgroup), deduped, order preserved."""
    entries = _as_list((bib.get("patent-classifications") or {}).get("patent-classification"))
    out: list[str] = []
    seen: set[str] = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        section = _ops_text(e.get("section")) or ""
        cls = _ops_text(e.get("class")) or ""
        subclass = _ops_text(e.get("subclass")) or ""
        main = _ops_text(e.get("main-group")) or ""
        sub = _ops_text(e.get("subgroup")) or ""
        if not (section and cls and subclass and main):
            continue
        code = f"{section}{cls}{subclass}{main}" + (f"/{sub}" if sub else "")
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


_IPC_COMPACT_RE = re.compile(r"^([A-Z]\d{2}[A-Z]\d+/\d+)")


def _extract_ipc_codes(bib: dict[str, Any]) -> list[str]:
    """``classifications-ipcr.classification-ipcr`` -- OPS gives raw fixed-width IPC text (e.g.
    ``"G02B  27/    01            A I"``); collapsed to the compact ``"G02B27/01"`` form."""
    entries = _as_list((bib.get("classifications-ipcr") or {}).get("classification-ipcr"))
    out: list[str] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        raw = _ops_text(e.get("text"))
        if not raw:
            continue
        compact = re.sub(r"\s+", "", raw)
        m = _IPC_COMPACT_RE.match(compact)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def _extract_title(bib: dict[str, Any]) -> str:
    """``invention-title`` -- a bare object for one language, a list for several. Prefers English,
    falls back to the first language present."""
    entries = [t for t in _as_list(bib.get("invention-title")) if isinstance(t, dict)]
    for t in entries:
        if t.get("@lang") == "en":
            return _ops_text(t) or ""
    for t in entries:
        val = _ops_text(t)
        if val:
            return val
    return ""


def _extract_abstract(doc: dict[str, Any]) -> str:
    """``abstract`` -- a bare object for one language, a list for several; each carries one or more
    ``p`` paragraphs. Prefers English, falls back to the first language present, joins multiple
    paragraphs with a single space."""

    def _paragraphs(a: dict[str, Any]) -> str:
        parts = [_ops_text(p) for p in _as_list(a.get("p"))]
        return " ".join(p for p in parts if p)

    entries = [a for a in _as_list(doc.get("abstract")) if isinstance(a, dict)]
    for a in entries:
        if a.get("@lang") == "en":
            text = _paragraphs(a)
            if text:
                return text
    for a in entries:
        text = _paragraphs(a)
        if text:
            return text
    return ""


def _extract_epo_biblio(data: dict[str, Any], *, fallback_pub_number: str = "") -> dict[str, Any]:
    """One EPO OPS biblio response (:func:`epo_ops_biblio`) -> the provider-neutral row shape
    ``eoa.patents.scan`` turns into a :class:`PatentRecord` -- deliberately the same key set
    :func:`normalize_odp_record` emits, plus EPO-native ``applicants``/``family_id``/``ipc``, so a
    caller can treat an EPO- and an ODP-sourced biblio record almost interchangeably. Tolerant of a
    missing/renamed branch (returns an all-empty shape rather than raising) -- the exact shape is
    deeply nested and only verified against the handful of live documents this project has actually
    fetched (docs/qa/content_review/PATENTS-OPS.md)."""
    empty = {
        "pub_number": fallback_pub_number or None,
        "kind": None,
        "country": None,
        "title": "",
        "abstract": "",
        "applicants": [],
        "assignees": [],
        "inventors": [],
        "cpc": [],
        "ipc": [],
        "family_id": None,
        "application_number": None,
        "priority_date": None,
        "filing_date": None,
        "publication_date": None,
        "url": None,
    }
    try:
        raw_doc = data["ops:world-patent-data"]["exchange-documents"]["exchange-document"]
    except (KeyError, TypeError):
        return empty
    docs = [d for d in _as_list(raw_doc) if isinstance(d, dict)]
    if not docs:
        return empty
    doc = docs[0]

    country = doc.get("@country")
    doc_number = doc.get("@doc-number")
    kind = doc.get("@kind")
    family_id = doc.get("@family-id")
    pub_number = (f"{country or ''}{doc_number or ''}{kind or ''}" or "").strip() or fallback_pub_number or None

    bib = doc.get("bibliographic-data") or {}
    if not isinstance(bib, dict):
        bib = {}
    parties = bib.get("parties") or {}
    applicants = _extract_party_names(parties.get("applicants"), "applicant", "applicant-name")
    inventors = _extract_party_names(parties.get("inventors"), "inventor", "inventor-name")

    publication_date = None
    for d in _as_list((bib.get("publication-reference") or {}).get("document-id")):
        if isinstance(d, dict) and d.get("@document-id-type") == "docdb":
            publication_date = _ops_date(d.get("date"))
            break

    application_number = None
    filing_date = None
    app_doc_ids = [d for d in _as_list((bib.get("application-reference") or {}).get("document-id")) if isinstance(d, dict)]
    for d in app_doc_ids:
        if d.get("@document-id-type") == "epodoc":
            application_number = _ops_text(d.get("doc-number"))
            filing_date = _ops_date(d.get("date"))
            break
    if application_number is None and app_doc_ids:
        application_number = _ops_text(app_doc_ids[0].get("doc-number"))
        filing_date = filing_date or _ops_date(app_doc_ids[0].get("date"))

    priority_date = None
    for claim in _as_list((bib.get("priority-claims") or {}).get("priority-claim")):
        if not isinstance(claim, dict):
            continue
        for d in _as_list(claim.get("document-id")):
            if not isinstance(d, dict):
                continue
            candidate = _ops_date(d.get("date"))
            if candidate and (priority_date is None or candidate < priority_date):
                priority_date = candidate

    return {
        "pub_number": pub_number,
        "kind": kind,
        "country": country,
        "title": _extract_title(bib),
        "abstract": _extract_abstract(doc),
        "applicants": applicants,
        # EPO OPS's "parties" model has no separate assignee concept -- the applicant is the
        # closest OPS equivalent -- so it is also exposed under "assignees" (matching
        # normalize_odp_record's key) for a caller that reads either provider's rows uniformly.
        "assignees": applicants,
        "inventors": inventors,
        "cpc": _extract_cpc_codes(bib),
        "ipc": _extract_ipc_codes(bib),
        "family_id": family_id,
        "application_number": application_number,
        "priority_date": priority_date,
        "filing_date": filing_date,
        "publication_date": publication_date,
        "url": _google_patents_url(pub_number) if pub_number else None,
    }


@mcp.tool()
def epo_ops_biblio(pub_number: str) -> str:
    """EPO OPS bibliographic detail for one publication (``pub_number`` in compact form, e.g.
    ``"US2024220012A1"`` -- the same shape :func:`epo_ops_search`'s own results and
    :func:`uspto_odp_search`'s ``pub_number`` use). Returns ``{"pub_number", "kind", "country",
    "title", "abstract", "applicants", "assignees", "inventors", "cpc", "ipc", "family_id",
    "application_number", "priority_date", "filing_date", "publication_date", "url"}`` -- any field
    the response did not carry is ``None``/``[]``/``""``, never guessed. Requires
    ``EPO_OPS_KEY``/``EPO_OPS_SECRET``."""
    token = _epo_token_value()
    if token is None:
        return not_configured("EPO_OPS_KEY", "EPO_OPS_SECRET")
    parsed = _pub_number_to_docdb(pub_number)
    if parsed is None:
        return json_out({"error": f"unrecognized pub_number format: {pub_number!r}"})
    country, num, kind = parsed
    url = EPO_OPS_BIBLIO_URL_TMPL.format(cc=country, num=num, kind=kind)
    _epo_throttle_wait("retrieval")
    resp = http_get_json(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        include_headers=True,
    )
    _record_throttle_state(resp.get("headers"))
    if resp["status"] == 404:
        return json_out({"error": "not_found", "pub_number": pub_number})
    if resp["status"] != 200:
        return json_out({"error": f"EPO OPS returned HTTP {resp['status']}", "body": resp.get("text")})
    return json_out(_extract_epo_biblio(resp["json"] or {}, fallback_pub_number=pub_number))


# --------------------------------------------------------------------------
# USPTO Open Data Portal (Patent File Wrapper API)
# --------------------------------------------------------------------------

_ODP_QUERY_SPECIALS = re.compile(r'["\\]')


def _odp_phrase(value: str) -> str:
    """Quote ``value`` as an exact phrase for the OpenSearch query-string ``q``; the only characters
    that can break out of a quoted phrase are the quote and the backslash, so those are dropped
    (a patent title/assignee never legitimately needs them)."""
    return '"' + _ODP_QUERY_SPECIALS.sub("", value).strip() + '"'


def _odp_cpc_clause(cpc: str) -> str:
    """``cpcClassificationBag`` holds full group codes (``H01L29/66325``); a caller normally passes
    a prefix (section/class/subclass/main group, e.g. ``G01S`` or ``H04N5/33``), so the clause is a
    trailing-wildcard match. Anything outside the CPC alphabet is stripped rather than escaped."""
    code = re.sub(r"[^A-Za-z0-9/]", "", cpc).upper()
    return f"applicationMetaData.cpcClassificationBag:{code}*"


def build_odp_search_body(
    query: str,
    *,
    assignee: str = "",
    cpc: str = "",
    date_from: str = "",
    limit: int = 20,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """The ``PatentSearchRequest`` JSON body for one search (pure; unit-tested separately).

    ``query`` is passed through as the free-text/field-syntax ``q`` (ODP's simplified syntax:
    bare words search every searchable field; ``field:value``, ``AND``/``OR``/``NOT``,
    ``"phrases"``, ``wild*`` and ``[a TO b]`` ranges are all accepted). ``assignee`` matches the
    applicant (post-AIA the applicant is usually the owning company) *or* a recorded assignee;
    ``cpc`` is a trailing-wildcard CPC prefix; ``date_from`` (ISO date) keeps records whose
    earliest publication or grant date falls in ``[date_from TO today]`` -- two closed ranges
    OR-ed in ``q`` rather than a ``rangeFilters`` entry because ``rangeFilters`` entries are
    AND-ed and a granted-without-PGPub record has no publication date at all.
    """
    today = today or dt.date.today()
    clauses: list[str] = []
    query = query.strip()
    if query:
        clauses.append(f"({query})")
    if assignee.strip():
        name = _odp_phrase(assignee)
        clauses.append(
            "(applicationMetaData.firstApplicantName:"
            f"{name} OR applicationMetaData.applicantBag.applicantNameText:{name}"
            f" OR assignmentBag.assigneeBag.assigneeNameText:{name})"
        )
    if cpc.strip():
        clauses.append(_odp_cpc_clause(cpc))
    if date_from.strip():
        start = dt.date.fromisoformat(date_from.strip()[:10]).isoformat()  # ValueError -> caller
        end = today.isoformat()
        clauses.append(
            f"(applicationMetaData.earliestPublicationDate:[{start} TO {end}]"
            f" OR applicationMetaData.grantDate:[{start} TO {end}])"
        )
    return {
        "q": " AND ".join(clauses),
        "fields": list(USPTO_ODP_FIELDS),
        "pagination": {"offset": 0, "limit": max(1, min(int(limit), USPTO_ODP_MAX_LIMIT))},
        "sort": [{"field": "applicationMetaData.filingDate", "order": "desc"}],
    }


_PUB_NUMBER_JUNK = re.compile(r"[\s\-/,.]")


def _normalize_pub_number(raw: str | None) -> str | None:
    """``"US 2014-0167116 A1"`` (ODP's ``earliestPublicationNumber`` format) -> ``"US20140167116A1"``
    -- the compact form Google Patents/Espacenet use and ``patents.pub_number`` stores."""
    if not raw:
        return None
    compact = _PUB_NUMBER_JUNK.sub("", str(raw)).upper()
    return compact or None


def _google_patents_url(pub_number: str | None) -> str | None:
    return f"https://patents.google.com/patent/{pub_number}/en" if pub_number else None


def _first_date(*candidates: Any) -> str | None:
    """First candidate that parses as an ISO date (``publicationDateBag`` entries are lists)."""
    for c in candidates:
        values = c if isinstance(c, list) else [c]
        for v in values:
            if not v:
                continue
            try:
                return dt.date.fromisoformat(str(v)[:10]).isoformat()
            except ValueError:
                continue
    return None


def _dedupe_names(names: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for n in names:
        s = str(n or "").strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    return out


def normalize_odp_record(item: dict[str, Any]) -> dict[str, Any] | None:
    """One ``patentFileWrapperDataBag`` entry -> the provider-neutral row ``eoa.patents.scan``
    turns into a ``PatentRecord``. Returns ``None`` when the entry carries no usable identifier.

    ``pub_number`` preference: the granted ``patentNumber`` (``US<n>``, kind unknown -- ODP does not
    say B1 vs B2), else the compacted ``earliestPublicationNumber`` (``US20140167116A1``), else
    the bare application number (``US<application>``, ``url`` then points at Patent Center rather
    than Google Patents since an unpublished application has no publication page).
    ``assignees`` = applicants first (the owning company for post-AIA filings), then recorded
    assignees, deduped case-insensitively.
    """
    meta = item.get("applicationMetaData") or {}
    if not isinstance(meta, dict):
        meta = {}
    app_no = str(item.get("applicationNumberText") or "").strip() or None
    patent_number = str(meta.get("patentNumber") or "").strip() or None
    earliest_pub = _normalize_pub_number(meta.get("earliestPublicationNumber"))
    kind: str | None = None
    if patent_number:
        pub_number = f"{USPTO_ODP_COUNTRY}{patent_number}"
        url = _google_patents_url(pub_number)
    elif earliest_pub:
        pub_number = earliest_pub
        m = re.search(r"([A-Z]\d?)$", earliest_pub)
        kind = m.group(1) if m else None
        url = _google_patents_url(pub_number)
    elif app_no:
        pub_number = f"{USPTO_ODP_COUNTRY}{app_no}"
        url = f"https://patentcenter.uspto.gov/applications/{app_no}"
    else:
        return None

    applicants = [a.get("applicantNameText") for a in meta.get("applicantBag") or [] if isinstance(a, dict)]
    if not applicants and meta.get("firstApplicantName"):
        applicants = [meta.get("firstApplicantName")]
    assignees_recorded: list[Any] = []
    for assignment in item.get("assignmentBag") or []:
        if not isinstance(assignment, dict):
            continue
        for a in assignment.get("assigneeBag") or []:
            if isinstance(a, dict):
                assignees_recorded.append(a.get("assigneeNameText"))
    inventors = [i.get("inventorNameText") for i in meta.get("inventorBag") or [] if isinstance(i, dict)]
    cpc = [str(c).strip() for c in meta.get("cpcClassificationBag") or [] if str(c or "").strip()]

    grant_date = _first_date(meta.get("grantDate"))
    publication_date = _first_date(
        meta.get("earliestPublicationDate"), meta.get("publicationDateBag"), meta.get("grantDate")
    )
    return {
        "pub_number": pub_number,
        "kind": kind,
        "country": USPTO_ODP_COUNTRY,
        "application_number": app_no,
        "patent_number": patent_number,
        "earliest_publication_number": earliest_pub,
        "title": str(meta.get("inventionTitle") or "").strip(),
        "assignees": _dedupe_names(applicants + assignees_recorded),
        "inventors": _dedupe_names(inventors),
        "cpc": cpc,
        "filing_date": _first_date(meta.get("filingDate")),
        "publication_date": publication_date,
        "grant_date": grant_date,
        "status": str(meta.get("applicationStatusDescriptionText") or "").strip() or None,
        "url": url,
    }


def _odp_post(body: dict[str, Any], api_key: str) -> dict[str, Any]:
    """One rate-limited ODP call: serialised per process (ODP burst limit is 1 per key), spaced
    ``_ODP_MIN_INTERVAL_S`` apart, and a 429 retried exactly once after the 5 s USPTO asks for."""
    global _odp_last_call_at
    headers = {"X-API-KEY": api_key}
    with _odp_lock:
        resp: dict[str, Any] = {"status": 0, "json": None, "text": None}
        for attempt in (0, 1):
            wait = _ODP_MIN_INTERVAL_S - (time.monotonic() - _odp_last_call_at)
            if wait > 0:
                _sleep(wait)
            _odp_last_call_at = time.monotonic()
            resp = http_post_json(USPTO_ODP_SEARCH_URL, json_body=body, headers=headers)
            if resp["status"] != 429 or attempt == 1:
                break
            log.warning("uspto_odp_rate_limited", retry_in_s=_ODP_429_RETRY_DELAY_S)
            _sleep(_ODP_429_RETRY_DELAY_S)
        return resp


@mcp.tool()
def uspto_odp_search(
    query: str, assignee: str = "", cpc: str = "", date_from: str = "", limit: int = 20
) -> str:
    """USPTO Open Data Portal (Patent File Wrapper) search over US applications and grants.

    ``query``: free text or ODP field syntax (``applicationMetaData.inventionTitle:infrared*``,
    ``"night vision" AND applicationMetaData.applicationTypeLabelName:Utility``). ``assignee``:
    applicant/assignee organisation. ``cpc``: CPC prefix (``G01S``, ``H04N5/33``). ``date_from``:
    ISO date; keeps records published or granted since then. ``limit``: 1-100.

    Requires ``USPTO_ODP_API_KEY`` (USPTO.gov account linked to an ID.me identity; one key per
    account, deleted after 90 days unused). Returns ``{"total_count", "results": [{"pub_number",
    "kind", "title", "assignees", "inventors", "cpc", "filing_date", "publication_date",
    "grant_date", "url", ...}]}`` -- the same row shape ``epo_ops_search`` emits."""
    _warn_patentsview_deprecated_once()
    api_key = _odp_api_key()
    if not api_key:
        return not_configured("USPTO_ODP_API_KEY")
    try:
        body = build_odp_search_body(query, assignee=assignee, cpc=cpc, date_from=date_from, limit=limit)
    except ValueError as exc:
        return json_out({"error": f"invalid date_from: {exc}"})
    resp = _odp_post(body, api_key)
    status = resp["status"]
    if status == 404:  # ODP: "No matching records found, refine your search criteria" -- not a failure
        return json_out({"total_count": 0, "results": [], "query": body["q"]})
    if status in (401, 403):
        return json_out(
            {
                "error": f"USPTO ODP rejected the API key (HTTP {status})",
                "hint": (
                    "check USPTO_ODP_API_KEY at https://data.uspto.gov (Manage API Key); "
                    "keys unused for 90 days are deleted"
                ),
            }
        )
    if status == 429:
        return json_out(
            {"error": "USPTO ODP rate limit (HTTP 429) -- one call at a time per key; retry after 5 s"}
        )
    if status != 200:
        return json_out({"error": f"USPTO ODP returned HTTP {status}", "body": resp.get("text")})
    data = resp["json"] or {}
    if not isinstance(data, dict):
        return json_out({"error": "USPTO ODP returned a non-object JSON body"})
    items = data.get("patentFileWrapperDataBag") or []
    results = [row for row in (normalize_odp_record(i) for i in items if isinstance(i, dict)) if row]
    count = data.get("count")
    return json_out(
        {
            "total_count": int(count) if isinstance(count, int) else len(results),
            "results": results,
            "query": body["q"],
        }
    )


# --------------------------------------------------------------------------
# PatentsView (retired) -- kept importable so older callers degrade honestly
# --------------------------------------------------------------------------


def patentsview_search(query: str, assignee: str = "", limit: int = 20) -> str:
    """**Deprecated, always ``not_configured``.** USPTO PatentsView was retired in the ODP
    migration (see the module docstring); use :func:`uspto_odp_search`. No longer registered as an
    MCP tool -- kept as a plain function so any remaining importer keeps working."""
    _warn_patentsview_deprecated_once()
    payload = json.loads(not_configured("USPTO_ODP_API_KEY"))
    payload["message"] += " (PatentsView is retired; PATENTSVIEW_API_KEY is ignored -- use uspto_odp_search)"
    return json_out(payload)


if __name__ == "__main__":
    mcp.run(transport="stdio")
