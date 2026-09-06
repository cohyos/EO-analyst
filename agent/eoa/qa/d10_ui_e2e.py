"""D10 -- UI/e2e deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D10).

Off by default (``--e2e`` in ``scripts/qa_score.py``) since a full Playwright run against the live
app takes minutes and needs the app actually running -- see ``e2e/playwright.config.ts``'s own
"drives whatever is already running" design note. When run, this only reads the JSON reporter
output; it never starts/stops the app (docs/CONVENTIONS.md-adjacent instruction: this project's QA
tooling must not restart processes).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from eoa.qa.types import Check, DomainScore


def _e2e_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "e2e"


def run_playwright_json(*, timeout_s: int = 2400) -> dict | None:
    """Runs ``npx playwright test --reporter=json`` in ``e2e/`` and returns the parsed JSON
    report, or ``None`` on a hard failure to even produce one (npx missing, no app running,
    timeout) -- the caller treats that as "manual only" for this round, not a 0."""
    e2e_dir = _e2e_dir()
    if not e2e_dir.exists():
        return None
    # Round-3 close (2026-09-06): parsing stdout failed silently ("playwright run failed to
    # produce a JSON report") because the config's own reporters and npm/npx warnings share
    # stdout with the JSON. Ask Playwright to write the JSON report to a file instead and read that.
    out_path = e2e_dir / "test-results" / "qa_d10_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("EOA_D10_REUSE_JSON") == "1" and out_path.exists():
        # Round 5 (2026-09-07): re-score an already completed e2e run (the full 5-project suite
        # takes ~22 minutes) instead of driving the browsers again -- explicit opt-in only.
        try:
            return json.loads(out_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    if out_path.exists():
        out_path.unlink()
    env = {**os.environ, "PLAYWRIGHT_JSON_OUTPUT_NAME": str(out_path)}
    try:
        subprocess.run(
            ["npx", "playwright", "test", "--reporter=json"],
            cwd=str(e2e_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=True,  # Windows: npx is a .cmd shim
            env=env,
        )
    except OSError:
        return None
    except subprocess.TimeoutExpired:
        # Round 5 (2026-09-07): the round-5 run finished at 22 min, past the old 15 min cap -- the
        # shell shim was killed but Playwright itself kept going and wrote the file a few minutes
        # later; give it that grace period before declaring the run lost.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline and not out_path.exists():
            time.sleep(15)
    try:
        return json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _pass_ratio(report: dict) -> tuple[int, int]:
    passed = 0
    total = 0

    def walk(suite: dict) -> None:
        nonlocal passed, total
        for spec in suite.get("specs", []) or []:
            for test in spec.get("tests", []) or []:
                total += 1
                results = test.get("results", []) or []
                if results and results[-1].get("status") == "passed":
                    passed += 1
        for child in suite.get("suites", []) or []:
            walk(child)

    for suite in report.get("suites", []) or []:
        walk(suite)
    return passed, total


def score_D10(*, run_e2e: bool = False) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D10: e2e pass ratio, run only when ``run_e2e`` (``--e2e``) is passed."""
    if not run_e2e:
        return DomainScore(
            domain="D10", score_0_100=None, checks=[], n=0, note="skipped (pass --e2e to run playwright)"
        )
    report = run_playwright_json()
    if report is None:
        return DomainScore(
            domain="D10",
            score_0_100=None,
            checks=[],
            n=0,
            note="playwright run failed to produce a JSON report",
        )
    passed, total = _pass_ratio(report)
    if total == 0:
        return DomainScore(
            domain="D10", score_0_100=None, checks=[], n=0, note="playwright report had zero tests"
        )
    ratio = passed / total
    check = Check(
        "e2e_pass_ratio", passed=ratio >= 0.95, weight=1.0, evidence=f"{passed}/{total} passed ({ratio:.1%})"
    )
    return DomainScore(domain="D10", score_0_100=round(ratio * 100, 1), checks=[check], n=total)
