# QA loop — round 3 fixes (2026-09-06)

Inputs: `round_1_judge.md` (worst list), `round_2_auto.json`, `round_2_judge.md` (pending at the time
these fixes started — the judge was still reading the live DB, so every **data** repair below was
held back until it finished; code landed first).

## D3 — events (Fable, commit 44ac947)

Both judges confirmed the same two defects on the live 5432 DB:

| defect | root cause | fix |
|---|---|---|
| item 81: three Norkin appointments (events 22/221/245), two funding rounds (23/222), two Elbit partnerships (24/223) | the `(item_id, kind, lower(title))` unique index only stops verbatim repeats, and the Q3-6b near-duplicate rule only looked at a *different* kind at 0.9 character similarity — a later analyze pass re-words the same event and lands a new row every time | `eoa.memory.relational.same_kind_duplicate`: same item + same kind merge when the titles are a typo-level character match (≥0.9), share ≥0.6 of their content words (Jaccard/containment blend over Hebrew-prefix-stripped tokens), or are moderately close on both signals (token ≥0.45 and chars ≥0.55). Guards: different numbers ("אופק 19" vs "דור 1", "T-REX 25-2" vs "T-REX 2026"), different Latin proper nouns ("Nexus Observer" vs "Nexus Sentinel"), and conflicting non-empty party lists never merge. Merge now unions parties (item 50 / Palantir) instead of keeping the old list. |
| `amount_usd` stored in millions — events 55 ($540.7), 84/89/121 ($464.8) | the analyze prompt said "copy every number as it appears in the source", so "$464.8 million" became 464.8 | prompt + schema now ask for full units; `eoa.pipeline.analyze.normalize_amount_from_source` anchors any amount < 100 000 to the item text: the same number followed by million/billion/M/bn/מיליון/מיליארד is scaled (464.8 million → 464 800 000); a figure with no magnitude word in the source (Nexus Observer, $1 595) is left alone. |

Repair script `scripts/repair_events_round3.py` (dry-run by default, prints target host:port and
alembic head, refuses 5433). Dry run on 2026-09-06 12:45:

- amounts: 5 examined, 4 scaled (55, 84, 89, 121), 1 left alone (198, a $1 595 unit price).
- duplicates: 16 merges across items 12, 71 (3), 73, 81 (4), 105, 150, 153, 155 (2), 235, 2386 — every
  pair read by hand; the first version of the rule also proposed 4 false merges (two satellites,
  two exercises, two different XTEND MoD agreements, two unrelated "השלכות" sentences) which is
  what produced the number/proper-noun guards and the two-signal weak rule.

Applied: _pending the round-2 judge finishing its read of the live DB — see below_.

Tests: `tests/unit/test_events_round3.py` (46 cases incl. the live false positives as regressions),
`tests/unit/test_events_near_duplicate.py` updated for the widened candidate query.

## D6 / D7 / D9 / D8 — in flight (sonnet repair agents, code only)

See the per-domain sections appended below when each lands.

## D3 repair — applied 2026-09-06 12:58 (after judge J2 finished)

`scripts/repair_events_round3.py --apply` on 127.0.0.1:5432 (alembic 0019): 4 amounts scaled
(55 → 540 700 000; 84/89/121 → 464 800 000; 198 left at 1 595), 16 duplicate rows merged
(185 → 169 events). Verified from a separate connection: item 81 now has 4 events (22 with the
2026-09-01 date pulled from 245, 23, 24 with parties Anduril/Elbit/Elbit Systems, 224).

## D1 — stranded domain-NULL items (Fable)

18 clean items (52, 56, 57, 84, 182, 186, 188, 190, 194, 196, 198, 200, 203, 206, 208, 211, 212,
214) had `classify` in `processed_stages` but `domain IS NULL`; triage skips domain NULL and
classify never revisits a "done" stage, so they were invisible forever. `run_classify` now appends
`get_items_stuck_unclassified()` to its batch (unscoped runs only); they are re-classified on the
next pipeline pass (also triggered explicitly after the restart below).

## D2 — the templated so_what phrase was our own example

`analyze.md` illustrated `so_what_he` with "להערכתנו, המהלך מחזק את מעמדה מול..." — the exact
phrase the judge counted on 32 items DB-wide. The example is gone; the prompt now demands a
concrete beneficiary/loser/change and forbids the generic formulas. Existing rows keep their text
until re-analysed (the nightly key_facts backfill re-runs analyze on them progressively).

## D4 — job 91 regression (Fable)

`scripts/mark_legacy_investigations.py`'s not_found→partial pass is now scoped to job ids 15/20
(`NOT_FOUND_TO_PARTIAL_JOB_IDS`); job 91 restored to `not_found / 0.0` with a `repair_notes` entry.
