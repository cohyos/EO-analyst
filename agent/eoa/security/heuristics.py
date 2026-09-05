"""Heuristic detector for prompt-injection attacks in fetched content."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Hit:
    """A detected injection pattern."""

    pattern_id: str
    excerpt: str
    weight: float


@dataclass
class HeuristicResult:
    """Result of heuristic injection scan."""

    score: float  # 0.0-1.0
    hits: list[Hit]
    flagged: bool  # True if score >= 0.5


class _PatternSet:
    """Precompiled regex patterns for injection detection."""

    def __init__(self):
        # Instruction override patterns
        self.ignore_previous = re.compile(
            r"ignore\s+(?:all\s+)?(?:(?:previous|prior|earlier)\s+)?instructions",
            re.IGNORECASE | re.UNICODE,
        )
        self.disregard = re.compile(
            r"disregard|forget|dismiss|overrid[e]?\s+(?:the\s+)?(?:above|previous)",
            re.IGNORECASE | re.UNICODE,
        )
        self.override_system = re.compile(
            r"override|bypass|circumvent|disable\s+(?:all\s+)?(?:security|restrictions|protections|safeguards)",
            re.IGNORECASE | re.UNICODE,
        )

        # Role-change patterns
        self.you_are_now = re.compile(
            r"you\s+are\s+now\s+(?:a|an)\s+\w+",
            re.IGNORECASE | re.UNICODE,
        )
        self.act_as = re.compile(
            r"act\s+as|pretend\s+to\s+be|role\s*play\s*ing?\s+as",
            re.IGNORECASE | re.UNICODE,
        )

        # Addressing AI/LLM/analyst
        self.ai_addressed = re.compile(
            r"(?:dear|hi|hello)\s+(?:ai|assistant|llm|analyst|agent|model)",
            re.IGNORECASE | re.UNICODE,
        )
        self.ai_instruction = re.compile(
            r"(?:ai|assistant|llm|analyst|agent|model)\s*[:–-]?\s*(?:execute|perform|run|do|process)",
            re.IGNORECASE | re.UNICODE,
        )

        # Fake system messages and chat tokens
        self.fake_system_bracket = re.compile(
            r"\[SYSTEM\]|\[system\]|\[INST\]|\[/INST\]",
            re.UNICODE,
        )
        self.fake_system_angle = re.compile(
            r"<\|im_start\|>system|<\|im_end\|>|<\|im_start\|>",
            re.UNICODE,
        )
        self.fake_system_hash = re.compile(
            r"###\s*(?:system|instruction|directive|admin|developer)",
            re.IGNORECASE | re.UNICODE,
        )

        # Tool hijack patterns
        self.notify_tool = re.compile(
            r"(?:call|invoke|execute|run|use)\s+(?:the\s+)?notify\s+tool",
            re.IGNORECASE | re.UNICODE,
        )
        self.tool_actions = re.compile(
            r"(?:execute|run|call|invoke)\s+(?:database|sql|delete|send|query|curl|http|command|script)",
            re.IGNORECASE | re.UNICODE,
        )
        self.shell_commands = re.compile(
            r"curl\s+(?:http|https)://|(?:python|bash|sh|cmd)\s+-[a-z]",
            re.IGNORECASE | re.UNICODE,
        )

        # Exfiltration patterns
        self.send_to_url = re.compile(
            r"send\s+(?:to|data|everything|all)\s+(?:to\s+)?(?:http|https)://\S+|http[s]?://\S+/(?:ingest|exfil|extract|webhook|callback)",
            re.IGNORECASE | re.UNICODE,
        )
        self.exfil_placeholder = re.compile(
            r"(?:send|export|transmit|upload|forward|leak)\s+(?:to|on|via|through)\s+http[s]?://\S+\?(?:\w+=\w+&)*(?:data|auth|token|key)=",
            re.IGNORECASE | re.UNICODE,
        )

        # Base64 decode patterns
        self.base64_decode = re.compile(
            r"(?:decode|execute|run|process)\s+(?:this\s+)?(?:base64|encoded|b64)\s+(?:instruction|command|payload|message)",
            re.IGNORECASE | re.UNICODE,
        )
        self.base64_blob = re.compile(
            r"(?:hint|instruction|payload|command)\s*:\s*[A-Za-z0-9+/]{40,}={0,2}(?:\s|$)",
            re.UNICODE,
        )

        # Chat template tokens
        self.chat_tokens = re.compile(
            r"<\|im_start\|>|<\|im_end\|>|<\|system\|>|<\|user\|>|<\|assistant\|>|\[INST\]|\[/INST\]|<s>|</s>",
            re.UNICODE,
        )

        # Prompt leak patterns
        self.repeat_prompt = re.compile(
            r"repeat\s+(?:your\s+)?(?:system\s+)?prompt|output\s+your\s+(?:instructions|guidelines|rules)",
            re.IGNORECASE | re.UNICODE,
        )
        self.reveal_instructions = re.compile(
            r"(?:reveal|disclose|show|print|provide)\s+(?:all\s+)?(?:your\s+)?(?:hidden\s+)?(?:instructions|system\s+prompt|security|guidelines|constraints|rules|original\s+instructions)",
            re.IGNORECASE | re.UNICODE,
        )

        # Multilingual instruction-override keywords
        # Note: Only trigger on actual non-English text, not English words containing similar sequences
        self.multilingual_ignore = {
            "he": re.compile(
                r"זנח|התעלם|שכח|עקוף",
                re.IGNORECASE | re.UNICODE,
            ),
            "ru": re.compile(
                r"игнор|игнораций|обход|отключ",
                re.IGNORECASE | re.UNICODE,
            ),
            "zh": re.compile(
                r"忽略|忽视|绕过|禁用",
                re.UNICODE,
            ),
            # More specific French: require accent marks or full words like "ignorez", "contournez"
            "fr": re.compile(
                r"contourner|désactiv|ignorez|contournez",
                re.IGNORECASE | re.UNICODE,
            ),
            # More specific German: require umlaut or full words like "ignorieren", "umgehen"
            "de": re.compile(
                r"ignorieren|umgeh|deaktiv|deaktivieren",
                re.IGNORECASE | re.UNICODE,
            ),
            "ar": re.compile(
                r"تجاهل|تخطي|تعطيل|حظر",
                re.UNICODE,
            ),
        }

        # Imperative second-person patterns (unusual density)
        self.imperative_you = re.compile(
            r"\b(?:you\s+(?:must|should|will|are|do|execute|send|provide|extract)|provide\s+me\s+(?:with|all)|give\s+me|send\s+(?:me|data|everything))",
            re.IGNORECASE | re.UNICODE,
        )

        # Subtle persuasion patterns
        self.compliance_pressure = re.compile(
            r"(?:for\s+)?(?:compliance|audit|regulatory|legal|governance)\s+(?:reasons?|requirements?|purposes?)",
            re.IGNORECASE | re.UNICODE,
        )
        self.rating_manipulation = re.compile(
            r"rate\s+(?:this\s+)?(?:item|entry|finding)\s+(?:10|critical|highest|maximum|priority)",
            re.IGNORECASE | re.UNICODE,
        )

        # Embedded tool-call/function-call JSON spoofing (Q2-5, 2026-09-06): fetched
        # content that carries a fake tool invocation as JSON, hoping a downstream
        # step will parse and act on it rather than treat it as inert DATA. Matches
        # the literal JSON key shape (`"tool":`, `"tool_call":`, `"function_call":`)
        # rather than the bare English words, so normal prose mentioning "the tool"
        # or "a function call" doesn't trip this.
        self.json_tool_call_key = re.compile(
            r'"(?:tool|tool_call|function_call)"\s*:',
            re.IGNORECASE | re.UNICODE,
        )
        self.json_tool_name_action = re.compile(
            r'"name"\s*:\s*"(?:read|fetch|search)"',
            re.IGNORECASE | re.UNICODE,
        )
        self.json_tool_call_args = re.compile(
            r'"(?:url|arguments)"\s*:',
            re.IGNORECASE | re.UNICODE,
        )
        self.file_uri_scheme = re.compile(r"file://", re.IGNORECASE | re.UNICODE)
        self.link_local_metadata_ip = re.compile(r"\b169\.254\.\d{1,3}\.\d{1,3}\b", re.UNICODE)
        self.json_localhost_reference = re.compile(
            r'"[^"\n]*(?:localhost|127\.0\.0\.1)[^"\n]*"',
            re.IGNORECASE | re.UNICODE,
        )

    def find_hits(self, text: str) -> list[tuple[str, float, re.Pattern[str]]]:
        """
        Scan text for all patterns.

        Returns a list of ``(pattern_id, weight, pattern)`` tuples -- the
        pattern is the actual compiled regex that matched, so callers can
        build an excerpt from the right match instead of guessing (finding
        #23 in ``output/reviews/codex_security_review.md``: excerpts used to
        come from whichever pattern happened to match first in attribute
        iteration order, not the one that produced the hit).
        """
        hits: list[tuple[str, float, re.Pattern[str]]] = []

        def add(pattern_id: str, weight: float, pattern: re.Pattern[str]) -> None:
            hits.append((pattern_id, weight, pattern))

        # Instruction override
        if self.ignore_previous.search(text):
            add("instruction_override_ignore", 0.9, self.ignore_previous)
        if self.disregard.search(text):
            add("instruction_override_disregard", 0.8, self.disregard)
        if self.override_system.search(text):
            add("instruction_override_bypass", 0.85, self.override_system)

        # Role-change
        if self.you_are_now.search(text):
            add("role_change_you_are", 0.85, self.you_are_now)
        if self.act_as.search(text):
            add("role_change_act_as", 0.8, self.act_as)

        # AI/LLM addressed
        if self.ai_addressed.search(text):
            add("ai_addressed", 0.75, self.ai_addressed)
        if self.ai_instruction.search(text):
            add("ai_instruction", 0.8, self.ai_instruction)

        # Fake system messages
        if self.fake_system_bracket.search(text):
            add("fake_system_bracket", 0.8, self.fake_system_bracket)
        if self.fake_system_angle.search(text):
            add("fake_system_angle", 0.85, self.fake_system_angle)
        if self.fake_system_hash.search(text):
            add("fake_system_hash", 0.75, self.fake_system_hash)

        # Tool hijack
        if self.notify_tool.search(text):
            add("tool_notify", 0.9, self.notify_tool)
        if self.tool_actions.search(text):
            add("tool_actions", 0.8, self.tool_actions)
        if self.shell_commands.search(text):
            add("shell_commands", 0.85, self.shell_commands)

        # Exfiltration
        if self.send_to_url.search(text):
            add("exfil_url", 0.9, self.send_to_url)
        if self.exfil_placeholder.search(text):
            add("exfil_placeholder", 0.85, self.exfil_placeholder)

        # Base64
        if self.base64_decode.search(text):
            add("base64_decode_request", 0.8, self.base64_decode)
        if self.base64_blob.search(text):
            add("base64_blob", 0.7, self.base64_blob)

        # Chat tokens
        if self.chat_tokens.search(text):
            add("chat_template_token", 0.75, self.chat_tokens)

        # Prompt leak
        if self.repeat_prompt.search(text):
            add("prompt_leak_repeat", 0.85, self.repeat_prompt)
        if self.reveal_instructions.search(text):
            add("prompt_leak_reveal", 0.8, self.reveal_instructions)

        # Multilingual
        for lang, pattern in self.multilingual_ignore.items():
            if pattern.search(text):
                add(f"multilingual_{lang}", 0.75, pattern)

        # Imperative second-person (count density)
        imperative_matches = self.imperative_you.findall(text)
        if len(imperative_matches) >= 3:
            add(
                "imperative_density_high",
                min(0.75, 0.3 + len(imperative_matches) * 0.1),
                self.imperative_you,
            )

        # Subtle persuasion
        if self.compliance_pressure.search(text):
            add("subtle_compliance", 0.65, self.compliance_pressure)
        if self.rating_manipulation.search(text):
            add("subtle_rating", 0.7, self.rating_manipulation)

        # Embedded tool-call/function-call JSON spoofing (Q2-5)
        if self.json_tool_call_key.search(text):
            add("json_tool_call_key", 0.85, self.json_tool_call_key)
        if self.json_tool_name_action.search(text) and self.json_tool_call_args.search(text):
            add("json_tool_name_with_args", 0.85, self.json_tool_name_action)
        if self.file_uri_scheme.search(text):
            add("file_uri_scheme", 0.6, self.file_uri_scheme)
        if self.link_local_metadata_ip.search(text):
            add("link_local_metadata_ip", 0.65, self.link_local_metadata_ip)
        if self.json_localhost_reference.search(text):
            add("json_localhost_reference", 0.5, self.json_localhost_reference)

        return hits


# Global compiled patterns
_PATTERNS = _PatternSet()


def _extract_excerpt(text: str, pattern: re.Pattern, max_len: int = 160) -> str:
    """Extract a centered excerpt around a pattern match."""
    match = pattern.search(text)
    if not match:
        return text[:max_len]

    start, end = match.span()
    left_pad = max(0, start - max_len // 2)
    right_pad = min(len(text), end + max_len // 2)

    excerpt = text[left_pad:right_pad].strip()
    if len(excerpt) > max_len:
        excerpt = excerpt[:max_len] + "..."
    return excerpt


def _combine_scores(weights: list[float]) -> float:
    """Monotonic noisy-OR combination of hit weights: ``1 - prod(1 - w)``.

    Replaces the previous plain average (finding #23): under averaging,
    piling on extra low-weight hits could pull a strong hit's score back
    *down* below the 0.5 quarantine threshold -- e.g. one 0.9 hit plus ten
    0.1 hits used to average to ~0.27, well under threshold, even though
    the 0.9 signal alone already warranted action. Noisy-OR is monotonic
    in every weight: adding any additional hit can only raise (or, at
    weight 0, leave unchanged) the combined score, never lower it.
    """
    if not weights:
        return 0.0
    survival = 1.0
    for w in weights:
        survival *= 1.0 - min(max(w, 0.0), 1.0)
    return min(1.0, 1.0 - survival)


def scan_heuristics(text: str, title: str = "") -> HeuristicResult:
    """
    Scan text and title for prompt-injection attack patterns.

    Uses weighted heuristic patterns covering:
    - Instruction overrides (ignore, disregard, override)
    - Role-change attempts
    - AI/LLM addressing
    - Fake system messages and chat tokens
    - Tool/command hijacking
    - Data exfiltration URLs
    - Base64-encoded payloads
    - Prompt leaking requests
    - Multilingual injections (Hebrew, Russian, Chinese, French, German, Arabic)
    - Subtle persuasion and compliance pressure
    - Imperative density analysis

    Note: This heuristic layer does NOT detect HTML-hidden, zero-width unicode,
    or base64-blob vectors—those require sanitizer/parser-level detection.

    Args:
        text: Article/content body text
        title: Optional article title (also scanned)

    Returns:
        HeuristicResult with:
        - score: float 0.0-1.0 (capped)
        - hits: list of detected patterns with excerpts and weights
        - flagged: bool (True if score >= 0.5)
    """
    combined_text = f"{title}\n{text}" if title else text

    hits_raw = _PATTERNS.find_hits(combined_text)

    # Build Hit objects with excerpts, each taken from the regex that
    # actually produced that hit (see `find_hits`'s docstring / finding #23).
    hits: list[Hit] = []
    seen_patterns = set()

    for pattern_id, weight, pattern_obj in hits_raw:
        if pattern_id in seen_patterns:
            continue
        seen_patterns.add(pattern_id)

        excerpt = _extract_excerpt(combined_text, pattern_obj)
        hits.append(Hit(pattern_id=pattern_id, excerpt=excerpt, weight=weight))

    score = _combine_scores([h.weight for h in hits])

    return HeuristicResult(score=score, hits=hits, flagged=score >= 0.5)
