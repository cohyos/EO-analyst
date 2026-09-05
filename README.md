# EO-Analyst — Autonomous OSINT Agent for Defense Electro-Optics

**English** | [עברית](#עברית)

## What is EO-Analyst?

A local, offline-first OSINT analyst agent for defense electro-optics (EO/IR) and computer vision. It ingests technical news, company announcements, conference schedules, and patent filings from 40+ Western sources; embeds and deduplicates content; classifies by confidence level; performs deep-search investigations on high-priority items; and generates daily briefing reports in docx format with full citation trails. Zero paid APIs. All inference runs locally on NVIDIA GPU (Ollama). Designed for a defense analyst or researcher who needs continuous, autonomous technical tracking.

## Architecture

**Services** (docker-compose):
- **postgres** (PG17 + Apache AGE + pgvector) — relational data, knowledge graph, embeddings
- **agent** — orchestrator, pipeline stages (classify, triage, analyze), CLI entrypoint
- **fetcher** — RSS/HTML ingestion with network isolation (egress network only)
- **searxng** — meta-search engine for deep investigations (isolated, no internet from agent)
- **ntfy** — push notifications (desktop/mobile) for daily reports and red alerts
- **web** — FastAPI + React UI for ops console and interactive chat

**Models** (Ollama, local host):
- **Resident** (all-night): `gemma4:12b` (Google, 8.2 GB VRAM)
- **Light** (daytime): `gemma4:e4b` (Google, 5.5 GB VRAM)
- **Embed**: `snowflake-arctic-embed2` (1024-dim, 1.3 GB)
- **Guard L1** (CPU): DeBERTa prompt-injection classifier
- **Guard L2** (LLM): `granite3-guardian:2b` (2.5 GB)

All Western origin. Hebrew+English system prompts. Prompts live in `agent/eoa/llm/prompts/*.md`.

## Quick Start

**As of 2026-09-05, the primary supported path on Windows is native (no Docker)** — see
ADR-004 (`docs/adr/004-windows-native.md`). Docker Compose still works and is documented below
as the legacy path.

### Native (Windows, no Docker) — primary path

#### Prerequisites
- Python ≥3.12 already on the host (the `py` launcher or `python` on PATH — no download needed)
- NVIDIA GPU + Ollama native install
- Node.js ≥20
- PowerShell 7 (`pwsh`)

#### Install

```powershell
pwsh -File scripts\native\install_native.ps1
# preview what it would do first, with no changes:
pwsh -File scripts\native\install_native.ps1 -DryRun
```

Idempotent — safe to run twice. Provisions `.venv`, PostgreSQL 17, ntfy, the prompt-guard
model, and the frontend build entirely under `<repo>\runtime\` (no admin rights). See the
script's own `Get-Help scripts\native\install_native.ps1 -Full` for all options
(`-Dev`, `-SkipPostgres`, `-SkipNtfy`, `-SkipGuardModel`, `-SkipFrontend`).

#### First run

```powershell
# Start postgres + ntfy + orchestrator + api
eo native start

# Check status (postgres, ollama, GPU/RAM/disk, native process liveness)
eo status
eo native status

# Run a single cycle (ingest → classify → triage → analyze → report)
eo run daily --mode=eco

# Open UI
# http://127.0.0.1:8765

# Register autostart at logon (once)
pwsh -File scripts\native\register_autostart.ps1
```

See `docs/RUNBOOK.md` § "Native (Windows) Operations" for day-to-day commands (start/stop,
logs, psql, alembic, backups).

### Docker (legacy, pre-2026-09-05)

#### Prerequisites
- Docker Desktop (WSL2) or Linux with Docker
- NVIDIA GPU + Ollama native install
- Python ≥3.12 (or uv)
- Node.js ≥20

#### Install

```bash
# Windows (PowerShell 7)
.\install.ps1

# Linux / WSL
bash install.sh
```

Idempotent — safe to run twice. Checks prerequisites, builds images, starts services, runs migrations, pulls models, seeds data. See the script for options (`-SkipModels`, `-SkipBuild`).

#### First run

```bash
# Check status (postgres, ollama, searxng, GPU/RAM/disk)
eo status

# Run a single cycle (ingest → classify → triage → analyze → report)
eo run daily --mode=eco

# Start orchestrator (nightly at 01:00 Asia/Jerusalem, pre-flight at 23:30)
docker compose up -d agent web

# Open UI
# http://127.0.0.1:8765
```

## Daily Rhythm

**Night window:** 01:00–06:00 Asia/Jerusalem (nightly batch run)
- 01:00: Orchestrator wakes, enqueues daily job
- 01:00–01:25: **Ingest** — RSS/HTML fetch from 40 sources (~220 items/night)
- 01:25–01:35: **Embed & Dedup** — cosine-sim filter at 0.92 threshold
- 01:35–02:05: **Classify** — confidence score (LLM), tags, language detect
- 02:05–02:20: **Triage** — levels (red/orange/yellow), daily-report filter
- 02:20–03:40: **Deep Search** — max 4 ReAct investigations of red items (SearXNG)
- 03:40–04:25: **Analyze** — deeper review, RTL fact extraction
- 04:25–04:55: **Report** — daily docx + push notification at ~06:00
- 04:55–05:05: **Export & Backup** — snapshot to output/backups/

**Report output:**
- `output/reports/eo-analyst_YYYY-MM-DD.docx` — numbered citations to all sources
- `output/backups/eo-analyst_YYYY-MM-DD.sqlite` — database snapshot
- ntfy push: link to report + top red items

**Daytime (06:00–01:00):**
- Light RSS polling every 2 hours (no LLM processing)
- Queue background jobs if triggered
- On-demand `eo investigate "..."` runs available

## CLI Cheatsheet

```bash
# Run cycles
eo run daily              # Full nightly cycle (ingest → report)
eo run ingest             # Fetch only
eo run classify           # Re-classify existing items
eo run triage             # Re-triage (rescore)
eo run analyze            # Deep review
eo run report             # Build docx
eo run daily --mode=eco   # Skip heavy models (light role)

# Deep search
eo investigate "What's the latest on X-ray EO sensors?"
eo investigate "What's the latest on X-ray EO sensors?" --item-id 42

# Status
eo status                 # Services, GPU/RAM, loaded models, gate decisions

# Models
eo models list            # Show registry
eo models lock            # Pin digests to models.lock
eo models pull resident   # Pull a single model

# Serve
eo serve                  # Start FastAPI app (http://127.0.0.1:8765)

# Orchestration
eo orchestrate            # Start background scheduler (runs nightly)
```

## Outputs

Reports live in `output/reports/`:
- `eo-analyst_YYYY-MM-DD.docx` — **Main deliverable**
  - Right-to-left layout (Hebrew text)
  - Executive summary: red/orange/yellow item counts
  - Section per item: title, source, confidence, summary, citations
  - Appendix: embedded model/source audit trail

Backups and logs:
- `output/backups/eo-analyst_YYYY-MM-DD.sqlite` — Full database snapshot (50MB typical)
- `output/logs/run_log/` — Job execution traces

## Security & Isolation

**Network isolation (ADR-002):**
- `agent` container: DNS disabled (`0.0.0.0`), static hosts only (postgres, searxng, ntfy, host.docker.internal)
- `fetcher`: isolated egress network, user-agent header, robots.txt respect
- `searxng`: no direct internet from agent; only fetcher can access it
- Ollama: Windows firewall rule (run `scripts/host/firewall_ollama.ps1` once, elevated)

**Prompt injection defense:**
- L1: DeBERTa classifier (CPU, <1ms)
- L2: LLM judge (granite3-guardian)
- Quarantine + incident logging on flag

**Provenance:**
- Every item: `source_url`, `fetched_at`, item provenance chain
- Every report claim: citation `[n]` mapped to source URL
- No LLM tool access when reading raw content (wrapped as `<<<DATA>>>`)

See **docs/adr/002-*.md** for details and **תוכנית_פיתוח_מפורטת_v2.md §2.4** for the security plan.

## Configuration

All config in `config/*.yaml` (no code changes needed):

| File | Purpose |
|------|---------|
| `config.yaml` | Schedule, thresholds, resource limits, model roles |
| `models.yaml` | Ollama registry (vendor, origin, VRAM est., capabilities) |
| `models.lock` | Pinned digests (auto-generated by `eo models lock`) |
| `watchlist.yaml` | Companies, programs, conferences to track |
| `sources.yaml` | RSS feeds and HTML sources |

Edit `config/config.yaml` to tune:
- `schedule.night_window` — batch run hours
- `triage.levels` — red/orange/yellow thresholds
- `resources.gpu_temp_pause_c` — thermal throttle point
- `deep_search.max_per_night` — max investigations per night

## Development & Operations

See **docs/**:
- **CONVENTIONS.md** — Engineering hard rules (LLM-only via client, Pydantic validation, provenance everywhere)
- **MODULES.md** — Module/API reference (orchestrator, CLI, deep search, pipeline, security, config, resources)
- **RUNBOOK.md** — Ops procedures (check status, re-run a stage, restore from backup, add a source, handle OOM, rotate embedding model, verify isolation)
- **adr/002-*.md** — Architecture decision: Ollama + network isolation

## Links

- **תוכנית_פיתוח_מפורטת_v2.md** — Full Hebrew development plan (context, phases, requirements, architecture)
- **מפרט_דרישות_סוכן_אנליסט_אלקטרואופטיקה.md** — Requirements spec (Hebrew)

---

# עברית

## מה זה EO-Analyst?

סוכן OSINT אוטונומי לעיתונות טכנית בתחום האלקטרו-אופטיקה ההגנתית (EO/IR). מבצע הגד (scraping) של חדשות, הודעות חברות, לוחות ועידות וזיכיונות משלושים וארבע מקורות מערביים; משכן ודיבלוקציה (embedding & deduplication) של תוכן; סיווג לפי רמת ביטחון (confidence); ביצוע חקירות עמוקות של פריטים בדרגת חומרה גבוהה; וייצור דוחות תיזמורת יומיים בפורמט docx עם מסלול מלא של ציטוטים ומקורות. אפס API בתשלום. כל ההיסק מריץ באופן מקומי על GPU של NVIDIA (Ollama). מעוצב לעבור אנליטיקאי הגנה או חוקר הזקוק לעקיבה טכנית רציפה ואוטונומית.

## ארכיטקטורה

**שירותים** (docker-compose):
- **postgres** (PG17 + Apache AGE + pgvector) — נתונים יחסיים, גרף ידע, embedding
- **agent** — מתזמורת, שלבי pipeline (classify, triage, analyze), נקודת כניסה CLI
- **fetcher** — ספיגת RSS/HTML עם בידוד רשת (egress network בלבד)
- **searxng** — מנוע חיפוש מטא לחקירות עמוקות (מבודד, אין אינטרנט מ-agent)
- **ntfy** — הודעות push (שולחן עבודה/נייד) לדוחות יומיים וזיהויים אדומים
- **web** — FastAPI + React UI לקונסול ופעולות ודיון אינטראקטיבי

**מודלים** (Ollama, מחשב מארח):
- **Resident** (כל הלילה): `gemma4:12b` (Google, 8.2 GB VRAM)
- **Light** (יום): `gemma4:e4b` (Google, 5.5 GB VRAM)
- **Embed**: `snowflake-arctic-embed2` (1024-dim, 1.3 GB)
- **Guard L1** (CPU): מסווג injectionשל prompt
- **Guard L2** (LLM): `granite3-guardian:2b` (2.5 GB)

הכל מקור מערבי. הנושאים המערכתיים בעברית + אנגלית. הנושאים חיים ב- `agent/eoa/llm/prompts/*.md`.

## התחלה מהירה

**החל מ-2026-09-05, מסלול ההתקנה הראשי בוינדוס הוא נייטיב (ללא Docker)** — ראו ADR-004
(`docs/adr/004-windows-native.md`). Docker Compose עדיין עובד ומתועד למטה כמסלול legacy.

### נייטיב (Windows, ללא Docker) — מסלול ראשי

#### דרישות מקדמיות
- Python ≥3.12 כבר מותקן במחשב (launcher `py` או `python` ב-PATH — אין צורך בהורדה)
- GPU של NVIDIA + התקנה מקומית של Ollama
- Node.js ≥20
- PowerShell 7 (`pwsh`)

#### התקן

```powershell
pwsh -File scripts\native\install_native.ps1
# תצוגה מקדימה בלבד, בלי לשנות כלום:
pwsh -File scripts\native\install_native.ps1 -DryRun
```

Idempotent — בטוח להריץ פעמיים. מתקין `.venv`, PostgreSQL 17, ntfy, מודל ה-guard, ובניית
הפרונטאנד — הכל תחת `<repo>\runtime\` (ללא הרשאות admin).

#### הריצה ראשונה

```powershell
# התחל postgres + ntfy + orchestrator + api
eo native start

# בדוק סטטוס
eo status
eo native status

# הרץ מחזור יחיד (ingest → classify → triage → analyze → report)
eo run daily --mode=eco

# פתח UI
# http://127.0.0.1:8765

# רישום הפעלה אוטומטית בכניסה למשתמש (פעם אחת)
pwsh -File scripts\native\register_autostart.ps1
```

ראו `docs/RUNBOOK.md` § "Native (Windows) Operations" לפקודות היומיומיות.

### Docker (legacy, לפני 2026-09-05)

#### דרישות מקדמיות
- Docker Desktop (WSL2) או Linux עם Docker
- GPU של NVIDIA + התקנה מקומית של Ollama
- Python ≥3.12 (או uv)
- Node.js ≥20

#### התקן

```bash
# Windows (PowerShell 7)
.\install.ps1

# Linux / WSL
bash install.sh
```

Idempotent — בטוח להריץ פעמיים. בודק דרישות מקדמיות, בונה תמונות, מפעיל שירותים, מריץ הגדלות, משך מודלים, זורע נתונים.

#### הריצה ראשונה

```bash
# בדוק סטטוס (postgres, ollama, searxng, GPU/RAM/disk)
eo status

# הרץ מחזור יחיד (ingest → classify → triage → analyze → report)
eo run daily --mode=eco

# התחל מתזמורת (יומית בשעה 01:00, pre-flight בשעה 23:30)
docker compose up -d agent web

# פתח UI
# http://127.0.0.1:8765
```

## קצב יומי

**חלון לילה:** 01:00–06:00 Asia/Jerusalem (הרצה יומית בתפזורת)
- 01:00: המתזמורת מתעוררת, מערומת עבודה יומית
- 01:00–01:25: **ספיגה** — RSS/HTML מ-40 מקורות (~220 פריטים/לילה)
- 01:25–01:35: **Embedding & Dedup** — מסנן cosine-sim ב-0.92
- 01:35–02:05: **סיווג** — score ביטחון (LLM), תגים, זיהוי שפה
- 02:05–02:20: **טריאג** — רמות (אדום/כתום/צהוב), מסנן דוח יומי
- 02:20–03:40: **חקירה עמוקה** — עד 4 חקירות ReAct של פריטים אדומים (SearXNG)
- 03:40–04:25: **ניתוח** — בדיקה עמוקה יותר, חילוץ עובדות RTL
- 04:25–04:55: **דוח** — docx יומי + הודעת push בסביבות 06:00
- 04:55–05:05: **יצוא וגיבוי** — תמונת ידעי של output/backups/

**פלט דוח:**
- `output/reports/eo-analyst_YYYY-MM-DD.docx` — ציטוטים ממוספרים לכל מקורות
- `output/backups/eo-analyst_YYYY-MM-DD.sqlite` — תמונת פתק של מסד הנתונים
- ntfy push: קישור לדוח + פריטים אדומים עליונים

**יום (06:00–01:00):**
- סקר RSS קל כל שעתיים (אין עיבוד LLM)
- עבודות ברקע אם הופעלו
- הריצות `eo investigate "..."` בדרישה זמינות

## דף רמז CLI

```bash
# הרץ מחזורים
eo run daily              # מחזור לילי מלא (ingest → report)
eo run ingest             # רק משוך
eo run classify           # סיווג חוזר של פריטים קיימים
eo run triage             # טריאג חוזר (rescore)
eo run analyze            # בדיקה עמוקה
eo run report             # בנה docx
eo run daily --mode=eco   # דלג על מודלים כבדים (תפקיד light)

# חקירה עמוקה
eo investigate "מה החדש בחיישני EO של קרנים X?"
eo investigate "מה החדש בחיישני EO של קרנים X?" --item-id 42

# סטטוס
eo status                 # שירותים, GPU/RAM, מודלים טעונים, החלטות שער

# מודלים
eo models list            # הצג רגיסטרי
eo models lock            # קוד דיגיטלי קבוע ל-models.lock
eo models pull resident   # משוך מודל יחיד

# שרת
eo serve                  # התחל אפליקציית FastAPI (http://127.0.0.1:8765)

# מתזמורת
eo orchestrate            # התחל מתזמורת ברקע (רץ בלילה)
```

## פלט

דוחות חיים ב- `output/reports/`:
- `eo-analyst_YYYY-MM-DD.docx` — **משמעות ראשית**
  - פריסת מימין לשמאל (טקסט עברי)
  - סיכום בעל: ספירת פריטים אדומים/כתומים/צהובים
  - סעיף לכל פריט: כותרת, מקור, ביטחון, סיכום, ציטוטים
  - נספח: עקבות ביקורת של מודל/מקור משובח

גיבויים וחרושים:
- `output/backups/eo-analyst_YYYY-MM-DD.sqlite` — תמונת מסד נתונים מלאה
- `output/logs/run_log/` — עקבות ביצוע עבודה

## ביטחון וביצול

**בידוד רשת (ADR-002):**
- `agent` container: DNS מכובה (`0.0.0.0`), רק hosts סטטיות (postgres, searxng, ntfy, host.docker.internal)
- `fetcher`: רשת egress מבודדת, user-agent header, robots.txt כבוד
- `searxng`: אין אינטרנט ישיר מ-agent; רק fetcher יכול להגיע אליו
- Ollama: כלל Windows Firewall (הרץ `scripts/host/firewall_ollama.ps1` פעם אחת, מועלה)

**הגנה על injection של prompt:**
- L1: מסווג DeBERTa (CPU, <1ms)
- L2: שופט LLM (granite3-guardian)
- בידוד + logging תקריות על דגל

**הוכחת כתב:**
- כל פריט: `source_url`, `fetched_at`, שרשרת הוכחת פריט
- כל טענת דוח: ציטוט `[n]` ממופה ל-URL מקור
- אין גישת כלי LLM בעת קריאת תוכן גולמי (עטוף כ- `<<<DATA>>>`)

ראה **docs/adr/002-*.md** לפרטים ו- **תוכנית_פיתוח_מפורטת_v2.md §2.4** לתוכנית ביטחון.

## תצורה

כל התצורה ב- `config/*.yaml` (אין שינויים בקוד הנדרשים):

| קובץ | מטרה |
|------|---------|
| `config.yaml` | לוח זמנים, ערכי סף, מגבלות משאב, תפקידי מודל |
| `models.yaml` | רגיסטרי Ollama (ספק, מקור, VRAM הערכה, יכולות) |
| `models.lock` | קודים דיגיטליים קבועים (ייווצר אוטומטית על ידי `eo models lock`) |
| `watchlist.yaml` | חברות, תוכניות, ועידות לעקיבה |
| `sources.yaml` | RSS feeds ומקורות HTML |

עריכת `config/config.yaml` לכיוונון:
- `schedule.night_window` — שעות הריצה בתפזורת
- `triage.levels` — ערכי סף אדום/כתום/צהוב
- `resources.gpu_temp_pause_c` — נקודת הנעת חום תרמית
- `deep_search.max_per_night` — חקירות מקסימום בלילה

## פיתוח וטיפול

ראה **docs/**:
- **CONVENTIONS.md** — כללים קשים בהנדסה (LLM-only דרך לקוח, Pydantic validation, הוכחה בכל מקום)
- **MODULES.md** — מודול/ייחוס API (מתזמורת, CLI, חקירה עמוקה, pipeline, ביטחון, תצורה, משאבים)
- **RUNBOOK.md** — הליכים לטיפול (בדוק סטטוס, הרץ שלב מחדש, שחזר מגיבוי, הוסף מקור, טיפל ב-OOM, סובב מודל embedding, אמת בידוד)
- **adr/002-*.md** — החלטה אדריכלית: Ollama + בידוד רשת

## קישורים

- **תוכנית_פיתוח_מפורטת_v2.md** — תוכנית פיתוח עברית מלאה (הקשר, שלבים, דרישות, ארכיטקטורה)
- **מפרט_דרישות_סוכן_אנליסט_אלקטרואופטיקה.md** — מפרט דרישות (עברית)