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
