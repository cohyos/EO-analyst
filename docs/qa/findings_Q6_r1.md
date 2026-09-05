# Q6 — תפעול והפעלה, סבב r1 (2026-09-06 01:54–02:18)

בדיקות חיות עם הפרעות מבוקרות (< 60 ש' כל אחת). המערכת הוחזרה למצב UP ואומתה.

| # | חומרה | היכן | תיאור | הוכחה | שורש | סטטוס |
|---|---|---|---|---|---|---|
| Q6-1 | P2 | cli.py native_start | `eo native start` מדפיס הצלחה בלי להפעיל: `DETACHED_PROCESS` גורם ל-alias של pwsh לצאת 0 בלי להריץ -File; `CREATE_NO_WINDOW` עובד | 4 ניסויים | דגל יצירת תהליך | **תוקן 2026-09-06 03:40** (CREATE_NO_WINDOW + בדיקת חיות אחרי 3 ש') |
| Q6-2 | P3 | supervisor pidfiles | ה-pidfile מצביע על ה-launcher של venv; ה-worker הוא תהליך-בן; הריגת אחד הורגת את השני (Job Object) — קוסמטי | Win32_Process | Start-Process | פתוח קוסמטי |
| Q6-3 | PASS | התאוששות מקריסה | api/orchestrator/ntfy זוהו תוך 1–3 ש' והופעלו מחדש; api חוזר ל-200 אחרי ~48 ש' (import כבד) | supervisor.log | — | PASS |
| Q6-4 | PASS + עיצוב | stop/start | עצירה מסודרת ~1 ש', הפעלה ~2 ש', downtime ~22 ש'. הסקריפט עוצר גם postgres בכל stop — כדאי דגל נפרד | log | — | PASS; הערת עיצוב פתוחה |
| Q6-5a | P3 | runtime/logs/agent.log, web.log | ללא רוטציה (הסופרווייזר מסובב את הלוגים שלו בלבד) | ls | ערוץ כפול | פתוח |
| Q6-5b | P3 | *.log.err | UnicodeEncodeError charmap על שורת לוג עברית (לפני PYTHONUTF8) | traceback | קידוד handler | פתוח — לאמת עם PYTHONUTF8=1 |
| Q6-6 | PASS | scheduler | 9 משימות רשומות; 0 running תקועים; reaper קיים | log/SQL | — | PASS |
| Q6-6b | P3 | jobs 75 fetch_url queued | תקוע ב-queued ללא started_at | SQL | בדיקה מקבילה? | למעקב |
| Q6-6c | P3 | jobs.error | ריק כשנכשל; traceback רק ב-run_log | SQL | finish_job בלי error | פתוח |
| Q6-7 | PASS | gate | 580 החלטות ב-12 ש': 306 proceed, **168 thermal_pause**, 100 queued, 5 deferred — GPU קרוב ל-83°C בלילה | resource_log, nvidia-smi | — | PASS; לשקול כיוונון תרמי / ניקוי אוורור |
| Q6-8 | PASS חלקי | ntfy | פרסום/שליפה מקומיים תקינים; 100.70.157.25 היא כתובת המחשב עצמו — נגישות מהטלפון לא נבדקה | curl | — | בדיקה מהטלפון (משתמש) |
| Q6-9 | P1→תוקן | גיבוי לילי | הגיבוי הישן שוחזר בכישלון (5 FK, טבלאות ריקות; dump מ-PG 17.11 של Docker). הגיבוי המתוקן: 263 TOC, שחזור מלא 6/6 טבלאות | תרגיל שחזור | ענף docker (Q1-1) | **אומת כמתוקן** |
| Q6-10 | ירוק | NIGHT_CHECKLIST | כל הסעיפים ירוקים (RAM 22GB פנוי, דיסק 620GB) פרט ל-autostart (החלטת משתמש) | — | — | ירוק |
| Q6-11 | מידע | Task Scheduler | "EO-Analyst Wake" = no-op (`cmd /c exit 0`), לא תקף אחרי ההסבה; Supervisor לא רשום (החלטת משתמש) | Get-ScheduledTask | — | להסיר Wake בהסכמה |
| Q6-12 | מידע | Docker | eoa-postgres + volumes נשמרים; image נוסף `eoa-postgres-age-test` 692MB לא ברשימה | docker images | — | החלטת משתמש |
