"""Stage: tender / RFI / RFP ingestion (section 5.2 / FR-5.2).

For each source in ``config/tenders.yaml``: fetch (``eoa.fetch.remote.fetch_raw_remote`` for
``api_json``/``rss`` sources needing the raw body; ``eoa.search.searxng_client`` for ``kind:
search`` sources, denylist-filtered) -> parse into ``NoticeRaw`` rows -> a **two-signal gate**
(``_passes_gate``: at least one PROCUREMENT-context signal -- explicit for ``search``/``rss``
sources, implicit for a structured procurement-portal ``api_json`` source like TED/Contracts
Finder -- AND at least one EO/IR/CV DOMAIN signal; server-side keyword params are unreliable, see
config/tenders.yaml notes, so this is always re-applied client-side) -> dedupe by a
globally-unique ``external_ref`` (``"<source_id>:<notice id>"``) -> LLM classification
(``chat_structured``, DATA-guarded, budget-capped, lessons-augmented -- see
``eoa.tenders.feedback.tender_lessons_text``) -> a **hard rejection gate**
(``_gate_reject_reason``) -> **open intake** (W2b, docs/REVIEW_2026-09-06_evening.md, user
requirement 2026-09-06 18:55, verbatim: "be open -- and through the relevance feedback given to
each tender, the system tunes itself").

W2b superseded the old F24 "strict quality gate" (2026-09-06 morning): that gate rejected outright
-- never inserted, no DB row at all -- on a low LLM relevance verdict, an unrecognised
``notice_type``, a passed ``deadline``, a stale ``published_at``, or an unverified+undated snippet.
The very next round's own audit (docs/MODULES.md "Round 4 discovery" W2) found that gate silently
dropping genuine, on-topic Navy sources-sought notices whenever a trusted tracker domain (GovTribe,
SAM.gov, ...) 403'd the page fetch and left the LLM with nothing but a content-free teaser -- a
correct LLM verdict on thin evidence, but the wrong system response (discard, not "flag as
uncertain"). The user's own explicit follow-up requirement replaces "drop it" with "store it,
carrying a relevance signal the operator's own feedback can correct": :func:`_gate_reject_reason`
now rejects outright (never inserted, ``tender_rejected`` logged with a reason) only on the four
HARD cases no amount of relevance feedback should ever override -- an explicit awarded/closed
``status_hint``, a denylisted document-hosting/aggregator domain (Scribd, DocPlayer, ...), a dead
link (no URL at all to show/verify), or an exact duplicate (checked earlier, via ``_tender_exists``,
before this gate ever runs). Everything else that clears the two-signal vocabulary gate is stored,
with:

- ``tenders.relevance_score`` (0-1): ``extract.relevance / 10`` when the LLM actually classified
  the notice, or ``0.5`` (neutral -- "unknown, don't presume either way") when the LLM was
  deferred/unavailable/failed. Never itself a rejection reason.
- ``tenders.intake``: ``'accepted'`` when ``relevance_score`` already met the current *learned*
  threshold (``eoa.tenders.feedback.get_relevance_threshold``, starts at 0.6) at insert time, else
  ``'candidate'`` -- and later ``'rejected-by-user'``/``'accepted'`` once an operator gives explicit
  👎/👍 feedback (``eoa.tenders.feedback.record_feedback``, which also recomputes the learned
  threshold and per-source scan priority -- see that module).
- ``status`` is computed exactly as before (``_initial_status``: deadline/notice_type/publish-date
  rubric) -- intake and status are orthogonal; a low-relevance ``'candidate'`` can still be
  ``status='open'`` if its dates say so, it just won't appear in the accepted-only daily/BD report
  section until feedback (or the self-tuning threshold) promotes it.

Source scan order is nudged (never gated) by :func:`eoa.tenders.feedback.get_source_priorities` --
a source whose last 20 stored notices drew feedback but never a single 👍 is scanned later in the
pass, never skipped.

Finally transitions any ``status='open'`` row whose ``deadline`` has passed, or whose
``published_at`` is stale (>365 days) with no deadline at all, to ``status='closed'`` (see
``_initial_status``/``_transition_closed``, F2 2026-09-05: an undated notice is never assumed to
stay open forever), and archives any ``status='closed'`` row whose ``deadline`` passed more than 30
days ago (``_archive_stale_closed``, F24 2026-09-06).

``kind: html`` sources in config/tenders.yaml are documented but intentionally not scraped here
(see the notes on each entry -- bot-protected or client-hydrated pages); ``kind: api_json`` sources
marked ``verified: false`` are skipped the same way. Both are covered instead by a sibling
``kind: search`` source that runs through the already-verified, keyless SearXNG client.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode, urlparse

import structlog
import yaml
from dateutil import parser as date_parser
from psycopg.types.json import Json
from pydantic import BaseModel, Field

from eoa.config import CONFIG_DIR
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.fetch.remote import fetch_raw_remote, fetch_remote
from eoa.fetch.rss import parse_feed
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.tenders import TenderExtract
from eoa.memory.relational import insert_item, update_item_fields
from eoa.product_lines.tagging import tag_product_lines
from eoa.search.provider import SearchHit, search
from eoa.tenders.feedback import get_relevance_threshold, get_source_priorities, tender_lessons_text

log = structlog.get_logger(__name__)

TENDERS_YAML = CONFIG_DIR / "tenders.yaml"

# Fallback if a config entry/file somehow omits these (every entry in config/tenders.yaml merges
# the shared `&kw` anchor and the file carries top-level `procurement_signals`/`deny_domains`, so
# these should never actually trigger outside of ad-hoc test fixtures).
DEFAULT_KEYWORDS = [
    "electro-optical",
    "infrared",
    "thermal imaging",
    "thermal imager",
    "FLIR",
    "targeting pod",
    "EO/IR",
    "gimbal",
    "laser rangefinder",
    "laser designator",
    "seeker",
    "counter-UAS",
    "C-UAS",
    "surveillance camera",
    "night vision",
    "image intensifier",
    "hyperspectral",
    "optronic",
    "computer vision",
    "ATR",
]

DEFAULT_PROCUREMENT_SIGNALS = [
    "tender",
    "RFP",
    "RFI",
    "RFQ",
    "solicitation",
    "sources sought",
    "invitation to tender",
    "contract notice",
    "prior information notice",
    "request for proposal",
    "request for information",
    "request for quotation",
    "call for proposals",
    "מכרז",
    "בקשה למידע",
    "בקשת מידע",
]

DEFAULT_DENY_DOMAINS = [
    "wikipedia.org",
    "reddit.com",
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "linkedin.com",
    "quora.com",
    "medium.com",
    "britannica.com",
    "nasa.gov",
    "marketsandmarkets.com",
    "grandviewresearch.com",
    "alliedmarketresearch.com",
    "researchandmarkets.com",
    "globenewswire.com",
    "prnewswire.com",
    "businesswire.com",
    "techradar.com",
    "sciencenotes.org",
    "scienceinsights.org",
    "coolcosmos.ipac.caltech.edu",
    "howstuffworks.com",
    "thoughtco.com",
    "investopedia.com",
    # F24: document-hosting/aggregator sites -- see config/tenders.yaml's deny_domains comment.
    "scribd.com",
    "docplayer.net",
    "docplayer.com",
    "yumpu.com",
    "slideshare.net",
    "pdfcoffee.com",
    "coursehero.com",
]

MAX_KEYWORDS_PER_API_SOURCE = 5

# R7-tenders (round-6 D9 finding 2, docs/qa/loop/round_6_judge.md): a deterministic CPV
# (Common Procurement Vocabulary, the EU/OCDS classification code every TED/Contracts-Finder/OCDS
# notice carries -- see ``_parse_ted_notices``/``_parse_contracts_finder``/``_parse_generic_ocds``
# below) pre-filter, layered on top of the pre-existing keyword two-signal gate. Candidate id 34
# ("Expert / Coach Transformatie en Contracten Juridisch", Gemeente Rotterdam -- a Dutch legal/HR
# consulting contract) is the concrete case this targets: it slipped past the keyword gate on a
# spurious substring match (the acronym keyword "ATR" matching inside the unrelated Dutch word
# "priva-ATR-echtelijke" -- fixed separately by the word-boundary check in
# :func:`_matches_keywords`), but a CPV-aware source carrying an explicit non-defence
# classification code (this one had none -- TenderNed's API doesn't expose CPV at all in the
# shape probed 2026-09-06) would clear that keyword bug entirely on the CPV signal alone. Two
# lists, both matched by CPV *prefix* (a CPV code's leading digits name its broad family; e.g.
# "79100000" (legal services) and "79000000" (business services) both start with "79"):
#
# - ``DEFAULT_CPV_ALLOW_PREFIXES``: known defence/security/optronics families -- 35 (security,
#   firefighting, police, defence equipment -- includes 35700000 electronic defence systems), 38
#   (laboratory, optical and precision equipment -- includes 38600000 optical instruments,
#   38630000 astronomical/optical instruments, 38651000 photographic equipment, 38127000 infrared
#   detection). A code in this family is never rejected by the CPV pre-filter regardless of the
#   deny list below (an EO/IR-coded notice always wins).
# - ``DEFAULT_CPV_DENY_PREFIXES``: obvious non-defence families the user's finding named
#   explicitly -- 79 (business services, which subsumes 79100000-79140000 legal services and
#   79600000-79635000 recruitment/HR services), plus the adjacent families a coach/consulting/HR
#   notice like candidate 34 would actually carry: 80 (education/training services), 85 (health
#   and social work services), 98 (other community/personal/household services).
#
# Only applied via :func:`_cpv_gate_reject_reason` when the notice actually carries CPV code(s)
# (``NoticeRaw.cpv_naics``, populated by the structured parsers above) AND every one of them falls
# in the deny list with none in the allow list -- a notice with no CPV data at all (every source
# except TED/Contracts Finder/OCDS-shaped ones, and TenderNed as probed) is completely unaffected,
# same as before this fix; a notice with a mixed/ambiguous CPV set (one deny-family code alongside
# one allow-family code) is never rejected on CPV alone either -- the allow signal wins.
DEFAULT_CPV_ALLOW_PREFIXES = ["35", "38"]
DEFAULT_CPV_DENY_PREFIXES = ["79", "80", "85", "98"]

# TENDERS-SAM (2026-09-08, docs/qa/content_review/TENDERS-SAM.md): fallback if config/tenders.yaml
# somehow omits `negative_keywords`/`defence_context_signals` -- see that file's own comment above
# these two lists for the full rationale. Kept short here (the real, actively-maintained lists live
# in the YAML); these fallbacks only matter for a test fixture or a stripped-down config.
DEFAULT_NEGATIVE_KEYWORDS = [
    "spectroscopy",
    "laboratory reagent",
    "veterinary",
    "office supplies",
    "janitorial",
    "food service",
    "recruitment services",
]
DEFAULT_DEFENCE_CONTEXT_SIGNALS = [
    "military",
    "defense",
    "defence",
    "army",
    "navy",
    "air force",
    "DoD",
    "NATO",
]

# R7-tenders: keywords this short are prone to matching as a substring *inside* an unrelated word
# (candidate id 34's "ATR" matching inside the Dutch "privaatrechtelijke") -- see
# :func:`_matches_keywords`'s word-boundary handling below. Deliberately conservative (<=4 chars,
# alphanumeric-only): a longer phrase ("computer vision", "seeker") keeps plain substring matching
# so a genuine plural ("seekers") still matches, which is the majority of the existing keyword
# vocabulary and every existing scan test's expectation.
_SHORT_KEYWORD_MAX_LEN = 4

# RELEVANCE_MIN_ACCEPT / VALID_NOTICE_TYPES: no longer a hard rejection floor (see the module
# docstring's W2b section -- a notice below this is stored as intake='candidate', not dropped).
# Still meaningful as the deterministic-minimum relevance the thin-snippet rescue grants a rescued
# trusted-tracker notice (_rescue_thin_snippet_from_trusted_tracker), and as the set of notice types
# that function will infer. NOTICE_MAX_AGE_DAYS is no longer gate-enforced either (an old notice is
# now stored like any other, its age reflected only in relevance_score/status, never a rejection) --
# kept as documented policy context for anything that wants to flag "old" without rejecting it.
RELEVANCE_MIN_ACCEPT = 6
NOTICE_MAX_AGE_DAYS = 90
VALID_NOTICE_TYPES = frozenset({"rfi", "rfp", "rfq", "sources_sought", "tender"})


# --------------------------------------------------------------------------
# source config
# --------------------------------------------------------------------------


class TenderSource(BaseModel):
    """One ``config/tenders.yaml`` entry, validated."""

    id: str
    name: str
    kind: Literal["api_json", "rss", "html", "search"]
    country: str
    url: str | None = None
    method: str = "GET"
    query_template: str | None = None
    query_params: dict[str, str] | None = None
    queries: list[str] | None = None
    engine_lang: str = "en"
    keywords: list[str] = Field(default_factory=list)
    # A15: ``keywords`` above does double duty everywhere else -- it's both the per-source DOMAIN
    # vocabulary used by the two-signal gate (``_matches_keywords``) AND, for ``kind: api_json``
    # sources, the list of terms rotated into the API query itself (``_collect_source_notices``).
    # That coupling breaks for a source whose API query terms are NOT human-readable domain phrases
    # -- e.g. ``ted_eu_cpv``'s CPV classification codes ("38620000") -- since a raw code string will
    # never literally appear in a notice's title/summary text, so using it as the gate's keyword
    # list too would silently reject every single notice the source ever returns. ``None`` (the
    # default) means "no override, keep using ``keywords`` for the query loop too" -- every
    # existing source is unaffected; only ``ted_eu_cpv`` sets this.
    api_query_keywords: list[str] | None = None
    parse_hints: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    verified_at: str | None = None
    notes: str | None = None
    # A15 (docs/TENDER_PORTALS.md): set only on an unverified source whose live probe showed the
    # portal itself is reachable/parseable but gated behind an API key/subscription this repo does
    # not have (e.g. SAM.gov's Opportunities API v2, Doffin's Ocp-Apim-Subscription-Key) -- lets
    # eoa.api.services.tender_source_coverage tell "waiting for a key" apart from "blocked/
    # not integrated for another reason" (bot-protection, wrong endpoint, deliberately out of
    # scope, ...) without fragile text-matching over `notes`. Never used by scan_tenders itself.
    needs_key_env_var: str | None = None
    # R7-tenders-b: per-source page/pace tuning for a `kind: api_json` source (finding 1: TED's
    # date-filtered/sorted query needs to page past a single response's `limit` to actually reach
    # recent notices; finding 2: UK Contracts Finder/Find a Tender 429'd live 2026-09-07 under this
    # repo's keyword-rotation loop, which had no inter-request pacing at all). Every
    # manually-constructed TenderSource in the existing test suites (which never set these) and
    # every config/tenders.yaml entry not touched this round gets `max_pages=1` (a single request,
    # exactly the pre-round behaviour) and `pace_seconds=0.0` (`time.sleep(0.0)` is a real no-op --
    # zero added latency for a source that was never rate-limited) -- a config entry opts into
    # paging/pacing explicitly, nothing changes silently underneath an existing source.
    max_pages: int = 1
    pace_seconds: float = 0.0
    # R7-tenders-b (finding 1): 0 (the default) means "no {since_date} substitution -- exactly the
    # pre-round query, every source except ted_eu/ted_eu_cpv". A positive value tells
    # `_fetch_api_json` to embed a `PD>=<today - N days>` clause (TED expert-query syntax,
    # confirmed live 2026-09-07: `FT ~ "x" AND PD>=20260801` -> HTTP 200) via the template's own
    # `{since_date}` placeholder. Deliberately a static per-source config value, NOT the caller's
    # own `since_days` (`scan_tenders(since_days=...)`) -- it only needs to be a generous buffer
    # comfortably wider than every `since_days` this repo actually calls scan_tenders with (3 in
    # production, up to 14 for a backfill), narrowing TED's full-text search down from its entire
    # archive (~2016-present, the round's own diagnosed root cause) without threading since_days
    # through `_fetch_api_json`'s signature -- that function is monkeypatched by name with a
    # `(src, keyword)`-only stub in existing tests (tests/unit/test_tenders_scan.py's
    # TestCollectSourceNoticesApiQueryKeywordsOverride), so its outward signature must stay exactly
    # as it was.
    query_lookback_days: int = 0


def _load_yaml(path: str | Path | None) -> dict[str, Any]:
    file_path = Path(path) if path is not None else TENDERS_YAML
    with file_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_tender_sources(path: str | Path | None = None) -> list[TenderSource]:
    """Load and validate every entry in ``config/tenders.yaml`` (or an alternate ``path``)."""
    raw = _load_yaml(path)
    sources = [TenderSource.model_validate(e) for e in raw.get("sources", [])]
    log.debug("tenders.sources_loaded", count=len(sources), path=str(path or TENDERS_YAML))
    return sources


def load_procurement_signals(path: str | Path | None = None) -> list[str]:
    """Top-level ``procurement_signals`` list from ``config/tenders.yaml`` -- words/phrases whose
    presence marks a notice as being *about* a solicitation/tender process (as opposed to e.g. a
    general-interest article that merely mentions a domain term)."""
    raw = _load_yaml(path)
    return list(raw.get("procurement_signals") or DEFAULT_PROCUREMENT_SIGNALS)


def load_deny_domains(path: str | Path | None = None) -> list[str]:
    """Top-level ``deny_domains`` list -- hosts (matched by exact domain or subdomain) never
    treated as a tender/RFI/RFP lead regardless of keyword matches (Wikipedia, Reddit, market-
    research-report publishers, ...): defense-in-depth against a generic web search returning
    content that happens to mention a domain term without being a procurement source at all."""
    raw = _load_yaml(path)
    return list(raw.get("deny_domains") or DEFAULT_DENY_DOMAINS)


def load_cpv_allow_prefixes(path: str | Path | None = None) -> list[str]:
    """R7-tenders: top-level ``cpv_allow_prefixes`` -- CPV code prefixes that are always treated
    as on-topic defence/optronics families (see ``DEFAULT_CPV_ALLOW_PREFIXES``'s docstring)."""
    raw = _load_yaml(path)
    return list(raw.get("cpv_allow_prefixes") or DEFAULT_CPV_ALLOW_PREFIXES)


def load_cpv_deny_prefixes(path: str | Path | None = None) -> list[str]:
    """R7-tenders: top-level ``cpv_deny_prefixes`` -- CPV code prefixes that name an obviously
    non-defence family (business/legal/HR consulting, education, health/social work, other
    community services -- see ``DEFAULT_CPV_DENY_PREFIXES``'s docstring)."""
    raw = _load_yaml(path)
    return list(raw.get("cpv_deny_prefixes") or DEFAULT_CPV_DENY_PREFIXES)


def load_negative_keywords(path: str | Path | None = None) -> list[str]:
    """TENDERS-SAM: top-level ``negative_keywords`` -- off-topic-domain phrases (medical,
    laboratory, office/facilities services, ...) that demote (never outright reject, per W2b)
    ``tenders.relevance_score`` when present without a countervailing ``defence_context_signals``
    term -- see ``_negative_keyword_penalty``."""
    raw = _load_yaml(path)
    return list(raw.get("negative_keywords") or DEFAULT_NEGATIVE_KEYWORDS)


def load_defence_context_signals(path: str | Path | None = None) -> list[str]:
    """TENDERS-SAM: top-level ``defence_context_signals`` -- broad military/security-context
    vocabulary that overrides a ``negative_keywords`` hit (see ``_has_defence_context``): a notice
    mentioning both an off-topic word AND a defence-context word is not demoted."""
    raw = _load_yaml(path)
    return list(raw.get("defence_context_signals") or DEFAULT_DEFENCE_CONTEXT_SIGNALS)


# --------------------------------------------------------------------------
# NoticeRaw + parsing
# --------------------------------------------------------------------------


@dataclass
class NoticeRaw:
    """One parsed notice, before the gate / DB insertion."""

    source_id: str
    external_ref: str
    title: str
    summary: str = ""
    agency: str | None = None
    country: str | None = None
    published_at: dt.date | None = None
    deadline: dt.date | None = None
    url: str | None = None
    cpv_naics: list[str] = field(default_factory=list)
    status_hint: str | None = None  # 'open' | 'closed' | 'awarded' | None (unknown -> derived)
    raw: dict[str, Any] = field(default_factory=dict)


def _dig(obj: Any, path: str) -> Any:
    """Walk a dotted path (``"tender.title"``) through nested dicts; ``None`` on any miss."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _parse_date(raw: Any) -> dt.date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    s = str(raw)
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        pass
    try:
        return date_parser.parse(s, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _parse_ted_notices(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """TED v3 ``/notices/search`` response: ``{"notices": [{"ND", "TI": {lang: title}, "PD", "links"}]}``."""
    out: list[NoticeRaw] = []
    for n in payload.get("notices") or []:
        nd = n.get("ND") or n.get("publication-number")
        if not nd:
            continue
        ti = n.get("TI")
        title = ""
        if isinstance(ti, dict):
            title = ti.get("eng") or next((v for v in ti.values() if v), "")
        elif isinstance(ti, str):
            title = ti
        url = _dig(n, "links.htmlDirect.ENG") or _dig(n, "links.html.ENG")
        # R7-tenders: TED's own field name for its CPV classification, confirmed live 2026-09-07
        # (only returned when explicitly requested in the query's `fields` list -- see
        # config/tenders.yaml's ted_eu/ted_eu_cpv `query_template`). A list of code strings
        # (e.g. ``["38600000"]``); absent/empty leaves ``cpv_naics`` at its default ``[]``, which
        # the CPV pre-filter (``_cpv_gate_reject_reason``) treats as "no signal, don't reject".
        cpv = n.get("classification-cpv") or []
        # R7-tenders-b (finding 1): TED's own field name for a notice's submission deadline
        # (confirmed live 2026-09-07, only returned when explicitly requested in the query's
        # `fields` list -- see config/tenders.yaml's ted_eu/ted_eu_cpv `query_template`) is
        # `deadline-receipt-request`, a *list* of per-lot ISO datetimes (almost always one entry
        # for these single-lot-heavy defence/optronics notices) -- the first is used as the
        # notice's own deadline, the same "good enough, not authoritative" treatment every other
        # source's own `deadline_field` parse_hint already gets. Absent/empty leaves `deadline` at
        # its default `None` (never itself a rejection -- W2b open intake is unchanged); now also
        # feeds `_within_window`'s new "deadline already passed" check downstream.
        deadline_raw = n.get("deadline-receipt-request")
        if isinstance(deadline_raw, list):
            deadline_raw = deadline_raw[0] if deadline_raw else None
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{nd}",
                title=title or f"TED notice {nd}",
                country=src.country,
                published_at=_parse_date(n.get("PD")),
                deadline=_parse_date(deadline_raw),
                url=url,
                cpv_naics=list(cpv) if isinstance(cpv, list) else [str(cpv)],
                raw=n,
            )
        )
    return out


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _parse_contracts_finder(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """UK Contracts Finder OCDS response: ``{"releases": [{"ocid", "tender": {...}, "buyer": {...}}]}``."""
    out: list[NoticeRaw] = []
    for r in payload.get("releases") or []:
        ocid = r.get("ocid") or r.get("id")
        if not ocid:
            continue
        tags = r.get("tag") or []
        status_hint = "awarded" if "award" in tags else ("closed" if "tenderCancellation" in tags else None)
        m = _UUID_RE.search(str(r.get("id") or ocid))
        url = f"https://www.contractsfinder.service.gov.uk/Notice/{m.group(0)}" if m else None
        # R7-tenders: OCDS's classification lives at `tender.classification.id` with
        # `tender.classification.scheme == "CPV"` (confirmed live 2026-09-07) -- a single code
        # string, not a list, unlike TED; wrapped in a list here so ``NoticeRaw.cpv_naics`` has one
        # consistent shape for ``_cpv_gate_reject_reason`` to check regardless of source.
        cpv = _dig(r, "tender.classification.id")
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{ocid}",
                title=_dig(r, "tender.title") or f"Contracts Finder {ocid}",
                summary=_dig(r, "tender.description") or "",
                agency=_dig(r, "buyer.name"),
                country=src.country,
                published_at=_parse_date(r.get("date")),
                deadline=_parse_date(_dig(r, "tender.tenderPeriod.endDate")),
                url=url,
                cpv_naics=[str(cpv)] if cpv else [],
                status_hint=status_hint,
                raw=r,
            )
        )
    return out


def _parse_generic_ocds(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """A15: generic OCDS (Open Contracting Data Standard, https://standard.open-contracting.org)
    release-package parser, driven entirely by ``src.parse_hints`` -- so a new keyless OCDS-shaped
    portal (UK Find a Tender Service, etc.) needs only a ``config/tenders.yaml`` entry, not a
    bespoke Python function like :func:`_parse_contracts_finder` (kept separate above for its
    UUID-in-``id`` URL-construction quirk, which is Contracts-Finder-specific).

    ``parse_hints`` keys (all optional, defaults match the plain OCDS release-package shape):
    ``notice_path`` (default ``"releases"``), ``id_field`` (default ``"ocid"``), ``title_field``
    (default ``"tender.title"``), ``summary_field`` (default ``"tender.description"``),
    ``agency_field`` (default ``"buyer.name"``), ``date_field`` (publish date; default ``"date"``),
    ``deadline_field`` (default ``"tender.tenderPeriod.endDate"``), either ``url_field`` (a
    dotted path to a direct URL already in the payload) or ``url_pattern`` (a ``"{ocid}"``/
    ``"{id}"`` format template) for the notice's public URL, and (R7-tenders) ``cpv_field``
    (default ``"tender.classification.id"``, matching plain OCDS's CPV classification path --
    confirmed live against UK Contracts Finder 2026-09-07) feeding ``NoticeRaw.cpv_naics`` for the
    CPV pre-filter (``_cpv_gate_reject_reason``)."""
    hints = src.parse_hints or {}
    notice_path = hints.get("notice_path", "releases")
    id_field = hints.get("id_field", "ocid")
    title_field = hints.get("title_field", "tender.title")
    summary_field = hints.get("summary_field", "tender.description")
    agency_field = hints.get("agency_field", "buyer.name")
    date_field = hints.get("date_field", "date")
    deadline_field = hints.get("deadline_field", "tender.tenderPeriod.endDate")
    url_field = hints.get("url_field")
    url_pattern = hints.get("url_pattern")
    cpv_field = hints.get("cpv_field", "tender.classification.id")

    out: list[NoticeRaw] = []
    for r in payload.get(notice_path) or []:
        if not isinstance(r, dict):
            continue
        rid = _dig(r, id_field) or r.get("id")
        if not rid:
            continue
        tags = r.get("tag") or []
        status_hint = "awarded" if "award" in tags else ("closed" if "tenderCancellation" in tags else None)
        url = _dig(r, url_field) if url_field else None
        if not url and url_pattern:
            m = _UUID_RE.search(str(r.get("id") or rid))
            url = url_pattern.format(ocid=rid, id=r.get("id") or rid, uuid=m.group(0) if m else "")
        cpv = _dig(r, cpv_field)
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{rid}",
                title=_dig(r, title_field) or f"{src.name} {rid}",
                summary=_dig(r, summary_field) or "",
                agency=_dig(r, agency_field),
                country=src.country,
                published_at=_parse_date(_dig(r, date_field) or r.get(date_field)),
                deadline=_parse_date(_dig(r, deadline_field)),
                url=url,
                cpv_naics=[str(cpv)] if cpv else [],
                status_hint=status_hint,
                raw=r,
            )
        )
    return out


def _parse_generic_json_list(payload: dict[str, Any], src: TenderSource) -> list[NoticeRaw]:
    """A15: generic parser for a keyless JSON API that returns a flat list of records (not OCDS) --
    e.g. SBIR.gov, USAspending, the EU Funding & Tenders (SEDIA) search API, BOAMP open data.
    Entirely driven by ``src.parse_hints``: ``notice_path`` (dotted path to the list, or ``""`` if
    the top-level payload itself is the list -- see ``_fetch_api_json``, which wraps a bare list
    response as ``{"_root": [...]}`` before calling any parser), ``id_field`` (default ``"id"``),
    ``title_field`` (default ``"title"``), ``summary_field`` (default ``"summary"``),
    ``agency_field``, ``date_field`` (default ``"date"``), ``deadline_field``, ``url_field`` (a
    dotted path) or ``url_pattern`` (a ``"{id}"`` format template), and (R7-tenders) ``cpv_field``
    (a dotted path; ``None`` by default -- unlike OCDS, a flat JSON-list API has no conventional
    CPV field name/shape, so this parser never guesses one; a source that does carry a CPV code
    opts in explicitly via its own ``parse_hints.cpv_field``) feeding ``NoticeRaw.cpv_naics`` for
    the CPV pre-filter (``_cpv_gate_reject_reason``)."""
    hints = src.parse_hints or {}
    notice_path = hints.get("notice_path", "")
    id_field = hints.get("id_field", "id")
    title_field = hints.get("title_field", "title")
    summary_field = hints.get("summary_field", "summary")
    agency_field = hints.get("agency_field")
    date_field = hints.get("date_field", "date")
    deadline_field = hints.get("deadline_field")
    url_field = hints.get("url_field")
    url_pattern = hints.get("url_pattern")
    cpv_field = hints.get("cpv_field")

    records = _dig(payload, notice_path) if notice_path else payload.get("_root")
    if records is None:
        records = []

    out: list[NoticeRaw] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        rid = _dig(r, id_field)
        if not rid:
            continue
        url = _dig(r, url_field) if url_field else None
        if not url and url_pattern:
            url = url_pattern.format(id=rid)
        cpv = _dig(r, cpv_field) if cpv_field else None
        cpv_list = cpv if isinstance(cpv, list) else ([str(cpv)] if cpv else [])
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{rid}",
                title=_dig(r, title_field) or f"{src.name} {rid}",
                summary=_dig(r, summary_field) or "",
                agency=_dig(r, agency_field) if agency_field else None,
                country=src.country,
                published_at=_parse_date(_dig(r, date_field)),
                deadline=_parse_date(_dig(r, deadline_field)) if deadline_field else None,
                url=url,
                cpv_naics=[str(c) for c in cpv_list],
                raw=r,
            )
        )
    return out


_API_PARSERS: dict[str, Callable[[dict[str, Any], TenderSource], list[NoticeRaw]]] = {
    "ted_eu": _parse_ted_notices,
    # A15: TED's CPV-classification-code rotation (config/tenders.yaml `ted_eu_cpv`) hits the same
    # /v3/notices/search endpoint with the same response shape -- only the query template differs
    # (classification-cpv=<code> instead of FT ~ "<phrase>") -- so it reuses this parser verbatim.
    "ted_eu_cpv": _parse_ted_notices,
    "uk_contracts_finder": _parse_contracts_finder,
}

# A15: fallback parsers for any `kind: api_json` source not in `_API_PARSERS` above, selected by
# `parse_hints.format` -- lets a new keyless JSON portal be added as pure config (see
# config/tenders.yaml's US/EU/other sections) rather than needing a bespoke Python parser function.
_GENERIC_API_PARSERS: dict[str, Callable[[dict[str, Any], TenderSource], list[NoticeRaw]]] = {
    "ocds": _parse_generic_ocds,
    "json_list": _parse_generic_json_list,
}


def _is_denylisted_domain(url: str, deny_domains: list[str]) -> bool:
    """True if ``url``'s host is (or is a subdomain of) one of ``deny_domains``."""
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in deny_domains)


def _parse_search_hits(hits: list[SearchHit], src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for h in hits:
        if not h.url or _is_denylisted_domain(h.url, deny_domains):
            continue
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{h.url}",
                title=h.title or h.url,
                summary=h.snippet or "",
                country=src.country,
                published_at=_parse_date(h.published),
                url=h.url,
                raw={"snippet": h.snippet, "engine": h.engine},
            )
        )
    return out


def _parse_rss_notices(raw_text: str, src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for e in parse_feed(raw_text):
        if not e.url or _is_denylisted_domain(e.url, deny_domains):
            continue
        out.append(
            NoticeRaw(
                source_id=src.id,
                external_ref=f"{src.id}:{e.url}",
                title=e.title or e.url,
                summary=e.summary or "",
                country=src.country,
                published_at=e.published_at.date() if e.published_at else None,
                url=e.url,
            )
        )
    return out


# --------------------------------------------------------------------------
# per-kind fetchers
# --------------------------------------------------------------------------


_RATE_LIMIT_STATUS_CODES = (429, 503)
_RATE_LIMIT_MAX_ATTEMPTS = 3
_RATE_LIMIT_DEFAULT_BASE_SLEEP_S = 2.0
_RATE_LIMIT_STATUS_RE = re.compile(r"(?<!\d)(429|503)(?!\d)")


def _is_rate_limited_error(exc: Exception) -> bool:
    """R7-tenders-b (finding 2): true only for an HTTP 429 (Too Many Requests) or 503 (Service
    Unavailable) -- the two codes UK Contracts Finder/Find a Tender returned live 2026-09-07 under
    this repo's keyword-rotation loop, which had no inter-request pacing at all. Never true for
    anything else (a 400 query-syntax error, a 404, a DNS/connection failure, ...) -- those still
    propagate and fail that one request immediately, exactly as before this round (a single
    source's failure is caught by ``scan_tenders``'s own per-source ``try/except``, never stops the
    others). Two exception shapes are checked: a direct ``httpx.HTTPStatusError`` (host/dev role,
    in-process fetch -- carries ``.response.status_code``), and the isolated ``agent`` role's own
    ``FetchError``/generic exception, which instead surfaces the fetcher container's error as a
    plain string that still names the numeric status code (``eoa.fetch.remote._wait_job``) --
    matched as a standalone number (word-boundary-style, via a negative lookaround since ``\\b``
    doesn't stop at a digit) so it can't misfire on an unrelated "...4295..." substring."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status in _RATE_LIMIT_STATUS_CODES:
        return True
    return bool(_RATE_LIMIT_STATUS_RE.search(str(exc)))


def _call_with_rate_limit_backoff(
    src: TenderSource, do_request: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """R7-tenders-b (finding 2): retries one HTTP round trip (``do_request``) up to
    ``_RATE_LIMIT_MAX_ATTEMPTS`` times total when it fails with :func:`_is_rate_limited_error`,
    sleeping an exponentially growing multiple of the source's own ``pace_seconds`` (or
    ``_RATE_LIMIT_DEFAULT_BASE_SLEEP_S`` when a source has no pacing configured at all, i.e.
    ``pace_seconds`` left at its ``0.0`` default) before each retry -- 1x, 2x, 4x. Any other error,
    or the final attempt, re-raises immediately."""
    base_sleep = src.pace_seconds if src.pace_seconds > 0 else _RATE_LIMIT_DEFAULT_BASE_SLEEP_S
    for attempt in range(_RATE_LIMIT_MAX_ATTEMPTS):
        try:
            return do_request()
        except Exception as exc:
            if attempt == _RATE_LIMIT_MAX_ATTEMPTS - 1 or not _is_rate_limited_error(exc):
                raise
            sleep_s = base_sleep * (2**attempt)
            log.debug(
                "tender_api_json_rate_limited_retry",
                source=src.id,
                attempt=attempt + 1,
                sleep_s=sleep_s,
                error=str(exc)[:200],
            )
            time.sleep(sleep_s)
    raise AssertionError("unreachable -- loop above always returns or raises")


def _fetch_api_json(src: TenderSource, keyword: str) -> list[NoticeRaw]:
    """Requirement: the EO keyword set must ride in the API query itself, not just a post-filter
    -- ``query_template``/``query_params`` (config/tenders.yaml) both embed ``{keyword}`` directly
    in the request TED/Contracts Finder actually receive; the client-side gate below is a
    (necessary, per each source's own notes on unreliable server-side filtering) second pass, not
    the only one.

    R7-tenders (finding 1c): a ``query_params`` value may also embed ``{api_key}`` -- resolved from
    the actual environment variable named in ``src.needs_key_env_var`` (e.g. ``sam_gov_api``'s
    ``SAM_GOV_API_KEY``) at request time, never a hardcoded placeholder in the YAML. Empty string
    when no such variable is configured/set -- harmless for a source without an ``{api_key}``
    placeholder at all, and this function is only ever reached for a source
    ``_api_json_source_enabled`` already confirmed has a real, non-empty key when one is required.

    R7-tenders-b (finding 1): also embeds ``{since_date}`` (a ``YYYYMMDD`` string,
    ``src.query_lookback_days`` before today -- empty string, a harmless no-op substitution, when
    that field is ``0``) and ``{page}`` (1-indexed) the same way. Pages ``src.max_pages`` times
    (default 1 -- a single request, unchanged from before this round), stopping early the moment a
    page comes back with zero notices (no more results, or -- since TED's own ``SORT BY PD DESC``
    means a later page is strictly older -- genuinely exhausted). This function's own outward
    signature (``src``, ``keyword``, nothing else) is unchanged from before this round on purpose:
    it is monkeypatched by name with a bare ``(src_arg, keyword)`` stub in
    ``tests/unit/test_tenders_scan.py``'s ``TestCollectSourceNoticesApiQueryKeywordsOverride``."""
    if not src.url:
        return []
    api_key = os.environ.get(src.needs_key_env_var, "") if src.needs_key_env_var else ""
    since_date = (
        (dt.date.today() - dt.timedelta(days=src.query_lookback_days)).strftime("%Y%m%d")
        if src.query_lookback_days
        else ""
    )
    # TENDERS-SAM: same lookback window as `{since_date}` above, just rendered MM/dd/yyyy for an
    # API (SAM.gov v2) whose own date-range params require that format rather than YYYYMMDD. Empty
    # string (a harmless no-op substitution) when `query_lookback_days` is 0, same convention as
    # `{since_date}`.
    since_date_us = (
        (dt.date.today() - dt.timedelta(days=src.query_lookback_days)).strftime("%m/%d/%Y")
        if src.query_lookback_days
        else ""
    )
    today_us = dt.date.today().strftime("%m/%d/%Y")
    out: list[NoticeRaw] = []
    for page in range(1, max(1, src.max_pages) + 1):
        if page > 1 and src.pace_seconds:
            time.sleep(src.pace_seconds)

        def _do_request(page: int = page) -> dict[str, Any]:
            if src.query_template:
                # NOT str.format(): query_template is a JSON literal
                # ('{"query":"...","fields":[...]}') -- its own braces would be misparsed as
                # format fields. Substitute each placeholder directly, JSON-string-escaping the
                # keyword/api_key first so neither can break the surrounding JSON syntax
                # (since_date is always a plain 8-digit string, page a plain integer -- neither
                # needs escaping).
                escaped_keyword = json.dumps(keyword)[1:-1]
                escaped_api_key = json.dumps(api_key)[1:-1]
                body = json.loads(
                    src.query_template.replace("{keyword}", escaped_keyword)
                    .replace("{api_key}", escaped_api_key)
                    .replace("{since_date}", since_date)
                    .replace("{since_date_us}", since_date_us)
                    .replace("{today_us}", today_us)
                    .replace("{page}", str(page))
                )
                return fetch_raw_remote(src.url, method=src.method or "POST", json_body=body)
            params = {
                k: v.format(
                    keyword=keyword,
                    api_key=api_key,
                    since_date=since_date,
                    since_date_us=since_date_us,
                    today_us=today_us,
                    page=str(page),
                )
                for k, v in (src.query_params or {}).items()
            }
            return fetch_raw_remote(f"{src.url}?{urlencode(params)}", method=src.method or "GET")

        resp = _call_with_rate_limit_backoff(src, _do_request)
        data = resp.get("json")
        if isinstance(data, list):
            # A15: some keyless JSON APIs (e.g. a bare-array response) return the list itself as
            # the top-level payload -- wrap it so _parse_generic_json_list's `notice_path: ""` case
            # (see its docstring) has a dict to read `_root` off of, matching every other parser's
            # signature.
            data = {"_root": data}
        if not isinstance(data, dict):
            break
        parser = _API_PARSERS.get(src.id)
        if parser is None:
            parser = _GENERIC_API_PARSERS.get((src.parse_hints or {}).get("format", ""))
        page_notices = parser(data, src) if parser else []
        out.extend(page_notices)
        if not page_notices:
            break
    return out


def _fetch_search(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    out: list[NoticeRaw] = []
    for q in src.queries or []:
        resp = search(q, lang=src.engine_lang, max_results=8)
        if resp.error:
            log.debug("tender_search_query_failed", source=src.id, query=q, error=resp.error)
            continue
        out.extend(_parse_search_hits(resp.hits, src, deny_domains))
    return out


def _fetch_rss(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    if not src.url:
        return []
    resp = fetch_raw_remote(src.url, method="GET")
    text = resp.get("text")
    if not text:
        return []
    return _parse_rss_notices(text, src, deny_domains)


def _collect_source_notices(src: TenderSource, deny_domains: list[str]) -> list[NoticeRaw]:
    if src.kind == "api_json":
        seen: set[str] = set()
        out: list[NoticeRaw] = []
        # A15: api_query_keywords (when set -- currently only ted_eu_cpv) overrides which list is
        # rotated into the API query itself; `src.keywords` (the domain-gate vocabulary) is
        # untouched either way -- see the field's docstring on TenderSource.
        query_terms = src.api_query_keywords if src.api_query_keywords else src.keywords
        for i, kw in enumerate((query_terms or DEFAULT_KEYWORDS)[:MAX_KEYWORDS_PER_API_SOURCE]):
            if i > 0 and src.pace_seconds:
                # R7-tenders-b (finding 2): UK Contracts Finder/Find a Tender 429'd live
                # 2026-09-07 under exactly this per-keyword rotation loop -- it had no
                # inter-request pacing at all. Configurable per source (`pace_seconds`); a real
                # no-op (`time.sleep(0.0)`) for the many sources that were never rate-limited,
                # since that field's Pydantic default is 0.0.
                time.sleep(src.pace_seconds)
            for n in _fetch_api_json(src, kw):
                if n.external_ref not in seen:
                    seen.add(n.external_ref)
                    out.append(n)
        return out
    if src.kind == "search":
        return _fetch_search(src, deny_domains)
    if src.kind == "rss":
        return _fetch_rss(src, deny_domains)
    return []  # html: documented, not scraped (see config/tenders.yaml notes)


# --------------------------------------------------------------------------
# two-signal gate / dedupe / status
# --------------------------------------------------------------------------


def _term_present(term: str, text_casefold: str) -> bool:
    """R7-tenders (round-6 D9 finding 2, candidate id 34): plain casefold substring match for
    ``term`` in the already-casefolded ``text_casefold`` -- *except* for a short term (at most
    ``_SHORT_KEYWORD_MAX_LEN`` characters), which additionally requires the match to sit at a word
    boundary (not embedded inside a longer, unrelated word). A short acronym-shaped term ("ATR",
    "RFI", ...) is exactly the shape prone to matching as a fragment *inside* an unrelated longer
    word in the same or another language -- the concrete bug this fixes: the EO/IR domain keyword
    "ATR" matched inside the Dutch word "privaatrechtelijke" ("under private law"), letting a
    Rotterdam legal/HR coaching contract (candidate id 34) clear the domain-signal check on pure
    accident. A longer phrase ("computer vision", "seeker") is left on plain substring matching so
    a genuine plural ("seekers") or compound still matches, unchanged from before this fix."""
    term_cf = term.casefold()
    if len(term) > _SHORT_KEYWORD_MAX_LEN:
        return term_cf in text_casefold
    return re.search(rf"\b{re.escape(term_cf)}\b", text_casefold) is not None


def _matches_keywords(notice: NoticeRaw, keywords: list[str]) -> list[str]:
    """DOMAIN signal: EO/IR/CV terms actually present (see ``_term_present``) in title+summary."""
    text = f"{notice.title} {notice.summary}".casefold()
    return [kw for kw in (keywords or DEFAULT_KEYWORDS) if _term_present(kw, text)]


def _has_procurement_signal(notice: NoticeRaw, src_kind: str, procurement_signals: list[str]) -> bool:
    """PROCUREMENT signal: explicit tender/RFI/RFP/... language (see ``_term_present``) in
    title+summary for a general source (search/rss -- could be any web content), or implicitly
    satisfied for a structured procurement-portal API (``api_json`` -- TED/Contracts Finder
    notices *are*, by construction, real tender/contract records; most don't literally spell
    "tender" in their title text, so requiring the word there would reject the overwhelming
    majority of genuine notices)."""
    if src_kind == "api_json":
        return True
    text = f"{notice.title} {notice.summary}".casefold()
    return any(_term_present(sig, text) for sig in (procurement_signals or DEFAULT_PROCUREMENT_SIGNALS))


def _negative_defence_text(notice: NoticeRaw) -> str:
    """Shared casefolded text used by :func:`_has_defence_context`/:func:`_negative_keyword_penalty`
    -- title + summary + agency, so a defence-context signal sitting only in the buyer's own name
    (e.g. ``agency="DEPARTMENT OF THE NAVY"``) still counts, matching the intuition that a notice
    from a defence buyer has defence context even if its title text is generic."""
    return f"{notice.title} {notice.summary} {notice.agency or ''}".casefold()


def _has_defence_context(notice: NoticeRaw, defence_context_signals: list[str]) -> bool:
    """TENDERS-SAM: True if any ``defence_context_signals`` term (military/security vocabulary,
    deliberately broader/less precise than the EO/IR domain vocabulary itself) is present in
    title+summary+agency -- see ``_negative_keyword_penalty``'s docstring for how this is used."""
    text = _negative_defence_text(notice)
    return any(
        _term_present(sig, text) for sig in (defence_context_signals or DEFAULT_DEFENCE_CONTEXT_SIGNALS)
    )


# TENDERS-SAM: relevance_score ceiling applied when a negative_keywords term is present with no
# countervailing defence_context_signals term -- low enough to sit well below
# eoa.tenders.feedback.DEFAULT_RELEVANCE_THRESHOLD (0.6) and even THRESHOLD_MIN (0.3), so a demoted
# notice is never accidentally 'accepted' by a self-tuned threshold at its floor.
_NEGATIVE_KEYWORD_RELEVANCE_CEILING = 0.2


def _negative_keyword_penalty(
    notice: NoticeRaw, negative_keywords: list[str], defence_context_signals: list[str]
) -> str | None:
    """TENDERS-SAM: returns the first matched ``negative_keywords`` term when the notice's
    title+summary+agency contains one AND :func:`_has_defence_context` is False for the same text
    -- the caller (``_relevance_score_for``) uses a non-``None`` return to cap ``relevance_score``
    at :data:`_NEGATIVE_KEYWORD_RELEVANCE_CEILING`. Returns ``None`` (no penalty) either when no
    negative term matched, or when one did but a defence-context term also matched -- per W2b this
    is a *demotion* signal only, never a rejection reason (``_gate_reject_reason`` is untouched)."""
    if _has_defence_context(notice, defence_context_signals):
        return None
    text = _negative_defence_text(notice)
    for term in negative_keywords or DEFAULT_NEGATIVE_KEYWORDS:
        if _term_present(term, text):
            return term
    return None


def _passes_gate(
    notice: NoticeRaw, src_kind: str, domain_keywords: list[str], procurement_signals: list[str]
) -> list[str]:
    """Two-signal gate (coordinator requirement): a notice is stored only if it carries at least
    one DOMAIN term AND at least one PROCUREMENT signal. Returns the matched domain terms (used
    as ``tenders.matched_terms``) on success, or ``[]`` -- treated as "gate failed" by the caller,
    including the case where domain terms matched but the procurement signal did not."""
    domain_terms = _matches_keywords(notice, domain_keywords)
    if not domain_terms:
        return []
    if not _has_procurement_signal(notice, src_kind, procurement_signals):
        return []
    return domain_terms


def _within_window(notice: NoticeRaw, since_days: int, today: dt.date) -> bool:
    """Only structured API sources (TED/Contracts Finder) carry a trustworthy publish date that's
    worth filtering on -- TED's full-text search returns its entire archive back to ~2016, so
    without this filter every scan would re-touch thousands of old notices. Search-hit/RSS
    "published" dates are unreliable enough (often missing or the crawl date) that they're never
    filtered out on age -- an undated or old-looking lead is still worth a look.

    R7-tenders-b (finding 1): also excludes a notice whose own submission deadline has already
    passed. TED's own diagnosis this round was stark -- once the archive/no-date-filter bug was
    fixed, every remaining "recent enough by published_at" TED notice this repo had ever accepted
    still had a passed deadline, making it useless for BD purposes despite clearing every other
    gate. `deadline` is only ever populated by a structured source's own parser (TED's
    `deadline-receipt-request`, Contracts Finder/FTS's `tender.tenderPeriod.endDate`, ...) at parse
    time -- never by the LLM extraction pass, which runs after this filter -- so this reuses the
    same "trustworthy date, worth filtering on" reasoning as the `published_at` check right below
    it, and is exactly as harmless when absent: a notice with no deadline at all (most search/rss
    sources, or a structured source whose deadline field wasn't populated for this notice) is
    completely untouched, same open-intake philosophy (W2b) as before this round. A deadline of
    exactly `today` still counts as open (last day to submit)."""
    if notice.deadline is not None and notice.deadline < today:
        return False
    if notice.published_at is None:
        return True
    return (today - notice.published_at).days <= since_days


def _tender_exists(external_ref: str) -> bool:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM tenders WHERE external_ref = %s", (external_ref,))
        return cur.fetchone() is not None


#: Round-6 D9 fix (docs/qa/loop/round_5_judge.md, junk tender candidates): 5 of the 13 live
#: `tenders` rows were the SAME generic Northrop Grumman EO/IR product page, and 2 more were the
#: same "Unmanned Airspace" Counter-UAS category listing -- each re-inserted once per per-country
#: `search`-kind source that happened to surface it (us_defense_innovation_search, pl_search,
#: jp_search, gcc_search, nz_search, ...), because `external_ref` is built as
#: "<source_id>:<url>" (module docstring): a different `source_id` per country produces a
#: different `external_ref` for the byte-identical URL, so `_tender_exists` above never catches it.
#: This is a second, title+portal dedupe layer specifically for that shape of duplicate --
#: "portal" is the notice's own URL host (the actual originating site), not our internal
#: per-country `source_id`, since that's exactly the dimension the bug duplicates across. Scoped to
#: 'candidate'-intake rows only (never 'accepted'/'archived') -- a low-relevance marketing page
#: repeatedly resurfacing is exactly what this catches; a genuinely re-surfaced already-accepted
#: tender is left alone.
_TENDER_TITLE_WS_RE = re.compile(r"\s+")


def _normalize_tender_title(title: str | None) -> str:
    return _TENDER_TITLE_WS_RE.sub(" ", (title or "").strip()).casefold()


def _notice_portal(url: str | None) -> str:
    """The URL host ("portal") a notice actually came from, lowercased; ``""`` when there's no URL
    to derive one from (never matches anything, so such a notice is never treated as a duplicate by
    :func:`_candidate_duplicate_exists`)."""
    if not url:
        return ""
    return (urlparse(url).netloc or "").lower()


def _candidate_duplicate_exists(normalized_title: str, portal: str) -> bool:
    """True if a 'candidate'-intake `tenders` row already exists with the same normalized title
    from the same portal (URL host) -- see the module-level note above."""
    if not normalized_title or not portal:
        return False
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT url FROM tenders WHERE intake = 'candidate' "
            "AND lower(regexp_replace(btrim(title), '\\s+', ' ', 'g')) = %(t)s",
            {"t": normalized_title},
        )
        rows = cur.fetchall()
    # the pool's connection() yields dict rows -- r[0] raised KeyError: 0 and crashed the whole
    # tenders stage of the nightly run (2026-09-08 01:31, run_errors 279)
    urls = [(r["url"] if isinstance(r, dict) else r[0]) for r in rows]
    return any(_notice_portal(u) == portal for u in urls if u)


# F2 (2026-09-05): "assume open when undated" bug -- a notice with no deadline (the overwhelming
# majority of search/rss hits) used to default straight to 'open' forever. A notice whose only date
# signal (published_at) is this old, with still no deadline, is stale enough to treat as closed
# rather than showing it as an open opportunity indefinitely.
_STALE_DAYS = 365


def _initial_status(notice: NoticeRaw, today: dt.date, notice_type: str | None = None) -> str:
    """F2 status rubric. Priority order: (1) a structured source's own explicit tag
    (``status_hint`` -- e.g. Contracts Finder's OCDS ``tag``) is the most authoritative signal
    available and wins outright; (2) the LLM extraction's ``notice_type == 'award'`` (a notice
    reporting an already-signed contract, not a future ask) also means 'awarded'; (3) a known
    deadline decides open/closed; (4) with no deadline at all, a notice with no ``published_at``
    either is 'unknown' (not assumed open -- there is no date evidence either way); (5) with no
    deadline but a ``published_at`` older than ``_STALE_DAYS``, treat it as closed (stale); (6)
    otherwise open."""
    if notice.status_hint in ("awarded", "closed"):
        return notice.status_hint
    if notice_type == "award":
        return "awarded"
    if notice.deadline is not None:
        return "closed" if notice.deadline < today else "open"
    if notice.published_at is None:
        return "unknown"
    if (today - notice.published_at).days > _STALE_DAYS:
        return "closed"
    return "open"


# F13 (2026-09-05): country-by-domain fallback for the generic `kind: search` sources, which carry
# a placeholder `country` (e.g. "other"/"US" aggregate) in config/tenders.yaml rather than the
# notice's real geography. Covers the common portals seen live in production (SAM.gov/HigherGov/
# usarfp.com notices all landing as country='other' via the rfi_rfp_news/sam_gov_search sources)
# plus every structured-portal domain already in config/tenders.yaml for completeness. Only used
# when the notice's own country is missing/'other' AND the LLM extraction didn't supply one either
# -- never overrides a real value.
_DOMAIN_COUNTRY_FALLBACK: dict[str, str] = {
    "sam.gov": "US",
    "highergov.com": "US",
    "usarfp.com": "US",
    "ted.europa.eu": "EU",
    "contractsfinder.service.gov.uk": "UK",
    "mod.gov.il": "IL",
    "nspa.nato.int": "NATO",
    "ncia.nato.int": "NATO",
    "tenders.gov.au": "AU",
    "canadabuys.canada.ca": "CA",
}


def _country_from_domain(url: str | None) -> str | None:
    """Best-effort country inference from a notice URL's hostname (see ``_DOMAIN_COUNTRY_FALLBACK``
    above). Matches exact domain or subdomain, same convention as ``_is_denylisted_domain``."""
    if not url:
        return None
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    for domain, country in _DOMAIN_COUNTRY_FALLBACK.items():
        if host == domain or host.endswith("." + domain):
            return country
    return None


def _apply_extraction_to_notice(notice: NoticeRaw, extract: TenderExtract) -> None:
    """F2/F13: fill date/agency/country gaps on ``notice`` from the LLM extraction -- never
    overwrites a value the source's own structured parser already supplied (TED/Contracts Finder
    stay authoritative over their own fields; this only helps the generic search/rss sources that
    had nothing but a title+snippet to begin with)."""
    if notice.published_at is None and extract.published_at is not None:
        notice.published_at = extract.published_at
    if notice.deadline is None and extract.deadline is not None:
        notice.deadline = extract.deadline
    if not notice.agency and extract.agency:
        notice.agency = extract.agency
    if (not notice.country or notice.country == "other") and extract.country:
        notice.country = extract.country


def _apply_domain_country_fallback(notice: NoticeRaw) -> None:
    """Runs regardless of whether the LLM extraction ran/succeeded -- a pure URL-based fallback for
    when the country is still missing/generic after the above (F13)."""
    if not notice.country or notice.country == "other":
        domain_country = _country_from_domain(notice.url)
        if domain_country:
            notice.country = domain_country


# Round-4 W2 (docs/REVIEW_2026-09-06_evening.md): audit of job 102's 2026-09-06 scan found that
# every one of the 9 ``llm_rejected`` notices from ``rfi_rfp_news``/``canada_buys_search`` whose
# URL was a known DoD contract-tracker aggregator (govtribe.com, sam.gov, highergov.com, ...)
# scored ``relevance=0`` -- because those aggregators return HTTP 403 to a bare fetch (confirmed
# live in runtime/logs/orchestrator.2026-09-06.log), so ``_fetch_notice_text`` falls back to just
# the search hit's title+snippet, and that snippet is frequently a generic
# "sign up to see this opportunity" teaser carrying none of the real notice content -- even when
# the notice's own URL slug/title already names a genuine, on-topic solicitation (e.g.
# ".../electro-opticinfrared-eoir-sight-system-eoss-n0016426snb35",
# ".../request-for-information-rfisources-sought-laser-range-finder-n0016423snb23", both real Navy
# NSWC Crane solicitation numbers). The LLM's own "never invent a fact not in the text" instruction
# then correctly refuses to score those as relevant from a content-free teaser -- so the fix is not
# "make the LLM guess harder", it is a deterministic rescue that only fires when the *title/URL
# slug themselves* (never fabricated body content) already carry both required signals.
TRUSTED_PROCUREMENT_TRACKER_DOMAINS = frozenset(
    {
        "govtribe.com",
        "sam.gov",
        "beta.sam.gov",
        "highergov.com",
        "usarfp.com",
        "grants.gov",
        "dibbs.bsm.dla.mil",
        "sbir.gov",
        "www.sbir.gov",
    }
)

# Maps a procurement-signal phrase (as matched by ``_has_procurement_signal``, casefold substring)
# to the ``TenderExtract.notice_type`` it implies -- needed because the rescue below must produce a
# value in ``VALID_NOTICE_TYPES`` (F24's gate rejects ``notice_type == 'other'`` outright), and the
# LLM's own low-content classification typically left it at the ``other`` default. Order matters:
# checked most-specific-first so "sources sought" (which also contains no "rfi"/"rfp" substring
# anyway) and "request for quotation" don't get shadowed by a coarser match.
_NOTICE_TYPE_BY_SIGNAL: tuple[tuple[str, str], ...] = (
    ("sources sought", "sources_sought"),
    ("request for quotation", "rfq"),
    ("rfq", "rfq"),
    ("request for proposal", "rfp"),
    ("rfp", "rfp"),
    ("request for information", "rfi"),
    ("בקשת מידע", "rfi"),
    ("בקשה למידע", "rfi"),
    ("rfi", "rfi"),
    ("invitation to tender", "tender"),
    ("contract notice", "tender"),
    ("prior information notice", "tender"),
    ("tender", "tender"),
    ("מכרז", "tender"),
)


def _infer_notice_type_from_text(text: str) -> str | None:
    """Best-effort ``notice_type`` from the DEFAULT_PROCUREMENT_SIGNALS phrase actually present in
    ``text`` (title+summary) -- used only by the thin-snippet rescue below, never to override an
    LLM-supplied value."""
    low = text.casefold()
    for phrase, notice_type in _NOTICE_TYPE_BY_SIGNAL:
        if phrase in low:
            return notice_type
    return None


def _is_trusted_tracker_domain(url: str | None) -> bool:
    """True if ``url``'s host is (or is a subdomain of) a known DoD/government contract-notice
    aggregator that is known to 403 a bare page fetch (see ``TRUSTED_PROCUREMENT_TRACKER_DOMAINS``
    above) -- i.e. an unfetchable page here is a *platform* limitation, not evidence the notice
    itself is thin or dubious."""
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in TRUSTED_PROCUREMENT_TRACKER_DOMAINS)


#: Rescued notices are always stored with reduced confidence and exactly this relevance -- never
#: higher, since the rescue never actually confirmed anything beyond the title/URL signal.
_RESCUED_RELEVANCE = RELEVANCE_MIN_ACCEPT
_RESCUED_CONFIDENCE_CAP = 0.4


def _rescue_thin_snippet_from_trusted_tracker(
    notice: NoticeRaw,
    extract: TenderExtract,
    *,
    page_verified: bool,
    src_kind: str,
    domain_terms: list[str],
    procurement_signals: list[str],
) -> TenderExtract:
    """Round-4 W2: when the LLM classified a notice as not relevant / below the relevance floor
    *because* its page could not be fetched (``page_verified`` is False) and all it saw was a thin
    search snippet, but the notice's own title+summary -- the reliable part, already gate-verified
    by ``_passes_gate`` before the LLM ever ran -- already carries a real EO/IR/CV domain term AND a
    procurement-process signal, AND the notice comes from a known government contract-tracker
    domain that predictably blocks bare page fetches: treat it as relevant at the deterministic
    minimum (``RELEVANCE_MIN_ACCEPT``) rather than discard it. Never touches
    ``published_at``/``deadline``/``agency``/``country`` (those stay whatever the LLM -- possibly
    nothing -- actually found; the rescue only concerns topical relevance, it invents no facts).

    A no-op (returns ``extract`` unchanged) unless every one of these holds:
      - the page was never actually verified (``not page_verified``) -- a notice whose real page WAS
        read and still scored low relevance is left alone, that is a genuine LLM verdict;
      - ``src_kind`` is ``search``/``rss`` (an ``api_json`` structured record is never this thin);
      - the notice is already relevant per the deterministic two-signal gate (``domain_terms`` +
        ``_has_procurement_signal``) -- the exact same check ``_passes_gate`` already ran, re-run
        here against title+summary alone (never the LLM's own possibly-empty extraction text);
      - the URL host is a recognised tracker domain (``_is_trusted_tracker_domain``);
      - the LLM's own verdict was actually the failure mode this targets (not relevant / below
        floor) -- a notice the LLM already accepted needs no rescue.
    """
    if page_verified or src_kind not in ("search", "rss"):
        return extract
    if extract.relevant and extract.relevance >= RELEVANCE_MIN_ACCEPT:
        return extract
    if not domain_terms or not _has_procurement_signal(notice, src_kind, procurement_signals):
        return extract
    if not _is_trusted_tracker_domain(notice.url):
        return extract
    inferred_type = extract.notice_type
    if inferred_type not in VALID_NOTICE_TYPES:
        guessed = _infer_notice_type_from_text(f"{notice.title} {notice.summary}")
        if guessed is None:
            return extract  # can't even name a valid notice_type -- nothing to confidently rescue
        inferred_type = guessed
    log.info(
        "tender_thin_snippet_rescued",
        external_ref=notice.external_ref,
        domain=urlparse(notice.url or "").netloc,
        original_relevance=extract.relevance,
        matched_terms=domain_terms,
    )
    return extract.model_copy(
        update={
            "relevant": True,
            "relevance": max(extract.relevance, _RESCUED_RELEVANCE),
            "matched_terms": extract.matched_terms or domain_terms,
            "notice_type": inferred_type,
            "confidence": min(extract.confidence, _RESCUED_CONFIDENCE_CAP),
            "summary_he": extract.summary_he
            or "לא ניתן היה לשלוף את תוכן העמוד (חסימת גישה בפורטל); הרלוונטיות הוסקה מכותרת ההודעה בלבד -- מומלץ לבדוק ידנית.",
        }
    )


def _as_datetime(d: dt.date | None) -> dt.datetime | None:
    return None if d is None else dt.datetime.combine(d, dt.time(), tzinfo=dt.UTC)


# W2b: the neutral relevance_score assigned when the LLM never actually classified the notice
# (deferred/unavailable/failed) -- "unknown, don't presume either way", never itself a rejection.
_RELEVANCE_SCORE_WHEN_LLM_UNAVAILABLE = 0.5


def _relevance_score_for(
    extract: TenderExtract | None,
    notice: NoticeRaw | None = None,
    negative_keywords: list[str] | None = None,
    defence_context_signals: list[str] | None = None,
) -> float:
    """W2b: the 0-1 normalized signal ``tenders.intake``/the self-tuning threshold act on --
    ``extract.relevance / 10`` when the LLM actually ran, else the neutral default above. Never
    used to reject a notice outright (see ``_gate_reject_reason``'s docstring).

    TENDERS-SAM: when ``notice`` is supplied, the score is additionally capped at
    :data:`_NEGATIVE_KEYWORD_RELEVANCE_CEILING` if :func:`_negative_keyword_penalty` fires (an
    off-topic-domain term present with no defence-context term to override it) -- this can only
    ever lower the score the LLM/default already produced, never raise it. ``notice=None`` (every
    pre-existing caller/test) skips this check entirely, exactly the old behaviour."""
    base = (
        _RELEVANCE_SCORE_WHEN_LLM_UNAVAILABLE
        if extract is None
        else max(0.0, min(1.0, extract.relevance / 10.0))
    )
    if notice is not None:
        penalty_term = _negative_keyword_penalty(
            notice, negative_keywords or DEFAULT_NEGATIVE_KEYWORDS, defence_context_signals or DEFAULT_DEFENCE_CONTEXT_SIGNALS
        )
        if penalty_term is not None:
            base = min(base, _NEGATIVE_KEYWORD_RELEVANCE_CEILING)
    return base


def _intake_for_score(relevance_score: float) -> str:
    """W2b: ``'accepted'`` once ``relevance_score`` meets the current *learned* threshold
    (``eoa.tenders.feedback.get_relevance_threshold``, self-tuned from operator 👍/👎 feedback --
    see that module), else ``'candidate'``. Falls back to the module's own documented default
    (0.6) on a threshold-read failure -- a DB hiccup here must never block insertion."""
    try:
        threshold = get_relevance_threshold()
    except Exception as exc:
        log.debug("tender_relevance_threshold_unavailable", error=str(exc)[:120])
        threshold = 0.6
    return "accepted" if relevance_score >= threshold else "candidate"


def _insert_tender_and_item(
    notice: NoticeRaw,
    matched_terms: list[str],
    *,
    relevance: int | None = None,
    relevance_score: float | None = None,
    intake: str = "candidate",
    summary_he: str = "",
    entities: list[str] | None = None,
    status_override: str | None = None,
    notice_type: str | None = None,
) -> tuple[int | None, int]:
    """Insert the ``items`` row first (so ``tenders.item_id`` can reference it), then the
    ``tenders`` row itself. ``relevance``/``summary_he``/``entities`` default to the deterministic
    keyword-hit baseline when the caller didn't supply an LLM-derived value (LLM unavailable);
    ``relevance_score``/``intake`` (W2b, additive) default to the same "LLM unavailable" neutral
    baseline (0.5 / ``'candidate'``) when the caller doesn't supply one either, though every
    ``scan_tenders`` call site always does (see ``_relevance_score_for``/``_intake_for_score``).
    ``status_override`` forces ``status`` regardless of the deadline-derived value; ``notice_type``
    (from the LLM extraction, F2) feeds ``_initial_status``'s ``'award' -> 'awarded'`` rule when
    ``status_override`` doesn't already force something else. ``notice.agency``/``notice.country``/
    ``notice.published_at``/``notice.deadline`` are expected to already carry any LLM-filled values
    by the time this is called (see ``_apply_extraction_to_notice``/``_apply_domain_country_fallback``
    in ``scan_tenders``). Returns ``(tender_id, item_id)`` -- ``tender_id`` is ``None`` if a
    concurrent scan already inserted the same ``external_ref`` (``ON CONFLICT DO NOTHING``)."""
    clean_text = f"{notice.title}\n\n{notice.summary}".strip()
    url = notice.url or f"urn:tender:{notice.external_ref}"
    item_id = insert_item(
        source_id=None,
        url=url,
        title=notice.title,
        lang="he" if notice.country == "IL" else "en",
        published_at=_as_datetime(notice.published_at),
        clean_text=clean_text,
        report_kind="tender",
        geography=notice.country,
    )
    if summary_he:
        update_item_fields(item_id, summary_he=summary_he)

    today = dt.date.today()
    status = status_override or _initial_status(notice, today, notice_type)
    final_relevance = relevance if relevance is not None else max(1, min(10, len(matched_terms)))
    final_relevance_score = (
        relevance_score if relevance_score is not None else _RELEVANCE_SCORE_WHEN_LLM_UNAVAILABLE
    )
    # R9-reports #3a (round-8 judge D9 #6): the open TED tender (id 42) carried no `product_lines`
    # tag because tagging only ever ran for `items`/`events` (the live pipeline hook) and, one-off,
    # for whatever already existed in `tenders` at backfill time (`scripts/backfill_product_lines
    # .py`) -- a tender inserted after that backfill was never tagged at all. Tag every notice at
    # intake instead, the same deterministic (no LLM, no DB) `tag_product_lines` call and
    # title+description+CPV/entities input `scripts/backfill_product_lines.py`'s own tenders sweep
    # uses (title/description as `text_en`, the LLM's own Hebrew summary as `text_he` when present).
    product_lines = tag_product_lines(
        text_he=summary_he or None,
        text_en=" ".join(filter(None, [notice.title, notice.summary, " ".join(notice.cpv_naics or [])])),
        entities=entities or [],
        subdomain=None,
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tenders (
                source, external_ref, title, agency, country, published_at, deadline,
                url, cpv_naics, summary_he, relevance, relevance_score, intake, matched_terms,
                entities, status, item_id, raw, product_lines
            )
            VALUES (
                %(source)s, %(external_ref)s, %(title)s, %(agency)s, %(country)s, %(published_at)s,
                %(deadline)s, %(url)s, %(cpv_naics)s, %(summary_he)s, %(relevance)s,
                %(relevance_score)s, %(intake)s, %(matched_terms)s,
                %(entities)s, %(status)s, %(item_id)s, %(raw)s, %(product_lines)s
            )
            ON CONFLICT (external_ref) DO NOTHING
            RETURNING id
            """,
            {
                "source": notice.source_id,
                "external_ref": notice.external_ref,
                "title": notice.title,
                "agency": notice.agency,
                "country": notice.country,
                "published_at": _as_datetime(notice.published_at),
                "deadline": notice.deadline,
                "url": notice.url,
                "cpv_naics": notice.cpv_naics or None,
                "summary_he": summary_he or "",
                "relevance": final_relevance,
                "relevance_score": final_relevance_score,
                "intake": intake,
                "matched_terms": matched_terms or None,
                "entities": entities or None,
                "status": status,
                "product_lines": product_lines,
                "item_id": item_id,
                "raw": Json(notice.raw),
            },
        )
        row = cur.fetchone()
    return (row["id"] if row else None), item_id


_NOTICE_FETCH_CHAR_CAP = 6000


def _fetch_notice_text(notice: NoticeRaw, src_kind: str) -> tuple[str, bool]:
    """F2/F24: for a ``search``/``rss``-hit-derived notice (title+snippet only), fetch the actual
    notice page so the LLM extraction can find real dates/agency/notice_type -- a snippet
    essentially never carries a deadline. ``api_json`` sources (TED/Contracts Finder) already
    parsed those fields structurally and don't need a page fetch. Never raises: a fetch failure
    (network, bot-block, SSRF-guard rejection, ...) just falls back to title+summary, same as
    before this fix -- a failing fetch must never block classification (docs/CONVENTIONS.md rule 9).

    Returns ``(text, page_verified)``. ``page_verified`` is ``True`` for a structured ``api_json``
    source (the record itself, not a page, is the authoritative source) or when a live page fetch
    for a ``search``/``rss`` notice actually returned real content; ``False`` when there was no URL
    to fetch, the fetch failed, or it came back empty -- meaning the caller is left with nothing
    but a search snippet/RSS blurb. F24's gate (:func:`_gate_reject_reason`) rejects an undated
    notice outright when this is ``False``: an unverified snippet with no dates is exactly the
    "unknown, undated, never actually looked at" shape that let stale/irrelevant rows in before.
    """
    base = f"{notice.title}\n\n{notice.summary}".strip()
    if src_kind == "api_json":
        return base, True
    if not notice.url:
        return base, False
    try:
        page = fetch_remote(notice.url)
    except Exception as exc:
        log.debug("tender_notice_fetch_failed", url=(notice.url or "")[:200], error=str(exc)[:200])
        return base, False
    text = (page.get("text") or "").strip()
    if text:
        return text[:_NOTICE_FETCH_CHAR_CAP], True
    return base, False


def _llm_classify(
    notice: NoticeRaw, *, role: str, interactive: bool, src_kind: str = "search"
) -> tuple[TenderExtract, bool]:
    """Pure LLM relevance/summary/date classification -- no DB writes (the caller decides what to
    do with the result: see ``scan_tenders``'s open-intake handling, W2b). DATA-guarded via
    ``wrap_data``, keyed by the notice's ``external_ref`` since no ``items`` row exists yet at this
    point. ``src_kind`` controls whether the notice page itself is fetched first (see
    ``_fetch_notice_text``). The prompt is augmented with up to 8 recent operator feedback examples
    (``eoa.tenders.feedback.tender_lessons_text``, mirroring ``pipeline.triage``'s lessons
    mechanism) so 👍/👎 feedback actually shapes future classifications, not just the stored
    ``intake``. Returns ``(extract, page_verified)`` -- the latter now only feeds the thin-snippet
    rescue (``_rescue_thin_snippet_from_trusted_tracker``), never a rejection."""
    text_for_llm, page_verified = _fetch_notice_text(notice, src_kind)
    try:
        lessons = tender_lessons_text()
    except Exception as exc:
        log.debug("tender_lessons_unavailable", error=str(exc)[:120])
        lessons = "אין עדיין משוב רלוונטיות קודם מהמשתמש."
    prompt = render(
        "tender_extract",
        source_name=notice.source_id,
        country=notice.country or "?",
        lessons=lessons,
        data=wrap_data(text_for_llm[:_NOTICE_FETCH_CHAR_CAP], notice.external_ref, notice.url or ""),
    )
    extract = chat_structured(
        role,
        TenderExtract,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="classify",
        interactive=interactive,
    )
    return extract, page_verified


def _cpv_gate_reject_reason(
    cpv_naics: list[str] | None, *, allow_prefixes: list[str], deny_prefixes: list[str]
) -> str | None:
    """R7-tenders (round-6 D9 finding 2): CPV-code family pre-filter. ``None`` (never rejected) when
    ``cpv_naics`` is empty -- most sources carry no CPV data at all, and this must never invent a
    rejection reason from an absent signal. Otherwise: ``None`` if *any* code starts with an
    ``allow_prefixes`` entry (a defence/optronics family always wins, even alongside a deny-family
    code on the same multi-classified notice); ``"cpv_non_defence_<prefix>"`` only when *every*
    code starts with a ``deny_prefixes`` entry and none matches ``allow_prefixes`` -- an obviously
    non-defence family (business/legal/HR consulting, education, health/social work, ...) with no
    countervailing defence signal at all. A code matching neither list (an ordinary, CPV-neutral
    family) is left alone -- this is a pre-filter for the *obvious* non-defence case named in the
    finding, not a whitelist requiring every notice to carry a recognised defence CPV code."""
    codes = [c for c in (cpv_naics or []) if c]
    if not codes:
        return None
    if any(code.startswith(p) for code in codes for p in allow_prefixes):
        return None
    denied = [p for code in codes for p in deny_prefixes if code.startswith(p)]
    if denied and len(denied) >= len(codes):
        return f"cpv_non_defence_{denied[0]}"
    return None


def _gate_reject_reason(
    notice: NoticeRaw,
    *,
    deny_domains: list[str],
    cpv_allow_prefixes: list[str] | None = None,
    cpv_deny_prefixes: list[str] | None = None,
) -> str | None:
    """W2b (open intake, docs/REVIEW_2026-09-06_evening.md, user requirement 2026-09-06 18:55,
    verbatim: "be open"): the only rejections that still drop a notice outright -- never inserted,
    ``tender_rejected`` logged with a reason. Returns ``None`` if the notice may be stored (which,
    post-W2b, is almost always the case once it has cleared the two-signal vocabulary gate).

    This replaces the old F24 "strict quality gate", which rejected on a low/absent LLM relevance
    verdict, an unrecognised ``notice_type``, a passed ``deadline``, a stale ``published_at``, or an
    unverified+undated snippet -- exactly the failure mode that silently dropped genuine Navy
    sources-sought notices whenever a trusted tracker domain (GovTribe, SAM.gov, ...) 403'd the page
    fetch (docs/MODULES.md "Round 4 discovery" W2). None of those are grounds to discard a notice
    any more -- they only shape ``relevance_score``/``intake`` (see ``scan_tenders``), which the
    operator's own 👍/👎 feedback and the self-tuning threshold then correct over time
    (``eoa.tenders.feedback``). Five HARD cases remain, none of which any amount of relevance
    feedback should override:

    - an explicit awarded/closed ``status_hint`` -- the source itself says this opportunity is
      already decided/gone, not merely "not relevant";
    - a denylisted document-hosting/aggregator domain (Scribd, DocPlayer, ...) -- never itself the
      authoritative notice, regardless of content;
    - a dead link -- no URL at all, so there is nothing for an analyst to open or verify;
    - an exact duplicate -- checked earlier, via ``_tender_exists``, before this gate ever runs;
    - (R7-tenders) an unambiguous non-defence CPV code family (``_cpv_gate_reject_reason``) -- a
      structural classification signal, not a relevance judgment, so it belongs alongside the other
      four rather than only shaping ``relevance_score``.
    """
    if notice.status_hint in ("awarded", "closed"):
        return f"status_hint_{notice.status_hint}"
    if _is_denylisted_domain(notice.url or "", deny_domains):
        return "denylisted_domain"
    if not notice.url:
        return "dead_link"
    cpv_reason = _cpv_gate_reject_reason(
        notice.cpv_naics,
        allow_prefixes=cpv_allow_prefixes if cpv_allow_prefixes is not None else DEFAULT_CPV_ALLOW_PREFIXES,
        deny_prefixes=cpv_deny_prefixes if cpv_deny_prefixes is not None else DEFAULT_CPV_DENY_PREFIXES,
    )
    if cpv_reason is not None:
        return cpv_reason
    return None


def _transition_closed() -> int:
    """Nightly status transition (F2): close any ``'open'`` tender whose ``deadline`` has passed
    (unchanged), AND any ``'open'`` tender that has no deadline at all but whose ``published_at`` is
    older than ``_STALE_DAYS`` -- the "assume open forever when undated" bug this fix addresses
    also applied to rows already sitting in the DB from before a deadline could be filled in."""
    today = dt.date.today()
    stale_before = today - dt.timedelta(days=_STALE_DAYS)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tenders SET status = 'closed'
            WHERE status = 'open' AND (
                (deadline IS NOT NULL AND deadline < %(today)s)
                OR (deadline IS NULL AND published_at IS NOT NULL AND published_at < %(stale_before)s)
            )
            RETURNING id
            """,
            {"today": today, "stale_before": stale_before},
        )
        return len(cur.fetchall())


# F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): a tender/RFI/RFP stays useful as a "recently
# closed" reference for a while, but a board cluttered with notices closed months or years ago
# (the review found rows closed 2015-2025 still showing) is not -- archived rows are never
# deleted (F1: no fetched-content row is ever destroyed outright), just excluded from the tenders
# board's default view (see eoa.api.services / TendersPage).
_ARCHIVE_AFTER_DAYS = 30


def redrive_all_tender_statuses(today: dt.date | None = None) -> int:
    """Round-3 (D9 finding 4b, docs/qa/loop/round_1_judge.md): a whole-table maintenance sweep
    that re-derives ``status`` purely from ``deadline`` vs ``today`` for every row that still
    carries one of the two "current" statuses (``'open'``/``'unknown'``) -- closes any row whose
    deadline has now passed, and (re-)opens an ``'unknown'`` row that has since acquired a
    still-future deadline (e.g. an LLM re-extraction backfilled one after the fact). Idempotent
    and safe to run as often as desired (a dedicated nightly/maintenance entry point, unlike
    ``_transition_closed``/``_archive_stale_closed`` above, which only run as the tail end of a
    full ``scan_tenders`` pass); never touches a terminal status this module assigns deliberately
    (``'awarded'``/``'archived'``) or a row with no deadline at all (that undated case is exactly
    what ``_transition_closed``'s ``_STALE_DAYS`` rule already covers). Returns the number of rows
    changed."""
    today = today or dt.date.today()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE tenders SET status = 'closed' "
            "WHERE status IN ('open', 'unknown') AND deadline IS NOT NULL AND deadline < %(today)s "
            "RETURNING id",
            {"today": today},
        )
        closed = len(cur.fetchall())
        cur.execute(
            "UPDATE tenders SET status = 'open' "
            "WHERE status = 'unknown' AND deadline IS NOT NULL AND deadline >= %(today)s "
            "RETURNING id",
            {"today": today},
        )
        reopened = len(cur.fetchall())
    if closed or reopened:
        log.info("tender_statuses_redriven", closed=closed, reopened=reopened)
    return closed + reopened


def _archive_stale_closed() -> int:
    """Nightly transition (F24): move any ``'closed'`` tender to ``'archived'`` once it has been
    closed for more than ``_ARCHIVE_AFTER_DAYS`` -- measured from ``deadline`` when known, else
    ``published_at``, else (both absent -- a stale-undated closure, see ``_transition_closed``)
    ``updated_at`` (the moment it was actually transitioned to ``'closed'``)."""
    today = dt.date.today()
    archive_before = today - dt.timedelta(days=_ARCHIVE_AFTER_DAYS)
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tenders SET status = 'archived'
            WHERE status = 'closed'
              AND COALESCE(deadline, published_at::date, updated_at::date) < %(archive_before)s
            RETURNING id
            """,
            {"archive_before": archive_before},
        )
        return len(cur.fetchall())


# --------------------------------------------------------------------------
# R7-tenders (round-6 D9 finding 2): repair path for existing candidates that predate the
# word-boundary keyword fix / CPV pre-filter above
# --------------------------------------------------------------------------


def _recheck_prefilter(
    text: str,
    cpv_naics: list[str] | None,
    src_kind: str,
    domain_keywords: list[str],
    procurement_signals: list[str],
    cpv_allow_prefixes: list[str],
    cpv_deny_prefixes: list[str],
) -> str | None:
    """Re-applies today's gate (post-R7-tenders word-boundary keyword matching + the CPV
    pre-filter) to an already-stored candidate's persisted text -- used only by the repair path
    below (:func:`find_prefilter_violations`), never by the live scan itself (which re-checks a
    fresh ``NoticeRaw`` via ``_passes_gate``/``_gate_reject_reason`` directly). ``text`` is expected
    to be ``items.clean_text`` (``"<title>\\n\\n<summary>"``, set at insert time by
    ``_insert_tender_and_item``) -- equivalent for matching purposes to the ``f"{title} {summary}"``
    string ``_matches_keywords``/``_has_procurement_signal`` build from a live ``NoticeRaw``, since
    both are casefolded before any comparison. Returns the violation reason, or ``None`` if the row
    would still clear today's gate."""
    text_cf = text.casefold()
    domain_terms = [kw for kw in (domain_keywords or DEFAULT_KEYWORDS) if _term_present(kw, text_cf)]
    if not domain_terms:
        return "keyword_gate_no_domain_term"
    if src_kind != "api_json":
        signals = procurement_signals or DEFAULT_PROCUREMENT_SIGNALS
        if not any(_term_present(sig, text_cf) for sig in signals):
            return "keyword_gate_no_procurement_signal"
    return _cpv_gate_reject_reason(
        cpv_naics, allow_prefixes=cpv_allow_prefixes, deny_prefixes=cpv_deny_prefixes
    )


@dataclass
class PrefilterViolation:
    """One existing ``tenders`` row that :func:`find_prefilter_violations` found no longer clears
    today's gate."""

    id: int
    source: str
    title: str
    reason: str


def find_prefilter_violations() -> list[PrefilterViolation]:
    """R7-tenders repair path (round-6 D9 finding 2, candidate id 34): every ``intake='candidate'``
    ``tenders`` row that would no longer clear today's gate -- read-only, always safe to call (a
    dry-run listing). Deliberately scoped to ``intake='candidate'`` only, exactly like
    ``scripts/repair_round6.py``'s own tender dedupe: an ``'accepted'`` row already carries operator
    trust (its own 👍, or the learned relevance threshold, promoted it) or predates this filter
    entirely, and re-litigating it here would contradict the module docstring's W2b open-intake
    philosophy ("through the relevance feedback given to each tender, the system tunes itself") --
    a filter *tightening* must never silently override an operator's own accept. A row whose
    ``source`` is no longer present in ``config/tenders.yaml`` is skipped (nothing to re-check
    the keyword vocabulary/kind against)."""
    sources_by_id = {s.id: s for s in load_tender_sources()}
    procurement_signals = load_procurement_signals()
    cpv_allow = load_cpv_allow_prefixes()
    cpv_deny = load_cpv_deny_prefixes()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.id, t.source, t.title, t.cpv_naics, i.clean_text "
            "FROM tenders t JOIN items i ON i.id = t.item_id "
            "WHERE t.intake = 'candidate'"
        )
        rows = cur.fetchall()
    violations: list[PrefilterViolation] = []
    for row in rows:
        src = sources_by_id.get(row["source"])
        if src is None:
            continue
        reason = _recheck_prefilter(
            row.get("clean_text") or row.get("title") or "",
            row.get("cpv_naics"),
            src.kind,
            src.keywords or DEFAULT_KEYWORDS,
            procurement_signals,
            cpv_allow,
            cpv_deny,
        )
        if reason is not None:
            violations.append(
                PrefilterViolation(
                    id=row["id"], source=row["source"], title=row["title"] or "", reason=reason
                )
            )
    return violations


def repair_relevance_prefilter(*, apply: bool = False) -> list[PrefilterViolation]:
    """Finds every current prefilter violation (:func:`find_prefilter_violations``); with
    ``apply=True`` also archives those rows (``status='archived'`` -- F1: no fetched-content row is
    ever deleted outright, same convention as ``_archive_stale_closed``). ``apply=False``
    (the default): dry run, no writes at all. The ``UPDATE``'s own ``WHERE ... AND intake =
    'candidate'`` is a second, defence-in-depth guard against ever touching an ``'accepted'`` row
    even if the violation list were somehow stale by the time this runs; ``tender_feedback`` is a
    separate table this function never references, so it is untouched either way."""
    violations = find_prefilter_violations()
    if apply and violations:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tenders SET status = 'archived', updated_at = now() "
                "WHERE id = ANY(%(ids)s::bigint[]) AND intake = 'candidate'",
                {"ids": [v.id for v in violations]},
            )
    return violations


# --------------------------------------------------------------------------
# TENDERS-SAM (2026-09-08, docs/qa/content_review/TENDERS-SAM.md item 2): re-scoring existing
# tenders rows against the negative-keyword/defence-context relevance signal -- same
# find_*/repair_* dry-run/apply shape as find_prefilter_violations/repair_relevance_prefilter
# above, deliberately kept as a *separate* pair of functions rather than folded into that one:
# a prefilter violation (no domain term / no procurement signal / a denied CPV family) means the
# row shouldn't have cleared the gate AT ALL and gets archived outright, whereas a negative-keyword
# hit is only ever a relevance *demotion* (per W2b, "never reject outright on a relevance signal")
# -- the row stays exactly where it is, only relevance_score/intake move.
# --------------------------------------------------------------------------


@dataclass
class RelevanceRescoreResult:
    """One existing ``tenders`` row :func:`find_relevance_demotions` found should have its
    ``relevance_score``/``intake`` lowered under today's negative-keyword/defence-context check."""

    id: int
    source: str
    title: str
    negative_term: str
    before_score: float
    after_score: float
    before_intake: str
    after_intake: str


def find_relevance_demotions() -> list[RelevanceRescoreResult]:
    """Every existing ``tenders`` row (any ``intake`` except ``'rejected-by-user'`` -- an explicit
    operator decision this must never override, mirroring :func:`find_prefilter_violations`'s own
    "never re-litigate an operator's own accept" rule, extended here to never re-litigate a
    reject either) whose title+summary+agency triggers :func:`_negative_keyword_penalty` under
    today's ``negative_keywords``/``defence_context_signals`` config -- read-only, always safe to
    call. Only ever a *demotion*: a row already at/below the penalty ceiling, or whose recomputed
    intake would (absurdly) come out as ``'accepted'`` from a lower score, is left out/unchanged --
    this can never raise a row's relevance_score or promote a 'candidate' to 'accepted'."""
    negative_keywords = load_negative_keywords()
    defence_context_signals = load_defence_context_signals()
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, source, title, summary_he, agency, relevance_score, intake FROM tenders "
            "WHERE intake != 'rejected-by-user'"
        )
        rows = cur.fetchall()
    results: list[RelevanceRescoreResult] = []
    for row in rows:
        notice = NoticeRaw(
            source_id=row.get("source") or "",
            external_ref="",
            title=row.get("title") or "",
            summary=row.get("summary_he") or "",
            agency=row.get("agency"),
        )
        term = _negative_keyword_penalty(notice, negative_keywords, defence_context_signals)
        if term is None:
            continue
        before_score_raw = row.get("relevance_score")
        before_score = float(before_score_raw) if before_score_raw is not None else 0.5
        after_score = min(before_score, _NEGATIVE_KEYWORD_RELEVANCE_CEILING)
        if after_score >= before_score:
            continue  # already at/below the ceiling -- nothing to change
        before_intake = row.get("intake") or "candidate"
        after_intake = _intake_for_score(after_score)
        # Never re-promote: a demotion in score must never come out as a "more accepted" intake
        # than the row already had.
        if before_intake == "candidate" and after_intake == "accepted":
            after_intake = "candidate"
        results.append(
            RelevanceRescoreResult(
                id=row["id"],
                source=row.get("source") or "",
                title=row.get("title") or "",
                negative_term=term,
                before_score=before_score,
                after_score=after_score,
                before_intake=before_intake,
                after_intake=after_intake,
            )
        )
    return results


def repair_relevance_scores(*, apply: bool = False) -> list[RelevanceRescoreResult]:
    """Finds every current relevance demotion (:func:`find_relevance_demotions`); with
    ``apply=True`` also writes the demoted ``relevance_score``/``intake`` (``apply=False``, the
    default: dry run, no writes). The ``UPDATE``'s own ``WHERE ... AND intake != 'rejected-by-
    user'`` is a second, defence-in-depth guard, same convention as
    :func:`repair_relevance_prefilter`'s own ``intake = 'candidate'`` guard."""
    results = find_relevance_demotions()
    if apply and results:
        with connection() as conn, conn.cursor() as cur:
            for r in results:
                cur.execute(
                    "UPDATE tenders SET relevance_score = %(score)s, intake = %(intake)s, updated_at = now() "
                    "WHERE id = %(id)s AND intake != 'rejected-by-user'",
                    {"score": r.after_score, "intake": r.after_intake, "id": r.id},
                )
    return results


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


@dataclass
class TenderStats:
    sources_scanned: int = 0
    sources_failed: int = 0
    notices_fetched: int = 0
    matched: int = 0
    duplicates: int = 0
    llm_used: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    # W2b: gate_rejected now only counts the four HARD reasons (status_hint/denylisted domain/dead
    # link -- exact duplicates are their own `duplicates` counter above). A low/absent LLM verdict
    # no longer rejects anything -- see `accepted`/`candidates` below instead.
    gate_rejected: int = 0
    inserted: int = 0
    accepted: int = 0  # W2b: inserted with intake='accepted' (relevance_score >= learned threshold)
    candidates: int = 0  # W2b: inserted with intake='candidate' (below the learned threshold)
    closed_transitioned: int = 0
    archived_transitioned: int = 0
    statuses_redriven: int = 0  # round-3 D9 finding 4b: redrive_all_tender_statuses()


LLM_BUDGET_SECONDS_DEFAULT = 15 * 60


def _api_json_source_enabled(src: TenderSource) -> bool:
    """R7-tenders (finding 1c): an ``api_json`` source is scanned when it is ``verified: true``
    (the pre-existing rule -- a live-probed, keyless, working endpoint), OR when it is marked
    ``needs_key_env_var`` (round-6 D9's SAM.gov Opportunities API v2 case, ``config/tenders.yaml``'s
    ``sam_gov_api``) AND that environment variable is actually set to a non-empty value at scan
    time. This makes a key-gated source spring to life automatically the moment its key is added to
    the environment -- no code or config change (flipping ``verified: true`` by hand) required --
    while still never attempting a request without one (the brief's "skip silently otherwise").
    Every other unverified source (bot-protected, wrong endpoint, deliberately out of scope --
    see each entry's own ``notes``) stays skipped exactly as before."""
    if src.verified:
        return True
    if src.needs_key_env_var:
        return bool(os.environ.get(src.needs_key_env_var, "").strip())
    return False


def _order_sources_by_priority(sources: list[TenderSource]) -> list[TenderSource]:
    """W2b: nudge (never gate) scan order by each source's learned priority decrement
    (``eoa.tenders.feedback.get_source_priorities`` -- 0 for every source that hasn't earned one).
    A stable sort on ``-decrement`` keeps every source at its original config-file position among
    peers at the same priority, and only sinks a chronically-never-👍 source later in the pass --
    it is still scanned every run, just later. Never raises: a priority-read failure just scans in
    the original config order, same as before this feature existed."""
    try:
        priorities = get_source_priorities()
    except Exception as exc:
        log.debug("tender_source_priority_read_failed", error=str(exc)[:200])
        return sources
    if not priorities:
        return sources
    return sorted(sources, key=lambda s: -priorities.get(s.id, 0))


def scan_tenders(
    since_days: int = 3,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_budget_s: float = LLM_BUDGET_SECONDS_DEFAULT,
    sources: list[TenderSource] | None = None,
) -> TenderStats:
    """FR/section-5.2 entry point: scan every configured tender source, apply the two-signal gate,
    dedupe, LLM-relevance-classify within ``llm_budget_s`` (best-effort -- degrades to the neutral
    0.5 relevance_score when unavailable, per the module docstring's W2b section), insert
    ``tenders``+``items`` rows for everything that isn't a hard rejection (open intake), and
    transition passed-deadline tenders to ``status='closed'``. A single source failing (network,
    parse error, ...) never stops the others (docs/CONVENTIONS.md rule 9)."""
    stats = TenderStats()
    today = dt.date.today()
    llm_deadline = time.monotonic() + llm_budget_s
    seen_refs: set[str] = set()
    procurement_signals = load_procurement_signals()
    deny_domains = load_deny_domains()
    cpv_allow_prefixes = load_cpv_allow_prefixes()
    cpv_deny_prefixes = load_cpv_deny_prefixes()
    negative_keywords = load_negative_keywords()
    defence_context_signals = load_defence_context_signals()

    ordered_sources = _order_sources_by_priority(sources if sources is not None else load_tender_sources())
    for src in ordered_sources:
        if src.kind == "html":
            continue
        if src.kind == "api_json" and not _api_json_source_enabled(src):
            continue
        try:
            notices = _collect_source_notices(src, deny_domains)
        except Exception as exc:
            log.warning("tender_source_failed", source=src.id, error=str(exc)[:200])
            stats.sources_failed += 1
            continue
        stats.sources_scanned += 1
        stats.notices_fetched += len(notices)

        for notice in notices:
            if notice.external_ref in seen_refs:
                continue
            if src.kind == "api_json" and not _within_window(notice, since_days, today):
                continue
            domain_terms = _passes_gate(notice, src.kind, src.keywords, procurement_signals)
            if not domain_terms:
                continue
            stats.matched += 1
            if _tender_exists(notice.external_ref):
                stats.duplicates += 1
                seen_refs.add(notice.external_ref)
                continue
            candidate_portal = _notice_portal(notice.url)
            if candidate_portal and _candidate_duplicate_exists(
                _normalize_tender_title(notice.title), candidate_portal
            ):
                stats.duplicates += 1
                seen_refs.add(notice.external_ref)
                log.info(
                    "tender_candidate_duplicate_skipped",
                    external_ref=notice.external_ref,
                    title=(notice.title or "")[:120],
                    portal=candidate_portal,
                )
                continue

            extract: TenderExtract | None = None
            page_verified = False
            if time.monotonic() < llm_deadline:
                try:
                    extract, page_verified = _llm_classify(
                        notice, role=role, interactive=interactive, src_kind=src.kind
                    )
                    stats.llm_used += 1
                except ResourceUnavailable:
                    stats.llm_deferred += 1
                except LLMOutputError as exc:
                    log.warning(
                        "tender_llm_classify_failed", external_ref=notice.external_ref, error=str(exc)[:200]
                    )
                    stats.llm_failed += 1
                except Exception as exc:
                    # Never let one notice's LLM call take down the whole scan (docs/
                    # CONVENTIONS.md rule 9) -- extract just stays None, which _relevance_score_for
                    # treats as "unknown" (0.5), never a rejection (W2b, open intake).
                    log.warning(
                        "tender_llm_classify_unexpected_error",
                        external_ref=notice.external_ref,
                        error=str(exc)[:200],
                    )
                    stats.llm_failed += 1
            else:
                stats.llm_deferred += 1

            # F2/F13: fill in date/agency/country gaps from the LLM extraction (when it ran and
            # succeeded), then fall back to the URL-domain country table regardless -- both mutate
            # `notice` in place so `_insert_tender_and_item` (which reads notice.* directly) picks
            # them up without needing its own signature to grow further.
            if extract is not None:
                _apply_extraction_to_notice(notice, extract)
            _apply_domain_country_fallback(notice)

            if extract is not None:
                rescued = _rescue_thin_snippet_from_trusted_tracker(
                    notice,
                    extract,
                    page_verified=page_verified,
                    src_kind=src.kind,
                    domain_terms=domain_terms,
                    procurement_signals=procurement_signals,
                )
                extract = rescued

            reject_reason = _gate_reject_reason(
                notice,
                deny_domains=deny_domains,
                cpv_allow_prefixes=cpv_allow_prefixes,
                cpv_deny_prefixes=cpv_deny_prefixes,
            )
            if reject_reason is not None:
                stats.gate_rejected += 1
                log.info(
                    "tender_rejected",
                    external_ref=notice.external_ref,
                    reason=reject_reason,
                    relevance=(extract.relevance if extract else None),
                )
                seen_refs.add(notice.external_ref)
                continue

            # W2b (open intake): everything past the four hard checks above is stored -- a low or
            # absent LLM verdict only shapes relevance_score/intake (self-tuning, see
            # eoa.tenders.feedback), it is never itself a reason to discard the notice.
            relevance_score = _relevance_score_for(
                extract, notice, negative_keywords, defence_context_signals
            )
            intake = _intake_for_score(relevance_score)
            try:
                tender_id, _item_id = _insert_tender_and_item(
                    notice,
                    (extract.matched_terms or domain_terms) if extract is not None else domain_terms,
                    relevance=(
                        extract.relevance if extract is not None else max(1, min(10, len(domain_terms)))
                    ),
                    relevance_score=relevance_score,
                    intake=intake,
                    summary_he=(extract.summary_he if extract is not None else ""),
                    entities=(extract.entities if extract is not None else None),
                    notice_type=(extract.notice_type if extract is not None else None),
                )
            except Exception as exc:
                log.warning("tender_insert_failed", external_ref=notice.external_ref, error=str(exc)[:200])
                continue
            seen_refs.add(notice.external_ref)
            if tender_id is None:
                stats.duplicates += 1
                continue
            stats.inserted += 1
            if intake == "accepted":
                stats.accepted += 1
            else:
                stats.candidates += 1

    stats.closed_transitioned = _transition_closed()
    stats.archived_transitioned = _archive_stale_closed()
    # D9 finding 4b (round-3): a comprehensive whole-table status re-derivation on top of the two
    # targeted transitions above -- also reopens an 'unknown' row that has since acquired a
    # still-future deadline, which neither of the above ever does.
    stats.statuses_redriven = redrive_all_tender_statuses(today)
    log.info("tender_scan_done", **vars(stats))
    return stats
