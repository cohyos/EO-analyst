# Q1 — קוד, סבב r2 (2026-09-06 ~05:00)

| בדיקה | r1 | r2 |
|---|---|---|
| ruff check | 50 | **0** |
| ruff format | 55 קבצים | 36 (נדחה במכוון לסוף) |
| mypy | 199/35 | 204/36 (מודולים חדשים; חוב טיפוסי psycopg) |
| pytest unit+security | 5 כשלים | **1628/1628** (2 כשלים מדומים כש-HF_HUB_OFFLINE=1 דולף ל-pytest — Q1-r2-1) |
| integration live / testcontainers | 2+2 כשלים | **15 passed 3 skipped / 5 passed** |
| web lint/test/build | build FAIL | lint 0 שגיאות (6 אזהרות), 137 tests, **build PASS** |
| pip-audit | transformers ×6 | ללא שינוי (ננעל ל-optimum-onnx) |
| npm audit prod / dev | 2 moderate | **0** / 5 (esbuild/vite dev-only — Q1-r2-2) |
| alembic | DB 0009≠0010 | **0016 = head**, שרשרת נקייה 0001→0016 על DB ריק, revisions ייחודיים |
| config/mcp/tenders נטענים; מודולים חדשים מיובאים | — | PASS |

ממצאי r1: P1 3/3 סגורים; P2 4/6 סגורים (פתוחים: Q1-6 transformers, Q1-7 mypy); P3 3/6 סגורים (פתוחים: Q1-10 format, Q1-11 אזהרות eslint 4→6, Q1-13 defaults). חדשים: Q1-r2-1 (P3) בידוד בדיקת guard L1 מ-HF_HUB_OFFLINE/מודל מקומי; Q1-r2-2 (P3) esbuild/vite dev-only.
