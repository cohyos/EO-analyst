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

log = structlog.get_logger(__name__)

PLATFORM_PAYLOADS_YAML = Path(__file__).resolve().parent / "platform_payloads.yaml"

_TRIGGER_EVENT_KINDS = ("contract_award", "deployment", "launch")
_LOOKBACK_DAYS = 90

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
        self.category_he: str = raw.get("category_he", self.key)
        self.payload_need_he: str = raw.get("payload_need_he", "")
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
    its own (matching the ``unique(platform, buyer_country, payload_need)`` DB constraint)."""
    watchlist_vendors = _watchlist_vendor_names()
    groups: dict[tuple[str, str | None], ForecastCandidate] = {}

    for ev in events:
        text = " ".join(
            str(x)
            for x in (ev.get("title"), ev.get("item_title"), ev.get("clean_text"), ev.get("summary_he"))
            if x
        )
        if not text:
            continue
        buyer_country = ev.get("geography")
        for platform in platforms:
            if not platform.matches(text):
                continue
            key = (platform.key, buyer_country)
            vendors = [v for v in platform.typical_vendors if v in watchlist_vendors] or platform.typical_vendors
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


def _rationale_data_block(candidate: ForecastCandidate, items: dict[int, dict[str, Any]]) -> str:
    blocks = []
    for item_id in candidate.trigger_item_ids:
        row = items.get(item_id)
        if row is None:
            continue
        text = (row.get("clean_text") or row.get("title") or "")[:2000]
        blocks.append(f"[item {item_id}] {row.get('title') or ''}\n{wrap_data(text, item_id, row.get('url') or '')}")
    return "\n\n".join(blocks) if blocks else "(אין פריטי מקור זמינים)"


def _llm_rationale(
    candidate: ForecastCandidate, likelihood: float, window: tuple[dt.date, dt.date], data_block: str, *, role: str
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
    out = chat_structured(
        role,
        TenderForecastOut,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="classify",
    )
    return out.rationale_he


def _fallback_rationale(candidate: ForecastCandidate) -> str:
    """Deterministic rationale used when the LLM call is unavailable/fails -- still cites the
    trigger items by id (per FR-5.3's citation rule), just without prose synthesis."""
    refs = " ".join(f"[item {iid}]" for iid in candidate.trigger_item_ids)
    return (
        f"זוהו {len(candidate.trigger_event_ids)} אירוע(ים) הקשורים לפלטפורמה '{candidate.platform_he}' "
        f"ב-90 הימים האחרונים {refs}; פלטפורמה זו נזקקת בדרך כלל ל-{candidate.payload_need_he}. "
        "נימוק זה נוצר באופן דטרמיניסטי (המודל השפתי לא היה זמין)."
    )


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------


def _upsert_forecast(
    candidate: ForecastCandidate, likelihood: float, window: tuple[dt.date, dt.date], rationale_he: str
) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_forecasts (
                platform, buyer_country, trigger_event_id, trigger_item_id, payload_need,
                candidate_vendors, likelihood, window_from, window_to, rationale_he, sources
            )
            VALUES (
                %(platform)s, %(buyer_country)s, %(trigger_event_id)s, %(trigger_item_id)s,
                %(payload_need)s, %(vendors)s, %(likelihood)s, %(window_from)s, %(window_to)s,
                %(rationale_he)s, %(sources)s
            )
            ON CONFLICT (platform, buyer_country, payload_need) DO UPDATE SET
                trigger_event_id = EXCLUDED.trigger_event_id,
                trigger_item_id = EXCLUDED.trigger_item_id,
                candidate_vendors = EXCLUDED.candidate_vendors,
                likelihood = EXCLUDED.likelihood,
                window_from = EXCLUDED.window_from,
                window_to = EXCLUDED.window_to,
                rationale_he = EXCLUDED.rationale_he,
                sources = EXCLUDED.sources
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
                "sources": [f"item:{iid}" for iid in candidate.trigger_item_ids] or None,
            },
        )
        return cur.fetchone()["id"]


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


def forecast_tenders(*, role: str = "resident", lookback_days: int = _LOOKBACK_DAYS) -> ForecastStats:
    """section-5.2 entry point: recent platform events -> deterministic candidates+likelihood ->
    upsert ``tender_forecasts`` (Hebrew rationale via the LLM, best-effort)."""
    stats = ForecastStats()
    platforms = load_platform_payloads()
    events = _recent_trigger_events(lookback_days)
    candidates = _build_candidates(platforms, events)
    stats.candidates = len(candidates)
    if not candidates:
        log.info("tender_forecast_done", **vars(stats))
        return stats

    all_item_ids = sorted({iid for c in candidates for iid in c.trigger_item_ids})
    item_rows = _fetchall(
        "SELECT id, title, url, clean_text FROM items WHERE id = ANY(%(ids)s)", {"ids": all_item_ids}
    )
    items_by_id = {r["id"]: r for r in item_rows}

    today = dt.date.today()
    for cand in candidates:
        likelihood = compute_likelihood(cand)
        window = _window(cand, today)
        data_block = _rationale_data_block(cand, items_by_id)
        try:
            rationale_he = _llm_rationale(cand, likelihood, window, data_block, role=role)
            stats.llm_used += 1
        except ResourceUnavailable:
            rationale_he = _fallback_rationale(cand)
            stats.llm_deferred += 1
        except LLMOutputError as exc:
            log.warning("tender_forecast_llm_failed", platform=cand.platform_key, error=str(exc)[:200])
            rationale_he = _fallback_rationale(cand)
            stats.llm_failed += 1

        _upsert_forecast(cand, likelihood, window, rationale_he)
        stats.upserted += 1

    log.info("tender_forecast_done", **vars(stats))
    return stats
