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


@mcp.tool()
def epo_ops_search(query: str, limit: int = 20) -> str:
    """EPO Open Patent Services published-data search (CQL query syntax, e.g.
    ``'ti=\"night vision\" AND pd within \"2023-2026\"'``). Requires ``EPO_OPS_KEY``/
    ``EPO_OPS_SECRET`` (OAuth2 client-credentials app, free registration at ops.epo.org)."""
    token = _epo_token_value()
    if token is None:
        return not_configured("EPO_OPS_KEY", "EPO_OPS_SECRET")
    resp = http_get_json(
        EPO_OPS_SEARCH_URL,
        params={"q": query, "Range": f"1-{max(1, min(limit, 100))}"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
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
