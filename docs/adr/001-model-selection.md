# ADR-001: Model selection for 12 GB VRAM (bake-off 2026-09-04)

**Status:** accepted — revisit after the 7-night pilot with the golden-set evals (evals/run_evals.py)

## Context
RTX 5070 Ti Laptop, 12,227 MiB VRAM. Only Western-origin open-weight models. Hebrew output quality (NFR-4/11),
reliable JSON-schema output, and native tool calling for the deep-search ReAct loop are the three hard requirements.
Bake-off: `scripts/bakeoff.py` → `evals/results/bakeoff_20260904_1514.md` (8 classify snippets, 4 Hebrew summaries,
2 ReAct scenarios incl. one fictitious event, 1 Hebrew rewrite; VRAM/temperature sampled every second).

## Results

| model | tok/s (classify) | JSON valid | domain acc* | tok/s (he) | Hebrew ratio | ReAct finished | honest on fake event | peak VRAM | CPU offload |
|---|---|---|---|---|---|---|---|---|---|
| gemma4:12b (Google) | 34.3 | 100% | 50% | 48.8 | 0.79 | yes | yes (text, no `finish` call) | 9,680 MB | no |
| DictaLM-3.0-Nemotron-12B (Dicta/NVIDIA base) | 45.1 | 100% | 75% | 50.3 | **0.90** | no (narrates, then stops) | yes (text) | 8,908 MB | no |
| gemma4:26b (MoE A4B) | 28.5 | 100% | 50% | 44.6 | 0.77 | no (loops on search) | yes (text) | 11,271 MB | **yes** |

\* domain accuracy on a minimal prompt without the taxonomy — indicative only; the production prompt carries the taxonomy.

Qualitative: on the "rewrite as senior-analyst Hebrew" task DictaLM returned exactly one polished paragraph;
both Gemma variants answered with a three-option English-framed essay (instruction following for Hebrew prose is
weaker). Gemma 4 12B was the only model that drove the tool loop to a proper `finish`. gemma4:26b brings no
measurable gain and needs CPU offload (slower, 11.3 GB peak) — rejected as resident.

## Decision
Two 12B models, at most two swaps per night (each ≈ 9 GB, never co-resident):
- **`resident` = DictaLM-3.0-Nemotron-12B-Instruct** — classify, triage, analyze, page summaries in Hebrew, daily
  report drafting, the "ask the analyst" chat. Origin IL, base NVIDIA Nemotron Nano V2 (US). License text to be
  confirmed from the HF model card (recorded as open-weight; personal, non-commercial use here).
- **`investigator` = gemma4:12b** — the deep-search ReAct loop (search/read/finish tools) and the light-weight
  page reader inside it. Apache 2.0.
- `light` = gemma4:e4b (daytime quick answers, eco mode). `embed` = snowflake-arctic-embed2 (1024-d; chosen by a
  Hebrew↔English similarity test: pair 0.535 vs cross 0.284 vs unrelated 0.091; EmbeddingGemma 0.492/0.295/0.269).
- `guard_l1` = protectai/deberta-v3-base-prompt-injection-v2 (CPU); `guard_l2` = granite3-guardian:2b.
- gpt-oss:20b and nemotron-3.5-lightning stay installed as optional `heavy_investigator` candidates (not used).

## Consequences
- The night pipeline orders stages so the swap happens once: [DictaLM: classify→triage] → [Gemma: deep search] →
  [DictaLM: analyze→report]. The resource gate enforces `min_loaded_seconds` to avoid thrashing.
- DictaLM lacks Ollama "thinking"; `think` must not be requested for it (client passes `think=False` only on
  structured calls and never `True`).
- Re-run `evals/run_evals.py --set classify_triage` after the first nights to validate the 75%/50% gap on the
  production prompts; if Gemma wins there, flip `resident`/`hebrew_editor` roles (one-line config change).
