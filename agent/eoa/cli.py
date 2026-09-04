"""`eo` command line: run cycles, investigate, status, models, serve."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(help="EO-Analyst — local OSINT analyst for defense EO/IR & CV", no_args_is_help=True)
models_app = typer.Typer(help="Model registry & allow-list")
app.add_typer(models_app, name="models")


@app.command()
def run(
    scope: str = typer.Argument("daily", help="daily|ingest|report|dedup|classify|triage|analyze"),
    mode: str = typer.Option("full", help="full|eco"),
    now: bool = typer.Option(True, help="run in-process now"),
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

        rprint(build_daily())
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
    from eoa.search.searxng_client import ping as searx_ping

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


if __name__ == "__main__":
    app()
