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


def _read_summary_text(entry: dict[str, Any] | None) -> str:
    if not entry:
        return ""
    return f"{entry.get('title') or ''}\n{entry.get('summary') or ''}"


def _mentions_product(text: str, product_name: str, aliases: list[str]) -> bool:
    """A page's own read summary must actually mention the product (name or an alias, plain
    case-insensitive substring -- this is a page-relevance check, not the stricter whole-word
    corpus-item gate in ``eoa.dossier.corpus``) or it is dropped outright rather than being handed
    to the extraction model as if it were on-topic (the live French WeTransfer forum thread that
    never mentioned SPECTRO XR at all)."""
    hay = (text or "").casefold()
    if not hay.strip():
        # No summary at all to judge by -- err on the side of keeping it (a read page whose
        # summariser produced nothing is a summariser gap, not evidence of irrelevance).
        return True
    if product_name and product_name.casefold() in hay:
        return True
    return any(a and a.casefold() in hay for a in aliases)


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
        {"topic": t.key, "title_he": t.title_he, "status": "pending", "seconds": None, "sources_found": None}
        for t in topics
    ]


def run_plan(
    corpus: CorpusResult,
    *,
    job_id: int | None = None,
    rounds_per_topic: int | None = None,
    budget_multiplier: float | None = None,
    max_topics: int | None = None,
    on_progress: ProgressCallback | None = None,
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

    ``on_progress`` (item 5), when given, is called with the full progress list (one entry per
    planned topic, ``TOPICS`` order) once up front and again after every topic's status changes --
    the caller (``eoa.dossier.report.build_product_dossier``) uses it to persist a live per-topic
    banner into the running job's own row."""
    cfg = settings().dossier
    rounds = rounds_per_topic if rounds_per_topic is not None else cfg.rounds_per_topic
    mult = budget_multiplier if budget_multiplier is not None else cfg.budget_multiplier
    cap = max_topics if max_topics is not None else cfg.max_topics
    aliases_he = ", ".join(corpus.aliases) or "אין"
    topics = TOPICS[: max(cap, 0)]

    seen_by_normalized_url: dict[str, ProgressEntry] = {}
    for row in corpus.registry:
        if row.get("kind") == "web" and row.get("url"):
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
    for idx, topic in enumerate(topics):
        question = topic.question_he(
            product_name=corpus.product_name, vendor=corpus.vendor or "", aliases_he=aliases_he
        )
        progress[idx]["status"] = "running"
        _emit()
        started = time.monotonic()
        try:
            inv = investigate(
                question,
                job_id=job_id,
                context_he=context_he,
                max_rounds=rounds,
                budget_multiplier=mult,
                deadline_s=cfg.topic_time_cap_s,
            )
        except Exception as exc:  # a single topic must never take the whole dossier down
            elapsed = round(time.monotonic() - started, 1)
            log.warning("dossier_topic_failed", topic=topic.key, error=str(exc)[:200], seconds=elapsed)
            progress[idx].update(status="failed", seconds=elapsed)
            _emit()
            continue

        summaries_by_url = {s.get("url"): s for s in inv.read_summaries if s.get("url")}
        source_ns: list[dict[str, Any]] = []
        for src in inv.read_sources:
            url = (src.get("url") or "").strip()
            if not url:
                continue
            summary_entry = summaries_by_url.get(url)
            summary_text = _read_summary_text(summary_entry)
            if not _mentions_product(summary_text, corpus.product_name, corpus.aliases):
                log.info("dossier.source_dropped_irrelevant", topic=topic.key, url=url[:300])
                continue
            norm = normalize_url(url)
            existing = seen_by_normalized_url.get(norm)
            if existing is not None:
                source_ns.append(existing)
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
            corpus.registry.append(row)
            seen_by_normalized_url[norm] = row
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
        progress[idx].update(status="done", seconds=elapsed, sources_found=len(source_ns))
        _emit()
        log.info("dossier.topic_done", topic=topic.key, seconds=elapsed, sources_found=len(source_ns))
    return result


__all__ = [
    "TOPICS",
    "PlanResult",
    "ProgressCallback",
    "ProgressEntry",
    "Topic",
    "TopicFinding",
    "build_topics",
    "classify_web_source",
    "normalize_url",
    "run_plan",
]
