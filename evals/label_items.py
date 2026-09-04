"""Interactive tool to label real DB items and extend the golden set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
import typer

from eoa.db import ping
from eoa.memory.relational import get_items_for_stage

log = structlog.get_logger(__name__)

GOLDEN_DIR = Path(__file__).parent / "golden"


def prompt_for_domain() -> str:
    """Prompt user for domain selection."""
    domains = [
        "airborne_pods",
        "land_surveillance",
        "naval_surveillance",
        "air_defense",
        "c_uas",
        "computer_vision",
        "secondary",
        "out_of_scope",
    ]
    print("\nDomains:")
    for i, d in enumerate(domains, 1):
        print(f"  {i}. {d}")
    while True:
        try:
            choice = input("Select domain (1-8 or enter to skip): ").strip()
            if not choice:
                return ""
            idx = int(choice) - 1
            if 0 <= idx < len(domains):
                return domains[idx]
            print("Invalid choice, try again.")
        except ValueError:
            print("Invalid choice, try again.")


def prompt_for_level() -> str:
    """Prompt user for triage level."""
    levels = ["red", "orange", "yellow", "archive"]
    print("\nTriage Levels:")
    for i, level in enumerate(levels, 1):
        print(f"  {i}. {level}")
    while True:
        try:
            choice = input("Select level (1-4 or enter to skip): ").strip()
            if not choice:
                return ""
            idx = int(choice) - 1
            if 0 <= idx < len(levels):
                return levels[idx]
            print("Invalid choice, try again.")
        except ValueError:
            print("Invalid choice, try again.")


def prompt_for_report_kind() -> str:
    """Prompt user for report kind."""
    kinds = [
        "verified_report",
        "company_pr",
        "rumor_speculation",
        "academic",
        "tender",
        "patent",
        "regulatory",
    ]
    print("\nReport Kinds:")
    for i, k in enumerate(kinds, 1):
        print(f"  {i}. {k}")
    while True:
        try:
            choice = input("Select report kind (1-7 or enter to skip): ").strip()
            if not choice:
                return ""
            idx = int(choice) - 1
            if 0 <= idx < len(kinds):
                return kinds[idx]
            print("Invalid choice, try again.")
        except ValueError:
            print("Invalid choice, try again.")


def format_item_for_display(item: dict[str, Any]) -> str:
    """Format DB item for display to user."""
    text = item.get("clean_text", "")
    text_preview = f"{text[:500]}...\n" if len(text) > 500 else f"{text}\n"
    text_size = len(text) // 10
    lines = [
        f"\n{'=' * 80}",
        f"ID: {item['id']} | Source: {item.get('source_name', '?')}",
        f"Title: {item.get('title', '?')[:100]}",
        f"Lang: {item.get('lang', '?')} | Published: {item.get('published_at', '?')}",
        f"\nText ({text_size} x 10 chars):",
        text_preview,
    ]
    if item.get("entities_mentioned"):
        lines.append(f"Entities: {', '.join(item['entities_mentioned'][:10])}\n")
    return "".join(lines)


def item_to_golden(item: dict[str, Any], domain: str, level: str, report_kind: str) -> dict[str, Any]:
    """Convert DB item to golden format."""
    # Generate a negative ID (typically the highest negative ID in use + 1)
    next_id = -1
    golden_path = GOLDEN_DIR / "classify_triage.jsonl"
    if golden_path.exists():
        with open(golden_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    obj = json.loads(line)
                    if obj["id"] < next_id:
                        next_id = obj["id"] - 1

    level_to_score = {"red": [8, 10], "orange": [6, 7], "yellow": [4, 5], "archive": [1, 3]}

    return {
        "id": next_id,
        "title": item.get("title", ""),
        "text": item.get("clean_text", ""),
        "lang": item.get("lang", "en"),
        "source_kind": item.get("report_kind", "verified_report"),
        "expected": {
            "domain": domain,
            "subdomain": item.get("subdomain", ""),
            "report_kind": report_kind,
            "level": level,
            "score_range": level_to_score.get(level, [1, 10]),
            "entities": item.get("entities_mentioned", []),
        },
        "human_verified": True,
    }


def main(
    limit: int = typer.Option(20, help="Number of items to label"),
    level_filter: str = typer.Option("", help="Filter by level (red,orange,yellow)"),
) -> None:
    """Interactively label items from the database and extend golden set."""
    if not ping():
        print("ERROR: Database unavailable. Cannot proceed.")
        raise typer.Exit(1)

    log.info("label_start", limit=limit)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    # Get items from DB (simple heuristic: items without classification yet)
    items = get_items_for_stage("classify", limit=limit)

    if not items:
        print("No items to label.")
        return

    labeled_count = 0
    golden_path = GOLDEN_DIR / "classify_triage.jsonl"

    for item in items:
        print(format_item_for_display(item))

        # Prompt for labels
        domain = prompt_for_domain()
        if not domain:
            print("Skipping item.")
            continue

        report_kind = prompt_for_report_kind()
        if not report_kind:
            print("Skipping item.")
            continue

        level = prompt_for_level()
        if not level:
            print("Skipping item.")
            continue

        # Convert to golden format and append
        golden_item = item_to_golden(item, domain, level, report_kind)

        with open(golden_path, "a") as f:
            f.write(json.dumps(golden_item) + "\n")

        labeled_count += 1
        print("✓ Item labeled and saved.")

    print(f"\n{labeled_count} items labeled and appended to {golden_path}")
    log.info("label_complete", labeled=labeled_count)


if __name__ == "__main__":
    typer.run(main)
