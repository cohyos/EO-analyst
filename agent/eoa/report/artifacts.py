"""Immutable output names and the visibility rule for quarantined reports."""

from pathlib import Path
from uuid import uuid4

VISIBLE_REPORT_SQL = "COALESCE(qa_report->'archive'->>'reason', '') = ''"


def versioned_paths(docx: Path, md: Path, html: Path) -> tuple[Path, Path, Path]:
    """Give one build its own directory, shared by all three renderings."""
    version = uuid4().hex
    return tuple(p.parent / "versions" / version / p.name for p in (docx, md, html))
