"""Deep Search Protocol (FR-7/FR-8): a budgeted, multilingual, persistent ReAct investigation.

Rounds (persistence protocol):
  1. direct queries (he/en)
  2. reformulations: synonyms, alternate program/system names, contract numbers, acronyms
  3. source-type switch: official releases → regulatory (SEC/EDGAR) → tender portals → patents → archives →
     conference material; secondary languages join here
  4. entity decomposition: subsidiaries, known partners, key people, program codes

Budgets: max_queries, max_pages, per_investigation_timeout_min, confidence_stop. The model that reads
page text has NO tools (it only summarises DATA); the orchestrating loop here is the only tool caller.
Everything tried is logged to ``investigation_log``; a not-found result is reported honestly.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings
from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import InvestigationOut, QueryPlan
from eoa.search.searxng_client import SearchHit, search

log = structlog.get_logger(__name__)

ROUND_HINTS = {
    1: "Round 1 — direct: ask the question plainly in Hebrew and English.",
    2: "Round 2 — reformulate: synonyms, alternative program/system names, acronyms, contract or solicitation numbers, "
    "manufacturer part names; vary phrasing.",
    3: "Round 3 — switch source types: official press releases, SEC/EDGAR filings, government contract portals "
    "(SAM.gov, TED, defense.gov contracts), patents, conference agendas, web archives; add secondary languages "
    "appropriate to the actors (Russian for Russian actors, Chinese for Chinese actors, etc.).",
    4: "Round 4 — decompose entities: subsidiaries, known partners, key executives, program codes, customer nations.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Web metasearch. Returns titles/snippets/urls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "lang": {"type": "string", "description": "ISO 639-1 (he,en,ru,zh,fr,de,ar,ko,tr)"},
                },
                "required": ["query", "lang"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Fetch a URL and return its sanitized main text.",
            "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Stop and report the conclusion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": ["found", "partial", "not_found"]},
                    "answer_he": {"type": "string"},
                    "confidence": {"type": "number"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "key_facts": {"type": "array", "items": {"type": "string"}},
                    "contradictions_he": {"type": "string"},
                    "what_was_tried_he": {"type": "string"},
                },
                "required": ["outcome", "answer_he", "confidence", "sources"],
            },
        },
    },
]


@dataclass
class Budget:
    max_queries: int
    max_pages: int
    deadline: float
    confidence_stop: float
    queries: int = 0
    pages: int = 0

    @property
    def exhausted(self) -> str | None:
        if time.monotonic() > self.deadline:
            return "stopped_timeout"
        if self.queries >= self.max_queries and self.pages >= self.max_pages:
            return "stopped_budget"
        return None

    def remaining_text(self) -> str:
        left = max(int(self.deadline - time.monotonic()), 0)
        return f"queries {self.queries}/{self.max_queries}, pages {self.pages}/{self.max_pages}, {left // 60} min left"


@dataclass
class Investigation:
    job_id: int | None
    item_id: int | None
    question: str
    result: InvestigationOut | None = None
    outcome: str = "not_found"
    rounds_done: int = 0
    read_urls: list[str] = field(default_factory=list)
    hits_seen: dict[str, SearchHit] = field(default_factory=dict)
    stop_requested: bool = False


class StopRequested(Exception):
    pass


# ----------------------------------------------------------------------------- tools
def _tool_search(inv: Investigation, budget: Budget, query: str, lang: str, round_no: int) -> str:
    if budget.queries >= budget.max_queries:
        return json.dumps({"error": "query budget exhausted"})
    budget.queries += 1
    resp = search(query, lang, max_results=8)
    for h in resp.hits:
        inv.hits_seen.setdefault(h.url, h)
    _log(
        inv,
        round_no,
        lang,
        query,
        engine="searxng",
        results_n=len(resp.hits),
        pages_read=0,
        outcome="partial" if resp.hits else "not_found",
        notes=resp.error,
    )
    out = [{"url": h.url, "title": h.title, "snippet": h.snippet, "date": h.published} for h in resp.hits]
    return json.dumps({"query": query, "lang": lang, "results": out, "error": resp.error}, ensure_ascii=False)


def _tool_read(inv: Investigation, budget: Budget, url: str, round_no: int) -> str:
    if budget.pages >= budget.max_pages:
        return json.dumps({"error": "page budget exhausted"})
    if url in inv.read_urls:
        return json.dumps({"error": "already read", "url": url})
    budget.pages += 1
    inv.read_urls.append(url)
    try:
        from eoa.fetch.remote import fetch_remote
        from eoa.security.guard import screen

        page = fetch_remote(url)
        text = page.get("text") or ""
        title = page.get("title") or ""
        verdict = screen(
            text,
            title,
            item_id=f"inv-{inv.job_id}",
            sanitizer_flags=list(page.get("suspicious") or []),
            hidden_text_ratio=float(page.get("hidden_text_ratio") or 0.0),
            encoded_blobs=int(page.get("encoded_blobs") or 0),
            use_l2=False,
        )
        if verdict.verdict != "clean":
            _log(
                inv,
                round_no,
                None,
                None,
                engine="fetch",
                results_n=0,
                pages_read=1,
                outcome="not_found",
                notes=f"security {verdict.verdict}: {verdict.kind}",
            )
            return json.dumps({"url": url, "error": f"page quarantined by security gate ({verdict.kind})"})
        summary = _summarise_page(inv, text, url)
        _log(
            inv,
            round_no,
            page.get("lang"),
            None,
            engine="fetch",
            results_n=0,
            pages_read=1,
            outcome="partial",
        )
        return json.dumps(
            {
                "url": url,
                "title": title,
                "published": str(page.get("published_at") or ""),
                "lang": page.get("lang"),
                "summary": summary,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        _log(
            inv,
            round_no,
            None,
            None,
            engine="fetch",
            results_n=0,
            pages_read=1,
            outcome="not_found",
            notes=f"fetch failed: {str(exc)[:160]}",
        )
        return json.dumps({"url": url, "error": str(exc)[:200]})


def _summarise_page(inv: Investigation, text: str, url: str) -> str:
    """Tool-less reader: extracts what the page says about the question (DATA-framed, no tools)."""
    prompt = (
        f"השאלה הנחקרת: {inv.question}\n"
        "קרא את הדף (DATA) והחזר בעברית: (1) העובדות הרלוונטיות לשאלה, עם מספרים ותאריכים מדויקים; "
        "(2) ציטוט קצר באנגלית של המשפט המרכזי; (3) מה הדף לא אומר. אם הדף לא רלוונטי — כתוב 'לא רלוונטי'. עד 180 מילים.\n\n"
        + wrap_data(text[:14000], f"inv-{inv.job_id}", url)
    )
    res = chat(
        "resident",
        [{"role": "system", "content": DATA_GUARD_SYSTEM}, {"role": "user", "content": prompt}],
        task="summarize",
        think=False,
        options={"temperature": 0.1},
    )
    return res.content.strip()[:2500]


def _log(
    inv: Investigation,
    round_no: int,
    lang: str | None,
    query: str | None,
    *,
    engine: str,
    results_n: int,
    pages_read: int,
    outcome: str,
    notes: str | None = None,
) -> None:
    try:
        from eoa.memory.relational import insert_investigation_log

        insert_investigation_log(
            job_id=inv.job_id,
            trigger_item=inv.item_id,
            round=round_no,
            lang=lang,
            query=query,
            engine=engine,
            results_n=results_n,
            pages_read=pages_read,
            outcome=outcome,
            notes=notes,
        )
    except Exception as exc:
        log.debug("investigation_log_failed", error=str(exc)[:120])


def _check_stop(inv: Investigation) -> None:
    if inv.job_id is None:
        return
    try:
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute(
                "SELECT payload->>'stop' AS stop FROM jobs WHERE id=%s", (inv.job_id,)
            ).fetchone()
            if row and str(row["stop"]).lower() == "true":
                inv.stop_requested = True
                raise StopRequested
    except StopRequested:
        raise
    except Exception:
        pass


# ----------------------------------------------------------------------------- planning
def plan_queries(question: str, round_no: int, langs: list[str], context_he: str) -> list[dict[str, str]]:
    """Ask the model for this round's queries (multilingual, term-aware translation)."""
    prompt = render(
        "deep_search_plan",
        question=question,
        round_hint=ROUND_HINTS[round_no],
        langs=", ".join(langs),
        context=context_he or "אין.",
    )
    try:
        plan = chat_structured(
            "resident",
            QueryPlan,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="react",
        )
        return [q for q in plan.queries if q.get("query") and q.get("lang") in langs][:8]
    except LLMOutputError:
        return [{"lang": lang, "query": question} for lang in langs[:2]]


# ----------------------------------------------------------------------------- main loop
def investigate(
    question: str,
    *,
    item_id: int | None = None,
    job_id: int | None = None,
    context_he: str = "",
    langs: list[str] | None = None,
    max_rounds: int = 4,
) -> Investigation:
    """Run the persistence protocol; returns an Investigation with ``result`` (never invents)."""
    cfg = settings().deep_search
    inv = Investigation(job_id=job_id, item_id=item_id, question=question)
    budget = Budget(
        cfg.max_queries,
        cfg.max_pages,
        time.monotonic() + cfg.per_investigation_timeout_min * 60,
        cfg.confidence_stop,
    )
    primary = langs or cfg.langs_primary
    transcript: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)
            + "\n\n"
            + render("deep_search_system"),
        },
        {"role": "user", "content": f"שאלת החקירה: {question}\nהקשר ידוע: {context_he or 'אין.'}"},
    ]
    try:
        for round_no in range(1, max_rounds + 1):
            _check_stop(inv)
            inv.rounds_done = round_no
            if budget.exhausted:
                break
            langs_now = primary + (cfg.langs_secondary if round_no >= 3 else [])
            queries = plan_queries(question, round_no, langs_now, context_he)
            # seed the round: run planned queries directly (parallel across languages), then let the model act
            seeded = []
            for q in queries:
                if budget.queries >= budget.max_queries:
                    break
                seeded.append(_tool_search(inv, budget, q["query"], q["lang"], round_no))
            transcript.append(
                {
                    "role": "user",
                    "content": f"[{ROUND_HINTS[round_no]}]\nתוצאות חיפוש ראשוניות של הסבב ({budget.remaining_text()}):\n"
                    + "\n".join(s[:3500] for s in seeded)
                    + "\n\nבחר עד 4 דפים לקריאה (read), חפש עוד אם צריך (search), או סיים (finish) כשיש תשובה בביטחון "
                    f"≥ {cfg.confidence_stop} או כשמיצית את הסבב. לעולם אל תמציא — אם לא נמצא, finish עם not_found.",
                }
            )
            finished = _act(inv, budget, transcript, round_no)
            if finished:
                break
            if inv.result and inv.result.confidence >= cfg.confidence_stop:
                break
    except StopRequested:
        log.info("investigation_stopped_by_user", job_id=job_id)
    except ResourceUnavailable as exc:
        log.warning("investigation_resources", job_id=job_id, error=str(exc))

    if inv.result is None:
        inv.result = InvestigationOut(
            outcome="not_found",
            answer_he="לא נמצא מידע מספק במסגרת התקציב.",
            confidence=0.0,
            sources=list(inv.read_urls),
            what_was_tried_he=f"{budget.queries} שאילתות, {budget.pages} דפים, {inv.rounds_done} סבבים.",
        )
    inv.outcome = budget.exhausted or inv.result.outcome
    _log(
        inv,
        inv.rounds_done,
        None,
        None,
        engine="final",
        results_n=len(inv.hits_seen),
        pages_read=budget.pages,
        outcome=inv.outcome
        if inv.outcome in {"found", "partial", "not_found", "stopped_budget", "stopped_timeout"}
        else "partial",
        notes=inv.result.answer_he[:500],
    )
    _learn(inv)
    return inv


def _act(
    inv: Investigation, budget: Budget, transcript: list[dict[str, Any]], round_no: int, max_steps: int = 8
) -> bool:
    """Let the model call tools until it finishes or the step/budget cap; returns True if finished."""
    for _ in range(max_steps):
        _check_stop(inv)
        if budget.exhausted:
            transcript.append(
                {
                    "role": "user",
                    "content": "התקציב מוצה. סכם עכשיו עם finish (found/partial/not_found), בלי להמציא.",
                }
            )
        res = chat(
            "resident", transcript, task="react", tools=TOOLS, think=False, options={"temperature": 0.2}
        )
        transcript.append(
            {"role": "assistant", "content": res.content, "tool_calls": res.tool_calls}
            if res.tool_calls
            else {"role": "assistant", "content": res.content}
        )
        if not res.tool_calls:
            transcript.append({"role": "user", "content": "השתמש בכלי (search / read / finish)."})
            continue
        for call in res.tool_calls:
            fn = call.get("function", {})
            name, args = fn.get("name"), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name == "search":
                out = _tool_search(
                    inv, budget, str(args.get("query", "")), str(args.get("lang", "en")), round_no
                )
            elif name == "read":
                out = _tool_read(inv, budget, str(args.get("url", "")), round_no)
            elif name == "finish":
                try:
                    inv.result = InvestigationOut.model_validate(
                        {
                            **args,
                            "sources": [
                                s for s in args.get("sources", []) if s in inv.read_urls or s in inv.hits_seen
                            ],
                        }
                    )
                except Exception as exc:
                    out = json.dumps({"error": f"invalid finish payload: {str(exc)[:200]}"})
                    transcript.append({"role": "tool", "content": out, "tool_name": "finish"})
                    continue
                return True
            else:
                out = json.dumps({"error": f"unknown tool {name}"})
            transcript.append({"role": "tool", "content": out[:6000], "tool_name": name})
        if budget.exhausted and inv.result is None:
            continue
    return inv.result is not None


def _learn(inv: Investigation) -> None:
    """Record what worked in search_playbook (successful queries → strategy)."""
    if not inv.result or inv.result.outcome == "not_found":
        return
    try:
        from eoa.db import connection

        with connection() as conn:
            for url in inv.result.sources[:5]:
                hit = inv.hits_seen.get(url)
                if not hit:
                    continue
                conn.execute(
                    "INSERT INTO search_playbook(pattern, strategy, lang, success_count, last_used) VALUES (%s,%s,%s,1,now()) "
                    "ON CONFLICT DO NOTHING",
                    (inv.question[:200], f"engine={hit.engine}; title={hit.title[:120]}", None),
                )
    except Exception as exc:
        log.debug("playbook_write_failed", error=str(exc)[:120])
