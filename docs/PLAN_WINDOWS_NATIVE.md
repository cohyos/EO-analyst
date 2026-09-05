# תוכנית הסבה: EO-Analyst כאפליקציית Windows ללא Docker + תוכנית תיקונים משולבת

**תאריך:** 2026-09-05 ערב · **החלטת המשתמש:** מעבר מלא מ-Docker להתקנה מקומית, גם במחיר ירידה בבידוד הרשת. התיקונים מהמעבר המשותף ([REVIEW_2026-09-05.md](REVIEW_2026-09-05.md)) משולבים בתוכנית ומתבצעים במקביל.

## 1. עקרונות

1. **הכול בתיקיית הפרויקט.** PostgreSQL נייד, ntfy, Python 3.12, מודל ה-guard: תחת `runtime/` (לא ב-Program Files, ללא הרשאות מנהל). היוצא מן הכלל: משימה אחת ב-Task Scheduler ברמת המשתמש להפעלה אוטומטית בכניסה.
2. **תהליך אחד במקום שלושה קונטיינרים:** ה-agent מבצע קליטה בעצמו (הקוד כבר תומך ב-`EOA_ROLE=host`), ה-web רץ כ-uvicorn מקומי. גשר ה-jobs בין agent ל-fetcher (ADR-003) מתבטל.
3. **בלי הרחבות PostgreSQL:** pgvector מוחלף בחישוב cosine ב-numpy (מאות עד אלפי וקטורים בחלון של 7 ימים, טריוויאלי); Apache AGE מוחלף בטבלת `graph_edges` ב-SQL עם CTE רקורסיבי לשכנים בעומק N. ה-API של `eoa.memory.graph` נשמר זהה כדי שהקוראים (analyze, api, obsidian, monthly) לא ישתנו.
4. **חיפוש בלי SearXNG:** ספק חיפוש מובנה בפייתון (ספריית `ddgs`, מנועים מרובים: duckduckgo/bing/google/brave/yahoo) מאחורי ממשק `eoa.search.provider`; SearXNG נשאר ספק אופציונלי אם קיים URL.
5. **פיצוי אבטחתי לאובדן הבידוד** (ADR-004): נשארים שומרי L1 (ONNX) ו-L2 (LLM), מסגור DATA, SSRF guard, רשימת URL מותרת בחקירות עומק, deny-list דומיינים. נוסף: לוג יוצא של כל בקשת HTTP (host, bytes) לביקורת.
6. **Ollama** נשאר כפי שהוא (מקומי, GPU).
7. **מודלי ענן דרך CLI (U8)** הופכים פשוטים בסביבה מקומית: `agy` / `claude -p` / `codex exec` כתת-תהליך, opt-in מפורש, מסומן בממשק, לעולם לא בריצת הלילה.

## 2. ארכיטקטורה חדשה

```
EO-analyst/
  runtime/                 # לא ב-git
    pgsql/                 # PostgreSQL 17 binaries (EDB zip)
    pgdata/                # cluster, port 5433 (ללא שינוי ב-DATABASE_URL)
    ntfy/ntfy.exe + ntfy-cache/   # port 8090
    python/                # CPython 3.12 (uv python install)
    models/prompt-guard/   # ONNX של Protect AI (EOA_GUARD_L1_DIR)
    logs/
  .venv/                   # uv venv על 3.12, `eo` CLI
  web/dist/                # npm run build, מוגש על ידי FastAPI
  scripts/native/
    install_native.ps1     # התקנה חד-פעמית (הורדות, initdb, venv, build, מיגרציה)
    eoa-supervisor.ps1     # מפעיל ומנטר: postgres, ntfy, agent, web; restart on crash
    register_autostart.ps1 # Task Scheduler "EO-Analyst Supervisor" (at logon, user level)
    migrate_from_docker.ps1# pg_dump מהקונטיינר → restore מקומי (חד-פעמי)
```

תהליכים: `pg_ctl` (postgres) · `ntfy.exe serve` · `python -m eoa.orchestrator.main` (scheduler+worker+fetch) · `uvicorn eoa.api.app:app --port 8765`.

## 3. שלבי הביצוע

| שלב | תוכן | תלות | מבצע |
|---|---|---|---|
| 0 | תוכנית זו; פתיחת סוכני גל 1 | — | Fable |
| 1a | vector.py → numpy + `embedding real[]` (alembic 0006); graph.py → SQL `graph_edges` באותו API; סקריפט ייצוא קשתות AGE → graph_edges | — | סוכן M1 |
| 1b | `eoa.search.provider` (ddgs + searxng אופציונלי); החלפת קריאות ב-deep_search, tenders, conferences | — | סוכן M2 |
| 1c | סקריפטי runtime (install/supervisor/autostart/migrate), `eo start/stop/status` מקומי, תיקון ntfy (F11), תאימות Windows (signals, paths), ADR-004 | — | סוכן M3 |
| 2 | הורדות (באישור): PostgreSQL 17 zip, ntfy.exe, CPython 3.12 (uv), מודל guard; initdb; venv; npm build | 1c | Fable + M3 |
| 3 | מיגרציית נתונים: alembic 0006 על DB הקונטיינר → ייצוא קשתות → pg_dump (ללא ag_catalog) → restore מקומי; אימות ספירות | 1a, 2 | Fable |
| 4 | הפעלה מקומית מלאה; `eo status`; בדיקות אינטגרציה חיות; e2e Playwright מול 8765 המקומי | 3 | Fable + סוכן QA |
| 5 | כיבוי Docker (containers/images; ה-volume נשמר עד אישור מחיקה); docs: RUNBOOK, USER_GUIDE, README, STATUS, NIGHT_CHECKLIST; רישום autostart | 4 | Fable |

## 4. תוכנית התיקונים המשולבת (מ-REVIEW_2026-09-05)

| חבילה | פריטים | מבצע | גל |
|---|---|---|---|
| A1 מכרזים | F1 (קטימת פרומפט תחזית → הגבלת DATA, num_ctx של summarize, שומר פלט), F2 (חילוץ תאריך פרסום/דדליין/גורם מזמין מעמוד ההודעה, סטטוס unknown ללא תאריכים, סגירה אוטומטית > 365 יום), F13 (מדינה) | סוכן | 1 |
| A2 דוחות | F3 (חלון 24 שעות אחורה), F4 (weekly לא מריץ דוח יומי כפול), F5–F8 (תקציר לא חוזר, מכרזים פעם אחת, כותרת עם גרשיים, שם מקור), F9/F16 (dedup אירועים + תאריכים), F10 (docx: ללא TOC-placeholder, היפר-קישורים, כותרת רצה), U13 (שבועי), U1 (HTML נקי, RTL תקין) | סוכן | 1 |
| A3 הבוקר/הפעלה | F12 (KPI נכונים), U2 (כרטיסים לחיצים), U3 (הפניות [n] מקשרות לפריט/מקור), U4 (הרץ עכשיו: מניעת כפילות, מצב ריצה, ETA, פס התקדמות, ג'ובים ברקע), F17 | סוכן | 2 |
| A4 שפה/גאוגרפיה | U5 (הנחיות בלשון אנושית, שפה אחת), U6 (בורר עברית/אנגלית), U7 (סינון/ריכוז לפי מדינה, ניתוח פר-מדינה בדוח) | סוכן | 2 |
| A5 צ'אט/חקירות | U9 (פריט בהקשר נכנס לתשובה; פריטים חסומים/403 לא נכנסים ל-RAG), U11/U12/F18 (ניסוח שאלות חקירה, "המשך חקירה" מובן, ביטחון על not_found) | סוכן | 2 |
| A6 ישויות | U10 (עיצוב מחדש: רשימה + כרטיס ישות + גרף קומפקטי, קישורים עובדים), F15 (סינון ישויות לא רלוונטיות) | סוכן | 2 |
| A7 מודלי ענן | U8 (ספקי CLI: agy/claude/codex; בורר בהגדרות ובצ'אט; אינדיקציה; ברירת מחדל מקומי) | סוכן | 2 |
| Q | F14 (מקורות שנכשלו), בדיקות, e2e, סקירת Codex על ה-diff | Fable/QA | 3 |

## 5. סיכונים והחלטות

- **onnxruntime/torch על Windows 3.12:** גלגלים קיימים; אם optimum בעייתי — נופלים ל-onnxruntime ישיר עם tokenizers.
- **ddgs נחסם/CAPTCHA:** ספק עם כמה מנועים + backoff; אפשר להשאיר SearXNG כ-fallback ב-WSL בעתיד.
- **ניידות PostgreSQL:** zip רשמי של EDB, ללא installer; `pg_ctl` מהסופרווייזר.
- **הרשאות:** ללא admin; Task Scheduler ברמת משתמש. NSSM קיים במחשב כאופציה (דורש admin).
- **Docker volume:** נשמר עד אישור המשתמש למחיקה; pg_dump נשמר ב-`output/backups/`.
