"""`POST /api/ask` -- RAG chat: retrieves relevant items and streams a cited Hebrew answer over SSE."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from eoa.api import ask_grounding, services
from eoa.config import settings
from eoa.errors import ResourceUnavailable
from eoa.llm import ollama_client

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ask"])

# U11 (2026-09-06 answer-format rewrite): `ask_answer_format.md` instructs the model to emit this
# exact sentinel, on its own line, after the full analyst answer and before an optional
# `{"source_notes": [...]}` JSON block -- the model's own per-source relevance notes, which belong
# in the UI's "מקורות" footer, never inline in the answer body. This is never shown to the user as
# text: the streaming loop below holds back only as much of the tail as could still be the start
# of this sentinel, so ordinary answer text keeps streaming token-by-token with no added latency,
# and only text at/after a confirmed sentinel match is diverted into `sources_buf` instead of a
# `token` event.
_SOURCES_SENTINEL = "===SOURCES_JSON==="

# ---------------------------------------------------------------------------------------------
# Round 2 (docs/qa/loop/round_2_chat_fixes.md, D5 P1): infinite-repetition-decoding loop guard.
# Round 1's judge report (docs/qa/loop/round_1_judge.md) live-found two golden questions entering
# an infinite repetition loop inside the "## פערים / מה לא ידוע" section -- 300-537s wall-clock,
# 30-49K chars of near-identical bullets, only stopped by the client's own timeout. Three
# independent, additive defenses:
#   (a) Ollama sampling options on the chat call itself (`repeat_penalty`/`repeat_last_n` make the
#       decoder itself less prone to looping; `num_predict` is a hard token-count backstop).
#   (b) a streaming n-gram/line repetition detector over the last ~600 chars of *answer* text
#       (never the trailing sources-JSON block) -- stops the generation the moment a loop starts,
#       instead of waiting for a token or wall-clock ceiling to (eventually) end it.
#   (c) a hard wall-clock ceiling per answer, independent of (b), for a degenerate pattern the
#       n-gram heuristic doesn't happen to catch.
# All three funnel into the same graceful-cut path: close the Ollama stream, truncate the answer
# at its last clean sentence boundary, append a one-line system note, and still emit `sources`.
# ---------------------------------------------------------------------------------------------

_CHAT_REPEAT_PENALTY = 1.15
_CHAT_REPEAT_LAST_N = 256
# Budget split: ~1800 tokens for the visible analyst answer, plus a small ~300-token allowance so
# the model still has room to emit the `===SOURCES_JSON===` tail after a full-length answer (the
# streaming contract is one continuous generation, so this is one `num_predict` covering both).
_CHAT_NUM_PREDICT_ANSWER = 1800
_CHAT_NUM_PREDICT_SOURCES_TAIL = 300
_CHAT_NUM_PREDICT = _CHAT_NUM_PREDICT_ANSWER + _CHAT_NUM_PREDICT_SOURCES_TAIL

_MAX_ANSWER_SECONDS = 240.0

_REPEAT_TAIL_CHARS = 600
_REPEAT_MIN_PATTERN_CHARS = 40
_REPEAT_MIN_COUNT = 3

_REPETITION_NOTE = "\n\n---\n_(התשובה קוצרה: המודל נכנס ללולאת חזרה.)_"
_TIMEOUT_NOTE = "\n\n---\n_(התשובה קוצרה: חריגה ממגבלת הזמן.)_"

# P1 fix (incident 2026-09-06 16:15-16:24): shown when the resource gate can't admit the chat's
# model within the interactive budget (`resources.interactive_wait_s`, default 20s) -- e.g. a
# nightly run or a report generation is holding the GPU. Emitted as a `token` (so it renders in
# today's chat UI exactly like any other answer text) *and* as its own `gate_busy` event (for a
# client that wants to distinguish it, e.g. to skip the "citations"/grounding UI chrome).
_GATE_BUSY_MESSAGE = "המודל המקומי תפוס כרגע (ריצת לילה/דוח); נסה שוב בעוד דקה"

# Sentinel distinguishing "the sync generator is exhausted" from any real chunk value (including
# `None`/`""`) when polling it via `next(stream_gen, _STREAM_DONE)` from the threadpool below.
_STREAM_DONE = object()

_NO_CITATION_PREFIX = "⚠ ללא ציטוטים: "
_OFF_TOPIC_PREFIX = "⚠ ייתכן שהתשובה אינה עוסקת בשאלה: "

_SENTENCE_END_RE = re.compile(r"[.!?״]")


def _repetition_detected(tail: str) -> bool:
    """Best-effort loop detector over ``tail`` (the trailing ``_REPEAT_TAIL_CHARS`` of streamed
    *answer* text -- never the sources-JSON tail). Either signal triggers:

    (a) a substring of >= ``_REPEAT_MIN_PATTERN_CHARS`` chars (the tail's own ending) occurs
        >= ``_REPEAT_MIN_COUNT`` times within ``tail``;
    (b) the same non-blank line occurs >= ``_REPEAT_MIN_COUNT`` times among the recent lines.

    Deliberately simple/cheap (called after every streamed chunk): no false-negative tolerance
    for "close but not exact" repeats is attempted -- round 1's finding was a verbatim-cycling
    loop, not a paraphrase loop.
    """
    if not tail:
        return False
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    if len(lines) >= _REPEAT_MIN_COUNT:
        counts: dict[str, int] = {}
        for ln in lines[-12:]:
            counts[ln] = counts.get(ln, 0) + 1
            if counts[ln] >= _REPEAT_MIN_COUNT:
                return True
    n = len(tail)
    if n >= _REPEAT_MIN_PATTERN_CHARS * _REPEAT_MIN_COUNT:
        pattern = tail[n - _REPEAT_MIN_PATTERN_CHARS :]
        if tail.count(pattern) >= _REPEAT_MIN_COUNT:
            return True
    return False


def _truncate_at_sentence(text: str) -> str:
    """Cut ``text`` at its last clean sentence boundary (``.``/``!``/``?``/``״``), dropping a
    trailing partial sentence left mid-word by an aborted generation. Returns ``text`` unchanged
    (just whitespace-trimmed) if no boundary is found at all."""
    matches = list(_SENTENCE_END_RE.finditer(text))
    if not matches:
        return text.rstrip()
    return text[: matches[-1].end()].rstrip()


def _question_hash(question: str) -> str:
    """Short, non-reversible correlation id for logging -- never the question text itself."""
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]


# Live-verified 2026-09-06 against docs/qa/loop/golden_questions.json Q3 (Greece/LORA): raw
# `extract_anchors()` output for that question is
# `['עסקת', 'ה-LORA', 'היוונית', 'Greece', 'עבור', 'התעשייה', 'הביטחונית']` -- generic Hebrew
# words ("עבור"/"התעשייה"/"הביטחונית") trivially appear in *any* EO/IR analyst answer regardless
# of topic, so the plain "any anchor present" check below never fired even though the answer
# never once mentioned LORA (round 1's exact D5 finding, still reproduced live before this fix).
# `docs/CONVENTIONS.md` rule 3 keeps technical/product terms and proper nouns in English inside
# Hebrew prose ("מונחים מקצועיים באנגלית בסוגריים בהופעה הראשונה") -- so the embedded Latin-script
# run inside a raw anchor (e.g. "ה-LORA" -> "LORA") is both the strongest topic-drift signal and
# reliably present in a genuinely on-topic answer; generic Hebrew anchors are the fallback only
# when a question has no Latin anchor at all (e.g. a fully Hebrew-named program).
_LATIN_ANCHOR_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")


_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}[ \t].*$", re.MULTILINE)


def _strip_markdown_headings(text: str) -> str:
    """Drop markdown heading lines (``# ...``/``## ...``) before the anchor check below.

    Live-verified 2026-09-06 (docs/qa/loop/golden_questions.json Q3, Greece/LORA): a model can
    "pass" a naive substring anchor check by echoing the anchor in a heading it generated by
    lightly rephrasing the question (e.g. a spurious ``# עסקת ה-LORA היוונית...`` H1 -- itself
    already against `ask_answer_format.md`'s own rule that the direct-answer section carries no
    heading at all) while the entire substantive body discusses something else completely. This
    reproduced round 1's Q3 finding again, unchanged, even after `_strong_anchors` fixed the
    generic-Hebrew-anchor false negative: "LORA" appeared exactly once, only in that echoed
    heading, while the rest of the answer stayed on an unrelated Greek air-defense deal.
    """
    return _MARKDOWN_HEADING_RE.sub("", text)


_FIRST_SECTION_HEADING_RE = re.compile(r"^###\s", re.MULTILINE)


def _answer_body_for_anchor_check(text: str) -> str:
    """The answer's substantive part -- ``### עובדות מרכזיות`` / ``### הערכת האנליסט`` / ``###
    פערים`` -- with markdown headings stripped, excluding the leading "תשובה ישירה" paragraph.

    Live-verified 2026-09-06 (docs/qa/loop/golden_questions.json Q3, Greece/LORA): stripping
    headings alone (``_strip_markdown_headings``) was not enough -- on a second live run, the
    model instead echoed "LORA" once in the *opening sentence itself* ("עסקת ה-LORA היוונית היא
    אירוע אסטרטגי...") and then spent the entire rest of the answer, including every "עובדות
    מרכזיות"/"הערכת האנליסט" bullet, on an unrelated Greek air-defense deal that never mentions
    LORA again. `ask_answer_format.md` mandates a direct-answer paragraph with no heading before
    the first ``###`` section, which is exactly where a model can trivially restate the question's
    own subject without engaging with it -- so the anchor check below looks only at what follows
    the first ``###``, falling back to the full (heading-stripped) text when no section marker is
    present at all (e.g. a very short answer with no sections).
    """
    m = _FIRST_SECTION_HEADING_RE.search(text)
    body = text[m.start() :] if m else text
    return _strip_markdown_headings(body)


def _strong_anchors(anchors: list[str]) -> list[str]:
    """Embedded Latin-script tokens (len >= 2) pulled out of each raw ``extract_anchors`` anchor,
    deduplicated case-insensitively, in order of first appearance."""
    strong: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        for m in _LATIN_ANCHOR_RE.finditer(anchor):
            token = m.group(0)
            if len(token) < 2:
                continue
            key = token.casefold()
            if key not in seen:
                seen.add(key)
                strong.append(token)
    return strong


_PAREN_SPAN_RE = re.compile(r"\(([^()]*)\)")


def _primary_anchors(question: str, strong_anchors: list[str]) -> list[str]:
    """``strong_anchors`` that do NOT come from a parenthetical gloss in ``question`` (e.g. the
    "(Greece)"/"(Elbit)"/"(C-UAS)" English translations glossing a preceding Hebrew term) --
    falls back to every strong anchor when none of them is primary (e.g. the subject itself is
    what's glossed, as in "מגן אור (Iron Beam)").

    Live-verified 2026-09-06 (docs/qa/loop/golden_questions.json, all 8 questions): a parenthetical
    gloss is optional/interchangeable with its Hebrew equivalent in a genuinely good answer --
    Q1/Q5/Q8's correct, on-topic answers never bothered to also say "Bradley"/"C-UAS"/"Elbit" in
    Latin script, only the Hebrew term. The non-parenthetical anchor, by contrast, is the one
    genuinely diagnostic of topic drift: Q3's answer stayed on an unrelated Greek air-defense deal
    and happened to quote an English source sentence containing "Greece" (the glossed anchor),
    which let it slip past a plain "any strong anchor" check even though "LORA" -- the actual,
    non-parenthetical subject of the question -- never appeared anywhere in the answer body.
    """
    gloss_text = " ".join(m.group(1) for m in _PAREN_SPAN_RE.finditer(question)).casefold()
    primary = [a for a in strong_anchors if a.casefold() not in gloss_text]
    return primary or strong_anchors


def _parse_source_notes(buf: str) -> dict[int, str]:
    """Best-effort ``{n: note}`` from the JSON blob the model wrote after the sentinel.

    Never raises -- a model that gets the format wrong (missing block, malformed JSON, wrong
    shape) just means the sources footer has no notes; it must never break the answer itself.
    """
    match = re.search(r"\{.*\}", buf, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    notes: dict[int, str] = {}
    for entry in data.get("source_notes") or []:
        if not isinstance(entry, dict) or "n" not in entry:
            continue
        try:
            n = int(entry["n"])
        except (TypeError, ValueError):
            continue
        note = entry.get("note")
        if isinstance(note, str) and note.strip():
            notes[n] = note.strip()
    return notes


# ---------------------------------------------------------------------------------------------
# Round 8 (package R8-chat-b, docs/qa/loop/round_7_judge_b.md D5 finding #2): a leaked internal
# delimiter reached 2 of 8 sampled `answer_final.text` values -- both ended with the literal
# `===SOURCES_JSON===\n"}]}` glued onto otherwise-clean content. The token-streaming loop below
# already does an exact-substring `_SOURCES_SENTINEL` split as it goes (kept unchanged below, as a
# low-latency optimisation only -- U11's own note above still applies) but that path is bypassed
# entirely whenever `answer_text` is replaced wholesale by a fresh, non-streamed LLM completion
# after the loop already finished -- exactly what `_run_citation_repair` does (reusing the same
# system+sources messages, which carry the same `ask_answer_format.md` instructions telling the
# model to always append the sentinel+JSON tail, so a repaired rewrite is just as likely to carry
# it as the original streamed answer was). `_split_sources_json` is applied twice below: once to
# the fully-assembled streamed `answer_text` (belt-and-suspenders for the "whole response arrived
# as one chunk" case the round-7b judge's own diagnosis raised) and once to the citation repair's
# own `corrected` text -- both *before* any grounding guard runs and before the `answer_final`
# event, per this round's own brief. `_strip_residual_sources_block` is the last-resort sanitizer
# the brief also asks for, run immediately before `answer_final` is yielded, independent of whether
# either `_split_sources_json` call above already caught it.
#
# Tolerant of the exact live-found leak shape (a bare sentinel with no valid JSON at all after it --
# `\n"}]}` is not parseable JSON, so `_parse_source_notes`'s own `\{.*\}` search correctly finds
# nothing there and just yields an empty notes dict, same as today) as well as the variants the
# brief calls out: extra/fewer `=` characters, a stray leading `#` heading marker, and the sentinel
# line wrapped in a markdown code fence (```` ```json ... ``` ````) -- none of which the streaming
# loop's exact-literal match would catch even when it does run.
# ---------------------------------------------------------------------------------------------

_SOURCES_SENTINEL_RE = re.compile(
    r"(?:```(?:json)?\s*)?"  # an opening code fence directly before the sentinel, if any
    r"#{0,6}\s*"  # a stray markdown heading marker glued onto the sentinel, if any
    r"=+\s*SOURCES_JSON\s*=+",  # the sentinel itself, tolerant of whitespace and the '=' count
    re.IGNORECASE,
)


def _split_sources_json(text: str) -> tuple[str, str]:
    """Split ``text`` at the first (tolerant) match of :data:`_SOURCES_SENTINEL_RE`. Returns
    ``(answer, sources_tail)`` -- ``answer`` is everything before the sentinel, right-trimmed;
    ``sources_tail`` is everything after it, handed to :func:`_parse_source_notes` exactly like the
    streaming loop's own ``sources_buf`` (which already tolerates a surrounding code fence or extra
    prose around the JSON blob itself via its own ``\\{.*\\}`` search, and safely yields no notes at
    all on a garbled/absent JSON blob -- see the section note above). Returns ``(text, "")``
    unchanged when no sentinel is found at all."""
    m = _SOURCES_SENTINEL_RE.search(text)
    if not m:
        return text, ""
    return text[: m.start()].rstrip(), text[m.end() :]


def _strip_residual_sources_block(text: str) -> str:
    """Last-resort sanitizer (round 8): strip a residual ``===SOURCES_JSON===`` sentinel -- and
    everything after it to the end of ``text`` -- that somehow survived every earlier split. A
    no-op when no sentinel is present."""
    m = _SOURCES_SENTINEL_RE.search(text)
    if not m:
        return text
    return text[: m.start()].rstrip()


# Round 8 item 3 (docs/qa/loop/round_7_judge_b.md, verified via `runtime/logs/api.2026-09-07.log`):
# live-sampled 5 of 5 real `ask.entailment_check` attempts on 2026-09-07 all skipped on
# `reason=timeout_or_error` -- see `ask_grounding.entailment_filter`'s own docstring for the root
# cause (a missing `interactive=True`, fixed there) and this round's own brief for the prescribed
# mitigation (raise the timeout, cap claims to 4). The cap is enforced *here*, not by lowering
# `config/config.yaml`'s `ask.entailment_max_claims` value itself, because that value is asserted
# `== 6` by `tests/unit/test_ask_round7.py`'s `TestAskConfig.test_default_config_values` -- a
# shared-suite test this package does not own and must not edit -- so the effective per-call cap is
# applied at the call site below instead, independent of whatever the raw config value is.
_ENTAILMENT_MAX_CLAIMS_CAP = 4


class AskRequest(BaseModel):
    # Q2-9: bounded so an oversized question can't be used to force an
    # unreasonably large retrieval/LLM-context payload.
    question: str = Field(..., max_length=4000)
    context_item_ids: list[int] = []
    context_entity_ids: list[int] = []
    history: list[dict[str, str]] = []
    provider: str | None = None  # U8: "ollama" | "agy[:<model>]" | "claude[:<model>]" | "codex[:<model>]"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _run_removal_guards(
    answer_text: str, question: str, retrieved: list[dict[str, Any]]
) -> tuple[str, int, dict[str, int]]:
    """Run every deterministic *removal/redaction* grounding guard from ``eoa.api.ask_grounding``
    over ``answer_text`` once, in a fixed order, returning ``(new_text, removed_count,
    removed_by_guard)``. Deliberately excludes ``sanitize_citation_markers`` (cheap enough to call
    separately at every call site that needs it) and the one-time-prepend guards not safe to run a
    second time on the same text (``retrieval_relevance_caveat``, ``relocate_source_admission_
    caveat``, ``low_citation_caveat``).

    Round 6 (docs/qa/loop/round_5_judge.md D5 finding #2, live Q2/Iron Beam): factored out so the
    identical guard sequence can be re-applied at every point ``answer_text`` can still change
    after the very first pass, not just once, right after generation, the way round 3/5 left it.
    The live gap this closes: a zero-citation answer's ``_run_citation_repair`` rewrite (below)
    became the new ``answer_text`` completely unguarded -- any fabrication the corrective LLM pass
    introduced (or reintroduced) while attaching `[n]` markers for the first time sailed straight
    through every guard, then straight into the anchor-miss "demoted" fallback section unexamined.
    Calling this same sequence again after that rewrite, and once more on the fully-assembled
    demoted text, closes both the specific repair-pass gap and the general "guards only ever ran
    once, upstream of a later rewrite" risk with one fix -- see docs/qa/loop/round_6_fixes.md.

    Round 7 (docs/qa/loop/round_6_judge.md D5, live Q1/XM30 recurrence + Q5/Skyranger): the
    tightened ``filter_claim_grounding`` (see its own docstring) and the new
    ``filter_uncited_factual_claims`` (live Q5: confident uncited claims left alongside cited
    siblings) both join this same re-applied sequence for the identical reason round 6 factored it
    out in the first place.

    Round 8 (docs/qa/loop/round_7_judge_b.md D5 finding #1, live "seven" -> "8" recurrence on item
    257): the new ``filter_claim_count_mismatch`` joins the same re-applied sequence, right after
    ``filter_claim_grounding``, for the identical reason.
    """
    if not retrieved:
        return answer_text, 0, {}
    removed_by_guard: dict[str, int] = {}
    total = 0

    answer_text, n = ask_grounding.ground_and_filter_answer(answer_text, question, retrieved)
    if n:
        total += n
        removed_by_guard["grounded_entity"] = n

    answer_text, n = ask_grounding.filter_claim_grounding(answer_text, question, retrieved)
    if n:
        total += n
        removed_by_guard["claim_grounding"] = n

    answer_text, n = ask_grounding.filter_claim_count_mismatch(answer_text, retrieved)
    if n:
        total += n
        removed_by_guard["count_mismatch"] = n

    answer_text, n = ask_grounding.filter_uncited_factual_claims(answer_text, retrieved)
    if n:
        total += n
        removed_by_guard["uncited_factual_claim"] = n

    answer_text, n = ask_grounding.filter_entity_equivalence(answer_text, retrieved, question=question)
    if n:
        total += n
        removed_by_guard["entity_equivalence"] = n

    answer_text, n = ask_grounding.filter_attribution_mismatches(answer_text, retrieved)
    if n:
        total += n
        removed_by_guard["attribution_mismatch"] = n

    answer_text, n = ask_grounding.filter_self_contradictions(answer_text, retrieved)
    if n:
        total += n
        removed_by_guard["self_contradiction"] = n

    return answer_text, total, removed_by_guard


def _run_citation_repair(
    messages: list[dict[str, Any]], answer_text: str, provider: str | None
) -> str | None:
    """Round 2 P2 (docs/qa/loop/round_2_chat_fixes.md): one short, non-streamed corrective pass
    that asks the model to rewrite ``answer_text`` with `[n]` citations attached, reusing the
    exact same system+sources context the original (uncited) answer saw. Returns ``None`` on any
    failure (LLM error, resource unavailable, ...) -- the caller falls back to a visible
    "no citations" prefix rather than ever raising out of the guard.

    Role: always ``resident`` -- the same model that just produced ``answer_text`` and is still
    warm from that call (``ollama.keep_alive``). Live-verified 2026-09-06 (docs/CONVENTIONS.md's
    12 GB VRAM card): resident (~7.5 GB) and ``light`` (~6.7 GB) don't fit together, so routing
    this corrective pass through ``light`` forces an unload+reload swap that queued for minutes
    behind the resource gate -- exactly the wrong trade for a pass meant to be short and cheap.
    """
    role = "resident"
    try:
        repair_messages = services.ask_citation_repair_messages(messages, answer_text)
        result = ollama_client.chat(
            role,
            repair_messages,
            task="react",
            options={"num_predict": 900},
            interactive=True,
            provider=provider,
        )
        return result.content.strip() or None
    except Exception as exc:  # a failed repair pass must never break the answer itself
        log.warning("ask.citation_repair_failed", error=str(exc))
        return None


@router.post("/ask")
async def ask(body: AskRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        try:
            retrieved = await run_in_threadpool(
                services.ask_retrieve, body.question, body.context_item_ids, body.context_entity_ids
            )
            messages, citations = services.ask_build_messages(body.question, body.history, retrieved)
            yield _sse({"type": "citations", "items": citations})
            # U8: tell the UI up front which provider/model will answer (badge chip), before
            # the (possibly slow, for a cloud CLI) call even starts.
            provider_kind, provider_model = ollama_client.resolve_provider_info(body.provider)
            yield _sse({"type": "meta", "provider": provider_kind, "model": provider_model})
            # P1 fix (incident 2026-09-06 16:15-16:24, docs/qa/...): `chat_stream` is a
            # synchronous generator -- its first `next()` blocks inside `ResourceGate.acquire()`
            # (which can itself `time.sleep()` across the gate's queue backoff) and every
            # subsequent `next()` blocks on a synchronous HTTP read. Driving it directly inside
            # this async generator (the previous approach, on the theory that a local
            # single-user deployment could accept blocking the loop for "the duration of this
            # one request") turned out to freeze the *entire* single-worker uvicorn process for
            # as long as the gate queued -- 7+ minutes live, with `GET /api/status` timing out at
            # 60s along with every other request. Each `next()` call below is now offloaded to
            # FastAPI's threadpool (`run_in_threadpool`) so the event loop stays free to serve
            # other requests for the whole time this one is blocked; see also the interactive
            # gate budget (`resources.interactive_wait_s`) in `eoa.resources.gate`, which now
            # caps how long that blocking wait can even be for an interactive call like this one.
            # U11: hold back at most `len(_SOURCES_SENTINEL) - 1` trailing characters of `pending`
            # at any time -- that's the most that could still turn into the sentinel once the next
            # chunk arrives, so ordinary text streams through with no perceptible delay. Once the
            # sentinel is confirmed, everything from there on is the (never streamed to the
            # client) source-notes JSON block instead of answer text.
            pending = ""
            in_sources = False
            sources_buf = ""
            answer_text = ""
            tail_buf = ""
            abort_note: str | None = None
            gate_busy = False
            t_answer_start = time.monotonic()

            stream_gen = ollama_client.chat_stream(
                "resident",
                messages,
                task="react",
                interactive=True,
                provider=body.provider,
                options={
                    "repeat_penalty": _CHAT_REPEAT_PENALTY,
                    "repeat_last_n": _CHAT_REPEAT_LAST_N,
                    "num_predict": _CHAT_NUM_PREDICT,
                },
            )
            try:
                while True:
                    chunk = await run_in_threadpool(next, stream_gen, _STREAM_DONE)
                    if chunk is _STREAM_DONE:
                        break
                    if time.monotonic() - t_answer_start > _MAX_ANSWER_SECONDS:
                        log.warning(
                            "ask.wallclock_abort",
                            question_hash=_question_hash(body.question),
                            seconds=_MAX_ANSWER_SECONDS,
                        )
                        abort_note = _TIMEOUT_NOTE
                        break
                    if in_sources:
                        sources_buf += chunk
                        continue
                    pending += chunk
                    idx = pending.find(_SOURCES_SENTINEL)
                    if idx != -1:
                        if idx > 0:
                            emitted = pending[:idx]
                            yield _sse({"type": "token", "text": emitted})
                            answer_text += emitted
                        in_sources = True
                        sources_buf = pending[idx + len(_SOURCES_SENTINEL) :]
                        pending = ""
                        continue
                    safe_len = max(0, len(pending) - (len(_SOURCES_SENTINEL) - 1))
                    if safe_len > 0:
                        emitted = pending[:safe_len]
                        yield _sse({"type": "token", "text": emitted})
                        answer_text += emitted
                        pending = pending[safe_len:]
                        tail_buf = (tail_buf + emitted)[-_REPEAT_TAIL_CHARS:]
                        if _repetition_detected(tail_buf):
                            log.warning(
                                "ask_repetition_abort",
                                question_hash=_question_hash(body.question),
                                chars=len(answer_text),
                            )
                            abort_note = _REPETITION_NOTE
                            break
            except ResourceUnavailable as exc:
                # Item 2 of the P1 fix: the interactive gate budget expired -- a nightly run or
                # report generation is holding the GPU/VRAM the chat model needs. Surface this
                # immediately as a clear, visible message instead of letting the request hang for
                # the patient `queue_timeout_min` a batch/pipeline caller would wait.
                log.warning("ask.gate_busy", question_hash=_question_hash(body.question), error=str(exc))
                gate_busy = True
            finally:
                # `chat_stream` is a generator (has `.close()`, which propagates `GeneratorExit`
                # through its `with c.stream(...)` and actually tears down the HTTP connection on
                # an abort); a test double or a plain-iterator stand-in may not be -- best-effort.
                close = getattr(stream_gen, "close", None)
                if callable(close):
                    close()

            if gate_busy:
                # Emitted as a plain `token` too (in addition to the dedicated `gate_busy` event)
                # so today's UI -- which renders `token`/`sources`/`done` but has no bespoke
                # handling for a `gate_busy` event yet -- still shows the user a clear message
                # instead of silence.
                yield _sse({"type": "token", "text": _GATE_BUSY_MESSAGE})
                yield _sse({"type": "gate_busy", "message": _GATE_BUSY_MESSAGE})
                yield _sse({"type": "sources", "items": []})
                return

            if abort_note is not None:
                # `pending` may still hold up to `len(_SOURCES_SENTINEL) - 1` real answer chars
                # held back only in case they turned out to be the start of the sentinel -- on
                # abort they never will, so flush them like a normal end-of-stream would before
                # truncating (for the guards below / a later full-replace) and appending the note.
                if pending and not in_sources:
                    yield _sse({"type": "token", "text": pending})
                    answer_text += pending
                    pending = ""
                answer_text = _truncate_at_sentence(answer_text)
                yield _sse({"type": "token", "text": abort_note})
                answer_text += abort_note
            elif pending and not in_sources:
                yield _sse({"type": "token", "text": pending})
                answer_text += pending

            # Round 8 (docs/qa/loop/round_7_judge_b.md D5 finding #2): a robust, tolerant pass over
            # the now fully-assembled `answer_text` -- independent of whatever the streaming loop's
            # own exact-literal match already did -- catches a sentinel (or a tolerant variant of
            # it) that arrived in a single chunk, or in a form the streaming match didn't
            # recognise. A no-op when `in_sources` is already True (the streaming loop already
            # split it out correctly) or no sentinel is present at all.
            if not in_sources:
                _stripped_answer, _tail = _split_sources_json(answer_text)
                if _tail:
                    log.warning(
                        "ask.sources_sentinel_leak_recovered", question_hash=_question_hash(body.question)
                    )
                    answer_text = _stripped_answer
                    sources_buf = _tail
                    in_sources = True

            # Round 3 (docs/qa/loop/round_2_judge.md, D5 new findings): two independent,
            # deterministic post-generation guards, run before the round-2 citation/anchor guards
            # below so those reason about the already-cleaned text.
            #   (a) strip a literal, never-substituted citation-placeholder token (`[n=5]`, `[n]`,
            #       `{n}`) live-found leaking into a rendered heading (round_2_judge.md's Q3
            #       finding) -- never a valid citation, always safe to remove outright.
            #   (b) drop any sentence/bullet that either names an entity/figure not grounded in
            #       the question, the retrieved sources, or the canonical watchlist (the Q4
            #       invented-professor/university/project pattern), or that cites a source that
            #       does not actually mention the watchlist entity it attributes to that source
            #       (the Q2 Rafael/AeroVironment conflation pattern) -- see
            #       `eoa.api.ask_grounding` for the full rationale and precision/recall trade-offs.
            answer_text, _leak_removed = ask_grounding.sanitize_citation_markers(answer_text)
            # Round 6 item 3 (docs/qa/loop/round_5_judge.md D2/D5, live Q8/SPECTRO finding): the
            # so_what/filler template-phrase crutch already banned from report prose leaks into
            # chat answers too -- pure style cleanup, independent of citations/retrieval, so it
            # runs unconditionally rather than inside the `if citations:` block below.
            answer_text, _template_phrases_removed = ask_grounding.strip_template_phrases(answer_text)
            ungrounded_removed = 0
            _removed_by_guard: dict[str, int] = {}
            if citations:
                answer_text, _grounding_removed = ask_grounding.ground_and_filter_answer(
                    answer_text, body.question, retrieved
                )
                ungrounded_removed += _grounding_removed
                if _grounding_removed:
                    _removed_by_guard["grounded_entity"] = _grounding_removed
                # Round 6 item 1 (docs/qa/loop/round_5_judge.md D5, worst-list item 3, live
                # Q1/XM30): a claim-vs-source check for the "עובדות מרכזיות" section and the
                # direct-answer paragraph -- catches a fabricated capability/relationship built out
                # of otherwise-real tokens (see `ask_grounding.filter_claim_grounding`'s own
                # docstring for the HEL/ATR/GPS live repro this closes).
                answer_text, _claim_removed = ask_grounding.filter_claim_grounding(
                    answer_text, body.question, retrieved
                )
                ungrounded_removed += _claim_removed
                if _claim_removed:
                    _removed_by_guard["claim_grounding"] = _claim_removed
                # Round 8 item 2 (docs/qa/loop/round_7_judge_b.md D5 finding #1, live "seven" ->
                # "8" recurrence on item 257): a plain-count mismatch for the same noun phrase
                # against the unit's own citation -- see `ask_grounding.filter_claim_count_mismatch`
                # for the full rationale (corrects the digit in place on an exact noun match, drops
                # the unit on a fuzzy-only match).
                answer_text, _count_removed = ask_grounding.filter_claim_count_mismatch(
                    answer_text, retrieved
                )
                ungrounded_removed += _count_removed
                if _count_removed:
                    _removed_by_guard["count_mismatch"] = _count_removed
                # Round 7 item 2 (docs/qa/loop/round_6_judge.md D5 worst-list #9, live
                # Q5/Skyranger): a confident, uncited claim left alongside cited siblings in the
                # same direct-answer/key-facts scope reads as equally well-supported when it is
                # not -- see `ask_grounding.filter_uncited_factual_claims`'s own docstring.
                answer_text, _uncited_removed = ask_grounding.filter_uncited_factual_claims(
                    answer_text, retrieved
                )
                ungrounded_removed += _uncited_removed
                if _uncited_removed:
                    _removed_by_guard["uncited_factual_claim"] = _uncited_removed
                # Round 5 (docs/qa/loop/round_3_judge.md, worst-list items 3/8 and its own
                # ranked-item-6 follow-up): three more deterministic, additive guards, all
                # documented in full in `eoa.api.ask_grounding` -- an entity-equivalence guard (the
                # live Q5 David's Sling/Skynex conflation), an attribution-consistency guard (the
                # live Q7 Finnish-RFI/"US government" mismatch), and a cross-sentence
                # self-contradiction pass. Chained after the round-3 guard above so each reasons
                # about the already-cleaned text, same as round 3 chained after round 2.
                # Round 5 P10: the question's own acronym/expansion glosses (DROIC / Digital Read-Out
                # Integrated Circuit) are not fabricated equivalences -- pass the question through.
                answer_text, _equiv_removed = ask_grounding.filter_entity_equivalence(
                    answer_text, retrieved, question=body.question
                )
                # Round 5 P10: when NO retrieved source mentions any primary anchor of the question
                # (live Q3/LORA, Q6/AUSA: the model echoes the anchor over unrelated sources), say so
                # explicitly at the top instead of letting the echo pass the anchor check below.
                answer_text, _relevance_caveat = ask_grounding.retrieval_relevance_caveat(
                    answer_text, body.question, retrieved
                )
                if _relevance_caveat:
                    _removed_by_guard["retrieval_relevance_caveat"] = 1
                ungrounded_removed += _equiv_removed
                if _equiv_removed:
                    _removed_by_guard["entity_equivalence"] = _equiv_removed
                answer_text, _attrib_removed = ask_grounding.filter_attribution_mismatches(
                    answer_text, retrieved
                )
                ungrounded_removed += _attrib_removed
                if _attrib_removed:
                    _removed_by_guard["attribution_mismatch"] = _attrib_removed
                answer_text, _contradiction_removed = ask_grounding.filter_self_contradictions(
                    answer_text, retrieved
                )
                ungrounded_removed += _contradiction_removed
                if _contradiction_removed:
                    _removed_by_guard["self_contradiction"] = _contradiction_removed
                # Round 7 item 2, continued (live Q5/Skyranger): the model's own "N of M sources
                # unrelated" admission belongs at the top, as the answer's leading caveat, not
                # trailing in a footer discovered only after the confident claims above it. When no
                # such admission is present but the answer still ends up with fewer than 2 actually
                # `[n]`-cited factual sentences overall, a generic caveat covers the same ground.
                answer_text, _admission_moved = ask_grounding.relocate_source_admission_caveat(answer_text)
                if _admission_moved:
                    _removed_by_guard["source_admission_caveat"] = 1
                else:
                    answer_text, _low_citation_added = ask_grounding.low_citation_caveat(answer_text)
                    if _low_citation_added:
                        _removed_by_guard["low_citation_caveat"] = _low_citation_added
                # Round 7 item 3 (config-gated, chat-only): an optional light-model entailment
                # check on top of every deterministic guard above -- see
                # `ask_grounding.entailment_filter`'s own docstring for the hard 20s timeout and
                # graceful-skip-on-error contract. Never wired into the pipeline/report paths.
                ask_cfg = settings().ask
                if ask_cfg.entailment_check:
                    answer_text, _entailment_removed = await run_in_threadpool(
                        ask_grounding.entailment_filter,
                        answer_text,
                        retrieved,
                        # Round 8 item 3: capped to `_ENTAILMENT_MAX_CLAIMS_CAP` regardless of the
                        # raw config value -- see that constant's own docstring for why the cap
                        # lives here instead of in `config/config.yaml` itself.
                        max_claims=min(ask_cfg.entailment_max_claims, _ENTAILMENT_MAX_CLAIMS_CAP),
                        # Round 10 (docs/qa/loop/round_9_judge.md finding 2): the only real call
                        # site opts into the cloud-chain fallback (`ask_grounding.entailment_filter`'s
                        # own docstring) -- a local RAM shortage that starves the primary `ollama`
                        # attempt now gets a second, cloud-routed chance instead of silently skipping
                        # every single time.
                        chain_fallback=True,
                    )
                    ungrounded_removed += _entailment_removed
                    if _entailment_removed:
                        _removed_by_guard["entailment_check"] = _entailment_removed
            if _leak_removed or _template_phrases_removed or ungrounded_removed:
                log.warning(
                    "ask.grounding_repair",
                    question_hash=_question_hash(body.question),
                    template_leaks_removed=_leak_removed,
                    template_phrases_removed=_template_phrases_removed,
                    ungrounded_removed=ungrounded_removed,
                    removed_by_guard=_removed_by_guard,
                )
                pass  # answer_final is emitted exactly once below (round-6 judge: duplicates/missing)

            # Round 2 P2 (docs/qa/loop/round_2_chat_fixes.md): live-verified 2026-09-06 that 3/5
            # cleanly-completed answers had zero inline [n] despite a populated sources array.
            if citations and not re.search(r"\[\d+\]", answer_text):
                corrected = await run_in_threadpool(
                    _run_citation_repair, messages, answer_text, body.provider
                )
                if corrected:
                    # Round 8 finding #2: `_run_citation_repair` reuses the same system+sources
                    # messages `services.ask_build_messages` built the original answer from --
                    # which carry the same `ask_answer_format.md` instructions telling the model to
                    # always append the sentinel+JSON tail -- so a repaired rewrite is just as
                    # likely to carry a leaked sentinel as the original streamed answer was, and
                    # this one-shot non-streamed call never goes through the streaming loop's own
                    # split at all. Split it here, before the `[n]` check below, so a trailing tail
                    # never reaches `answer_text`/`answer_final`.
                    corrected, _repair_tail = _split_sources_json(corrected)
                    if _repair_tail and not in_sources:
                        sources_buf = _repair_tail
                        in_sources = True
                    corrected, _ = ask_grounding.sanitize_citation_markers(corrected)
                if corrected and re.search(r"\[\d+\]", corrected):
                    answer_text = corrected
                    # Round 6 item 2 (docs/qa/loop/round_5_judge.md D5 finding #2, live Q2/Iron
                    # Beam): `corrected` is a brand-new LLM rewrite that never passed through any
                    # of the grounding guards above -- round 5 adopted it as the new `answer_text`
                    # completely unguarded. Re-run the same removal-guard sequence on it now, same
                    # as the very first pass, before it can reach the anchor check/demotion below.
                    answer_text, _repair_removed, _repair_by_guard = _run_removal_guards(
                        answer_text, body.question, retrieved
                    )
                    if _repair_removed:
                        for _k, _v in _repair_by_guard.items():
                            _removed_by_guard[_k] = _removed_by_guard.get(_k, 0) + _v
                        ungrounded_removed += _repair_removed
                        log.warning(
                            "ask.grounding_repair_post_citation_repair",
                            question_hash=_question_hash(body.question),
                            ungrounded_removed=_repair_removed,
                            removed_by_guard=_repair_by_guard,
                        )
                else:
                    answer_text = _NO_CITATION_PREFIX + answer_text
                pass  # answer_final is emitted exactly once below (round-6 judge: duplicates/missing)

            # Round 2 P2 topic-substitution guard (docs/qa/loop/round_2_chat_fixes.md, D5 Q3
            # Greece/LORA finding): the answer must mention at least one deterministic anchor
            # (proper noun/acronym/number) pulled from the question itself, reusing
            # `eoa.search.deep_search.extract_anchors` (same anchoring already used to gate deep
            # investigations) -- an answer that never touches any anchor almost certainly drifted
            # onto an unrelated but superficially similar topic. `_strong_anchors` narrows to the
            # Latin-script tokens among them (generic Hebrew anchors are too weak a signal on their
            # own); `_primary_anchors` further narrows to the non-parenthetical ones when any exist
            # (a "(Greece)"/"(Elbit)"-style gloss is too easy to satisfy by incidental quotation --
            # see its docstring for the live Q3 repro this closes).
            from eoa.search.deep_search import extract_anchors

            anchors = extract_anchors(body.question)
            strong_anchors = _strong_anchors(anchors)
            check_anchors = _primary_anchors(body.question, strong_anchors) if strong_anchors else anchors
            anchor_search_text = _answer_body_for_anchor_check(answer_text).casefold()
            if check_anchors and not any(a.casefold() in anchor_search_text for a in check_anchors):
                log.warning(
                    "ask.anchor_miss",
                    question_hash=_question_hash(body.question),
                    anchors=check_anchors,
                )
                # Round 3 (docs/qa/loop/round_2_judge.md, D5 Q3 finding): round 2's fix made the
                # miss visible (a warning prefix) but still presented the off-topic content as the
                # main answer body, immediately after the warning. Strengthened so the explicit gap
                # statement (naming the missing anchor) comes first, and the substitute content --
                # which may still be useful context, just not an answer to what was asked -- is
                # demoted into its own clearly-labelled section rather than left reading as if it
                # were the direct answer. `_OFF_TOPIC_PREFIX` itself is kept byte-for-byte (round 2
                # tests assert `answer_text.startswith(_OFF_TOPIC_PREFIX)`) with the gap statement
                # appended immediately after it, still ahead of everything else.
                gap_statement = (
                    f"המקורות שנשלפו אינם מזכירים {', '.join(check_anchors)} "
                    "עבור ההקשר שנשאל — לא ניתן לאשר תשובה ישירה."
                )
                answer_text = (
                    _OFF_TOPIC_PREFIX + gap_statement + "\n\n### הקשר קרוב (לא התשובה)\n" + answer_text
                )
                # Round 6 item 2 (docs/qa/loop/round_5_judge.md D5 finding #2, live Q2/Iron Beam):
                # the demoted section is already built from text every guard above has already
                # seen -- but belt-and-suspenders costs nothing here and closes any future gap in
                # that assumption (e.g. a guard added later that only some upstream call site
                # remembers to invoke). Re-run the same removal-guard sequence on the fully
                # assembled demoted text before it goes out.
                answer_text, _demoted_removed, _demoted_by_guard = _run_removal_guards(
                    answer_text, body.question, retrieved
                )
                if _demoted_removed:
                    for _k, _v in _demoted_by_guard.items():
                        _removed_by_guard[_k] = _removed_by_guard.get(_k, 0) + _v
                    ungrounded_removed += _demoted_removed
                    log.warning(
                        "ask.grounding_repair_post_demotion",
                        question_hash=_question_hash(body.question),
                        ungrounded_removed=_demoted_removed,
                        removed_by_guard=_demoted_by_guard,
                    )
                pass  # answer_final is emitted exactly once below (round-6 judge: duplicates/missing)

            # Round 6 item 5 (live-found on an iPhone Safari e2e run): a final, order-independent
            # normalisation pass -- a prior guard's unit removal can leave a `###`-style heading
            # glued onto the tail of the preceding line (the newline that used to separate them
            # belonged to the removed unit). Never rewrites/removes content, only re-inserts a line
            # break, so it is always safe to run last, after every guard above.
            answer_text = ask_grounding.ensure_headings_on_own_line(answer_text)
            # Round 10 (docs/qa/loop/round_9_judge.md worst #3, D5): live-sampled 2026-09-07, 5 of 8
            # golden answers shipped a truncated/dangling opening sentence -- a removal guard's own
            # unit boundary left a fragment (not a whole sentence) in place; see `_iter_units`'s own
            # round-10 fix for the confirmed root cause (a decimal point mis-parsed as a sentence
            # terminator) and `ask_grounding.enforce_answer_coherence`'s own docstring for the
            # content-blind safety net that also catches any removal shape that fix does not cover.
            # Content-blind and order-independent by design, so it runs last, after every guard
            # above (including the heading-normalisation pass just above it).
            answer_text, _coherence_removed = ask_grounding.enforce_answer_coherence(answer_text)
            if _coherence_removed:
                _removed_by_guard["dangling_fragment"] = _coherence_removed
                ungrounded_removed += _coherence_removed
            # Round 8 last-resort sanitizer (docs/qa/loop/round_7_judge_b.md D5 finding #2): strip
            # any residual sentinel/JSON tail that somehow survived every split above -- a no-op in
            # the overwhelming common case, defense-in-depth for the one case this round's own live
            # sample actually hit.
            answer_text = _strip_residual_sources_block(answer_text)
            # Round-6 judge (D5 worst #2/#3): the guards used to emit one `answer_final` per stage
            # that changed the text -- none at all when nothing changed (Q6), several with
            # intermediate/truncated texts when the anchor demotion and the citation repair both
            # fired (Q3/Q4). Contract now: exactly one `answer_final`, always, carrying the fully
            # guarded text and the aggregated guard counters.
            yield _sse(
                {
                    "type": "answer_final",
                    "text": answer_text,
                    "ungrounded_removed": ungrounded_removed,
                    "removed_by_guard": _removed_by_guard,
                }
            )

            notes = _parse_source_notes(sources_buf) if in_sources else {}
            sources: list[dict[str, Any]] = [{**c, "note": notes.get(c["n"])} for c in citations]
            yield _sse({"type": "sources", "items": sources})
        except Exception as exc:
            log.warning("ask.stream_failed", error=str(exc))
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")
