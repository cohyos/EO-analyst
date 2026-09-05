# Q4 — תקפות קישורים ונתונים חיצוניים, סבב r1 (2026-09-06 ~02:50)

נתונים גולמיים: [links_r1.csv](links_r1.csv) (416 URL). כלים: httpx (≤6 במקביל, 20 ש', UA דפדפן), feedparser, DB לקריאה, WebSearch לאימות כנסים.

**הסתייגות:** ה-IP של סביבת הבדיקה נחסם (403) על ידי דומיינים שבפרודקשן נטענים תקין (army/naval/airforce-technology.com, war.gov, wikipedia, ausa.org). 14 ה-403 בנספחי הדוחות מיוחסים לכך; לאמת מרשת אחרת לפני סגירה.

**סיכום:** 416 URL · 2xx 349 · 3xx לדומיין אחר 7 · 4xx 66 · 5xx 0 · דפי challenge 30 · אי-התאמת כותרת אמיתית 36 (30 מהן שתי תקלות שיטתיות) · פידים 40/40 נבדקו · stale אמיתי 1 · נכשלים קבוע 2.

| # | חומרה | היכן | תיאור | הוכחה | שורש | סטטוס |
|---|---|---|---|---|---|---|
| Q4-1 | **P1** | items ממקור Safran (13 פריטים: 177,182,186,188,190,194,196,198,200,203,206,208,211) | title/clean_text = טקסט חסימת Cloudflare ("This website is using a security service…"), summary_he NULL | GET → 403 "Attention Required! Cloudflare" | אין זיהוי דף-חסימה לפני שמירה (לעומת item 927 war.gov שסומן נכון "אינו נגיש") | פתוח — לסמן security_status/quarantine 'blocked', להוציא מ-RAG ומסיווג, לנסות דרך RSS |
| Q4-2 | P2 | sources Shephard Media (7), Janes (8) | נכשלים בכל ריצה (fail_count 37), 0 פריטים אי פעם: robots.txt חוסם | לוג ingest | מדיניות robots | פתוח — החלטה: לוותר / פיד חלופי / API של Janes דרך MCP (למשתמש יש מנוי) |
| Q4-3 | P2 | source Aviation Week (5) | 200 אך 0 פריטים; הכתבה החדשה בפיד מ-2026-03-31 | feedparser | פיד נטוש | פתוח — להחליף URL של הפיד |
| Q4-4 | P2 | conferences (15/15) | כולם `status='estimated'`, תאריכי placeholder 1–18 בחודש, last_verified_at NULL; 11/11 שנבדקו מול האתר הרשמי — פער (AUSA 12–14 Oct; DSEI 7–10 Sep 2027; Eurosatory 19–23 Jun 2028; Paris Air Show 14–20 Jun 2027; Farnborough 17–21 Jul 2028; SOF Week 3–6 May 2027; Xponential 17–20 May 2027 Miami; SPIE DCS 18–22 Apr 2027 Kissimmee; ISDEF ~18–20 May 2027; Euronaval 3–6 Nov 2026; IDEX 25–29 **Jan** 2027) | WebSearch | סריקת האימות החודשית FR-12.3 מעולם לא רצה | פתוח — להריץ FR-12.3 + לעדכן seed |
| Q4-5 | P3 | conferences organizer/city | "Française de l'Aéronautique" ל-Eurosatory ו-Euronaval (בפועל COGES/GICAT, GICAN); IDEX organizer "IDEX"; city "US" גנרי | DB | seed לא מדויק | פתוח |
| Q4-6 | P2 (מרחיב F24) | tenders 24,26,27,32,33 | לא הודעות מכרז: Scribd (חסום), כתבות משניות (32/33 כפילות של אותו RFI), דף ספק CanadaBuys | HTTP+title | סורק מכניס כתבות/מסמכים | פתוח — A10 |
| Q4-7 | P3 | tender 14 agency="Sam Acquisition 360"; 16/19 NULL; 29/30 כפילות | חילוץ agency מ-SPA; dedup | DB | — | פתוח — A10 |
| Q4-8 | P3 | נספחי 3 הדוחות | חיובי: כל [n] בגוף יש שורת נספח (5/5, 26/26, 28/28); 14/159 קישורים 403 בסביבת QA בלבד | השוואה | — | לאימות חוזר |
| Q4-9 | P2 | items Globes (18), Leonardo (12) | Globes: title = פתיח הכתבה; Leonardo: 12 פריטים שונים עם title "Financial highlights" | CSV ratio ≤0.29 | חילוץ כותרת (h1/title לעומת sidebar) | פתוח — לתקן ב-choose_title/HTML extractor |
| Q4-10 | P3 | rafael.co.il/news | קוד HTTP 247 לא תקני, ללא title | httpx | אתר | לתעד |
