"""Stage: tender-likelihood forecasting from platform events (section 5.2 / FR-5.2).

Deterministic pipeline: recent (last 90 days) ``events`` of kind ``contract_award`` /
``deployment`` / ``launch`` are matched against ``platform_payloads.yaml`` (substring match on the
event/item text) to recognise a platform category (fighter jet, MALE UAV, OPV/corvette, ...); each
match becomes a forecast candidate with a deterministic ``likelihood`` (rubric below), a
``window_from``/``window_to`` derived from the platform's typical ``lag_months``, and
``candidate_vendors`` straight from the knowledge table. The LLM is used *only* for the Hebrew
``rationale_he`` text (``chat_structured`` with ``TenderForecastOut``) -- every other field is
computed without it and is written/upserted even if the LLM call fails or is deferred
(``ResourceUnavailable``), per the task's "deterministic parts must not depend on the LLM" rule.

Likelihood rubric (0-1, capped):
    0.30 base, once any triggering event is found for a (platform, buyer_country, payload_need)
    +0.10 per additional corroborating event within the 90-day window, capped at +0.30
    +0.20 if an explicit RFI/RFP/"sources sought" mention is found in the triggering item(s)
    +0.15 if a prior related ``tenders`` row (same buyer_country, matching keyword) already exists
    floor 0.05 (still worth surfacing), cap 1.0
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
import yaml
from dateutil.relativedelta import relativedelta

from eoa.config import settings
from eoa.db import connection
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.tenders import TenderForecastOut
from eoa.report.geography import UNKNOWN_COUNTRY, country_mentions_in_text, normalize_country

log = structlog.get_logger(__name__)

PLATFORM_PAYLOADS_YAML = Path(__file__).resolve().parent / "platform_payloads.yaml"

_TRIGGER_EVENT_KINDS = ("contract_award", "deployment", "launch")
_LOOKBACK_DAYS = 90

# R6-forecast (round 6 judge D6, docs/qa/loop/round_5_judge.md finding 1): Hebrew multi-letter
# abbreviations (כטב"ם, רק"ם, מטע"ד, ...) are conventionally punctuated with the Hebrew gershayim
# mark (״, ״) rather than a plain ASCII double-quote, and a single-letter abbreviation with a
# geresh (׳, ׳) rather than an apostrophe. ``platform_payloads.yaml`` has used a plain ASCII
# quote/apostrophe for a while now, but some ``tender_forecasts`` rows were written back when it (or
# an earlier version of it) used the Hebrew marks -- e.g. row `platform='כטב״ם MALE'` (gershayim)
# alongside a later run's `platform='כטב"ם MALE'` (ASCII quote). Both spellings denote the exact
# same platform, but ``tender_forecasts`` has a hard ``UNIQUE (platform, buyer_country,
# payload_need)`` constraint (db/migrations/versions/0004_tenders.py) on the literal text, so the
# punctuation drift alone makes ``ON CONFLICT`` miss and insert a brand-new row instead of updating
# the existing one -- the observed "same platform/payload/reasoning text, window shifted by a day or
# two" near-duplicates (AeroVironment/E-HEL C-UAS, APC/IFV commander/gunner sight, MALE UAV EO/IR
# gimbal -- docs/qa/loop/round_5_judge.md D6/D9) are exactly this: one row from before the yaml's
# punctuation settled on ASCII, one from after.
_HEBREW_GERSHAYIM = "״"  # ״ -- Hebrew punctuation gershayim
_HEBREW_GERESH = "׳"  # ׳ -- Hebrew punctuation geresh


def normalize_hebrew_punctuation(text: str | None) -> str:
    """Canonicalise Hebrew gershayim/geresh onto the plain ASCII quote/apostrophe -- the form
    ``platform_payloads.yaml`` uses today. Used both when loading the yaml (so a future edit back
    to the Hebrew marks is canonicalised before it ever reaches the DB -- see the module-level note
    above) and by :func:`_forecast_stable_key` / :func:`find_duplicate_forecast_groups` to recognise
    already-diverged historical rows as the same forecast. ``None``/empty input returns ``""``."""
    if not text:
        return ""
    return text.replace(_HEBREW_GERSHAYIM, '"').replace(_HEBREW_GERESH, "'")


_RFI_RE = re.compile(
    r"\bRFI\b|\bRFP\b|request for information|request for proposal(?:s)?|sources sought|"
    r"בקשת מידע|קול קורא|מכרז",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# platform_payloads.yaml
# --------------------------------------------------------------------------


class PlatformSpec:
    """One ``platform_payloads.yaml`` entry, loosely typed (kept a plain class -- not persisted,
    not LLM-facing -- to avoid a pydantic dependency here for what is a pure config-read helper)."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.key: str = raw["key"]
        self.match: list[str] = [m.lower() for m in raw.get("match", [])]
        # R6-forecast: normalised at load time (see :func:`normalize_hebrew_punctuation`) so every
        # candidate built from this spec always writes the same canonical text to
        # ``tender_forecasts``, regardless of which punctuation mark the yaml entry happens to use.
        self.category_he: str = normalize_hebrew_punctuation(raw.get("category_he", self.key))
        self.payload_need_he: str = normalize_hebrew_punctuation(raw.get("payload_need_he", ""))
        self.payload_domain: str = raw.get("payload_domain", "")
        self.typical_vendors: list[str] = list(raw.get("typical_vendors", []))
        lag = raw.get("lag_months") or {}
        self.lag_min: int = int(lag.get("min", 6))
        self.lag_max: int = int(lag.get("max", 24))

    def matches(self, text: str) -> bool:
        low = text.lower()
        return any(m in low for m in self.match)


def load_platform_payloads(path: str | Path | None = None) -> list[PlatformSpec]:
    file_path = Path(path) if path is not None else PLATFORM_PAYLOADS_YAML
    with file_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return [PlatformSpec(p) for p in raw.get("platforms", [])]


# --------------------------------------------------------------------------
# candidate matching
# --------------------------------------------------------------------------


@dataclass
class ForecastCandidate:
    platform_key: str
    platform_he: str
    buyer_country: str | None
    payload_need_he: str
    candidate_vendors: list[str]
    trigger_event_ids: list[int]
    trigger_item_ids: list[int]
    trigger_texts: list[str]  # for the RFI regex + LLM rationale prompt
    lag_min: int
    lag_max: int


def _fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _recent_trigger_events(lookback_days: int = _LOOKBACK_DAYS) -> list[dict[str, Any]]:
    """Recent contract_award/deployment/launch events, joined to their triggering item's text and
    geography (used both for platform-name matching and as the buyer_country signal)."""
    return _fetchall(
        """
        SELECT e.id AS event_id, e.kind, e.title, e.date, e.parties, e.customer, e.program,
               e.summary_he, i.id AS item_id, i.title AS item_title, i.clean_text, i.geography
        FROM events e
        JOIN items i ON i.id = e.item_id
        WHERE e.kind = ANY(%(kinds)s)
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              >= (CURRENT_DATE - (%(days)s || ' days')::interval)
        ORDER BY e.date DESC NULLS LAST, e.id DESC
        """,
        {"kinds": list(_TRIGGER_EVENT_KINDS), "days": lookback_days},
    )


def _watchlist_vendor_names() -> set[str]:
    companies = (settings().watchlist or {}).get("companies") or []
    return {c.get("name") for c in companies if c.get("name")}


def _build_candidates(platforms: list[PlatformSpec], events: list[dict[str, Any]]) -> list[ForecastCandidate]:
    """Group matching events by (platform_key, buyer_country) so multiple events about the same
    platform/buyer within the lookback window corroborate one candidate rather than each spawning
    its own (matching the ``unique(platform, buyer_country, payload_need)`` DB constraint).

    Q3-11 (docs/qa/findings_Q3_r1.md): ``buyer_country`` is grouped on the *normalized*
    (``eoa.report.geography.normalize_country``) code rather than the raw ``items.geography``
    string, so two events tagged with different spellings of the same country still corroborate
    one candidate instead of silently splitting into two. A still-unknown (``"other"``) country
    at this stage is refined further in ``forecast_tenders`` (entities.country, then a country
    mention scan over the trigger text/rationale) before the row is persisted.
    """
    watchlist_vendors = _watchlist_vendor_names()
    groups: dict[tuple[str, str], ForecastCandidate] = {}

    for ev in events:
        text = " ".join(
            str(x)
            for x in (ev.get("title"), ev.get("item_title"), ev.get("clean_text"), ev.get("summary_he"))
            if x
        )
        if not text:
            continue
        buyer_country = normalize_country(ev.get("geography"))
        for platform in platforms:
            if not platform.matches(text):
                continue
            key = (platform.key, buyer_country)
            vendors = [
                v for v in platform.typical_vendors if v in watchlist_vendors
            ] or platform.typical_vendors
            cand = groups.get(key)
            if cand is None:
                groups[key] = ForecastCandidate(
                    platform_key=platform.key,
                    platform_he=platform.category_he,
                    buyer_country=buyer_country,
                    payload_need_he=platform.payload_need_he,
                    candidate_vendors=vendors,
                    trigger_event_ids=[ev["event_id"]],
                    trigger_item_ids=[ev["item_id"]],
                    trigger_texts=[text[:2000]],
                    lag_min=platform.lag_min,
                    lag_max=platform.lag_max,
                )
            else:
                cand.trigger_event_ids.append(ev["event_id"])
                cand.trigger_item_ids.append(ev["item_id"])
                cand.trigger_texts.append(text[:2000])

    return list(groups.values())


# --------------------------------------------------------------------------
# likelihood rubric (deterministic, no LLM)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Q3-11: buyer_country refinement + platform-type sanity check
# --------------------------------------------------------------------------


def _country_from_entities(item_ids: list[int]) -> str | None:
    """Q3-11: the country of any watchlist/known entity mentioned by one of ``item_ids``, when
    ``items.geography`` itself came back unknown. Best-effort -- returns ``None`` (never raises)
    on any DB issue or when no mentioned entity carries a known country."""
    if not item_ids:
        return None
    try:
        rows = _fetchall(
            """
            SELECT en.country
            FROM items i
            JOIN LATERAL unnest(COALESCE(i.entities_mentioned, '{}')) AS ent_name ON true
            JOIN entities en ON en.name = ent_name
            WHERE i.id = ANY(%(ids)s) AND en.country IS NOT NULL AND en.country <> %(unknown)s
            LIMIT 1
            """,
            {"ids": item_ids, "unknown": UNKNOWN_COUNTRY},
        )
    except Exception as exc:
        log.debug("forecast_country_from_entities_failed", error=str(exc)[:120])
        return None
    return rows[0]["country"] if rows else None


def _resolve_buyer_country(candidate: ForecastCandidate, rationale_he: str = "") -> str:
    """Q3-11: refine an ``"other"``/unknown ``buyer_country`` before persistence, in priority
    order: (1) the trigger items' ``entities.country``; (2) a country name mentioned in the
    trigger text itself; (3) a country name mentioned in the generated rationale. Returns the
    original value unchanged if it was already a known code, or if none of the three signals
    found anything."""
    if candidate.buyer_country and candidate.buyer_country != UNKNOWN_COUNTRY:
        return candidate.buyer_country
    country = _country_from_entities(candidate.trigger_item_ids)
    if country:
        return country
    mentions = country_mentions_in_text(" ".join(candidate.trigger_texts))
    if mentions:
        return mentions[0]
    if rationale_he:
        mentions = country_mentions_in_text(rationale_he)
        if mentions:
            return mentions[0]
    return candidate.buyer_country or UNKNOWN_COUNTRY


#: Q3-11: a candidate's platform key mapped to a broad airframe/vehicle class -- used only to spot
#: an internally-contradictory match (the trigger text matches two platforms whose classes cannot
#: both be true of the same program), never to re-derive the candidate's own category.
_PLATFORM_CLASS: dict[str, str] = {
    "fighter_jet": "fixed_wing_manned",
    "attack_helicopter": "rotary_wing",
    "male_uav": "fixed_wing_uas",
    "small_uas": "fixed_wing_uas",
    "opv_corvette": "naval",
    "submarine": "naval",
    "apc_ifv": "ground_vehicle",
    "border_project": "fixed_installation",
}
#: Class pairs that cannot both genuinely describe the same triggering program -- e.g. the
#: canonical Q3-11 example, "fixed-wing UAS vs combat helicopter" (rotary_wing vs fixed_wing_uas).
_CONTRADICTING_PLATFORM_CLASSES: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"rotary_wing", "fixed_wing_uas"}),
        frozenset({"rotary_wing", "fixed_wing_manned"}),
        frozenset({"naval", "ground_vehicle"}),
        frozenset({"naval", "fixed_wing_manned"}),
        frozenset({"ground_vehicle", "fixed_wing_manned"}),
    }
)
#: Likelihood penalty applied when the trigger text contradicts the candidate's own platform type.
PLATFORM_SANITY_PENALTY = 0.2


def _platform_type_contradiction(
    candidate: ForecastCandidate, platforms: list[PlatformSpec]
) -> PlatformSpec | None:
    """Q3-11: the first other platform whose keywords also appear in ``candidate``'s trigger text
    AND whose class contradicts ``candidate``'s own class (e.g. attack_helicopter wording
    alongside a male_uav/small_uas match) -- a signal the matched text may actually describe an
    unrelated platform mentioned in the same article, not the one this candidate is about.
    Returns ``None`` when there is no such contradiction (the overwhelming majority of candidates,
    including anything for a platform key not in :data:`_PLATFORM_CLASS`)."""
    own_class = _PLATFORM_CLASS.get(candidate.platform_key)
    if own_class is None:
        return None
    combined = " ".join(candidate.trigger_texts)
    for spec in platforms:
        if spec.key == candidate.platform_key:
            continue
        other_class = _PLATFORM_CLASS.get(spec.key)
        if other_class is None or other_class == own_class:
            continue
        if frozenset({own_class, other_class}) in _CONTRADICTING_PLATFORM_CLASSES and spec.matches(combined):
            return spec
    return None


def _has_prior_history(buyer_country: str | None, payload_need_he: str) -> bool:
    if not buyer_country:
        return False
    row = _fetchall(
        "SELECT 1 FROM tenders WHERE country = %(country)s "
        "AND (title ILIKE %(kw)s OR summary_he ILIKE %(kw)s) LIMIT 1",
        {"country": buyer_country, "kw": f"%{payload_need_he.split('(')[0].strip()[:20]}%"},
    )
    return bool(row)


def compute_likelihood(candidate: ForecastCandidate) -> float:
    score = 0.30
    score += min(0.10 * (len(candidate.trigger_event_ids) - 1), 0.30)
    if any(_RFI_RE.search(t) for t in candidate.trigger_texts):
        score += 0.20
    if _has_prior_history(candidate.buyer_country, candidate.payload_need_he):
        score += 0.15
    return round(max(0.05, min(1.0, score)), 3)


def _window(candidate: ForecastCandidate, today: dt.date) -> tuple[dt.date, dt.date]:
    return today + relativedelta(months=candidate.lag_min), today + relativedelta(months=candidate.lag_max)


# --------------------------------------------------------------------------
# LLM rationale (the only non-deterministic part)
# --------------------------------------------------------------------------

# F1 (2026-09-05 root cause): the "classify" task's num_ctx is 4096 -- fine for the short
# ClassifyOut/TriageOut-style schemas that share the task tag, but a candidate could carry 9-18
# trigger items at up to 2000 chars each, blowing well past 4096 tokens once the system prompt and
# instructions are added. Ollama silently truncates the *start* of an over-length prompt (verified
# live in the Ollama logs -- 12x "truncating input prompt" during the 01:06-01:13 night run), which
# is exactly where the task instructions live -- the model then "thinks out loud" about whatever
# fragment of the prompt survived instead of writing a rationale, and that raw reasoning ends up in
# rationale_he. Fix: cap the data block hard (below), and move to the "summarize" task (8192 ctx)
# with an explicit token-budget check as a second line of defense.
_MAX_TRIGGER_ITEMS_FOR_RATIONALE = 5  # most-recent-first (candidate.trigger_item_ids is already
# ordered this way: _recent_trigger_events sorts `ORDER BY e.date DESC`, and _build_candidates
# appends in the order events are encountered).
_MAX_CHARS_PER_TRIGGER_ITEM = 700
_MAX_DATA_BLOCK_CHARS = 3000

_NUM_CTX_SUMMARIZE = 8192  # config.yaml ollama.num_ctx.summarize -- kept here only for the safety
# check below; the real value always comes from the live config via chat()/_num_ctx, this is not
# read back from settings() to avoid this module depending on ollama_client internals.
_CHARS_PER_TOKEN_ESTIMATE = 2.5  # rough chars-per-token ratio for Hebrew-heavy text
_CTX_SAFETY_FRACTION = 0.70


def _rationale_data_block(candidate: ForecastCandidate, items: dict[int, dict[str, Any]]) -> str:
    """Build the DATA block for the rationale prompt, capped on three axes (F1): at most
    ``_MAX_TRIGGER_ITEMS_FOR_RATIONALE`` items (the most recent), each trimmed to
    ``_MAX_CHARS_PER_TRIGGER_ITEM`` chars, and the whole block hard-capped at
    ``_MAX_DATA_BLOCK_CHARS``. Prefers the item's own ``summary_he`` (already a short LLM/keyword
    summary) over the raw ``clean_text`` when both are available -- shorter and just as
    informative for the rationale's purposes."""
    blocks: list[str] = []
    total = 0
    for item_id in candidate.trigger_item_ids[:_MAX_TRIGGER_ITEMS_FOR_RATIONALE]:
        row = items.get(item_id)
        if row is None:
            continue
        text = (row.get("summary_he") or row.get("clean_text") or row.get("title") or "")[
            :_MAX_CHARS_PER_TRIGGER_ITEM
        ]
        block = f"[item {item_id}] {row.get('title') or ''}\n{wrap_data(text, item_id, row.get('url') or '')}"
        if blocks and total + len(block) > _MAX_DATA_BLOCK_CHARS:
            break
        blocks.append(block)
        total += len(block)
    if not blocks:
        return "(אין פריטי מקור זמינים)"
    joined = "\n\n".join(blocks)
    return joined[:_MAX_DATA_BLOCK_CHARS] if len(joined) > _MAX_DATA_BLOCK_CHARS else joined


def _estimate_tokens(text: str) -> int:
    """Rough chars/2.5 token estimate (Hebrew-heavy text runs fewer chars/token than English)."""
    return int(len(text) / _CHARS_PER_TOKEN_ESTIMATE)


def _llm_rationale(
    candidate: ForecastCandidate,
    likelihood: float,
    window: tuple[dt.date, dt.date],
    data_block: str,
    *,
    role: str,
) -> str:
    prompt = render(
        "tender_forecast",
        platform=candidate.platform_he,
        buyer_country=candidate.buyer_country or "לא ידוע",
        payload_need_he=candidate.payload_need_he,
        window_from=window[0].isoformat(),
        window_to=window[1].isoformat(),
        likelihood=f"{likelihood:.0%}",
        data=data_block,
    )
    # Second line of defense (F1.b): even with the caps above, estimate the rendered prompt's token
    # count and trim the data block further if it would eat past a safety fraction of the task's
    # num_ctx -- a live token count isn't available before the call, so this is deliberately
    # conservative (over-estimating trimming is harmless; under-estimating reproduces the bug).
    budget_tokens = int(_CTX_SAFETY_FRACTION * _NUM_CTX_SUMMARIZE)
    if _estimate_tokens(prompt) > budget_tokens and data_block:
        non_data_chars = len(prompt) - len(data_block)
        allowed_data_chars = max(200, int(budget_tokens * _CHARS_PER_TOKEN_ESTIMATE) - non_data_chars)
        prompt = prompt.replace(data_block, data_block[:allowed_data_chars])
    out = chat_structured(
        role,
        TenderForecastOut,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        # "summarize" carries an 8192 num_ctx (vs. "classify"'s 4096) -- the actual F1 fix; the
        # num_predict override below still applies since this schema's short Hebrew rationale plus
        # JSON overhead exceeds "summarize"'s own default num_predict cap mid-string otherwise.
        task="summarize",
        options={"num_predict": 1400},
    )
    return out.rationale_he


# F1.c: output guard against the reasoning-leak failure mode -- the model narrating its own
# instructions/task ("The prompt asks...", "השאלה מבקשת...") instead of writing a rationale. Every
# valid rationale must cite at least one trigger item; anything matching a known leak phrase, or
# implausibly long for a "2-4 sentence" rationale, is rejected.
_CITATION_RE = re.compile(r"\[item\s+\d+\]")
_REASONING_LEAK_PATTERNS = [
    r"\bthe prompt\b",
    r"\bthe user\b",
    r"\bi need to\b",
    r"\blet me\b",
    r"summary of the news article",
    r"השאלה מבקשת",
    r"המשימה דורשת",
    r"ניתוח הנתונים",
    r"מסקנה\s*:",
    r"עלי לזהות",
]
_REASONING_LEAK_RE = re.compile("|".join(_REASONING_LEAK_PATTERNS), re.IGNORECASE)
_MAX_RATIONALE_CHARS = 900


def _rationale_guard_failure(text: str) -> str | None:
    """Returns a short reason string if ``text`` fails the output guard, else ``None``."""
    if not text or not _CITATION_RE.search(text):
        return "no_citation"
    if _REASONING_LEAK_RE.search(text):
        return "reasoning_leak"
    if len(text) > _MAX_RATIONALE_CHARS:
        return "too_long"
    return None


def _llm_rationale_guarded(
    candidate: ForecastCandidate,
    likelihood: float,
    window: tuple[dt.date, dt.date],
    data_block: str,
    *,
    role: str,
) -> str:
    """``_llm_rationale`` plus the F1.c output guard: one retry with a shorter data block on
    failure, then the deterministic fallback. ``ResourceUnavailable``/``LLMOutputError`` from the
    underlying call are never caught here -- they propagate to ``forecast_tenders``'s own
    try/except so the existing llm_deferred/llm_failed stats bookkeeping still applies; only a
    *successful call with a bad output* is this function's concern."""
    rationale = _llm_rationale(candidate, likelihood, window, data_block, role=role)
    reason = _rationale_guard_failure(rationale)
    if reason is None:
        return rationale
    log.warning("forecast_rationale_rejected", platform=candidate.platform_key, reason=reason, attempt=1)

    shorter_block = data_block[: _MAX_DATA_BLOCK_CHARS // 2]
    rationale = _llm_rationale(candidate, likelihood, window, shorter_block, role=role)
    reason = _rationale_guard_failure(rationale)
    if reason is None:
        return rationale
    log.warning("forecast_rationale_rejected", platform=candidate.platform_key, reason=reason, attempt=2)
    return _fallback_rationale(candidate)


def _fallback_rationale(candidate: ForecastCandidate) -> str:
    """Deterministic rationale used when the LLM call is unavailable/fails -- still cites the
    trigger items by id (per FR-5.3's citation rule), just without prose synthesis."""
    refs = " ".join(
        f"[item {iid}]" for iid in dict.fromkeys(candidate.trigger_item_ids)
    )  # unique, order kept
    return (
        f"זוהו {len(candidate.trigger_event_ids)} אירוע(ים) הקשורים לפלטפורמה '{candidate.platform_he}' "
        f"ב-90 הימים האחרונים {refs}; פלטפורמה זו נזקקת בדרך כלל ל-{candidate.payload_need_he}. "
        "נימוק זה נוצר באופן דטרמיניסטי (המודל השפתי לא היה זמין)."
    )


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def _upsert_forecast(
    candidate: ForecastCandidate,
    likelihood: float,
    window: tuple[dt.date, dt.date],
    rationale_he: str,
    *,
    needs_regen: bool = False,
) -> int:
    """Q3-11: ``needs_regen`` is true whenever ``rationale_he`` came from the deterministic
    fallback (LLM unavailable/failed/rejected by the output guard) rather than a real LLM call --
    the nightly run's ``_regenerate_flagged_forecasts`` retries these once the LLM is available
    again, instead of leaving a generic fallback rationale in place forever."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_forecasts (
                platform, buyer_country, trigger_event_id, trigger_item_id, payload_need,
                candidate_vendors, likelihood, window_from, window_to, rationale_he, sources,
                needs_regen
            )
            VALUES (
                %(platform)s, %(buyer_country)s, %(trigger_event_id)s, %(trigger_item_id)s,
                %(payload_need)s, %(vendors)s, %(likelihood)s, %(window_from)s, %(window_to)s,
                %(rationale_he)s, %(sources)s, %(needs_regen)s
            )
            ON CONFLICT (platform, buyer_country, payload_need) DO UPDATE SET
                trigger_event_id = EXCLUDED.trigger_event_id,
                trigger_item_id = EXCLUDED.trigger_item_id,
                candidate_vendors = EXCLUDED.candidate_vendors,
                likelihood = EXCLUDED.likelihood,
                window_from = EXCLUDED.window_from,
                window_to = EXCLUDED.window_to,
                rationale_he = EXCLUDED.rationale_he,
                sources = EXCLUDED.sources,
                needs_regen = EXCLUDED.needs_regen
            RETURNING id
            """,
            {
                "platform": candidate.platform_he,
                "buyer_country": candidate.buyer_country,
                "trigger_event_id": candidate.trigger_event_ids[0],
                "trigger_item_id": candidate.trigger_item_ids[0],
                "payload_need": candidate.payload_need_he,
                "vendors": candidate.candidate_vendors or None,
                "likelihood": likelihood,
                "window_from": window[0],
                "window_to": window[1],
                "rationale_he": rationale_he,
                # Q3-11b (docs/qa/findings_Q3_r2.md): `candidate.trigger_item_ids` can repeat the
                # same item id (multiple triggering events on one item) -- dedupe (order-preserving)
                # so `sources` never stores the same "item:N" entry more than once.
                "sources": [f"item:{iid}" for iid in dict.fromkeys(candidate.trigger_item_ids)] or None,
                "needs_regen": needs_regen,
            },
        )
        return cur.fetchone()["id"]


def _item_ids_from_sources(sources: list[str] | None) -> list[int]:
    """Parse ``tender_forecasts.sources`` (``["item:123", ...]``, see ``_upsert_forecast``) back
    into item ids -- used by ``_regenerate_flagged_forecasts`` to re-fetch a flagged row's own
    trigger items. Any non-conforming entry is silently skipped."""
    ids: list[int] = []
    for s in sources or []:
        if not isinstance(s, str) or not s.startswith("item:"):
            continue
        try:
            ids.append(int(s.split(":", 1)[1]))
        except ValueError:
            continue
    return ids


def _regenerate_flagged_forecasts(role: str) -> int:
    """Q3-11: retry the LLM rationale for every ``tender_forecasts`` row still flagged
    ``needs_regen`` (set when its rationale came from the deterministic fallback) -- run once at
    the start of every ``forecast_tenders`` call, so a row produced while the LLM was unavailable
    gets a real rationale as soon as it is again, instead of keeping the generic fallback text
    forever. A row that still fails (LLM still unavailable, or the output guard still rejects it
    twice) is left exactly as it was, flag included, to retry again next run. Returns the count of
    rows actually regenerated.
    """
    try:
        rows = _fetchall(
            "SELECT id, platform, buyer_country, trigger_event_id, trigger_item_id, payload_need, "
            "candidate_vendors, likelihood, window_from, window_to, sources FROM tender_forecasts "
            "WHERE needs_regen = true"
        )
    except Exception as exc:
        log.warning("forecast_regen_fetch_failed", error=str(exc)[:160])
        return 0
    if not rows:
        return 0

    all_item_ids = sorted({iid for row in rows for iid in _item_ids_from_sources(row.get("sources"))})
    item_rows = _fetchall(
        "SELECT id, title, url, clean_text, summary_he FROM items WHERE id = ANY(%(ids)s)",
        {"ids": all_item_ids},
    )
    items_by_id = {r["id"]: r for r in item_rows}

    regenerated = 0
    for row in rows:
        trigger_item_ids = _item_ids_from_sources(row.get("sources"))
        if not trigger_item_ids:
            continue
        cand = ForecastCandidate(
            platform_key="",  # unknown here -- only used for the Q3-11 sanity check, skipped below
            platform_he=row["platform"],
            buyer_country=row["buyer_country"],
            payload_need_he=row["payload_need"],
            candidate_vendors=list(row.get("candidate_vendors") or []),
            trigger_event_ids=[row["trigger_event_id"]] if row.get("trigger_event_id") else [],
            trigger_item_ids=trigger_item_ids,
            trigger_texts=[],
            lag_min=0,
            lag_max=0,
        )
        window = (row["window_from"], row["window_to"])
        data_block = _rationale_data_block(cand, items_by_id)
        try:
            rationale_he = _llm_rationale(cand, row["likelihood"], window, data_block, role=role)
        except (ResourceUnavailable, LLMOutputError) as exc:
            log.debug("forecast_regen_still_unavailable", forecast_id=row["id"], error=str(exc)[:160])
            continue
        except Exception as exc:
            log.warning("forecast_regen_unexpected_error", forecast_id=row["id"], error=str(exc)[:160])
            continue
        if _rationale_guard_failure(rationale_he) is not None:
            continue  # still not a real rationale -- leave flagged, try again next run
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE tender_forecasts SET rationale_he=%s, needs_regen=false, updated_at=now() WHERE id=%s",
                (rationale_he, row["id"]),
            )
        regenerated += 1
    return regenerated


# --------------------------------------------------------------------------
# R6-forecast (round 6 judge D6, finding 1): existing-row near-duplicate detection/repair
# --------------------------------------------------------------------------


def _forecast_stable_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """The (platform, buyer_country, payload_need) identity a forecast should live at in
    ``tender_forecasts`` -- normalised (see :func:`normalize_hebrew_punctuation`) so historical
    Hebrew-punctuation drift, and incidental case/whitespace differences, don't split what is
    really one forecast into several rows. Deliberately excludes ``window_from``/``window_to``/
    ``likelihood``/``rationale_he``/``sources``/``updated_at`` -- ignoring dates is the point: those
    are exactly the fields a re-run is expected to refresh in place via ``ON CONFLICT``, not grow a
    new row for."""
    return (
        normalize_hebrew_punctuation(row.get("platform")).strip().casefold(),
        (row.get("buyer_country") or "").strip(),
        normalize_hebrew_punctuation(row.get("payload_need")).strip().casefold(),
    )


@dataclass
class ForecastDuplicateGroup:
    """One group of ``tender_forecasts`` rows sharing a :func:`_forecast_stable_key` -- i.e. a
    near-duplicate cluster :func:`dedupe_existing_forecasts` would collapse to a single row."""

    key: tuple[str, str, str]
    ids: list[int]
    #: the row to keep -- the most recently updated member of the group (ties broken by highest id,
    #: i.e. the most recently inserted), on the theory that it carries the freshest
    #: window/likelihood/rationale.
    kept_id: int
    dropped_ids: list[int]


def find_duplicate_forecast_groups(rows: list[dict[str, Any]]) -> list[ForecastDuplicateGroup]:
    """Group already-fetched ``tender_forecasts`` rows by :func:`_forecast_stable_key`, returning
    only groups with more than one member -- the near-duplicate clusters a repair pass should
    collapse. Pure function over plain dicts (no DB access), so both the dry-run report and this
    module's own unit tests can exercise it directly without touching the database. Each input row
    is expected to carry at least ``id``, ``platform``, ``buyer_country``, ``payload_need``, and
    (for ordering) ``updated_at``/``created_at``."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_forecast_stable_key(row), []).append(row)

    def _recency(row: dict[str, Any]) -> tuple[Any, Any]:
        return (row.get("updated_at") or row.get("created_at"), row.get("id") or 0)

    out: list[ForecastDuplicateGroup] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_recency, reverse=True)
        out.append(
            ForecastDuplicateGroup(
                key=key,
                ids=[m["id"] for m in members],
                kept_id=ordered[0]["id"],
                dropped_ids=[m["id"] for m in ordered[1:]],
            )
        )
    return out


def dedupe_existing_forecasts(conn: Any, *, apply: bool = False) -> list[ForecastDuplicateGroup]:
    """R6-forecast: read every ``tender_forecasts`` row, group by :func:`_forecast_stable_key`, and
    return the near-duplicate groups found (see :func:`find_duplicate_forecast_groups`).

    ``apply=False`` (the default): read-only, a dry-run listing -- never writes to the DB. This is
    the mode used for the report's duplicate-group listing; DB writes for existing rows are owned by
    the data-repair engineer, not this stage.

    ``apply=True``: for each group, merges every dropped row's ``sources`` into the kept row's own
    (order-preserving, deduplicated), stamps its ``updated_at``, and deletes the dropped rows. The
    kept row's own ``window_from``/``window_to``/``likelihood``/``rationale_he`` are left untouched
    -- it is already the most recently updated member of the group, i.e. the most current data.
    Does not commit -- like every other ``conn``-taking helper in this codebase, the caller's own
    ``with connection() as conn:`` block (or an explicit ``conn.commit()``) controls the transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, platform, buyer_country, payload_need, likelihood, window_from, window_to, "
            "rationale_he, sources, created_at, updated_at FROM tender_forecasts"
        )
        rows = cur.fetchall()
    groups = find_duplicate_forecast_groups(rows)
    if not apply or not groups:
        return groups

    rows_by_id = {row["id"]: row for row in rows}
    with conn.cursor() as cur:
        for group in groups:
            merged_sources = list(rows_by_id[group.kept_id].get("sources") or [])
            seen = set(merged_sources)
            for dropped_id in group.dropped_ids:
                for source in rows_by_id[dropped_id].get("sources") or []:
                    if source not in seen:
                        seen.add(source)
                        merged_sources.append(source)
            cur.execute(
                "UPDATE tender_forecasts SET sources=%s, updated_at=now() WHERE id=%s",
                (merged_sources or None, group.kept_id),
            )
            cur.execute("DELETE FROM tender_forecasts WHERE id = ANY(%s)", (group.dropped_ids,))
    return groups


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


@dataclass
class ForecastStats:
    candidates: int = 0
    upserted: int = 0
    llm_used: int = 0
    llm_deferred: int = 0
    llm_failed: int = 0
    #: Q3-11: rows whose rationale came from the deterministic fallback this run (needs_regen=true).
    needs_regen: int = 0
    #: Q3-11: previously-flagged rows successfully regenerated at the start of this run.
    regenerated: int = 0
    #: Q3-11: candidates whose trigger text contradicted their own platform type (penalized).
    platform_sanity_flags: int = 0


def forecast_tenders(*, role: str = "resident", lookback_days: int = _LOOKBACK_DAYS) -> ForecastStats:
    """section-5.2 entry point: recent platform events -> deterministic candidates+likelihood ->
    upsert ``tender_forecasts`` (Hebrew rationale via the LLM, best-effort).

    Q3-11 (docs/qa/findings_Q3_r1.md): before building this run's candidates, first retries the
    LLM rationale for any existing row still flagged ``needs_regen`` (see
    ``_regenerate_flagged_forecasts``) -- a fallback-authored rationale from a past run where the
    LLM was unavailable gets replaced with a real one as soon as it's available again, rather than
    staying generic forever.
    """
    stats = ForecastStats()
    stats.regenerated = _regenerate_flagged_forecasts(role)

    platforms = load_platform_payloads()
    events = _recent_trigger_events(lookback_days)
    candidates = _build_candidates(platforms, events)
    stats.candidates = len(candidates)
    if not candidates:
        log.info("tender_forecast_done", **vars(stats))
        return stats

    all_item_ids = sorted({iid for c in candidates for iid in c.trigger_item_ids})
    item_rows = _fetchall(
        "SELECT id, title, url, clean_text, summary_he FROM items WHERE id = ANY(%(ids)s)",
        {"ids": all_item_ids},
    )
    items_by_id = {r["id"]: r for r in item_rows}

    today = dt.date.today()
    for cand in candidates:
        likelihood = compute_likelihood(cand)

        # Q3-11: platform-type sanity check -- the trigger text contradicting the candidate's own
        # platform category (e.g. fixed-wing UAS wording alongside combat-helicopter wording)
        # lowers confidence and gets an explicit note, rather than silently forecasting as if the
        # match were clean.
        contradiction = _platform_type_contradiction(cand, platforms)
        sanity_note_he = ""
        if contradiction is not None:
            likelihood = round(max(0.0, likelihood - PLATFORM_SANITY_PENALTY), 3)
            sanity_note_he = (
                f" אזהרת עקביות: הטקסט המקור מזכיר גם מאפיינים של '{contradiction.category_he}', "
                "ייתכן שהזיהוי אינו חד-משמעי."
            )
            stats.platform_sanity_flags += 1
            log.info(
                "tender_forecast_platform_sanity_flag",
                platform=cand.platform_key,
                contradicts=contradiction.key,
            )

        window = _window(cand, today)
        data_block = _rationale_data_block(cand, items_by_id)
        used_fallback = False
        try:
            rationale_he = _llm_rationale_guarded(cand, likelihood, window, data_block, role=role)
            stats.llm_used += 1
        except ResourceUnavailable:
            rationale_he = _fallback_rationale(cand)
            stats.llm_deferred += 1
            used_fallback = True
        except LLMOutputError as exc:
            log.warning("tender_forecast_llm_failed", platform=cand.platform_key, error=str(exc)[:200])
            rationale_he = _fallback_rationale(cand)
            stats.llm_failed += 1
            used_fallback = True
        except Exception as exc:
            # Never let one candidate's LLM call (schema retry, logging, transport, ...) take down
            # the whole forecast run -- docs/CONVENTIONS.md rule 9 ("a failing item never stops the
            # stage"). The deterministic likelihood/window/vendors are already computed either way.
            log.warning(
                "tender_forecast_llm_unexpected_error", platform=cand.platform_key, error=str(exc)[:200]
            )
            rationale_he = _fallback_rationale(cand)
            stats.llm_failed += 1
            used_fallback = True

        if sanity_note_he:
            rationale_he = f"{rationale_he}{sanity_note_he}"

        # Q3-11: resolve buyer_country as late as possible -- after the rationale text exists, so
        # a country name mentioned only in the (LLM-written) rationale can still be picked up as a
        # last-resort signal when geography/entities gave nothing.
        cand.buyer_country = _resolve_buyer_country(cand, rationale_he)

        if used_fallback:
            stats.needs_regen += 1
        _upsert_forecast(cand, likelihood, window, rationale_he, needs_regen=used_fallback)
        stats.upserted += 1

    log.info("tender_forecast_done", **vars(stats))
    return stats
