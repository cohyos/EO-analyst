"""FR-3.3 / FR-11 feedback loop: calibration, surveys, and the weekly meta-summary.

- `calibration.py` -- turns `triage_feedback` into `lessons(kind='calibration')` rows.
- `surveys.py` -- the FR-11 rotating Hebrew question bank and answer ingestion.
- `meta.py` -- FR-11.4's "what changed because of your feedback" weekly summary.
"""

from __future__ import annotations
