# Q2 — אבטחה, סבב r1 (2026-09-06 ~03:00)

שער הזרקת פרומפט: 23/25 מתקפות נתפסו (92%); FP 2/10 בעברית. DATA framing מאומת בכל נתיבי הכלים (deep_search, mcp/registry). SSRF: 14/16 נחסמו. Path traversal בהורדת דוחות חסום. CORS תקין. 0 סודות בלוגים/דוחות/DB. סקירת Codex (read-only, stdin) הושלמה.

| # | חומרה | היכן | תיאור | הוכחה | שורש | סטטוס |
|---|---|---|---|---|---|---|
| Q2-1 | **P1** | docs/MODULES.md:223,704, docs/RUNBOOK.md:550 | סיסמת ה-DB בטקסט גלוי בתיעוד במעקב git. **הערה (Fable):** הסיסמה היא ברירת המחדל המסופקת `change-me-local-only` שמופיעה גם בקוד; ה-DB מקשיב ל-127.0.0.1 בלבד | grep | דוגמאות עם ערך אמיתי | פתוח — placeholder בתיעוד + **סיבוב סיסמה** בסוף סבב התיקונים (דורש restart) |
| Q2-2 | **P1** | fetch/remote.py assert_public_http_url | טווח CGNAT/Tailscale 100.64.0.0/10 עובר (100.70.157.25 מותר) — אין בדיקת `is_global` | הרצה ישירה | חסר `not ip.is_global` | **תוקן 2026-09-06 03:10** |
| Q2-3 | **P1** | llm/providers/api.py Gemini | GEMINI_API_KEY כ-query param + `except Exception: log(str(exc))` → המפתח ידלוף ללוג בכל שגיאת HTTP | קריאת קוד | חוסר עקביות מול Anthropic/OpenAI (headers) | פתוח — S1 |
| Q2-4 | P2 | fetch/remote.py _fetch_local | TOCTOU/DNS rebinding: הבדיקה לפני החיבור, redirect נבדק אחרי; DNS נפתר פעמיים | Codex | אין IP pinning | פתוח — S1 |
| Q2-5 | P2 | heuristics/guard | JSON tool-call spoofing לא מזוהה (2/25 החמצות) | heur 0.0 | אין heuristic ל-JSON tool-call | פתוח — S1 |
| Q2-6 | P2 | guard L1 | 2/10 פסקאות עבריות תמימות > 0.8 (0.964, 0.977) → L2 מיותר / flagged | הרצה | classifier חלש בעברית | פתוח — S1 (סף/L2 חובה בעברית) |
| Q2-7 | P2 | config notify.mirror_to_public, relay.py | כותרות דוחות נשלחות ל-ntfy.sh ציבורי בטופיק משותף לפרויקט אחר | config | פשרה זמנית עד הרשמת הטלפון | פתוח — **החלטת משתמש**: לכבות אחרי הרשמה ל-8091 |
| Q2-8 | P3 | api/routes/llm.py PUT | אין בדיקת X-EOA-Token כמו ב-settings PUT | grep | חוסר עקביות | פתוח — S1 |
| Q2-9 | P3 | api | אין מגבלת גודל בקשה גלובלית; AskRequest.question ללא max_length | קוד | — | פתוח — S1 |
| Q2-10 | P3 | api/app.py:135 | catch-all 500 מחזיר str(exc) | קוד | debug | פתוח — S1 |
| Q2-11 | P3 | llm/providers/cli.py | shutil.which על PATH (PATH hijacking תיאורטי) | Codex | תכנון | פתוח (נמוך) |
| Q2-12 | P3 | agy | פרומפט כארגומנט CLI (נראה ב-Task Manager) | ADR-005 | מגבלת agy | ידוע |
