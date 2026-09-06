#!/usr/bin/env python
"""Model bake-off over the QA golden set (docs/qa/loop/golden_items.json) -- measures how much of
the D1/D2 content-quality gap (docs/qa/loop/round_1_judge.md) is the *model*, not the prompts, and
recommends the zero-API-cost configuration that maximises the loop score.

For each candidate in ``eoa.qa.bakeoff.CANDIDATES`` (filtered to what's actually installed/
authenticated on this machine): run classify -> triage -> analyze on a stratified item sample
(unchanged prompts/schemas, see ``eoa.qa.bakeoff.candidate_context``), plus a handful of the fixed
"ask the analyst" questions (``docs/qa/loop/golden_questions.json``), never persisting anything.
Then score each candidate's outputs with:
  - deterministic checks (D1/D2, reused from ``eoa.qa.d1_classify``/``d2_summary`` -- no LLM, no DB)
  - a blind rubric judge (anonymised candidate labels, ``eoa.qa.bakeoff.blind_judge_item``)
and writes ``docs/qa/loop/bakeoff/*.json`` (raw, per candidate) plus
``docs/qa/loop/bakeoff/summary.json`` and ``docs/qa/loop/BAKEOFF.md``.

Item source: tries the live DB (``eoa.db``, read-only, the exact golden ids in
``golden_items.json``) first; if the DB is unreachable, falls back to a stratified SYNTHETIC
fixture (``docs/qa/loop/bakeoff/synthetic_items.json``, generated once and reused) so the harness
still produces real signal -- every output this script writes is tagged with which source was
used (``item_source``: "db" | "synthetic_fixture") so nothing is silently passed off as a real
golden-item result. See docs/qa/loop/BAKEOFF.md's "Methodology / limitations" section.

Usage:
    PYTHONPATH=agent python scripts/bakeoff_golden.py --items-limit 6 --candidates dictalm3_12b,gemma4_e4b
    PYTHONPATH=agent python scripts/bakeoff_golden.py --items-limit 20 --candidates all --questions-limit 4
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parent.parent
_AGENT_DIR = _ROOT / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

_LOOP_DIR = _ROOT / "docs" / "qa" / "loop"
_BAKEOFF_DIR = _LOOP_DIR / "bakeoff"
_GOLDEN_ITEMS_PATH = _LOOP_DIR / "golden_items.json"
_GOLDEN_QUESTIONS_PATH = _LOOP_DIR / "golden_questions.json"
_SYNTHETIC_ITEMS_PATH = _BAKEOFF_DIR / "synthetic_items.json"

from eoa import db  # noqa: E402
from eoa.qa.bakeoff import (  # noqa: E402
    CANDIDATES,
    Candidate,
    blind_judge_item,
    deterministic_checks_for_candidate,
    run_chat_for_question,
    run_pipeline_for_item,
)

# ---------------------------------------------------------------------------------------------
# Item sourcing: real DB (read-only) or a stratified synthetic fallback
# ---------------------------------------------------------------------------------------------

#: A small, hand-written, stratified fixture standing in for the real golden items when the DB is
#: unreachable -- red/orange/yellow/archive coverage plus 3 Israeli-relevant stories, all EO/IR/CV
#: defense-news-shaped text so classify/triage/analyze exercise the same taxonomy paths the real
#: golden set would. Clearly synthetic (no real source_url); never written to the DB.
_SYNTHETIC_FIXTURE: list[dict[str, Any]] = [
    {
        "id": "syn-1", "title": "US Army awards $310M for next-gen cooled MWIR targeting pods",
        "lang": "en", "url": "https://example-defense-news.test/syn-1", "source_name": "DefenseNewsWire",
        "clean_text": (
            "The US Army awarded a $310 million contract to L3Harris Technologies to supply "
            "next-generation cooled mid-wave infrared (MWIR) targeting pods for the AH-64E Apache "
            "fleet, the Pentagon announced Tuesday. The pods integrate a new focal plane array "
            "(FPA) with 3x the resolution of the current system and an onboard edge-AI automatic "
            "target recognition (ATR) module. Deliveries begin in Q3 2027. Boeing and Lockheed "
            "Martin also bid but were not selected."
        ),
        "expected_stratum": "red",
    },
    {
        "id": "syn-2", "title": "Elbit Systems unveils SkyGuard C-UAS multi-sensor suite at DSEI",
        "lang": "en", "url": "https://example-defense-news.test/syn-2", "source_name": "DSEI Daily",
        "clean_text": (
            "Elbit Systems showcased its new SkyGuard counter-UAS (C-UAS) suite at DSEI London, "
            "combining an EO/IR tracker, X-band radar, and RF direction-finding for layered drone "
            "detection. The company said SkyGuard has been selected for evaluation by an "
            "unnamed Gulf customer, with a demonstration planned for early 2027. No contract value "
            "was disclosed."
        ),
        "expected_stratum": "orange",
    },
    {
        "id": "syn-3", "title": "Israel's Rafael reports record order backlog led by Iron Beam laser system",
        "lang": "en", "url": "https://example-defense-news.test/syn-3", "source_name": "Globes",
        "clean_text": (
            "Rafael Advanced Defense Systems reported a record $18 billion order backlog in its "
            "quarterly filing, driven largely by continued production of the Iron Beam high-energy "
            "laser air-defense system for the Israeli Ministry of Defense and a new export deal "
            "with an undisclosed European country. Rafael's CEO said Iron Beam batteries are now "
            "operationally integrated alongside Iron Dome at three sites."
        ),
        "expected_stratum": "red", "israeli": True,
    },
    {
        "id": "syn-4", "title": "Naval optronic mast supplier Safran wins French frigate upgrade",
        "lang": "en", "url": "https://example-defense-news.test/syn-4", "source_name": "Naval News",
        "clean_text": (
            "Safran Electronics & Defense will supply its Series 25 optronic mast, combining a "
            "cooled MWIR imager and a low-light TV channel, for the French Navy's FREMM frigate "
            "mid-life upgrade programme, under a EUR 42 million contract signed this week."
        ),
        "expected_stratum": "orange",
    },
    {
        "id": "syn-5", "title": "Academic paper proposes lightweight thermal ATR for edge TPUs",
        "lang": "en", "url": "https://example-defense-news.test/syn-5", "source_name": "arXiv",
        "clean_text": (
            "We propose a lightweight thermal automatic target recognition (ATR) architecture "
            "based on a pruned YOLOv9 backbone, achieving 93% mAP on the DSIAC dataset while "
            "running at 55 FPS on a Coral edge TPU. The model is 40% smaller than the baseline "
            "with a 2-point mAP drop, evaluated only in simulation; no field trial was conducted."
        ),
        "expected_stratum": "yellow",
    },
    {
        "id": "syn-6", "title": "Border-town council debates new streetlight contract",
        "lang": "en", "url": "https://example-defense-news.test/syn-6", "source_name": "Local Gazette",
        "clean_text": (
            "The border town council voted 5-2 to approve a new municipal streetlight contract "
            "with a local electrical firm, citing energy savings. The contract does not involve "
            "any surveillance, sensor, or defense-related equipment."
        ),
        "expected_stratum": "archive",
    },
    {
        "id": "syn-7", "title": "Anduril's Lattice platform adds new EO/IR sensor fusion module for base defense",
        "lang": "en", "url": "https://example-defense-news.test/syn-7", "source_name": "Breaking Defense",
        "clean_text": (
            "Anduril Industries announced a new sensor-fusion module for its Lattice command-and-"
            "control platform, combining EO/IR tower feeds with radar tracks for base-defense "
            "customers. The company said the module is already deployed at two US installations "
            "under a classified contract whose value was not disclosed."
        ),
        "expected_stratum": "red",
    },
    {
        "id": "syn-8", "title": "South Korean firm Hanwha to co-develop counter-drone laser with local university",
        "lang": "en", "url": "https://example-defense-news.test/syn-8", "source_name": "Yonhap",
        "clean_text": (
            "Hanwha Systems signed a memorandum of understanding with a South Korean university "
            "to jointly research a compact counter-drone laser effector, targeting a demonstrator "
            "within two years. No production contract exists yet."
        ),
        "expected_stratum": "yellow",
    },
    {
        "id": "syn-9", "title": "IAI's Green Rock program tests AI-cued EO seeker for loitering munitions",
        "lang": "en", "url": "https://example-defense-news.test/syn-9", "source_name": "Jane's",
        "clean_text": (
            "Israel Aerospace Industries (IAI) completed a test campaign of its Green Rock "
            "loitering munition, featuring an AI-cued electro-optical (EO) seeker capable of "
            "re-targeting mid-flight. IAI said the system will enter low-rate production for the "
            "Israeli Air Force in 2027, with export approval pending from the Ministry of Defense."
        ),
        "expected_stratum": "red", "israeli": True,
    },
    {
        "id": "syn-10", "title": "Opinion: why defense budgets should prioritize cyber over sensors",
        "lang": "en", "url": "https://example-defense-news.test/syn-10", "source_name": "Op-Ed Weekly",
        "clean_text": (
            "In this opinion piece, the author argues that national defense budgets over-invest "
            "in physical sensor systems relative to cyber capabilities, without citing any "
            "specific program, contract, or EO/IR technology."
        ),
        "expected_stratum": "archive",
    },
    {
        "id": "syn-11", "title": "Turkish firm Aselsan reveals new gimbal for armed UAVs",
        "lang": "en", "url": "https://example-defense-news.test/syn-11", "source_name": "Defense Turkey",
        "clean_text": (
            "Aselsan unveiled a new lightweight gimbal (CATS-mini) for small armed UAVs, combining "
            "a SWIR imager and laser designator in a 4kg package, at IDEF. Series production is "
            "expected to start in 2026 for the Turkish Armed Forces, with export interest from two "
            "unnamed Gulf states."
        ),
        "expected_stratum": "orange",
    },
    {
        "id": "syn-12", "title": "Startup claims breakthrough hyperspectral sensor, no peer review yet",
        "lang": "en", "url": "https://example-defense-news.test/syn-12", "source_name": "TechCrunch",
        "clean_text": (
            "A Bay Area startup claims a breakthrough low-cost hyperspectral imaging sensor for "
            "defense and agriculture applications, in a press release with no peer-reviewed data, "
            "no named defense customer, and no specified delivery timeline."
        ),
        "expected_stratum": "yellow",
    },
    {
        "id": "syn-13", "title": "Israel Ministry of Defense issues RFI for next-gen night-vision goggles",
        "lang": "en", "url": "https://example-defense-news.test/syn-13", "source_name": "IsraelDefense",
        "clean_text": (
            "Israel's Ministry of Defense issued a request for information (RFI) for next-"
            "generation binocular night-vision goggles with fused EO/IR channels, seeking industry "
            "responses by Q1 2027. The RFI specifies a target weight under 700 grams."
        ),
        "expected_stratum": "orange", "israeli": True,
    },
    {
        "id": "syn-14", "title": "City council approves new library funding",
        "lang": "en", "url": "https://example-defense-news.test/syn-14", "source_name": "Metro Times",
        "clean_text": "The city council approved $2M in new funding for the downtown public library renovation, with no relation to defense or surveillance technology.",
        "expected_stratum": "archive",
    },
    {
        "id": "syn-15", "title": "Rheinmetall Skyranger 30 selected for German Army short-range air defense",
        "lang": "en", "url": "https://example-defense-news.test/syn-15", "source_name": "ESD Magazine",
        "clean_text": (
            "Rheinmetall's Skyranger 30 counter-UAS/short-range air defense system, combining a "
            "30mm airburst cannon with an EO/IR tracker and AESA radar, has been formally selected "
            "by the German Bundeswehr for its NNbS programme, with an initial order of 19 systems "
            "worth EUR 595 million."
        ),
        "expected_stratum": "red",
    },
    {
        "id": "syn-16", "title": "Small drone maker publishes blog post about camera stabilization",
        "lang": "en", "url": "https://example-defense-news.test/syn-16", "source_name": "DroneBlog",
        "clean_text": (
            "A consumer drone maker published a blog post about its new gimbal stabilization "
            "algorithm for hobbyist photography drones, with no defense or military application "
            "mentioned."
        ),
        "expected_stratum": "yellow",
    },
    {
        "id": "syn-17", "title": "Leonardo DRS wins US Army contract for vehicle-mounted thermal sights",
        "lang": "en", "url": "https://example-defense-news.test/syn-17", "source_name": "Army Times",
        "clean_text": (
            "Leonardo DRS was awarded a $128 million contract to supply Family of Weapon Sight-"
            "Individual thermal sights for the US Army, integrating an uncooled microbolometer "
            "and laser rangefinder. First deliveries are scheduled for late 2026."
        ),
        "expected_stratum": "orange",
    },
    {
        "id": "syn-18", "title": "Elbit Systems and Israel's Ministry of Defense sign SPECTRO ISR follow-on",
        "lang": "en", "url": "https://example-defense-news.test/syn-18", "source_name": "Globes",
        "clean_text": (
            "Elbit Systems signed a follow-on contract with the Israeli Ministry of Defense for "
            "additional SPECTRO ISR reconnaissance pods, combining EO/IR and SIGINT payloads for "
            "manned and unmanned platforms, valued at roughly $95 million, the company said."
        ),
        "expected_stratum": "red", "israeli": True,
    },
    {
        "id": "syn-19", "title": "Patent filing describes event-based vision sensor for low-light tracking",
        "lang": "en", "url": "https://example-defense-news.test/syn-19", "source_name": "USPTO filing summary",
        "clean_text": (
            "A newly published patent application describes an event-based vision sensor "
            "architecture claimed to improve low-light target tracking latency by an order of "
            "magnitude versus frame-based imagers; the filing names a US defense contractor as "
            "assignee but does not disclose a fielded product."
        ),
        "expected_stratum": "yellow",
    },
    {
        "id": "syn-20", "title": "Museum reopens after renovation, unrelated to defense sector",
        "lang": "en", "url": "https://example-defense-news.test/syn-20", "source_name": "Arts Weekly",
        "clean_text": "A regional art museum reopened after an 18-month renovation funded by private donors, with no connection to defense, surveillance, or sensor technology.",
        "expected_stratum": "archive",
    },
]


def _load_or_write_synthetic_items() -> list[dict[str, Any]]:
    if _SYNTHETIC_ITEMS_PATH.exists():
        return json.loads(_SYNTHETIC_ITEMS_PATH.read_text(encoding="utf-8"))
    _BAKEOFF_DIR.mkdir(parents=True, exist_ok=True)
    _SYNTHETIC_ITEMS_PATH.write_text(
        json.dumps(_SYNTHETIC_FIXTURE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return _SYNTHETIC_FIXTURE


#: Explicit column list (never ``SELECT *``): excludes ``embedding`` (huge float array, useless
#: for classify/triage/analyze) and casts every timestamp to text so the raw row is JSON-safe
#: without a custom encoder.
_ITEM_COLUMNS = """
    id, url, title, lang, published_at::text AS published_at, clean_text, source_name,
    domain, subdomain, geography, report_kind, trl, entities_mentioned, score, level,
    triage_reason, summary_he, so_what_he, key_facts, uncertainty_he, tech_maturity,
    tech_actor_kind, tech_readiness_note_he, content_status, israel_relevance, israel_reasons
"""


def _stratify_golden_ids(rows: dict[int, dict[str, Any]], golden_ids: list[int], limit: int) -> list[int]:
    """Stratified subset per the task brief: all red, up to 6 orange, up to 4 yellow, up to 4
    archive/out_of_scope, ensuring at least 3 Israel-relevant items are present -- computed from
    the golden items' OWN already-triaged ``level``/``domain``/``israel_relevance`` fields (this
    is just picking a representative subset to re-run from scratch, not trusting those fields as
    ground truth for the bake-off itself)."""
    ordered = [i for i in golden_ids if i in rows]

    def _is_israeli(r: dict[str, Any]) -> bool:
        return bool((r.get("israel_relevance") or 0) >= 0.5 or r.get("israel_reasons"))

    def _bucket(r: dict[str, Any]) -> str:
        level = r.get("level")
        if level in ("red", "orange", "yellow"):
            return level
        return "archive"  # archive, out_of_scope, or never-triaged (level is None)

    buckets: dict[str, list[int]] = {"red": [], "orange": [], "yellow": [], "archive": []}
    for i in ordered:
        buckets[_bucket(rows[i])].append(i)

    targets = {"red": len(buckets["red"]), "orange": 6, "yellow": 4, "archive": 4}
    selected: list[int] = []
    for name, n in targets.items():
        selected.extend(buckets[name][:n])
    selected = selected[:limit] if limit < len(selected) else selected

    israeli_selected = [i for i in selected if _is_israeli(rows[i])]
    if len(israeli_selected) < 3:
        pool = [i for i in ordered if _is_israeli(rows[i]) and i not in selected]
        need = 3 - len(israeli_selected)
        for i in pool[:need]:
            if len(selected) >= limit:
                selected[-1] = i  # swap out the last pick to make room
            else:
                selected.append(i)

    # backfill to `limit` from whatever's left, if the buckets above didn't reach it
    if len(selected) < limit:
        remaining = [i for i in ordered if i not in selected]
        selected.extend(remaining[: limit - len(selected)])

    return sorted(set(selected))[:limit]


def load_items(limit: int) -> tuple[list[dict[str, Any]], str]:
    """Returns (items, source) where source is "db" or "synthetic_fixture"."""
    golden_ids: list[int] = []
    if _GOLDEN_ITEMS_PATH.exists():
        golden_ids = json.loads(_GOLDEN_ITEMS_PATH.read_text(encoding="utf-8")).get("item_ids", [])

    if db.ping() and golden_ids:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT {_ITEM_COLUMNS} FROM items WHERE id = ANY(%s)", (golden_ids,))
            rows = {r["id"]: dict(r) for r in cur.fetchall()}
        if rows:
            ids = _stratify_golden_ids(rows, golden_ids, limit)
            items = [rows[i] for i in ids]
            if items:
                return items, "db"
    print(
        "[bakeoff] DB unreachable (or golden items missing) -- falling back to the stratified "
        "SYNTHETIC fixture (docs/qa/loop/bakeoff/synthetic_items.json). Results are tagged "
        "item_source=synthetic_fixture; see docs/qa/loop/BAKEOFF.md for why.",
        file=sys.stderr,
    )
    items = _load_or_write_synthetic_items()
    return items[:limit], "synthetic_fixture"


def load_questions(limit: int) -> list[str]:
    if not _GOLDEN_QUESTIONS_PATH.exists():
        return []
    qs = json.loads(_GOLDEN_QUESTIONS_PATH.read_text(encoding="utf-8")).get("questions", [])
    return qs[:limit]


# ---------------------------------------------------------------------------------------------
# GPU sampling (ollama candidates only)
# ---------------------------------------------------------------------------------------------


class GpuPoller(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.running = False
        self.peak_mem_mb = 0

    def run(self) -> None:
        self.running = True
        while self.running:
            try:
                out = subprocess.check_output(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    text=True, stderr=subprocess.STDOUT, timeout=5,
                )
                mem = int(out.strip().splitlines()[0])
                self.peak_mem_mb = max(self.peak_mem_mb, mem)
            except Exception:
                pass
            time.sleep(1)

    def stop(self) -> None:
        self.running = False
        self.join(timeout=3)


# ---------------------------------------------------------------------------------------------
# Availability filtering
# ---------------------------------------------------------------------------------------------


def _ollama_available_models() -> set[str]:
    try:
        out = subprocess.check_output(["ollama", "list"], text=True, timeout=15)
        return {line.split()[0] for line in out.splitlines()[1:] if line.strip()}
    except Exception:
        return set()


def filter_available(candidates: list[Candidate]) -> list[Candidate]:
    from eoa.config import settings as _settings
    from eoa.llm.providers.cli import CliProvider

    installed = _ollama_available_models()
    reg = _settings().registry.models
    available: list[Candidate] = []
    for c in candidates:
        if c.kind == "ollama":
            spec = reg.get(c.model_key) if c.model_key else None
            if spec and spec.ollama and (not installed or spec.ollama in installed):
                available.append(c)
            else:
                print(f"[bakeoff] skip {c.name}: ollama model not installed", file=sys.stderr)
        else:
            kind = (c.provider or "").split(":", 1)[0]
            if CliProvider(kind).is_available():
                available.append(c)
            else:
                print(f"[bakeoff] skip {c.name}: CLI '{kind}' not on PATH", file=sys.stderr)
    return available


# ---------------------------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------------------------


def run_sweep(
    candidates: list[Candidate], items: list[dict[str, Any]], questions: list[str], item_source: str
) -> dict[str, Any]:
    _BAKEOFF_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"item_source": item_source, "n_items": len(items), "candidates": {}}

    for candidate in candidates:
        print(f"[bakeoff] === {candidate.name} ({candidate.label}) ===", file=sys.stderr)
        poller = GpuPoller()
        if candidate.kind == "ollama":
            poller.start()

        item_results = []
        stage_latencies: dict[str, list[int]] = {"classify": [], "triage": [], "analyze": []}
        stage_failures: dict[str, int] = {"classify": 0, "triage": 0, "analyze": 0}
        for item in items:
            res = run_pipeline_for_item(item, candidate)
            item_results.append(res)
            for stage_name in ("classify", "triage", "analyze"):
                sr = getattr(res, stage_name)
                if sr is None:
                    continue
                stage_latencies[stage_name].append(sr.latency_ms)
                if not sr.ok:
                    stage_failures[stage_name] += 1
            print(
                f"[bakeoff]   item {res.item_id}: classify={res.classify.ok if res.classify else None} "
                f"triage={res.triage.ok if res.triage else None} analyze={res.analyze.ok if res.analyze else None}",
                file=sys.stderr,
            )

        chat_results = [
            run_chat_for_question(q, candidate) for q in questions
        ]

        if candidate.kind == "ollama":
            poller.stop()

        merged_items = [r.merged_item for r in item_results]
        det = deterministic_checks_for_candidate(merged_items)

        (_BAKEOFF_DIR / f"{candidate.name}.json").write_text(
            json.dumps(
                {
                    "candidate": candidate.name,
                    "label": candidate.label,
                    "item_source": item_source,
                    "items": [r.to_dict() for r in item_results],
                    "chat": [c.to_dict() for c in chat_results],
                    "deterministic": {k: v.to_dict() for k, v in det.items()},
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        n = max(len(items), 1)
        summary["candidates"][candidate.name] = {
            "label": candidate.label,
            "kind": candidate.kind,
            "cost_note": candidate.cost_note,
            "failure_rate": {
                stage: round(stage_failures[stage] / n, 3) for stage in stage_failures
            },
            "median_latency_ms": {
                stage: (statistics.median(v) if v else None) for stage, v in stage_latencies.items()
            },
            "deterministic_D1": det["D1"].score_0_100,
            "deterministic_D2": det["D2"].score_0_100,
            "peak_vram_mb": poller.peak_mem_mb if candidate.kind == "ollama" else None,
            "chat_completion_rate": round(
                sum(1 for c in chat_results if c.ok) / max(len(chat_results), 1), 3
            ),
            "chat_median_latency_ms": (
                statistics.median([c.latency_ms for c in chat_results if c.ok])
                if any(c.ok for c in chat_results) else None
            ),
        }

    return summary


def run_judge(candidates: list[Candidate], judge_provider: str) -> dict[str, Any]:
    """Blind rubric judge over every item every loaded candidate JSON has in common."""
    from eoa.qa.bakeoff import JudgeItemScore  # noqa: F401  (typing reference for readers)

    per_candidate_data = {}
    for c in candidates:
        p = _BAKEOFF_DIR / f"{c.name}.json"
        if p.exists():
            per_candidate_data[c.name] = json.loads(p.read_text(encoding="utf-8"))

    if len(per_candidate_data) < 2:
        return {"skipped": "fewer than 2 candidates with results -- nothing to compare blindly"}

    # index items by item_id across candidates
    item_ids = [r["item_id"] for r in next(iter(per_candidate_data.values()))["items"]]
    judge_scores: dict[str, list[int]] = {name: [] for name in per_candidate_data}
    examples: list[dict[str, Any]] = []

    for idx, item_id in enumerate(item_ids):
        outputs = {}
        source_item = None
        for name, data in per_candidate_data.items():
            row = data["items"][idx]
            if row["analyze"] and row["analyze"]["ok"]:
                merged = row["merged_item"]
                outputs[name] = {
                    "domain": merged.get("domain"), "subdomain": merged.get("subdomain"),
                    "level": merged.get("level"), "score": merged.get("score"),
                    "triage_reason": merged.get("triage_reason"), "summary_he": merged.get("summary_he"),
                    "so_what_he": merged.get("so_what_he"), "key_facts": merged.get("key_facts"),
                    "entities_mentioned": merged.get("entities_mentioned"),
                }
                source_item = merged
        if len(outputs) < 2 or source_item is None:
            continue
        try:
            scored = blind_judge_item(source_item, outputs, judge_provider=judge_provider)
        except Exception as exc:
            print(f"[bakeoff] judge failed on item {item_id}: {exc}", file=sys.stderr)
            continue
        for name, s in scored.items():
            judge_scores[name].append(s.score_0_100)
        if len(examples) < 3:
            examples.append(
                {
                    "item_id": item_id,
                    "outputs": outputs,
                    "judge_scores": {n: s.score_0_100 for n, s in scored.items()},
                    "judge_notes": {n: s.notes for n, s in scored.items()},
                }
            )

    return {
        "judge_provider": judge_provider,
        "mean_score": {
            name: (round(statistics.mean(v), 1) if v else None) for name, v in judge_scores.items()
        },
        "n_judged": {name: len(v) for name, v in judge_scores.items()},
        "examples": examples,
    }


def write_bakeoff_md(summary: dict[str, Any], judge: dict[str, Any]) -> None:
    lines = ["# Bake-off: candidate models at zero API cost", ""]
    lines.append(
        f"Item source: **{summary['item_source']}** (n={summary['n_items']}). "
        "See docs/qa/loop/BAKEOFF.md's own prose section (authored separately) for the full "
        "methodology note and recommendation -- this is the auto-generated table + judge section."
    )
    lines.append("")
    lines.append(
        "| candidate | kind | cost | D1 det. | D2 det. | judge mean | classify ms (median) | "
        "triage ms | analyze ms | fail rate (c/t/a) | peak VRAM MB | chat completion | chat ms |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, c in summary["candidates"].items():
        ml = c["median_latency_ms"]
        fr = c["failure_rate"]
        jm = judge.get("mean_score", {}).get(name)
        lines.append(
            f"| {name} | {c['kind']} | {c['cost_note']} | {c['deterministic_D1']} | "
            f"{c['deterministic_D2']} | {jm if jm is not None else 'n/a'} | "
            f"{ml['classify']} | {ml['triage']} | {ml['analyze']} | "
            f"{fr['classify']}/{fr['triage']}/{fr['analyze']} | {c['peak_vram_mb']} | "
            f"{c['chat_completion_rate']} | {c['chat_median_latency_ms']} |"
        )

    lines.append("")
    lines.append(f"Judge provider: {judge.get('judge_provider', 'n/a')}")
    lines.append("")
    lines.append("## Example items (blind judge)")
    for ex in judge.get("examples", []):
        lines.append(f"### item {ex['item_id']}")
        lines.append(f"judge scores: {json.dumps(ex['judge_scores'], ensure_ascii=False)}")
        for name, out in ex["outputs"].items():
            lines.append(f"- **{name}**: level={out.get('level')} score={out.get('score')} "
                         f"summary_he={out.get('summary_he')!r}")
        lines.append("")

    auto_path = _BAKEOFF_DIR / "AUTO_TABLE.md"
    auto_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[bakeoff] wrote {auto_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--items-limit", type=int, default=20)
    p.add_argument("--questions-limit", type=int, default=4)
    p.add_argument(
        "--candidates", default="all",
        help="comma-separated candidate names (see eoa.qa.bakeoff.CANDIDATES) or 'all'",
    )
    p.add_argument("--judge-provider", default="claude:claude-sonnet-5")
    p.add_argument("--skip-judge", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    by_name = {c.name: c for c in CANDIDATES}
    if args.candidates == "all":
        all_candidates = CANDIDATES
    else:
        names = [n.strip() for n in args.candidates.split(",") if n.strip()]
        unknown = [n for n in names if n not in by_name]
        if unknown:
            print(f"[bakeoff] unknown candidate name(s): {unknown}", file=sys.stderr)
            sys.exit(1)
        all_candidates = [by_name[n] for n in names]
    candidates = filter_available(all_candidates)
    if not candidates:
        print("[bakeoff] no candidates available -- nothing to do", file=sys.stderr)
        sys.exit(1)
    print(f"[bakeoff] candidates: {[c.name for c in candidates]}", file=sys.stderr)

    items, item_source = load_items(args.items_limit)
    questions = load_questions(args.questions_limit)
    print(f"[bakeoff] {len(items)} items ({item_source}), {len(questions)} chat questions", file=sys.stderr)

    summary = run_sweep(candidates, items, questions, item_source)

    judge = {} if args.skip_judge else run_judge(candidates, args.judge_provider)

    _BAKEOFF_DIR.mkdir(parents=True, exist_ok=True)
    (_BAKEOFF_DIR / "summary.json").write_text(
        json.dumps({"summary": summary, "judge": judge}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    write_bakeoff_md(summary, judge)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
