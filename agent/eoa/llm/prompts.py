"""Prompt templates stored as Markdown files in ``agent/eoa/llm/prompts/``.

A template may contain ``{placeholders}``; ``render`` fills them with ``str.format_map`` so that
stray braces in Hebrew text do not raise (missing keys are left verbatim).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Formatter

PROMPTS_DIR = Path(__file__).parent / "prompts"


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@lru_cache(maxsize=64)
def load(name: str) -> str:
    """Return the raw template text for ``name`` (without extension)."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def render(name: str, **values: object) -> str:
    """Load and fill a template."""
    return Formatter().vformat(load(name), (), _SafeDict(**{k: str(v) for k, v in values.items()}))
