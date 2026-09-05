# Q5 — UI/UX ודרישות ממשק, סבב r1 (2026-09-06 ~03:00)

Playwright ×2: **170/170** בכל סבב, 0 flaky; 13-accessibility 22/22. 127 צילומי מסך (14 מסכים × 2 viewports × 2 ערכות × 2 שפות) ב-[screens_r1/](screens_r1/). 0 overflow אופקי, 0 מפתחות i18n גולמיים. אימות פריטי REVIEW "תוקן": U2 ✔ U3 ✔ F12 ✔ (209/3/24 תואם DB) U11 חלקי, קיצורים ✔, ולידציית YAML ✔.

| # | חומרה | היכן | תיאור | הוכחה | שורש | סטטוס |
|---|---|---|---|---|---|---|
| Q5-1 | **P1** | /tenders "פתוחים" | מציג את כל 21 (8 closed, 13 unknown, 0 open) = F24 | curl, TendersPage.tsx:16 ללא status | אין ברירת מחדל | פתוח — A10 בביצוע |
| Q5-2 | P2 | /investigations/:id לוג | outcome גולמי באנגלית בשורות הלוג ("partial") | צילום | normalize.ts:296 ללא תרגום | פתוח — UX1 |
| Q5-3 | P2 | פיד, קיצור I | POST investigate בלי משוב; לחיצה כפולה = 2 ג'ובים; job 78 נכשל בשקט; חקירה קיימת לא נבדקה | network + DB | אין debounce/toast/בדיקת קיים | פתוח — UX1 (+ API 409 כמו run-now) |
| Q5-4 | P2 | כותרות דו-לשוניות בדוח | `)` נבלע ל-bdi ltr בעוד `(` נשאר RTL; slug גולמי naval_eo_ir | innerHTML | docx_builder split_runs א-סימטרי; מיפוי תוויות | פתוח — UX1 (split_runs), C2 (slug) |
| Q5-5 | P3 | ציר זמן הבוקר | שלב `post_tenders_catchup` בלי תווית עברית | read_page | STAGE_LABEL_HE | פתוח — UX1 |
| Q5-6 | P3 | חלונית חקירה חדשה | שדה ריק → כלום, בלי הודעה | network | ולידציה | פתוח — UX1 |
| Q5-7 | P3 | /entities | "AI" ו-"Amikam Norkin" כ-company | curl | kind NER | פתוח — C1 מטפל בנרמול |
| Q5-8 | ידוע | הבוקר | רקע לבן לדוח המוטבע (F21) — תוקן בקוד ע"י A9, ה-build/restart טרם עלו בזמן הבדיקה | צילום | — | לאימות ב-r2 |
| Q5-9 | הערה | StatusStrip | באנר "מנותק" 2–4 ש' בטעינה קרה: לא מבחין בין "אין snapshot עדיין" ל"מנותק" | Playwright | StatusStrip.tsx:54 | פתוח — UX1 (נמוך) |

מטריצת HMI: FR-13.2 ✔, FR-13.3 רובו (חסר תעבורת רשת/עומק תור), FR-5.6 ✔, FR-10 חלקי, FR-11 ✔, FR-12.7 ✔, U6 ✔ (הבוקר/ישויות/צ'אט/חקירות עדיין עברית-בלבד ב-en), axe ✔.
