"""Stage 2 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.2): the fixed research plan -- one
``eoa.search.deep_search.investigate()`` call per topic, each with a targeted Hebrew question and
the corpus summary (``CorpusResult.summary_he()``) as ``context_he``. Topics run sequentially on
whatever LLM chain is configured (cloud chain when ``EOA_PIPELINE=1``/``llm_providers.mode ==
"cloud"``, same as every other pipeline stage -- ``investigate()`` itself resolves that).

:func:`build_topics` is pure and deterministic (no network/DB) -- it is what a dry run exercises to
"prove the queries work" without spending the (network/GPU) budget of a real multi-round
investigation; :func:`run_plan` is the real pipeline entry point, called from
``eoa.dossier.report.build_product_dossier``.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import structlog

from eoa.config import settings
from eoa.dossier.corpus import CorpusResult
from eoa.dossier.datasheet import hunt_datasheets
from eoa.dossier.gaps import build_gap_followup_topics, extract_gaps_from_previous, gap_status
from eoa.dossier.programs import (
    MAX_PROGRAMME_PLATFORMS,
    detect_customer_countries,
    extra_langs_for_countries,
    identify_platforms,
    parse_programme_deals,
    platform_deal_question_he,
)
from eoa.fetch.remote import fetch_remote
from eoa.search.deep_search import Investigation, investigate

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 2): web-source hygiene for the citation registry -- dedupe by normalized
# URL, capture a real title, classify kind/reliability from the domain, and drop pages that never
# actually mention the product (a French WeTransfer forum thread was one live example). All of this
# runs at *row-construction* time, before ``corpus.next_n`` ever hands out a number -- a filtered-out
# or merged-into-a-duplicate source therefore never consumes a citation number in the first place, so
# there is no post-hoc renumbering/remapping needed (the plan's own "cites must be remapped, never
# left dangling" concern cannot arise: nothing downstream ever cited a number that was never issued).
# --------------------------------------------------------------------------

#: Vendor-domain / trade-press / forum / reference domain heuristics -- deliberately small and
#: conservative (a false "press" default is harmless; a false "vendor_official" is not, so that one
#: requires an actual slug match against the vendor name, not a fixed list).
_TRADE_PRESS_DOMAINS = {
    "defensenews.com",
    "janes.com",
    "shephardmedia.com",
    "defense-update.com",
    "breakingdefense.com",
    "flightglobal.com",
    "military.com",
    "thedrive.com",
    "airforce-technology.com",
    "army-technology.com",
    "naval-technology.com",
    "globes.co.il",
    "calcalist.co.il",
    "themarker.com",
}
_GENERAL_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "wiktionary.org",
    "britannica.com",
    "dictionary.com",
    "investopedia.com",
}
_FORUM_DOMAIN_HINTS = ("forum", "reddit.com", "quora.com", "wetransfer.com", "groups.google.com")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _bare_domain(text: str) -> str:
    return _NON_ALNUM_RE.sub("", (text or "").lower())


def _host(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    """A dedup KEY, not a display URL: lower-cased host with a leading ``www.`` stripped, scheme
    forced to ``https``, trailing slash and query/fragment dropped. The *stored* ``url`` on the
    registry row is always the original, unmodified string -- this is only ever compared against
    another call's own output."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def classify_web_source(url: str, vendor: str | None) -> tuple[str, str]:
    """``(kind, reliability)`` for one web source, from its domain alone: ``vendor_official`` when
    the domain itself contains the vendor's own (slugified) name -- ``reliability="primary"``;
    a known general-reference/encyclopedia domain -> ``reference``/``low``; a forum-shaped domain ->
    ``forum``/``low``; a curated defense/business trade-press domain -> ``trade_press``/``secondary``;
    anything else defaults to ``press``/``secondary`` (a plain news/blog domain, the common case)."""
    domain = _host(url)
    bare = _bare_domain(domain)
    vendor_slug = _bare_domain(vendor or "")
    if vendor_slug and len(vendor_slug) >= 3 and vendor_slug in bare:
        return "vendor_official", "primary"
    if any(domain == d or domain.endswith("." + d) for d in _GENERAL_REFERENCE_DOMAINS):
        return "reference", "low"
    if any(hint in domain for hint in _FORUM_DOMAIN_HINTS):
        return "forum", "low"
    if any(domain == d or domain.endswith("." + d) for d in _TRADE_PRESS_DOMAINS):
        return "trade_press", "secondary"
    return "press", "secondary"


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item A.1/A.2): vendor-domain resolution + MUST-READ vendor pages. Run 4's
# ``specifications`` topic read only ``instro.com`` (a reseller catalog page) and never reached
# ``elbitsystems.com/unmanned/maritime/usv-payloads/spectro`` or ``elbitsystems.com/product/
# spectro-maritime/`` -- the two pages that actually carry SPECTRO XR's concrete specs (7-inch
# common spotter, up to 9 digital sensors, eye-safe LRF, quadrant detector, Jetson Xavier) --
# because the ReAct search loop is free to wander off to whatever a general web search surfaces
# first. Two independent, deterministic (code-only, no LLM judgment call) mitigations:
#
# 1. :func:`gather_must_read_urls` / :func:`run_must_read` -- every ``vendor_official`` URL already
#    known (this run's own registry, or the previous dossier of the same ``product_key``) is fetched
#    unconditionally, once, before the topic loop starts, and folded into every topic's own
#    ``context_he`` -- a topic can no longer simply never encounter the vendor's own page.
# 2. :func:`resolve_vendor_domain` + the site-restricted search preamble below -- a spec-ish topic's
#    own question is prefixed with an explicit "search the vendor's own site first" instruction.
# --------------------------------------------------------------------------

#: Small, deliberately conservative vendor-name -> official-domain map -- used only as a fallback
#: when no ``vendor_official`` URL is already known anywhere in the corpus (a fresh product with no
#: prior dossier and no vendor-domain DB item yet). Keyed by the same bare (non-alnum-stripped,
#: lower-cased) form :func:`_bare_domain` produces, so lookup is a plain substring check, same
#: discipline as :func:`classify_web_source`'s own vendor-slug match.
_VENDOR_DOMAIN_HINTS: dict[str, str] = {
    "elbitsystems": "elbitsystems.com",
    "elbit": "elbitsystems.com",
    "rafael": "rafael.co.il",
    "rafaeladvanceddefensesystems": "rafael.co.il",
    "iai": "iai.co.il",
    "israelaerospaceindustries": "iai.co.il",
    "lockheedmartin": "lockheedmartin.com",
    "rtx": "rtx.com",
    "raytheon": "rtx.com",
    "northropgrumman": "northropgrumman.com",
    "l3harris": "l3harris.com",
    "teledyneflir": "flir.com",
    "flir": "flir.com",
    "thales": "thalesgroup.com",
    "thalesgroup": "thalesgroup.com",
    "safran": "safran-group.com",
    "leonardo": "leonardo.com",
    "hensoldt": "hensoldt.net",
    "aselsan": "aselsan.com.tr",
    "controp": "controp.com",
    "saab": "saab.com",
}

#: The three topics MUST-READ vendor pages exist for (section A.1) -- deliberately the first three
#: entries of :data:`TOPICS`, so folding the must-read summaries into the shared ``context_he``
#: before the topic loop starts already satisfies "before the specifications/versions/performance
#: topics start" for every one of them (and is harmless context for every later topic too).
SITE_RESTRICTED_TOPIC_KEYS: frozenset[str] = frozenset({"specifications", "versions", "performance"})

#: Bound on how many vendor pages one dossier build fetches up front -- a deliberate, small cap
#: (network + wall-clock cost, paid once per dossier build, not per topic).
_MUST_READ_URL_CAP = 6

_EXCERPT_MAX_CHARS = 1200


def resolve_vendor_domain(vendor: str | None, registry: list[dict[str, Any]] | None = None) -> str | None:
    """The vendor's own official web domain (bare host, e.g. ``"elbitsystems.com"``) -- preferring
    an actual ``vendor_official``-classified URL already sitting in ``registry`` (real evidence from
    this run or a merged-in previous dossier) over the small static hint map, which is only a
    fallback for a product with no such URL known yet."""
    for row in registry or []:
        if row.get("source_kind") == "vendor_official" and row.get("url"):
            domain = _host(row["url"])
            if domain:
                return domain
    vendor_key = _bare_domain(vendor or "")
    if not vendor_key:
        return None
    for name_key, domain in _VENDOR_DOMAIN_HINTS.items():
        if name_key in vendor_key or vendor_key in name_key:
            return domain
    return None


def _previous_vendor_official_urls(previous: dict[str, Any] | None) -> list[str]:
    """``vendor_official`` URLs from the previous dossier of the same ``product_key`` (its own
    persisted ``sources`` column, ``CorpusResult.previous`` is the raw ``product_dossiers`` row) --
    still worth re-reading even though they were already read once, since ``run_must_read`` folds
    their content back into *this* run's own topic context, independent of whichever registry row
    number they carried last time."""
    if not previous:
        return []
    sources = previous.get("sources") or []
    return [
        s.get("url")
        for s in sources
        if s.get("url") and (s.get("source_kind") == "vendor_official" or s.get("kind") == "vendor_official")
    ]


def gather_must_read_urls(corpus: CorpusResult) -> list[str]:
    """Deterministic (no network, no LLM) -- every URL section A.1 requires reading unconditionally:
    a ``vendor_official`` URL from the previous dossier of the same product, plus every URL already
    in ``corpus.registry`` whose host sits on the resolved vendor domain. Deduped by normalized URL,
    capped at :data:`_MUST_READ_URL_CAP`. Returns ``[]`` (a no-op for :func:`run_must_read`) when no
    vendor domain can be resolved at all -- a product with neither a previous dossier nor any
    vendor-domain DB item yet, and no hint-map entry for its vendor."""
    domain = resolve_vendor_domain(corpus.vendor, corpus.registry)
    urls: list[str] = []
    seen_norm: set[str] = set()

    def _add(url: str | None) -> None:
        if not url:
            return
        norm = normalize_url(url)
        if norm in seen_norm:
            return
        seen_norm.add(norm)
        urls.append(url)

    for url in _previous_vendor_official_urls(corpus.previous):
        _add(url)
    if domain:
        for row in corpus.registry:
            url = row.get("url")
            if not url:
                continue
            host = _host(url)
            if host == domain or host.endswith("." + domain):
                _add(url)
    return urls[:_MUST_READ_URL_CAP]


def _deterministic_excerpt(text: str, *, max_chars: int = _EXCERPT_MAX_CHARS) -> str:
    """No LLM call -- just whitespace-normalized truncation of the fetched page's own extracted
    text. This is what makes the MUST-READ step "no LLM decision": the page is always read and its
    own words are what land in the topic context, never a model's paraphrase of whether it bothered
    to look."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars].rstrip() + "…"


def run_must_read(corpus: CorpusResult, urls: list[str]) -> list[str]:
    """Fetches every ``urls`` entry (deterministic ``eoa.fetch.remote.fetch_remote`` page reader,
    the same primitive ``eoa.search.deep_search._tool_read`` itself wraps -- no LLM in the loop
    deciding whether to bother) and returns one formatted Hebrew context block per successfully
    fetched page -- ready to prepend into every topic's own ``context_he``.

    A URL that is already a registry row -- typically a DB item whose own ``url`` happens to sit on
    the vendor's domain, e.g. a press item that IS the vendor's own announcement page -- is never
    re-numbered: its fetched excerpt is folded into a context block under that row's OWN ``n``
    instead of minting a second, duplicate registry entry for the identical page (same "reuse the
    existing row" discipline :func:`run_plan`'s own topic-read dedup already applies). Only a URL
    genuinely new to the registry gets a fresh row. A fetch failure is logged and skipped (never
    fails the whole dossier build, same discipline as every other network-touching helper here)."""
    blocks: list[str] = []
    existing_by_norm = {normalize_url(r["url"]): r for r in corpus.registry if r.get("url")}
    already_fetched: set[str] = set()
    for url in urls:
        norm = normalize_url(url)
        if norm in already_fetched:
            continue
        try:
            page = fetch_remote(url)
        except Exception as exc:  # a must-read fetch failure must never break the dossier build
            log.warning("dossier.must_read_fetch_failed", url=url[:300], error=str(exc)[:200])
            continue
        text = page.get("text") or ""
        excerpt = _deterministic_excerpt(text)
        if not excerpt:
            log.info("dossier.must_read_empty_page", url=url[:300])
            continue
        already_fetched.add(norm)
        existing = existing_by_norm.get(norm)
        if existing is not None:
            n = existing["n"]
            title = existing.get("title") or (page.get("title") or "").strip() or _host(url)
        else:
            title = (page.get("title") or "").strip() or _host(url)
            kind, reliability = classify_web_source(url, corpus.vendor)
            row = {
                "n": corpus.next_n,
                "kind": "web",
                "id": None,
                "title": title,
                "url": url,
                "source_name": title,
                "published_at": page.get("published_at"),
                "topic": "must_read",
                "source_kind": kind,
                "reliability": reliability,
                "accessed_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            corpus.registry.append(row)
            existing_by_norm[norm] = row
            n = row["n"]
        blocks.append(f"[{n}] (עמוד יצרן, נקרא מראש) {title}\n{excerpt}")
        log.info("dossier.must_read_page", url=url[:300], n=n)
    return blocks


def _read_summary_text(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    return f"{entry.get('title') or ''}\n{entry.get('summary') or ''}"


#: PD-fix-4 (2026-09-09, item A.3): run 4's ``partnerships``/``competitors`` topics kept two pages
#: that were never about SPECTRO XR at all (a Romania Watchkeeper-X purchase, a generic "four
#: aircraft technology contracts" piece) despite this very function already existing --
#: ``_summarise_page``'s own prompt (``eoa.search.deep_search``) asks the model to write literally
#: "לא רלוונטי" for an off-topic page, but a model that instead explains *why* a page is off-topic
#: ("הדף עוסק בעסקת Watchkeeper X... אינו קשור ל-SPECTRO XR") ends up name-dropping the very product
#: it is disclaiming -- the old naive substring check then reads that negated mention as a positive
#: hit. An explicit negation/not-relevant marker anywhere in the summary now short-circuits to
#: "does not mention the product" regardless of any other substring match in the same text.
_NOT_RELEVANT_MARKERS_HE = (
    "לא רלוונטי",
    "אינו רלוונטי",
    "אינה רלוונטית",
    "לא קשור",
    "אינו קשור",
    "אינה קשורה",
    "not relevant",
    "no mention",
    "not related",
    "unrelated to",
)


def _mentions_product(
    text: str, product_name: str, aliases: list[str], *, extra_terms: list[str] | None = None
) -> bool:
    """A page's own read summary must actually mention the product (name or an alias, plain
    case-insensitive substring -- this is a page-relevance check, not the stricter whole-word
    corpus-item gate in ``eoa.dossier.corpus``) or it is dropped outright rather than being handed
    to the extraction model as if it were on-topic (the live French WeTransfer forum thread that
    never mentioned SPECTRO XR at all).

    ``extra_terms`` (LESSONS-1 item 2): additional terms that ALSO count as relevance on their own
    -- used for a programme-deal-topic's own reads, where a page about the carrier PLATFORM (e.g.
    "Watchkeeper X"), not the product by name, is exactly the evidence being searched for (see
    ``docs/qa/content_review/LESSONS-fable-dossier.md`` finding 2 -- the old relevance filter threw
    away a Romania Watchkeeper X purchase page for never naming SPECTRO XR)."""
    hay = (text or "").casefold()
    if not hay.strip():
        # No summary at all to judge by -- err on the side of keeping it (a read page whose
        # summariser produced nothing is a summariser gap, not evidence of irrelevance).
        return True
    if any(marker in hay for marker in _NOT_RELEVANT_MARKERS_HE):
        return False
    if product_name and product_name.casefold() in hay:
        return True
    if any(a and a.casefold() in hay for a in aliases):
        return True
    return any(t and t.casefold() in hay for t in (extra_terms or []))


@dataclass(frozen=True)
class Topic:
    key: str
    title_he: str
    question_template_he: str

    def question_he(self, *, product_name: str, vendor: str, aliases_he: str) -> str:
        return self.question_template_he.format(
            product_name=product_name, vendor=vendor or "היצרן", aliases_he=aliases_he or "אין"
        )


#: Section 4.2's fixed topic list, in the order the report renders them. Every topic's own
#: question names the product/vendor/aliases explicitly (never a bare "the product") per
#: ``eoa.search.deep_search.extract_anchors``'s own anchoring discipline -- a question with no
#: proper-noun anchor at all would have every one of its ``search`` calls rejected by that guard.
TOPICS: tuple[Topic, ...] = (
    Topic(
        "specifications",
        "מפרט ודף נתונים",
        "מה המפרט הטכני המלא והמדויק של {product_name} ({vendor})? חפש בעדיפות בדפי נתונים (datasheet) "
        "ועלוני יצרן רשמיים. כינויים נוספים למוצר: {aliases_he}.",
    ),
    Topic(
        "versions",
        "גרסאות וציר זמן",
        "אילו גרסאות/דגמים/וריאנטים ידועים של {product_name} ({vendor}) פורסמו, ומתי כל אחד הוצג "
        "לראשונה ומה השתנה בו לעומת הקודם? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "performance",
        "ביצועים (מוצהר מול נמדד)",
        "מהם נתוני הביצועים המוצהרים על ידי היצרן של {product_name} ({vendor}), ומהם נתוני ביצועים "
        "שנמדדו/הופעלו בפועל (ניסוי, שטח) אם דווחו בנפרד? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "maturity",
        "בשלות ופריסה",
        "מה מצב הבשלות (TRL) של {product_name} ({vendor}), מי המפעילים הידועים, על אילו פלטפורמות "
        "הוא שולב, ומתי הייתה הפריסה המבצעית הראשונה שלו? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        # LESSONS-1 item 2 (PD-datasheet, 2026-09-09): identifies the carrier platforms
        # (eoa.dossier.programs.identify_platforms scans this topic's own finding text) that
        # run_plan then runs a dedicated deal search for, per-platform -- see run_plan's own
        # "programme deals" block below. Placed right after "maturity" (which already asks about
        # platform integration in passing) and before "deals" (a general deal search benefits from
        # already knowing the platform names, even though the programme-deal search itself is
        # independent of this ordering).
        "platforms_and_programmes",
        "פלטפורמות ותוכניות",
        "על אילו פלטפורמות/כלי טיס/כלי שיט משולב {product_name} ({vendor})? ציין שמות פלטפורמה "
        "מדויקים (למשל דגם/גרסה, כגון Hermes 900 או Watchkeeper X). כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "deals",
        "עסקאות ולקוחות",
        "אילו עסקאות/חוזים/מכירות FMS ידועים עבור {product_name} ({vendor}) -- מי הלקוח, מתי, איזה "
        "היקף/כמות? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "pricing",
        "מחירים",
        "מה ידוע על מחיר {product_name} ({vendor}) מתוך חוזה, מכרז, שורת תקציב, הודעת FMS או ציטוט "
        "רשמי -- כולל הבסיס (ליחידה/למנה/לתוכנית כולה), הכמות והתאריך? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "partnerships",
        "שותפויות ואינטגרציות",
        "אילו שותפויות, קבלני משנה, שיתופי פיתוח או משווקים ידועים סביב {product_name} ({vendor})? "
        "כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "competitors",
        "מתחרים",
        "אילו מוצרים מתחרים ל-{product_name} ({vendor}) מוזכרים במפורש במקורות, ומה ההשוואה ביניהם "
        "(מפרט, מחיר, בשלות)? כינויים נוספים: {aliases_he}.",
    ),
    Topic(
        "regulatory",
        "רגולציה וייצוא",
        "מה ידוע על משטר הייצוא (ITAR/EAR/DECA וכדומה) וההגבלות הרגולטוריות החלות על {product_name} "
        "({vendor})? כינויים נוספים: {aliases_he}.",
    ),
)


def build_topics(
    product_name: str,
    vendor: str | None = None,
    aliases: list[str] | None = None,
    *,
    max_topics: int | None = None,
) -> list[dict[str, str]]:
    """Deterministic, no-network topic/question list -- what a dry run inspects to "prove the
    queries work" without spending a real investigation's budget."""
    cap = max_topics if max_topics is not None else settings().dossier.max_topics
    aliases_he = ", ".join(aliases or []) or "אין"
    out = []
    for topic in TOPICS[: max(cap, 0)]:
        out.append(
            {
                "key": topic.key,
                "title_he": topic.title_he,
                "question_he": topic.question_he(
                    product_name=product_name, vendor=vendor or "", aliases_he=aliases_he
                ),
            }
        )
    return out


@dataclass
class TopicFinding:
    key: str
    title_he: str
    question_he: str
    investigation: Investigation
    source_ns: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanResult:
    findings: list[TopicFinding] = field(default_factory=list)

    def context_blocks_he(self) -> list[str]:
        """One text block per topic, ready to feed ``eoa.dossier.extract``'s synthesis prompt --
        the topic title, its own question, the investigation's outcome/confidence and answer, each
        prefixed with the registry numbers of the sources it actually read (numbered by
        ``eoa.dossier.plan.run_plan``, continuing the corpus's own registry sequence)."""
        blocks = []
        for f in self.findings:
            inv = f.investigation
            result = inv.result
            if result is None:
                continue
            cite_ns = [s.get("n") for s in f.source_ns]
            cites_line = ", ".join(f"[{n}]" for n in cite_ns) or "(אין מקורות שנקראו)"
            blocks.append(
                f"### נושא: {f.title_he}\n"
                f"שאלה: {f.question_he}\n"
                f"תוצאה: {result.outcome} (ביטחון {result.confidence:.2f})\n"
                f"מקורות שנקראו: {cites_line}\n"
                f"תשובה: {result.answer_he}\n"
                + (f"עובדות מפתח: {'; '.join(result.key_facts)}\n" if result.key_facts else "")
                + (f"סתירות/פערים: {result.contradictions_he}\n" if result.contradictions_he else "")
            )
        return blocks


#: One entry per topic, in ``TOPICS`` order -- what ``on_progress`` (PD-fix item 5) is called with
#: after every status change, and what ``eoa.dossier.report``/``eoa.api.services`` persist into
#: ``jobs.result->'progress'`` for the pending-run banner to poll.
ProgressEntry = dict[str, Any]
ProgressCallback = Callable[[list[ProgressEntry]], None]


def _new_progress(topics: tuple[Topic, ...]) -> list[ProgressEntry]:
    return [
        {
            "topic": t.key,
            "title_he": t.title_he,
            "status": "pending",
            "seconds": None,
            "sources_found": None,
            #: PD-fix-4 item C: pages actually read for this topic (own read + any read-budget
            #: retry rounds), ``[{"n", "url", "kind"} ...]`` in read order -- what an operator/QA
            #: pass inspects to confirm a topic didn't stop after one shallow read.
            "pages_read": [],
        }
        for t in topics
    ]


#: PD-fix-4 item C: run 4's topics mostly stopped after 1-2 successful reads even when their own
#: search turned up plenty of candidate hits -- the ReAct loop's own round budget (rounds_per_topic)
#: happily "finishes" once it has *an* answer, not once it has read enough independent sources. This
#: is the floor a topic's own reads are topped up to (read-budget, not round-budget) when the
#: initial investigate() call under-delivers but its own search did surface candidates worth reading
#: (``inv.hits_seen`` non-empty -- an investigation whose search genuinely found nothing has no
#: candidates to top up with, and is left alone).
_MIN_SUCCESSFUL_READS_PER_TOPIC = 3
#: 1 initial call + up to this many follow-up "read more" calls, each with its own fresh search --
#: bounded so a topic that just keeps finding low-quality/irrelevant pages doesn't loop forever.
_MAX_TOPIC_READ_ATTEMPTS = 3


def _process_topic_reads(
    inv: Investigation,
    *,
    topic: Topic,
    corpus: CorpusResult,
    seen_by_normalized_url: dict[str, ProgressEntry],
    extra_relevance_terms: list[str] | None = None,
    component_of_package: bool = False,
) -> list[dict[str, Any]]:
    """One investigation's ``read_sources`` -> deduped, relevance-checked, classified registry rows
    (item 2's own logic, factored out so :func:`run_plan`'s read-budget retry loop -- item C -- can
    run it again over a follow-up ``investigate()`` call's own reads without duplicating the body).

    ``extra_relevance_terms``/``component_of_package`` (LESSONS-1 item 2): a programme-deal-topic's
    own reads pass the identified platform names here -- a page about the carrier platform (e.g. a
    Watchkeeper X purchase) is relevant evidence for the PRODUCT even when it never names the
    product itself, and is marked ``component_of_package: True`` on its own registry row so a
    downstream reader can tell "this source is about the deal for a platform this product rides
    on" apart from a source that is about the product directly."""
    summaries_by_url = {s.get("url"): s for s in inv.read_summaries if s.get("url")}
    added: list[dict[str, Any]] = []
    for src in inv.read_sources:
        url = (src.get("url") or "").strip()
        if not url:
            continue
        summary_entry = summaries_by_url.get(url)
        summary_text = _read_summary_text(summary_entry)
        if not _mentions_product(
            summary_text, corpus.product_name, corpus.aliases, extra_terms=extra_relevance_terms
        ):
            log.info("dossier.source_dropped_irrelevant", topic=topic.key, url=url[:300])
            continue
        norm = normalize_url(url)
        existing = seen_by_normalized_url.get(norm)
        if existing is not None:
            if component_of_package:
                existing["component_of_package"] = True
            added.append(existing)
            continue
        title = (src.get("title") or "").strip() or (summary_entry or {}).get("title") or _host(url)
        kind, reliability = classify_web_source(url, corpus.vendor)
        row = {
            "n": corpus.next_n,
            "kind": "web",
            "id": None,
            "title": title,
            "url": url,
            "source_name": title,
            "published_at": None,
            "topic": topic.key,
            "source_kind": kind,
            "reliability": reliability,
            "accessed_at": dt.datetime.now(dt.UTC).isoformat(),
        }
        if component_of_package:
            row["component_of_package"] = True
        corpus.registry.append(row)
        seen_by_normalized_url[norm] = row
        added.append(row)
    return added


def _resolve_competitor_seeds(product_line: str | None) -> list[dict[str, Any]]:
    """LESSONS-1 item 3: ``config/product_lines.yaml``'s ``competitor_products`` for this run's own
    ``product_line`` -- ``[{"name", "vendor"}, ...]``, ``[]`` when ``product_line`` is unset or
    unknown. Reads ``settings().product_lines`` directly (the raw parsed YAML dict every other
    consumer of this file also reads through, ``eoa.product_lines.registry`` included) rather than
    going through that module's own ``ProductLineDef`` (which has no ``competitor_products`` field
    of its own -- adding one would be a change to a file this lane doesn't own)."""
    if not product_line:
        return []
    rows = settings().product_lines.get("product_lines") or []
    for row in rows:
        if isinstance(row, dict) and row.get("id") == product_line:
            seeds = row.get("competitor_products") or []
            return [
                {"name": s.get("name"), "vendor": s.get("vendor")}
                for s in seeds
                if isinstance(s, dict) and s.get("name")
            ]
    return []


_BRACE_RE = re.compile(r"[{}]")


def _escape_braces(text: str) -> str:
    """``Topic.question_he`` always runs ``question_template_he.format(...)`` -- a gap-follow-up
    question's own text (LESSONS-1 item 4) is already the final, fully-resolved question (no
    ``{product_name}``-style placeholders left to fill), so any literal ``{``/``}`` it happens to
    contain (e.g. a quoted spec value) must be doubled first or ``.format()`` would raise/mangle
    it."""
    return _BRACE_RE.sub(lambda m: m.group(0) * 2, text)


def run_plan(
    corpus: CorpusResult,
    *,
    job_id: int | None = None,
    rounds_per_topic: int | None = None,
    budget_multiplier: float | None = None,
    max_topics: int | None = None,
    on_progress: ProgressCallback | None = None,
    llm_leg: str | None = None,
) -> PlanResult:
    """Runs :data:`TOPICS` (capped at ``max_topics``) sequentially through
    ``eoa.search.deep_search.investigate()``, each with the corpus summary as ``context_he`` and
    a hard per-topic wall-clock cap (``dossier.topic_time_cap_s``, item 6 -- forwarded as
    ``investigate()``'s own ``deadline_s``, so a slow topic stops with whatever it already found
    instead of running the full multiplier-scaled timeout).

    Every source a topic's investigation actually read is deduped by normalized URL, relevance-
    checked against its own read summary, classified (:func:`classify_web_source`) and only then
    appended to ``corpus.registry`` (mutated in place, item 2) -- so ``eoa.dossier.extract``'s
    citation registry, and the persisted ``sources`` column, covers both the DB half (corpus) and
    the web half (this stage) in one flat, numbered sequence, exactly like ``eoa.patents.survey``'s
    own patents-then-db-records convention (here: DB-then-web). A page already cited (same
    normalized URL, any topic) is never re-numbered -- the existing row is reused.

    PD-fix-2 (2026-09-08, item 4): the dedup-by-URL seed now covers **every** ``corpus.registry``
    row that carries a ``url`` -- not just an earlier ``"web"``-kind one. A DB item/event/patent/
    tender the corpus already knows about often carries the very same URL a topic's own web search
    then reads (e.g. a press item already in ``items`` that a "deals" investigation also finds via
    search) -- that source is *item-derived*: it already has the item's real ``title``/``url`` from
    ``eoa.dossier.corpus``, strictly better than a fresh web-read stub (whose title falls back to a
    bare hostname when the read itself returns none, see :func:`_host` below). Reusing the existing
    DB-kind row's own ``n`` for that URL, instead of minting a second, weaker "web" entry for the
    exact same page, is what keeps an item-derived source carrying the item's own url/title.

    ``on_progress`` (item 5), when given, is called with the full progress list (one entry per
    planned topic, ``TOPICS`` order) once up front and again after every topic's status changes --
    the caller (``eoa.dossier.report.build_product_dossier``) uses it to persist a live per-topic
    banner into the running job's own row.

    ``llm_leg`` (PD-cloud-tools, 2026-09-09): forwarded verbatim into every topic's own
    ``investigate()`` call -- see that function's own docstring. ``None`` (the default) preserves
    the exact prior dispatch for every existing caller.

    LESSONS-1 (PD-datasheet, 2026-09-09) adds four more stages, all best-effort (a failure in any
    of them is logged and never breaks the build, same discipline as every existing stage here):
    item 1, a datasheet/brochure hunt (``eoa.dossier.datasheet.hunt_datasheets``) folded into
    ``context_he`` before the topic loop, same placement as the must-read block above; item 2, a
    per-identified-platform programme-deal search run right after the new ``"platforms_and_
    programmes"`` topic (one of :data:`TOPICS`) completes, populating ``corpus.programme_deals``
    and extending this run's own search languages (item 5) once a customer country is known; item
    3, the "competitors" topic's own question is pointed at this run's ``product_line``'s named
    ``competitor_products`` (``config/product_lines.yaml``); item 4, extra "gap follow-up" topics
    (``eoa.dossier.gaps``) for every open gap the previous dossier of this product left behind,
    appended to the topic list and run exactly like any other topic, with
    ``corpus.gap_status`` populated once they've all run."""
    cfg = settings().dossier
    rounds = rounds_per_topic if rounds_per_topic is not None else cfg.rounds_per_topic
    mult = budget_multiplier if budget_multiplier is not None else cfg.budget_multiplier
    cap = max_topics if max_topics is not None else cfg.max_topics
    aliases_he = ", ".join(corpus.aliases) or "אין"
    fixed_topics = TOPICS[: max(cap, 0)]

    # LESSONS-1 item 4: gap follow-up topics, deterministic from the previous dossier of this same
    # product_key alone (no network needed to know WHAT to ask, only to answer it) -- appended to
    # the fixed topic list and run through the exact same loop as every other topic below.
    gap_texts = extract_gaps_from_previous(corpus.previous)
    gap_followups = build_gap_followup_topics(gap_texts, product_name=corpus.product_name, vendor=corpus.vendor)
    gap_topics = tuple(
        Topic(key=g.key, title_he=g.title_he, question_template_he=_escape_braces(g.question_he))
        for g in gap_followups
    )
    topics = fixed_topics + gap_topics

    # PD-fix-2 item 4: seed the dedup set from every registry row that already has a url (item/
    # event/patent/tender/web -- not just "web"), so a topic re-reading a URL the corpus already
    # knows as a DB item reuses that item-derived row (its real title/url) instead of minting a
    # second, weaker "web" entry for the same page.
    seen_by_normalized_url: dict[str, ProgressEntry] = {}
    for row in corpus.registry:
        if row.get("url"):
            seen_by_normalized_url.setdefault(normalize_url(row["url"]), row)

    progress = _new_progress(topics)

    def _emit() -> None:
        if on_progress is not None:
            try:
                on_progress([dict(p) for p in progress])
            except Exception as exc:  # a progress-reporting failure must never break the build
                log.warning("dossier_progress_callback_failed", error=str(exc)[:200])

    _emit()

    result = PlanResult()
    context_he = corpus.summary_he()

    # PD-fix-4 item A.1: every MUST-READ vendor page (previous-dossier vendor_official URLs + any
    # vendor-domain URL already in the corpus) is read deterministically, once, before the topic
    # loop starts -- its content is folded into the SAME context_he every topic receives, so
    # specifications/versions/performance (TOPICS' own first three entries) cannot start "cold"
    # of the vendor's own page the way run 4 did.
    must_read_urls = gather_must_read_urls(corpus)
    if must_read_urls:
        must_read_blocks = run_must_read(corpus, must_read_urls)
        # a fetch can fail (network, robots.txt, quarantine) -- only successfully read pages are
        # already registered by run_must_read, so re-seed the dedup map from whatever it added.
        for row in corpus.registry:
            if row.get("url"):
                seen_by_normalized_url.setdefault(normalize_url(row["url"]), row)
        if must_read_blocks:
            context_he = context_he + "\n\nעמודי יצרן שחובה להביא בחשבון (נקראו מראש):\n" + "\n".join(
                must_read_blocks
            )

    # PD-fix-4 item A.2: a spec-ish topic's own question is prefixed with an explicit "search the
    # vendor's own site first" instruction -- best-effort (the ReAct loop's own query planner still
    # decides the actual search string), derived from a real vendor_official URL when one is known,
    # falling back to the small hint map only when none is.
    vendor_domain = resolve_vendor_domain(corpus.vendor, corpus.registry)

    # LESSONS-1 item 1: datasheet/brochure hunt -- fold the found text into the SAME context_he
    # every topic receives, same placement discipline as the must-read block above (before the
    # topic loop starts, so specifications/versions/performance never start "cold" of it).
    try:
        datasheet_results = hunt_datasheets(
            corpus.product_name, corpus.vendor, corpus.aliases, vendor_domain=vendor_domain
        )
    except Exception as exc:
        log.warning("dossier_datasheet_hunt_failed", error=str(exc)[:200])
        datasheet_results = []
    datasheet_blocks: list[str] = []
    for ds in datasheet_results:
        url = ds.get("url") or ""
        if not url:
            continue
        norm = normalize_url(url)
        existing = seen_by_normalized_url.get(norm)
        if existing is not None:
            n = existing["n"]
            existing["source_kind"] = "datasheet"
            existing["reliability"] = "primary"
        else:
            n = corpus.next_n
            title = (ds.get("title") or "").strip() or _host(url)
            row = {
                "n": n,
                "kind": "web",
                "id": None,
                "title": title,
                "url": url,
                "source_name": title,
                "published_at": None,
                "topic": "datasheet_hunt",
                "source_kind": "datasheet",
                "reliability": "primary",
                "accessed_at": dt.datetime.now(dt.UTC).isoformat(),
            }
            corpus.registry.append(row)
            seen_by_normalized_url[norm] = row
        corpus.datasheets.append({**ds, "n": n})
        excerpt = _deterministic_excerpt(ds.get("text") or "")
        if excerpt:
            datasheet_blocks.append(f"[{n}] (עלון/דף נתונים יצרן, נקרא מראש) {ds.get('title') or ''}\n{excerpt}")
    if datasheet_blocks:
        context_he = context_he + "\n\nעלוני/דפי נתונים שנמצאו (נקראו מראש):\n" + "\n".join(datasheet_blocks)
        log.info("dossier.datasheets_found", count=len(datasheet_results))

    # LESSONS-1 item 3: this run's own product_line's named competitor products -- the
    # "competitors" topic's own question is pointed at them explicitly below.
    competitor_seeds = _resolve_competitor_seeds(corpus.product_line)
    corpus.competitor_seeds = competitor_seeds

    # LESSONS-1 item 5: extended once a customer country is identified (from the programme-deal
    # search below) -- every investigate() call from that point on requests these languages too, in
    # addition to deep_search's own configured primary languages (Hebrew always included there).
    langs_extra: list[str] = []

    def _investigate(question_text: str, *, deadline_s: float) -> Investigation:
        langs = None
        if langs_extra:
            base = list(settings().deep_search.langs_primary)
            langs = base + [lg for lg in langs_extra if lg not in base]
        return investigate(
            question_text,
            job_id=job_id,
            context_he=context_he,
            max_rounds=rounds,
            budget_multiplier=mult,
            deadline_s=deadline_s,
            llm_leg=llm_leg,
            langs=langs,
        )

    for idx, topic in enumerate(topics):
        question = topic.question_he(
            product_name=corpus.product_name, vendor=corpus.vendor or "", aliases_he=aliases_he
        )
        if vendor_domain and topic.key in SITE_RESTRICTED_TOPIC_KEYS:
            question = (
                f"חפש תחילה באתר היצרן בלבד (site:{vendor_domain} {corpus.product_name}) לפני כל "
                f"חיפוש כללי אחר. {question}"
            )
        if topic.key == "competitors" and competitor_seeds:
            names = "; ".join(
                f"{c['name']} ({c['vendor']})" if c.get("vendor") else c["name"] for c in competitor_seeds
            )
            question = f"{question} בדוק במפורש מול המתחרים הידועים הבאים בשוק: {names}."
        progress[idx]["status"] = "running"
        _emit()
        started = time.monotonic()
        try:
            inv = _investigate(question, deadline_s=cfg.topic_time_cap_s)
        except Exception as exc:  # a single topic must never take the whole dossier down
            elapsed = round(time.monotonic() - started, 1)
            log.warning("dossier_topic_failed", topic=topic.key, error=str(exc)[:200], seconds=elapsed)
            progress[idx].update(status="failed", seconds=elapsed)
            _emit()
            continue

        source_ns: list[dict[str, Any]] = _process_topic_reads(
            inv, topic=topic, corpus=corpus, seen_by_normalized_url=seen_by_normalized_url
        )
        source_ns_by_n = {r["n"] for r in source_ns}

        # PD-fix-4 item C: read budget, not round budget -- when the topic's own search surfaced
        # candidates (`inv.hits_seen`) but under-delivered actual reads, ask again for more, each
        # follow-up call getting whatever wall-clock remains under the topic's own time cap.
        attempts = 1
        while (
            len(source_ns) < _MIN_SUCCESSFUL_READS_PER_TOPIC
            and inv.hits_seen
            and attempts < _MAX_TOPIC_READ_ATTEMPTS
        ):
            remaining = cfg.topic_time_cap_s - (time.monotonic() - started)
            if remaining <= 5:
                break
            attempts += 1
            followup_question = (
                f"{question}\nהמשך לחפש ולקרוא לפחות {_MIN_SUCCESSFUL_READS_PER_TOPIC} מקורות שונים "
                "ואמינים בנושא זה, כולל מקורות שטרם נקראו."
            )
            try:
                inv = _investigate(followup_question, deadline_s=remaining)
            except Exception as exc:
                log.warning(
                    "dossier_topic_followup_failed", topic=topic.key, error=str(exc)[:200], attempt=attempts
                )
                break
            for row in _process_topic_reads(
                inv, topic=topic, corpus=corpus, seen_by_normalized_url=seen_by_normalized_url
            ):
                if row["n"] not in source_ns_by_n:
                    source_ns_by_n.add(row["n"])
                    source_ns.append(row)

        elapsed = round(time.monotonic() - started, 1)
        finding = TopicFinding(
            key=topic.key,
            title_he=topic.title_he,
            question_he=question,
            investigation=inv,
            source_ns=source_ns,
        )
        result.findings.append(finding)
        pages_read = [{"n": r["n"], "url": r.get("url"), "kind": r.get("source_kind")} for r in source_ns]
        progress[idx].update(
            status="done", seconds=elapsed, sources_found=len(source_ns), pages_read=pages_read
        )
        _emit()
        log.info(
            "dossier.topic_done",
            topic=topic.key,
            seconds=elapsed,
            sources_found=len(source_ns),
            attempts=attempts,
        )

        # LESSONS-1 item 2: right after "platforms_and_programmes" finishes, run one dedicated deal
        # search per identified platform -- treating a page about the carrier platform as evidence
        # about the product too (component_of_package=True, extra_relevance_terms=[platform] so it
        # survives the relevance filter without ever naming the product itself). Also extends this
        # run's own search languages (item 5) for every remaining topic once a customer country is
        # known from these deals.
        if topic.key == "platforms_and_programmes" and inv.result is not None:
            finding_texts = [inv.result.answer_he, *inv.result.key_facts]
            finding_texts += [it.get("title") or "" for it in corpus.items]
            finding_texts += [ev.get("title") or ev.get("program") or "" for ev in corpus.events]
            platforms = identify_platforms(finding_texts, limit=MAX_PROGRAMME_PLATFORMS)
            deal_texts: list[str] = []
            for platform in platforms:
                platform_question = platform_deal_question_he(corpus.product_name, corpus.vendor, platform)
                platform_topic = Topic(
                    key=f"programme_deal::{platform}",
                    title_he=f"עסקת תוכנית: {platform}",
                    question_template_he=_escape_braces(platform_question),
                )
                try:
                    p_inv = _investigate(platform_question, deadline_s=cfg.topic_time_cap_s)
                except Exception as exc:
                    log.warning(
                        "dossier_programme_deal_search_failed", platform=platform, error=str(exc)[:200]
                    )
                    continue
                p_sources = _process_topic_reads(
                    p_inv,
                    topic=platform_topic,
                    corpus=corpus,
                    seen_by_normalized_url=seen_by_normalized_url,
                    extra_relevance_terms=[platform],
                    component_of_package=True,
                )
                p_cites = [r["n"] for r in p_sources]
                if p_inv.result is not None:
                    deals = parse_programme_deals(
                        platform, answer_he=p_inv.result.answer_he, key_facts=p_inv.result.key_facts, cites=p_cites
                    )
                    corpus.programme_deals.extend(deals)
                    deal_texts.append(p_inv.result.answer_he)
                    deal_texts.extend(p_inv.result.key_facts)
                result.findings.append(
                    TopicFinding(
                        key=platform_topic.key,
                        title_he=platform_topic.title_he,
                        question_he=platform_question,
                        investigation=p_inv,
                        source_ns=p_sources,
                    )
                )
                progress.append(
                    {
                        "topic": platform_topic.key,
                        "title_he": platform_topic.title_he,
                        "status": "done",
                        "seconds": None,
                        "sources_found": len(p_sources),
                        "pages_read": [
                            {"n": r["n"], "url": r.get("url"), "kind": r.get("source_kind")} for r in p_sources
                        ],
                    }
                )
                _emit()
                log.info(
                    "dossier.programme_deal_topic_done", platform=platform, sources_found=len(p_sources)
                )
            countries = detect_customer_countries([corpus.product_name, *deal_texts])
            for lang in extra_langs_for_countries(countries):
                if lang not in langs_extra:
                    langs_extra.append(lang)

    # LESSONS-1 item 4: gap status for every gap-follow-up topic that ran this round -- see
    # eoa.dossier.gaps's own module docstring for why this only ever reports closed/open, never new.
    if gap_followups:
        outcomes: dict[str, tuple[str, float, list[int]]] = {}
        for f in result.findings:
            if f.investigation.result is not None:
                outcomes[f.key] = (
                    f.investigation.result.outcome,
                    f.investigation.result.confidence,
                    [s.get("n") for s in f.source_ns],
                )
        corpus.gap_status = gap_status(gap_followups, outcomes)

    return result


__all__ = [
    "SITE_RESTRICTED_TOPIC_KEYS",
    "TOPICS",
    "PlanResult",
    "ProgressCallback",
    "ProgressEntry",
    "Topic",
    "TopicFinding",
    "build_topics",
    "classify_web_source",
    "gather_must_read_urls",
    "normalize_url",
    "resolve_vendor_domain",
    "run_must_read",
    "run_plan",
]
