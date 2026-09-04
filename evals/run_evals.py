"""LLM evaluation harness: run classify + triage on golden items and compute metrics."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import typer

from eoa.errors import LLMOutputError, ResourceUnavailable
from eoa.llm.schemas.analysis import ClassifyOut, TriageOut
from eoa.pipeline.classify import classify_item
from eoa.pipeline.triage import triage_item

log = structlog.get_logger(__name__)

# Triage level ordinal for ±1 comparison
LEVEL_ORDINAL = {"red": 3, "orange": 2, "yellow": 1, "archive": 0}

EVALS_DIR = Path(__file__).parent
GOLDEN_DIR = EVALS_DIR / "golden"
RESULTS_DIR = EVALS_DIR / "results"


def load_golden_set(name: str) -> list[dict[str, Any]]:
    """Load golden items from JSONL file."""
    path = GOLDEN_DIR / f"{name}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Golden set not found: {path}")
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def make_fake_item(golden: dict[str, Any]) -> dict[str, Any]:
    """Convert golden item to fake DB item for pipeline."""
    return {
        "id": golden["id"],
        "title": golden["title"],
        "clean_text": golden["text"],
        "lang": golden["lang"],
        "url": f"golden://{golden['id']}",
        "source_name": golden.get("source_kind", "golden"),
        "published_at": None,
        "entities_mentioned": [],
    }


def compute_metrics(
    predicted: ClassifyOut | TriageOut, expected: dict[str, Any], stage: str
) -> dict[str, Any]:
    """Compute accuracy metrics for one prediction."""
    metrics: dict[str, Any] = {}

    if stage == "classify":
        pred = predicted  # type: ignore
        metrics["domain_match"] = pred.domain == expected.get("domain")
        metrics["report_kind_match"] = pred.report_kind == expected.get("report_kind")
        metrics["subdomain_match"] = pred.subdomain == expected.get("subdomain", "")

        # Entity recall (case-insensitive substring)
        expected_entities = set(e.lower() for e in expected.get("entities", []))
        pred_entities = set(e.name.lower() for e in pred.entities)
        if expected_entities:
            entity_matches = sum(1 for exp in expected_entities if any(exp in p for p in pred_entities))
            metrics["entity_recall"] = entity_matches / len(expected_entities)
        else:
            metrics["entity_recall"] = 1.0 if not pred_entities else 0.0

    elif stage == "triage":
        pred = predicted  # type: ignore
        metrics["level_match"] = pred.level == expected.get("level")
        expected_level_ord = LEVEL_ORDINAL.get(expected.get("level"), 0)
        pred_level_ord = LEVEL_ORDINAL.get(pred.level, 0)
        metrics["level_ordinal_match"] = abs(expected_level_ord - pred_level_ord) <= 1

        score_range = expected.get("score_range", [1, 10])
        metrics["score_in_range"] = score_range[0] <= pred.score <= score_range[1]

    return metrics


def run_eval(
    golden_items: list[dict[str, Any]],
    limit: int | None = None,
    role: str = "resident",
) -> dict[str, Any]:
    """Run evaluation on golden items."""
    if limit:
        golden_items = golden_items[:limit]

    results = []
    stats = {
        "total": len(golden_items),
        "classify_ok": 0,
        "classify_fail": 0,
        "triage_ok": 0,
        "triage_fail": 0,
        "classify_metrics": {},
        "triage_metrics": {},
        "items": [],
    }

    log.info("eval_start", total=len(golden_items), limit=limit, role=role)

    for i, golden in enumerate(golden_items):
        item_result: dict[str, Any] = {"id": golden["id"], "golden": golden["expected"]}
        fake_item = make_fake_item(golden)
        start_time = time.perf_counter()

        # CLASSIFY
        try:
            classify_out = classify_item(fake_item, role=role, interactive=False)
            item_result["classify"] = {
                "domain": classify_out.domain,
                "subdomain": classify_out.subdomain,
                "report_kind": classify_out.report_kind,
                "entities": [e.name for e in classify_out.entities],
            }
            metrics = compute_metrics(classify_out, golden["expected"], "classify")
            item_result["classify_metrics"] = metrics

            for key, val in metrics.items():
                if key not in stats["classify_metrics"]:
                    stats["classify_metrics"][key] = []
                stats["classify_metrics"][key].append(val)
            stats["classify_ok"] += 1
        except (LLMOutputError, ResourceUnavailable) as exc:
            log.warning("classify_failed", item_id=golden["id"], error=str(exc)[:200])
            item_result["classify_error"] = str(exc)[:200]
            stats["classify_fail"] += 1
        except Exception as exc:
            log.error("classify_exception", item_id=golden["id"], error=str(exc)[:200])
            item_result["classify_error"] = str(exc)[:200]
            stats["classify_fail"] += 1

        # TRIAGE (only if classify succeeded)
        if "classify" in item_result:
            try:
                triage_out = triage_item(fake_item, role=role, interactive=False)
                item_result["triage"] = {
                    "score": triage_out.score,
                    "level": triage_out.level,
                    "novelty": triage_out.novelty,
                    "magnitude": triage_out.magnitude,
                }
                metrics = compute_metrics(triage_out, golden["expected"], "triage")
                item_result["triage_metrics"] = metrics

                for key, val in metrics.items():
                    if key not in stats["triage_metrics"]:
                        stats["triage_metrics"][key] = []
                    stats["triage_metrics"][key].append(val)
                stats["triage_ok"] += 1
            except (LLMOutputError, ResourceUnavailable) as exc:
                log.warning("triage_failed", item_id=golden["id"], error=str(exc)[:200])
                item_result["triage_error"] = str(exc)[:200]
                stats["triage_fail"] += 1
            except Exception as exc:
                log.error("triage_exception", item_id=golden["id"], error=str(exc)[:200])
                item_result["triage_error"] = str(exc)[:200]
                stats["triage_fail"] += 1

        latency = time.perf_counter() - start_time
        item_result["latency_ms"] = latency * 1000
        results.append(item_result)

        if (i + 1) % 5 == 0:
            log.info("eval_progress", completed=i + 1, total=len(golden_items))

    # Aggregate metrics
    aggregated = {"classify": {}, "triage": {}}
    for stage in ["classify", "triage"]:
        for metric, values in stats[f"{stage}_metrics"].items():
            if values:
                aggregated[stage][metric] = {
                    "mean": sum(values) / len(values),
                    "count": len(values),
                }

    latencies = [r["latency_ms"] for r in results if "latency_ms" in r]
    stats["mean_latency_ms"] = sum(latencies) / len(latencies) if latencies else 0

    log.info("eval_complete", **{k: v for k, v in stats.items() if k != "items"})
    return {"stats": stats, "results": results, "aggregated": aggregated}


def format_markdown_report(eval_results: dict[str, Any]) -> str:
    """Format evaluation results as markdown."""
    stats = eval_results["stats"]
    aggregated = eval_results["aggregated"]
    results = eval_results["results"]

    lines = [
        "# Evaluation Results\n",
        f"**Run:** {datetime.now().isoformat()}\n",
        f"**Total Items:** {stats['total']}\n",
        f"**Classify OK:** {stats['classify_ok']} | Failed: {stats['classify_fail']}\n",
        f"**Triage OK:** {stats['triage_ok']} | Failed: {stats['triage_fail']}\n",
        f"**Mean Latency:** {stats['mean_latency_ms']:.1f} ms\n",
        "\n## Classify Metrics\n",
        "| Metric | Mean | Count |\n",
        "|--------|------|-------|\n",
    ]

    for metric, data in sorted(aggregated["classify"].items()):
        lines.append(f"| {metric} | {data['mean']:.3f} | {data['count']} |\n")

    lines.extend(
        [
            "\n## Triage Metrics\n",
            "| Metric | Mean | Count |\n",
            "|--------|------|-------|\n",
        ]
    )

    for metric, data in sorted(aggregated["triage"].items()):
        lines.append(f"| {metric} | {data['mean']:.3f} | {data['count']} |\n")

    lines.extend(
        [
            "\n## Per-Item Results\n",
            "| ID | Domain | Report Kind | Level | Score | Latency (ms) | Errors |\n",
            "|---|--------|-------------|-------|-------|------------|--------|\n",
        ]
    )

    for r in results:
        domain = r.get("classify", {}).get("domain", "—")
        report_kind = r.get("classify", {}).get("report_kind", "—")
        level = r.get("triage", {}).get("level", "—")
        score = r.get("triage", {}).get("score", "—")
        latency = f"{r['latency_ms']:.0f}" if "latency_ms" in r else "—"
        errors = []
        if "classify_error" in r:
            errors.append(f"classify: {r['classify_error'][:50]}")
        if "triage_error" in r:
            errors.append(f"triage: {r['triage_error'][:50]}")
        error_str = " | ".join(errors) if errors else "—"
        row = f"| {r['id']} | {domain} | {report_kind} | {level} | {score} | {latency} | {error_str} |\n"
        lines.append(row)

    return "".join(lines)


def main(
    set_name: str = typer.Option("classify_triage", help="Golden set name"),
    limit: int = typer.Option(None, help="Limit number of items"),
    role: str = typer.Option("resident", help="LLM role (resident or light)"),
) -> None:
    """Run LLM evaluation on golden items."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Load and run
    golden_items = load_golden_set(set_name)
    eval_results = run_eval(golden_items, limit=limit, role=role)

    # Save JSON
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"evals_{set_name}_{timestamp}.json"
    with open(json_path, "w") as f:
        json.dump(eval_results, f, indent=2)
    print(f"\nResults saved to: {json_path}")

    # Save markdown
    md_text = format_markdown_report(eval_results)
    md_path = RESULTS_DIR / f"evals_{set_name}_{timestamp}.md"
    with open(md_path, "w") as f:
        f.write(md_text)
    print(f"Report saved to: {md_path}")

    # Print to stdout
    print("\n" + md_text)


if __name__ == "__main__":
    typer.run(main)
