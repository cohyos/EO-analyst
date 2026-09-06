# Q6 — תפעול, סבב r2 (2026-09-06 04:14–04:27)

המערכת UP לאורך כל הסבב. 0 P1. הפרעות מבוקרות (kill api, stop/start) **דולגו** כי Playwright ותיקון הקטיעה רצו כל הזמן — נדחו ל-r3.

| # | חומרה | סטטוס | תיאור |
|---|---|---|---|
| Q6-1 | info | ראיה עקיפה חיובית | הסופרווייזר וילדיו עלו נקי ב-04:11 דרך `eo native start` המתוקן |
| Q6-3b | P3 חדש | פתוח | `eo native status` מדווח "api down (ReadTimeout)" כשה-API בריא אך איטי (2.6 ש' תחת עומס) — timeout 3 ש' צר מדי |
| Q6-4 | הערת עיצוב | פתוח | `eo native stop` עוצר גם postgres (unconditional) |
| Q6-5a | P3 | פתוח | לוגים ישנים ללא רוטציה (agent.log/web.log לפני 04:11) |
| Q6-5b | P3 | **סגור** | 0 שגיאות charmap מאז PYTHONUTF8 |
| Q6-6 | PASS | | 10 משימות מתוזמנות (כולל bd_report ראשון 06:30) |
| Q6-6b/6c | **סגור** | | תור נקי; jobs.error מאויש (ג'ובים 78/79: `'Settings' object has no attribute 'mcp'` — באג נפרד לפני restart, נעלם אחרי) |
| Q6-7 | פתוח | | 43/105 החלטות thermal_pause מאז 04:00; GPU 78–80°C תחת עומס הבדיקות |
| Q6-8 | PASS | | ntfy publish/poll |
| Q6-9 | PASS | | גיבוי .dump (263 TOC) שוחזר ל-DB זמני, 6/6 טבלאות |
| Q6-10 | ירוק | | צ'קליסט לילי (חוץ מ-autostart בהחלטת משתמש) |
| Q6-13 | P2 חדש | פתוח | עשרות `FATAL: password authentication failed` 04:06–04:19 — לא ה-API/מתזמן (מתחברים תקין). מקור סביר: סוכני QA/בדיקות שרצו עם ברירת המחדל (`config.py` default URL, `tests/integration/conftest.py`) אחרי סיבוב הסיסמה; לא פריצה. r3: להסיר סיסמה מברירות המחדל בקוד → לדרוש DATABASE_URL |
| Q6-13b | P3 חדש | פתוח | repair_truncated_hebrew.py: שגיאות `column triage_level/notice_type does not exist` (נתפסות) בשאילתות dry-run |
| Q6-14 | P3 חדש | פתוח | pidfiles ישנים (agent.pid/web.pid) מתים לצד החדשים (api/orchestrator/ntfy) — לנקות בעליית הסופרווייזר |
| Q6-15 | PASS | | .env ו-runtime/eoa.env מסכימים על הסיסמה החדשה; הישנה נכשלת |
