# Q2 — אבטחה, סבב r2 (2026-09-06 ~04:35, HEAD b8f908d)

הזרקת פרומפט: **25/25 זוהו (r1: 92%), 0/10 FP (r1: 20%)**. SSRF: 16/16 נחסמו. mode=local: אין נתיב הדלפה אוטומטי (effective_chain=[ollama], EOA_PIPELINE=1); llm_calls לא שומר טקסט.

| # | סטטוס r2 | תיאור |
|---|---|---|
| Q2-1 | **סגור** | הסיסמה הישנה נכשלת; החדשה 0 מופעים ב-docs/config/logs/output/git |
| Q2-2 | **סגור** | 16/16 כולל CGNAT |
| Q2-3 | **סגור** | x-goog-api-key + redact_secrets, 30/30 טסטים |
| Q2-4 | **סגור ל-_fetch_local** | per-hop validation + IP pinning |
| Q2-5 | **סגור** | tool-call spoofing 0.989–1.0 |
| Q2-6 | **סגור** | עברית L1-only → L2, fail-safe |
| Q2-7 | פתוח (החלטת משתמש) | mirror_to_public עדיין true |
| Q2-8/9/10 | **סגור** | token, 413/422 חי, 500 גנרי עם error_id |
| **Q2-13** | **P1 חדש** | **צינור הקליטה הלילי (fetch/service.py) קורא fetch_page בלי שער SSRF** (3 קריאות: פריט RSS, פיד, רשימת HTML) — URLs מגיעים מתוכן חיצוני | → תוקן ישירות 2026-09-06 04:50 |
| Q2-14 | P2 חדש | mcp_servers/_common.py follow_redirects ללא אימות hop | → S2 |
| Q2-15 | P2 חדש | procurement.py: מפתחות API כ-query param, שגיאות רשת בלי redact ל-mcp_calls.error ולמודל | → S2 |
| Q2-16 | P3 חדש | ping_mcp_server לא מכבד kill switch גלובלי | → S2 |
| Q2-11/12 | ידוע | PATH / agy argv |
