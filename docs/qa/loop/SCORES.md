# QA continuous-loop score trend (docs/QA_CONTINUOUS_LOOP.md)

Auto-generated/updated by `scripts/qa_score.py` (append/replace the row for `--round N`). `auto`
columns are the deterministic score (0-100, or `manual` when a domain has no deterministic signal
this round -- see D5/D10). `judge_merged` is `yes` once `--merge-judge round_N_judge.json` has been
applied for that round (0.5 auto / 0.5 judge blend per docs/QA_CONTINUOUS_LOOP.md section 1);
`total` is the weighted overall score.

| round | date | D1 | D2 | D3 | D4 | D5 | D6 | D7 | D8 | D9 | D10 | judge_merged | total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 2026-09-06 | 33.3 | 100.0 | 100.0 | 81.2 | manual | 82.4 | 71.4 | 100.0 | 80.0 | manual | yes | 56.9 |
| 1 | 2026-09-06 | 87.5 | 85.7 | 100.0 | 100.0 | manual | 82.4 | 50.0 | 100.0 | 80.0 | manual | yes | 61.8 |
| 2 | 2026-09-06 | 87.5 | 85.7 | 100.0 | 100.0 | manual | 82.4 | 50.0 | 100.0 | 80.0 | manual | yes | 63.9 |
| 3 | 2026-09-06 | 70.8 | 100.0 | 100.0 | 100.0 | manual | 100.0 | 50.0 | 81.8 | 100.0 | manual | no | 88.6 |
