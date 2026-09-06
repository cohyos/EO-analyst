# בנצ'מרק תבניות דוח: EO-Analyst מול תעשיית האנליזה המקצועית

**מטרה:** להשוות את מבנה חמשת סוגי הדוח של EO-Analyst (יומי, שבועי, חודשי, מיקוד BD טריטוריאלי, סקר פטנטים) ותשובת חקירת-העומק, מול תבניות מוצר מבוססות של קהילת המודיעין, אנליזה מסחרית-ביטחונית, שוקי הון, think-tanks וסקרי פטנטים — ולזהות פערים בעלי ראיות קונקרטיות מהפלטים החיים.

**בסיס ההשוואה (קריאה ראשונית, read-only):**
פרומפטים: `agent/eoa/llm/prompts/report_daily.md`, `report_weekly.md`, `report_monthly.md`, `report_bd_territory.md`, `patent_survey.md`, `deep_search_system.md`.
רינדור: `agent/eoa/report/docx_builder.py` (`_planned_headings`, `render_markdown`), `daily.py`, `weekly.py`, `bd_territory.py`, `monthly.py`, `agent/eoa/patents/survey.py`.
פלטים חיים (6 בספטמבר 2026): `output/reports/daily_2026-09-06.md`, `weekly_2026-09-06.md`, `bd_us_2026-09-06.md`, `bd_kr_2026-09-06.md`, `patent_survey_FPA_עם_פיקסל_דיגיטלי_DROIC_2026-09-06.md`.
כללים: `docs/CONVENTIONS.md` (משמעת ציטוט [n], DATA≠הוראות, provenance), `docs/QA_CONTINUOUS_LOOP.md` (עשרת תחומי הציון D1–D10).

הערה: המסמך נכתב על ידי סוכן מחקר (קריאה בלבד) ב-2026-09-06 בערב; הראיות מהפלטים החיים מתייחסות לגרסאות הדוחות של אותו יום (חלקן, למשל מנגנון הכשל בדוח היומי וסעיף המשוב בשבועי, כבר תוקנו בקוד באותו ערב — ר' docs/qa/loop/round_3_fixes.md).

---

## 1. תמצית עמוד אחד — מה משותף למוצרי אנליזה מקצועיים

בכל המשפחות שנבדקו (IC tradecraft, מודיעין צבאי-נאט"ואי, אנליזת שוק ביטחונית מסחרית (Janes/Forecast International/GlobalData), think-tanks (RAND/CSIS/IISS/SIPRI), מחקר שוקי הון, סקרי פטנטים של WIPO/Clarivate, ותוכניות טריטוריה עסקיות) חוזרות שמונה תכונות מבניות:

1. **BLUF / שורה תחתונה בראש** — המסקנה והמשמעות למקבל ההחלטה מופיעה במשפט או פסקה ראשונה, לפני הרקע והפרטים — לא בסופם. זהו התקן הרשמי של הכתיבה הצבאית האמריקאית (AR 25-50) ושל ICD 203 כאחד.
2. **שיפוט אנליטי מופרד מעובדה, עם שפת ודאות סטנדרטית** — ICD 203 מחייב **שני צירים נפרדים ולא מעורבבים באותו משפט**: (א) *סבירות האירוע* (ספקטרום מ-"remote" עד "nearly certain") ו-(ב) *מידת הביטחון של האנליסט בשיפוט עצמו* (high/moderate/low, לפי כמות ואיכות המקורות) — עם איסור מפורש לערבב את שתי השפות באותו משפט.
3. **"מה השתנה מאז המהדורה הקודמת"** — INTSUM נאט"ואי/צבאי נבנה כתמונה מצטברת מול הידיעה הקודמת, לא כרשימת פריטים מבודדת של התקופה הנוכחית.
4. **אינדיקטורים ואזהרה (I&W) עם סטטוס לאורך זמן** — מתודולוגיית I&W הקלאסית עוקבת אחרי אינדיקטור ספציפי כ"פעיל/רדום" (active/dormant) על ציר זמן, לא כמשפט חד-פעמי שנעלם בגיליון הבא.
5. **השלכות למקבל ההחלטה, לא רק תיאור אירוע** — Findings → Implications → Recommendations (RAND/CSIS), Rating/Price-Target/Catalysts/Risks (equity research), Business Implications (WIPO/Clarivate patent landscape) — כל המוצרים מחייבים "אז מה" מפורש, נפרד ומזוהה בבירור מהעובדה עצמה.
6. **אלטרנטיבות ואי-ודאות מפורשת** — Structured Analytic Techniques (CIA Tradecraft Primer): key assumptions check, devil's advocacy, alternative futures — מוצר מקצועי מציג הנחה מפורשת ואת מה שעלול להפריך את השיפוט המרכזי, לא רק תרחיש אחד.
7. **טבלאות עקביות + נספח מקורות מדורג** — כל המוצרים (equity research, Janes, WIPO PLR) בנויים על טבלאות דטרמיניסטיות קבועות (Rating/Target, אחזקות מדינה, אשכולות CPC) בנפרד מהפרוזה, ומקורות עם דירוג אמינות (primary/secondary, admiralty code, coverage %).
8. **משמעת אורך** — תקציר מנהלים קצר וממושמע (equity research: פסקה-שתיים; policy brief: פסקה או כמה תבליטים; INTSUM: BLUF בכמה משפטים) — לא פרוזה שהולכת ומתארכת ללא תקרה.

EO-Analyst עומד היטב בעקרון אחד קריטי שרוב המוצרים המסחריים **אינם** אוכפים באופן מכני: **כל משפט עובדתי מחויב ציטוט [n] ברמת סכימה (Sentence עם `cites`)**, נאכף על ידי `report/qa_citations.py` ו-`docs/CONVENTIONS.md` §4. זהו יתרון מובנה על פני רוב הפורמטים המסחריים שנבדקו (אשר בדרך כלל מסתפקים בהערות שוליים חופשיות). הפער העיקרי שנמצא הוא **בשכבת השיפוט האנליטי (2, 5, 6) ובעקביות/עמידות המבנה (3, 4, 8)** — לא במשמעת המקור.

---

## 2. טבלת פערים לכל סוג דוח

מקרא חומרה: 🔴 גדול · 🟠 בינוני · 🟢 קטן

### 2.1 דוח יומי (`report_daily.md` + `daily.py`)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה מהפלט החי |
|---|---|---|---|---|
| D1 | BLUF נפרד מרשימת עובדות | `exec_summary` הוא רצף Sentence-ים שמכיל גם אימוג'י עדיפות (🔴/🟠/🟡) וגם ציטוט מלא בתוך אותה פסקה — אין שורה אחת "שורה תחתונה" מופרדת חזותית | 🟠 | `daily_2026-09-06.md` שורה 7: תקציר המנהלים הוא פסקה של 5 משפטים ארוכה ורצופה, שממנה קשה לחלץ "מה חשוב לדעת קודם" בלי לקרוא עד הסוף |
| D2 | הפרדת סבירות (likelihood) מביטחון (confidence) | האינדיקטורים ב-`outlook` מסמנים `is_assessment` בודד עם מילת פתיחה ("להערכתנו"/"נראה ש"/"ייתכן") — אין ציר נפרד של % סבירות מול רמת ביטחון | 🟠 | פרומפט `report_daily.md` §outlook: משתמש רק במילת גידור בודדת, ללא שדה נפרד ל"רמת ביטחון" מול "סבירות" כפי שדורש ICD 203 |
| D3 | עמידות בכשל ("what happens when QA fails") | כשהטיוטה נכשלת פעמיים בבדיקת האזכורים, **כל** התוכן האנליטי (תקציר, ניתוח תחום, מבט קדימה, נקודות פתוחות) מוחלף בדחיסת-נתונים גולמית ללא ניסוח | 🔴 | `daily_2026-09-06.md` (גרסת הבוקר) שורה 9: "תקציר מובנה אוטומטית... ניסוח המודל הושמט במלואו"; רשימת הכותרות בפועל מדלגת לגמרי על "מבט קדימה" ו"נקודות פתוחות" לאותו יום — דוח שהאנליסט קורא ב-10 דקות מגיע ללא Outlook כלל |
| D4 | "מה השתנה מאז אתמול" | הדוח נבנה מרשימת פריטי היום בלבד; אין דלתא מפורשת מול הדוח היומי הקודם (אין "לעומת אתמול X עלה/ירד") | 🟠 | אין שדה כזה בסכמת `DailyReportDraft` או בפרומפט `report_daily.md` |
| D5 | I&W עם סטטוס לאורך זמן | `outlook` מייצר 2–3 אינדיקטורים *לכל דוח בנפרד*; אין רשימת מעקב מתמשכת (watchlist) שמראה אם אינדיקטור מאתמול "הבשיל/נותר פתוח/בוטל" | 🟠 | תבנית `OutlookIndicator` בפרומפט אינה מתייחסת לאינדיקטורים קודמים כלל |
| D6 | תעשייה ישראלית — כפילות תוכן | שלושת סעיפי "תעשייה ישראלית" (זכיות, תחרות, איומים) חוזרים על **אותה שורת טבלה** פעמיים-שלוש כמעט מילה במילה | 🟢 | `daily_2026-09-06.md` שורות 58/65/67: אותו תיאור מופיע גם ב"זכיות" וגם ב"תחרות" וגם ב"איומים" (תוקן חלקית ב-W28: שורה מופיעה פעם אחת בין טבלאות) |
| D7 | ציטוט/מקורות מדורגים | נספח המקורות שטוח — כל שורה נראית זהה (אין תיוג ראשוני/משני, אין ציון אמינות מקור למרות ש-`source_reliability` קיים כטבלת DB) | 🟢 | `docs/CONVENTIONS.md` מזכיר `source_reliability` בסכמת DB, אך `_planned_headings`/`render_markdown` לא מציגים אותו בנספח |

### 2.2 דוח שבועי (`report_weekly.md` + `weekly.py`)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה |
|---|---|---|---|---|
| W1 | אורך/משמעת קריאה (30 דק') | 24 כותרות Heading-1 בדוח בפועל, כולל שכפול תוכן בין "מגמות" לבין "סעיפי תחום" | 🔴 | `weekly_2026-09-06.md`: "פעילות מוגברת סביב אלביט...(Airborne Pods)" (שורה 9, מגמה) לעומת "פודים ומטע"דים אוויריים (Airborne Pods & Payloads)" (שורה 41, סעיף תחום) — שני סעיפי Heading-1 נפרדים על אותו תחום, כל אחד עם ניתוח דומה מאותם פריטים |
| W2 | BLUF נפרד | `exec_summary` יכול להגיע עד 8 משפטים — אין תקרה הדוקה יותר לקריאה של 30 שניות לפני צלילה לפרטים | 🟠 | פרומפט `report_weekly.md`: "עד 8 אובייקטי Sentence (בדרך כלל 3–5 מספיקים)" — רק המלצה, לא אכיפה |
| W3 | "מה השתנה מהשבוע הקודם" | המגמות (`trends`) מחושבות מהשבוע הנוכחי בלבד (עוצמה 1–5, ראיות); אין השוואה לחוזק המגמה בשבוע הקודם | 🟠 | פרומפט: "strength הוא חוזק המגמה מ-1 עד 5" — נתון סטטי לשבוע זה, לא דלתא |
| W4 | אלטרנטיבות/הנחות | `analyst_note_he` הוא המקום היחיד לסינתזה חופשית, אך אינו מנוסח כ"הנחה מרכזית + מה עשוי להפריך אותה" | 🟢 | פרומפט §analyst_note_he: "הערכה סינתטית... לא כדרך לעקוף ציטוט" — אין מבנה ACH |
| W5 | טבלאות עקביות מול המגמות | טבלת "רדאר טכנולוגי — סיכום שבועי" וטבלת "פטנטים ו-IP" אינן מקושרות בחזרה למגמות שצוינו למעלה בדוח (למשל: אין קישור בין "פטנטים חדשים" לבין מגמת ה-C-UAS שזוהתה) | 🟠 | היעדר cross-reference כזה בפלט; כל טבלה עומדת בפני עצמה |

### 2.3 דוח חודשי (`report_monthly.md` + `monthly.py`)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה |
|---|---|---|---|---|
| M1 | סכמת ציטוט מובנית | דוח חודשי **עדיין לא אומץ** למבנה `Sentence{text_he, cites}` שהיומי/השבועי/BD כבר עברו אליו — עדיין "[n]" חופשי בטקסט | 🔴 | פרומפט `report_monthly.md` §exec_summary_he: "כל משפט עובדתי... חייב להסתיים בהפניה [n]" — טקסט חופשי, בניגוד לדוח היומי/שבועי/BD שכבר "אסור בהחלט לכתוב [n] בתוך text_he" |
| M2 | תקציר-על תקופתי (חודש מול חודש) | אין השוואת חודש מול חודש קודם באף שדה בסכמה (`MonthlyReportDraft`) | 🟠 | לא קיים שדה כזה בפרומפט |
| M3 | I&W מצטבר | `outlook_he` הוא 2–4 משפטי הערכה חופשיים, ללא מבנה אינדיקטור/מעקב כמו ביומי/שבועי | 🟢 | פרומפט: "outlook_he — 'מבט קדימה', 2–4 משפטים בלבד... אין חובת [n]" |

### 2.4 דוח מיקוד BD טריטוריאלי (`report_bd_territory.md` + `bd_territory.py`)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה |
|---|---|---|---|---|
| B1 | מפת קונים / שלב-רכש (buyer landscape / pipeline stage) | אין טבלת "הזדמנות → שלב → בעל תפקיד רוכש → תאריך יעד" — יש רק `recommended_actions` (עד 8) עם `owner_role_he`/`timing_he`/`target`, ללא ריכוז שלבי-מכרז מפורש (RFI/RFP/הערכה/החלטה) | 🟠 | תבנית טריטוריה סטנדרטית ("account planning") דורשת org/stakeholder map + pipeline by stage; `report_bd_territory.md` לא כולל שדה כזה כלל |
| B2 | מסגרת ניקוד הזדמנות (accounts tiering) | המתחרים ממוינים לפי `אזכורים בחלון` בלבד — אין ניקוד לקוח/הזדמנות (Tier A/B/C) לפי גודל+סבירות+התאמה כמקובל בתוכניות טריטוריה | 🟢 | `bd_us_2026-09-06.md` טבלת "מתחרים פעילים בטריטוריה": עמודה יחידה "אזכורים בחלון", אין ציון-משקל משולב |
| B3 | הפרדת Rating/Confidence עקבית | לכל `recommended_action` יש `confidence` (0–1) ו-`priority` (H/M/L) — זהו בפועל הכי קרוב ל-ICD 203/equity-research standard מכל 5 סוגי הדוח | 🟢 (חוזקה, לא פער) | פרומפט §recommended_actions — ראוי לשכפל דפוס זה לדוחות האחרים |
| B4 | "מה השתנה בטריטוריה מאז הדוח הקודם" | חלון זמן קבוע (`lookback_days`) נסרק מחדש כל פעם; אין דלתא מול דוח הטריטוריה הקודם לאותה טריטוריה | 🟠 | אין שדה כזה בסכמה |
| B5 | אלטרנטיבות/הנחות | `risks_assumptions_he` הוא השדה הכי קרוב ל"assumptions" מכל 5 סוגי הדוח — אך הוא טקסט חופשי לא-מובנה (2–4 משפטים), לא רשימת "הנחה↔מה יפריך אותה" | 🟢 | פרומפט §risks_assumptions_he |

### 2.5 סקר פטנטים (`patent_survey.md` + `agent/eoa/patents/survey.py`)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה |
|---|---|---|---|---|
| P1 | דף שיטה/היקף בפתיחה (methodology & scope) | WIPO PLR מחייב תיאור שיטת החיפוש והיקפו *לפני* הממצאים; הדוח שלנו פותח ישר ב-`exec_summary` בלי לציין את שאילתת החיפוש, טווח התאריכים, ומאגרי הפטנטים שנסרקו | 🟠 | `patent_survey_FPA...md` שורה 5 ("תקציר מנהלים") — אין סעיף "שיטה והיקף" קודם לו; רק ה-`topic` מופיע בכותרת הדוח |
| P2 | שקיפות פערי כיסוי (חובה לפי כלל 8 בפרומפט עצמו!) | הפרומפט *כבר דורש* לציין פער כיסוי כשמתחת ל-70% מהפטנטים חסרי-מקצה — אך זה מוצג רק כמשפט בתוך `exec_summary`, לא כתיבה מובנית ("Data Coverage: 0%") למעלה כמו ב-WIPO/Clarivate | 🔴 | `patent_survey_FPA...md` שורה 5: "ל-17 מתוך 17 הפטנטים אין נתוני מקצה — לא ניתן להסיק בלעדיות או נתח שוק" — משפט חשוב קבור בתוך פסקת exec_summary ולא בתווית/תיבת-נתונים בולטת בראש הדוח |
| P3 | Business Implications מנוסחות בהתאם ל"Rating"/עדיפות | `business_implications` הן `action_he`+`rationale_he` בלבד, ללא `priority`/`confidence` כפי ש-`recommended_actions` בדוח ה-BD כן כוללים | 🟠 | פרומפט `patent_survey.md` §business_implications — אין `priority`/`confidence` |
| P4 | הפרדת "ידע כללי" (already good) | הפרומפט כבר דורש `is_general_knowledge=true` + תווית "ידע כללי (לא מאומת במאגר):" — התאמה מדויקת לעיקרון ICD 203 של הפרדת שיפוט ממקור | 🟢 (חוזקה) | פרומפט `patent_survey.md` כלל 2 |
| P5 | "White space" מנוסח בפועל אך לא ממופה חזותית | `white_spaces` הוא נרטיב טקסטואלי; WIPO/Clarivate משתמשים במפת-חום/מטריצת CPC×מקצה חזותית | 🟢 | פרומפט §white_spaces: "נרטיב" בלבד, ללא טבלה |

### 2.6 תשובת חקירת-עומק (Deep Search)

| # | ממד מקצועי | המבנה שלנו כיום | חומרה | ראיה |
|---|---|---|---|---|
| DS1 | הפרדת ממצא מסתירה (contradictions) | הפרומפט כבר דורש `contradictions_he` נפרד — זהה לעקרון "conflicting reporting" ב-IC tradecraft | 🟢 (חוזקה) | `deep_search_system.md` כלל 7 |
| DS2 | דיווח כנה על כשל/חוסם (already good) | `outcome="not_found"` + `what_was_tried_he` תואם את עקרון "כשל מדווח, לא מוסתר" של Tradecraft Primer | 🟢 (חוזקה) | `deep_search_system.md` כללים 4/6 |
| DS3 | חסימת אבטחה מוצגת כ"לא נמצא" גנרי לקורא הסופי | כשחקירה נחסמת בגלל חשד הזרקת הוראות, הדוח מציג זאת כ"לא נמצא: התשובה נחסמה בבדיקת אבטחה" ללא אבחנה אם המידע לא קיים או שרק נחסם טכנית | 🟠 | `daily_2026-09-06.md` שורה 37 — מוצג באותה שורת bullet כמו ממצא "חלקי" רגיל, בלי להבדיל בין "נחקר ולא נמצא" לבין "נחסם ולא נחקר בפועל" (הטיפול בחסימה חלקית עם `security_review` נכנס בסבב 4; התצוגה עדיין לא מבחינה) |

---

## 3. תבניות מוצעות לכל סוג דוח

עקרונות משותפים לכל התבניות המוצעות (ואינם משתנים): כל משפט עובדתי הוא `Sentence{text_he, cites}` עם `cites` לא-ריק; עברית תקנית RTL; מונחים טכניים באנגלית בסוגריים בהופעה ראשונה; טבלאות דטרמיניסטיות (מה-DB/גרף) מופרדות ומזוהות ככאלה; איסור המצאה מוחלט; `analyst_note_he` הוא היחיד הפטור מציטוט.

### 3.1 דוח יומי — תבנית מוצעת

| # | סעיף | מטרה | תקרת אורך | דטרמיניסטי / מנוסח-מודל | ביטוי ודאות/מקור בעברית |
|---|---|---|---|---|---|
| 1 | **שורה תחתונה** (BLUF) — חדש | משפט/שניים אחד בלבד, בפונט מודגש, עם רמת עדיפות אחת (🔴/🟠/🟡) — "מה חייבים לדעת לפני שממשיכים" | 1–2 `Sentence` | מנוסח-מודל, `cites` חובה | — |
| 2 | תקציר מנהלים | כפי שקיים היום, אך **בלי** אימוג'י-עדיפות בתוך אותה פסקה (הם עוברים לסעיף 1) | 3–5 `Sentence` | מנוסח-מודל | — |
| 3 | מה השתנה מאז אתמול — חדש | דלתא מפורשת: פריטים/מגמות שהתחדשו/הוסרו/עלו בעדיפות לעומת דוח היום הקודם | עד 3 `Sentence` | דטרמיניסטי (diff על item_id בין שתי ריצות) + שורת הקשר מנוסחת | — |
| 4 | סעיפי תחום (כפי שקיים) | ניתוח לפי domain | 3–8 `Sentence`/סעיף | מנוסח-מודל | — |
| 5 | טבלת אירועים עסקיים (כפי שקיים) | — | דטרמיניסטית | — | — |
| 6 | חקירות עומק — עם תיקון DS3 | להוסיף `blocked_reason_he` נפרד מ-`outcome=not_found` כדי להבדיל "נחקר ולא נמצא" מ"נחסם טכנית" | — | דטרמיניסטי (מבנה) | — |
| 7 | תחזיות מכרזים (כפי שקיים) | — | דטרמיניסטית + נימוק | — | — |
| 8 | תעשייה ישראלית (איחוד 3→1) | **לאחד** את שלושת הסעיפים (זכיות/תחרות/איומים) לטבלה אחת עם עמודת "סוג" כדי למנוע כפילות | טבלה אחת, ≤10 שורות | דטרמיניסטית מה-DB + נימוק Sentence | — |
| 9 | מבט קדימה + מעקב אינדיקטורים — משודרג | כמו היום, **ועוד** עמודת "מאז אתמול": חדש / נותר פתוח / הבשיל / בוטל, מול רשימת אינדיקטורים שנשמרת ב-DB (`indicator_watchlist`) | 2–3 `OutlookIndicator` + סטטוס | דטרמיניסטי (persist+diff) + Sentence | "סבירות: X%" + "ביטחון: גבוה/בינוני/נמוך" נפרדים |
| 10 | נקודות פתוחות (כפי שקיים) | — | — | — | — |
| 11 | נספח מקורות — משודרג | להוסיף עמודת אמינות מקור (מ-`source_reliability`), ומיון ראשוני/משני | טבלה | דטרמיניסטית מה-DB | — |

**מנגנון הכשל:** להחליף את "מחיקת כל הפרוזה בכשל QA כפול" בנפילה חלקית: לשמר את הטבלאות **וגם** להציג BLUF דטרמיניסטי קצר (3 בולטים: הפריט בעל העדיפות הגבוהה ביותר [n]; מספר האירועים העסקיים; מספר המכרזים הפתוחים) — לא פסקה רצופה של דחיסת-כל-הנתונים.

### 3.2 דוח שבועי — תבנית מוצעת

| # | סעיף | שינוי מהמצב הקיים |
|---|---|---|
| 1 | BLUF (חדש, כמו ביומי) | חדש |
| 2 | תקציר מנהלים | תקרה מחמירה יותר: 3–5 (לא "עד 8") |
| 3 | **מגמות משולבות עם תחום** (איחוד W1) | לבטל את ההפרדה בין "מגמות" ל"סעיפי תחום" — כל מגמה משויכת לתחום ומוצגת פעם אחת; תחום ללא מגמה מובהקת מקבל סעיף קצר "עדכון שוטף" |
| 4 | מה השתנה מהשבוע הקודם (חדש) | עוצמת מגמה (1–5) מוצגת **גם** לעומת השבוע שעבר: "↑ מ-3 ל-5" |
| 5 | טבלת אירועים עסקיים | כפי שקיים |
| 6 | חקירות עומק | כפי שקיים (עם איחוד ריצות חוזרות — נכנס בסבב 4) |
| 7 | נקודות פתוחות | כפי שקיים |
| 8 | מבט קדימה + מעקב אינדיקטורים | כמו ביומי (עם סטטוס לאורך זמן) |
| 9 | פטנטים ו-IP + רדאר טכנולוגי (מקושרים למגמות) | להוסיף `related_trend` בטבלת הפטנטים/הרדאר |
| 10 | מעקב רכישות ושותפויות | כפי שקיים |
| 11 | לוח 90 הימים הקרובים | כפי שקיים |
| 12 | תעשייה ישראלית (איחוד, כמו ביומי) | טבלה אחת "סוג" במקום 4 תת-סעיפים + סיכום שבועי לפי חברה |
| 13 | פטנטים חדשים (EO/IR) | כפי שקיים |
| 14 | נספח מקורות (משודרג כמו ביומי) | + אמינות מקור |

תוצאה משוערת: מ-24 כותרות Heading-1 היום ל-~14, בלי לאבד מידע — רק לאחד כפילויות.

### 3.3 דוח חודשי — תבנית מוצעת

השינוי הדחוף ביותר הוא **טכני-סכמתי**: לאמץ את אותו מבנה `Sentence{text_he, cites}` שכבר קיים ביומי/שבועי/BD (ר' M1). לאחר האימוץ, המבנה עצמו (exec_summary → trend_paragraphs → sections → outlook → open_points) קרוב ל-RAND/CSIS policy brief; להוסיף:

- **מה השתנה מהחודש הקודם** — לכל `trend_paragraph`, ציון האם המגמה חדשה החודש, מתחזקת, נחלשת או נעלמה.
- **מבט קדימה כאינדיקטורים** — להחליף את `outlook_he` (משפטים חופשיים) באותה סכמת `OutlookIndicator` של היומי/שבועי.

### 3.4 דוח מיקוד BD טריטוריאלי — תבנית מוצעת

| # | סעיף | שינוי |
|---|---|---|
| 1 | BLUF (חדש) | הפעולה הדחופה ביותר + מספר אחד שממחיש את גודל ההזדמנות/איום |
| 2 | תקציר מנהלים | כפי שקיים |
| 3 | תמונת שוק בטריטוריה (`market_bullets`) | כפי שקיים |
| 4 | מהלכי מתחרים (`competitor_moves`) | כפי שקיים |
| 5 | **מפת קונים / צינור הזדמנויות (חדש)** | טבלה: הזדמנות \| שלב (RFI/RFP/הערכה/החלטה/לאחר-זכייה) \| גורם רוכש (אם ידוע) \| תאריך יעד \| ציטוט |
| 6 | נקודות כניסה ופעולות מומלצות | כפי שקיים; `priority`+`confidence` כבר טובים — לשכפל לדוחות אחרים |
| 7 | כנסים קרובים בטריטוריה | כפי שקיים |
| 8 | מיצוב IP של מתחרים | כפי שקיים |
| 9 | מעקב רכישות ושותפויות | כפי שקיים (מסונן לטריטוריה — סבב 4b) |
| 10 | **הנחות ואלטרנטיבות (משודרג מ-`risks_assumptions_he`)** | רשימת "הנחה ↔ מה יפריך אותה" (2–4 שורות) במקום פסקה חופשית |
| 11 | נקודות פתוחות | כפי שקיים |
| 12 | נספח מקורות (משודרג) | + אמינות מקור |

### 3.5 סקר פטנטים — תבנית מוצעת

| # | סעיף | שינוי |
|---|---|---|
| 1 | **שיטה והיקף (חדש, בראש הדוח)** | תיבת נתונים דטרמיניסטית: שאילתת חיפוש, טווח תאריכים, מספר רשומות, **אחוז כיסוי מקצה** כתגית בולטת — לפני תקציר המנהלים |
| 2 | תקציר מנהלים | כפי שקיים, בלי לחזור על נתון הכיסוי |
| 3 | נוף הפטנטים | כפי שקיים |
| 4 | אשכולות טכנולוגיים | כפי שקיים |
| 5 | פרופילי מקצה | כפי שקיים |
| 6 | יחסים עסקיים | כפי שקיים |
| 7 | White spaces | להוסיף מטריצת CPC×מקצה (דטרמיניסטית) לצד הנרטיב |
| 8 | מיצוב ישראלי | כפי שקיים |
| 9 | השלכות עסקיות — משודרג | להוסיף `priority` (H/M/L) ו-`confidence` (0–1) לכל פעולה, כמו ב-BD |
| 10 | ציר זמן | כפי שקיים |
| 11 | מבט קדימה | כפי שקיים |
| 12 | נקודות פתוחות | כפי שקיים |
| 13 | נספחים (בעלי פטנטים, אשכולות, ציר זמן, CPC) | כפי שקיים |
| 14 | נספח מקורות | כפי שקיים |

### 3.6 תשובת חקירת-עומק — תבנית מוצעת

מבנה קיים תקין ברובו; שינוי יחיד נדרש: **להבחין בתצוגה** בין שלוש תוצאות סופיות: `found` (נמצא, עם ביטחון), `not_found` (נחקר במלואו ולא נמצא), ו-`blocked` (נחסם טכנית/אבטחתית ולא נחקר בפועל) — כיום `blocked` מוצג כמו `not_found` (ר' DS3).

---

## 4. רשימת יישום מתועדפת

מיון לפי (השפעה על ציון QA-loop) ÷ (מאמץ). עמודת "תחום" מפנה לרובריקת `docs/QA_CONTINUOUS_LOOP.md`.

| עדיפות | שינוי | מאמץ | קבצים מושפעים | תחום QA | השפעה צפויה |
|---|---|---|---|---|---|
| 1 | **תיקון מנגנון הכשל בדוח היומי** — נפילה חלקית (BLUF דטרמיניסטי + טבלאות) במקום מחיקת כל הפרוזה | S | `docx_builder.py`, `daily.py` | D6 (15%) | גבוהה — מונע דוח יומי ריק-מניתוח (הבסיס נכנס בסבב 3: תקציר מובנה מצוטט; נותר לעצב אותו כ-BLUF ולא כפסקה) |
| 2 | **איחוד סכימת ה-`Sentence` בדוח החודשי** לזו שביומי/שבועי/BD | M | `report_monthly.md`, סכמות, `monthly.py` | D6 (15%) | גבוהה — עקביות בין שלוש רמות הזמן, מונע JSON קטוע |
| 3 | **איחוד סעיפי "תעשייה ישראלית" לטבלה אחת עם עמודת סוג**, ביומי ובשבועי | S | `israel_section.py` | D6 (15%) | בינונית-גבוהה — מבטל כפילות תוכן, מקצר את הדוח |
| 4 | **הוספת BLUF נפרד** (שדה `bluf_he: Sentence`) בדוח יומי, שבועי, BD | M | סכמות + פרומפטים + `docx_builder.py` | D6 (15%), D7 (8%) | גבוהה — "אז מה תוך 10 שניות" |
| 5 | **הפרדת likelihood/confidence באינדיקטורים** — `confidence_level: high/medium/low` נפרד | M | סכמת `OutlookIndicator`, פרומפטים | D6, D7, D4 | בינונית — מיישר עם ICD 203 |
| 6 | **מעקב אינדיקטורים לאורך זמן** — טבלת `indicator_watchlist(indicator_text, first_seen, last_seen, status)` | L | `db/migrations/`, `daily.py`/`weekly.py`, פרומפטים | D6 | בינונית-גבוהה — "מה השתנה" בכל 3 רמות הזמן |
| 7 | **הבחנת `blocked` מ-`not_found` בחקירות עומק** | S | סכמת התוצאה, `deep_search_system.md`, תצוגת הדוח | D4 (12%) | בינונית |
| 8 | **תיבת "שיטה והיקף" + כיסוי-מקצה מודגש בראש סקר הפטנטים** | S | `survey.py`, `patent_survey.md`, renderer | D8 (7%) | בינונית |
| 9 | **`priority`/`confidence` ל-`business_implications` בסקר הפטנטים** | S | `patent_survey.md`, סכמה | D8 | נמוכה-בינונית |
| 10 | **טבלת "מפת קונים / שלב-הזדמנות" בדוח BD** | M | `bd_territory.py`, סכמה, פרומפט | D7 (8%) | בינונית |
| 11 | **מבנה "הנחה↔הפרכה"** ל-`risks_assumptions_he`/`analyst_note_he` בכל סוגי הדוח | M | כל הפרומפטים, סכמות | D6/D7/D8 | נמוכה-בינונית |
| 12 | **דירוג אמינות מקור בנספח** (`source_reliability` → עמודה) | M | `docx_builder.py`, `render_markdown` | D6/D7/D8 | נמוכה — שקיפות |

---

## 5. מקורות

1. ODNI — *Intelligence Community Directive 203, Analytic Standards*. https://irp.fas.org/dni/icd/icd-203.pdf
2. ODNI — *Objectivity* (key judgments, confidence levels). https://www.dni.gov/index.php/how-we-work/objectivity
3. CIA, Center for the Study of Intelligence — *A Tradecraft Primer: Structured Analytic Techniques* (2009). https://www.cia.gov/resources/csi/static/955180a45afe3f5013772c313b16face/Tradecraft-Primer-apr09.pdf
4. SpecialEurasia — *INTREPs and INTSUMs*. https://www.specialeurasia.com/2024/01/12/intreps-intsums-intellgence/
5. U.S. Army — *FM 101-5-2, Report and Message Formats*. https://www.bits.de/NRANEU/others/amd-us-archive/fm101-5-2(uk).pdf
6. IntrepX — *Writing INTREPs vs INTSUMs*. https://intrepx.com/2024/07/10/writing-intreps-vs-intsums-whats-the-difefrence/
7. Janes — *Janes Intara / defence industry insight*. https://www.janes.com/intara-interconnected-intelligence/defence-industry-insight
8. Janes — *Janes Defence and Intelligence Review*. https://shop.janes.com/janes-defence-and-intelligence-review-654-ma-shop2026b
9. Forecast International — *International Military Markets*. https://www.forecastinternational.com/
10. Corporate Finance Institute — *Equity Research Report*. https://corporatefinanceinstitute.com/resources/valuation/equity-research-report/
11. Mergers & Inquisitions — *Equity Research Report: Samples*. https://mergersandinquisitions.com/equity-research-report/
12. Gartner — *Magic Quadrant Research Methodology*. https://www.gartner.com/en/research/methodologies/magic-quadrants-research
13. Gartner — *Magic Quadrant FAQ*. https://www.gartner.com/en/about/magic-quadrant-faq
14. Routledge / IISS — *The Military Balance 2026*. https://www.routledge.com/The-Military-Balance-2026/TheInternationalInstituteforStrategicStudiesIISS/p/book/9781041314240
15. SIPRI — *Trends in International Arms Transfers, 2025*. https://www.sipri.org/publications/2026/sipri-fact-sheets/trends-international-arms-transfers-2025
16. SIPRI — *Sources and Methods for SIPRI Research*. https://www.sipri.org/publications/1995/sipri-fact-sheets/sources-and-methods-sipri-research
17. WIPO — *Guidelines for Preparing Patent Landscape Reports*. https://www.wipo.int/publications/en/details.jsp?id=3938
18. WIPO — *Patent Landscape Report: Generative AI*. https://www.wipo.int/web-publications/patent-landscape-report-generative-artificial-intelligence-genai/assets/62504/Generative%20AI%20-%20PLR%20EN_WEB2.pdf
19. Clarivate — *Derwent Data Analyzer*. https://clarivate.com/intellectual-property/patent-intelligence/derwent-data-analyzer/
20. IDRC — *How to write a policy brief*. https://idrc-crdi.ca/sites/default/files/2021-06/how-to-write-a-policy-brief_1%20(1)_0.pdf
21. CSIS — *Briefs*. https://www.csis.org/taxonomy/term/3014
22. AgentDock — *Territory Plan Template*. https://agentdock.ai/prompt-library/sales/territory-plan-template
23. Salesmotion — *Account Planning Guide*. https://salesmotion.io/blog/account-planning-guide
24. Deloitte Insights — *2026 Aerospace and Defense Industry Outlook*. https://www.deloitte.com/us/en/insights/industry/aerospace-defense/aerospace-and-defense-industry-outlook.html
25. PwC — *Aerospace and defense industry performance and outlook*. https://www.pwc.com/us/en/industries/industrial-products/library/aerospace-defense-review-and-forecast.html
26. Animalz — *BLUF*. https://www.animalz.co/blog/bottom-line-up-front
27. Wikipedia — *BLUF (communication)*. https://en.wikipedia.org/wiki/BLUF_(communication)
28. University of Illinois — *How to Write an Intelligence Product in the BLUF Format*. https://courses.physics.illinois.edu/phys280/sp2022/docs-for-assignments/BLUF%20Writing%20Format.pdf
29. Ontic — *Indications and Warning (I&W) Analysis*. https://ontic.co/resources/article/using-the-indications-and-warning-iw-analysis-to-manage-organizational-risk/
30. המרכז למורשת המודיעין (IICC) — *הערכת מודיעין לאומית*. https://www.intelligence.org.il/?module=articles&item_id=17&article_id=214&art_category_id=21
