# תוכנית סבב 5 — סגירת כל פערי הבנצ'מרק (docs/REPORT_TEMPLATE_BENCHMARK.md)

תאריך: 2026-09-06 ערב. עיקרון: אף פער לא יורד מהרשימה; מיקבול מקסימלי לפי בעלות על קבצים;
ניצחונות גדולים קודם; כל חבילה נבדקת ביחידה, נאספת ל-git, ובסוף הסבב — הפעלה מחדש, בנייה
מחדש של כל הדוחות, ניקוד סבב 5 ושופט J5. הלולאה ממשיכה אחר כך.

## גל א' — מתחיל מיד (קבצים פנויים)

| חבילה | פערים מהבנצ'מרק | קבצים (בעלות בלעדית) |
|---|---|---|
| P1 חודשי לסכמה מובנית | M1, M2, M3 | agent/eoa/report/monthly.py, agent/eoa/llm/schemas/monthly*.py (חדש/קיים), agent/eoa/llm/prompts/report_monthly.md, tests/unit/test_monthly_round5.py |
| P2 "מה השתנה" + מעקב אינדיקטורים + איחוד ישראל | D4, D5, W3, M2, B4 (צד הנתונים), D6-כפילות ישראל | db/migrations/versions/0023_indicator_watchlist.py, agent/eoa/report/deltas.py (חדש), agent/eoa/report/indicators.py (חדש), agent/eoa/report/daily.py, weekly.py (אספנים בלבד), agent/eoa/report/israel_section.py, tests/unit/test_report_deltas_round5.py |
| P5 סקר פטנטים | P1, P2, P3, P5 + נקודות פתוחות מסבב 3 (משפט כיסוי עם 17 ציטוטים, מקצה "Europe", אשכול "לא מסווג", קשר Anduril–Elbit לא מאומת) | agent/eoa/patents/**, agent/eoa/llm/schemas/patents.py, agent/eoa/llm/prompts/patent_survey.md, agent/eoa/patents/render.py, tests/unit/test_patents_round5.py |
| P8 צ'אט — ראשי תיבות ומונחים מומצאים | ממצא שופט 3/8 (D5: MAEC/RSPEOT, שוויון ישויות שגוי, סתירה פנימית) | agent/eoa/api/ask_grounding.py, tests/unit/test_ask_round5.py |
| P9 מדד QA לפערים החדשים | כל הפערים — בדיקות דטרמיניסטיות | agent/eoa/qa/d6_daily_report.py, d7_bd_report.py, d8_patent_survey.py, d4_*.py, tests/unit/test_qa_score.py |

## גל ב' — מתחיל כשסוכני סבב 4b משחררים את הקבצים

| חבילה | פערים | קבצים | ממתין ל- |
|---|---|---|---|
| P3 BLUF + הפרדת סבירות/ביטחון + הנחה↔הפרכה + איסור ניסוחי מילוי בפרומפטי הדוחות | D1, D2, W2, W4, B5, ממצא שופט D2 | agent/eoa/llm/schemas/analysis.py (Sentence/OutlookIndicator/BLUF), agent/eoa/llm/schemas/bd_territory.py, prompts report_daily/weekly/bd_territory/monthly, daily.py/weekly.py (הרכבת הטיוטה) | R4b-taxonomy (פרומפטים) |
| P4 מנוע התצוגה | D3 (BLUF דטרמיניסטי בכשל), D7 (אמינות מקור בנספח), DS3 (blocked ≠ not_found), W5 (קישור טבלאות למגמות), P1 (תיבת שיטה) | agent/eoa/report/docx_builder.py, agent/eoa/report/textnorm.py | R4b-bd (docx_builder) |
| P6 BD | B1 מפת קונים/צינור, B2 דירוג הזדמנויות, B4 דלתא טריטוריאלית, B5 הנחות | agent/eoa/report/bd_territory.py, schemas/bd_territory.py, prompts/report_bd_territory.md | R4b-bd |
| P7 חקירות עומק | DS3 — outcome `blocked` נפרד, תצוגה ביומי/שבועי/UI | agent/eoa/search/deep_search.py, schemas/analysis.py (InvestigationOut), web/src/pages/InvestigationsListPage.tsx + Detail | R4b-taxonomy (deep_search.py), R4-ui סיים |
| W19b מטע"דים במשפחות | בקשת משתמש | web/src/pages/PayloadsPage.tsx, components/payloads/**, api/routes/payloads.py | R4b-ui |

## סגירת סבב 4 (במקביל לגל א')

הפעלה מחדש → בנייה מחדש: שבועי, יומי, BD (US/IL/DE/GR/KR) → `qa_score.py --round 4 --e2e` → שופט J4
→ מיזוג. סבב 5 נמדד אחרי גל ב'.

## סדר עדיפות בתוך החבילות (ניצחונות גדולים קודם)

1. BLUF + כשל חלקי ביומי (P3+P4) — D6 15%.
2. חודשי לסכמה (P1).
3. איחוד ישראל + "מה השתנה" (P2).
4. סבירות/ביטחון (P3), blocked (P7), BD מפת קונים (P6).
5. פטנטים (P5), צ'אט (P8), אמינות מקור (P4), מדדים (P9).

## סטטוס P2 (2026-09-06 -- הושלם)

כל ארבעת המסירות של P2 הושלמו ונבדקו:

1. **"מה השתנה מאז הדוח הקודם"** (`agent/eoa/report/deltas.py`, חדש) — דלתא דטרמיניסטית מול הדוח
   הקודם מאותו `kind` (יומי/שבועי, ופונקציה גנרית שיכולה לשמש גם BD-טריטוריאלי בגל ב', לא חוברה
   שם בסבב זה): פריטים חדשים (עד 3 מובילים עם [n]), פריטים שעלו ברמה, ומגמות
   שהתחדשו/התחזקו/נחלשו/נעלמו (שבועי בלבד). נרינדר כ-`extra_sections` מיד אחרי תקציר המנהלים
   (`position="after_summary"`), עם שורה כנה אחת כשאין דוח קודם.
2. **מעקב אינדיקטורים** (`agent/eoa/report/indicators.py` + מיגרציה `0023`) — טבלת
   `indicator_watchlist` עוקבת אחרי כל אינדיקטור (`OutlookIndicator.text_he`) לאורך גיליונות:
   הבשלה (התאמת מונחי-מפתח דטרמיניסטית מול פריטי הגיליון הנוכחי), ביטול (30 יום בלי התאמה),
   ודה-דופ (דמיון טקסט מנורמל ≥0.85) של אינדיקטורים חדשים מול הפתוחים. נרינדר כטבלת מארקדאון
   ("מעקב אינדיקטורים") ב-`extra_sections` מיד אחרי "מבט קדימה" (`position="after_outlook"`).
   המודל אינו רואה או כותב את הטבלה הזו כלל.
3. **איחוד תעשייה ישראלית** (`agent/eoa/report/israel_section.py`) — ארבע טבלאות הקטגוריה מוזגו
   לטבלה אחת עם עמודת "סוג" (זכייה/תחרות/יצוא/איום, מחוברות ב-"/" כשפריט שייך ליותר מקטגוריה
   אחת), שורה אחת לכל פריט. שמות הפונקציות `daily_israel_tables`/`weekly_israel_tables` (וה-API
   שלהן) נשארו ללא שינוי.
4. **מיגרציה `0023`** (`db/migrations/versions/0023_indicator_watchlist.py`) — הוחלה ואומתה חיה:
   `alembic current` = `0024` (head, משורשר דרך `0024_payloads_variant.py` שנוסף במקביל);
   `reports.report_state` (JSONB) וטבלת `indicator_watchlist` קיימות ב-DB עם העמודות הצפויות.

חיווט ב-`daily.py`/`weekly.py`: קוד אספנים/הרכבה בלבד (אחרי `normalize_draft`, בטרם רינדור), כל
קטע בתוך `try/except` משלו כמו כל הקטעים האדיטיביים האחרים -- כשל בהם לעולם לא שובר את הדוח.
`_persist_report` בשני המודולים מקבל כעת `report_state` ושומר אותו לעמודה החדשה.

**בדיקות**: `tests/unit/test_report_deltas_round5.py` (חדש, 38 בדיקות) + תוספות ל-
`tests/unit/test_israel_section.py` (+9). `pytest tests/unit -q -k "delta or indicator or israel
or report_daily or report_weekly"` (עם `DATABASE_URL` מ-`runtime/eoa.env`): **154 עברו, 0 נכשלו**.
ruff (`check`+`format --check`) נקי על כל הקבצים שנגעו בהם.

**ממצא לוואי (לא בתחום P2, לתשומת לב מי שיחווט B4 ל-BD בגל ב')**: כל קריאת DB ב-`deltas.py`/
`indicators.py` משתמשת ב-`connection(timeout=5)` (מוסכמת "סעיף דוח אופציונלי" הקיימת כבר ב-
`bd_territory.py`) -- ולעומת זאת נמצא (חי, תוך כדי בדיקה) קריאת `connection()` חסרת timeout,
קיימת מראש ולא קשורה לסבב זה, ב-`eoa.pipeline.tech_watch.run_tech_watch_weekly` (נקראת מ-
`weekly.py`'s הקיים כבר A12 section) שיכולה לתקוע בדיקת אינטגרציה שלמה של `build_weekly` כש-DB
לא זמין/לא ממוקה. לא טופל כאן (מחוץ לבעלות הקבצים של חבילה זו).

## סטטוס P1 (עודכן 2026-09-06, בוצע)

**M1, M2, M3 סגורים.** `MonthlyReportDraft` (agent/eoa/llm/schemas/reports.py) עבר לסכמה מובנית
(`Sentence`/`StructuredSection`/`OutlookIndicator`, זהה בשמות השדות ל-`WeeklyReportDraft`) —
ציטוט [n] נאכף בבנייה, ללא שינוי נדרש ב-`qa_citations.py`/`textnorm.py`/`docx_builder.py`
(קריאה-בלבד, כמתוכנן). הסכמה הישנה נשמרת כ-`MonthlyReportDraftLegacy` לקריאת דוחות ישנים בלבד.
נוסף `MonthlyTrendSection` (חדש, M2) עם `strength_now`/`strength_prev`/`change` ומעקב חודש-מול-חודש
דרך `reports.qa_report` (`collect_previous_monthly_trends`); מגמות "gone" מוזרקות דטרמיניסטית
בקוד ולעולם לא נכתבות ע"י המודל. `agent/eoa/report/monthly.py` אומץ למבנה זהה ל-`weekly.py`
(input reduction, corrective retry, deterministic fallback לאחר שני כשלונות). `report_monthly.md`
נכתב מחדש. בדיקות: `tests/unit/test_monthly_round5.py` (27, חדש) + עדכון תיקוני-שם ב-3 קבצי בדיקה
לא-בבעלות המשימה (`test_docx_builder.py`/`test_report_qa.py`/`test_report_round3_d6.py`, ששימשו
ב-`MonthlyReportDraft` הישן רק כדוגמת-על לסכמה החופשית — שינוי שם בלבד ל-`MonthlyReportDraftLegacy`)
+ עדכון `test_report_weekly_monthly.py` לפיקסצ'ר החודשי החדש. ראו docs/MODULES.md "Round 5 P1"
לפירוט מלא ותוצאות בדיקות.

## סטטוס P5 (עודכן 2026-09-06, בוצע -- כולל השלמת עבודה שנקטעה ע"י rate limit)

**כל שישת הפערים סגורים.** (1) תיבת "שיטה והיקף" דטרמיניסטית -- שאילתה, מאגרים שנסרקו, טווח
תאריכים, מספר רשומות (חדשות מהסריקה מול מהמאגר), **"כיסוי נתוני מקצה: NN% (k/n)"** כתגית בולטת
משלה, כיסוי CPC, ומשפט הכיסוי הנמוך (כשרלוונטי) -- כל זה כ-`list[str]` דטרמיניסטי אחד
(`methodology_box_lines_he`, `agent/eoa/patents/survey.py`) שמוזרק *לפני* "תקציר מנהלים" בשלושת
הפורמטים (docx/md/html) דרך שלוש פונקציות חדשות ב-`agent/eoa/patents/render.py`
(`insert_section_before_summary_docx`/`_md_summary`/`_html_summary`) -- בלי לגעת ב-`docx_builder.py`
(לזה אין hook "לפני התקציר", רק `after_summary`/`after_outlook`; ההזרקה היא מניפולציית
python-docx/מחרוזת פוסט-רנדור, אותו דפוס שכבר קיים לגרף ה-ASCII/SVG של ציר הזמן). משפט הכיסוי
הנמוך הוסר מ`exec_summary` (שינוי חוזי ב-`_enforce_coverage_caveat` -- כבר לא מוסיף אליו, רק
לסעיפי "פרופיל מקצה"; 3 בדיקות ב-`test_patents_round3.py` עודכנו לחוזה החדש). (2) מקצים כוזבים
(אזור/מדינה/סיומת גנרית כמו "Inc"/"Ltd"/"United States"/"ארה\"ב") נדחים ב-`_is_real_company_assignee`
(regex + `resolve_country_name`) -- כבר ממומש מהעבודה שנקטעה, נוספו בדיקות. (3) כשאין CPC, האשכול
"לא מסווג" מתפצל ל-TF-IDF-lite (מימוש stdlib טהור, ללא sklearn/numpy) לפי מילות מפתח מהכותרת/תקציר,
עד `UNCLASSIFIED_MAX_SUBCLUSTERS` (=5) תת-אשכולות, כל אחד מתויג לפי מונחיו המובילים במקום תווית
גנרית אחת. (4) `_verify_relationship_edges` מוודא שכל צד בקשר אכן מוזכר במקור המצוטט (מילה שלמה,
דרך `find_watchlist_aliases_in_text` -- לא substring גולמי, ולא alias "strict" בלי ליווי השם
הקנוני עצמו -- זה בדיוק מה שתיקן את הרגרסיה: המילה האנגלית הרגילה "anvil" ב"the old blacksmith's
anvil" כבר לא מתפרשת כאזכור Anduril רק כי "Anvil" הוא alias מוצר strict שלה), ומבטל כפילויות לפי
זוג-צד-קנוני+סוג+ציטוט ("Elbit"/"Elbit Systems" לאותו IAI קורסים לשורה אחת) -- נבדק במפורש עם
תרחיש "Sigma 155" מ-round_3_judge.md D8 #4. (5) `PatentBizAction` קיבל `priority`
(high/medium/low) + `confidence` (0-1, ולידציה pydantic) -- מוצג כתחילית `[עדיפות: X | ביטחון: Y%]`
בנרטיב וכטבלה דטרמיניסטית נפרדת ("השלכות עסקיות והמלצות -- עדיפות וביטחון",
`business_implications_table`, ממוינת עדיפות-קודם). (6) מטריצת CPC x מקצה מלאה
(`_cpc_assignee_matrix`) לצד טבלת הפערים הקיימת -- מוצגת רק כששני הצירים לא ריקים (אחרת אין מה
לצייר בכנות).

**בדיקות:** `tests/unit/test_patents_round5.py` חדש, 58 בדיקות (אחת לכל התנהגות/גבול לכל אחד
משישת הפערים) + 3 בדיקות עודכנו ב-`test_patents_round3.py` (חוזה `_enforce_coverage_caveat` החדש).
`pytest tests/unit -q -k "patent or d8"`: 314 עברו, כשל אחד קיים-מראש ולא-קשור
(`test_patents_scan.py::TestScanPatentsOrchestration::test_duplicate_pub_number_across_queries_counted_once`
-- ניסיון חיבור DB אמיתי שנכשל על אימות, ב-`agent/eoa/patents/scan.py` שאינו בבעלות חבילה זו ולא
נגעתי בו; קיים כך גם לפני תחילת החבילה). `ruff check`/`ruff format --check` נקיים על כל הקבצים
שנגעתי בהם (`survey.py`, `cluster.py`, `render.py`, `schemas/patents.py`,
`test_patents_round5.py`, `test_patents_round3.py`) -- כולל שני תיקוני lint שהיו קיימים בקוד
המשוחזר מה-stash (`zip()` בלי `strict=` ב-`cluster.py`, ייבוא לא ממוין ב-`survey.py`).

**תלוי במפתחות EPO/PatentsView חסרים:** תיבת "שיטה והיקף" מדווחת באמת "Google Patents (חיפוש
חסר-מפתחות)" כל עוד `EPO_OPS_KEY`/`EPO_OPS_SECRET`/`PATENTSVIEW_API_KEY` לא מוגדרים -- שדה כיסוי
CPC/מקצה יישאר נמוך במבנה הנוכחי כי המקור חסר-המפתחות לרוב אינו מחזיר אף אחד מהם; שום דבר בחבילה
הזו לא מוסתר או ממציא נתון בהיעדרם, רק מדווח את הפער בכנות (כפי שכבר נהוג בקוד הקיים).

## סטטוס P7 (עודכן 2026-09-06, בוצע)

**DS3 סגור: `blocked` הוא outcome נפרד מ-`not_found`.** `InvestigationOut.outcome`
(`agent/eoa/llm/schemas/analysis.py`) הורחב ל-`Literal["found", "partial", "not_found", "blocked"]`
+ שדה אדיטיבי חדש `blocked_reason_he: str | None`. שלושת התרחישים שמניבים `blocked`
(`agent/eoa/search/deep_search.py::_finalize_outcome` למסלול המקומי,
`investigate_batch_cloud` למסלול המוזרם לענן):

| תרחיש | מסלול | תנאי דטרמיניסטי | `blocked_reason_he` |
|---|---|---|---|
| כל הדפים שנשלפו נחסמו | מקומי (ReAct) | `attempted_urls` לא ריק, `read_urls` ריק, `len(security_flagged_pages) == len(attempted_urls)` | `_BLOCKED_REASON_ALL_PAGES_QUARANTINED_HE` |
| שער אבטחה קשיח לפני קריאת דף כלשהו | מקומי (ReAct) | `hits_seen` ריק וגם `security_flagged_search_hits` לא ריק (כל תוצאות החיפוש נחסמו בשלב ה-heuristics לפני שדף כלשהו נפתח לקריאה) | `_BLOCKED_REASON_SEARCH_GATE_HE` |
| תשובת הענן הוסתרה במלואה | מוזרם לענן (batch) | `_screen_cloud_answer` לא השאיר שום משפט (`answer_he == _SECURITY_FULL_BLOCK_HE`, `sources=[]`) | `_BLOCKED_REASON_FULL_REDACTION_HE` |
| `not_found` רגיל (נחקר במלואו, לא נמצא) | שני המסלולים | ללא דגל אבטחה כלל | -- (`outcome` נשאר `not_found`) |
| `partial` עם הסתרה חלקית | מוזרם לענן | חלק מהמשפטים שרדו את ההסתרה | -- (`outcome` נשאר `partial`, `security_review=true`) |

בניגוד ל-`stopped_budget`/`stopped_timeout`/`insufficient_context` (שרק מעדכנים
`stopped_reason`/`Investigation.outcome` ומשאירים את `InvestigationOut.outcome` הפורמלי כ-`not_found`),
`blocked` דורס גם את `InvestigationOut.outcome` עצמו -- כי `eoa.report.daily.collect_deep_search`
(אספן הדוח, לא בבעלות חבילה זו) קורא `result.get("outcome")` ישירות. `stopped_reason` מקבל `"blocked"`
בשני המסלולים; `security_review`/`security_flag_reason` ממשיכים לעבוד כרגיל (Round-4 W10) גם כשלא
`blocked` (עמוד/hit בודד נחסם אך החקירה התאוששה ממקורות אחרים -- ה`outcome` המקורי, כולל `found`,
לעולם לא נדרס).

**חוזה אספן הדוח:** `collect_deep_search` (`agent/eoa/report/daily.py`, שורה ~326) כבר קורא
`result.get("outcome") or row.get("state")` -- `"blocked"` עובר ללא שינוי קוד כאילו היה כל מחרוזת
אחרת (זה string גולמי מה-DB, לא Literal מאומת בצד הקורא). אבל שדה `blocked_reason_he` **אינו**
נכלל היום ברשימת המפתחות שהפונקציה בונה בשורות 319-333 (`job_id`/`trigger_item_id`/.../`outcome`/
`answer_he`/`confidence`/`sources`/`key_facts`/`contradictions_he` -- ואין `blocked_reason_he`) --
תוספת שורה אחת נדרשת שם (`"blocked_reason_he": result.get("blocked_reason_he", "")`) כדי שהרנדרר
יוכל להציג את הסיבה. `daily.py` הוא קובץ של מהנדס אחר בסבב הזה כרגע (ר' תיאום המשימה) ולכן לא נגעתי
בו -- זו מסירה מתועדת בלבד, ר' docs/MODULES.md "Round-5 P7" למפרט המדויק (מיקום שורה, שם השדה,
נוסח התצוגה המצופה "נחסם (לא נחקר בפועל): <reason>").

**UI:** `web/src/lib/investigations.ts` (מפת `OUTCOME_LABEL`/`OUTCOME_TONE`, לא ברשימת הקבצים
הבלעדית אך היא מקור האמת היחיד של שני העמודים והיא לא בבעלות מהנדס אחר) קיבל `blocked: "נחסם"`
בגוון ענבר (`text-warn bg-level-orange-bg`, זהה למשפחת "נעצר בגלל.../לא רלוונטי" הקיימת) -- שונה
במפורש מהאפור של `not_found`. `InvestigationsListPage.tsx` לא נזקק לשינוי קוד (הצ'יפ כבר גנרי דרך
`outcomeLabel`/`outcomeTone`); `InvestigationDetailPage.tsx` קיבל בלוק "נחסם (לא נחקר בפועל):
<blocked_reason_he>" חדש (`data-testid="investigation-blocked-reason"`), מוצג לצד (לא במקום)
הבאנר הקיים של `security_review` -- שני המפתחות `investigations.blockedChip`/
`investigations.blockedReasonPrefix` נוספו ל-`web/src/i18n/dictionaries/{he,en}.ts` (מפתחות
חדשים בלבד; שאר מפת ה-outcome, בכל שפה, נשארה בעברית קשיחה כמו כל שאר הערכים בה -- לא רפקטור
i18n-רוחבי לפריט הזה).

**`agent/eoa/api/services.py` -- נבדק, לא נדרש שינוי קוד.** `list_investigations`/`get_investigation`
כבר עוברים דרך `_investigation_aggregate` (המחרוזת האחרונה מ-`investigation_log`, ממוינת לפי `id`)
עבור שדה `outcome` בתצוגת הרשימה, ו-`_deep_search_answer` מחזיר את `jobs.result` הגולמי (או
`deep_search.load_answer`, שלא קיים כרגע) לתצוגת הפרטים -- שניהם כבר מעבירים כל שדה חדש שקיים
ב-JSON בלי whitelist מפורש. התיקון היחיד שהיה נדרש היה ב-`deep_search.py` עצמו: שורת ה-`_log`
הסופית ב-`investigate()` הגבילה את ה-outcome שנכתב לשורת ה-log האחרונה לרשימה סגורה שלא כללה
`"blocked"` (נפל ל-`"partial"` בלי זה) -- תוקן (נוסף `"blocked"` לרשימה), אחרת `list_investigations`
היה ממשיך להציג `"partial"`/`"not_found"` עבור חקירה חסומה חדשה למרות ש-`jobs.result.outcome`
עצמו כבר `"blocked"`. `investigate_batch_cloud` כבר כתב `outcome=inv.outcome` ישירות לשורת ה-log
שלו בלי מגבלה כזו.

**Backfill (אחד-פעמי, לא הרצתי -- קריאה בלבד בוצעה):** סריקה חיה (`SELECT` בלבד, DB פורט 5432)
של `jobs` עבור `kind='deep_search' AND result->>'outcome'='not_found'` מצאה **job_id=113 בלבד**
(השאלה "מפעל פולקסווגן→רפאל", המסלול המוזרם-לענן שתועד ב-docs/MODULES.md W10 -- `answer_he` מתחיל
ב-"התשובה נחסמה בבדיקת אבטחה") -- גם החיפוש הרחב יותר (`security_review=true AND outcome='not_found'`)
החזיר 0 שורות, כי שדה `security_review` פשוט לא היה קיים כשג'וב 113 רץ. שורת ה-`investigation_log`
היחידה שלו (`id=392`) גם היא עדיין `outcome='not_found'`. ה-SQL המדויק (ר' docs/MODULES.md "Round-5
P7" לגוף המלא) מעדכן את שני המקומות יחד (`jobs.result` + `investigation_log.outcome`) כדי ששני
העמודים (יומי + UI) יראו את אותה תוצאה.
