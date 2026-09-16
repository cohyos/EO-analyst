# הצעת הרחבה: מקורות חברות (Task B item 3, 2026-09-16)

תוצר של `scripts/propose_company_sources.py` (read-only, ללא כתיבה ל-DB) -- מדרג חברות
(`entities.kind='company'`) לפי מה שהמערכת כבר "למדה" עליהן: אזכורים ב-90 הימים האחרונים
בפריטים בתחום (`domain != 'out_of_scope'`), מספר קשתות גרף (`graph_edges`), והאם השם מופיע
ברשימות `competitors`/`competitor_products.vendor` של `config/product_lines.yaml` או ב-
`config/company_facts.yaml`. חברות עם מקור `*_press`/`*_linkedin_search` קיים כבר (כולל
ההרחבה מ-Task B items 1-2 באותו יום) הוצאו מהרשימה (התאמה הוריסטית לפי שם/ראשי-תיבות --
ר' `_covers`/`_normalize` בסקריפט).

**זו הצעה בלבד -- אין תיוג/הוספה אוטומטית ל-DB.** ה-lead/המשתמש מאשר לפני הוספה בפועל.

## Top-25

| # | חברה | מדינה | אזכורים (90 יום) | קשתות גרף | ברשימת מתחרים | LinkedIn (ניחוש) |
|---|---|---|---|---|---|---|
| 1 | Northrop Grumman | US | 11 | 2 | כן | linkedin.com/company/northrop-grumman |
| 2 | Lockheed Martin | US | 9 | 3 | כן | linkedin.com/company/lockheed-martin |
| 3 | RTX | US | 7 | 1 | לא | linkedin.com/company/rtx |
| 4 | Xtend | IL | 6 | 11 | לא | linkedin.com/company/xtend |
| 5 | AeroVironment | US | 6 | 6 | לא | linkedin.com/company/aerovironment |
| 6 | General Atomics | US | 5 | 7 | לא | linkedin.com/company/general-atomics |
| 7 | MBDA | EU | 5 | 3 | לא | linkedin.com/company/mbda |
| 8 | Hanwha | KR | 5 | 1 | לא | linkedin.com/company/hanwha |
| 9 | L3Harris | US | 4 | 2 | כן | linkedin.com/company/l3harris |
| 10 | Anthropic | - | 4 | 0 | לא | linkedin.com/company/anthropic |
| 11 | Kongsberg | - | 3 | 11 | לא | linkedin.com/company/kongsberg |
| 12 | ParaZero Technologies | - | 3 | 6 | לא | linkedin.com/company/parazero-technologies |
| 13 | Quantum Systems | - | 3 | 3 | לא | linkedin.com/company/quantum-systems |
| 14 | Naval Group | FR | 3 | 2 | לא | linkedin.com/company/naval-group |
| 15 | Aselsan | TR | 3 | 1 | כן | linkedin.com/company/aselsan |
| 16 | FUSE | - | 3 | 1 | לא | linkedin.com/company/fuse |
| 17 | DJI | - | 3 | 0 | לא | linkedin.com/company/dji |
| 18 | OpenAI | - | 3 | 0 | לא | linkedin.com/company/openai |
| 19 | PGZ | - | 3 | 0 | לא | linkedin.com/company/pgz |
| 20 | Shield AI | US | 2 | 3 | לא | linkedin.com/company/shield-ai |
| 21 | Diehl Defence | DE | 2 | 2 | לא | linkedin.com/company/diehl-defence |
| 22 | Navantia | - | 2 | 2 | לא | linkedin.com/company/navantia |
| 23 | Axon Vision | - | 2 | 1 | לא | linkedin.com/company/axon-vision |
| 24 | Boeing | US | 2 | 1 | לא | linkedin.com/company/boeing |
| 25 | BAE Systems | UK | 2 | 0 | כן | linkedin.com/company/bae-systems |

(489 חברות נבדקו; 291 עם סיגנל כלשהו ולא מכוסות עדיין. `Anthropic`/`OpenAI`/`DJI`/`PGZ` נכללות
כי הן `entities.kind='company'` בפועל אך אינן רלוונטיות ליריבים/BD בתחום EO/IR הישראלי -- לא
מיועדות להוספה, מובאות כאן לשקיפות מלאה של הפלט הגולמי.)

## נוספו בפועל כ-`enabled: false` (Task B item 3, "unambiguous top-tier")

לפי הנחיית המשתמש -- שמות שאין מחלוקת שהם שחקני top-tier ביחס לקווי המוצר של המשתמש -- נוספו
ל-`config/sources.yaml` כ-placeholder `kind: html` / `enabled: false` (ה-URL/selector טרם
אומתו; להפעיל בעת מציאת מקור אמיתי): L3Harris, Lockheed Martin Missiles & Fire Control,
RTX/Raytheon, Northrop Grumman, BAE Systems, General Atomics, Kratos, Baykar, Aselsan,
Excelitas, Leonardo DRS, Elbit America.

## המלצה

מתוך ה-Top-25: **Northrop Grumman, Lockheed Martin, L3Harris, BAE Systems, Aselsan** כבר
ברשימות המתחרים הקיימות (`competitors`/`competitor_products`) -- מועמדים חזקים במיוחד להוספת
מקור press/LinkedIn אמיתי (לא placeholder). **RTX, General Atomics, MBDA, Hanwha, Kongsberg**
בעלי סיגנל גבוה (אזכורים/קשתות) גם בלי הופעה ברשימת המתחרים -- שווה בדיקה ידנית.
