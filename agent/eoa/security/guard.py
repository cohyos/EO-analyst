"""Source Security Gate (FR-9): sanitizer signals + heuristics (L1a) + CPU classifier (L1b) + LLM judge (L2).

``screen(text, title, ...)`` returns a ``ScreenResult``; callers quarantine flagged items and never pass
their raw text to a tool-enabled model. The L2 judge runs with no tools and sees the text only as DATA.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import REPO_ROOT, settings
from eoa.security.heuristics import HeuristicResult, scan_heuristics

log = structlog.get_logger(__name__)

_L1_LOCK = threading.Lock()
_L1_PIPE: Any = None
_L1_FAILED = False

# Q2-6 (2026-09-06): cheap char-ratio Hebrew-dominance check, used to gate the
# guard's Hebrew-specific L1 false-positive mitigation below. A `langdetect`
# model call would work too, but this needs no dependency on the hot classify
# path and no risk of `langdetect`'s own failure modes (it raises on very
# short/ambiguous text) -- good enough for "is this mostly Hebrew prose".
_HEBREW_CHAR_RE = re.compile(r"[֐-׿]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_HEBREW_DOMINANT_THRESHOLD = 0.5


def _is_hebrew_dominant(text: str, threshold: float = _HEBREW_DOMINANT_THRESHOLD) -> bool:
    """True when Hebrew-script characters make up most of the letters in `text`."""
    letters = _LETTER_RE.findall(text)
    if not letters:
        return False
    hebrew = sum(1 for ch in letters if _HEBREW_CHAR_RE.match(ch))
    return (hebrew / len(letters)) >= threshold


@dataclass
class ScreenResult:
    verdict: str  # clean | flagged | quarantined
    score: float
    layer: str  # heuristic | l1 | l2 | sanitizer
    kind: str = "none"
    excerpt: str = ""
    heuristics: HeuristicResult | None = None
    l1_score: float | None = None
    l2: dict[str, Any] | None = None
    sanitizer_flags: list[str] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return self.verdict == "clean"


def _l1_pipeline() -> Any:
    """Lazy-load the CPU prompt-injection classifier (Protect AI DeBERTa v2). None if unavailable.

    Resolution order:
    1. ``EOA_GUARD_L1_DIR`` env var — a model baked into the image at build time
       (ONNX weights + tokenizer, loaded via ``optimum``'s ``ORTModelForSequenceClassification``).
       This is the path used inside the `agent` container, which has no internet
       access at runtime (see ``docker/agent/Dockerfile``); ``HF_HUB_OFFLINE=1`` is
       set there too, so no step here can silently fall back to a network call.
    2. The HF model id from the registry (``config/models.yaml`` -> ``guard_l1``),
       downloaded live via ``transformers.pipeline`` — only reachable on dev
       machines with internet.
    3. ``None`` — the existing graceful fallback; ``screen()`` still runs on
       heuristics alone.
    """
    global _L1_PIPE, _L1_FAILED
    if _L1_PIPE is not None or _L1_FAILED:
        return _L1_PIPE
    with _L1_LOCK:
        if _L1_PIPE is not None or _L1_FAILED:
            return _L1_PIPE

        local_dir = os.environ.get("EOA_GUARD_L1_DIR")
        if not local_dir:
            # Native/Windows install (docs/adr/004-windows-native.md): scripts/native/install_native.ps1
            # downloads the same ONNX weights docker/agent/Dockerfile bakes into the image, into
            # <repo>/runtime/models/prompt-guard, and scripts/native/eoa-supervisor.ps1 sets
            # EOA_GUARD_L1_DIR from runtime/eoa.env before launching the orchestrator/api -- but a
            # bare `eo` invocation (or a shell that hasn't sourced runtime/eoa.env) would otherwise
            # silently fall through heuristics-only. Fall back to that default location when it
            # actually exists, so the guard model still loads without the env var explicitly set.
            default_dir = REPO_ROOT / "runtime" / "models" / "prompt-guard"
            if default_dir.is_dir():
                local_dir = str(default_dir)
                log.info("guard_l1_using_default_dir", dir=local_dir)
        if local_dir:
            try:
                from optimum.onnxruntime import (  # type: ignore[import-not-found]
                    ORTModelForSequenceClassification,
                )
                from transformers import AutoTokenizer, pipeline  # type: ignore[import-not-found]

                model = ORTModelForSequenceClassification.from_pretrained(local_dir)
                tokenizer = AutoTokenizer.from_pretrained(local_dir)
                _L1_PIPE = pipeline(
                    "text-classification",
                    model=model,
                    tokenizer=tokenizer,
                    truncation=True,
                    max_length=512,
                )
                log.info("guard_l1_loaded", model=local_dir, runtime="onnx")
                return _L1_PIPE
            except Exception as exc:
                log.warning("guard_l1_local_load_failed", dir=local_dir, error=str(exc)[:200])
                if os.environ.get("HF_HUB_OFFLINE") == "1":
                    # Offline-forced (the agent container): no point trying the
                    # network path below, it will only fail the same way.
                    _L1_FAILED = True
                    return None

        spec = settings().registry.models.get(settings().models.get("guard_l1") or "")
        if spec is None or not spec.hf:
            _L1_FAILED = True
            return None
        # In the `agent` role, never let this fall through to a network
        # fetch: `local_files_only=True` makes `transformers` raise instead
        # of reaching out to the HF hub, regardless of `HF_HUB_OFFLINE`
        # (finding #24 in output/reviews/codex_security_review.md).
        agent_role = os.environ.get("EOA_ROLE") == "agent"
        try:
            from transformers import pipeline  # type: ignore[import-not-found]

            _L1_PIPE = pipeline(
                "text-classification",
                model=spec.hf,
                truncation=True,
                max_length=512,
                device=-1,
                local_files_only=agent_role,
            )
            log.info("guard_l1_loaded", model=spec.hf, local_files_only=agent_role)
        except Exception as exc:
            log.warning("guard_l1_unavailable", error=str(exc)[:200], agent_role=agent_role)
            _L1_FAILED = True
    return _L1_PIPE


_INJECTION_LABEL_MARKERS = ("INJECTION", "JAILBREAK", "UNSAFE")

_L1_WINDOW_CHARS = 1800
_L1_WINDOW_STEP = 1500
_L1_MAX_WINDOWS = 40


def _l1_injection_label(pipe: Any) -> str | None:
    """Resolve the output label name that means "injection" for the *loaded model's own* ``id2label``.

    Never guesses a generic ``LABEL_1``/``LABEL_0`` convention: a checkpoint
    with the reverse mapping would silently invert every score and mark
    attacks safe (finding #22 in output/reviews/codex_security_review.md).
    If the label can't be resolved from the model's own config, the
    classifier is treated as unavailable (``None``) rather than guessed.
    """
    try:
        id2label = pipe.model.config.id2label
    except Exception as exc:
        log.error("guard_l1_id2label_unavailable", error=str(exc)[:200])
        return None
    candidates = [str(name) for name in id2label.values()]
    for name in candidates:
        upper = name.upper()
        if any(marker in upper for marker in _INJECTION_LABEL_MARKERS):
            return name
    log.error("guard_l1_injection_label_unresolvable", labels=candidates)
    return None


def _l1_windows(text: str) -> list[str]:
    """Bounded windows spanning the ENTIRE text, including the tail.

    Previously only the first 30,000 characters were scanned, so an
    obfuscated payload placed later in a long document never reached the
    classifier (finding #24). To keep worst-case latency bounded on a very
    long document, at most `_L1_MAX_WINDOWS` windows are kept, sampled
    evenly across the full span -- rather than truncating -- so coverage
    stays spread out and the final window (the tail) is always included.
    """
    if not text:
        return [text]
    length = len(text)
    if length <= _L1_WINDOW_CHARS:
        return [text]

    last_start = length - _L1_WINDOW_CHARS
    starts = list(range(0, last_start + 1, _L1_WINDOW_STEP))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)

    if len(starts) > _L1_MAX_WINDOWS:
        idxs = sorted({round(i * (len(starts) - 1) / (_L1_MAX_WINDOWS - 1)) for i in range(_L1_MAX_WINDOWS)})
        starts = [starts[i] for i in idxs]

    return [text[s : s + _L1_WINDOW_CHARS] for s in starts]


def _l1_call_all_scores(pipe: Any, chunks: list[str]) -> list[Any]:
    """Request every class probability per chunk (finding #22: previously only the top label came back)."""
    try:
        return pipe(chunks, batch_size=8, top_k=None)
    except TypeError:
        # Older `transformers` pipeline versions use `return_all_scores`
        # instead of `top_k=None` for the same "all classes" behavior.
        return pipe(chunks, batch_size=8, return_all_scores=True)


def _l1_score(text: str) -> float | None:
    """Max injection-class probability over bounded windows spanning the whole text; None if unavailable."""
    pipe = _l1_pipeline()
    if pipe is None:
        return None
    injection_label = _l1_injection_label(pipe)
    if injection_label is None:
        return None

    chunks = _l1_windows(text)
    raw = _l1_call_all_scores(pipe, chunks)

    best = 0.0
    for per_chunk_scores in raw:
        scores = per_chunk_scores if isinstance(per_chunk_scores, list) else [per_chunk_scores]
        for entry in scores:
            if str(entry.get("label")) == injection_label:
                best = max(best, float(entry.get("score", 0.0)))
                break
    return best


def _l2_judge(text: str, item_id: int | str, hits: list[str]) -> dict[str, Any] | None:
    """Isolated LLM judgment (no tools). Returns dict or None if the LLM is unavailable."""
    from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable

    try:
        from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
        from eoa.llm.prompts import render
        from eoa.llm.schemas.analysis import GuardVerdict

        role = "guard_l2" if settings().has_model("guard_l2") else "light"
        prompt = render(
            "guard_l2", hits="; ".join(hits)[:800], data=wrap_data(text[:12_000], item_id, "guard")
        )
        verdict = chat_structured(
            role,
            GuardVerdict,
            [
                {"role": "system", "content": DATA_GUARD_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            task="classify",
        )
        return verdict.model_dump()
    except (DeadlineExceeded, LeaseLost):
        raise
    except ResourceUnavailable as exc:
        log.info("guard_l2_deferred", item_id=item_id, reason=str(exc)[:200])
        return None
    except Exception as exc:
        log.warning("guard_l2_failed", error=str(exc)[:200])
        return None


def screen(
    text: str,
    title: str = "",
    *,
    detect_text: str = "",
    item_id: int | str = 0,
    sanitizer_flags: list[str] | None = None,
    hidden_text_ratio: float = 0.0,
    encoded_blobs: int = 0,
    use_l2: bool = True,
) -> ScreenResult:
    """Run the full gate on one piece of fetched content.

    ``detect_text`` is the optional confusable-mapped/zero-width-stripped
    detection copy `eoa.fetch.sanitize.extract_clean_text` builds alongside
    the readable ``text`` (``CleanText.detect_text``): a homoglyph swap
    (e.g. Cyrillic 'а' for Latin 'a') that would let an instruction evade
    the regex heuristics on the display text is normalized back to plain
    Latin here. Heuristics are run on both and the higher-scoring result is
    kept (finding #21 in output/reviews/codex_security_review.md).
    """
    sec = settings().security
    flags = list(sanitizer_flags or [])
    if hidden_text_ratio >= sec.hidden_text_min_ratio:
        flags.append(f"hidden_text_ratio={hidden_text_ratio:.2f}")
    if encoded_blobs:
        flags.append(f"encoded_blobs={encoded_blobs}")

    heur = scan_heuristics(text, title)
    if detect_text and detect_text != text:
        heur_detect = scan_heuristics(detect_text, title)
        if heur_detect.score > heur.score:
            heur = heur_detect
    l1 = _l1_score(f"{title}\n{text}") if text else None

    suspicious = heur.score >= 0.5 or (l1 is not None and l1 >= 0.8) or bool(flags)
    if not suspicious:
        return ScreenResult("clean", max(heur.score, l1 or 0.0), "heuristic", heuristics=heur, l1_score=l1)

    # Q2-6: the L1 classifier has measured false positives on benign,
    # predominantly-Hebrew defense/exercise prose (0.96-0.98 observed on
    # conference-announcement-style paragraphs) with essentially no
    # heuristic support at all. An L1-only verdict on such text must never
    # resolve to "quarantined" or "flagged" by itself -- it always falls
    # through to the L2 judge for confirmation instead.
    hebrew_only_l1_signal = (
        bool(text)
        and heur.score < 0.2
        and l1 is not None
        and l1 >= 0.8
        and _is_hebrew_dominant(f"{title}\n{text}")
    )
    if hebrew_only_l1_signal:
        log.info(
            "guard_hebrew_l1_requires_l2",
            item_id=item_id,
            heur_score=heur.score,
            l1_score=l1,
        )

    # strong signals -> quarantine without asking the LLM
    if not hebrew_only_l1_signal and (heur.score >= 0.8 or (l1 is not None and l1 >= 0.95)):
        excerpt = heur.hits[0].excerpt if heur.hits else ""
        return ScreenResult(
            "quarantined",
            max(heur.score, l1 or 0.0),
            "l1" if (l1 or 0) >= 0.95 else "heuristic",
            kind="instruction_override",
            excerpt=excerpt,
            heuristics=heur,
            l1_score=l1,
            sanitizer_flags=flags,
        )

    l2 = _l2_judge(text, item_id, [h.pattern_id for h in heur.hits] + flags) if use_l2 else None
    if l2 is None:
        if hebrew_only_l1_signal:
            # L2 is unreachable (LLM down) -- per Q2-6, an L1-only Hebrew
            # signal must not resolve to "flagged" on its own either, since
            # that reproduces the same unconfirmed false positive. Log it
            # and let the item through as clean rather than surface a
            # verdict we have no way to have actually confirmed.
            log.warning("guard_hebrew_l2_unavailable_no_flag", item_id=item_id, l1_score=l1)
            return ScreenResult(
                "clean",
                max(heur.score, l1 or 0.0),
                "l1",
                kind="hebrew_suspect_l2_unavailable",
                heuristics=heur,
                l1_score=l1,
                sanitizer_flags=flags,
            )
        # cannot adjudicate -> be conservative: flag (kept out of tool-enabled contexts, reviewable in UI)
        return ScreenResult(
            "flagged",
            max(heur.score, l1 or 0.0),
            "heuristic",
            kind="other",
            excerpt=heur.hits[0].excerpt if heur.hits else "",
            heuristics=heur,
            l1_score=l1,
            sanitizer_flags=flags,
        )
    if l2.get("injection") and float(l2.get("confidence", 0)) >= 0.6:
        return ScreenResult(
            "quarantined",
            float(l2["confidence"]),
            "l2",
            kind=str(l2.get("kind", "other")),
            excerpt=str(l2.get("excerpt", ""))[:300],
            heuristics=heur,
            l1_score=l1,
            l2=l2,
            sanitizer_flags=flags,
        )
    return ScreenResult(
        "clean",
        float(l2.get("confidence", 0.0)),
        "l2",
        heuristics=heur,
        l1_score=l1,
        l2=l2,
        sanitizer_flags=flags,
    )


def screen_and_record(item: dict[str, Any], **kw: Any) -> ScreenResult:
    """Screen an ``items`` row, persist security_log + item status, return the result."""
    from eoa.memory.relational import log_security, update_item_fields

    kw.setdefault("detect_text", item.get("detect_text") or "")
    res = screen(item.get("clean_text") or "", item.get("title") or "", item_id=item["id"], **kw)
    if res.verdict != "clean":
        log_security(
            item_id=item["id"],
            source_id=item.get("source_id"),
            layer=res.layer,
            verdict=res.kind,
            score=res.score,
            excerpt=res.excerpt,
            action="quarantined" if res.verdict == "quarantined" else "flagged",
        )
        update_item_fields(item["id"], security_status=res.verdict)
        _maybe_blocklist(item.get("source_id"))
    return res


def _maybe_blocklist(source_id: int | None) -> None:
    if source_id is None:
        return
    try:
        from eoa.db import connection

        n = settings().security.blocklist_after_incidents
        with connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS c FROM security_log WHERE source_id=%s AND action='quarantined' "
                "AND created_at > now() - interval '30 days'",
                (source_id,),
            ).fetchone()
            if row and row["c"] >= n:
                conn.execute("UPDATE sources SET blocklisted=true WHERE id=%s", (source_id,))
                log.warning("source_blocklisted", source_id=source_id, incidents=row["c"])
    except Exception as exc:
        log.debug("blocklist_check_failed", error=str(exc))
