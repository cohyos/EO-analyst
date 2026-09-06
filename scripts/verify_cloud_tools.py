"""U8-6/(ג) (docs/adr/005-cloud-llm-cli.md, Revision 2026-09-06): verify, live, which of this
machine's agentic CLIs (agy/claude/codex) can actually do web research in HEADLESS mode -- the
prerequisite for `eoa.search.deep_search`'s cloud-delegated batch investigation path (U8-6b),
which hands an entire file of pending questions to one CLI call and expects it to search and
fetch sources on its own.

Runs ONE simple, verifiable research question through each CLI with the flags this project's
delegation code actually uses, and records:

  - whether the process succeeded at all (binary found, non-zero exit, timeout)
  - whether the CLI's own usage/metadata says it actually invoked a web tool (not just answered
    from parametric memory -- a plausible-sounding but ungrounded answer is exactly the failure
    mode this project cannot accept, per docs/CONVENTIONS.md rule #5 "never invent")
  - the exact flags that worked, for the ADR's permission matrix

This is a read-only diagnostic: it never writes to the DB, never touches `llm_calls`, and is not
imported by any other module. Run manually::

    PYTHONPATH=agent python scripts/verify_cloud_tools.py

Output: a human-readable table to stdout, and the same data as JSON to
``runtime/tmp/verify_cloud_tools_<ts>.json`` for pasting into the ADR.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A question with a single, checkable, time-sensitive-ish fact that cannot be answered reliably
# from a model's static training data alone -- forces the CLI to actually go get it if it can.
QUESTION = (
    "What is the latest stable release version of PostgreSQL as of today? "
    "Reply with just the version number and the exact source URL you used, nothing else."
)
TIMEOUT_S = 120
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


@dataclass
class ToolCheckResult:
    cli: str
    binary_found: bool
    flags_tried: list[str] = field(default_factory=list)
    ok: bool = False
    used_a_tool: bool | None = None  # None = couldn't tell from the CLI's own output
    duration_s: float = 0.0
    answer_excerpt: str = ""
    error: str = ""
    notes: str = ""


def _run(
    args: list[str], *, input_text: str | None = None, timeout: int = TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        stdin=subprocess.DEVNULL if input_text is None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def check_claude() -> ToolCheckResult:
    """Verified live 2026-09-06: `--restricted --allowedTools WebSearch --output-format json`
    works headlessly with NO permission-bypass flag at all -- `--restricted` only strips
    Bash/code-execution tools and WebFetch *unless* named in `--allowedTools`/`--tools`, so
    naming WebSearch (and WebFetch) there is sufficient; `usage.server_tool_use.web_search_requests`
    in the JSON output confirms whether a real search happened, not just a memorized answer."""
    res = ToolCheckResult(cli="claude", binary_found=bool(shutil.which("claude")))
    if not res.binary_found:
        res.error = "claude binary not found on PATH"
        return res
    args = ["claude", "-p", "--output-format", "json", "--restricted", "--allowedTools", "WebSearch,WebFetch"]
    res.flags_tried = args[1:]
    t0 = time.monotonic()
    try:
        proc = _run(args, input_text=QUESTION)
    except subprocess.TimeoutExpired:
        res.error = f"timed out after {TIMEOUT_S}s"
        return res
    res.duration_s = round(time.monotonic() - t0, 1)
    if proc.returncode != 0:
        res.error = f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}"
        return res
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        res.error = f"non-JSON output: {proc.stdout[:300]!r}"
        return res
    if data.get("is_error"):
        res.error = f"is_error: {str(data.get('result'))[:300]}"
        return res
    tool_use = (data.get("usage") or {}).get("server_tool_use") or {}
    searches = int(tool_use.get("web_search_requests") or 0)
    fetches = int(tool_use.get("web_fetch_requests") or 0)
    res.ok = True
    res.used_a_tool = (searches + fetches) > 0
    res.answer_excerpt = str(data.get("result", ""))[:300]
    res.notes = f"web_search_requests={searches} web_fetch_requests={fetches}"
    return res


def check_agy() -> ToolCheckResult:
    """agy exposes no documented `--search`/`--web` flag (`agy --help`, checked 2026-09-06) and
    its JSON output carries no per-call tool-usage metadata the way claude's does, so a "used a
    tool" verdict can't be read off the response the way it can for claude -- `used_a_tool` stays
    None (unknown) here even on success; a correct, current-sounding answer is suggestive but not
    proof of real grounding (docs/CONVENTIONS.md rule #5's concern). `--dangerously-skip-permissions`
    was NOT tried by this script (a prior manual attempt was refused by this machine's own agent
    sandbox before agy itself ran) -- if a future run needs it, try it directly, outside this
    harness's own approval layer.
    """
    res = ToolCheckResult(cli="agy", binary_found=bool(shutil.which("agy")))
    if not res.binary_found:
        res.error = "agy binary not found on PATH"
        return res
    args = ["agy", "-p", QUESTION, "--output-format", "json"]
    res.flags_tried = args[1:]
    t0 = time.monotonic()
    try:
        proc = _run(args, input_text=None)
    except subprocess.TimeoutExpired:
        res.error = f"timed out after {TIMEOUT_S}s"
        return res
    res.duration_s = round(time.monotonic() - t0, 1)
    if proc.returncode != 0:
        res.error = f"exit {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}"
        return res
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        res.error = f"non-JSON output: {proc.stdout[:300]!r}"
        return res
    if data.get("status") and data["status"] != "SUCCESS":
        res.error = f"status={data.get('status')}"
        return res
    res.ok = True
    res.answer_excerpt = str(data.get("response", ""))[:300]
    res.notes = "no per-call tool-usage field in agy's JSON output -- cannot confirm grounding from this response alone"
    return res


def check_codex() -> ToolCheckResult:
    """`codex exec --help` (checked 2026-09-06, this machine's installed version) has no
    `--search`/`--web`/`-c web_search=...` option -- only a shell sandbox (`-s read-only` /
    `workspace-write` / `danger-full-access`) that runs model-generated commands, which is not a
    dedicated search/fetch tool and is exactly the kind of arbitrary-code surface this project's
    CLI providers deliberately avoid (docs/adr/005-cloud-llm-cli.md). Recorded as unsupported
    rather than attempted with a risky sandbox mode."""
    res = ToolCheckResult(cli="codex", binary_found=bool(shutil.which("codex")))
    res.notes = (
        "no --search/--web flag in `codex exec --help` on this machine's installed version; not attempted"
    )
    return res


def main() -> int:
    results = [check_claude(), check_agy(), check_codex()]

    print(f"{'CLI':<8} {'found':<7} {'ok':<6} {'used_tool':<10} {'time_s':<8} notes")
    for r in results:
        print(
            f"{r.cli:<8} {r.binary_found!s:<7} {r.ok!s:<6} "
            f"{r.used_a_tool!s:<10} {r.duration_s:<8} {r.notes or r.error}"
        )
        if r.answer_excerpt:
            print(f"         answer: {r.answer_excerpt}")

    out_dir = REPO_ROOT / "runtime" / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"verify_cloud_tools_{ts}.json"
    out_path.write_text(
        json.dumps(
            {"question": QUESTION, "results": [asdict(r) for r in results]}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
