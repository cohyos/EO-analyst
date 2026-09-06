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

## D6 — daily/weekly (agent R3-D6, commit 01d9f68)

- Two structured-draft QA failures no longer drop the summary: `_deterministic_fallback_draft`
  builds a labelled ("תקציר מובנה אוטומטית (ללא ניסוח מודל)"), fully cited summary from the top
  items, the day's events and the Israel-relevant items — passes `qa_citations.check` by
  construction. Same machinery in weekly.
- "תעשייה ישראלית — תחרות ומתחרים" requires an IL-watchlist company entity or a business event
  kind; an item whose only Israeli hook is IDF/MoD/IAF is excluded (the opinion piece and the
  Lebanon-ridge item the judge found).
- Source appendix excluded from the D6 duplicate-sentence scan; duplicates elsewhere still fail.
- `textnorm.normalize_draft` (doubled ASCII quotes → gershayim/geresh) on daily/weekly/monthly.

## D7 — BD territory report (agents R3-D7/D9 b29bb61 + B2 954e004 + wiring 18cfe68)

- Conference dates: table already DB-sourced; prose mentions now corrected against the DB by name.
- `textnorm` on every BD prose field and table cell; explicit no-activity marker naming the
  watchlist competitors checked (accepted by the D7 check, contradiction still fails).
- **Structured schema migration** (the judge's #3): `BdTerritoryReportDraft` with cited Sentences
  for exec summary / market bullets / competitor moves / action rationales, typed analyst note;
  one corrective retry then a deterministic cited substitute — the warning-banner path no longer
  exists.
- New data-driven sections: "מעקב רכישות ושותפויות" (A16) and "מחירי ייחוס למטע"דים" (A17).

## D8 — patent survey (agent R3-D8, commit 8e9d97e)

Assignee-coverage caveat + exclusivity-claim scrub below 70% coverage; relationship edges verified
against the cited source text (the Anduril–Elbit "Sigma 155" row is dropped and counted);
timeline/CPC render data rows or an explicit disclosure line (scorer requires one of the two);
synthesis retries + `narrative_pending` marker with a regeneration hook.

## D9 — tenders and sources (b29bb61 + A15 eb3e8c5)

Status redrive for the whole table; open-first ordering; `sources.active` synced with config
(3 disabled-in-config sources and 4 renamed arXiv feeds explained the "stale" 7); A15 added 8
keyless machine-readable portals + 22 regional search sources (docs/TENDER_PORTALS.md).

## D5 — chat (agent R3-D5, pending)

Grounded-entity check, cross-source conflation guard, `[n=…]` template-leak sanitiser, topic
substitution gap-first — see docs/qa/loop/round_3_chat_fixes.md when it lands.

## Correction to the D1 note above (verified 2026-09-06 15:20)

The 18 "stranded" domain-NULL items are **not stuck**: every one carries `dedup_of` (52 → 10,
57 → 50, 84 → 62, twelve blocked-page rows → 177, …) — `run_classify` marks a duplicate's stage
done without classifying it, by design. The judge read "domain NULL + classify done" as a
pipeline failure; it is the duplicate convention. The self-heal tail stays (harmless, and it does
cover a real persist failure), and the UI/judge should read `dedup_of` first. Item 39 was
re-analysed on the new code (summary now "חברת Ophir Optronics … השיקה עדשה חדשה למטע״דים …",
8 key facts, 1 event, 1 edge).

## D2 — corrective pass for generic so_what (Fable, after the prompt change)

Item 39's fresh analysis still produced "מחזקת את מעמדה של תעשייה אווירית" despite the prompt
ban — a 12B-model habit. `analyze_item` now runs one targeted rewrite of `so_what_he` when it
matches a generic formula (morphology-tolerant regexes), keeps the original if the rewrite is
still generic or malformed. 56 rows carry the formula today (40 in scope); they are rewritten
progressively as the nightly backfill re-analyses them.

## Ingest job 94 (14:00) failed with `relational has no attribute deactivate_orphaned_sources`

The long-running orchestrator process had imported the old `relational` module before the D7/D9
agent added the function to the working tree, then lazily imported the new `fetch/service.py`
that calls it. Gone after the 15:15 restart (job 95 re-run queued to confirm).

## Open for round 4 (seen while closing round 3)

- **Cloud-delegated deep search blocked by the L2 guard (job 113, item 6872 — Rafael buys the
  Volkswagen Osnabrück plant for an Iron Dome line).** The delegated claude run did real research
  (Hebrew/English/German queries) but its *answer* tripped the injection heuristic ("התשובה נחסמה
  בבדיקת אבטחה") and the whole investigation returned `not_found` with no sources. The guard should
  score fetched page content, not the delegated model's own answer; on suspicion, drop the
  flagged sentence and keep the rest with a caveat, never the entire answer.
- **Weekly domain keys from the model** ("land_eoir", "cuas", an "out_of_scope" section) — fixed
  in 11e951c (canonicalised + prompt lists the allowed keys); verify on the cloud-drafted weekly
  rebuilt after the restart (job 108).
- **Patent surveys with 0 patents when the keyless search times out** — fixed in 934710b (stored
  patents supplement the search); verify on jobs 109/110.
- **iPhone nav rail overflow + FAB overlap after the payloads entry** (3 e2e failures on
  iphone-safari) — agent in flight.
- **Cloud chain throughput:** weekly = ~30 claude CLI calls at 25–37 s; BD ≈ 5 calls. Nightly
  pipeline on 200 items in batches of 8 → ~25 classify/triage/analyze batch calls; measure the
  first night (2026-09-07 01:00) and compare with the 4 h local baseline.
- **Search provider throttling (evening 2026-09-06):** the day's tender/patent/deep-search
  queries pushed DuckDuckGo into timeouts and Google into "sorry" captcha pages, so the `kind:
  search` tender sources and the patent live search return nothing for hours. Round 4: per-query
  daily cache, exponential backoff per provider, a second keyless provider in rotation
  (Brave/Bing via SearXNG or Startpage), and a per-run query budget so one stage cannot exhaust
  the quota for the rest of the night.
