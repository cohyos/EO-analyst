# BIDI-REPORTS — Hebrew/Latin word-gluing on the embedded daily report (2026-09-08)

Scope: the systemic bidi defects reported from a screenshot of the daily report embedded on the
morning page (`output/reports/daily_2026-09-08.md`/`.html`, reports row of 2026-09-08 01:35).
Ownership boundary (per the task brief): `agent/eoa/report/textnorm.py`, the markdown→HTML
rendering in `agent/eoa/report/docx_builder.py` (not the table-caption code, owned by a concurrent
agent), `agent/eoa/report/style.py`, `web/src/lib/bidiText.tsx`, and tests. Read-only on
`web/src/pages/MorningPage.tsx`, `web/src/components/reports/ReportBody.tsx`,
`web/src/lib/reportHtml.ts` (owned by other agents right now).

## 1. Bug report

> "3דולר לירי" (digit glued to the Hebrew word), "צמצם ב- %75את" (the percent sign flipped to the
> left of the number and the number glued to the next word), "AeroVirementבעלות", "6מערכות",
> "ReDroneנוספות", "C-UASישראלית", "( Axon Vision, מזכר" (parenthesis displaced), "5פריטים".

## 2. Diagnosis: which layer

**The `.md` source is correct.** Every one of the seven phrases already carries a normal single
space in `output/reports/daily_2026-09-08.md`:

```
צבא ארה״ב מדווח כי נשק לייזר של AeroVironment בעלות 3 דולר לירי צמצם ב-75% את טיסות רחפני
הקרטלים... הולנד מזמינה 6 מערכות ReDrone נוספות מאלביט... לצד הזמנת C-UAS ישראלית נוספת מ-Axon
Vision... לעומת הדוח היומי הקודם: 5 פריטים חדשים.
```

No missing spaces, no reversed characters, nothing an LLM drafting pass got wrong.

**The generated `.html` was the defect.** `docx_builder._bidi_html` (via `split_runs`) correctly
*identified* every Latin/digit run and wrapped it in `<bdi dir="ltr">…</bdi>` — the wrapping itself
was never missing. The bug was *where the joining space ended up*: `split_runs`'s docstring says
"whitespace/punctuation characters inherit the class of the run they fall in" — for a trailing
space right after a Latin/digit run (e.g. `"AeroVironment "`, `"3 "`, `"75% "`), that rule keeps
the space glued *inside* the `'other'` run, so it got wrapped *inside* the `<bdi>` tag along with
the Latin content:

```html
<!-- before the fix, in the live output/reports/daily_2026-09-08.html -->
<bdi dir="ltr">AeroVironment </bdi>בעלות <bdi dir="ltr">3 </bdi>דולר לירי
```

An HTML `<bdi dir="ltr">` isolate is atomic to the browser's bidi algorithm. A space trapped
*inside* it does not act as a normal word-separator against the Hebrew text right outside — it
renders as if the two words are directly glued together, no visible gap at all. This was confirmed
empirically with a live-browser test (not just visual inspection): a minimal repro page rendered
side by side with `<bdi dir="ltr">AeroVironment </bdi>בעלות` (space inside) vs.
`<bdi dir="ltr">AeroVironment</bdi> בעלות` (space outside), then measured with
`Range.getBoundingClientRect()` per character. The "space inside" variant showed **zero pixels**
between `ת` (of בעלות) and the immediately-following Latin content — i.e. an exact, measured
reproduction of "AeroVirementבעלות" — while the "space outside" variant matched the plain-text
(no-bdi) reference exactly.

**Per-symptom root cause:**

| Symptom | Cause |
|---|---|
| "3דולר" | trailing space after `<bdi dir="ltr">3 </bdi>` trapped inside the isolate |
| "AeroVirementבעלות" | trailing space after `<bdi dir="ltr">AeroVironment </bdi>` trapped inside the isolate |
| "6מערכות" / "ReDroneנוספות" | same pattern, digit run and Latin-name run each |
| "C-UASישראלית" | same pattern, Latin acronym run |
| "5פריטים" | same pattern, digit run |
| "צמצם ב- %75את" | **not** a percent-sign reversal in the current renderer — `%` was already correctly attached to its digits inside one `<bdi>` (`<bdi dir="ltr">75% </bdi>`); the *trailing space* after `%` was trapped the same way, so "75%" glued directly onto "את" with no gap. (The exact "%75" reversed-order artifact in the screenshot is consistent with an even older renderer state where `%` sat outside the isolate entirely — see the `fix_percent_sign_order` defense added below as a source-text safety net for that variant, in case it recurs from an LLM draft quirk.) |
| "( Axon Vision, מזכר" | same trapped-trailing-space pattern, on the isolate `<bdi dir="ltr">Axon Vision, </bdi>` (the source has `(Axon Vision, מזכר...)`, i.e. the paren directly touches the isolate — see below for why the fix does *not* also move the paren) |

**The UI path.** `MorningPage.tsx` passes `report.html` (the same backend-rendered HTML file) into
`ReportBody` (`web/src/components/reports/ReportBody.tsx`), which pipes it through
`wrapReportTables(fixBdiSpacing(enhanceSourceAppendixLinks(linkifyReportCitations(html, …))))`
(`web/src/lib/reportHtml.ts`) before `dangerouslySetInnerHTML`. **`fixBdiSpacing` already exists**
and already does the right thing — regexes that move a `<bdi>`'s leading/trailing whitespace
*outside* the tag — added in an earlier content-review pass for exactly this failure mode ("CR-ui.md" is referenced in its own comment). It is a genuine, correct client-side patch. It did not
fully eliminate the symptom because (a) it only rewrites whitespace, so it can't have addressed a
case where a mark other than whitespace was the actual defect, and (b) most importantly, the
report the user screenshotted may simply predate this client patch reaching the deployed bundle —
either way, **the backend was still emitting the broken HTML**, so every consumer of that HTML
(`.html` file, the DB-stored copy the API serves, and the `.docx`) carried the defect regardless of
what the UI's own client-side patch could paper over. Fixing the backend renderer (this task) means
`fixBdiSpacing` now has nothing left to fix for these cases (its regexes simply won't match
anymore) — it stays as a harmless no-op safety net, not a conflict.

## 3. Fix

### `agent/eoa/report/docx_builder.py` — `split_runs` / `_rebalance_boundary_whitespace` (new)

`split_runs(text)` still builds the same `[(cls, chunk), …]` run list as before (Hebrew-vs-Latin/
digit classification, punctuation inherits the surrounding run, symmetric bracket-pair handling
unchanged — Q5-4 regression re-verified, see tests below). The one addition: before returning, the
run list is passed through a new `_rebalance_boundary_whitespace`, which moves any whitespace
sitting at the *edge* of an `'other'` run onto the adjacent `'he'` run instead, whenever that
neighbor exists — so the joining space between a Hebrew word and an LTR run is never left trapped
*inside* the isolate `_bidi_html` (HTML) or `add_mixed_paragraph`/`split_runs_with_citations`
(the `.docx` Word-run path — both consumers share `split_runs`, so both are fixed by the one
change) wrap around it.

```python
def _rebalance_boundary_whitespace(runs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    ...
    for i, (cls, chunk) in enumerate(runs):
        if cls != "other" or not chunk:
            continue
        if i > 0 and runs[i - 1][0] == "he":
            # move leading whitespace back onto the preceding Hebrew run
        if i + 1 < len(runs) and runs[i + 1][0] == "he":
            # move trailing whitespace onto the following Hebrew run
```

Result, for the exact reported phrases:

```html
לייזר של <bdi dir="ltr">AeroVironment</bdi> בעלות <bdi dir="ltr">3</bdi> דולר לירי
צמצם ב-<bdi dir="ltr">75%</bdi> את טיסות
שדווחו היום (<bdi dir="ltr">Axon Vision,</bdi> מזכר עין שלישית) נמוכים
הולנד מזמינה <bdi dir="ltr">6</bdi> מערכות <bdi dir="ltr">ReDrone</bdi> נוספות
הזמנת <bdi dir="ltr">C-UAS</bdi> ישראלית נוספת
לעומת הדוח היומי הקודם: <bdi dir="ltr">5</bdi> פריטים חדשים
```

**Why the fix does not also move the paren.** The "( Axon Vision, מזכר" case was investigated as a
possible *second* bug (a bracket, not whitespace, needing to move across the isolate boundary) and
a candidate fix (absorbing a directly-touching opening bracket into the following isolate) was
prototyped and verified live in a browser. It was **not** adopted: precise
`getBoundingClientRect`-per-character measurement, run across all seven symptom phrases plus the
pre-existing Q5-4 regression case, showed that whitespace-rebalancing *alone* already eliminates
every actual Hebrew-letter-touching-Latin-letter glue (the only objectively broken pattern — a
bracket sitting flush against a letter with zero gap, e.g. `"(Axon"`/`"Vision,"`, is normal,
unremarkable typography, not a defect, and matches how the plain-text reference itself renders).
The bracket-absorption candidate, by contrast, changed the pre-existing (and correctly tested)
Q5-4 "homogeneous-content" parenthetical case (`'פודים ומטע"דים אוויריים (Airborne Pods & Payloads)'`)
without fixing anything additional — so it was reverted in favor of the narrower, fully-sufficient
whitespace-only fix. `test_split_runs_bracket_pair_stays_symmetric_regression` in the new test file
re-asserts the Q5-4 behavior is unchanged.

### `agent/eoa/report/textnorm.py` — two new narrowly-scoped passes

Per the brief, extended textnorm's Hebrew/Latin handling for the punctuation-adjacent variants
described in the bug report:

- `fix_percent_sign_order` — a percent sign that lands *before* its number (with or without a
  stray space, e.g. `"% 75"`/`"%75"`) swaps back to `"75%"`. Always safe: this ordering is never
  legitimate in Hebrew or English prose.
- `collapse_space_after_hebrew_prefix_hyphen` — a stray space between a Hebrew single-letter
  prefix's maqaf hyphen and the digit/Latin token it binds to collapses away, e.g. `"ב- 75%"` →
  `"ב-75%"`. Scoped to the closed set of single-letter prefixes (`ב כ ל מ ש ו ה` + `-`) so it can
  never touch an unrelated hyphenated compound.

Both are wired into `normalize_hebrew_punctuation` (now 8 passes, was 6) and run against every
report draft via the existing `normalize_draft` call sites — no call-site changes needed.

**Deliberately not added:** a blanket "insert a space at every bare Hebrew/Latin character
boundary" pass. Checked against the actual report corpus first (`grep` across
`output/reports/*.md` for a Hebrew char directly touching a Latin/digit char) and rejected: a
single-letter Hebrew conjunction/prefix — most commonly `ו-` ("and") — correctly glues directly to
a following Latin/digit token with **zero space** per ordinary Hebrew grammar, and this pattern is
already live in the corpus (`"וH04N5"` = "and H04N5", a patent-code citation in
`daily_2026-09-06.md`; also `"וSabanci"` = "and Sabanci"). A blanket space-insertion rule would
have corrupted these. `test_normalize_hebrew_punctuation_does_not_touch_hebrew_conjunction_prefix`
locks this in. This also confirms the real "3דולר"-class bug was a *rendering*-layer defect (a
real space, already present in the source, ending up in the wrong place), not a *source-text*
defect needing a source-level insertion pass.

## 4. Tests

New file `tests/unit/test_report_bidi_round15.py` (19 tests, filename per the brief):

- 6 parametrized cases, one per reported symptom phrase, asserting the `<bdi>` wraps with the
  space outside and the glued form never appears.
- A direct `split_runs` invariant check (no `'other'` run boundary next to a Hebrew run may start
  or end with whitespace) across all seven phrases.
- A letter-adjacency check on the paren/mixed-content case (no two letters from different runs may
  touch with zero gap).
- The Q5-4 regression, re-asserted unchanged.
- `fix_percent_sign_order` / `collapse_space_after_hebrew_prefix_hyphen` unit cases, the combined
  `normalize_hebrew_punctuation` pipeline case (`"ב- %75"` → `"ב-75%"`), an idempotence/no-op case
  on already-correct text, and the Hebrew-conjunction-prefix false-positive guard above.

Full suite run: `pytest tests/unit/test_docx_builder.py tests/unit/test_report_bidi_round15.py -q`
→ **57 passed**. `ruff check agent/eoa/report/docx_builder.py agent/eoa/report/textnorm.py
tests/unit/test_report_bidi_round15.py` → clean.

## 5. Rebuild verification

`EOA_PIPELINE=1`, `build_daily(dt.date(2026, 9, 8), dt.date(2026, 9, 8), force=True)` →
`report_id=190`, `qa_passed=True`, wrote `output/reports/daily_2026-09-08.{md,html,docx}`. (The
rebuilt draft covers a narrower item window than the original 2026-09-08 01:35 build — 1 item vs.
the original's larger set — since `force=True` re-runs the full collect→draft→QA pipeline against
the current DB state rather than re-rendering the stored draft; the AeroVironment/laser story is
still the lead item, so all the digit/percent/Latin-name phrases are present and checkable.)

Verified in the new `.html`:

```
<bdi dir="ltr">AeroVironment</bdi> בעל...     <bdi dir="ltr">3</bdi> דולר, צמצם ב-<bdi dir="ltr">75%</bdi> את טיסו...
```

A full scrape of every text node in the rebuilt `daily_2026-09-08.html` for a Hebrew character
directly touching a Latin letter with zero gap (the objective defect signature established during
this investigation) returned **zero matches** across the whole document.

Verified in the rebuilt `.docx` (python-docx run inspection, same paragraph): each Latin/digit
token is its own non-RTL run with the joining space living in the *neighboring* Hebrew run, e.g.
`'AeroVironment,'` (rtl=False) directly followed by `' בעלות ירי של '` (rtl=True) — not
`'AeroVironment, '` (space trapped in the Latin run).

## 6. Restart needed?

**Python restart: yes**, for the change to take effect in the running orchestrator/API process —
`agent/eoa/report/docx_builder.py` and `agent/eoa/report/textnorm.py` are backend Python modules;
per this project's operating rule, Python changes need the lead to restart the service (this
session only invoked `build_daily` directly via a one-off script against the current source, which
is why the verification above already shows the fix working — that does not substitute for
restarting whatever long-running process serves `GET /api/reports/{id}`).

**No `npm run build` needed** — no `web/**` file was changed (`reportHtml.ts`'s existing
`fixBdiSpacing` client patch was read-only; it is unaffected and remains a harmless no-op for the
cases this backend fix now already emits correctly).

## 7. Out of scope, flagged separately

`agent/eoa/search/deep_search.py` carries an independent, hand-mirrored copy of the same
Hebrew/Latin run-splitting logic (`_split_bidi_runs`/`_bidi_space_and_isolate_line`, wrapping runs
in Unicode LRI/PDI isolate marks instead of HTML `<bdi>`), with the identical trapped-whitespace
defect. Whether this is live/user-visible depends on whether its isolate-wrapped output is ever
displayed directly, or always passed through `textnorm.strip_bidi_isolates` + a report renderer
first (which would already dodge it via this round's fix). Out of this task's ownership
(`agent/eoa/search/**`) — flagged as a background task for investigation and, if needed, a mirrored
fix, rather than touched here.
