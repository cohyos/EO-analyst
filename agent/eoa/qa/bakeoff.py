"""Model bake-off harness for the QA continuous loop (docs/qa/loop/BAKEOFF.md).

Goal: measure how much of the D1/D2 content-quality gap (docs/qa/loop/round_1_judge.md: D1=40,
D2=55) is the *model* rather than the prompts, at zero incremental API cost, by running the
existing classify/triage/analyze stage functions (``eoa.pipeline.{classify,triage,analyze}``) and
the "ask the analyst" chat builder (``eoa.api.services.ask_build_messages``) unchanged against a
swappable set of candidate LLMs -- local Ollama models substituted into the ``resident`` role, or
cloud CLI providers (``agy``/``claude``) already installed and authenticated per
``docs/adr/005-cloud-llm-cli.md``.

Nothing here persists to the DB (no ``persist_classification``/``persist_analysis`` calls), writes
to ``config/*.yaml``, or sets ``EOA_PIPELINE`` -- ``candidate_context`` below only ever mutates the
in-process, ``@lru_cache``-singleton ``Settings`` object (restored on exit) or the
``chat_structured`` name each pipeline module bound at import time (also restored on exit). See
``scripts/bakeoff_golden.py`` for the CLI driver and ``docs/qa/loop/BAKEOFF.md`` for results.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, Field

from eoa.config import settings
from eoa.llm.ollama_client import ChatResult, chat, chat_structured
from eoa.llm.schemas.analysis import AnalyzeOut, ClassifyOut, TriageOut
from eoa.pipeline import analyze as analyze_mod
from eoa.pipeline import classify as classify_mod
from eoa.pipeline import triage as triage_mod
from eoa.qa.d1_classify import score_D1
from eoa.qa.d2_summary import score_D2
from eoa.qa.types import DomainScore

log = structlog.get_logger(__name__)

_PIPELINE_MODULES = (classify_mod, triage_mod, analyze_mod)


# ---------------------------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One bake-off entrant.

    ``kind="ollama"``: ``model_key`` must be a key in ``config/models.yaml``'s ``models:`` map;
    the candidate is run by temporarily repointing ``settings().models[role_target]`` at it.

    ``kind="cloud"``: ``provider`` must be a ``chat()``/``chat_structured()`` provider string,
    e.g. ``"agy:gemini-3.1-pro-high"`` or ``"claude:claude-sonnet-5"`` (docs/adr/005). Cost is
    0 for every candidate here -- local Ollama (already-downloaded weights) or an existing CLI
    subscription (agy/claude), never a paid per-token API key.
    """

    name: str
    kind: str  # "ollama" | "cloud"
    label: str
    role_target: str = "resident"
    model_key: str | None = None
    provider: str | None = None
    cost_note: str = "$0 (local weights)"
    notes: str = ""


#: The task brief's candidate list (A-F). Availability (installed / authenticated) is checked at
#: run time by ``scripts/bakeoff_golden.py`` -- listing an unavailable candidate here is harmless,
#: it will simply fail fast on its first call and be recorded as such.
CANDIDATES: list[Candidate] = [
    Candidate(
        "dictalm3_12b", "ollama", "DictaLM-3.0-Nemotron-12B (current resident)",
        model_key="dictalm3_12b", cost_note="$0 (local, currently resident)",
    ),
    Candidate(
        "gemma4_12b", "ollama", "Gemma 4 12B",
        model_key="gemma4_12b", cost_note="$0 (local, installed)",
    ),
    Candidate(
        "gemma4_e4b", "ollama", "Gemma 4 e4B (light)",
        model_key="gemma4_e4b", cost_note="$0 (local, installed)",
    ),
    Candidate(
        "agy_gemini31_pro_high", "cloud", "Gemini 3.1 Pro (High) via agy CLI",
        provider="agy:gemini-3.1-pro-high", cost_note="$0 (agy subscription)",
    ),
    Candidate(
        "agy_gemini38_flash_high", "cloud", "Gemini 3.8 Flash (High) via agy CLI",
        provider="agy:gemini-3.8-flash-high", cost_note="$0 (agy subscription)",
    ),
    Candidate(
        "claude_sonnet5", "cloud", "Claude Sonnet 5 via claude CLI",
        provider="claude:claude-sonnet-5", cost_note="$0 (claude subscription)",
    ),
    Candidate(
        "gpt_oss_20b", "ollama", "GPT-OSS 20B (optional, already pulled)",
        model_key="gpt_oss_20b", cost_note="$0 (local, installed)",
    ),
]


def candidate_by_name(name: str) -> Candidate:
    for c in CANDIDATES:
        if c.name == name:
            return c
    raise KeyError(f"unknown candidate: {name!r}")


@contextmanager
def candidate_context(candidate: Candidate, *, role: str = "resident"):
    """Route every ``classify_item``/``triage_item``/``analyze_item`` call made inside this
    ``with`` block to ``candidate``, then restore exactly what was changed.

    - ``kind="ollama"``: repoints ``settings().models[role]`` (an in-process singleton dict, never
      written to ``config/config.yaml`` on disk) at ``candidate.model_key`` so the stage
      functions' existing ``chat_structured(role, ...)`` calls resolve to that model via the
      normal ``gate().acquire(role)`` path -- unchanged prompts, unchanged schemas, unchanged
      corrective-retry logic.
    - ``kind="cloud"``: monkeypatches the ``chat_structured`` NAME each pipeline module bound at
      import time (``from eoa.llm.ollama_client import chat_structured``) to a thin wrapper that
      injects ``provider=candidate.provider`` whenever the caller didn't pass one explicitly --
      this also covers ``triage_item``'s internal score-reconciliation retry, since that calls
      the very same bound name.

    Never touches a config file, never sets ``EOA_PIPELINE``, never persists anything.
    """
    s = settings()
    prior_model = s.models.get(role)
    originals: dict[Any, Callable[..., Any]] = {m: m.chat_structured for m in _PIPELINE_MODULES}

    def _inject_provider(orig: Callable[..., Any]) -> Callable[..., Any]:
        def _wrapped(
            role_: str,
            schema: type[BaseModel],
            messages: list[dict[str, Any]],
            *,
            task: str = "classify",
            interactive: bool = False,
            options: dict[str, Any] | None = None,
            provider: str | None = None,
        ) -> Any:
            return orig(
                role_,
                schema,
                messages,
                task=task,
                interactive=interactive,
                options=options,
                provider=provider or candidate.provider,
            )

        return _wrapped

    if candidate.kind == "ollama":
        if not candidate.model_key:
            raise ValueError(f"ollama candidate {candidate.name!r} needs model_key")
    elif candidate.kind == "cloud":
        if not candidate.provider:
            raise ValueError(f"cloud candidate {candidate.name!r} needs provider")
    else:
        raise ValueError(f"unknown candidate kind: {candidate.kind!r}")

    try:
        if candidate.kind == "ollama":
            s.models[role] = candidate.model_key
        else:
            for m in _PIPELINE_MODULES:
                m.chat_structured = _inject_provider(originals[m])
        yield
    finally:
        if candidate.kind == "ollama":
            if prior_model is None:
                s.models.pop(role, None)
            else:
                s.models[role] = prior_model
        for m in _PIPELINE_MODULES:
            m.chat_structured = originals[m]


# ---------------------------------------------------------------------------------------------
# Per-item classify -> triage -> analyze run
# ---------------------------------------------------------------------------------------------


@dataclass
class StageResult:
    ok: bool
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: int = 0


@dataclass
class ItemRunResult:
    item_id: Any
    candidate: str
    classify: StageResult | None = None
    triage: StageResult | None = None
    analyze: StageResult | None = None
    merged_item: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "candidate": self.candidate,
            "classify": self.classify.__dict__ if self.classify else None,
            "triage": self.triage.__dict__ if self.triage else None,
            "analyze": self.analyze.__dict__ if self.analyze else None,
            "merged_item": self.merged_item,
        }


def _timed_call(fn: Callable[..., BaseModel], *args: Any, **kwargs: Any) -> tuple[StageResult, BaseModel | None]:
    t0 = time.monotonic()
    try:
        out = fn(*args, **kwargs)
        ms = int((time.monotonic() - t0) * 1000)
        return StageResult(ok=True, output=out.model_dump(mode="json"), latency_ms=ms), out
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        log.warning("bakeoff_stage_failed", error=str(exc)[:300], exc_info=False)
        return StageResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500], latency_ms=ms), None


def run_pipeline_for_item(
    item: dict[str, Any], candidate: Candidate, *, role: str = "resident"
) -> ItemRunResult:
    """Run classify -> triage -> analyze for ``item`` with ``candidate``, feeding each stage's
    output forward into the next exactly like the real pipeline does (classify's domain/entities
    feed triage's prompt; triage's level/score are available to analyze's context). Never
    persists. A stage failure does not prevent later stages from being attempted with whatever
    fields are available -- mirrors the real pipeline's "a failing item never stops the run"
    contract (docs/CONVENTIONS.md rule 9), and lets deterministic checks see the partial result.
    """
    merged: dict[str, Any] = dict(item)
    result = ItemRunResult(item_id=item.get("id"), candidate=candidate.name, merged_item=merged)

    with candidate_context(candidate, role=role):
        stage, out = _timed_call(classify_mod.classify_item, merged, role=role, interactive=True)
        result.classify = stage
        if isinstance(out, ClassifyOut):
            merged["domain"] = out.domain
            merged["subdomain"] = out.subdomain
            merged["report_kind"] = out.report_kind
            merged["trl"] = out.trl
            merged["geography"] = out.geography
            merged["entities_mentioned"] = [e.name for e in out.entities]

        stage, out = _timed_call(triage_mod.triage_item, merged, role=role, interactive=True)
        result.triage = stage
        if isinstance(out, TriageOut):
            merged["level"] = out.level
            merged["score"] = out.score
            merged["triage_reason"] = out.reason_he

        stage, out = _timed_call(analyze_mod.analyze_item, merged, role=role, interactive=True)
        result.analyze = stage
        if isinstance(out, AnalyzeOut):
            merged["summary_he"] = out.summary_he
            merged["so_what_he"] = out.so_what_he
            merged["key_facts"] = out.key_facts
            merged["uncertainty_he"] = out.uncertainty_he
            merged["tech_readiness_note_he"] = out.tech_readiness_note_he
            merged["events"] = [e.model_dump(mode="json") for e in out.events]

    return result


# ---------------------------------------------------------------------------------------------
# Chat ("ask the analyst")
# ---------------------------------------------------------------------------------------------


@dataclass
class ChatRunResult:
    ok: bool
    question: str
    candidate: str
    content: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    latency_ms: int = 0
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "question": self.question,
            "candidate": self.candidate,
            "content": self.content,
            "citations": self.citations,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "model": self.model,
        }


def run_chat_for_question(
    question: str,
    candidate: Candidate,
    *,
    role: str = "resident",
    retrieved: list[dict[str, Any]] | None = None,
) -> ChatRunResult:
    """Build the real RAG chat prompt (``ask_build_messages``, unchanged) and send it to
    ``candidate`` as one non-streamed ``chat()`` call. ``retrieved`` is a list of item-row-shaped
    dicts as ``eoa.api.services._ask_retrieve`` would return (empty when the DB/vector index is
    unavailable -- the system prompt already tells the model to say so explicitly rather than
    invent an answer, per ``docs/CONVENTIONS.md`` rule 5)."""
    from eoa.api.services import ask_build_messages

    messages, citations = ask_build_messages(question, None, retrieved or [])
    provider = candidate.provider if candidate.kind == "cloud" else None
    t0 = time.monotonic()
    try:
        with candidate_context(candidate, role=role):
            res: ChatResult = chat(role, messages, task="chat", interactive=True, provider=provider)
        return ChatRunResult(
            ok=True,
            question=question,
            candidate=candidate.name,
            content=res.content,
            citations=citations,
            latency_ms=int((time.monotonic() - t0) * 1000),
            model=res.model,
        )
    except Exception as exc:
        return ChatRunResult(
            ok=False,
            question=question,
            candidate=candidate.name,
            error=f"{type(exc).__name__}: {exc}"[:500],
            latency_ms=int((time.monotonic() - t0) * 1000),
        )


# ---------------------------------------------------------------------------------------------
# Deterministic checks (reused, not reimplemented -- see eoa.qa.d1_classify / d2_summary)
# ---------------------------------------------------------------------------------------------


def deterministic_checks_for_candidate(merged_items: list[dict[str, Any]]) -> dict[str, DomainScore]:
    """D1 + D2 deterministic scores for one candidate's merged item outputs, via the exact same
    scorers the live QA loop uses (``scripts/qa_score.py``) -- schema/taxonomy validity, Hebrew
    truncation, gershayim (ASCII-quote-between-Hebrew), key_facts duplicates, entities non-empty
    for D1; length/Hebrew-dominance/chatter/terminology for D2. No LLM call, no DB."""
    return {"D1": score_D1(merged_items), "D2": score_D2(merged_items)}


# ---------------------------------------------------------------------------------------------
# Anonymised blind-judge harness
# ---------------------------------------------------------------------------------------------


class JudgeItemScore(BaseModel):
    label: str = Field(description="the CANDIDATE_n label this score is for, verbatim")
    score_0_100: int = Field(ge=0, le=100)
    notes: str = Field(default="", description="one or two sentences of justification, in English")


class JudgeVerdict(BaseModel):
    scores: list[JudgeItemScore]


_JUDGE_RUBRIC_TEMPLATE = """You are a blind, independent grader for a defense-OSINT analyst \
pipeline's LLM outputs. You do NOT know which model produced which candidate answer below -- \
grade purely on merit, per the rubric.

Rubric (docs/QA_CONTINUOUS_LOOP.md sec 1, Q3), 0-100 per candidate:
- Faithfulness to the source text below (no invented facts, no numbers/dates not present in the source)
- Correctness of domain/subdomain/level per the taxonomy and triage rules implied by the source
- Analyst-grade inference in "so_what" (real implication, not a paraphrase of the summary)
- Hebrew quality (fluent, professional, terms kept in English in parentheses per convention)
- Entity/key-facts completeness relative to what the source actually states

=== SOURCE ITEM ===
{source_text}
=== END SOURCE ITEM ===

=== CANDIDATE OUTPUTS (order is randomised; grade independently) ===
{candidates_block}
=== END CANDIDATE OUTPUTS ===

Score EVERY label listed here: {labels}. Return ONLY the JSON schema requested -- one entry per \
label, no extra commentary outside the schema.
"""


def _source_text_for_item(item: dict[str, Any]) -> str:
    title = item.get("title") or ""
    text = (item.get("clean_text") or "")[:4000]
    return f"Title: {title}\n\n{text}"


def blind_judge_item(
    item: dict[str, Any],
    candidate_outputs: dict[str, dict[str, Any]],
    *,
    judge_provider: str = "claude:claude-sonnet-5",
    role: str = "report",
    rng: random.Random | None = None,
) -> dict[str, JudgeItemScore]:
    """Shuffle ``candidate_outputs`` (``{candidate_name: output_dict}``) behind anonymous
    ``CANDIDATE_n`` labels, ask ``judge_provider`` to score each against the rubric, then
    unshuffle so the caller gets real candidate names back. The judge never sees a candidate
    name or model id anywhere in its prompt."""
    rng = rng or random.Random()
    names = list(candidate_outputs.keys())
    shuffled = names[:]
    rng.shuffle(shuffled)
    label_of = {name: f"CANDIDATE_{i + 1}" for i, name in enumerate(shuffled)}
    reveal = {label: name for name, label in label_of.items()}

    blocks = []
    for name in shuffled:
        label = label_of[name]
        payload = json.dumps(candidate_outputs[name], ensure_ascii=False, indent=2)
        blocks.append(f"--- {label} ---\n{payload}")

    prompt = _JUDGE_RUBRIC_TEMPLATE.format(
        source_text=_source_text_for_item(item),
        candidates_block="\n\n".join(blocks),
        labels=", ".join(label_of[n] for n in shuffled),
    )
    messages = [{"role": "user", "content": prompt}]
    verdict = chat_structured(role, JudgeVerdict, messages, task="judge", provider=judge_provider)

    out: dict[str, JudgeItemScore] = {}
    for s in verdict.scores:
        name = reveal.get(s.label)
        if name is not None:
            out[name] = s
    return out
