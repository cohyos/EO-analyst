"""Source Security Gate (FR-9): sanitizer signals + heuristics (L1a) + CPU classifier (L1b) + LLM judge (L2).

``screen(text, title, ...)`` returns a ``ScreenResult``; callers quarantine flagged items and never pass
their raw text to a tool-enabled model. The L2 judge runs with no tools and sees the text only as DATA.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings
from eoa.security.heuristics import HeuristicResult, scan_heuristics

log = structlog.get_logger(__name__)

_L1_LOCK = threading.Lock()
_L1_PIPE: Any = None
_L1_FAILED = False


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
    """Lazy-load the CPU prompt-injection classifier (Protect AI DeBERTa v2). None if unavailable."""
    global _L1_PIPE, _L1_FAILED
    if _L1_PIPE is not None or _L1_FAILED:
        return _L1_PIPE
    with _L1_LOCK:
        if _L1_PIPE is not None or _L1_FAILED:
            return _L1_PIPE
        spec = settings().registry.models.get(settings().models.get("guard_l1") or "")
        if spec is None or not spec.hf:
            _L1_FAILED = True
            return None
        try:
            from transformers import pipeline  # type: ignore[import-not-found]

            _L1_PIPE = pipeline(
                "text-classification", model=spec.hf, truncation=True, max_length=512, device=-1
            )
            log.info("guard_l1_loaded", model=spec.hf)
        except Exception as exc:
            log.warning("guard_l1_unavailable", error=str(exc)[:200])
            _L1_FAILED = True
    return _L1_PIPE


def _l1_score(text: str) -> float | None:
    """Max injection probability over 512-token windows; None if the classifier is unavailable."""
    pipe = _l1_pipeline()
    if pipe is None:
        return None
    chunks = [text[i : i + 1800] for i in range(0, min(len(text), 30_000), 1500)] or [text]
    best = 0.0
    for out in pipe(chunks, batch_size=8):
        label = str(out.get("label", "")).upper()
        p = float(out.get("score", 0.0))
        best = max(best, p if label in {"INJECTION", "LABEL_1"} else 1.0 - p)
    return best


def _l2_judge(text: str, item_id: int | str, hits: list[str]) -> dict[str, Any] | None:
    """Isolated LLM judgment (no tools). Returns dict or None if the LLM is unavailable."""
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
    except Exception as exc:
        log.warning("guard_l2_failed", error=str(exc)[:200])
        return None


def screen(
    text: str,
    title: str = "",
    *,
    item_id: int | str = 0,
    sanitizer_flags: list[str] | None = None,
    hidden_text_ratio: float = 0.0,
    encoded_blobs: int = 0,
    use_l2: bool = True,
) -> ScreenResult:
    """Run the full gate on one piece of fetched content."""
    sec = settings().security
    flags = list(sanitizer_flags or [])
    if hidden_text_ratio >= sec.hidden_text_min_ratio:
        flags.append(f"hidden_text_ratio={hidden_text_ratio:.2f}")
    if encoded_blobs:
        flags.append(f"encoded_blobs={encoded_blobs}")

    heur = scan_heuristics(text, title)
    l1 = _l1_score(f"{title}\n{text}") if text else None

    suspicious = heur.score >= 0.5 or (l1 is not None and l1 >= 0.8) or bool(flags)
    if not suspicious:
        return ScreenResult("clean", max(heur.score, l1 or 0.0), "heuristic", heuristics=heur, l1_score=l1)

    # strong signals -> quarantine without asking the LLM
    if heur.score >= 0.8 or (l1 is not None and l1 >= 0.95):
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
