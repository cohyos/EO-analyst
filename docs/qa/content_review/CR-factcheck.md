# CR Fact-Check — Line-by-Line Content Review

Date of review: 2026-09-07. Reviewer: independent read-only content-review pass (Claude, general-purpose research agent), cross-checked against `items`/`patents` DB tables (via psycopg, DATABASE_URL on 5432), `config/watchlist.yaml`, and external knowledge. No code, DB rows, or reports were modified.

Scope covered: daily_2026-09-07.md, weekly_2026-09-07.md, monthly_2026-09-30.md, all 8 bd_<territory>_2026-09-07.md, both patent_survey_*_2026-09-07.md, all 6 pl_<line>_2026-09-07.md. Daily/weekly/monthly/bd_il reviewed exhaustively (essentially every factual sentence and every table row checked against its cited item, or checked against a sibling report that shares the same underlying item pool). Other reports sampled at ≥60% of factual claims, weighted toward numeric/corporate/attribution claims and toward claims that reuse items already found to be mis-analyzed elsewhere in the corpus.

Verdict legend: **FALSE** = contradicted by the cited source or by external fact; **UNSUPPORTED** = no evidence in the cited source for the specific claim; **MISLEADING** = technically traceable to a source but framed/labeled/combined in a way that overstates certainty or garbles the meaning; **OK-sampled** = checked against source/DB and found accurate.

---

## daily_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 26–39 | KR investigation: Cheongwang Block-I laser oscillator, 76%→90% localization, +50% laser power, 2-4s→1-2s engagement time, operational since late 2024 | OK-sampled | Matches item 149-investigation sourcing (thedefensenews.com); daily's own "gaps" section correctly notes cross-source consistency | — |
| 66 | US Army $464.8M contract to AeroVironment for Locust X3 (E-HEL), first-of-kind | OK-sampled | Confirmed against item id=10 (Defense News) and item id=47 (EDR): both state $464.8M / AeroVironment / E-HEL | — |
| 53–59 | Estonia/David's Sling indicator rows ("להערכתנו…") | OK-sampled (conclusions) | Grounded in src-2 (Israel Defense) content re: Estonia RFI, Rafael/Raytheon co-production; conclusions follow from stated premises | — |
| 60 | "נראה שהיקף ותנאי עסקת מפעל פולקסווגן יתבררו רק עם פרסום רשמי נוסף" (VW/Rafael deal scope still unclear) | OK-sampled | Correctly hedged — contrast with bd_de/bd_eu below, which state the deal as already concluded | — |
| 67–72 | Tender-forecast table (C-UAS, air-defense seeker, RK"M sight, MALE gimbal, targeting pod, submarine mast, micro-gimbal) | OK-sampled (spot-checked 4/7 rows against cited items) | Percentages/windows/rationale trace to the named items (e.g. row 1 cites item 10/47 correctly for the $464.8M figure) | — |
| 78–143 | Source appendix (66 rows) | OK-sampled | Titles/outlets/URLs spot-checked (10 rows) match DB `items.title`/`items.url` | — |

No FALSE or UNSUPPORTED claims found in the daily report itself; it is the most conservatively-hedged of the four core reports. It shares templated forecast rows with weekly/other bd_ reports — see recurring issues below.

---

## weekly_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 133 | "תע״א, באמצעות Ophir Optronics, השיקה עדשת זום..." (IAI, via [subsidiary] Ophir Optronics, launched a new lens) | **FALSE** | Ophir Optronics is an **MKS Instruments** company (`config/watchlist.yaml` alias list: "MKS Ophir"; source article item id=39 clean_text ends "photo courtesy MKS" and never mentions IAI). IAI has no ownership stake in Ophir Optronics. | Item analysis fabrication — item 39's own `summary_he`/`key_facts` in the DB already assert "חברה בת של תעשייה אווירית (תע״א)" (IAI subsidiary), a fact invented by the ingestion/summarization step with zero support in the source text. |
| 460, 475 | Patent CN112074705A ("optical inertial tracking of moving object") listed as an **Anduril** patent, value-score 12 | **FALSE** | DB `patents.assignees` = ['Anduril'], but the stored abstract/raw snippet is: "...implemented based on fpga lcmxo3lft-2100e-5UWG49 CTR50(**Lattice Semiconductor Corporation**, USA)." This is a chip vendor name, unrelated to Anduril. `entity_ids: [7]` confirms the item was linked to Anduril's entity record. | Entity/alias mismatch — Anduril's watchlist alias "Lattice" collided with "Lattice Semiconductor Corporation" (an unrelated FPGA maker) in the entity-linking step. |
| 108, 476 | US10506436B1 "Lattice mesh" (real Anduril patent) described as an optical lens/mirror/fiber array that "improves image quality, reduces distortion" | **FALSE** | DB `patents.abstract` for this pub_number is literally an assignment-transfer notice ("2019-03-07 Assigned to Anduril Industries Inc...") with **zero technical content**. The "claims_summary_he" analysis inventing lenses/mirrors/fibers/geometric arrangement is fabricated from nothing. (Real Anduril "Lattice Mesh" almost certainly concerns networked sensor/command-and-control mesh, not optics.) | Item analysis fabrication — LLM hallucinated a plausible-sounding but invented technical description when the source abstract carried no usable content. |
| 432 | Business-events row: "Elbit \| מיזוג/רכישה \| Anduril" (Elbit–Anduril **merger/acquisition**) | **MISLEADING** | Source (item 81/src-37) describes Elbit and Anduril jointly pitching the Sigma 155 howitzer to the US Army (cannon + C2/autonomy integration) — a **product partnership**, not any M&A/ownership transaction. | Pipeline event-type misclassification. |
| 502–504 | Greece air-defense deal cited three times at three different values: "$4 מיליארד" (src-29/item 155), "3.1 מיליארד אירו" (src-36/item 90, "$3.6B" per item text), "3.5 מיליארד אירו" (src-50/item 150) | **MISLEADING** | All three figures trace to genuinely different Globes articles about the same underlying Greek deal, but the weekly report states all three as flat fact in three different sections with no reconciliation note (contrast: bd_gr's own analyst note explicitly flags this as "a deal still being finalized" — see below). A reader skimming any one section gets a different, uncaveated number. | Renderer/prompt — no cross-source reconciliation step for conflicting numeric facts about the same event. |
| 225–229 | Investigation "key facts": "נורקין נבחר לראש פעילות אנדוריל בישראל" stated flatly, vs. the same investigation's opening line ("ההכרעה בנושא זה טרם אושרה סופית") | OK-sampled (minor tension only) | Item 81's own summary_he uses "בחרה" (has selected/chosen) language identically, so the flat framing is grounded in the source's own wording, not invented; only a mild internal-register inconsistency. | — |
| 230 | "אנדוריל שותפת עם אלביט במערכת ההאובצר Sigma 155" | OK-sampled | Confirmed verbatim in item 81's clean_text ("The companies are working together to offer the US Army the Sigma 155 howitzer system..."). | — |
| 253, 258 | AeroVironment $464.8M Locust X3 E-HEL details (investigation section) | OK-sampled | Matches items 10/47/army.mil/avinc.com sourcing. | — |

---

## monthly_2026-09-30.md

(Shares almost all narrative content with weekly_2026-09-07.md — same underlying 90-day item pool — so the Ophir/IAI, CN112074705A, and Greece-figure issues above recur here too. Only monthly-specific/new issues are tabled below; see weekly table for the shared ones, which also apply at lines 65, and the CN patent issue does **not** recur in monthly's own patent table but the Ophir and M&A-mislabel issues do.)

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 65 | "תעשייה אווירית (תע״א), באמצעות **חברת הבת** Ophir Optronics" (IAI, via its **subsidiary** Ophir Optronics) | **FALSE** | Same as weekly line 133; monthly's phrasing is even more explicit ("subsidiary company"), making the error more emphatic. | Item analysis fabrication (same root item, id=39). |
| 137 | Business-events row: "מיזוג/רכישה \| Anduril, Amikam Norkin \| Israel Ministry of Defense and IDF \| **10,000,000,000 USD**" | **FALSE** | Cited source [37]/item 81 (Norkin-appointment article) contains **no transaction of any kind** with IMOD/IDF, and mentions only Anduril's own **company valuation** estimate of ~$100B (not $10B, not a deal). The row invents a fictitious $10B M&A event, wrong client ("Israel Ministry of Defense and IDF" never appears as a counterparty), wrong event type (this is a personnel appointment, not M&A). | Pipeline/structured-extraction fabrication — the event-extractor generated a numeric business-event row from an item that describes no such event. |
| 499 | "10 האירועים המובילים לפי היקף כספי" (top-10 events by $ value) table: "m_and_a \| Anduril \| 10,000,000,000 USD \| [37]" | **FALSE** | Same fabricated $10B figure as line 137, now promoted into the report's "top 10 biggest-value events" ranking — the single most prominent placement of this error in the corpus. | Same as above; the fabricated row was carried forward into a second derived table without re-validation. |
| 120, 138 | Two separate business-event rows for "Elbit \| זכייה בחוזה \| 270,000,000 USD," one dated 2026-09-02 [src-24, Israel Defense] and one dated 2026-09-01 [src-57, Airforce Technology] | **MISLEADING** | Both articles describe the same underlying $270M Elbit SPECTRO/AMPS-NG ISR deal (confirmed: src-24 and src-57 titles both reference the SPECTRO/ISR award); counting it twice inflates the apparent deal volume for the period. | Pipeline — no dedup of the same underlying event reported by two outlets on different days. |
| 497 | "10 events by value" row: "regulation \| Elbit \| 32,000,000,000 USD" | **MISLEADING** (number OK, category wrong) | $32B figure is correct (matches item 114's Elbit backlog disclosure exactly), but labeling a quarterly-earnings/backlog announcement as "regulation" is a category error. | Pipeline event-type classifier misfire. |
| 682–691 | "סיכום חודשי לפי חברה" — Ophir Optronics: 1 mention, 0 wins, 0 active competitors, listed directly under Elbit/IAI/Rafael | OK-sampled | Consistent with the (fabricated) Ophir/IAI-subsidiary framing carried through the rest of the report — not a separate error, downstream of the line-65 issue. | — |

---

## bd_il_2026-09-07.md (exhaustive)

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 23 | "תעשייה אווירית (Ophir Optronics) השיקה עדשת זום..." | **FALSE** (by implication) | Parenthetical framing again implies Ophir = IAI's optics arm. Same root cause as weekly/monthly. | Item analysis fabrication (item 39). |
| 62 | "Elbit \| מיזוג/רכישה \| Anduril" (M&A row, no amount) | **MISLEADING** | Same Sigma-155-partnership-mislabeled-as-M&A issue as weekly line 432. | Pipeline event-type misclassification. |
| 100 | "רכש ופלטפורמות" row: "2026-09-01 \| מטוס קרב \| Israel Ministry of Defense and IDF \| Anduril \| **10,000,000,000 USD** \| פוד כיוון (Targeting Pod) \| [13]" | **FALSE — most severe finding in the corpus** | Source [13] is again the Norkin-appointment article (item 81). It contains **no fighter jet, no targeting pod, no IMOD/IDF procurement, and no $10 billion transaction of any kind.** This single row invents an entire fictitious platform-level defense sale (fighter jet, Anduril-supplied targeting pod, $10B, IMOD/IDF as buyer) out of a story about an executive hire. It appears in bd_il's own procurement pipeline table as an "A"-tier near-term opportunity signal. | Pipeline/structured-extraction fabrication — the same event-extractor bug as monthly lines 137/499, here additionally cross-contaminated with an unrelated "מטוס קרב / פוד כיוון (Targeting Pod)" forecast template (identical wording to the real fighter-jet/targeting-pod forecast rows elsewhere in the corpus), suggesting a template/row got attached to the wrong source item. |
| 101 | Same table, row 2: "— \| מטוס קרב \| Israel \| Israel \| 3,600,000,000 USD \| פוד כיוון (Targeting Pod) \| [8]" | **FALSE/nonsensical** | Buyer and supplier are both listed as "Israel" for a "fighter jet" platform. Source [8] is the Greece air-defense-deal article (item 90, "$3.1B/€3.6B" figure) — a garbled re-use of that dollar figure into a nonsensical buyer=seller=Israel row. | Pipeline/structured-extraction fabrication (same generator bug as bd_gr line 84, below). |
| 21–28 | "תמונת שוק" bullets (Elbit $270M ISR, Serbia JV 51%, Ophir lens, IAI Blue Spear/Singapore, IAI-Germany LORA firing, Elbit vessel-to-carrier concept, Estonia David's Sling, Greece $4B deal) | OK-sampled (6/8 checked) | Confirmed against items 24 (Elbit ISR), 28 (Serbia), 7 (Blue Spear), 18/44 (LORA/Germany), 4 (Estonia), 15/155 (Greece $4B). Only the Ophir framing (line 23, above) and the Greece $4B figure (uncaveated vs. weekly's own conflicting $3.1B/$3.5B figures, though bd_il only cites the $4B figure so internally consistent) are problematic. | — |
| 137–145 (action table) | "לעקוב אחר מינויו הצפוי של... נורקין..." recommendations | OK-sampled | Grounded in item 81. | — |

---

## bd_de_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 7, 11, 23, 45, 70, 97–98 | "רפאל רכשה את מפעל פולקסווגן בגרמניה להקמת קו ייצור מקומי של מערכת כיפת ברזל" (Rafael **has purchased/acquired** the VW plant), repeated 6× across bottom-line, exec summary, market picture, assumptions, opportunity pipeline and action-item sections | **UNSUPPORTED / MISLEADING** | Cited source item (id=6872, Israel Hayom, "מפעל פולקסווגן **נמכר** לרפאל") has **empty `clean_text` and `key_facts`** in the DB — nothing beyond the headline was ever ingested. A separate, contemporaneous item in the same corpus (id=168, "**Rafael-VW talks continue** despite Qatari opposition") explicitly states the negotiation is still **ongoing** ("דיווח על המשך משא ומתן..."), and the corpus's own daily/weekly investigations (daily indicator line 60; weekly investigation #113/#25) independently hedge this as unresolved ("לא נמצא מידע מספק"). bd_de presents a headline-only, content-less item as a completed acquisition with no hedge anywhere in the report. | Data gap treated as fact — an empty-content item's headline (itself ambiguous/possibly a translation of "in talks to sell") was read as a concluded transaction, without cross-checking the sibling item that explicitly contradicts it. |
| 3–5, 76–77 | German Navy LORA firing (Marine 2035), Rheinmetall/Hensoldt Skymaster passive-sensor demo, Hensoldt Ubifly e200X avionics | OK-sampled | Confirmed against items 38/44/9. | — |

---

## bd_eu_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 11, 22, 77, 117 | "פרויקט כיפת ברזל בגרמניה מתקדם בפועל עם מכירת מפעל פולקסווגן לרפאל" (the plant sale to Rafael is a done deal) | **UNSUPPORTED / MISLEADING** | Same empty-content item (6872) / contradicting "talks continue" item (168) as bd_de above. | Same as bd_de. |
| 21, 25–26, 30–35 | Estonia David's Sling (5 bidders), Leonardo Centauro II Brazil, Rheinmetall/Hensoldt Skymaster demo, Finland TED computer-vision tender, UVision/Mistral $50M | OK-sampled | Confirmed against items 4/11/10/15. | — |

---

## bd_gb_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 7, 21–23 | UK MoD Project PANOPTES (multi-vendor RFI), Saab Giraffe 1X to RAF (urgent operational requirement), Elbit/ESUK $370M CBP orders, Kongsberg StrikeMaster Arctic exercise | OK-sampled | All confirmed against items 3/2/32/1. Report correctly frames PANOPTES as RFI-stage, no premature "won" framing. | — |

No FALSE/UNSUPPORTED claims found; this is one of the better-disciplined bd_ reports (appropriately hedges lack of RFP detail, doesn't overclaim).

---

## bd_gr_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 13 | Analyst note explicitly flags the $4B/€3.1B/€3.5B figures as signs of "a deal that is still being finalized, not yet closed" | OK-sampled (**best practice** in the corpus) | Contrast with weekly/monthly, which state all three figures as flat fact without reconciliation — bd_gr is the one report that correctly treats the discrepancy as a signal rather than silently repeating it. | — |
| 84 | "רכש ופלטפורמות" row: "— \| מטוס קרב \| Israel \| Israel \| 3,600,000,000 USD \| פוד כיוון (Targeting Pod) \| [1]" | **FALSE/nonsensical** | Buyer field = "Israel", supplier field = "Israel" for a "fighter jet" platform, sourced from the Greece-deal article (item 90, whose text says "$3.1B/€3.6B" for an **air-defense** deal, not a fighter jet). The row is a garbled re-purposing of that dollar figure into an unrelated, self-contradictory platform row. | Pipeline/structured-extraction fabrication (same generator bug producing the identical pattern in bd_il line 101). |
| 33–35 | Table row "מטוס קרב \| Rafael \| €3,500,000,000 \| פוד כיוון (Targeting Pod) \| [4]" | **MISLEADING** | Source [4] (item 150, "Huge Greek air defense deal") is about an **air-defense system** package (David's Sling/Spyder/Barak MX), not a fighter jet or targeting pod; "Rafael" as sole supplier for €3.5B ignores IAI's co-supplier role stated in the same source. | Same structured-extraction issue. |
| 23–29 | Greece $4B deal, Omnisys BRO-API, three IIR/targeting-pod/MALE forecast rows at 60% | OK-sampled | Confirmed against items 155/48 and forecast items. | — |

---

## bd_in_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 7, 19, 27 | "המתחרה הישראלי רפאל **מתכננת** להקים קו ייצור מקומי לטילי טמיר בהודו" | OK-sampled (appropriately hedged with "מתכננת"/"בוחנת") | Source item title itself is "Rafael to produce Iron Dome interceptors in India **- report**" (i.e., unconfirmed/planning-stage); bd_in's hedged phrasing matches this uncertainty reasonably well, unlike the VW/Rafael case in bd_de/bd_eu. | — |
| 20–23 | Hensoldt/Ubifly e200X avionics, AeroVironment $464.8M E-HEL, Elbit $370M CBP, Ultra Maritime sonobuoys | OK-sampled | Confirmed against items 4/(10,47)/32/13. | — |

---

## bd_kr_2026-09-07.md

Entirely a "no activity found" placeholder report (correctly says so); no factual claims to check. OK-sampled by default — no fabrication risk since it makes no substantive assertions.

---

## bd_us_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 55 | "Elbit \| מיזוג/רכישה \| Anduril" (M&A row) | **MISLEADING** | Same Sigma-155-partnership mislabeled as M&A, recurring a fourth time in the corpus (weekly, monthly[implicit], bd_il, bd_us). | Pipeline event-type misclassification. |
| 69–70, 140–141 | "מיצוב IP" / patent table: CN112074705A and US10506436B1 listed as Anduril patents, value-score 12 each | **FALSE** | Same two fabricated/mis-attributed patents as weekly (see above); recurs here in the US-territory competitor-IP section. | Entity/alias mismatch (CN patent) + item analysis fabrication (US10506436B1 claims text). |
| 11, 23–36 | AeroVironment $464.8M, MMA $10M target, Halo_Shield 3-exercise proof, UVision/Mistral $50M, Shield AI Taiwan >$1B/280 V-BAT, Leonardo DRS Space Force prototype, XTEND $1.5B | OK-sampled (7/7 checked) | Confirmed against items 10/47, 39/8, 11, 34, 58, 20, 2/45. | — |

---

## patent_survey_Anduril_Lattice_counter-UAS_EO_IR_optical_tracking_patents_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 20, 47, 63, 108 | US10506436B1 "Lattice mesh" described in detail as an optical lens/mirror/fiber array "that improves image quality, reduces distortion, adds system flexibility" | **FALSE** | DB abstract for this patent is a bare USPTO assignment notice with no technical content whatsoever (see weekly table above). The entire "התקדמות פטנט" analysis is invented. This is the report's headline "white-space" patent (⁦"אשכול נושאי: lattice / mesh"), so the fabrication anchors one of the report's five technology clusters. | Item analysis fabrication. |
| 17, 27, 39, 51, 124 | Executive-summary and business-recommendation claims built on "Anduril's clear filing lead," at stated confidence levels of 80–90% | **MISLEADING (overstated confidence)** | 3 of Anduril's 4 listed patents (US20230082239A1, US11385659B2, US20200363824A1) have abstracts the report **itself** repeatedly flags as "אינו מספק מספיק מידע לניתוח תביעות מלא" (insufficient for full claims analysis); the 4th (Lattice Mesh) is the fabricated one above. Recommending "90% confidence" strategic actions (line 124: avoid promoting near Anduril/no counter-marketing) on a foundation this thin overstates the evidentiary basis. | Prompt/renderer — confidence score not discounted for source-abstract insufficiency that the same report explicitly acknowledges elsewhere. |
| 1–13 | Method/scope note: date range 2016-03-11–2021-09-15, 6 records, "Google Patents (חיפוש חסר-מפתחות)" | OK-sampled | Consistent with the 6 patents listed in the appendix; correctly flags the keyless-search limitation. | — |

---

## patent_survey_FPA_עם_פיקסל_דיגיטלי_DROIC_2026-09-07.md

Sampled 8/10 patent entries plus the executive summary and White-Space section.

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 17–26, 99–108 | Per-patent "התקדמות" claims analysis for all 10 patents (Raytheon Co ×3, Sensors Unlimited ×3, Sabanci Universitesi ×2, MIT ×1, Black Forest Engineering ×1) | OK-sampled | Every entry explicitly and repeatedly states "התקציר אינו מספק מספיק מידע לניתוח תביעות מלא" (abstract insufficient) rather than inventing detail beyond what the abstract actually supports — the opposite discipline from the Anduril/Lattice report above. Titles/assignees/CPC codes are consistent with plausible real Google Patents records for these pub numbers. | — |
| 16, 68–70 | "אין ולו רשומת פטנט אחת עם בעל-פטנטים ישראלי" (no Israeli patent-holder in the sample), correctly caveated as possibly a search-coverage gap rather than a real absence | OK-sampled | Appropriately hedged, does not overclaim. | — |

No FALSE/UNSUPPORTED claims found. This report is the best-disciplined document in the corpus for its treatment of thin-abstract patents.

---

## pl_ball_gimbals_16in_2026-09-07.md / pl_lorop_pods_2026-09-07.md

Both product-line reports are built from the same single source item (Elbit $270M SPECTRO/AMPS-NG ISR deal, item id≈65/24/57) and both explicitly and repeatedly flag that the source **does not specify** whether 16-inch ball gimbals or LOROP pods specifically are included in the contract ("לא מפרט האם מדובר במטע״דים כדוריים 16 אינץ׳ באופן ספציפי"). This is honest hedging, not fabrication.

| Verdict | Evidence |
|---|---|
| OK-sampled | Both reports correctly attribute the $270M figure to item 24/57 and correctly avoid asserting product-line-specific inclusion. |

One systemic note: the same $270M Elbit deal is stretched across at least 3 separate product-line reports (ball gimbals, LOROP, and — via the daily/weekly SPECTRO-family framing — targeting pods) as the sole evidentiary basis for "high priority" action items in each. Each report hedges appropriately on its own, but a reader consuming all three in parallel would not realize they're all leaning on the identical single ambiguous data point. **MISLEADING at the corpus level**, not within any one report.

---

## pl_border_long_range_eo_2026-09-07.md

No items in window; report correctly states "אין ממצאים." OK-sampled (nothing to fact-check).

---

## pl_eo_air_defense_warning_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 23–24, 30–31 | Two near-duplicate "IIR Seeker/EO Tracker" forecast rows at 60% probability, windows 2027-03-05→2029-03-05 [1] and 2027-03-06→2029-03-06 [2] | **MISLEADING (unverifiable)** | The "נספח מקורות" source-appendix table (lines 33–37) is **completely empty** — no rows at all — so [1] and [2] cannot be traced to any actual item. The one-day date drift between the two rows suggests the same underlying forecast was regenerated on two different pipeline runs and both copies were kept instead of deduplicated. | Pipeline — forecast dedup failure + broken/missing source-appendix rows. |

---

## pl_mws_eo_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 19–25 | Three DIRCM patents (US7378626B2, US20030142005A1, US20070075182A1) listed with owner "—" (unknown/not stated) | OK-sampled | Report honestly declines to attribute an owner rather than inventing one — correct behavior given no assignee data was available. | — |

No FALSE/UNSUPPORTED claims found.

---

## pl_targeting_pods_2026-09-07.md

| Line | Quoted claim (short) | Verdict | Evidence | Root-cause guess |
|---|---|---|---|---|
| 23, 48 | Business-event row: "ניסוי \| Rafael \| US Department of Defense" ("trial/experiment" event, parties Rafael + US DoD) | **MISLEADING** | Cited source [1] (item 5122, usarfp.com) is a **tender/RFP notice** ("The DEPARTMENT OF THE AIR FORCE... has announced a new tender for Litening advanced targeting pod"), issued by the **Department of the Air Force** specifically (not "US Department of Defense" generically), and does **not** describe any Rafael-DoD trial or interaction — Rafael is only the pod's manufacturer, mentioned by product name. | Pipeline event-type misclassification (tender notice read as a "trial/experiment" event) + imprecise agency attribution. |
| 66–67, 73–74 | Duplicate "מטוס קרב / פוד כיוון (Targeting Pod)" forecast rows at 60% [1] and 40% [2], windows differing by one day (2027-03-05 vs 2027-03-06 start) | **MISLEADING (unverifiable)** | [2] is not listed in the source appendix (only src-1 exists, line 88) — the second row's citation is broken/untraceable, same pattern as pl_eo_air_defense_warning. | Pipeline — forecast dedup failure + broken citation. |
| 7, 11 | "יש לעקוב מיידית אחר מכרז ה-Litening... תחזית רכש... 60%" | OK-sampled | Consistent with the (single, valid) src-1 tender listing. | — |

---

# Ranked list — 15 most severe problems

1. **bd_il line 100** — Fabricated $10,000,000,000 Anduril "fighter jet / targeting pod" procurement row from the IMOD/IDF, invented wholesale from an executive-appointment article that mentions none of it. FALSE. (item analysis / pipeline fabrication)
2. **monthly lines 137 & 499** — Same fabricated $10B Anduril "m_and_a" event, promoted into the report's own "top 10 events by financial value" ranking. FALSE. (pipeline fabrication)
3. **weekly line 133 / monthly line 65 / bd_il line 23** — Ophir Optronics falsely stated to be a subsidiary of IAI/Israel Aerospace Industries; it is in fact an MKS Instruments company. FALSE, and explicitly named as a required check in the review brief. (item analysis fabrication, traced to item id=39's own DB summary/key_facts)
4. **weekly lines 460/475, bd_us lines 69–70/140** — Patent CN112074705A attributed to Anduril; the stored snippet actually names "Lattice Semiconductor Corporation," an unrelated FPGA vendor, mismatched via Anduril's "Lattice" alias. FALSE, and explicitly named as a required check in the review brief. (entity/alias-collision)
5. **patent_survey_Anduril_Lattice (multiple lines), recurring in weekly/bd_us patent tables** — Anduril's real patent US10506436B1 ("Lattice mesh") is given a fully invented technical description (optical lens/mirror/fiber array) when its actual DB record contains only a non-technical assignment notice. FALSE.
6. **bd_de (6 locations) / bd_eu (4 locations)** — "Rafael has purchased the Volkswagen Osnabrück plant" stated as a completed fact from a title-only, content-empty item, while a separate corpus item explicitly says talks are still ongoing and other reports (daily, weekly) correctly hedge this as unresolved. UNSUPPORTED/MISLEADING.
7. **weekly line 432 / bd_il line 62 / bd_us line 55** (and implicitly monthly) — Elbit–Anduril Sigma 155 howitzer co-marketing partnership mislabeled as "מיזוג/רכישה" (merger/acquisition) in the business-events/M&A-tracking table, four times across the corpus. MISLEADING.
8. **bd_il line 101 / bd_gr line 84** — "רכש ופלטפורמות" rows list buyer = supplier = "Israel" for a nonexistent "fighter jet" platform valued at $3.6B, garbled from the Greece air-defense deal's dollar-equivalent figure. FALSE/nonsensical.
9. **weekly lines 502–504 / monthly (parallel sections)** — The Greece air-defense deal is quoted at three irreconcilable values ($4B, €3.1B/$3.6B, €3.5B) in three different sections of the same report with no reconciliation note, unlike bd_gr which explicitly flags the discrepancy. MISLEADING.
10. **monthly lines 120 & 138** — The same $270M Elbit SPECTRO/ISR deal, reported by two different outlets on two different days, is counted as two separate business events, inflating the period's apparent deal count. MISLEADING.
11. **pl_targeting_pods lines 23/48** — A Litening targeting-pod tender notice from the US Air Force is mislabeled as a "ניסוי" (trial/experiment) between Rafael and "US Department of Defense," implying an interaction the source never describes. MISLEADING.
12. **pl_eo_air_defense_warning lines 23–24/30–31 and pl_targeting_pods lines 66–67/73–74** — Duplicate forecast rows with one-day-drifted date windows, one of each pair citing a source number ([2]) that does not exist in that report's own (empty or truncated) source appendix — unverifiable, likely un-deduplicated pipeline reruns.
13. **monthly line 497** — Elbit's correctly-cited $32B order-backlog disclosure is filed under event type "regulation" instead of an earnings/backlog category. MISLEADING (low severity, number itself is correct).
14. **patent_survey_Anduril_Lattice lines 17/27/39/51/124** — Strategic recommendations issued at 80–90% stated confidence rest substantially on patents whose own abstracts the report repeatedly admits are "insufficient for full claims analysis," plus the fabricated Lattice-Mesh entry (#5 above). MISLEADING (confidence overstatement).
15. **Corpus-wide** — The single ambiguous $270M Elbit SPECTRO/AMPS-NG deal (no confirmed customer, no confirmed exact payload mix) is independently used as the sole "high priority, act immediately" evidentiary basis in at least three separate product-line reports (ball gimbals 16in, LOROP pods, and via the daily/weekly framing, targeting pods generally) without cross-referencing each other — each instance is individually hedged but the aggregate effect overstates how much independent evidence exists for each product line.

# Per-root-cause count

Counting each report-line instance separately (a claim repeated in N reports counts N times):

| Root cause | Count | Examples |
|---|---|---|
| Item analysis fabrication (ingestion/summarization LLM invented facts not present in source text) | 6 | Ophir/IAI subsidiary (×3: weekly, monthly, bd_il); US10506436B1 fabricated optical-lens claims (×3: patent_survey, weekly patent table, bd_us patent table) |
| Pipeline / structured-extraction fabrication (event- or platform-row generator producing rows not grounded in the cited item, including garbled/self-contradictory rows) | 11 | $10B Anduril fighter-jet/M&A rows (×3: monthly ×2, bd_il); Israel=Israel $3.6B garbled row (×2: bd_il, bd_gr); Elbit–Anduril "מיזוג/רכישה" mislabel (×4: weekly, bd_il, bd_us, +monthly implicit); Litening "ניסוי" mislabel (×1); Elbit $32B "regulation" mislabel (×1) |
| Entity/alias collision (NER or patent-assignee linking matched an unrelated name via a watchlist alias) | 2 | CN112074705A "Lattice Semiconductor" → Anduril (weekly, bd_us) |
| Unreconciled source contradiction (renderer/prompt failed to flag or reconcile conflicting numeric facts about the same event) | 2 | Greece deal $4B/€3.1B/€3.5B (weekly, monthly) |
| Data gap treated as fact (empty-content/title-only item combined with confident phrasing, contradicted by a sibling item the pipeline had already ingested) | 2 | VW/Rafael plant "purchased" (bd_de, bd_eu) |
| Duplicate/un-deduplicated forecast rows with broken source citations | 2 | pl_eo_air_defense_warning, pl_targeting_pods |
| Confidence/certainty overstatement relative to acknowledged source thinness | 1 | patent_survey_Anduril_Lattice recommendations |

**Totals: 26 distinct problem-instances logged across the corpus, 5 of them independently verified as FALSE corporate/attribution/financial facts (not just "unsupported" or "misleading" framing).**
