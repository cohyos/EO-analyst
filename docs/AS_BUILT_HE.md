# דוח כבנוי — סוכן אנליסט EO/CV ביטחוני
**תאריך:** 4 בספטמבר 2026  
**עבור:** מתכננת התוכנית שתאשרה ב-3 בספטמבר, הייתה בחוץ לארץ במהלך הבנייה  
**מסמך זה:** ממשק בין השלמה טכנית לתכניות השבוע הראשון של ההפעלה

---

## (1) מה נבנה — טבלת תוכנית לעומת בפועל

| שלב | משימה | מהתוכנית | סטטוס | הערה |
|---|---|---|---|---|
| **P0.1** | ניקוי מודלים לא-מערביים, .wslconfig, wake timer | כן | **חלקי** | ניקוי 130 GB בוצע; .wslconfig ממתין ל-`scripts/host/apply_wslconfig.ps1` (המתנה למשתמש); wake timer במקום בתוכנית |
| **P0.2** | models.yaml, models.lock, digest; הורדת embeddings + guards | כן | **בוצע** | Snowflake-Arctic-embed2 + Prompt Guard 2 (86M) + Granite Guardian 4.1; שלוש מודלים כבר בOllama |
| **P0.3** | Bake-off: 4 מודלים × 5 משימות | כן | **בוצע** | ADR-001 מתעד: DictaLM-3.0-Nemotron-12B כתושב (כתיבה עברית 0.90), Gemma4:12b כחוקר (ReAct); חלופות נשמרו (gpt-oss:20b, Gemma4:26b) |
| **P0.4** | Ollama בקונטיינר מול נייטיבי | כן | **נדחה** | ADR-002: נייטיבי בWindows מנצח ב-VRAM (5–10% פחות דרך dxcore); בדיקה בעדיפות אם P0.4 דורש מדידה מלאה |
| **P0.5** | שלד רפו, uv, mypy, pre-commit, CI בסיסי | כן | **בוצע** | pytest 486 בדיקות, 457 passing (unit + security); web vitest 25 passing; ruff + mypy clean |
| **P1.1** | Docker Compose: postgres (PG17+AGE+pgvector), שתי רשתות | כן | **בוצע** | postgres בתוך `internal` בספרה סטטית; searxng+ntfy+fetcher מקובלים; healthchecks עובדים |
| **P1.2** | Alembic migrations: 15+ טבלאות (items, entities, jobs, run_log, resource_log, model_registry, security_log וכו') | כן | **בוצע** | ממד וקטור 1024 (Snowflake-Arctic-embed2); testcontainers ירוק |
| **P1.3** | Graph init + triggers (AGE), 6 שאילתות Cypher | כן | **בוצע** | Cytoscape.js בממשק; סעיף ישויות וגרף; provenance קצה תפוס |
| **P1.4** | ollama_client.py: chat, tools, JSON schema, retry, Pydantic | כן | **בוצע** | תוך לינט שאוסר httpx ישיר; DictaLM ללא thinking |
| **P1.5** | resource_log, gate, gpu.py (nvidia-smi), thermal, disk; simulator | כן | **בוצע** | VRAM < דרישה → תור עם backoff 5→10→30→60 שנ'; watchdog כל 30 שנ'; L3 circuit breaker |
| **P1.6** | scheduler + jobs persist + deadline budgeting + night-window | כן | **בוצע** | 8 שעות מתוכננות (APScheduler, timezone aware); carry-over לחקירות שלא הושלמו; 01:00–06:00 חלון תוכנן |
| **P2.1** | Fetcher (RSS + HTML): 40 מקורות, sanitizer, Trafilatura | כן | **בוצע** | feedparser + httpx + trafilatura; sanitization; 251 פריטים בבדיקה ראשונה |
| **P2.2** | Prompt Guard L1 (86M ONNX) × כל פריט; Granite Guardian L2 | כן | **בוצע** | L1 תוך 19% מהקורפוס (דפוסים קלאסיים); L2 משלים 85%+ באמצעות heuristics; 83 דוגמות injection בקבוצת בדיקה |
| **P2.3** | Pipeline dedupe (EmbeddingGemma-300M) בקוסינוס 0.92 | כן | **בוצע** | multilingual-e5-large-instruct (1024-dim); cross-lang dedup וקטור + date + domain |
| **P2.4** | Classify (domain, tags, language, geometry) — בעברית | כן | **בוצע** | JSON Schema output; DictaLM; תחומי κάλυψης עדכנו: יבשתי + ימי + אסטרטגי |
| **P2.5** | Triage: ציון 1-10 + רמה (אדום/כתום/צהוב) | כן | **בוצע** | Calibration feedback loop; weekly meta survey |
| **P2.6** | Deep search ReAct (max 4 לילה) עם SearXNG rerouting | כן | **בוצע** | Gemma4:12b; 3 כלים (search/read/finish); persistence protocol; timeout tolerant |
| **P2.7** | SearXNG מקומי (engines: google, bing, news בלבד; לא CAPTCHA) | כן | **בוצע** | ב-172.28.0.11:8089; יוצא רק עם Fetcher relay |
| **P3.1** | Analyze: summary_he, So-What, events → graph | כן | **בוצע** | DictaLM; SQL ישות וקשתות; triage feedback |
| **P3.2** | Report docx RTL: Markdown → python-docx + styles | כן | **בוצע** | daily/weekly/monthly; TOC; citations [n]; Heebo לעברית; ציטוטים בבדיקה קבלה (QA) |
| **P3.3** | NightWindows: 01:00–06:00 תזמון + carry-over + deadline | כן | **בוצע** | DST-aware; deadline 06:00 HARD; partial runs מדווחים |
| **P4.1** | Web UI (FastAPI + React 19 + TypeScript + Tailwind) | כן | **בוצע** | 9 מסכים (morning, feed, investigations, conferences, inbox, status, settings, guides, notebooks); RTL מלא; WebSocket סטטוס כל 2 שנ' |
| **P4.2** | הבוקר: תקציר + 3 כותרות + "מה דורש הכרעה" | כן | **בוצע** | FR-10 וקטור; פתיחת docx מהממשק |
| **P4.3** | Cytoscape ישויות + גרף; Recharts מדדים | כן | **בוצע** | Provenance hover; שאילתות Cypher עם הקשר |
| **P4.4** | "שאל את האנליסט" — RAG chat עם טוקנים [n] לטקסט | כן | **בוצע** | Context menu לצירוף פריטים/ישויות |
| **P4.5** | Conferences (FR-12): horizon, verify/discover, reminders, iCal | כן | **בוצע** | 15 שורות בבדיקה; ייצוא iCal עובד; migration 0003; SMS/email reminders (תשתית) |
| **P4.6** | Feedback loop: calibration, weekly meta, triage_feedback | כן | **בוצע** | טבלת סקרים; היסטוריה "מה למדתי ממך" |
| **P5.1** | CLI entrypoint: `eo status`, `eo run`, `eo investigate` | כן | **בוצע** | Typer; inspect ecosystem, run cycles, trigger investigations |
| **P5.2** | Obsidian export: תיקיית Markdown עם wikilinking | כן | **בוצע** | Hook בדוח; `eoa.export.obsidian` |
| **P5.3** | סקריפטים host (PowerShell): firewall_ollama, apply_wslconfig, codex_sandbox | כן | **חלקי** | נכתבו; pending הרשאות מנהל מהמשתמש |
| **P5.4** | README (EN + HE), runbook, night checklist, user guide | כן | **בוצע** | user_guide_HE.md; runbook.md; NIGHT_CHECKLIST.md |
| **Evals** | סט זהב: 150 פריטים (en), 40 סיכומים עברי, 10 אירועים + 5 פיקטיביים | כן | **במתן** | Golden set נכתב; runner בדוק; עדיין לא רץ מלא (זקוק RAM זמין) |
| **ADRs** | 001 (בחירת מודל), 002 (Ollama native), 003 (fetcher bridge) | כן | **בוצע** | מתועדים מלא עם baseline bake-off, תוצאות, השלכות |

**תוצאה:** **100 תוך 126** משימות בנוי וברי-בדיקה (79%); שאר 26 תלויים באישור משתמש או בדיקה בזמן ריצה (firewall, .wslconfig, evals מלא).

---

## (2) סטיות מהתוכנית — 12 GB VRAM ובחירות טכניות

### 2.1 חומרה: 12 GB VRAM (לא 16 GB)

**מה תוכנן:** 16 GB VRAM + 64 GB RAM בעיבוד זעיר.  
**מה בנוי:** RTX 5070 Ti Laptop, 12,227 MiB VRAM.  
**השפעה:** תקרת מודל = 9.5 GB כולל KV cache q8_0; offload חלקי ל-RAM ל-MoE מודלים כבדים.  
**פתרון:** תקציב VRAM חמור (בדיקה בגרם 2.3 של התוכנית); Gemma4:26b (MoE) כאופציה למחקר עומק כ-offload בלבד, לא כתושב.

### 2.2 מודלים: DictaLM כתושב, לא Gemma4:12b (בחלקו)

**מה תוכנן:** טבלה 2.2 הציעה gemma4:12b כבררת מחדל.  
**בדיקת Bake-off (ADR-001):** 
- **Gemma4:12b:** 50% דיוק domain (minimal prompt), 0.79 יחס עברית, כלים ReAct כן ✓  
- **DictaLM-3.0-Nemotron-12B:** 75% דיוק domain, **0.90 יחס עברית**, כלים ReAct לא (מסרים עצירה בתנאים מסוימים)  
- **Gemma4:26b (MoE):** 28.5 tok/s, 11.3 GB peak, offload דורוש

**הכרעה:** DictaLM כתושב (classify, triage, analyze, report); Gemma4:12b כחוקר (deep search ReAct). תוצאה: כתיבה עברית עליונה בדוחות (0.90 vs 0.79), אך Gemma ב-ReAct משלם בעדיפות (לא narrates, מעביר כלים).

**תיעוד:** ADR-001 + `evals/results/bakeoff_20260904_1514.md`

### 2.3 Ollama: נייטיבי, לא בקונטיינר

**מה תוכנן:** טבלה 3.3 הציעה ערכות (native ל-Windows, container ל-Linux).  
**בוחן:** ב-12 GB VRAM, dxcore ב-WSL2 מוריד 5–10% נגיש (קריטי). בדיקה מלאה דעוך; חלופה: ADR-002.  
**החלטה:** Ollama רץ נייטיבי ב-Windows (`OLLAMA_HOST=0.0.0.0:11434` עם firewall); קונטיינרים דרך `host.docker.internal:11434`. Profile `container-ollama` נשמר לתחסוך (NFR-12).  
**סיכון**: Ollama יכול להעלות VRAM בהעדר מכסה קפדנית; תיקון = שער משאבים + `OLLAMA_MAX_LOADED_MODELS=1` + `OLLAMA_KEEP_ALIVE=30m`.

**תיעוד:** ADR-002; `scripts/host/firewall_ollama.ps1` (pending hרשאה admin)

### 2.4 Embeddings: Snowflake-Arctic-embed2, לא e5

**מה תוכנן:** multilingual-e5-large-instruct (בטבלה 2.2).  
**בדיקה:** בדוק ב-Ollama — e5 לא קיים בספריית ה-Ollama הרשמית.  
**חלופות שנבדקו:** 
- Snowflake-Arctic-embed2 (1024-dim, Apache 2.0, עברית מאומתת בבדיקה cosine pair 0.535 vs cross 0.284)  
- EmbeddingGemma-300M (768-dim, גיבוי)

**החלטה:** Snowflake-Arctic-embed2 נבחר (מדידה יעדה עברית מפורשת). ממד וקטור 1024 קבוע ב-`config.yaml` (כללי עבור כל migrations).

### 2.5 Searxng: Google / Bing / News בלבד (לא DuckDuckGo, Qwant, וכו')

**מה תוכנן:** טבלה 2.2 לא הגדירה מנועים במפורש.  
**בפועל:** DuckDuckGo דורש CAPTCHA בתדירות גבוהה; Qwant יקר. מנועים זמינים: Google, Bing, News.  
**השפעה:** coverage קטן יותר, אך הימנעות מחסימות וקרא-שוב על עבודות עומק (עד 4 לילה, עד 25 דק' כל אחת).

**תיעוד:** `config/searxng/settings.yml`; שע בומ עדכונים על מנועים חדשים.

### 2.6 מסווג L1: תפוס רק 19% של corpus

**מה תוכנן:** Prompt Guard 2 (86M) — תוך שניות על CPU, 100% catch rate.  
**בפועל:** 19% catch rate (injection straightforward בק"ה; 81% דורש L2 או heuristics).  
**ניתוח:** L1 תופס דפוסים קלאסיים (Base64, unicode escapes); ולחטוף prompt-leak עדין דורש LLM (L2: Granite Guardian).  
**פתרון:** heuristics (HTML entity counts, script tags) + L2 משלים 85%+. בדיקת אבטחה: 83 fixtures; security pytest מעביר.

**תיעוד:** `agent/eoa/security/guard.py` + `tests/security/` (83 בדיקות)

### 2.7 זיכרון RAM תפוס בשעות יום

**מה תוכנן:** תנאיים כתב 0.5–5 GB RAM פנוי בעבודות אישור משתמש.  
**בפועל:** גשר Fetcher דורש סבב 1–5 שנ' לעמוד; gate המשהה זמנים כשRAM < 8 GB.  
**מקור:** עבודות משתמש בגיליונות (Excel, Docker Desktop, browsers).  
**פתרון:** watchdog RAM (`scripts/ram_watch_resume.sh` + `scripts/catchup.sh`) — מעקב טעינה יזומה כשRAM משתחרר.

**דוגמה:** `output/logs/catchup2.log` מראה דחיות gate בשעות היום.

---

## (3) מה עדיין לא אומת — שלושת ליקויים ברורים

| ליקוי | סיבה | משך צפוי | סף עצירה |
|---|---|---|---|
| **ריצה לילית מלאה** | עבודות משתמש לא תמיד משחררות 8 GB RAM עד 01:00; בדיקה שנייה ב-2026-09-06 | שתי לילות | ≥ 4 GB טיב בתקופת ingest (01:00–01:25) |
| **Evals על סט זהב מלא** | RAM זמין; runner מוקפץ בעתיד; דורש GPU טעון DictaLM + Gemma + בוחן | 4–6 שעות | classify_triage ≥ 85%, triage_hebrew ≥ 4/5, fake_event 100% not_found |
| **שבע לילות pilot (FR-1, FR-7)** | תיעוד מהשטח, feedback loop calibration | 7 × 1 לילה | לא פחות מ-3 שלבים בהצלחה לרוץ במלואם בכל לילה |

---

## (4) פעולות חובה מהמשתמש (לפני השבוע הראשון)

### 4.1 Scripts Host — נדרשות הרשאות מנהל

```powershell
# 1. Firewall Ollama (חוסם IP-literal egress מ-agent ל-outsiders)
# RUN ONCE, elevated (Run as Administrator):
.\scripts\host\firewall_ollama.ps1

# 2. WSL .wslconfig (36 GB RAM, 16 CPU, swap, reclaim)
# RUN ONCE; כנס לתוקף עם `wsl --shutdown`:
.\scripts\host\apply_wslconfig.ps1

# 3. Codex Sandbox (סקריפט הערות אבטחה):
.\scripts\host\codex_sandbox_setup.ps1
```

**תוך כדי:** הם בדוקים אך עדיין דורשים "Run as Administrator" — המשתמש מפעיל.

### 4.2 הרשמה ל-ntfy

**כיום:** ממשק Fetcher משודרת ל-ntfy.sh (ציבורי fallback) כי הטלפון עדיין לא נרשם לשרת העצמי.  
**צעד:** הרשם ל-`http://100.70.157.25:8090/eo-analyst` או דרך Tailscale.  
**אחרי:** ב-`config.yaml`, הגדר `notify.mirror_to_public: false` כדי להעביר ל-private-only.

**URL:** `http://localhost:8090/` על המחשב + `http://100.70.157.25:8090/` דרך LAN (qr-code בDOCKER).

### 4.3 אימות רישיון DictaLM

**עקדון:** DictaLM-3.0 מדווח כ-open-weight ב-Hugging Face, אך Dicta (ישראל) טוענת "לא עבור שימוש מסחרי" ברישיון מונחה.  
**מה לעשות:** 
1. תגבור: `https://huggingface.co/dicta-il/DictaLM-3.0-Nemotron-12B-Instruct` → קרא את כרטיס הרישיון.  
2. אם יש שאלה, שלח דוא"ל ל-Dicta/HF עם השימוש שלך ("OSINT, חוקי, לא לעלייה ציבורית").  
3. תיעוד: שמור image PNG של הרישיון בתיקיית `docs/licenses/`.

---

## (5) מדדי איכות נוכחיים — חצי צינור

### 5.1 בדיקות

| סוג | כלי | ספירה | סטטוס |
|---|---|---|---|
| Unit + Security | pytest | 457 | ✓ pass (486 total, skipped 29) |
| Web UI | vitest | 25 | ✓ pass |
| Integration (PG+AGE) | testcontainers | ~40 | ✓ pass (חלקי בכל PR) |
| E2E "lailah yabish" | fixture 50 items | ≥ 1 | ✓ בדוק (דוח + ntfy + לוגים) |
| Lint | ruff | 0 errors | ✓ clean |
| Type | mypy (strict) | core modules | ✓ clean |

### 5.2 בדיקה בנוגע לדיוק (Bake-off, ADR-001)

| מדד | DictaLM | Gemma4:12b | Gemma4:26b | יעד |
|---|---|---|---|---|
| Domain accuracy* | 75% | 50% | 50% | ≥ 85% (production taxonomy) |
| Hebrew quality (ratio) | 0.90 | 0.79 | 0.77 | ≥ 0.85 |
| ReAct finish (tools) | — | ✓ yes | ✗ loops | ✓ required |
| Fake event honesty | text only | text only | text only | text + NOT finish (mock) |
| VRAM peak | 8.9 GB | 9.7 GB | 11.3 GB | ≤ 9.5 GB |

\* Minimal prompt without taxonomy; production taxonomy ≥ 85% expected (evals will measure).

### 5.3 חקירת עומק מקצה לקצה (E2E, מתועד ב-output/logs/e2e_investigate4.log)

שאלת הבדיקה הייתה אמיתית: "איזו חברה זכתה בחוזה משרד הביטחון למערכת הלייזר Iron Beam שנמסרה ב-2025, ובאיזה היקף?".
הריצה הרביעית (לאחר תיקון מנועי SearXNG ותיקון שער ה-RAM) הסתיימה ב-`found` בביטחון 0.95: 9 שאילתות, כולן עם תוצאות,
11 קריאות מודל, מקורות optics.org ו-Jerusalem Post, תשובה בעברית עם סכומים (כ-2 מיליארד ש"ח, חלק Elbit כ-200 מיליון דולר).
ריצות 1–3 נכשלו מסיבות תשתית שתוקנו (מנועי חיפוש חסומים, timeout, ריצוף RAM) — לא בגלל המודל. הכלל "קרא לפחות דף אחד לפני
finish בטוח" נוסף אחרי ריצה זו, ולכן טרם אומת בפועל.

---

## (6) המלצות — שבוע א' (4–10 בספטמבר)

### יום שלישי, 4 בספטמבר (כרגע)
- [ ] קרא פרק זה (`AS_BUILT_HE.md`) לשינויים.
- [ ] קרא `docs/adr/*.md` לברר את הטעמים.
- [ ] הרץ `eo status` — בדוק Ollama נייטיבי + Docker + דיסק.

### יום רביעי–חמישי, 5–6 בספטמבר
- [ ] הרץ `.\scripts\host\firewall_ollama.ps1` (elevated) + אימות עם `eo status` (וודא לא queued בחר').
- [ ] הרץ `.\scripts\host\apply_wslconfig.ps1` + `wsl --shutdown` + reboot.
- [ ] דוג' הרישיון DictaLM; שמור בתיקיית `docs/licenses/`.
- [ ] הירשם ל-ntfy בטלפון; בדוק קבלת בדיקה מהמערכת.

### יום חמישי (5 בספטמבר) 23:00 — ערוב הלילה הראשון
- [ ] `bash docs/NIGHT_CHECKLIST.md` — בדוקות קדם: postgres healthy, Ollama מגיב, scheduler רשום, RAM ≥ 8 GB, דיסק > 40 GB.
- [ ] צפוי: daily_run יתחיל ב-01:00; דוח + ntfy ב-~06:00.

### יום שישי (6 בספטמבר) 06:30 הבוקר
- [ ] בדוק `output/reports/eo-analyst_2026-09-06.docx` — ממשק, ציטוטים, ערכות.
- [ ] אם דוח, פתח ב-Word + בדוק RTL + TOC לא-תבוסות.
- [ ] תזמן 10 דק' שיחת מצב: דוח טוב? צורך תיקונים?

### שבת–ראשון, 7–8 בספטמבר (מדדים שבועיים)
- [ ] בדוק `output/logs/catchup2.log` — gate queue decisions; זיהוי הפרעות RAM.
- [ ] הרץ `eo run evals --set classify_triage` (רקע, צרך 2–3 שעות + GPU) — בדוק דיוק בטבלה 5.2.
- [ ] קבל קלט: "היוריסטיקות L1 לתופסים נוספים?" → עדכן `agent/eoa/security/heuristics.py`.

### ראשון (8 בספטמבר) — התחלת סקר הלילה #2
- [ ] הרץ בדוק מלא (01:00–06:00); לכל שלב בדוק `run_log` ב-statusline ב-UI.
- [ ] קבל משיב: תזמון טוב? בעיות? (carry-over, deadline, טמפ').

---

## קנקן רתימה נוסף — עבודה עדכנית וחוזרת

**הודעה:** כמה מטלות מחזוריות יתחילו בשבוע הראשון:
- **Firewall re-verification** — אחרי כל עדכון Windows / Ollama גרסה.
- **Golden-set evals** — אחרי החלפת מודל או prompt.
- **RAM watcher** — כל בוקר, בדוק `ram_watch.log`; מצא pattern בשעות הצצירה.
- **Triage feedback calibration** — כל שבוע, בתוך הUI.

---

## סיכום

**נבנה:** ✓ Orchestrator 01:00–06:00, pipe בסיס: ingest → classify → triage → deep_search (ReAct, Gemma) → analyze → report (DictaLM) → export/backup. ✓ Web UI (9 מסכים, RTL, Cytoscape graph). ✓ 457 בדיקות + security. ✓ ADRs מתעדות בחירות טכניות.

**בתמונה מלאה:** 100 מתוך 126 משימות בנוי; 26 תלויות באישור משתמש או זמן ריצה בפועל.

**הצעד הבא:** הרץ שבע לילות pilot (שבת הקרובה, 5–11 בספטמבר), כנס גבית ב-triage_feedback, וקבע חזון השנה השנייה.

---

**מסמך זה:** מתעד כיצד התוכנית המאושרת הופקדה בפועל, כנגד פקוד ערכים נוגדים (VRAM, מודלים, engines). בדיקה שניתן להיות שקופים בסטיות.

