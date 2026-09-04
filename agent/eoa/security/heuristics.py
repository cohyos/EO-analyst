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

    def find_hits(self, text: str) -> list[tuple[str, float]]:
        """
        Scan text for all patterns.
        Returns list of (pattern_id, weight) tuples.
        """
        hits = []

        # Instruction override
        if self.ignore_previous.search(text):
            hits.append(("instruction_override_ignore", 0.9))
        if self.disregard.search(text):
            hits.append(("instruction_override_disregard", 0.8))
        if self.override_system.search(text):
            hits.append(("instruction_override_bypass", 0.85))

        # Role-change
        if self.you_are_now.search(text):
            hits.append(("role_change_you_are", 0.85))
        if self.act_as.search(text):
            hits.append(("role_change_act_as", 0.8))

        # AI/LLM addressed
        if self.ai_addressed.search(text):
            hits.append(("ai_addressed", 0.75))
        if self.ai_instruction.search(text):
            hits.append(("ai_instruction", 0.8))

        # Fake system messages
        if self.fake_system_bracket.search(text):
            hits.append(("fake_system_bracket", 0.8))
        if self.fake_system_angle.search(text):
            hits.append(("fake_system_angle", 0.85))
        if self.fake_system_hash.search(text):
            hits.append(("fake_system_hash", 0.75))

        # Tool hijack
        if self.notify_tool.search(text):
            hits.append(("tool_notify", 0.9))
        if self.tool_actions.search(text):
            hits.append(("tool_actions", 0.8))
        if self.shell_commands.search(text):
            hits.append(("shell_commands", 0.85))

        # Exfiltration
        if self.send_to_url.search(text):
            hits.append(("exfil_url", 0.9))
        if self.exfil_placeholder.search(text):
            hits.append(("exfil_placeholder", 0.85))

        # Base64
        if self.base64_decode.search(text):
            hits.append(("base64_decode_request", 0.8))
        if self.base64_blob.search(text):
            hits.append(("base64_blob", 0.7))

        # Chat tokens
        if self.chat_tokens.search(text):
            hits.append(("chat_template_token", 0.75))

        # Prompt leak
        if self.repeat_prompt.search(text):
            hits.append(("prompt_leak_repeat", 0.85))
        if self.reveal_instructions.search(text):
            hits.append(("prompt_leak_reveal", 0.8))

        # Multilingual
        for lang, pattern in self.multilingual_ignore.items():
            if pattern.search(text):
                hits.append((f"multilingual_{lang}", 0.75))

        # Imperative second-person (count density)
        imperative_matches = self.imperative_you.findall(text)
        if len(imperative_matches) >= 3:
            hits.append(("imperative_density_high", min(0.75, 0.3 + len(imperative_matches) * 0.1)))

        # Subtle persuasion
        if self.compliance_pressure.search(text):
            hits.append(("subtle_compliance", 0.65))
        if self.rating_manipulation.search(text):
            hits.append(("subtle_rating", 0.7))

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

    # Build Hit objects with excerpts
    hits: list[Hit] = []
    seen_patterns = set()

    for pattern_id, weight in hits_raw:
        if pattern_id in seen_patterns:
            continue
        seen_patterns.add(pattern_id)

        # Find the corresponding pattern object
        pattern_obj = None
        for attr_name in dir(_PATTERNS):
            attr = getattr(_PATTERNS, attr_name)
            if isinstance(attr, re.Pattern) and attr.search(combined_text):
                pattern_obj = attr
                break

        excerpt = ""
        if pattern_obj:
            excerpt = _extract_excerpt(combined_text, pattern_obj)

        hits.append(Hit(pattern_id=pattern_id, excerpt=excerpt, weight=weight))

    # Calculate weighted score
    if hits:
        score = sum(h.weight for h in hits) / len(hits)
        score = min(1.0, score)  # Cap at 1.0
    else:
        score = 0.0

    return HeuristicResult(score=score, hits=hits, flagged=score >= 0.5)
