"""R7-tenders repair: docs/qa/loop/round_6_judge.md D9 finding 2 (candidate id 34, "Expert / Coach
Transformatie en Contracten Juridisch", Gemeente Rotterdam -- a Dutch legal/HR consulting contract
that cleared the old keyword gate on a fluke substring match: the EO/IR domain keyword "ATR"
matched inside the unrelated Dutch word "privaatrechtelijke").

``eoa.tenders.scan`` gained two deterministic pre-filter fixes this round (see that module's
``_term_present``/``_cpv_gate_reject_reason``): word-boundary matching for short (<=4 char)
keywords, and a CPV-code-family pre-filter. Both apply automatically to every *future* scan; this
script is the one-time (or as-needed) repair pass over rows already stored under the old, looser
gate -- reusing ``eoa.tenders.scan.find_prefilter_violations``/``repair_relevance_prefilter``
directly so this script and the live gate can never drift apart.

Scoped entirely to ``intake='candidate'`` rows (an ``'accepted'`` row already carries operator
trust and is never touched here -- see ``find_prefilter_violations``'s own docstring); never writes
to ``tender_feedback``.

Defaults to a dry run (report only, no writes). Pass ``--apply`` to actually archive the violating
rows (``status='archived'`` -- F1: no fetched-content row is ever deleted outright).

Run with the same DATABASE_URL as the app, e.g.:

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round7_tenders.py            # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round7_tenders.py --apply     # write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows consoles default stdout to the cp1252 codepage, which cannot encode non-ASCII title text
# this script may print -- reconfigure to UTF-8 (same fix as scripts/repair_tenders.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/repair_round7_tenders.py` without pre-setting PYTHONPATH=agent
# (mirrors scripts/repair_tenders.py's own convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa.tenders.scan import find_prefilter_violations, repair_relevance_prefilter  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="archive the violating rows (default: dry run)")
    args = ap.parse_args()

    if args.apply:
        violations = repair_relevance_prefilter(apply=True)
        mode = "apply"
    else:
        violations = find_prefilter_violations()
        mode = "dry_run"

    report = {
        "mode": mode,
        "violations_found": len(violations),
        "violations": [
            {"id": v.id, "source": v.source, "title": v.title, "reason": v.reason} for v in violations
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
