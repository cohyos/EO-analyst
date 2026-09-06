"""Deterministic half of the QA continuous-loop score (docs/QA_CONTINUOUS_LOOP.md).

Ten domains, D1-D10, each scored 0-100 from a list of pass/fail :class:`~eoa.qa.types.Check`
objects with a weight and evidence string. This package owns only the deterministic scoring
logic and sampling -- it never mutates pipeline data and never talks to Ollama. See
``scripts/qa_score.py`` for the CLI that drives a round end to end.
"""

from __future__ import annotations
