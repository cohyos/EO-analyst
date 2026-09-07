### R7-patents status

Scope: `docs/qa/loop/round_6_judge.md` D8 worst #10 -- the term-derived "אשכול נושאי: <terms>"
cluster label (introduced round 6 to replace the flat "לא מסווג" bucket) frequently surfaced
assignee-name fragments (company names, "inc", "systems", "co", "universitesi") or generic
scrape/legal-boilerplate words instead of real technology terms. Files touched (per this round's
ownership -- `agent/eoa/patents/survey.py` and `scan.py` stayed frozen, untouched):
`agent/eoa/patents/cluster.py`, `tests/unit/test_patents_round7.py` (new, 25 cases, no DB). Sampled
the live `patents` table (86 rows, `127.0.0.1:5432` via `DATABASE_URL` from `runtime/eoa.env`,
read-only) to find and verify against real garbage shapes -- no repair script, no writes.

#### What changed in `agent/eoa/patents/cluster.py`

1. **New term-pool builder (`_term_pool_tokens`)** replaces the raw `_subcluster_tokens(title +
   abstract)` call feeding `tfidf_subcluster_unclassified`'s TF-IDF vectors. It now:
   - **Never draws from `row["assignees"]`** -- every word of every assignee name is stripped via
     `_assignee_name_tokens` (e.g. `["Anduril Industries Inc"]` -> `{"anduril", "industries",
     "inc"}` scrubbed from the pool).
   - Strips a **corporate-suffix stoplist** (`_CORPORATE_SUFFIX_STOPWORDS`): `inc, ltd, llc, corp,
     corporation, co, gmbh, ag, sa, systems, technologies, company, university, universitesi,
     institute, industries` -- catches a suffix word even when it leaked in from text that isn't
     literally this row's own assignee.
   - Strips a **patent-administrative-boilerplate stoplist** (`_PATENT_BOILERPLATE_STOPWORDS`):
     the USPTO assignment-record jargon named in the finding (`assignment, assignors,
     reassignment, interest, document, details, ...`) **plus** Google Patents' own recurring
     "Legal status" disclaimer paragraph and per-record metadata fields discovered live against
     real rows this round (`legal, status, assumption, conclusion, analysis, representation,
     accuracy, listed, inaccurate, performed, free, format, text, intermediate, effective, date,
     japanese`) -- real scrape noise the original finding's list didn't name but that dominated
     several live unclassified clusters (see id 70/29 below).
   - Adds a known CPC code's own English title text into the pool when the row carries one (point
     1's "+ CPC-title text") via a new `_CPC_CODE_TITLES` table (see point 4 below).
2. **Prefer technology nouns + bigrams (`_boosted_top_terms`, `_term_boost`)**: a curated EO/IR
   vocabulary (`_EOIR_VOCAB_TERM_HE` unigrams + `_EOIR_VOCAB_PHRASE_HE` multi-word phrases,
   assembled from `config/taxonomy.yaml`'s own English subdomain terms plus the finding's own
   worked example list -- gimbal, focal plane [array], ROIC/DROIC, thermal, SWIR/MWIR/LWIR/eSWIR,
   detector, seeker, tracking, laser, designator, hyperspectral, uncooled, microbolometer, lidar,
   infrared, pixel, ATR, metasurface, optronic, plus phrases quantum dot / night vision / beam
   control / super resolution / target recognition / sensor fusion / edge AI) boosts a matching
   term's ranking weight (phrase x3, vocabulary unigram x2, everything else x1) purely for
   *label-term selection* -- the underlying cosine-similarity clustering math is untouched.
   `_phrase_tokens`/`_raw_words` extract an actually-occurring multi-word phrase
   ("focal plane array") as its own token before ranking, so it can outrank its own bare component
   words ("focal", "plane", "array").
3. **Hebrew mapping + ordering (`_display_term_he`, updated `_unclassified_label_he`)**: each
   selected label term is mapped through the vocabulary to Hebrew where one exists (e.g.
   "detector" -> "גלאי", "focal plane array" -> "מערך מישור מוקד"), else kept in English rather
   than invented; the (still-capped-at-3) displayed terms are then ordered Hebrew-first. Round 6's
   own contract is unchanged and re-verified against the new pool: `_unclassified_label_he` never
   emits "לא מסווג"/"ללא סיווג" once at least one real term exists, and falls back to the bare
   label only when a sub-cluster's patents carry no title/abstract tokens at all.
4. **CPC-title fallback (`_CPC_CODE_TITLES`, updated `_cpc_label`)**: a cluster whose CPC code
   matches no configured watch topic but is one of the 12 codes `config/patents.yaml` itself
   already tracks (its own `cpc:` list, sourced from that file's trailing comments -- the only
   "CPC title text" this project's own data has anywhere, since the `patents` table stores bare
   codes with no per-row title column) is now labelled `"אשכול טכנולוגי: <title> (<code>)"` instead
   of the bare `"אשכול טכנולוגי <code>"`. A matching watch topic still wins over this fallback; a
   truly unknown code keeps the old bare fallback (verified in
   `TestCpcLabelTitleFallback::test_truly_unknown_code_keeps_the_bare_fallback`).

`_subcluster_tokens`/`_top_terms` (round 5's originals) are left in place, untouched and still
correct as general-purpose primitives -- `tfidf_subcluster_unclassified` now calls
`_term_pool_tokens`/`_boosted_top_terms` instead.

#### Before / after (8 examples, live `patents` table rows, 2026-09-07, read-only sample)

Computed by running round 6's own `cluster.py` (via `git show HEAD:...`) against this round's
fixed version over the same 86 live rows + this project's real `config/patents.yaml` watch topics.

| # | Patent (assignee, if any) | BEFORE (round 6) | AFTER (round 7) |
|---|---|---|---|
| 1 | id 62, US10506436B1 "Lattice mesh" (assignee: **Anduril**) | `אשכול נושאי: anduril / inc / industries` | `אשכול נושאי: lattice / mesh` |
| 2 | id 4, US12287242B2 "Readout circuit of infrared focal plane array..." | `אשכול נושאי: readout / circuit / unit` | `אשכול נושאי: מישור מוקד / מערך מישור מוקד / אינפרא-אדום` |
| 3 | id 14, "...droic with extended counting..." (assignee: **Europe** -- bogus placeholder) | `אשכול נושאי: droic / modulated / pulse` | `אשכול נושאי: אינפרא-אדום / DROIC / modulated` |
| 4 | id 17, US11050963B2 (assignee: **Massachusetts Institute of Technology**) | `אשכול נושאי: chip / block / circuit` | `אשכול נושאי: מישור מוקד / chip / DROIC` |
| 5 | id 29, JP6771616B2 (abstract is pure JPO metadata: "Free format text: JAPANESE INTERMEDIATE CODE: A523 · Effective date: 20190619") | `אשכול נושאי: code / date / effective` | `אשכול נושאי: code` |
| 6 | id 70, US3117231A "Optical tracking system" (abstract opens with Google's canned "Legal status..." disclaimer) | `אשכול נושאי: legal / not / status` | `אשכול נושאי: מעקב / not / has` |
| 7 | id 66, US20140226024A1 "Camera control in presence of latency" (genuinely technical text -- stability check) | `אשכול נושאי: camera / command / messages` | `אשכול נושאי: camera / command / messages` (unchanged, as expected) |
| 8 | Live 17-patent sub-cluster (ids incl. 30/41/42/51/56/58/63/65/67/70-79) | `אשכול נושאי: optical / sensor / tracking` | `אשכול נושאי: מעקב / לייזר / optical` |

Row 7 is included deliberately: it shows the fix does not touch a cluster whose top terms were
already genuine technical content, confirming the change is scoped to the actual defect rather
than a blanket re-ranking.

#### Tests

`tests/unit/test_patents_round7.py`, 25 new cases (>= 12 required): `_assignee_name_tokens` (3),
`_term_pool_tokens` (6, including the exact real Anduril/id-62 and Europe/id-14 shapes above),
`_boosted_top_terms` (4), `_display_term_he` / `_unclassified_label_he` term-mapping (7, including
an explicit regression guard reproducing round 6's own fixture verbatim), `_cpc_label` CPC-title
fallback (3), and 2 end-to-end `cluster_patents`/`tfidf_subcluster_unclassified` regressions using
the real garbage row shapes. All green together with the existing round 5 (50) and round 6 (41)
patent-cluster suites -- 116 passed, 0 failed:

```
PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest \
  tests/unit/test_patents_round7.py tests/unit/test_patents_round6.py tests/unit/test_patents_round5.py \
  -q -p no:cacheprovider
# 116 passed in 0.9s
```

`ruff check`/`ruff format --check` clean on both touched files.

#### Left for a follow-up round (not in this round's scope/ownership)

- `_CPC_CODE_TITLES` only covers the 12 CPC codes `config/patents.yaml` itself already tracks; the
  live table also carries CPC codes outside that set (e.g. `H04N5`, `H03M1`, `G01S7`, `H04W4`,
  `F41H11`, `G05D1`, `F41H3`, `H04N25`) that still fall through to the bare `"אשכול טכנולוגי
  <code>"` label -- unchanged by design this round (no CPC title text exists anywhere in this
  project's own data for those codes; inventing one would violate the no-fabrication rule). A
  future round could add a small static CPC-code -> title reference table (WIPO/USPTO's own public
  CPC scheme text) if broader coverage is wanted.
- The "code / date / effective" -> "code" case (id 29, example 5 above) shows a genuinely
  content-free row (the row's only real text is Japan Patent Office intermediate-code metadata)
  still surfaces one residual non-technical term ("code") rather than falling back to
  `UNCLASSIFIED_LABEL_HE` -- "code" was deliberately left out of the boilerplate stoplist (dual-use
  word, e.g. "error-correcting code", "Gray code") rather than blanket-suppressed; this is a
  correct, honest outcome (one weak term beats an invented one) but is called out here since it is
  the least-improved of the 8 examples.
- `agent/eoa/patents/survey.py`/`scan.py` were frozen this round; nothing in this fix required a
  change there, so none was made or proposed.
