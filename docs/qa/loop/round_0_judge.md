# Round 0 -- Independent Judge Report

**Judge:** independent read-only agent (no pipeline runs, no fixes, no OMC tools). DB accessed read-only via `runtime/eoa.env` + psycopg. Live chat tested against the running server on `127.0.0.1:8765`. Source fidelity spot-checked with live `httpx` fetches.

## Scores

| Domain | Score /100 | n | One-line verdict |
|---|---|---|---|
| D1 Classification/triage | 55 | 40 | Reasoning is sound when it runs, but 10% of golden items got no triage output at all; truncation + one invalid taxonomy leaf. |
| D2 Summary/so-what | 58 | 40 | Genuinely faithful and inferential on the best items (verified against source); formulaic phrasing and one truncated item drag it down. |
| D3 Events/entities | 42 | 40 | Verified entity hallucination via a watchlist alias bug; heavy event duplication with inconsistent name spellings; one missing lead entity. |
| D4 Deep investigations | 35 | 6 | 5/6 golden investigations came back "not found"; the one confident answer was on the wrong topic (caught retroactively). Honesty about gaps is a real strength. |
| D5 Chat ("ask the analyst") | 25 | 8 | 4/8 questions timed out; of the 4 that answered, 0 used inline `[n]` citations and all 4 used raw Markdown; one apparent hallucination, one confusing conflation. |
| D6 Daily/weekly reports | 28 | 2 | Weekly report ships literal test/placeholder text as its executive summary and forward-look, plus an ~80-line duplicate feedback-log dump. Daily is tables-only by an honest but total QA-gate failure. |
| D7 BD focus reports (US/IL) | 45 | 2 | IL report has a real summary and an actions table (generic but present); US report's summary is empty and has no actions table at all. One cross-cited fact in IL. |
| D8 Patent survey | 20 | 1 | 16/17 patents unassigned/unrelated to the named target; timeline and CPC tables empty; no per-patent technical narrative anywhere. |
| D9 Tenders/forecasts/conferences | 50 | 27 | Conferences are solid and verified; tenders table is completely empty of open/current items (0 of 5, several from 2015-2016). |

## Evidence log (selected, with sources)

### D1 -- classification
- Items **1, 4, 7, 14**: `domain='out_of_scope'`, `level=NULL`, `score=NULL`, `triage_reason=NULL`, yet `processed_stages` includes `'triage'`. Compare items 2/13/16/17/18 (also out_of_scope) which DO have `level='archive'`, `score=1` and a full reason -- the failure is inconsistent, i.e. a bug, not a designed skip.
- Item **39**: `triage_reason` truncated mid-word: *"העדשה מיועדת למטע"*. Matches deterministic hit-list (items 8, 33, 39, 55).
- Item **24**: `domain='secondary'`, `subdomain=NULL` -- not a valid taxonomy leaf per `config/taxonomy.yaml`.
- Positive: items 5, 8, 24 (all low-scored) show correct application of the EO/IR scope gate -- ships/platforms/financial-trend stories without EO/IR substance are correctly kept low/archive with reasoning that tracks `agent/eoa/llm/prompts/triage.md`'s core_relevance/magnitude/novelty rubric.

### D2 -- summary / so-what
- Item **62** (German Navy LORA firing) verified against `navalnews.com`: summary is faithful (OPEX/Marine 2035, trailer+launcher, precision hit) and the so-what's Arrow-3 connection is genuinely present in the source (IAI Chairman Boaz Levy's quote) -- real analyst inference, not paraphrase. Minor: the "CEO" attribution in the item is closer to what the Chairman actually said.
- Item **39**: summary_he is a truncated fragment, *"עדשה חדשה למטע"* -- unusable.
- Item **10**: `key_facts` repeats the same sentence 3x verbatim.
- Recurring template phrase *"מחזק את מעמדה של X"* appears in the so_what of items 3, 9, 33, 47, 50, 62, 81 -- serviceable but not differentiated per item.

### D3 -- events / entities
- Item **47** (AeroVironment Locust X3 / E-HEL, $464.8M): `entities_mentioned` includes **BlueHalo**, verified absent from the live source (`edrmagazine.eu`). Root cause: `config/watchlist.yaml` lists `LOCUST` as a BlueHalo alias, colliding with AeroVironment's actual "Locust X3" product name. Same spurious entity recurs on items 9, 10, 50.
- Item **50** (TITAN $192M to Palantir+Anduril): `entities_mentioned` = `[US Army, Anduril, BlueHalo]` -- **omits Palantir**, a lead named party in the item's own summary/key_facts.
- Item **81** (Anduril appoints Norkin): the same appointment recorded as **3 duplicate events** (ids 22, 221, 245) with **3 different spellings** of the appointee (Amikam Norkin / Amiram Norkin / עמירם נורקין / אמירם נורקין), plus 2 duplicate funding-round events (23, 222) and 2 duplicate Elbit-partnership events (24, 223). The deterministic `(item_id,kind,title)` dedup gate misses all of these because titles are reworded each time.
- amount_usd unit bug: event id 89 (item 47) stores `"464.8"` (should be `464800000`) while other events in the same table use full-precision dollar figures.
- Positive: events id 80 (item 62) and id 138 (item 50) are clean, correctly attributed, single events.

### D4 -- deep investigations (golden job ids 46, 47, 48, 70, 86, 91)
- **46, 47, 48, 70, 91**: outcome=`not_found`, confidence 0.0-0.3. Job 91 (2x-budget rerun of 86) still failed after 30 queries / 10 pages / 4 rounds.
- **86**: answered a question about the Reaper-successor timeline with a detailed, confident description of Elbit's **MOSP 5000** payload -- a system never mentioned in the question. Caught only via a `relevance_check` note added *retroactively* ("2026-09-06 ... retroactive correction").
- Positive: 46/47/48/70 are honest about failing -- they state "לא נמצא מידע מספק" and log concrete attempted queries/pages rather than fabricating an answer.

### D5 -- chat ("ask the analyst"), live-tested against `POST /api/ask`
- **4/8 questions timed out** at 120s with zero output (Q5 Skyranger, Q6 AUSA 2026, Q7 US EO/IR RFI, Q8 SPECTRO ISR maturity).
- Of the 4 that answered (Q1 XM30, Q2 Iron Beam, Q3 Greece LORA, Q4 DROIC): **0/4 contain any inline `[n]` citation** despite a citations array being returned, and **4/4 render raw Markdown** (`##`, `**bold**`, bullet lists) -- both are explicit, quantifiable violations of the D5 deterministic rule, at a 100% rate on the answers that did return.
- **Q4** appears to hallucinate: attributes an arXiv algorithm ("RAFT-DVC", the real citation is an academic paper) to fabricated corporate R&D by "Leonardo DRS / Omnisys / Tobyhanna Army Depot" under an invented "FlexibleFusion" platform.
- **Q2** frames its answer as Rafael's Iron Beam contract but spends most of the text on an unrelated AeroVironment/Locust X3 US Army contract, only caveating the mismatch in the final two lines.
- **Q3** is directionally correct (David's Sling/Barak MX/Spyder layered stack, Turkey-rivalry framing) but highly repetitive across its own sections.

### D6 -- daily/weekly reports
- `weekly_2026-09-05.md`: executive summary = *"זהו תקציר בדיקה קצר"* ("this is a short test summary"); two section headers literally read "placeholder air_defense" / "placeholder airborne_pods" with body "תיאור כללי"; the entire forward-look section is *"להערכתנו זהו מבחן"* ("in our assessment, this is a test"). Also contains ~80 lines of feedback-calibration log, dominated by exact duplicate contradictory red&harr;yellow flips for the same headlines.
- `daily_2026-09-06.md`: exec summary is explicitly dropped -- *"הטיוטה הטקסטואלית של הדוח לא עברה את בדיקת האזכורים... ולכן הושמטה במלואה"* -- leaving a tables-only report. Confirmed per the loop doc's explicit ask: yes, tables-only, and the reason is a citation-QA gate rejecting the draft outright.
- Positive: the tender-forecast rationale prose in both reports is coherent and cites concrete item ids.

### D7 -- BD focus reports
- `bd_us_2026-09-06.md`: executive summary empty (citation-QA gate); **no actions/recommendations table exists at all** (confirmed independently by `round_0_auto.json`'s `actions_table_nonempty` check).
- `bd_il_2026-09-06.md`: has a real, numbers-backed executive summary and a populated actions table, but 6/7 action rows are the same templated "attend conference X" action; citation **[5]** is bound to the wrong source (a Litening tender listing) for a fact ("Elbit's $370M in US orders") that is correctly cited as **[9]** two lines later in the same document.

### D8 -- patent survey (latest by file mtime: `patent_survey_Anduril_Lattice_counter-UAS_EO_IR_optical_tracking_patents_2026-09-06.md`, 08:42)
- Only **1 of 17** listed patents (`US10506436B1`, "Lattice mesh") is actually assigned to Anduril; the other 16 have no assignee and are generic decades-old optical-tracking prior art from a keyword search.
- Annual-timeline and top-CPC-codes tables are present as headers but **completely empty**.
- No per-patent technical description anywhere -- every row is a title + flat score-of-10 + link.
- The narrative openly admits no Anduril patents were found, then pivots into a news recap (contracts, hires) instead of a patent/technology analysis.

### D9 -- tenders / forecasts / conferences
- `tenders` table: **5 total rows, 0 with status='open'**. Deadlines include 2015-08-27, 2016-12-17, 2023-03-08, 2025-10-01 (already past) -- the tenders feed currently surfaces nothing actionable, directly failing the "only open/current" requirement.
- `conferences` table: 15 rows checked, all plausible, matching `config/watchlist.yaml`'s live-verified `conferences_seed` -- solid.
- `tender_forecasts`: 7 structurally reasonable rows anchored to real item ids, but prose is templated across rows.

## Worst 10 items (this round)

1. **D6** `weekly_2026-09-05.md` -- executive summary / trend / forward-look are literal test placeholder strings shipped as production output.
2. **D6** `weekly_2026-09-05.md` -- ~80-line duplicate contradictory feedback-calibration log dump.
3. **D8** `patent_survey_Anduril_Lattice_...` -- 16/17 patents unassigned/unrelated; timeline and CPC tables empty; no per-patent narrative.
4. **D5** golden Q4 (DROIC trend) -- fabricated corporate attribution of an academic algorithm (RAFT-DVC) to named defense companies.
5. **D5** golden Q2 (Iron Beam contract) -- conflates an unrelated AeroVironment contract with Rafael's Iron Beam program.
6. **D5** all 4 answered golden questions -- 0/4 inline citations, 4/4 raw Markdown (100% rule violation rate).
7. **D4** job 86 (item 1352) -- confident answer about the wrong system (MOSP 5000 vs. the asked-about Reaper successor), caught only retroactively.
8. **D3** item 47 -- hallucinated "BlueHalo" entity, verified absent from source, traced to a watchlist alias bug affecting 4+ items.
9. **D1** items 1, 4, 7, 14 -- silent triage failure (score/level/reason all NULL) on 10% of the golden sample.
10. **D7** `bd_us_2026-09-06.md` -- empty executive summary and no actions table at all.
