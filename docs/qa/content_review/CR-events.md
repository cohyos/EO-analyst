# CR Events — Business-Event Fabrication Root-Cause, Fix, and Repair (Round 14)

Date: 2026-09-07. Trigger: `docs/qa/content_review/CR-factcheck.md` (independent fact-check pass,
2026-09-07) — the structured `events` extraction (`eoa.pipeline.analyze`'s `EventOut`/
`insert_event` path) fabricated or garbled facts and promoted them into the corpus's most
prominent tables (monthly's "top 10 events by value", bd_il/bd_gr's "רכש ופלטפורמות" procurement
pipeline). Five distinct problems, listed in CR-factcheck.md's own ranked list at #1, #2, #7, #8,
#10, #13.

## 1. Root causes

`eoa.pipeline.analyze.persist_analysis` had exactly one grounding check on the `events` it
persists — `normalize_amount_from_source` (anchors a suspicious *small* number, e.g. `464.8`, back
to a magnitude word in the source) — and nothing else. Every other field (`kind`, `customer`,
`parties`, whether an amount is a deal amount vs. a company valuation) was inserted as the model
extracted it, unchecked. `eoa.pipeline.analysis_grounding` already does this kind of work for
`summary_he`/`so_what_he`/`key_facts`; there was no `events`-shaped counterpart before this round.

1. **Amount attributed to the wrong fact within the same article** (events 22/23, item 81 — a
   Globes article about Anduril appointing Amikam Norkin to head its Israel operation, which *also*
   mentions a ~$10B financing round in progress and a ~$100B *valuation*). Event 22 was extracted
   as `kind='m_and_a'`, `amount_usd=$10,000,000,000`, `customer='Israel Ministry of Defense and
   IDF'` — the source describes a personnel appointment, no transaction, and no IMOD/IDF
   counterparty of any kind; the $10B figure belongs to a *different* sentence (event 23's real
   financing round). This fabricated row was then promoted into `monthly_2026-09-30.md`'s own "top
   10 events by value" table (CR-factcheck #2) and into `bd_il_2026-09-07.md`'s procurement
   pipeline table as a fictitious "$10B fighter-jet/targeting-pod" opportunity (CR-factcheck #1,
   "most severe finding in the corpus"), additionally cross-contaminated there with an unrelated
   platform-payload template — see root cause 3 below.
2. **`kind` misclassification with no vocabulary check at all.** `m_and_a` was used for: event 22
   (a personnel appointment, no transaction of any kind), event 23 (a real financing round — not a
   merger/acquisition), event 24 (the Elbit×Anduril Sigma-155 co-marketing/teaming partnership,
   CR-factcheck #7 — "מיזוג/רכישה" recurring across weekly/bd_il/bd_us/monthly), and event 224
   (general market-entry commentary, no transaction). Separately, event 70 (item 114, Elbit's real
   $32B backlog disclosure) was filed under `kind='regulation'` (CR-factcheck #13) — a quarterly
   earnings/backlog announcement is not a regulatory action, but the pre-existing 9-value
   `events.kind` enum had no earnings/backlog/appointment slot to file either shape under correctly.
3. **`collect_platform_events` inferred a platform from the item's *entire* body text, not from the
   event itself.** Item 90 (the Greece air-defense deal, €3.1bn/$3.6bn) mentions "F-16" exactly
   once, in an unrelated sentence about Turkish drone incidents ("A pair of Greek Air Force F-16s
   were scrambled...") — nothing to do with the air-defense sale the event describes. The old match
   text (`title + item_title + clean_text + summary_he`) picked that up anyway and attached the
   "מטוס קרב" (fighter jet) / "פוד כיוון" (targeting pod) platform template to the row (CR-factcheck
   #1's "unrelated forecast template attached to the wrong source item", and independently #8: bd_il
   line 101 / bd_gr line 84, same item).
4. **`buyer`/`vendor` derivation produced `buyer == vendor` whenever `customer` was null.**
   `buyer = customer or parties[0]`; `vendor = next(p for p in parties if p != customer)` — when
   `customer` is `None`, `p != None` is true for *every* party, so `vendor` fell back to the very
   same `parties[0]` the `buyer` fallback had just picked. Item 90's event (`customer=None,
   parties=['Israel','Greece']`) rendered as `buyer='Israel', vendor='Israel'` — CR-factcheck #8's
   "buyer = supplier = Israel" nonsense row, independently reproduced live while fixing this (see
   §3 below).
5. **No cross-source reconciliation.** The same real-world event reported by more than one item
   was always inserted as a separate row with no link between them: the Greece air-defense deal
   (items 90/150/155, quoted at $3.6B/€3.1B, $4B/€3.5B, €3.5B respectively — CR-factcheck #9) and
   the Elbit $270M SPECTRO/ISR contract (items 93/321, reported by israeldefense.co.il and
   airforce-technology.com a day apart — CR-factcheck #10) each produced multiple independent rows
   with no shared identity, so a reader/table consuming "all events" or "top events by value" saw
   the same deal counted 2-7 times over, at each source's own (sometimes conflicting) figure.

## 2. Fix

### (a) `agent/eoa/pipeline/event_grounding.py` (new module)

`ground_event(item, event) -> GroundedEvent` — the per-event grounding entry point, five rules:

- **(a) Amount/currency grounding** — `amount_usd` must be traceable to a number in the source at
  the same magnitude (accepts "10 billion"/"$10bn"/"€3.1 billion" digit+magnitude-word forms, or a
  bare grouped-digit literal). A number immediately *preceded* (within a 60-char window — before
  only, not after) by a valuation/market-size cue ("valuation of", "valued at", "market cap", "שווי")
  is dropped even when the digits are grounded. The before-only window is what distinguishes event
  22's fabricated $10B (dropped: no valuation cue precedes it, but it's simply not this event's own
  fact) from event 23's real $10B (kept: "the size of the round has reached about $10 billion,
  based on a company **valuation** of about $100 billion" — only the *second*, $100B figure has a
  valuation cue directly before it).
- **(b) Party/customer grounding** — every `customer`/`parties` entry must appear literally in the
  source or resolve (`entity_normalize.resolve_canonical`) to a watchlist record at least one of
  whose *other* aliases the source mentions (e.g. "IAI" ↔ "Israel Aerospace Industries" spelled
  out). Placeholder strings a model sometimes writes instead of leaving the field empty ("לא צוין",
  "not specified", "n/a", ...) are normalised to `None` and never counted as a grounding failure.
- **(c) `kind` reclassification**, checked against the **event's own** text (`title`/`summary_he`/
  `program`) — deliberately *not* the item's full source corpus. Checking the full corpus was
  tried first and produces the identical false-positive shape as root cause 3 above: item 81's
  corpus contains the word "acquisitions" in an unrelated sentence ("the company... examined
  possible collaborations, investments, and acquisitions"), which would have let event 22 keep its
  fabricated `m_and_a` kind for exactly that reason. `m_and_a` without acquisition/merger/takeover
  vocabulary in its own text downgrades to `partnership` (co-marketing/teaming vocabulary),
  `investment` (financing-round vocabulary), `appointment` (personnel-appointment vocabulary), or
  `other`; `regulation`/`other` with earnings/backlog/financial-results vocabulary reclassifies to
  `financial_results`. `appointment` and `financial_results` are two new `events.kind` values
  (`db/migrations/versions/0030_events_grounding.py` — neither existing 9-value enum member had an
  honest slot for either).
- **(d) Title-only/empty-body items** may not carry an `amount_usd` or `customer` at all
  (< 80 chars of real body text — there's no real body for a number/name to be traceable *to*).
- **(e) Confidence cap** — `confidence` is capped at 0.5 whenever *any* field was actually dropped
  (a `kind` reclassification alone, nothing else lost, does not trigger the cap — event 23 keeps
  its original 0.7).

`reconcile_events(events) -> list[ReconciledEvent]` — the separate, list-level cross-source merge
(root cause 5). A union-find over a pairwise `_mergeable` test:

- **Same item**: same `kind` and `eoa.memory.relational.same_kind_duplicate` on the titles — the
  exact rule `insert_event`'s own near-duplicate upsert already uses for a fresh insert, reused
  here (not re-implemented) so an already-persisted pair is judged identically to a new one. A
  naive "same item + same kind ⇒ always merge" was tried first and, live against the full `events`
  table, wrongly collapsed item 101's two genuinely distinct satellite-launch events (Dror 1 and
  Ofek 19) and item 153's four genuinely distinct partnership rows (WeatherNext/GraphCast/
  Pangu-Weather/IFS HRES) into one row apiece — `same_kind_duplicate`'s own distinct-numbers/
  distinct-proper-nouns block is exactly the guard that case needed.
- **Cross item**: same `kind` + overlapping company identity (customer/parties) + an
  **actually-confirmed** date match (within 14 days) or amount match (within ~12%, comparing in
  USD via a fixed EUR/ILS approximation) — an OR of the two signals, but each requires *both* sides
  to genuinely carry a date/amount; "either side unknown" is never treated as a pass. This, too,
  was tightened after a live false-merge: identity-overlap-only (no confirmed corroboration
  required) wrongly clustered event 61 (Leonardo DRS wins a US Space Force contract) with event 124
  (Leonardo wins a Centauro II contract for the Brazilian Army) — two unrelated deals sharing only
  the common vendor name "Leonardo" and landing two days apart by coincidence. A hard block was
  added: two events that each name a *specific, different* customer never merge, whatever else
  overlaps.

A merged cluster keeps the richest member (most non-null fields, ties → lowest/earliest id),
widens `customer`/`parties` by union, records every other item id in the new
`events.source_item_ids` column, and — when the cluster's own amounts disagree beyond the ~12%
tolerance (the Greece case) — appends `"סכומים שונים בין המקורות: <amount 1>, <amount 2>, ..."` to
`summary_he` instead of silently keeping one figure.

### (b) `agent/eoa/report/bd_territory.py` — `collect_platform_events`/`platform_events_table`

- **Platform match text** is now the event's own `title`/`summary_he`/`program` plus the item's
  headline (`item_title`) only — the full `clean_text` body is no longer searched (root cause 3).
  A `match is None` falls back to the event's own `program`/`title`, which for item 90 is already
  correct ("מכירת שלושה מערכות הגנה אווירית ליוון" — sale of three air-defense systems to Greece),
  not a wrong category.
- **`buyer`** is now only ever the event's own grounded `customer` — never guessed from `parties`.
  **`vendor`** is a *different* named party than the buyer. Either side missing, or (defensively)
  still equal after that, renders `"לא ידוע"` rather than a guess (root cause 4). Item 90 now
  renders `buyer="לא ידוע", vendor="Israel"` instead of `buyer="Israel", vendor="Israel"`; item
  150 (which has a real `customer='Greece'`) renders `buyer="Greece", vendor="Rafael"`.
- Amount/currency are still taken straight from the (now-grounded, once wired — see §3) `events`
  row; never re-derived here.

### (c) `agent/eoa/llm/prompts/payload_extract.md`

Additive iron-rule clarification (same valuation-vs-deal-amount distinction as event_grounding
rule (a), and a "buyer must be explicitly named, never inferred" rule) for the separate EO/IR
payload-price extraction pipeline (`eoa.payloads.extract`) — out of this round's strict scope
(business events), but the identical fabrication shape is possible there and the fix is a two-line
prompt addition.

### (d) `db/migrations/versions/0030_events_grounding.py`

- Widens `events.kind` CHECK: adds `'appointment'`, `'financial_results'`.
- Adds `events.source_item_ids BIGINT[] NOT NULL DEFAULT '{}'` (+ GIN index) — the cross-source
  reconciliation merge's sibling-item record.

Applied to the live DB (`alembic upgrade head`, now at `0030`).

## 3. Wiring instruction for `agent/eoa/pipeline/analyze.py` (owned by another agent this round —
not editable here; apply by hand)

In `persist_analysis`'s `for ev in _dedup_events(out.events):` loop, immediately after the existing

```python
amount_usd, magnitude = normalize_amount_from_source(ev.amount_usd, _source_text_for_amounts(item))
```

insert:

```python
from eoa.pipeline.event_grounding import ground_event
grounded = ground_event(
    item,
    {
        "kind": ev.kind, "title": ev.title, "date": ev.date, "amount_usd": amount_usd,
        "currency": ev.currency, "parties": event_parties, "customer": ev.customer,
        "program": ev.program, "summary_he": ev.summary_he, "confidence": ev.confidence,
    },
)
if grounded is None:
    log.info("event.dropped", item_id=item["id"], title=(ev.title or "")[:160])
    continue
if grounded.dropped_fields:
    log.info(
        "event.ungrounded_field_dropped",
        item_id=item["id"], title=(ev.title or "")[:160], fields=grounded.dropped_fields,
    )
```

then replace the `insert_event(...)` call's `kind=ev.kind, ..., amount_usd=amount_usd,
currency=ev.currency, parties=event_parties, customer=ev.customer, program=ev.program,
summary_he=ev.summary_he, confidence=ev.confidence` keyword arguments with `kind=grounded.kind,
title=grounded.title, date=_parse_date(grounded.date), amount_usd=grounded.amount_usd,
currency=grounded.currency, parties=grounded.parties, customer=grounded.customer,
program=grounded.program, summary_he=grounded.summary_he, confidence=grounded.confidence`.

`reconcile_events` is a repair-script/batch-report concern, not a per-item extraction-time one
(the whole point is comparing an event against *other items'* already-persisted events) —
`scripts/repair_round14_events.py` already runs it over the full table; no analyze.py wiring needed
for it.

## 4. Repair — `scripts/repair_round14_events.py`, run against the live DB (127.0.0.1:5432/eoanalyst)

Three passes (`ground` → `reconcile` → `apply`), dry-run by default, `--apply` to write, backed up
first to `runtime/backups/repair_round14_events_<timestamp>.json`. Run twice (a second small fix —
see below — plus the original sweep):

- `runtime/backups/repair_round14_events_20260907T191933Z.json` — 36 rows (25 grounding updates +
  11 pre-merge event rows backed up before deletion).
- `runtime/backups/repair_round14_events_20260907T192049Z.json` — 1 row (event 107's placeholder
  customer, "לא צוין" → `None`, caught on the second run because the first version of the script
  only updated a row when `GroundedEvent.changes` was non-empty, which a *silent* placeholder
  normalisation never populates — fixed in the script itself, see its own `_grounded_differs_from_row`).

Verified from a **fresh** connection after `--apply` (`scripts/repair_round14_events.py`'s own
`_verify`): `events_still_ungrounded: []`, `keep_rows_missing_after_apply: []`,
`merged_siblings_still_present: []`. `events` row count: 125 → 114 (11 deleted by merge).
A third dry run after both applies reports `events_changed: 0, clusters_merged: 0` — fully
idempotent.

### Events 22/23 (item 81) — fixed first, as required

| id | field | before | after | evidence |
|---|---|---|---|---|
| 22 | kind | `m_and_a` | `appointment` | "מינוי אמיתי נורקין לראש פעילות אנדו[ריל בישראל]" (event's own title) |
| 22 | customer | `Israel Ministry of Defense and IDF` | `None` | not found in source text (literally or via alias) |
| 22 | amount_usd | `10,000,000,000` | `None` | `kind='appointment'` — personnel moves carry no deal amount |
| 23 | kind | `m_and_a` | `investment` | "סבב גיוס הון חדש של אנדוריל אנדוריל נמ[צאת בתהליך...]" (financing-round vocabulary) |

Event 23's `amount_usd` ($10B) and `confidence` (0.7) are **unchanged** — the figure is real
(the source's own "the size of the round has reached about $10 billion"), only its `kind` was
wrong.

### Every other changed row (grounding pass, 23 more events)

| id | item | field | before | after | evidence |
|---|---|---|---|---|---|
| 24 | 81 | kind | `m_and_a` | `partnership` | "שיתוף פעולה בין אנדוריל לאלביט מערכות..." |
| 52 | 180 | kind | `m_and_a` | `other` | no acquisition/partnership/investment/appointment vocabulary in the event's own text |
| 59 | 285 | amount_usd | `3,450,000,000` | `None` | valuation/market-size context: "...uire Ultra Maritime in a d[eal valued at...]" |
| 70 | 114 | kind | `regulation` | `financial_results` | "דיווח על שיא במלאי ההזמנות..." (Elbit's real $32B backlog — figure unchanged) |
| 79 | 172 | parties | `ריית'און` | dropped | not found in source text (literally or via alias) |
| 84 | 96 | amount_usd | `464,800,000` | `None` | valuation/market-size context |
| 89 | 47 | amount_usd | `464,800,000` | `None` | valuation/market-size context: "...s awarded a landmark contr[act valued at...]" |
| 102 | 122 | parties | `מחברי המאמר` | dropped | not found in source text |
| 105 | 147 | parties | `חוקרים אקדמיים בתחום ראיית מכונה ומודי...` | dropped | not found in source text |
| 118 | 71 | parties | `Israeli Ministry of Defense` / `משרד הביטחון של מדינה חברה בנאט"ו` | dropped (both) | not found in source text |
| 119 | 71 | parties | `משרד ההגנה האמריקאי` / `US Department of Defense` | dropped (both) | not found in source text |
| 134 | 257 | amount_usd | `1,530,000,000` | `None` | valuation/market-size context: "...ping framework.\nValued at [...]" |
| 181 | 5122 | customer | `US Department of Defense` | `None` | not found (source is a Dept. of the Air Force tender notice — CR-factcheck's own pl_targeting_pods finding, root-caused independently here) |
| 202, 231, 250, 264 | 290 | parties/customer | `UK Ministry of Defence (MoD)` / `UK Ministry of Defence` | dropped | not found in source text (multiple rows/fields) |
| 219 | 147 | parties | `המחברים (לא צוינו שמות)` | dropped | not found in source text |
| 224 | 81 | kind | `m_and_a` | `other` | no acquisition/partnership/investment/appointment vocabulary |
| 246, 247 | 126 | customer | `סרביה` | `None` | not found in source text |
| 259 | 6163 | parties | `גרמניה` | dropped | not found in source text |
| 261 | 58 | amount_usd | `464,800,000` | `None` | amount not found at this magnitude anywhere in the source (a copy/cross-contamination artifact — unrelated Japan ASM-3ER missile item) |
| 107 | 93 | customer | `לא צוין` | `None` | placeholder normalisation (not a grounding failure — no confidence penalty) |

### Cross-source reconciliation merges (6 clusters, 11 rows deleted)

| keep event | keep item | merged/deleted event id(s) | merged item(s) | amounts reconciled |
|---|---|---|---|---|
| 74 | 150 | 25, 228 | 90, 155 | **yes** — summary_he now reads "...בשווי 3.5 מיליארד אירו... סכומים שונים בין המקורות: 3,500,000,000 EUR, 3,600,000,000 USD, 4,000,000,000 USD" (the Greece $4B/€3.1B/€3.5B mess, CR-factcheck #9); confidence capped to 0.6 |
| 107 | 93 | 54 | 321 | no — both $270,000,000 USD exactly (the Elbit SPECTRO/ISR duplicate, CR-factcheck #10) |
| 121 | 10 | 84, 89, 131, 138, 213 | 47, 50, 96, 235 | **yes** — the AeroVironment $464.8M E-HEL contract, reported across 5 outlets |
| 106 | 147 | 105 | — (same item) | no — two near-duplicate "academic partnership" rows on the same item |
| 150 | 3 | 160 | 1352 | no — the US Air Force MMA/MQ-9A-replacement story |
| 169 | 2386 | 210 | 1352 | no — same MMA/MQ-9A story, a second pair of rows |

## 5. Reports the lead must rebuild

Every report below cites at least one of events 22/23/24/70 or the item-90/150/155/93/321 clusters
directly, or the `collect_platform_events`/`platform_events_table` bug:

- `monthly_2026-09-30.md` — lines 137, 499 (fabricated $10B m_and_a, "top 10 events by value"),
  120/138 (Elbit $270M double-count), 497 (Elbit $32B "regulation" mislabel).
- `weekly_2026-09-07.md` — line 432 (Elbit×Anduril "מיזוג/רכישה"), 502-504 (Greece deal 3 figures,
  no reconciliation).
- `bd_il_2026-09-07.md` — lines 62 (Elbit×Anduril M&A mislabel), 100 (the $10B fighter-jet
  fabrication, "most severe finding in the corpus"), 101 (buyer=supplier=Israel).
- `bd_gr_2026-09-07.md` — lines 33-35, 84 (same buyer=supplier=Israel row, plus the Rafael-only
  supplier framing that ignored IAI's co-supplier role).
- `bd_us_2026-09-07.md` — line 55 (Elbit×Anduril M&A mislabel, 4th recurrence).
- `daily_2026-09-07.md` / `bd_de_2026-09-07.md` / `bd_eu_2026-09-07.md` — not directly caused by
  this round's fabrications (their own issues, VW/Rafael and Ophir/IAI, are analysis-stage/item-39
  problems already covered by `eoa.pipeline.analysis_grounding`/`docs/qa/content_review/
  CR-patents.md`), but should still be regenerated so their event/platform tables reflect the
  now-corrected `events` rows.

## 6. Scope notes / what this round deliberately did not touch

- `program` field is not validated by `event_grounding` (only `title`/`summary_he`/`customer`/
  `parties`/`amount_usd`/`kind` are) — out of the task brief's explicit five rules. Event 107's
  `program='לא צוין'` (the same placeholder pattern fixed for `customer`) was left as-is; a
  follow-up could extend the placeholder-normalisation rule to `program` too.
- `agent/eoa/pipeline/analyze.py`, `agent/eoa/patents/**`, `agent/eoa/report/docx_builder.py`,
  `web/**` were out of scope for this round (owned by other agents/rounds) — see §3 for the exact
  one-time wiring change `analyze.py` still needs.
