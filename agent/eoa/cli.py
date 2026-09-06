"""`eo` command line: run cycles, investigate, status, models, serve, native lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(help="EO-Analyst — local OSINT analyst for defense EO/IR & CV", no_args_is_help=True)
models_app = typer.Typer(help="Model registry & allow-list")
app.add_typer(models_app, name="models")
native_app = typer.Typer(
    help="Native (no-Docker) lifecycle: postgres/ntfy/orchestrator/api via scripts/native/ "
    "(docs/adr/004-windows-native.md)",
    no_args_is_help=True,
)
app.add_typer(native_app, name="native")


@app.command()
def run(
    scope: str = typer.Argument("daily", help="daily|ingest|report|dedup|classify|triage|analyze|bd|patents"),
    mode: str = typer.Option("full", help="full|eco"),
    now: bool = typer.Option(True, help="run in-process now"),
    territory: str = typer.Option(
        None, help="scope=bd only: ISO-2 country code or region code (US, IL, EU, ...)"
    ),
    lookback_days: int = typer.Option(90, help="scope=bd only: lookback window in days"),
    topic: str = typer.Option(None, help="scope=patents only: scan just this one ad-hoc topic"),
) -> None:
    """Run a cycle (or one stage) immediately in this process — respects the resource gate & polite mode."""
    from eoa.orchestrator.main import configure_logging

    configure_logging()
    if scope == "daily":
        from eoa.memory.relational import enqueue_job, finish_job
        from eoa.orchestrator.jobs import run_daily

        job_id = enqueue_job("daily_run", {"mode": mode}, priority=0)
        try:
            stats = run_daily({"id": job_id, "kind": "daily_run", "payload": {"mode": mode}}, night=False)
            finish_job(job_id, "done", result=stats)
            rprint(json.dumps(stats, ensure_ascii=False, indent=2))
        except Exception as exc:
            finish_job(job_id, "failed", error=str(exc)[:400])
            raise
        return
    if scope == "ingest":
        from eoa.fetch.service import run_ingest

        rprint(asyncio.run(run_ingest()))
    elif scope == "dedup":
        from eoa.pipeline.dedup import run_dedup

        rprint(run_dedup())
    elif scope == "classify":
        from eoa.pipeline.classify import run_classify

        rprint(run_classify(role="light" if mode == "eco" else "resident"))
    elif scope == "triage":
        from eoa.pipeline.triage import run_triage

        rprint(run_triage(role="light" if mode == "eco" else "resident"))
    elif scope == "analyze":
        from eoa.pipeline.analyze import run_analyze

        rprint(run_analyze(role="light" if mode == "eco" else "resident"))
    elif scope == "report":
        from eoa.report.daily import build_daily

        # F4: a manual `eo run report` always builds a fresh report, bypassing build_daily's
        # 6-hour idempotency guard (which exists to stop the automatic nightly pipeline from
        # building a second daily report on top of one built minutes earlier by another job).
        rprint(build_daily(force=True))
    elif scope == "bd":
        from eoa.report.bd_territory import build_bd_territory

        if not territory:
            raise typer.BadParameter("scope=bd requires --territory (e.g. --territory US)")
        paths = build_bd_territory(territory, lookback_days)
        rprint(
            json.dumps(
                {
                    "report_id": paths.report_id,
                    "territory": paths.territory,
                    "qa_passed": paths.qa.passed,
                    "docx": str(paths.docx),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif scope == "patents":
        from eoa.patents.analyze import analyze_patents
        from eoa.patents.scan import WatchTopic, scan_patents
        from eoa.patents.valuation import score_and_persist

        scan_stats = scan_patents(topics=[WatchTopic(name_he=topic, query=topic)] if topic else None, assignees=[] if topic else None)
        analyze_stats = analyze_patents(30)
        scored = score_and_persist(limit=100)
        rprint(
            json.dumps(
                {
                    "scan": vars(scan_stats),
                    "analyze": vars(analyze_stats),
                    "valued": scored,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        raise typer.BadParameter(f"unknown scope {scope}")


@app.command()
def investigate(question: str, item_id: int | None = None) -> None:
    """Run one deep-search investigation now and print the answer."""
    from eoa.memory.relational import enqueue_job, finish_job
    from eoa.orchestrator.main import configure_logging
    from eoa.search.deep_search import investigate as _inv

    configure_logging()
    job_id = enqueue_job("deep_search", {"question": question, "item_id": item_id}, priority=0)
    inv = _inv(question, item_id=item_id, job_id=job_id)
    finish_job(job_id, "done", result=inv.result.model_dump() if inv.result else None)
    rprint(json.dumps(inv.result.model_dump() if inv.result else {}, ensure_ascii=False, indent=2))


@app.command()
def status() -> None:
    """Show services, GPU/RAM/disk, loaded models and recent gate decisions."""
    from eoa import db
    from eoa.llm import ollama_client
    from eoa.resources.gate import gate
    from eoa.search.provider import ping as searx_ping

    st = gate().status()
    t = Table(title=f"EO-Analyst status {datetime.now(tz=UTC):%H:%M:%S}Z")
    t.add_column("what")
    t.add_column("value")
    t.add_row("postgres", "up" if db.ping() else "DOWN")
    t.add_row("ollama", "up" if ollama_client.ping() else "DOWN")
    t.add_row("searxng", "up" if searx_ping() else "down/unreachable from host")
    g = st["gpu"]
    t.add_row("gpu", f"{g['vram_used_mb']}/{g['vram_total_mb']} MB, util {g['util_pct']}%, {g['temp_c']}°C")
    t.add_row("ram free", f"{st['ram']['free_mb']} MB")
    t.add_row("disk free", f"{st['disk_free_gb']} GB")
    t.add_row("loaded", ", ".join(m["name"] for m in st["loaded_models"]) or "—")
    t.add_row("night window", str(st["batch_window"]))
    rprint(t)


@app.command()
def serve(host: str | None = None, port: int | None = None) -> None:
    """Start the web API/UI."""
    import uvicorn

    from eoa.config import settings

    uvicorn.run(
        "eoa.api.app:app", host=host or settings().api.host, port=port or settings().api.port, reload=False
    )


@app.command()
def orchestrate() -> None:
    """Start scheduler + worker (what the `agent` container runs)."""
    from eoa.orchestrator.main import main

    main()


@models_app.command("list")
def models_list() -> None:
    """Registry vs. what Ollama has; flags anything outside the allow-list."""
    from eoa.config import settings
    from eoa.llm import ollama_client

    have = {m["name"]: m for m in ollama_client.list_models()}
    reg = settings().registry
    t = Table(title="models")
    for c in ("key", "ollama", "origin", "license", "present", "digest"):
        t.add_column(c)
    for key, spec in reg.models.items():
        m = have.get(spec.ollama or "")
        t.add_row(
            key,
            spec.ollama or spec.hf or "",
            spec.origin,
            spec.license,
            "yes" if m else "no",
            (m or {}).get("digest", "")[:16],
        )
    rprint(t)
    unknown = [n for n in have if n not in {s.ollama for s in reg.models.values()}]
    if unknown:
        rprint(f"[yellow]present in Ollama but not in registry: {', '.join(unknown)}[/yellow]")


@models_app.command("lock")
def models_lock() -> None:
    """Write config/models.lock with the current digests of registered models."""
    from eoa.config import CONFIG_DIR, settings
    from eoa.llm import ollama_client

    have = {m["name"]: m for m in ollama_client.list_models()}
    lock = {}
    for key, spec in settings().registry.models.items():
        m = have.get(spec.ollama or "")
        if m:
            lock[key] = {
                "ollama": spec.ollama,
                "digest": m.get("digest"),
                "size": m.get("size"),
                "locked_at": datetime.now(tz=UTC).isoformat(),
            }
    (CONFIG_DIR / "models.lock").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    rprint(f"locked {len(lock)} models → config/models.lock")


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a pid on Windows, without adding a psutil dependency.

    `os.kill(pid, 0)` (the usual POSIX trick) is not meaningful on Windows: signal 0 isn't a
    supported value there. `tasklist` is a native Windows command available on every install.
    """
    if pid <= 0:
        return False
    import subprocess

    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
        return str(pid) in r.stdout
    except Exception:
        return False


def _native_paths() -> dict[str, Path]:
    from eoa.config import REPO_ROOT

    root = REPO_ROOT
    return {
        "root": root,
        "supervisor_script": root / "scripts" / "native" / "eoa-supervisor.ps1",
        "runtime": root / "runtime",
        "pid_dir": root / "runtime" / "pids",
        "log_dir": root / "runtime" / "logs",
        "sentinel": root / "runtime" / "supervisor.stop",
        "supervisor_pid": root / "runtime" / "supervisor.pid",
    }


@native_app.command("start")
def native_start() -> None:
    """Launch scripts/native/eoa-supervisor.ps1 detached, unless it's already running."""
    import subprocess

    paths = _native_paths()
    if paths["supervisor_pid"].exists():
        pid_text = paths["supervisor_pid"].read_text(encoding="utf-8").strip()
        if pid_text and _pid_alive(int(pid_text)):
            rprint(f"[yellow]supervisor already running (pid {pid_text})[/yellow]")
            return
        rprint("[dim]stale supervisor pidfile found; starting a new one[/dim]")

    if not paths["supervisor_script"].exists():
        rprint(f"[red]{paths['supervisor_script']} not found[/red]")
        raise typer.Exit(code=1)

    paths["sentinel"].unlink(missing_ok=True)
    # Q6-1 (2026-09-06): DETACHED_PROCESS makes the `pwsh` App-Execution-Alias exit 0 without running
    # -File; CREATE_NO_WINDOW keeps the console hidden and actually starts the script.
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    proc = subprocess.Popen(
        ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(paths["supervisor_script"])],
        cwd=str(paths["root"]),
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    import time as _time

    _time.sleep(3)
    if proc.poll() is not None:
        rprint(
            f"[red]supervisor exited immediately (code {proc.returncode}); see runtime/logs/supervisor.log[/red]"
        )
        raise typer.Exit(code=1)
    rprint("[green]native supervisor launched (hidden window) — check `eo native status` shortly[/green]")


@native_app.command("stop")
def native_stop(
    timeout: int = typer.Option(30, help="seconds to wait for the supervisor to exit"),
    with_postgres: bool = typer.Option(
        False,
        "--with-postgres",
        help="also stop postgres (default: Q6-4 — leave postgres running so other tools/queries "
        "keep working; the supervisor's own -KeepPostgres default matches this)",
    ),
) -> None:
    """Write the stop sentinel and wait for the supervisor (and its children) to exit.

    Q6-4 (2026-09-06): postgres is left running unless --with-postgres is given, which is
    conveyed to the running supervisor via the sentinel file's content (scripts/native/
    eoa-supervisor.ps1 looks for the substring "with-postgres" in it).
    """
    import time

    paths = _native_paths()
    paths["runtime"].mkdir(parents=True, exist_ok=True)
    sentinel_text = "stop-with-postgres" if with_postgres else "stop"
    paths["sentinel"].write_text(sentinel_text, encoding="utf-8")
    rprint(f"stop sentinel written ({paths['sentinel']}); waiting up to {timeout}s...")

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not paths["supervisor_pid"].exists():
            rprint("[green]supervisor stopped[/green]")
            return
        pid_text = paths["supervisor_pid"].read_text(encoding="utf-8").strip()
        if not pid_text or not _pid_alive(int(pid_text)):
            rprint("[green]supervisor stopped[/green]")
            return
        time.sleep(1)
    rprint("[yellow]supervisor still running after timeout — check runtime/logs/supervisor.log[/yellow]")


@native_app.command("status")
def native_status() -> None:
    """pg_ctl status, ntfy health, api /api/status, orchestrator/supervisor process liveness."""
    import subprocess
    import time

    import httpx

    paths = _native_paths()
    t = Table(title="EO-Analyst native status")
    t.add_column("component")
    t.add_column("state")

    pg_ctl = paths["runtime"] / "pgsql" / "bin" / "pg_ctl.exe"
    pgdata = paths["runtime"] / "pgdata"
    if pg_ctl.exists():
        r = subprocess.run([str(pg_ctl), "-D", str(pgdata), "status"], capture_output=True, text=True)
        t.add_row("postgres", "up" if r.returncode == 0 else "down")
    else:
        t.add_row("postgres", "not installed (run scripts/native/install_native.ps1)")

    # Q6-3b (2026-09-06): the probe timeout was widened from 3s to 10s so a healthy-but-busy
    # api/ntfy doesn't get misreported as "down (ReadTimeout)"; a "slow" label is added below when
    # a probe still takes more than 3s, so an unusually slow-but-up response stays visible.
    slow_threshold_s = 3.0

    try:
        t0 = time.monotonic()
        r = httpx.get(
            os.environ.get("NTFY_URL", "http://127.0.0.1:8091").rstrip("/") + "/v1/health", timeout=10
        )
        elapsed = time.monotonic() - t0
        state = "up" if r.status_code == 200 else f"http {r.status_code}"
        if elapsed > slow_threshold_s:
            state = f"{state} (slow {elapsed:.1f}s)"
        t.add_row("ntfy", state)
    except Exception as exc:
        t.add_row("ntfy", f"down ({exc.__class__.__name__})")

    try:
        # agent/eoa/api/routes/status.py — this codebase has no separate /api/health route.
        t0 = time.monotonic()
        r = httpx.get("http://127.0.0.1:8765/api/status", timeout=10)
        elapsed = time.monotonic() - t0
        state = "up" if r.status_code == 200 else f"http {r.status_code}"
        if elapsed > slow_threshold_s:
            state = f"{state} (slow {elapsed:.1f}s)"
        t.add_row("api", state)
    except Exception as exc:
        t.add_row("api", f"down ({exc.__class__.__name__})")

    for name in ("orchestrator", "api"):
        pid_file = paths["pid_dir"] / f"{name}.pid"
        if pid_file.exists():
            pid_text = pid_file.read_text(encoding="utf-8").strip()
            alive = pid_text and _pid_alive(int(pid_text))
            t.add_row(f"{name} process", f"pid {pid_text} ({'alive' if alive else 'stale pidfile'})")
        else:
            t.add_row(f"{name} process", "no pidfile")

    if paths["supervisor_pid"].exists():
        pid_text = paths["supervisor_pid"].read_text(encoding="utf-8").strip()
        alive = pid_text and _pid_alive(int(pid_text))
        t.add_row("supervisor", f"pid {pid_text} ({'alive' if alive else 'stale pidfile'})")
    else:
        t.add_row("supervisor", "not running")

    rprint(t)


@native_app.command("logs")
def native_logs(
    name: str = typer.Argument("supervisor", help="supervisor|postgres|ntfy|orchestrator|api"),
    lines: int = typer.Option(100, "-n", "--lines", help="tail this many lines"),
    follow: bool = typer.Option(False, "-f", "--follow", help="keep printing new lines (like tail -f)"),
) -> None:
    """Tail runtime/logs/<name>*.log (supervisor.log, or the daily-rotated <name>.<date>.log)."""
    import time

    paths = _native_paths()
    log_dir = paths["log_dir"]
    if name == "supervisor":
        candidates = [log_dir / "supervisor.log"]
    else:
        candidates = sorted(log_dir.glob(f"{name}.*.log"), reverse=True)
        if not candidates:
            candidates = [log_dir / f"{name}.log"]
    log_file = next((c for c in candidates if c.exists()), None)
    if log_file is None:
        rprint(f"[red]no log file found for '{name}' under {log_dir}[/red]")
        raise typer.Exit(code=1)

    if not follow:
        text = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in text[-lines:]:
            print(line)
        return

    with log_file.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, 2)
        try:
            while True:
                line = fh.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    app()
