# Golden Set Format

This directory contains golden-standard evaluation datasets for the EO-analyst classify/triage pipeline.

## File: `classify_triage.jsonl`

JSONL format (one JSON object per line). Each item represents a news/research snippet to be classified and triaged.

### Fields

**Input (evaluation input):**
- `id` (int, negative): Unique identifier (negative to avoid collisions with real DB)
- `title` (str): Article/document title
- `text` (str): 120–250 word body text (this becomes `clean_text`)
- `lang` (str): `"en"`, `"he"`, `"ru"`, or other ISO 639-1 code
- `source_kind` (str): `"news"`, `"company_pr"`, `"academic"`, `"tender"`, `"rumor"`, `"patent"` etc.

**Expected Outputs (ground truth for evaluation):**
- `expected` (object):
  - `domain` (str): One of `airborne_pods`, `land_surveillance`, `naval_surveillance`, `air_defense`, `c_uas`, `computer_vision`, `secondary`, `out_of_scope`
  - `subdomain` (str): Specific sub-domain key or empty
  - `report_kind` (str): `verified_report`, `company_pr`, `rumor_speculation`, `academic`, `tender`, `patent`, `regulatory`
  - `level` (str): `red`, `orange`, `yellow`, or `archive` (triage importance)
  - `score_range` (list[int]): Expected score min/max (1–10), e.g. [8, 10] for red
  - `entities` (list[str]): Expected entity names (case-insensitive substring match)

**Metadata:**
- `human_verified` (bool): `false` for initial draft, `true` after user validation

### Example

```json
{
  "id": -1,
  "title": "Elbit Systems Secures $150M Targeting Pod Upgrade Contract",
  "text": "Elbit Systems announced today that the Israeli Defense Ministry has selected its advanced LITENING targeting pod for the next-generation F-16I fleet modernization...",
  "lang": "en",
  "source_kind": "company_pr",
  "expected": {
    "domain": "airborne_pods",
    "subdomain": "targeting_pods",
    "report_kind": "company_pr",
    "level": "red",
    "score_range": [8, 10],
    "entities": ["Elbit Systems", "Israeli Defense Ministry", "F-16I"]
  },
  "human_verified": false
}
```

### Evaluation Metrics

The `run_evals.py` script computes:

- **Domain accuracy**: exact match on `domain`
- **Report kind accuracy**: exact match on `report_kind`
- **Level exact match**: exact match on `level` (red/orange/yellow/archive)
- **Level ordinal**: level is within ±1 ordinal distance (red=3, orange=2, yellow=1, archive=0)
- **Score in range**: model's predicted score falls within `expected.score_range`
- **Entity recall**: each expected entity is found (case-insensitive substring)
- **Mean latency**: wall-clock time per item

All metrics are aggregated per `source_kind`, `lang`, and globally.

### Taxonomy Keys

**Domains:**
- `airborne_pods` (targeting pods, ISR pods, UAV gimbals, micro-gimbals, EO warfare, strategic ISR)
- `land_surveillance` (border towers, mobile observation, sights, night vision, perimeter security)
- `naval_surveillance` (naval directors, coastal, optronic masts, USV payloads)
- `air_defense` (IIR seekers, EO trackers, sensor fusion, HEL)
- `c_uas` (detect/track, multi-sensor, effectors, convoy/site, counter-FPV)
- `computer_vision` (ATR, edge AI, GPS-denied nav, real-time video, multimodal ISR, sim2real)
- `secondary` (detectors/FPA, computational optics, defense AI infra)
- `out_of_scope` (cyber-only, unrelated platforms, non-defense topics)

**Triage levels:**
- `red`: score ≥ 8 (big contract awards, M&A of watchlist companies)
- `orange`: score 6–7 (important news, notable partnerships)
- `yellow`: score 4–5 (background info, minor developments)
- `archive`: score 1–3 (out of scope, rumors, dated info)

### Use Cases

1. **`run_evals.py --set classify_triage`**: Runs classification and triage on all golden items, reports metrics.
2. **`label_items.py --limit 20`**: Interactive labeling of real DB items to extend the golden set.
3. **Continuous validation**: Before deployment, ensure all metrics pass thresholds (e.g., domain accuracy ≥ 85%, red level precision ≥ 90%).
