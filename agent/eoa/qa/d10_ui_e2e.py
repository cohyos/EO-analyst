"""D10 -- UI/e2e deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D10).

Off by default (``--e2e`` in ``scripts/qa_score.py``) since a full Playwright run against the live
app takes minutes and needs the app actually running -- see ``e2e/playwright.config.ts``'s own
"drives whatever is already running" design note. When run, this only reads the JSON reporter
output; it never starts/stops the app (docs/CONVENTIONS.md-adjacent instruction: this project's QA
tooling must not restart processes).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from eoa.qa.types import Check, DomainScore


def _e2e_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "e2e"


def run_playwright_json(*, timeout_s: int = 900) -> dict | None:
    """Runs ``npx playwright test --reporter=json`` in ``e2e/`` and returns the parsed JSON
    report, or ``None`` on a hard failure to even produce one (npx missing, no app running,
    timeout) -- the caller treats that as "manual only" for this round, not a 0."""
    e2e_dir = _e2e_dir()
    if not e2e_dir.exists():
        return None
    try:
        proc = subprocess.run(
            ["npx", "playwright", "test", "--reporter=json"],
            cwd=str(e2e_dir),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            shell=True,  # Windows: npx is a .cmd shim
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
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
            domain="D10", score_0_100=None, checks=[], n=0, note="playwright run failed to produce a JSON report"
        )
    passed, total = _pass_ratio(report)
    if total == 0:
        return DomainScore(domain="D10", score_0_100=None, checks=[], n=0, note="playwright report had zero tests")
    ratio = passed / total
    check = Check("e2e_pass_ratio", passed=ratio >= 0.95, weight=1.0, evidence=f"{passed}/{total} passed ({ratio:.1%})")
    return DomainScore(domain="D10", score_0_100=round(ratio * 100, 1), checks=[check], n=total)
