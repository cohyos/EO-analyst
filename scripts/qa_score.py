#!/usr/bin/env python
"""Deterministic QA-loop scorer (docs/QA_CONTINUOUS_LOOP.md) -- one round of D1-D10.

Runs every deterministic domain check (``eoa.qa.scorer.score_all_domains``) over the frozen golden
sample (persisted to ``docs/qa/loop/golden_items.json`` on first run) plus a fresh rotating sample
seeded by the round number, writes ``docs/qa/loop/round_N_auto.json``, prints a summary table, and
appends/updates the round's row in ``docs/qa/loop/SCORES.md``.

Usage:
    PYTHONPATH=agent DATABASE_URL=postgresql://... python scripts/qa_score.py --round 0
    PYTHONPATH=agent DATABASE_URL=postgresql://... python scripts/qa_score.py --round 1 --e2e
    PYTHONPATH=agent DATABASE_URL=postgresql://... python scripts/qa_score.py --round 1 \\
        --merge-judge docs/qa/loop/round_1_judge.json

This script never mutates pipeline data, never restarts any process, and never runs an LLM call --
every check is a pure read + regex/query over what's already in the DB or on disk (the one
exception, gated behind ``--e2e`` and off by default, shells out to ``npx playwright test`` inside
``e2e/``, which itself only talks to whatever app is already running).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Windows consoles default stdout to cp1252, which can't encode the Hebrew evidence strings this
# script prints -- reconfigure to UTF-8 (same fix as scripts/repair_tenders.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.qa.sample import load_golden_questions, resolve_sample  # noqa: E402
from eoa.qa.scorer import DOMAIN_WEIGHTS, score_all_domains, weighted_total  # noqa: E402
from eoa.qa.types import DomainScore  # noqa: E402

_LOOP_DIR = Path(__file__).resolve().parent.parent / "docs" / "qa" / "loop"
_GOLDEN_ITEMS_PATH = _LOOP_DIR / "golden_items.json"
_GOLDEN_QUESTIONS_PATH = _LOOP_DIR / "golden_questions.json"
_SCORES_MD_PATH = _LOOP_DIR / "SCORES.md"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--round", type=int, required=True, dest="round_no")
    p.add_argument("--e2e", action="store_true", help="also run D10 via `npx playwright test` (slow, needs a live app)")
    p.add_argument("--no-links", action="store_true", help="skip D6's httpx link-liveness check (offline dev)")
    p.add_argument("--json", action="store_true", help="print the full JSON result instead of the summary table")
    p.add_argument("--merge-judge", type=Path, default=None, help="path to a round_N_judge.json to blend 0.5/0.5")
    return p.parse_args()


def _load_judge(path: Path | None) -> dict[str, float]:
    if path is None:
        return {}
    if not path.exists():
        print(f"warning: --merge-judge path {path} does not exist -- ignoring", file=sys.stderr)
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for domain, value in raw.items():
        if isinstance(value, dict):
            if "score" in value:
                out[domain] = float(value["score"])
        else:
            out[domain] = float(value)
    return out


def _print_table(scores: dict[str, DomainScore], judge_scores: dict[str, float], total: float | None) -> None:
    print(f"{'domain':<6}{'weight':>7}{'auto':>8}{'judge':>8}{'combined':>10}  note")
    for domain, weight in DOMAIN_WEIGHTS.items():
        ds = scores.get(domain)
        auto = ds.score_0_100 if ds else None
        judge = judge_scores.get(domain)
        if auto is not None and judge is not None:
            combined = 0.5 * auto + 0.5 * judge
        else:
            combined = auto if auto is not None else judge
        auto_s = f"{auto:.1f}" if auto is not None else "manual"
        judge_s = f"{judge:.1f}" if judge is not None else "-"
        combined_s = f"{combined:.1f}" if combined is not None else "-"
        note = ds.note if ds else ""
        print(f"{domain:<6}{weight:>7.0f}{auto_s:>8}{judge_s:>8}{combined_s:>10}  {note}")
    print("-" * 60)
    print(f"weighted total: {total if total is not None else 'n/a'}")


def _update_scores_md(round_no: int, scores: dict[str, DomainScore], judge_scores: dict[str, float], total: float | None) -> None:
    import datetime as dt

    _LOOP_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "| round | date | " + " | ".join(DOMAIN_WEIGHTS.keys()) + " | judge_merged | total |\n"
        + "|---" * (len(DOMAIN_WEIGHTS) + 4) + "|\n"
    )
    if not _SCORES_MD_PATH.exists():
        _SCORES_MD_PATH.write_text(
            "# QA continuous-loop score trend (docs/QA_CONTINUOUS_LOOP.md)\n\n" + header, encoding="utf-8"
        )
    text = _SCORES_MD_PATH.read_text(encoding="utf-8")
    if header not in text:
        # legacy file without the current column set -- append a fresh header block rather than
        # guessing at a schema migration for a hand-editable trend log.
        text += "\n" + header
    date_s = dt.datetime.now().strftime("%Y-%m-%d")
    cells = [str(round_no), date_s]
    for domain in DOMAIN_WEIGHTS:
        ds = scores.get(domain)
        auto = ds.score_0_100 if ds else None
        cells.append(f"{auto:.1f}" if auto is not None else "manual")
    cells.append("yes" if judge_scores else "no")
    cells.append(f"{total:.1f}" if total is not None else "n/a")
    row = "| " + " | ".join(cells) + " |\n"

    # replace an existing row for this round (idempotent re-run), else append
    lines = text.splitlines(keepends=True)
    prefix = f"| {round_no} |"
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = row
            replaced = True
            break
    if not replaced:
        lines.append(row)
    _SCORES_MD_PATH.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    golden_questions = load_golden_questions(_GOLDEN_QUESTIONS_PATH) if _GOLDEN_QUESTIONS_PATH.exists() else []

    with db.connection() as conn:
        resolved = resolve_sample(conn, round_no=args.round_no, golden_items_path=_GOLDEN_ITEMS_PATH)
        scores = score_all_domains(
            resolved,
            conn,
            golden_questions=golden_questions,
            run_link_check=not args.no_links,
            run_e2e=args.e2e,
        )

    judge_scores = _load_judge(args.merge_judge)
    total = weighted_total(scores, judge_scores=judge_scores)

    out_path = _LOOP_DIR / f"round_{args.round_no}_auto.json"
    _LOOP_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "round": args.round_no,
                "golden_item_ids": resolved.golden_item_ids,
                "rotating_item_ids": resolved.rotating_item_ids,
                "investigation_job_ids": resolved.investigation_job_ids,
                "domains": {k: v.to_dict() for k, v in scores.items()},
                "judge_scores": judge_scores,
                "weighted_total": total,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"wrote {out_path}")

    if args.json:
        print(json.dumps({k: v.to_dict() for k, v in scores.items()}, ensure_ascii=False, indent=2, default=str))
    else:
        _print_table(scores, judge_scores, total)

    _update_scores_md(args.round_no, scores, judge_scores, total)
    print(f"updated {_SCORES_MD_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
