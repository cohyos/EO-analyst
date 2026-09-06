"""``PatentRecord``: the scan-time shape of one patent, before DB insertion (A14).

Mirrors ``eoa.tenders.scan.NoticeRaw``'s role -- a plain dataclass every scan source (EPO OPS,
PatentsView, the Google Patents search fallback) normalizes into, so the rest of the pipeline
(dedupe, analyze, valuation, insert) never needs to know which source produced a given record.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PatentRecord:
    pub_number: str
    kind: str | None = None
    title: str = ""
    abstract: str = ""
    assignees: list[str] = field(default_factory=list)
    inventors: list[str] = field(default_factory=list)
    cpc: list[str] = field(default_factory=list)
    priority_date: dt.date | None = None
    filing_date: dt.date | None = None
    publication_date: dt.date | None = None
    grant_date: dt.date | None = None
    family_id: str | None = None
    jurisdictions: list[str] = field(default_factory=list)
    forward_citations: int | None = None
    backward_citations: int | None = None
    url: str | None = None
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)
