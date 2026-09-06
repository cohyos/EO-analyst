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

## סטטוס P4 (עודכן 2026-09-06, בוצע -- כולל אימות חי מול P3/P6 שנחתו במקביל באותו ערב)

כל חמשת הפערים שהוקצו לחבילה (D3/BLUF-בכשל, D7/אמינות-מקור, DS3/blocked, W5/קישור-טבלאות-למגמות,
P1/סבירות-ביטחון) סגורים ב-`agent/eoa/report/docx_builder.py` בלבד (`textnorm.py` לא נגעתי --
אף כלל רינדור חדש לא נזקק לו). כל הרינדור נכתב דפנסיבית מול `getattr` (עוטף חדש: `_field`, תומך גם
במפתחות dict) כדי לעבוד כ-no-op בלי סכמת P3 -- ותוך כדי העבודה P3 אכן נחת (`git diff` חי הראה
`DailyReportDraft.bluf`/`OutlookIndicator.likelihood`/`confidence_level`/`confidence_basis_he`/
`assumptions: list[AssumptionFalsifier]`), ואומת חי מול הסכמה האמיתית: BLUF מלא, סבירות/ביטחון
מופרדים ב-clauses, והנחות/הפרכות נרנדרים נכון end-to-end (ר' פלט `render_markdown` בבדיקה הידנית
בתיעוד המפורט). P3 עצמו תיעד ב-`qa_citations.py` שהוא ויתר על מסלול `extra_sections` משלו לטובת
הרינדור הנטיבי הזה בדיוק כדי למנוע רינדור כפול -- ר' docs/MODULES.md "Round 5 P4" לחוזה המדויק
(שמות שדה, מיקומים, מפתחות) **וגם** לשני ממצאים שהתגלו רק אחרי הנחיתה: (1) תיקון ניסוח קטן שביצעתי
ב-`_render_assumption` ("הפרכה:" במקום "יופרך אם:", כדי להתאים למילות המפתח הדטרמיניסטיות של
`eoa.qa.d7_bd_report`); (2) ממצא חוצה-צוותים (לא בבעלות החבילה, לא תוקן) -- `weekly.py`/`monthly.py`
עדיין מייבאים `bluf_extra_section`/`assumptions_extra_section` שאינם קיימים בשום מקום בקוד (ייבוא
שבור, ככל הנראה שאריות מתכנון שננטש), חוסם כרגע כמה מודולי בדיקה (`test_monthly_round5.py` וכו').

1. **BLUF ("שורה תחתונה") + D3 (נפילה חלקית ביומי).** `draft.bluf` (כשיגיע מ-P3) מרונדר כסעיף
   ראשון, לפני "תקציר מנהלים", מודגש, `[n]` דטרמיניסטי מ-`cites`. עד אז -- ודווקא בשביל D3 --
   `eoa.report.daily._deterministic_fallback_draft`'s הצורה האמיתית שלה כבר קיימת היום (`sections`
   ריק, `system_note_he` לא ריק, `exec_summary` בנוי מהפריט המוביל) מזוהה אוטומטית והרנדרר בונה
   ממנה BLUF מסונתז בן 1-2 משפטים (מ-`exec_summary[:2]`), מתויג "(שורה תחתונה אוטומטית מהנתונים,
   ללא ניסוח מודל)" -- בדיוק התיקון ל"מנגנון הכשל" (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 item 1)
   שהיה שמור ל-P3+P4 יחד. תמיכה נוספת ב-`position="before_summary"` עבור `extra_sections` (חלופה
   ל-P3/P6 לדוחות בפרוזה חופשית שלא יקבלו שדה `bluf` משלהם).
2. **סבירות/ביטחון נפרדים.** `_render_outlook_indicator` מוסיף "סבירות: X%; ביטחון: <רמה> (<בסיס>)"
   רק כש-`likelihood`/`confidence_level` קיימים על האינדיקטור -- מופרד בנקודה-פסיק כדי לעמוד ב-
   `eoa.qa.d6_daily_report._CLAUSE_SPLIT_RE` (בדיקה `outlook_likelihood_and_confidence_separated`);
   אינדיקטור legacy בלי השדות מרונדר בדיוק כמו היום.
3. **הנחות והפרכות.** `draft.assumptions` (כשיגיע) מרונדר כסעיף "הנחות והפרכות" מיד אחרי "מבט
   קדימה", כל שורה "<הנחה> — יופרך אם: <הפרכה> [n]".
4. **DS3 -- `blocked` ≠ `not_found`.** תווית חדשה ב-`_OUTCOME_LABELS_HE["blocked"]` = "נחסם (לא
   נחקר בפועל)"; הגוף מוצג מ-`entry.get("blocked_reason_he")` (לא מ-`answer_he`, שלעולם לא הופק).
   עיצוב ענבר (`class="ds-blocked"`) ב-HTML בלבד (כמבוקש); `rerun_note_he`
   (מ-`eoa.report.daily.reconcile_deep_search_reruns`, כבר קיים) מוצג מתחת לכל רשומה בשלושת
   הפלטים -- בפורמט markdown כשורת `  - ` מוזחת כדי שלא תיקרא כרשומה שנייה ע"י ה-regex של
   `eoa.qa.d4_investigations`. **תלות ידועה, לא בבעלותי (תועדה גם ב-"סטטוס P7" למעלה):**
   `eoa.report.daily.collect_deep_search` (שורה ~326) עדיין לא כולל `blocked_reason_he` ברשימת
   המפתחות שהוא בונה מ-`result` -- עד שתתווסף שם השורה `"blocked_reason_he":
   result.get("blocked_reason_he", "")`, הרנדרר יציג "—" כסיבה (מעולם לא יציג "לא נמצא" בטעות,
   וזה כל מה שה-QA הדטרמיניסטי `blocked_distinct_from_not_found` בודק).
5. **D7 -- אמינות מקור בנספח.** עמודת "אמינות" חדשה בנספח המקורות (בין "מקור" ל"תאריך") בשלושת
   הפלטים; `reliability_label` (חדשה, ציבורית) מקבלת `None`/מחרוזת מוכנה/`dict` בצורה
   `{"kind": "primary"|"secondary", "score": float|None, "label": str|None}` ומרנדרת "מקור
   ראשוני/משני · <label> · <score:.2f>"; "—" כשאין נתון. `items[i]["reliability"]` הוא המפתח
   האופציונלי החדש -- אספני הדוח (לא בבעלותי) יאכלסו אותו מ-`sources.reliability`/טבלת
   `source_reliability` כשירצו.
6. **W5 -- קישור טבלאות למגמות.** שורת טבלה יכולה עכשיו להיות גם `dict`
   `{"cells": [...], "related_trend_he": "..."}` (לצד הצורה הישנה, `list[Any]`, שנשארת עובדת ללא
   שינוי בכל קורא קיים) -- `related_trend_he` מתקפל כ"(מגמה: …)" בתא האחרון של השורה בשלושת
   הפלטים; `tbl["related_trend_he"]` ברמת הטבלה כולה מרונדר כהערה נפרדת מתחת לכותרת/`note_he`.
   `dedupe_rows_across_tables`/`_row_identity` עודכנו לתמוך בשתי הצורות ללא שינוי בהתנהגות הקיימת
   (נבדק ברגרסיה מול `tests/unit/test_table_dedupe_round4.py`).

**בדיקות:** `tests/unit/test_renderer_round5.py` (חדש, 38 מקרים -- אחד לכל התנהגות/גבול) +
`tests/unit/test_docx_builder.py` (עודכן: כותרת נספח המקורות כוללת "אמינות"). `pytest tests/unit -q
-k "docx or renderer or md_body or table_dedupe or report_round3_d6"`: 128 עברו, 0 נכשלו. `ruff
check`/`ruff format --check` נקיים על שלושת הקבצים שנגעתי בהם
(`agent/eoa/report/docx_builder.py`, `tests/unit/test_renderer_round5.py`,
`tests/unit/test_docx_builder.py`).

## סטטוס P6 (2026-09-06, בוצע -- BD טריטוריאלי: מפת קונים, דירוג הזדמנויות, דלתא, הנחות והפרכות)

כל ארבעת הפערים (B1, B2, B4, B5) ופריט ה-BLUF לדוח ה-BD נסגרו. קבצים:
`agent/eoa/llm/schemas/bd_territory.py`, `agent/eoa/report/bd_territory.py`,
`agent/eoa/llm/prompts/report_bd_territory.md`, `tests/unit/test_bd_round5.py` (חדש, 45 בדיקות).

**ממצא חוצה-חבילות (P4 כבר רינדר BLUF+הנחות נטיבית):** בזמן חיווט מיקום ה-BLUF (לפי הבריף: לנסות
`extra_sections` position `"before_summary"`, וליפול חזרה ל-`"after_summary"` אם לא נתמך עדיין),
התברר ש-`eoa.report.docx_builder` (P4, אותו ערב) כבר בנה רינדור נטיבי, מוקלד-ברווח (duck-typed),
ישירות מ-`draft.bluf`/`draft.assumptions` -- שני שדות ששמם זהה בדיוק לשדות שהוספתי כאן. `_draft_bluf_
info`/`_draft_bluf_text` מציגים "שורה תחתונה" ראשונה בדוח, ממש לפני "תקציר מנהלים" (בדיוק המטרה
שהבריף חשש שתדרוש מנגנון מיוחד), כולל fallback דטרמיניסטי מתויג לדוחות ה-`_tables_only_draft`/
`_deterministic_fallback_draft` של המודול הזה. `_draft_assumptions`/`_render_assumption` מציגים
"הנחות והפרכות" מיד אחרי "מבט קדימה". טיוטה ראשונה של העבודה הזו דחפה את שני הסעיפים גם כ-
`extra_sections` ידניים (וגם הוסיפה helper שבודק אם `docx_builder` תומך ב-`"before_summary"`) --
נתפס בבדיקה חיה (כותרת "הנחות והפרכות" כפולה באותו דוח) והוסר; ראו ההערות ברמת המודול ב-
`bd_territory.py`. **שני פערי ניסוח/מילות-מפתח חוצי-צוות תועדו ונשלחו כ-follow-up task (לא תוקנו
כאן -- שני הקבצים שייכים למהנדסים אחרים הסבב הזה):** (1) `eoa.qa.d6_daily_report`/`d7_bd_report`'s
`_bluf_check` נכשל (`cited=False`) גם מול BLUF תקין ומצוטט כהלכה, כי מפצל המשפטים המקומי מפריד
"טקסט. [n]" לשני קטעים ("טקסט." ו-"[n]") ודורש ציטוט בכל קטע בנפרד; (2) `assumptions_falsifiers_
list_present` נכשל (`falsifier_language=False`) כי הניסוח הנטיבי "... — יופרך אם: ..." אינו מכיל אף
אחת ממילות-המפתח `("פריך", "הפרכ", "falsif")` כתת-מחרוזת.

**B1 (מפת קונים / צינור הזדמנויות):** טבלה דטרמיניסטית חדשה (`pipeline_table`) -- שורות ממכרזים
(`_pipeline_rows_from_tenders`, שלב RFI/RFP/הערכה נגזר מכותרת/סטטוס), מתחזיות (`_pipeline_rows_
from_forecasts`, שלב "החלטה" לחלון קרוב+סבירות גבוהה, אחרת "הערכה"), מאירועי זכייה בלבד
(`_pipeline_rows_from_events`, `events.kind == 'contract_award'` בלבד -> "לאחר-זכייה") ועד 3 שורות
מהמודל (`BdPipelineOpportunity`, מאומת ציטוט כמו `rationale` של פעולה מומלצת).

**B2 (דירוג הזדמנויות):** נוסחה מתועדת: `tier_score = magnitude (0-3) + recency (0-2) + watchlist
bonus (0-1)`; A מ-4 ומעלה, B מ-2, אחרת C (`tier_label`/`_tier_score`, הערת מודול מלאה ב-
`bd_territory.py`). עמודת "דרג" נוספה לטבלת הצינור וגם ל-`competitors_table`.

**B4 (דלתא טריטוריאלית):** חיווט ישיר, ללא שינוי, של `eoa.report.deltas.compute_deltas`/
`build_report_state`/`delta_extra_section` עם `kind="bd_territory"` ו-`territory=code` -- בדיוק
כפי שתועד מראש בסטטוס P2. `reports.report_state` מאוכלס עכשיו גם לדוחות `bd_territory`.

**B5 (הנחות והפרכות):** `BdAssumption` חדש (`assumption_he`/`falsifier_he`/`cites` אופציונלי)
מחליף את `risks_assumptions_he` בפרומפט החדש; השדה הישן נשאר בסכמה (ריק כברירת מחדל) כדי שטיוטה
ישנה עדיין תיטען לאובייקט תקין.

**בדיקות:** `tests/unit/test_bd_round5.py` (45, חדש). `pytest tests/unit -q -k "bd or territory or
acquisition"`: 268 עברו, 0 נכשלו (223 קיימות + 45 חדשות; שורת בדיקה אחת בקובץ לא-בבעלותי,
`test_report_bd_territory.py`, עודכנה לכותרת הטבלה החדשה עם עמודת "דרג", אותה מוסכמה כמו P1).
`ruff check`/`ruff format --check` נקיים על כל הקבצים שנגעתי בהם.

## סטטוס P3 (2026-09-06, בוצע -- BLUF + סבירות/ביטחון + הנחה↔הפרכה + איסור so_what בפרומפטי הדוחות)

כל ארבעת המסירות (BLUF, הפרדת סבירות/ביטחון, הנחות↔הפרכות, איסור ניסוחי so_what) סגורות עבור
הדוח היומי/שבועי/חודשי. קבצים: `agent/eoa/llm/schemas/analysis.py` (מחלקות טיוטת דוח בלבד --
`Sentence`/`OutlookIndicator`/`AssumptionFalsifier`/`DailyReportDraft`), `agent/eoa/llm/schemas/
reports.py` (`WeeklyReportDraft`/`MonthlyReportDraft`), `agent/eoa/report/qa_citations.py` (פס
ה-so_what), `agent/eoa/report/{daily,weekly,monthly}.py` (חיווט בלבד), פרומפטי
`report_{daily,weekly,monthly}.md`, `tests/unit/test_bluf_round5.py` (חדש, 31 בדיקות).

**דלתת סכמה:** `bluf: list[Sentence]` (עד 2 משפטים, עד 40 מילה בסה"כ, ולידציה משותפת
`validate_bluf_length`) על שלוש הטיוטות; `OutlookIndicator.likelihood: "גבוהה"|"בינונית"|"נמוכה"|
None` + `.confidence_level: "גבוה"|"בינוני"|"נמוך"|None` + `.confidence_basis_he: str=""` (אדיטיבי,
`confidence_level` דורש `confidence_basis_he` לא-ריק); `AssumptionFalsifier` חדש
(`assumption_he`/`falsifier_he`/`cites` אופציונלי) + שדה `assumptions` (0-4 ביומי, 2-4 מומלץ
בשבועי/חודשי, לא נאכף כמינימום בסכמה).

**ממצא חוצה-חבילות קריטי (זהה לזה שגילה P6 עצמאית באותו ערב עבור ה-BD):** התוכנית המקורית של
חבילה זו הייתה לחשוף BLUF/הנחות כ-`extra_sections` ולבנות עותק-רינדור עם סבירות/ביטחון משורשרים
לתוך `text_he`. תוך כדי מימוש התברר ש-`eoa.report.docx_builder` (P4, אותו ערב) כבר מרנדר נטיבית,
duck-typed, ישירות מ-`draft.bluf`/`OutlookIndicator.likelihood`/`confidence_level`/
`confidence_basis_he`/`draft.assumptions` -- בדיוק שמות השדות שחבילה זו נחתה איתם -- כולל תמיכה
אמיתית ב-`extra_sections` position `"before_summary"` וסינתוז BLUF מתויג לטיוטת-כשל ריקת-נרטיב.
קוד ה-`extra_sections`/עותק-הרינדור המיותר הוסר לפני נחיתה (היה גורם לכפילות: כותרת "שורה תחתונה"
כפולה, סיומת "סבירות/ביטחון" כפולה על כל אינדיקטור) -- התוצאה: `daily.py`/`weekly.py`/
`monthly.py` מעבירים את `draft` המקורי בלי שינוי לשלוש קריאות הרינדור.

**אי-התאמת פורמט-ערך לא-קריטית, מתועדת (לא תוקנה -- `docx_builder.py` קריאה-בלבד לחבילה זו):**
`_format_likelihood`/`_format_confidence_level` ב-`docx_builder.py` נכתבו בהנחת קלט מספרי (יחס
0-1)/מפתח אנגלי ("high"/"medium"/"low"); חבילה זו משתמשת בערכי `Literal` עבריים (לפי הבריף של
חבילה זו). נבדק חי: שני הפונקציות נופלות בבטחה ל-`return str(value)`/`dict.get(value, value)`
עבור קלט לא-מספרי/לא-מפתח-אנגלי -- כלומר עבור ערך עברי כבר-מוכן זה מחזיר בדיוק את הטקסט הנכון.
שני הצדדים לא-מסונכרנים על הנייר אך תואמים בפועל; מכוסה ב-`tests/unit/test_bluf_round5.py`.

**אישוש (לא תיקון כפול) לבאג ידוע ב-checker של D6:** P6 כבר דיווח (follow-up task, לא בקובץ
בבעלותו) ש-`eoa.qa.d6_daily_report._bluf_check`/`d7_bd_report`'s מקבילו נכשלים (`cited=False`)
גם מול BLUF תקין לגמרי, כי מפצל המשפטים המקומי שובר "טקסט. [1]" לשני קטעים. נבדק חי גם עבור
היומי/שבועי (הן BLUF מהמודל והן ה-BLUF המסונתז דטרמיניסטית של טיוטת-הכשל) -- אותה תקלה חוזרת,
בדיוק כפי ש-P6 חזה. לא נשלחה כאן משימת follow-up כפולה -- רק אישוש נוסף לתיעוד הקיים.

**איסור ניסוחי so_what (D2, docs/qa/loop/round_3_judge.md):** `SO_WHAT_TEMPLATE_PHRASES_HE` +
`strip_so_what_phrases`/`strip_so_what_phrases_from_draft` חדשים ב-`qa_citations.py` (עותק מקומי
קטן של אלגוריתם `eoa.report.style`'s -- `style.py` עצמו מחוץ לבעלות הקבצים של הסבב הזה); מוריד
"מחזק את מעמדה"/"מהווה צעד משמעותי"/"מעיד על מגמה" וכו' מ-`exec_summary`/`sections[].sentences`,
מתעד ספירה ב-structlog. חוברה יחד עם `eoa.report.style.apply_style_guard` (קיים מסבב 4b אך
מעולם לא חובר בפועל לשלושת בוני הדוח, לפי ההערה ב-`style.py` עצמו) לתוך `build_daily`/
`build_weekly`/`build_monthly`, מיד אחרי `normalize_draft`.

**פרומפטים:** שלושתם קיבלו מפרט שדה `bluf` (עם אימוג'י העדיפות שעבר מ-`exec_summary`), הסבר שני-
צירי-ICD-203 ל-`likelihood`/`confidence_level`/`confidence_basis_he` (עם איסור לכתוב "סבירות"/
"ביטחון" בתוך `text_he` -- זה תפקיד הרנדרר), מפרט `assumptions`, איסור ניסוחי so_what, וכן שלוש
מילות המילוי החסרות שכבר נאסרו ב-`style.py` אך לא הופיעו עדיין ברשימת הפרומפט
("כפי שצוין לעיל"/"כאמור לעיל"/"ניתן לומר כי"/"ניתן לציין כי"/"באופן כללי ניתן לומר").

**טיוטות כשל דטרמיניסטיות:** `_deterministic_fallback_draft` בשלושת המודולים משאירה במכוון
`bluf=[]` (לא ממלאת מהפריט המוביל) -- ר' ההערה למעלה: `docx_builder` כבר מסנתז BLUF מתויג
מהצורה הזו בדיוק; מילוי מפורש כאן היה מדכא את התווית "ללא ניסוח מודל". נבדק חי מול הרינדור
האמיתי של שלושת המודולים.

**בדיקות:** `tests/unit/test_bluf_round5.py` (31, חדש). `pytest tests/unit -q -k "bluf or
report_daily or report_weekly or monthly or style"`: **165 עברו, 0 נכשלו** (כולל `test_qa_round5.py`
של P9 ו-`test_renderer_round5.py` של P4, שניהם ירוקים מול הסכמה של חבילה זו). `ruff check`/`ruff
format --check` נקיים על כל הקבצים שנגעתי בהם. ראו docs/MODULES.md "Round 5 P3" לפירוט מלא,
כולל חוזה הרינדור המדויק (בבעלות P4) וטבלת שדה/מיקום.
