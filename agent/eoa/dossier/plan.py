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

from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings
from eoa.dossier.corpus import CorpusResult
from eoa.search.deep_search import Investigation, investigate

log = structlog.get_logger(__name__)


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


def run_plan(
    corpus: CorpusResult,
    *,
    job_id: int | None = None,
    rounds_per_topic: int | None = None,
    budget_multiplier: float | None = None,
    max_topics: int | None = None,
) -> PlanResult:
    """Runs :data:`TOPICS` (capped at ``max_topics``) sequentially through
    ``eoa.search.deep_search.investigate()``, each with the corpus summary as ``context_he``.
    Every source a topic's investigation actually read is appended to ``corpus.registry`` (mutated
    in place) so ``eoa.dossier.extract``'s citation registry -- and the persisted ``sources``
    column -- covers both the DB half (corpus) and the web half (this stage) in one flat, numbered
    sequence, exactly like ``eoa.patents.survey``'s own patents-then-db-records convention (here:
    DB-then-web)."""
    cfg = settings().dossier
    rounds = rounds_per_topic if rounds_per_topic is not None else cfg.rounds_per_topic
    mult = budget_multiplier if budget_multiplier is not None else cfg.budget_multiplier
    cap = max_topics if max_topics is not None else cfg.max_topics
    aliases_he = ", ".join(corpus.aliases) or "אין"

    result = PlanResult()
    context_he = corpus.summary_he()
    for topic in TOPICS[: max(cap, 0)]:
        question = topic.question_he(
            product_name=corpus.product_name, vendor=corpus.vendor or "", aliases_he=aliases_he
        )
        try:
            inv = investigate(
                question,
                job_id=job_id,
                context_he=context_he,
                max_rounds=rounds,
                budget_multiplier=mult,
            )
        except Exception as exc:  # a single topic must never take the whole dossier down
            log.warning("dossier_topic_failed", topic=topic.key, error=str(exc)[:200])
            continue
        source_ns: list[dict[str, Any]] = []
        for src in inv.read_sources:
            row = {
                "n": corpus.next_n,
                "kind": "web",
                "id": None,
                "title": src.get("title"),
                "url": src.get("url"),
                "source_name": src.get("url"),
                "published_at": None,
                "topic": topic.key,
            }
            corpus.registry.append(row)
            source_ns.append(row)
        finding = TopicFinding(
            key=topic.key,
            title_he=topic.title_he,
            question_he=question,
            investigation=inv,
            source_ns=source_ns,
        )
        result.findings.append(finding)
    return result


__all__ = ["TOPICS", "PlanResult", "Topic", "TopicFinding", "build_topics", "run_plan"]
