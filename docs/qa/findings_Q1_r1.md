# Q1 — קוד, סבב r1 (2026-09-06 ~02:40)

בדיקות: ruff check **FAIL** (50, 47 אוטו-תיקון) · ruff format **FAIL** (55 קבצים) · mypy **FAIL** (199 ב-35 מודולים) · pytest unit **FAIL** 5/1107 · integration live 2 כשלים (בדיקות מיושנות: head=0010, embedding ARRAY) · integration testcontainers 2 כשלים · web lint PASS · web test PASS 111 · web build **FAIL** (TS2322) · stubs PASS · vulture PASS · pip-audit **FAIL** (transformers 4.57.6 ×6) · npm audit web moderate ×2 (react-router) · config↔models PASS · alembic head יחיד PASS · alembic check FAIL (DB 0009 מול 0010) · Windows readiness PASS.

| # | חומרה | היכן | תיאור | הוכחה | שורש | סטטוס |
|---|---|---|---|---|---|---|
| Q1-1 | **P1** | orchestrator/jobs.py `_pg_dump` | הגיבוי הלילי מגבה את ה-DB של Docker (5433, הקונטיינר הישן עדיין רץ) ולא את ה-DB המקומי: הענף `EOA_ROLE=="agent"` → psycopg, אחרת `docker compose exec ... pg_dump`; במצב host נופל לענף Docker | run_log 72/73/74: `eoanalyst_20260905.sql.gz` (תבנית Docker); `docker ps` מראה eoa-postgres חי | בדיקת תפקיד לא עודכנה ל-host | **תוקן 2026-09-06 03:00** (pg_dump מקומי מ-runtime/pgsql, נפילה ל-COPY) |
| Q1-2 | **P1** | /api/mcp/calls | DB ב-0009, קוד ב-0010 → 500 | curl → relation mcp_calls does not exist | מיגרציה לא הוחלה | **תוקן** (alembic upgrade head 03:00) |
| Q1-3 | **P1** | web/src/components/settings/ChainsEditor.tsx:248 | `npm run build` נכשל: prop `title` על אייקון lucide | TS2322 | עבודת A7c בתהליך | פתוח — A7c |
| Q1-4 | P2 | tests/unit/test_llm_batch_mode.py (5) | mock של get_items_for_stage לא מקבל `item_ids` | TypeError | תיקון F22 שינה חתימה | פתוח — גל תיקון |
| Q1-5 | P2 | config.py database_url, env.py, scripts | ברירת מחדל 5433 | קוד | לא עודכן אחרי ADR-004 | **תוקן** (5432) |
| Q1-6 | P2 | transformers 4.57.6 | 6 חולשות (RCE בטעינת מודל לא אמין; CVE-2026-4372 קריטי, תיקון 5.3.0; CVE-2026-9856 תיקון 5.10.0). חשיפה חיה נמוכה (HF_HUB_OFFLINE=1, מודלים מקומיים) | pip-audit | גרסה ישנה | פתוח — לשדרג ≥5.10 ולאמת guard L1 |
| Q1-7 | P2 | api/services.py, memory/relational.py, report/* | 199 שגיאות mypy (טיפוסי שורות psycopg) | mypy | אין TypedDict לתוצאות | פתוח — חבילת היגיינה |
| Q1-8 | P2 | tests/integration/test_schema.py, test_live_stack.py | בדיקות מיושנות: מצפות ל-AGE vertex, ל-vector, ל-head קבוע | פלט pytest | לא עודכנו אחרי 0006 | פתוח — לעדכן בדיקות |
| Q1-9 | P3 | scripts/bakeoff.py ועוד | 50 ruff (רובן W293) | ruff | היגיינה | פתוח |
| Q1-10 | P3 | 55 קבצים | ruff format | ruff format --check | — | פתוח (לבדוק מחרוזות עברית) |
| Q1-11 | P3 | LevelBadge, I18nContext | 4 אזהרות react-refresh | eslint | — | פתוח |
| Q1-12 | P3 | react-router ≤7.17 | 2 moderate (open redirect, SSR) — SPA בלבד | npm audit | — | פתוח — שדרוג 7.18.3 |
| Q1-13 | P3 | SearxngCfg/NotifyCfg defaults | ברירות מחדל Docker (אינרטיות, config.yaml עוקף) | grep | — | פתוח קוסמטי |
