# CR-platform-opportunity: "כלל הזדמנות אינטגרציה בפלטפורמה"

תאריך: 2026-09-16. טריגר: "אתמול התפרסם שאנדוריל מוציאה את Fury עם פוד אלקטרואופטי חיצוני — זה
כלל לא עלה בסקירה ולא הועלה כאפשרות להתקדמות עסקית".

## 1. אבחון: הסיפור כן הוזן, שלושה פריטים, כולם ארכיון out_of_scope

- item 22760, TWZ, "YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions" (2026-09-15)
- item 23252, Breaking Defense, "Anduril officials say company can't start on CCA production
  contract without FY27 budget" (2026-09-15)
- item 20162, Defense News, "Vengeance and Fury: US Air Force names new CCAs" (2026-09-14)

כל השלושה: `domain=out_of_scope`, `level=archive`, `score=1`, `product_lines=[]`. **זה לא כשל
איסוף — זה כשל סיווג/triage.**

## 2. שרשרת הכלל (file:line)

### 2.1 classify — הכלל שגרם לארכוב

`agent/eoa/llm/prompts/classify.md:7` — "כלל פלטפורמה מול מטע"ד": פריט שעוסק בפלטפורמה **בלי
תוכן אלקטרו-אופטי/אינפרה-אדום ממשי** → `out_of_scope`. זה בדיוק התיאור של 22760/23252: הכתבות
עצמן עוסקות בנשק/תקציב/פלטפורמה, עם אזכור חולף בלבד ל-"targeting pods". המודל יישם את הכלל
נכונה לפי הטקסט שלו (`triage_reason`: "Platform weapons integration, no substantive EO/IR/CV
payload detail" / "only passing mention of targeting pods") — **אבל הכלל לא הבחין בין "אין תוכן
טכני" לבין "יש הזדמנות עסקית לאינטגרציה עתידית"**, שהם שני דברים שונים.

`agent/eoa/pipeline/classify.py:439-441` (ולפני התיקון גם ~413-419 בנתיב ה-batch) — ברגע
ש-`domain == out_of_scope`, המערכת כותבת `level=archive, score=1` ומדלגת על triage לגמרי.

### 2.2 הסינון שמונע מ-triage/analyze לרוץ בכלל

`agent/eoa/pipeline/triage.py:500-506` (`run_triage`) — `if it.get("domain") == "out_of_scope":
mark_stage(...); continue` — פריט out_of_scope לעולם לא מקבל score/level אמיתיים.

`agent/eoa/memory/relational.py:257-259, 273-274` (`_ANALYZE_STAGE_SCOPE_FILTER`,
`get_items_for_stage`) — שלב ה-`analyze` מסונן ב-SQL: `AND domain IS DISTINCT FROM 'out_of_scope'
AND level IS DISTINCT FROM 'archive'`. **פריט out_of_scope אף פעם לא מגיע ל-analyze.**

### 2.3 תיוג קווי מוצר — למה הוא דולג

`agent/eoa/pipeline/analyze.py:955-1011` (בתוך `persist_analysis`) — תיוג קווי המוצר
(`tag_product_lines` / `llm_tag_batch`) רץ **רק בתוך persist_analysis**, כלומר רק לפריטים שהגיעו
לשלב analyze. מכיוון ש-2.2 חוסם out_of_scope מלהגיע לשם — **תיוג קו המוצר אף פעם לא רץ על פריט
out_of_scope**, גם אם הטקסט שלו מכיל "targeting pods" מילולית.

**מסקנה**: פלטפורמה חדשה (CCA נכנס לייצור, עם חריץ פתוח לאינטגרציית פוד/חיישן וכוונות מוצהרות
לשלב פודי ציון מטרות) היא בדיוק הזדמנות עסקית לפיתוח עסקי — גם כשאין בכתבה עצמה פירוט טכני
EO/IR. הכלל הקיים התייחס ל"סיפור פלטפורמה" כאל out_of_scope גורף, וללא domain בתחום → אין תיוג
קו מוצר → אין הופעה בפיד, בדוח היומי, בדוח ה-BD, או בדוח קו המוצר.

## 3. מה השתנה

### 3.1 קונפיג — `config/product_lines.yaml`

לכל אחד מקווי המוצר `targeting_pods` / `mws_eo` / `lorop_pods` / `ball_gimbals_16in` (הקבוצה
הרלוונטית לאינטגרציה על פלטפורמה נישאת — לא `eo_air_defense_warning`/`border_long_range_eo`,
שהם חיישני קרקע ולא "נישאים"): שני שדות חדשים, `platforms` (Fury/YFQ-44A, Vengeance/YFQ-42A,
Watchkeeper X, Hermes 900, Heron, Bayraktar TB2/Akıncı, MQ-9B, Gray Eagle) ו-`opportunity_signals`
(מונחי פוד/חיישן/חריץ אינטגרציה חיצוני, למשל "targeting pod", "external stores", "pylon", וגם
מחלקת הפלטפורמה עצמה — "CCA"/"collaborative combat aircraft"/"loyal wingman"/"MUM-T", ראו כיול
בסעיף 5).

### 3.2 מודול דטרמיניסטי חדש — `agent/eoa/pipeline/opportunity_signals.py`

`detect_platform_opportunity(title, clean_text)` — התאמת substring (לא word-boundary כמו
`eoa.product_lines.tagging`, כי כותרת אמיתית מטה ל-"targeting pod**s**" ברבים, וה-matcher
המחמיר היה מחמיץ) בין `platforms` ל-`opportunity_signals` בו-זמנית, לכל קו מוצר מוגדר. מחזיר
`PlatformOpportunityHint` (קווי מוצר תואמים, פלטפורמות שנמצאו, מונחים שנמצאו) או `None`.

### 3.3 classify.py — רמז + gate חדש

- `_classify_prompt` מזריק `{opportunity_hint}` (טקסט עברי אינפורמטיבי) לתוך הפרומפט.
- `classify.md` קיבל סעיף כלל חדש ("כלל הזדמנות אינטגרציה בפלטפורמה") — חריג מפורש לכלל
  הפלטפורמה מול מטע"ד: פלטפורמה + רמז אינטגרציה חיצוני → בתחום, `dimensions` כולל `business`,
  `tag: platform_integration_opportunity`.
- `apply_platform_opportunity_gate` (חדש, `classify.py`) — override דטרמיניסטי: אם יש hint וה-
  domain עדיין out_of_scope, כופה domain/subdomain לקו המוצר התואם (`airborne_pods.<sub>`),
  מוסיף `business` ל-dimensions ו-`platform_integration_opportunity` ל-tags. רץ **אחרון**
  ב-`run_classify` (אחרי `apply_no_eoir_gate`/`apply_generic_ai_market_gate`) — הוא היחיד שמותר
  לו להחזיר פריט **מ**-out_of_scope.
- `persist_classification` כותב `product_lines` ישירות מה-hint כבר בשלב classify (לא מחכה ל-
  analyze) — "לתייג קו מוצר גם כשה-LLM שמרן" כפי שהמשימה דרשה.

### 3.4 triage.py — רצפת level

`_apply_platform_opportunity_floor` (חדש) — פריט עם tag `platform_integration_opportunity`
מובטח `level >= yellow` (score >= 4), לעולם לא מוריד. `triage.md` קיבל הערה מקבילה ל-A13/A16.

### 3.5 analyze.py — מיזוג (לא דריסה) של product_lines + ניסוח so_what ל-BD

- ה-block שכותב `product_lines` בסוף `persist_analysis` עבר מ"דריסה" ל"איחוד" עם מה שכבר נכתב
  ב-classify (`existing_product_lines = item.get("product_lines") or []`).
- `_analyze_prompt` מזריק הערת הקשר ל-BD (`_PLATFORM_OPPORTUNITY_CONTEXT_NOTE_HE`) כשה-tag נמצא
  — מנחה את `so_what_he` להתמקד בקו מוצר ישראלי, לוח זמנים מוצהר, בניסוח מסויג.

### 3.6 שכבת הדוחות

- `agent/eoa/report/platform_opportunities.py` (חדש) — טבלת "הזדמנויות אינטגרציה בפלטפורמות"
  (≤4 עמודות), מבוססת על tag `platform_integration_opportunity`, מנגנון `tables=[...]` תוסף
  זהה ל-`eoa.report.tech_watch`.
- מחווט לדוח היומי (`agent/eoa/report/daily.py`) וגם לדוח קו המוצר, מסונן לפי line_id
  (`agent/eoa/report/product_line.py`).
- **דוח BD-טריטוריה (`bd_territory.py`) — לא נגעתי**: הדוח הזה מסונן לפי טריטוריית הקונה
  (גיאוגרפיה של הלקוח), בעוד שהזדמנות אינטגרציה בפלטפורמה היא אות גלובלי מצד היצרן/פלטפורמה —
  אין בו "slot" טבעי בלי לסבך את מנגנון ה-citations-gate/redundancy המורכב שהמשימה ביקשה
  להשאיר נקי. פריטים שכבר תויגו בקו מוצר עדיין מופיעים בטבלת ה-market_items הרגילה של דוח קו
  המוצר, ובנוסף בטבלה הייעודית לעיל.

## 4. תיקון שלושת הפריטים — לפני/אחרי

ראו את גוף התשובה הסופית לתוצאות בפועל (הרצה מלאה דרך `classify_item`/`triage_item` +
`persist_analysis` עם `EOA_PIPELINE=1`, שרשרת ענן).

## 5. כיול: מדוע item 20162 (כתבת שם/תקציב טהורה) גם היא נכנסת

`opportunity_signals` כולל את מחלקת ה-CCA עצמה ("CCA"/"collaborative combat aircraft") כי כל
כלי טיס מסוג CCA/loyal-wingman הוא, מעצם הגדרתו, נושא תשלובת פתוחה. משמעות: כתבה שמזכירה שם
פלטפורמה (Fury/Vengeance) לצד "CCA" בלבד — גם בלי שום תוכן פוד/חיישן — תיכנס גם היא לתחום. זו
החלטת עיצוב מכוונת (לא תקלה): ה-gate רק מכניס לתחום; רצפת ה-level ב-triage (סעיף 3.4) מבטיחה
`yellow` בלבד, לא מנפחת חשיבות. כתבה עם תוכן אמיתי (22760/23252) מצופה לקבל ניקוד גבוה יותר
לפי `novelty`/`magnitude` של המודל עצמו, לא בגלל הרצפה.

## 6. בדיקת מקור — `anduril_press` (`config/sources.yaml`)

`https://www.anduril.com/sitemap.xml` קיים (200, `robots.txt` לא חוסם), sitemap מסוג
Google-News extension, 311 `<loc>`, 241 מתחת ל-`/news/`, עם `news:publication_date`/
`news:title`/`news:keywords` לכל פריט — עדכני עד 2026-09-09 (תדירות שבועית, פער סביר).
**אינו** swap פשוט: (א) לא RSS/Atom — `kind: rss` (feedparser) לא יפרש; (ב) `kind: html`'s
`_extract_links` (`agent/eoa/fetch/service.py:133`) קורא רק attribute `href` מהצומת שנבחר
(מיועד ל-`<a href>`) — ה-URL ב-sitemap יושב בתוכן הטקסט של `<loc>`, לא ב-attribute; אימתתי
מקומית ש-`cssselect("loc")` מוצא את כל 311 הצמתים אך `href` מוחזר `None` לכולם. גם
`lxml.html.fromstring` נכשל על str שמכיל את הכרזת `<?xml ... encoding=...?>` (חובה bytes).
תיקון תקין דורש שינוי קטן אך משותף (`agent/eoa/fetch/service.py`, בשימוש ע"י כל מקור מסוג
`html`) — נשאר `verified: false`, עם `notes` מעודכן במקור עם הממצא המדויק, כדי לא לצרף שינוי
בקוד פתיחה משותף/חי לתיקון scope תוכן ממוקד.
