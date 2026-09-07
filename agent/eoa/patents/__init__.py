"""A14: patent / IP landscape tracking for EO/IR & CV defense technology.

Sub-modules:

- ``models``: :class:`PatentRecord` -- the scan-time shape, before DB insertion.
- ``scan``: ``scan_patents`` -- per-watch-topic + per-assignee patent discovery (EPO OPS /
  USPTO ODP when configured, else a keyless Google Patents search fallback).
- ``analyze``: ``analyze_patents`` -- LLM claims summary / subdomain / so-what, plus the
  deterministic Israel-relevance signal.
- ``valuation``: ``score_patent`` -- a deterministic, documented value-score proxy (never a
  financial valuation -- see the module's own disclaimer text).
- ``survey``: ``build_patent_survey`` -- an on-demand "סקר פטנטים" (patent landscape survey) for a
  free-text topic, rendered via ``eoa.report.docx_builder`` and persisted as a ``reports`` row.
- ``report_section``: weekly/monthly/BD-territory report integration hooks, mirroring
  ``eoa.tenders.report_section``.
"""

from __future__ import annotations
