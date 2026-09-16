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

## 7. כיול 2026-09-16 — הרחבת דור-6 + פלטפורמות קרקע/ים (החלטת המשתמש: לא לצמצם למונחי פוד בלבד)

**הנחיה**: להשאיר CCA/MUM-T כאותות עצמאיים ולהוסיף תוכניות מטוסי-דור-6 (NGAD/F-47, F/A-XX,
GCAP/Tempest, FCAS/SCAF, KF-21, KAAN) — **לא** לצמצם ל"פוד" בלבד. בנוסף הורחב `platforms` בארבע
השורות המוטסות (רשימת CCA/loyal-wingman מלאה, MALE/HALE, מסוקים ל-mws_eo/ball_gimbals_16in),
ונוספו `platforms`/`opportunity_signals` חדשים ל-`eo_air_defense_warning` ו-`border_long_range_eo`
(כלי רכב/כלי שיט קרקעיים וימיים). המטואצ'ר טוקנים קצרים (≤4 תווים, למשל "ACP"/"OMS"/"F-47")
שונה כעת ל-word-boundary בלבד (ולא substring חופשי) — ראו `agent/eoa/pipeline/opportunity_signals.py`
ו-`tests/unit/test_opportunity_signals.py::TestShortTermWordBoundary`.

### הרצה על ה-DB האמיתי (30 יום אחרונים, in_scope + out_of_scope, read-only)

סה"כ פריטים שנסרקו: 1538. התאמות חדשות לפי שורה (סה"כ, מתוכם כרגע `out_of_scope`):

| line_id | matches | out_of_scope |
|---|---|---|
| mws_eo | 34 | 21 |
| ball_gimbals_16in | 33 | 20 |
| targeting_pods | 32 | 19 |
| lorop_pods | 32 | 19 |
| eo_air_defense_warning | 2 | 1 |
| border_long_range_eo | 1 | 1 |

### Top-25 התאמות חדשות (כותרות מקוצרות, domain נוכחי)

1. `[out_of_scope]` 23260 — New Details On How Space Force Has Waged Electronic Warfare Against Iran — platforms=(Fury,) signals=(missile warning,)
2. `[out_of_scope]` 10613 — After heavy Reaper drone loses in Iran, Air Force accelerates affordable future replacement
3. `[airborne_pods]` 59 — Navy Sounds Like It Now Wants The Air Combat Drone It Notoriously Passed Over A Decade Ago
4. `[out_of_scope]` 120 — הצי האמריקאי מחפש כטב״מים חמושים לנושאות מטוסים
5. `[out_of_scope]` 11295 — Anduril doubles down on Poland with autonomous aircraft pitch for F-35, Apache
6. `[out_of_scope]` 11314 — US Navy's next-generation fighter decision to be unveiled soon, sources say
7. `[out_of_scope]` 51 — US Navy RFI Details Plans for Carrier-Based Collaborative Combat Aircraft (CCA)
8. `[secondary]` 22294 — A new era of uncrewed airpower: GA-ASI's lineup for high-tech warfare
9. `[out_of_scope]` 11330 — South Korea supercharges defense budget to record levels — signal=KF-21 בלבד
10. `[secondary]` 12161 — Pentagon's $1.5B reprogramming would shift money to AI center, MV-75 tiltrotor — signal="mission payload" (חשד: אזכור אגבי)
11. `[airborne_pods]` 2386 — USAF Wants MQ-9 Reaper Successor At A Fraction Of The Cost At $10M Each
12. `[out_of_scope]` 11309 — What to know about Europe's next-gen fighter programs
13. `[out_of_scope]` 20 — UK Government Commits To Boom Refueling For RAF Tankers — signal=GCAP/Tempest אגבי
14. `[airborne_pods]` 12625 — GCAP Electronics Evolution receives contract...
15. `[out_of_scope]` 7245 — ארה״ב: חיל האוויר מאיץ החלפת הריפר בכטב״ם חדש
16. `[naval_surveillance]` 160 — Elbit eyes converting vessels into drone carriers — signal="payload bay"
17. `[airborne_pods]` 12 — To counter China, America's next CCA needs a different mission
18. `[computer_vision]` 153 — **TC-Next: Zero-Shot Multimodal Cyclone Forecasting** — signal="Tempest" (ר' False positive #1 למטה)
19. `[out_of_scope]` 9752 — בריטניה מתחייבת להוסיף תדלוק אווירי בבום — signal=GCAP אגבי
20. `[out_of_scope]` 19 — Defense Business Brief: takeaways from America's first jet engine test site — platforms=(Apache, Black Hawk) signal="collaborative combat aircraft" (ר' False positive #2)
21. `[airborne_pods]` 3 — After 'concerning' losses, Air Force pushes to replace Reaper faster
22. `[out_of_scope]` 17576 — GCAP Electronics Evolution receives contract... (כפילות מקור ל-#14)
23. `[airborne_pods]` 1352 — US Air Force speeds Reaper successor timeline after Iran losses
24. `[out_of_scope]` 19927 — Space Force has 'space control weapons' on orbit
25. `[out_of_scope]` 22523 — Turkey certifies military training aircraft as safe — signal=KAAN בלבד

### False positives שזוהו (לא תוקנו בקוד — ההחלטה אצל ה-lead)

1. **item 153** ("TC-Next: Zero-Shot Multimodal Cyclone Forecasting") — "Tempest" תפס כאן ציקלון
   מטאורולוגי, לא את תוכנית המטוס. False positive אמיתי. לא הוסר מהרשימה לפי ההנחיה המפורשת של
   המשתמש (לא לצמצם) — הסיכון מתקבל בכוונה, כמו התקדימים הקיימים ל-CCA/Fury (סעיף 5 לעיל):
   ה-gate רק מכניס לתחום ברמת `yellow`, לא מנפח חשיבות.
2. **item 19** ("Defense Business Brief...") — כתבת-סיכום (digest) שמזכירה Apache/Black Hawk
   בפסקה אחת ו-"collaborative combat aircraft" בפסקה נפרדת לגמרי — אין קשר אמיתי בין המונחים.
   מגבלה מובנית של substring/word-boundary על כתבות-סיכום מרובות-נושאים.
3. **items 11330/20/9752/22523** — אזכור שם תוכנית (KF-21/GCAP/Tempest/KAAN) אגבי בכתבת תקציב/
   מדיניות רחבה יותר, ללא תוכן פוד/חיישן. זהה לתקדים item 20162 המתועד בסעיף 5 — מתקבל לפי
   עיצוב (רצפת `yellow` בלבד).

### מסקנה

הרשימות "מגנות" גם ברמה סבירה — רוב ה-Top-25 הם סיפורי CCA/דור-6 אמיתיים ורלוונטיים לעסק (Fury/
Vengeance/F-47/GCAP/FCAS/KF-21/KAAN, MQ-9B successor programs, Apache autonomy pitch). False
positive יחיד וברור (#153, "Tempest"/ציקלון) וכמה אזכורים אגביים של שם-תוכנית בלבד — שניהם
מתקבלים לפי עיצוב הרצפה הקיימת (`yellow`, לא high/red) ולפי ההנחיה המפורשת שלא לצמצם. לא בוצע
תיוג מחדש בפועל ב-DB — ההחלטה הסופית אצל ה-lead.
