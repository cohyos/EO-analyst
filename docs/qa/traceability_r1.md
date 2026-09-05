# מטריצת עקיבות דרישות — Q7, סבב r1 (2026-09-06)

מקורות: `מפרט_דרישות_סוכן_אנליסט_אלקטרואופטיקה.md` (v1.4), `תוכנית_פיתוח_מפורטת_v2.md`,
`תוכנית_מימוש_שלב_א_סוכן_אנליסט.md` (v1.1), `docs/AS_BUILT_HE.md`, `docs/REVIEW_2026-09-05.md`,
`docs/PLAN_WINDOWS_NATIVE.md`, `docs/adr/001-005`, `docs/MODULES.md`. ראיות חיות: API
`http://127.0.0.1:8765/api/status` (2026-09-06), שאילתות PostgreSQL ישירות (read-only), `eo --help`.

מקרא סטטוס: **מומש** / **חלקי** / **חסר** / **הוחלט לא לממש**.

---

## FR-1 — איסוף (Ingestion)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-1.1 | משיכה אוטומטית RSS/API/scraping, יומית לפחות | מומש | `agent/eoa/fetch/rss.py`, `html.py`, `service.py`; `config/sources.yaml` (40 מקורות); ריצת לילה חיה 2026-09-06 01:46: `entries_seen=113, items_inserted=113, sources_attempted=40, sources_failed=2` (מ-`/api/status`) | F14 (2 RSS + 3 מכרזים נכשלו בלילה מסוים) פתוח כ"לבדוק אילו" — לא חוסם |
| FR-1.2 | תמיכה עברית/אנגלית + תרגום אוטומטי לשפות נוספות | מומש (בסיסי) | `docs/MODULES.md` §Fetch layer, `sanitize.py`; רב-לשוניות מלאה יותר ב-FR-8 (deep search) | תרגום מקורות שוטפים (לא רק חקירות) לא מתועד כמודול נפרד — מרבית המקורות באנגלית/עברית ממילא |
| FR-1.3 | עמידות לכשלים; לוג שגיאות | מומש | `_run_stage` עם error isolation (`docs/MODULES.md` §Orchestrator); `sources_failed` נספר ולא עוצר ריצה (ראיה חיה לעיל) | — |

## FR-2 — סיווג ותיוג

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-2.1 | סיווג תחום/ממד/ישויות/גיאוגרפיה | מומש | `agent/eoa/pipeline/classify.py`; `config/taxonomy.yaml`; DB: 365 `items`, שדה `geography` | גיאוגרפיה חלשה בפועל: 335/353 `items.geography='other'` נכון ל-2026-09-05 (`docs/MODULES.md` §i18n/U7) — הכלי (normalize_country) קיים, אך הזיהוי בשלב ה-classify לא ממלא את השדה. **פער תפעולי מתועד, לא פער קוד** |
| FR-2.2 | טקסונומיה היררכית + תגיות חופשיות, ניתנת לעריכה | מומש | `config/taxonomy.yaml`; `SettingsPage.tsx` עורך YAML דרך `GET/PUT /api/settings/taxonomy` | — |
| FR-2.3 | NER: חברות/מערכות/סכומים/תאריכים/אישים | מומש | `entities` (410 שורות חיות), `events` (119 שורות); סינון רלוונטיות ב-`entity_relevance.py` (F15) | ראו FR-3.3-adjacent: `items.entities_mentioned` מאוכלס רק ב-32/353 פריטים — הורחב ב-2026-09-06 לכלול ראיות גרף/אירועים (ראו entities section) |

## FR-3 — דירוג חשיבות (Triage)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-3.1 | ציון 1–10 לפי רלוונטיות/גודל/חידוש/קרבה לעניין | מומש | `agent/eoa/pipeline/triage.py`; ריצה חיה: `triage: red=0, orange=1, done=1` (01:47) | — |
| FR-3.2 | רמות 🔴/🟠/🟡/⚪ | מומש | `config/taxonomy.yaml: triage_levels`; DB CHECK constraint על `items.level` (red>orange>yellow>archive) | — |
| FR-3.3 | משוב משתמש → כיול | מומש | `agent/eoa/feedback/calibration.py` (FR-3.3), `triage_feedback` (92 שורות ב-DB, יש נתונים אמיתיים) | `calibrate()` נבדק ביחידה (402 טסטים ירוקים); לא אומת שהוא רץ אוטומטית מתוזמן (לא נמצא cron job ל-`calibrate()` ב-`main.py`'s scheduler) — **להריץ ידנית דרך `POST /api/feedback/calibrate` בלבד כרגע** |

## FR-4 — ניתוח וסינתזה

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-4.1 | סיכום 2–4 משפטים בעברית + קישור למקור | מומש | `agent/eoa/pipeline/analyze.py`; `items.summary_he` | — |
| FR-4.2 | ניתוח So-What | מומש | `items.so_what_he`; מוצג בדוח ובפיד | — |
| FR-4.3 | זיהוי מגמות רוחב | מומש | `agent/eoa/report/trends.py`: entity clusters / domain surge / market convergence / tech race (4 סוגי מגמה, ללא LLM, מבחני יחידה 19/19) | — |
| FR-4.4 | כל טענה מגובה בהפניה; אפס הלוצינציות; ציון "לא נמצא" מפורש | מומש | `agent/eoa/report/qa_citations.py` — חוסם לפני הפקה, corrective retry + `_strip_uncited`; `docs/MODULES.md` §Report layer | QA נאכף אוטומטית ולא רק כהנחיה — ראיה חזקה. F1 (רציונל תחזית "חושב בקול" מפריט אחר עקב חיתוך prompt) תוקן 2026-09-06 |

## FR-5 — תוצרים (דוחות)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-5.1 | פורמט Word (.docx) חובה, RTL, סגנונות, TOC אוטומטי | מומש | `agent/eoa/report/docx_builder.py`: `w:bidi`/`w:rtl`, `_add_toc_field`+`_flag_update_fields`, `validate_docx`; קבצים חיים: `output/reports/daily_2026-09-06.docx`, `weekly_2026-09-05.docx` | **בדיקה מפורשת של המשתמש — מאושר: "הדוח תמיד קיים גם כקובץ docx"** (הכרעה 4.9, פרק 13 בתוכנית v2), ומופק בפועל לצד md/html בכל ריצה |
| FR-5.2 | מבנה אחיד: תקציר מנהלים / פרקי העמקה / טבלת אירועים / נספח מקורות | מומש | `build_docx()` בונה בדיוק סדר זה (`docs/MODULES.md` §docx_builder) | — |
| FR-5.3 | סימוכין [n] בכל מקום; טענה בלי סימוכין לא נכנסת (QA חוסם) | מומש | ראו FR-4.4; F23 (בדוח docx הפניה [n] הייתה טקסט בלבד ללא הערת שוליים/קישור אמיתי) **תוקן 2026-09-06** לפי STATUS/REVIEW | הבדיקה הנוכחית לא כללה פתיחת קובץ docx בפועל ב-Word לאימות חזותי של ההיפר-קישורים (Q5/Q4 בתחום UI ולא Q7) |
| FR-5.4 | יומי (5–10 פריטים) / שבועי (מגמות+מבט קדימה) / חודשי (נוף תחרותי מלא) | מומש | `daily.py`/`weekly.py`/`monthly.py`; DB: 7 שורות `reports` (kind=daily/weekly); `monthly.py: players_map()` לנוף תחרותי | חודשי טרם נצפה ברצף DB (jobs cron ל-1 לחודש, טרם הגיע התאריך מאז המימוש) — קוד קיים ונבדק ביחידה בלבד, לא ריצה חיה מלאה |
| FR-5.5 | התראות מיידיות ntfy לאירועי 🔴 | מומש | `agent/eoa/notify/ntfy.py`; `/api/status` מראה `services.ntfy: true`; F11 (קידוד ASCII בכותרת עברית) **תוקן** | — |
| FR-5.6 | שאילתות אד-הוק (RAG chat) | מומש | `POST /api/ask` SSE, `AskPage`/`ChatPanel`; U9 (הקשר פריט לא נכנס לתשובה) **תוקן 2026-09-06** | — |

## FR-6 — זיכרון מנוהל (PostgreSQL היברידי)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-6.1 | שכבה טבלאית: items/entities/events/contracts/sources/conferences/reports עם provenance | מומש | ספירות DB חיות: items 365, entities 410, events 119, conferences 15, tenders 21, reports 7 | "contracts" כטבלה ייעודית לא קיימת בשם הזה — מוזגה למעשה ל-`events` (kind='contract_award') ול-`tenders`; זו החלטת מימוש סבירה, לא מתועדת כ-ADR מפורש אך עולה בבירור מהסכמה |
| FR-6.2 | שכבה גרפית (Apache AGE / חלופת nodes-edges) | הוחלט לא לממש (AGE) → מומש (חלופה) | ADR-004 §1: AGE הוחלף ב-`graph_edges` SQL + CTE רקורסיבי, אותו API ב-`eoa.memory.graph` | **החלטה מתועדת** (ADR-004, שורה 9 בתוכנית Windows Native): מעבר מלא ל-Windows ללא Docker חייב זאת. אין "FR חסר בלי החלטה" כאן |
| FR-6.3 | שכבה וקטורית (pgvector) / RAG | הוחלט לא לממש (pgvector) → מומש (חלופה) | `docs/PLAN_WINDOWS_NATIVE.md` §1.3: `vector.py` → numpy cosine, `embedding real[]` (migration 0006) | **החלטה מתועדת** (ADR-004 הקשר; תוכנית Windows Native §1 סעיף 3): נפח נתונים (מאות-אלפי וקטורים בחלון 7 ימים) הופך את pgvector ללא נחוץ |
| FR-6.4 | זיכרון למידה: triage_feedback/source_reliability/search_playbook/lessons/investigation_log | חלקי | `triage_feedback` (92 שורות פעילות), `investigation_log`/`search_playbook` (`docs/MODULES.md` §Deep Search — persistence protocol קיים), `lessons` (טבלה קיימת, סכימה תקינה, **0 שורות ב-DB חי** נכון לרגע הבדיקה) | `source_reliability` — לא אותרה טבלה/מודול בשם זה בקוד או ב-DB (רק `sources.fail_count`/`reliability` כשדה סטטי ב-`sources.yaml`, לא לומד דינמית כמפורט ב-FR-9.5/5.4 של המפרט). **ממצא**: היסטוריית דיוק-מקור דינמית (FR-6.4/FR-9.5) לא מומשה כמנגנון לומד |
| FR-6.5 | ייצוא Obsidian | מומש | `agent/eoa/export/obsidian.py`, 30/30 טסטים ירוקים, `config.export.obsidian.enabled=true` מאומת חי | — |

## FR-7 — חקירה יזומה (Deep Search)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-7.1 | טריגר אוטומטי ל-🔴 + הפעלה ידנית | מומש | `deep_search.trigger_levels: [red]` ב-`config.yaml`; `eo investigate` CLI; `POST /api/items/{id}/investigate` | — |
| FR-7.2 | 4 סבבי התמדה (ישיר/ניסוח מחדש/מעבר מקורות/פירוק ישויות) | מומש | `agent/eoa/search/deep_search.py` — "4-round persistence protocol" מדויק לפי המפרט (`docs/MODULES.md` §Deep Search) | — |
| FR-7.3 | תנאי עצירה: תקרת שאילתות/עמודים, קונפיגורבילי | מומש | `config.yaml: deep_search.max_queries=15`; `max_pages`, `per_investigation_timeout_min`, `confidence_stop` | תואם מדויק לברירת המחדל שבמפרט (15/30) |
| FR-7.4 | דיווח כן "לא נמצא" עם לוג מלא, לעולם לא ממציא | מומש | U18/F18 (ביטחון 1.0 על not_found חסר משמעות) **תוקן**; מבחן קבלה קבוע: "אירוע פיקטיבי → not_found" (T7 בתוכנית מימוש, ADR-001 bake-off כלל תרחיש פיקטיבי) | — |
| FR-7.5 | אסטרטגיות מוצלחות נשמרות ל-search_playbook ומועדפות | מומש | `_log`/`_learn` ב-`deep_search.py` (`docs/MODULES.md` §Cloud LLM Revision, "engine=cloud_batch" מבדיל שורות) | לא אומת בריצה חיה שאסטרטגיה משוחזרת בפועל משפיעה על סבב הבא (persistence קיים; "learning loop" בפועל לא נבדק E2E) |

## FR-8 — חיפוש רב-לשוני מקביל

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-8.1 | חקירה רצה בכל השפות המוגדרות במקביל (he/en/ru/zh/fr/de) | חלקי | `config.yaml`/`תוכנית v2 §4`: שפות מוגדרות; `provider.py: LANG_REGION` תומך he/en/ru/zh/fr/de | תוכנית v2 §A16 כבר מתעדת החלטה: "שפות משניות רק בסבב 3+" (לא כל הסבבים בכל השפות בו-זמנית) — זו **הכרעה מתועדת** שמצמצמת את "המקביליות המלאה" של המפרט המקורי לטובת תקציב זמן; לא פער בלתי-מוסבר |
| FR-8.2 | תרגום שאילתה ע"י המודל המקומי, התאמת מונחים מקצועיים | מומש | `deep_search.py`'s `query_planner` (LLM, ללא כלים) מנסח שאילתות; `provider.py` region mapping | — |
| FR-8.3 | מיפוי שפה↔מקור (סינית לשחקנים סיניים וכו') | חלקי | `LANG_REGION` קיים כמיפוי טכני (region לפי שפה), אך אין מדיניות מפורשת "לזירה X תמיד שפה Y" מעבר לכך | לא ממצא חמור — המנגנון הטכני קיים; המדיניות התוכן-אנליטית לא מתועדת בנפרד |
| FR-8.4 | נרמול לעברית + דה-דופ חוצה-שפות | מומש | `embed_dedup` stage (cosine sim, ריצה חיה: `embedded=5, duplicates=0`); `docs/AS_BUILT_HE.md` §2.4: dedup חוצה-שפות בוצע ונבדק | — |

## FR-9 — בקרת אבטחת מקורות (Security Gate)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-9.1 | סניטציה: הסרת סקריפטים/iframes, זיהוי טקסט מוסתר/base64/unicode | מומש | `agent/eoa/fetch/sanitize.py`; `tests/fixtures/injection_samples/` (68 דגימות: html_hidden, base64_encoded, zero_width_unicode מנוטרלות בשכבת הסניטציה עצמה, לפני שהמסווג בכלל רואה אותן — מאומת ב-`docs/MODULES.md` §Security guard L1) | — |
| FR-9.2 | גלאי הזרקות (L1 מודל קל) | חלקי | L1b (`protectai/deberta-v3-base-prompt-injection-v2`, ONNX, CPU) בנוי ונבדק, אך **מדידה אמיתית מתועדת בקוד עצמו**: 13/68=19.1% coverage בלבד על קורפוס הבדיקה (מול יעד 80%) | **ממצא מתועד בקוד (לא מוסתר)**: L1b בפני עצמו חלש על tool-hijack/exfiltration/subtle-persuasion; המפרט (A6 בתוכנית v2) כבר קבע החלטה חלופית — L1a היוריסטיקות (85%+ כיסוי, `test_heuristics.py`) + L2 LLM guard משלימים. **הכיסוי הכולל של המערכת (L1a+L2) עומד ביעד; L1b לבדו לא — Q2 (אבטחה) הוא השכבה המתאימה לבדוק סף כיסוי כולל בפועל, כאן מצוין כהערת עקיבות** |
| FR-9.3 | עקרון DATA-בלבד; תיחום מפורש | מומש | `wrap_data()` (`eoa.llm.ollama_client`), נאכף ברמת קונבנציה (`docs/CONVENTIONS.md` rule #3) ובקוד בכל נתיב תוכן חיצוני כולל MCP (`docs/MODULES.md` §MCP client מיישם DATA-framing) | — |
| FR-9.4 | הפרדת הרשאות: worker ללא כלים/רשת; רק orchestrator מפעיל כלים | הוחלט לא לממש (בידוד רשת דוקר) → חלקי (מקומי) | ADR-004: מעבר ל-Windows native ויתר על בידוד רשת ברמת קונטיינר; שכבות פיצוי: L1/L2, DATA-framing, SSRF guard, allow-list URL, deny/blocklist מקור (ADR-004 §3) | **החלטה מתועדת** לגבי בידוד הרשת (ADR-004). אך: "outbound HTTP audit log" המוזכר כפיצוי חלקי **"not yet implemented"** לפי ADR-004 עצמו — זהו **פריט המשך פתוח ומתועד**, לא ממצא חדש |
| FR-9.5 | הסגר + עדכון source_reliability + ntfy + blocklist אחרי 2 אירועים | חלקי | `_maybe_blocklist` קיים (`eoa.security.guard`); `security_log` טבלה קיימת אך **0 שורות ב-DB חי** (לא נצפה אירוע הזרקה אמיתי עדיין — סביר) | ראו FR-6.4: אין `source_reliability` דינמי נפרד; ה"עדכון אמינות מקור" לא מומש כטבלה/מנגנון ייעודי |
| FR-9.6 | לוג אבטחה לביקורת אנושית | מומש (מבנה) | `security_log` טבלה (סכמה קיימת ב-migration 0001) | 0 שורות עד כה — לא נבדק חי עם הזרקה אמיתית (זה תפקיד Q2, לא Q7) |

## FR-10 — שער הבהרות מול המשתמש (Clarification Gate)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-10.1 | שאלות הבהרה לפני חקירת עומק במקרה עמימות | חלקי | `InboxPage` תומך "clarifications" עם מענה חד-קליק; `parse_free_text`/`ingest_answers` בפרויקט feedback כותבים `clarifications(kind='watchlist_proposal')` | לא אותר מנגנון ספציפי שמזהה "עמימות" (שמות דומים/סתירת מקורות) ומייצר שאלת הבהרה *לפני* חקירת עומק באופן פרואקטיבי — הקיים הוא כיוון הפוך (סקר→lessons→clarification proposal). `clarifications` טבלה ריקה (0 שורות) חי |
| FR-10.2 | לפני דוח יומי: רשימת נקודות פתוחות לאישור | מומש | `collect_open_clarifications` ב-`daily.py`; "נקודות פתוחות" section ב-docx; `MorningPage` מציג "מה דורש הכרעה" | — |
| FR-10.3 | timeout לשמירת אוטונומיה (30 דק'/שעה) + סימון "⚠️ הנחת עבודה" | חלקי | `ntfy.ask_user(question, options, timeout_sec=300)` — טיימאאוט קיים (5 דק' ברירת מחדל למימוש ב-`ntfy.py`, שונה מ-30/60 דק' שבמפרט) | לא אותר סימון מפורש "⚠️ הנחת עבודה" בתוכן הדוח עצמו כשפועלים על הנחה לא-מאושרת. ערך ה-timeout (300 שנ' = 5 דק') שונה מברירת המחדל במפרט (30/60 דק') — לא מצאתי תיעוד/הכרעה מפורשת לשינוי הזה |
| FR-10.4 | שאלות קצרות/סגורות למענה מהיר מהנייד | מומש | ntfy Actions (כפתורי פעולה) מוזכרים ב-`ntfy.send(actions=...)`; ADR-005/`docs/REVIEW` F-series מתעדים "אשר/דחה/eco" | — |
| FR-10.5 | הכרעות נשמרות ל-lessons ומיושמות אוטומטית | מומש (מבנה), לא נצפה חי | `parse_free_text` → `lessons(kind='decision')`; **0 שורות `lessons` ב-DB חי** | הלוגיקה קיימת ונבדקת ביחידה (`test_feedback_surveys.py`); לולאת הלמידה בפועל טרם הופעלה (תלוי במשוב משתמש אמיתי דרך הסקר) |

## FR-11 — משוב משתמש יומי

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-11.1 | 5–8 שאלות מתחלפות, ~70% סגורות/30% פתוחות | מומש | `surveys.py: QUESTION_BANK` — 12 שאלות (8 סגורות/4 פתוחות = בדיוק 70/30 יחסית ל-12), `rotating_subset(k=6)` | — |
| FR-11.2 | טופס בממשק / טקסט חופשי מפורסר | מומש | `InboxPage` מציג סקר; `parse_free_text` מפרסר תשובות חופשיות | — |
| FR-11.3 | משוב → triage_feedback/lessons, כיול/watchlist/סגנון | מומש | `ingest_answers()` כותב `lessons(kind='style'/'watchlist'/'decision')`; `calibration.py` | — |
| FR-11.4 | סיכום מטא שבועי | מומש | `feedback/meta.py: weekly_meta_summary`, `post_weekly_meta` (ntfy), נבדק ביחידה; משולב בדוח שבועי (`weekly.py: collect_meta_summary`) | — |
| FR-11.5 | אי-מענה לא חוסם; משוב מצטבר | מומש | תשתית הסקר לא חוסמת ריצות (survey היא side-table, לא בנתיב הקריטי) | — |

## FR-12 — לוח כנסים מתגלגל (Rolling Conference Tracker)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-12.1 | אופק 24 חודשים מתגלגל | מומש | `tracker.roll_horizon(months=24)`, cron `conference_scan` ב-02:30 חודשי | — |
| FR-12.2 | שדות מלאים: רישום/early-bird/CFP/עלות/מציגים/סטטוס | מומש | `conference_card()` — כל 14 השדות מהמפרט (`docs/MODULES.md` §Conferences); DB: 15 כנסים, כולם עם `url`+`organizer` (per STATUS.md: "15 conferences, all with official URLs") | — |
| FR-12.3 | סריקה חודשית: גילוי/אימות/השלמת פרטים | מומש | `monthly_scan()` = `roll_horizon + verify_conference (עד 15) + discover_new`; 67/67 טסטים ירוקים | הזרימה החיה (verify/discover מול חיפוש+LLM אמיתי) לא נבדקה E2E בסבב זה — מתועד כפער בדיקה מודע ב-`docs/MODULES.md` עצמו ("Not exercised by these tests: end-to-end search/fetch/LLM flow") |
| FR-12.4 | תזכורות ntfy (רישום/early-bird/CFP/חודש לפני) | מומש | `reminders.py: due_reminders` — 4 סוגים בדיוק לפי הסף במפרט (rel≥4 לפתיחת הרשמה, 14 יום ל-early-bird/CFP, 30 יום+rel==5 לכנס מרכזי) | — |
| FR-12.5 | שילוב בדוחות: לוח 90 יום (שבועי) + לוח מלא (חודשי) | מומש | `weekly.py: upcoming_conferences(90)`; `monthly.py: full_horizon_table()` | — |
| FR-12.6 | קישור דו-כיווני לידע (Obsidian) | חלקי | Obsidian export קיים אך אין תיעוד מפורש שפריטי כנס מקושרים חזרה לרשומת הכנס (הפרופיל ב-Obsidian הוא לפי entity, לא לפי conference) | לא אותר קוד ספציפי המקשר `items` שנאספו מכנס לרשומת `conferences` — ממצא: FR-12.6 לא מומש במלואו |
| FR-12.7 | ייצוא iCal | מומש | `ical.py: build_ical`; `GET /api/conferences/ical` — text/calendar חי | — |

## FR-13 — ממשק משתמש (HMI)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-13.1–13.2 | חלופות ממשק + אב-טיפוס Claude Design | הוחלט/מומש | תוכנית v2 §8.1/8.4: 3 חלופות הוצגו, **C (היברידי "חדר מצב + עמית") נבחר והוכרע** (§13, הכרעה #4); מומש ישירות ב-React (לא אב-טיפוס נפרד ב-Claude Design — קוצר, מתועד: "P4.1 מקוצר: מסמך D1 הופך לאישור; P4.2 מתחיל מיד") | **החלטה מתועדת**: דילוג על שלב אב-טיפוס נפרד לטובת מעבר ישיר למימוש — אינו "FR חסר בלי החלטה" |
| FR-13.3 | פאנל סטטוס משאבים קבוע (GPU/VRAM/CPU/RAM/רשת/תור/שירותים) | מומש | `StatusStrip` component; `/api/status` מחזיר בדיוק את כל השדות (gpu, ram, disk, loaded_models, queue_depth, stage, services) — נבדק חי | — |
| FR-13.4 | שער אישור: מימוש מתחיל רק אחרי אישור אב-טיפוס | הוחלט לא לממש כלשונו | ראה 13.1-13.2 — הוחלף בתהליך מקוצר עם אישור המשתמש על החלופה עצמה | מתועד |

## FR-14 — שער זמינות משאבי חישוב (Compute Resource Gate)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| FR-14.1 | בדיקה לפני כל קריאת מודל: VRAM/GPU/CPU/RAM | מומש | `agent/eoa/resources/gate.py: ResourceGate.acquire()`; ריצה חיה `/api/status`: `gate.recent_decisions` עם `vram_free/used`, סיבת proceed | — |
| FR-14.2 | תור עם backoff מדורג; דחייה + לוג + ntfy | מומש | `config.yaml` (backoff 5→10→30→60 לפי תוכנית v2 §6 L2); `resource_log`-שווה-ערך ב-`gate.status().recent_decisions` | — |
| FR-14.3 | ניהול swap מודלים + מדיניות min-loaded-time | מומש | ADR-001 "min_loaded_seconds" אנטי-thrashing; `loaded_models` ב-`/api/status` מראה מודל יחיד טעון (DictaLM, 7,444MB) | — |
| FR-14.4 | מודעות לשימוש חיצוני במחשב → מצב מנומס | מומש | `polite_mode.external_gpu_util_threshold`; `batch_window: true/false` ב-`/api/status` | — |
| FR-14.5 | תיעוד כל החלטת שער בפאנל הסטטוס | מומש | `gate.recent_decisions` מוצג חי ב-`/api/status` ו-UI | — |
| — | (תוספת תוכנית v2) שער תרמי (>83°C השהיה, >88°C עצירה) | מומש | `agent/eoa/resources/thermal.py` (מוזכר ב-`docs/MODULES.md`); `/api/status` מחזיר `temp_c: 67` בזמן הבדיקה | לא נצפתה חצייה בפועל של סף החום בסבב זה (תלוי עומס) |

## דרישות לא-פונקציונליות (NFR, מפרט + תוספות תוכנית v2)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| NFR-1 | מקומיות ופרטיות: אין תוכן לענן ב-run רגיל | מומש (עם הרחבה מוסדרת) | ברירת מחדל `llm_providers.mode: local`; `EOA_PIPELINE` gate מכריח שרשרת מקומית אלא אם `mode=cloud` מוגדר מפורשות ומתועד (ADR-005 Revision) | ADR-005 מתעד בפירוט את ה"פרצה המבוקרת" (U8) לענן — מדיניות ברורה, לא סטייה שקטה |
| NFR-2 | חומרה: VRAM ≤ 14GB (מפרט) / בפועל 12GB, מודל ≤9.5GB | מומש | ADR-001: DictaLM resident ~8.9GB peak; תואם A1 בתוכנית v2 | המפרט המקורי הניח 16GB — **מתועד כטעות בסיס ותוקן** (A1, תוכנית v2 §1.1) |
| NFR-3 | ביצועים: מחזור יומי <2 שעות; אד-הוק ≤30 שנ' | מומש (חלקית נצפה) | ריצת לילה 2026-09-06: 01:45:51→01:52:44 = כ-7 דקות בלבד (מעט פריטים חדשים בלילה זה) — בתוך התקציב, אך לא מדד עומס-שיא (200+ פריטים) בסבב זה | evals ביצועים מלאים (200 פריטים סינתטיים) מתועדים כ"עדיין לא רצו במלואם" ב-AS_BUILT (§3) |
| NFR-4 | שפה: תוצרים בעברית תקינה + מונחים אנגליים בסוגריים | מומש | `docx_builder.py` bidi מלא; DictaLM כ-hebrew_editor (ADR-001, יחס עברית 0.90) | — |
| NFR-5 | אמינות: אוטונומי, התאוששות מקריסה, watchdog+ntfy | מומש | `eoa-supervisor.ps1` (restart on crash, backoff); L3 watchdog (heartbeat 30 שנ', circuit breaker 3 כשלים) | — |
| NFR-6 | עקיבות: כל משפט → מקור | מומש | ראו FR-4.4/FR-5.3 (qa_citations חוסם) | — |
| NFR-7 | תחזוקתיות: הוספת מקור/ישות/תגית ב-YAML בלבד | מומש | `config/sources.yaml`/`watchlist.yaml`/`taxonomy.yaml`; `SettingsPage` עורך YAML | — |
| NFR-8 | אבטחה: הפרדה מארגון | לא רלוונטי (סביבה אישית) | — | — |
| NFR-9 | חסינות הזרקות: 100% תוכן דרך FR-9 | חלקי | ראו FR-9.2 — L1b לבדו לא מגיע ל-100%/80%; L1a+L2 יחד כן (לפי הבדיקות התיעודיות בקוד) | ראו FR-9.2 הערה; Q2 (שכבת אבטחה) היא הכתובת לאימות סף כולל חי |
| NFR-10 | פרטיות חיפוש: SearXNG מקומי | הוחלט לא לממש (SearXNG) → מומש (חלופה) | ADR-004 §1 + `PLAN_WINDOWS_NATIVE.md` §1.4: SearXNG הוחלף ב-`ddgs` (ריבוי מנועים, ללא חשבון); SearXNG נשאר backend אופציונלי | **מתועד**: מעבר Windows-native חייב זאת (אין container runtime זמין) |
| NFR-11 | איכות תוצרים: עברית רהוטה ברמת אנליסט בכיר | מומש (עם סייג) | DictaLM נבחר בדיוק על בסיס זה (ADR-001, יחס 0.90); F5 (תקציר חוזר על גוף הפרק) **תוקן** | האם "רמת אנליסט בכיר" מספקת — נבדק תוכן/רובריקה ע"י Q3, לא Q7 |
| NFR-12 | ניידות: עצמאי מ-Claude Code/ענן; התקנה בפקודה אחת | מומש (עודכן) | תוכנית מקורית דרשה Docker Compose; **הוחלף ב-ADR-004** ל-`scripts/native/install_native.ps1` (ללא הרשאות מנהל, תחת `runtime/`) | הכרעה מתועדת (ADR-004) לשנות את מסלול ההתקנה מ-Docker ל-native; README מתעד את שני המסלולים (native=ראשי, Docker=legacy) |
| NFR-13 (תוכנית v2, A2) | מקור מודלים מערבי בלבד | מומש | `config/models.yaml: allowed_origins: [US,EU,UK,CH,CA,IL]`; נאכף זמן-ריצה (`docs/MODULES.md` §Config); כל מודל עם `origin` מתועד | 7 מודלים סיניים/DeepSeek/bge-m3 הוסרו בפועל (תוכנית v2 §13 הכרעה #1) |
| NFR-14 (תוכנית v2, A9) | דיסק ≥150GB פנוי, watchdog דיסק | מומש | `/api/status`: `disk_free_gb: 621.8` (הרבה מעל הסף); `resources/disk.py` | — |
| NFR-15 (תוכנית v2, A13) | אפס עלות API בזמן ריצה רגילה | מומש | ntfy self-hosted, SearXNG/ddgs חינמיים, מודלים מקומיים; `llm_calls` בפועל = 0 שורות (אין קריאות ענן שקרו) | עלות ענן אפשרית רק תחת `mode=cloud` מוגדר במפורש + `pricing` ב-config (ADR-005 Revision) — לא הופעל |

## ממשק אדם-מכונה — בקשות ספציפיות (REVIEW_2026-09-05, U1–U14)

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| U1–U7, U9–U13 | תיקוני UI/UX (HTML דוח, KPI לחיצים, הפניות [n], "הרץ עכשיו", לשון אחידה, בורר שפה, גיאוגרפיה, RAG הקשר, חקירות, "המשך חקירה", דוח שבועי) | מומש | כולם מסומנים "תוקן 2026-09-06" ב-`REVIEW_2026-09-05.md`; מומחש בקוד: `i18n/` (U6), `geography.py` (U7), `ModelPicker`/`ChatThread` (חלק מ-U8) |
| U8 | בחירת ספק LLM (מקומי/ענן) — גלובלי, שרשרת נפילה, API ישיר, batch, MCP לחקירות ענן | מומש (עם "טרם live" מתועד) | ADR-005 + Revision 2026-09-06 — קוד/מיגרציות/בדיקות קיימים במלואם (1072 unit tests ירוקים); **אך**: `docs/MODULES.md` §Cloud LLM Revision מסיים במפורש "Left for the user: restart the port-8765 uvicorn... none of it is live there yet". אומת חי: `llm_calls`=0 שורות, `mode` כרגע `local` (ברירת מחדל) | 
| U14 | Docker מול התקנה מקומית | הוחלט ומומש | ADR-004: מעבר מלא ל-Windows native | — |

## MCP layer (A8, תוכנית Windows Native §4)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| A8.1 | שכבת לקוח MCP באג'נט (קריאה בלבד, allow-list, DATA-framing) | מומש (קוד) | `agent/eoa/mcp/client.py`+`registry.py` — DATA-framing, guard screening, audit (`mcp_calls`) מתועדים בקוד | קובץ ADR מוזכר בכל מודול (`docs/adr/006-mcp-sources.md`) **אינו קיים בפועל** ב-`docs/adr/` (רק 001–005 קיימים) — **ממצא תיעוד** |
| A8.2 | שרתי MCP: רכש (SAM.gov/USAspending/DSCA/Federal Register/Congress.gov) | מומש (קוד) | `agent/eoa/mcp_servers/procurement.py` — 5 כלים, נבדק שהחתימות/ה-URLs אמיתיים | — |
| A8.3 | Janes Data Services (מנוי המשתמש, JANES_API_KEY) | מומש (קוד, לא מאומת מול API אמיתי) | `agent/eoa/mcp_servers/janes.py` — קונה גנרי, "best-effort construction... verify against your subscription's API docs" (מתועד בקוד עצמו כלא-מאומת) | תלוי במפתח אמיתי מהמשתמש; לא נבדק חי מול Janes |
| A8.4 | פטנטים (EPO OPS / PatentsView) | מומש (קוד, אימות חלקי) | `patents.py` — EPO OPS אומת חי (401 תקין ללא credentials, מבנה ה-endpoint נכון); PatentsView "unverified live" (DNS לא נפתר לדומיין המודרני) | — |
| A8.5 | הפעלה בפועל | **חסר (לא מופעל)** | `config/mcp.yaml: enabled: false` (מתג כיבוי גלובלי); DB: migration `0010_mcp_calls.py` **קיים בקוד אך לא הוחל** (`alembic current` = `0009`; `select count(*) from mcp_calls` → `relation does not exist`) | **ממצא ברור**: השכבה כתובה ונבדקת ביחידה, אך כבויה בקונפיג וחסרה מיגרציית DB — נדרש `alembic upgrade head` + `enabled: true` להפעלה בפועל. אין תיעוד החלטה מפורש להשאיר כבוי (סביר שזו שלב-ביניים מכוון, אך לא מתועד כהחלטה) |

## אבטחה — שכבות L1/L2 (חתך Q7, פירוט מלא ב-Q2)

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| — | L1 היוריסטיקות (regex/entropy וכו') | מומש | `tests/security/test_heuristics.py` — 85%+ כיסוי מתועד |
| — | L1b מסווג ONNX (CPU, ללא אינטרנט) | מומש (עם ממצא ביצועים מתועד) | ראו FR-9.2 — 19.1% כיסוי עצמאי, מסמך בקוד עצמו |
| — | L2 LLM guard (Granite Guardian) | מומש | `config/models.yaml: granite_guardian_2b`; `guard.screen()` מפעיל L2 על חשודים |
| — | הסגר + blocklist אחרי 2 אירועים | מומש (מבנה, לא נצפה חי) | `_maybe_blocklist`; `security_log`=0 שורות (לא נצפה אירוע אמיתי) |

## מכרזים/RFI/RFP + תחזיות (section 5.2)

| ID | דרישה | סטטוס | ראיה | הערות/פער |
|---|---|---|---|---|
| — | סריקת מכרזים (TED/SAM.gov/UK Contracts Finder) + סינון רלוונטיות | מומש (עם ממצא תוכן פתוח) | `agent/eoa/tenders/scan.py`; DB: 21 tenders | F24 (מ-QA_PROGRAM עצמו, 2026-09-06 02:00): רשימה עדיין כוללת שורות ישנות/סגורות/לא-רלוונטיות ("21 שורות, 8 סגורות 2015–2025, 13 ללא תאריך") — **ממצא פתוח, מוזכר כבר ב-QA_PROGRAM.md כדרישה לסבב r1, לא ממצא Q7 חדש אלא אישור שהוא עדיין רלוונטי בזמן כתיבת מסמך זה** |
| — | תחזיות (RFI/RFP צפויים לפי אירועים+פלטפורמה) | מומש | `forecast.py` — 8 `tender_forecasts` ב-DB, `compute_likelihood` rubric מתועד; F1 (רציונל "חושב בקול") **תוקן** | — |

## דוחות שבועי/חודשי ומגמות

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| — | דוח שבועי (מגמות + מבט קדימה + לוח 90 יום) | מומש | `weekly_2026-09-05.docx` קיים בפועל; U13 (איכות נמוכה) **תוקן** |
| — | דוח חודשי (נוף תחרותי + לוח דו-שנתי) | מומש (קוד), לא נצפה ריצה חיה מלאה | `monthly.py`; cron `monthly_run` ב-1 לחודש 03:30 — טרם הגיע מועד ריצה טבעי מאז המימוש |

## תצפית יבשה/ימית (הרחבת תחומי כיסוי, תוכנית v2 §1.3)

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| — | הוספת תחום "מטע"די תצפית יבשתיים/ימיים/אסטרטגיים" לטקסונומיה | מומש | AS_BUILT §P2.4: "תחומי כיסוי עדכנו: יבשתי + ימי + אסטרטגי" |
| — | הרחבת watchlist (Controp, Hensoldt Optronics, Vectronix, WESCAM, Terma וכו') | מומש | `config/watchlist.yaml` — כל השמות מהתוכנית קיימים בפועל עם `focus: [land_surveillance/naval_surveillance/...]` (נבדק ישירות בקובץ) |

## Western-only models + bake-off + DictaLM

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| — | רשימת מקורות מערבית בלבד, נאכפת זמן-ריצה | מומש | `config/models.yaml: allowed_origins`; ראו NFR-13 |
| — | Bake-off מתועד + הכרעת מודל תושב | מומש | `docs/adr/001-model-selection.md` — טבלת מדדים מלאה, תוצאה: DictaLM resident / gemma4:12b investigator |
| — | DictaLM בשימוש בפועל | מומש | `/api/status` חי: `loaded_models: [{"name":"hf.co/dicta-il/DictaLM-3.0-Nemotron-12B-Instruct-GGUF:Q4_K_M", ...}]` — **המודל טעון בפועל ברגע הבדיקה** |

## תזמון לילי ו-Deep Search cap

| ID | דרישה | סטטוס | ראיה |
|---|---|---|---|
| — | חלון לילה 01:00–06:00 + ריצה יזומה | מומש | `config.yaml: schedule.night_window: {start: "01:00", end: "06:00"}`; `/api/status: night_window: true`, `next_run_at: 2026-09-07T01:00:00+03:00` |
| — | תקרת deep search 4/לילה, ניתנת להרחבה | מומש | `config.yaml: deep_search.max_per_night: 4` — פרמטר config, ניתן לעריכה דרך `SettingsPage`/קובץ ישירות |

## Docx-always, ntfy, Hebrew quality gates, citations QA

כל הפריטים הללו כוסו לעיל תחת FR-5/FR-9/NFR-4/NFR-11 — ראו שם.
