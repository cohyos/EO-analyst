"""Hebrew text quality assessment: heuristics and optional LLM judge."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

# Hebrew character ranges
HEBREW_CHARS = r"[֐-׿]"


class HebrewMetrics(BaseModel):
    """Pure-Python heuristic metrics for Hebrew text."""

    hebrew_ratio: float = Field(ge=0, le=1, description="Fraction of text that is Hebrew letters")
    latin_terms_in_parens_ratio: float = Field(
        ge=0, le=1, description="Fraction of words in parentheses that are Latin"
    )
    avg_sentence_len: float = Field(description="Average words per sentence")
    telegraphic_flag: bool = Field(description="Text seems telegraphic (very short sentences)")


def score_hebrew(text: str) -> HebrewMetrics:
    """Score Hebrew text quality using heuristics."""
    if not text:
        return HebrewMetrics(
            hebrew_ratio=0,
            latin_terms_in_parens_ratio=0,
            avg_sentence_len=0,
            telegraphic_flag=False,
        )

    # Hebrew ratio
    hebrew_chars = len(re.findall(HEBREW_CHARS, text))
    total_alpha = len(re.findall(r"[a-zA-Z֐-׿]", text))
    hebrew_ratio = hebrew_chars / total_alpha if total_alpha > 0 else 0

    # Latin terms in parentheses
    parens_content = re.findall(r"\(([^)]+)\)", text)
    if parens_content:
        latin_in_parens = sum(1 for match in parens_content if re.search(r"[a-zA-Z]", match))
        latin_terms_in_parens_ratio = latin_in_parens / len(parens_content)
    else:
        latin_terms_in_parens_ratio = 0

    # Average sentence length (split on . ! ?)
    sentences = re.split(r"[.!?]", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if sentences:
        total_words = sum(len(s.split()) for s in sentences)
        avg_sentence_len = total_words / len(sentences)
    else:
        avg_sentence_len = 0

    # Telegraphic flag: very short sentences (< 5 words on average)
    telegraphic_flag = avg_sentence_len < 5 if avg_sentence_len > 0 else False

    return HebrewMetrics(
        hebrew_ratio=hebrew_ratio,
        latin_terms_in_parens_ratio=latin_terms_in_parens_ratio,
        avg_sentence_len=avg_sentence_len,
        telegraphic_flag=telegraphic_flag,
    )


class HebrewRubric(BaseModel):
    """LLM judgment of Hebrew text quality."""

    score: int = Field(ge=1, le=5, description="1=poor, 5=excellent")
    fluency: int = Field(ge=1, le=5, description="Sentence structure and flow")
    terminology: int = Field(ge=1, le=5, description="Technical term accuracy and usage")
    authenticity: int = Field(ge=1, le=5, description="Sounds like natural Hebrew, not machine")
    summary: str = Field(description="Brief explanation of score")


def judge_with_llm(text: str, rubric_schema: dict | None = None) -> HebrewRubric:
    """Judge Hebrew text quality using LLM (optional, requires Ollama)."""
    import structlog

    from eoa.llm.ollama_client import chat_structured

    log = structlog.get_logger(__name__)

    if rubric_schema is None:
        rubric_schema = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "fluency": {"type": "integer", "minimum": 1, "maximum": 5},
                "terminology": {"type": "integer", "minimum": 1, "maximum": 5},
                "authenticity": {"type": "integer", "minimum": 1, "maximum": 5},
                "summary": {"type": "string", "maxLength": 200},
            },
            "required": ["score", "fluency", "terminology", "authenticity", "summary"],
        }

    system_prompt = (
        "You are an expert Hebrew language analyst. Evaluate the provided Hebrew text for "
        "quality, fluency, authenticity, and technical accuracy. Be strict: "
        "machine-generated Hebrew often sounds stilted or uses awkward phrasing. "
        "Provide a holistic score and detailed feedback."
    )

    user_prompt = f"""Evaluate this Hebrew text:

{text}

Provide scores from 1–5 for:
- score: Overall quality
- fluency: Sentence structure and flow
- terminology: Technical term accuracy (if applicable)
- authenticity: Sounds like natural human Hebrew, not machine-generated
- summary: Brief explanation

Output JSON only."""

    try:
        result = chat_structured(
            "resident",
            HebrewRubric,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            task="judge_hebrew",
            interactive=False,
        )
        return result  # type: ignore
    except Exception as exc:
        log.warning("hebrew_judge_failed", error=str(exc)[:200])
        # Fallback: return neutral score
        return HebrewRubric(
            score=3,
            fluency=3,
            terminology=3,
            authenticity=3,
            summary="LLM judge unavailable, neutral score assigned",
        )


if __name__ == "__main__":
    # Test heuristics
    test_text = (
        "זהו טקסט לבדיקה בעברית טובה. "
        "המערכה משלבת חיישנים (sensors) מתקדמים עם אלגוריתמים (algorithms) חדישים. "
        "הביצועים כוללים שיעור דיוק גבוה. זה נראה טוב."
    )

    metrics = score_hebrew(test_text)
    print(f"Metrics: {metrics.model_dump_json(indent=2)}")
