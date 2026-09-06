# סקר פורטלי מכרזים גלובלי (A15, 2026-09-06)

מענה לדרישת המשתמש: *"Check which tender publication sites exist in the US, Europe and elsewhere
and make sure they are integrated into the system."*

כל פורטל להלן נבדק בפועל ב-**2026-09-06** עם בקשת `curl` אחת, מנומסת (User-Agent מזהה, timeout
20 שניות, `GET` בלבד אלא אם צוין אחרת). התוצאה (קוד HTTP, סוג תוכן, האם התקבלה רשימה קריאה-למכונה)
נרשמה כפי שנצפתה, כולל כישלונות. המקורות המשולבים בפועל חיים ב-`config/tenders.yaml`
(נטענים ע"י `eoa.tenders.scan.load_tender_sources`); הרישום המלא של כל מקור, כולל מקורות
שלא שולבו, נמצא שם עם הערות (`notes`) המפנות למסמך הזה.

לוח הבקרה החי (מסך `/tenders`, פאנל **"כיסוי מקורות"**) נגזר מאותו קובץ קונפיגורציה + טבלת
`tenders` בזמן אמת דרך `GET /api/tenders/coverage` (`eoa.api.services.tender_source_coverage`) —
המסמך הזה הוא התיעוד הסטטי/נרטיבי; הפאנל הוא התצוגה החיה.

## סיכום כמותי (41 מקורות רשומים ב-`config/tenders.yaml`)

| קטגוריה | ספירה | הגדרה |
|---|---|---|
| **משולב, ללא מפתח (ישיר)** | 8 | `kind: api_json`/`rss`, `verified: true` — נסרק ישירות בכל ריצה |
| **חיפוש בלבד (SearXNG)** | 22 | `kind: search` — כיסוי עקיף דרך מטא-חיפוש, לא תלוי בהגנת הבוט של האתר עצמו |
| **ממתין למפתח API** | 2 | `verified: false` + `needs_key_env_var` מוגדר |
| **לא משולב (סיבה מתועדת)** | 9 | חסום/JS-shell/נקודת קצה שגויה/מחוץ לטווח במכוון |
| **סה"כ** | **41** | |

8 המקורות הישירים החדשים/הקיימים: `ted_eu`, `ted_eu_cpv` (חדש), `uk_contracts_finder`,
`uk_find_tender` (חדש), `fr_boamp` (חדש), `nl_tenderned` (חדש), `es_placsp_atom` (חדש),
`us_grants_gov` (חדש). כלומר **6 אינטגרציות ישירות חדשות** נוספו היום, פרסרים גנריים
(`_parse_generic_ocds`, `_parse_generic_json_list`) נוספו כדי שלא יידרש קוד ייעודי לכל מקור,
ופרסר קיים (`_parse_ted_notices`) הורחב לתמוך גם ברוטציית קודי CPV.

---

## ארה"ב (United States)

| פורטל | URL | רלוונטיות ביטחונית | סוג גישה | מפתח נדרש? | הגבלות קצב | תוצאת בדיקה חיה (2026-09-06) | סטטוס שילוב |
|---|---|---|---|---|---|---|---|
| **SAM.gov** — Opportunities API v2 | api.sam.gov/opportunities/v2/search | גבוהה — הפורטל הפדרלי המרכזי לכל מכרזי DoD | JSON API | **כן** (מפתח אישי; `DEMO_KEY` הציבורי הוצא משימוש) | לפי מפתח | `curl` עם `api_key=DEMO_KEY` → **HTTP 404** (הנתיב הישן הוצא משימוש) | `sam_gov_api`, `verified:false`, `needs_key_env_var: SAM_GOV_API_KEY` |
| **SAM.gov** — ממשק חיפוש ציבורי | sam.gov/search/?index=opp | גבוהה | HTML (SPA) | לא (לצפייה) | — | **HTTP 200**, HTML נטען אך מקושט לקוח (React) — אין רשימה סטטית | לא משולב ישירות; מכוסה ע"י `sam_gov_search` |
| **SAM.gov** — תמציות יומיות (data extracts) | sam.gov/data-services | גבוהה | קובצי CSV/JSON יומיים | לא | יומי | לא נבדק ישירות (מחוץ לטווח `since_days=3`) | לא משולב; ראו הערת `sam_gov_api` בקובץ הקונפיג |
| **GSA eBuy** | ebuy.gsa.gov | בינונית — רכש כללי, כולל אלקטרוניקה | HTML, מאחורי login | לא (לצפייה חלקית) | — | **HTTP 302** → הפניה ל-gsaadvantage.gov (פורטל קונים מחובר) | לא משולב; מכוסה חלקית ע"י `sam_gov_search`/`us_defense_innovation_search` |
| **DLA DIBBS** | dibbs.bsm.dla.mil | גבוהה — לוגיסטיקה ביטחונית | HTML | לא | — | **HTTP 302** → מסך אזהרת DoD (`dodwarning.aspx`), אין API ציבורי | לא משולב |
| **SBIR.gov** — API פתרונות | api.www.sbir.gov/public/api/solicitations | בינונית-גבוהה — מחקר ופיתוח כולל EO/IR | JSON API, מתועד כללי-מפתח | לא (לפי התיעוד) | לא ידוע | **HTTP 403** `{"message":"Forbidden"}` — גם עם User-Agent דפדפן מלא + Referer | `us_sbir_gov`, `verified:false` — חסימת WAF, לא בעיית מפתח |
| **Grants.gov** — search2 API | api.grants.gov/v1/api/search2 | בינונית — כולל BAA/RFI של מעבדות DoD | JSON API (POST) | **לא** | לא צוין | **HTTP 200**, JSON אמיתי, 4 תוצאות רלוונטיות אמיתיות (NRL, AFOSR, DEVCOM ARL) | ✅ **`us_grants_gov`, `verified:true`** |
| **DIU** — Commercial Solutions Openings | diu.mil/work-with-us/opportunities | גבוהה — יחידת חדשנות ביטחונית | HTML (SPA גדול) | לא | — | **HTTP 404** בנתיב הספציפי; האתר הוא Nuxt SPA (3MB) | לא משולב; מכוסה ע"י `us_defense_innovation_search` |
| **AFWERX / SOFWERX** | afwerx.com, sofwerx.org | גבוהה — חדשנות חיל האוויר | HTML | לא | — | AFWERX: **HTTP 200** (HTML שיווקי, ללא feed); SOFWERX: **HTTP 308** (הפניה) | לא משולב ישירות; מכוסה ע"י `us_defense_innovation_search` |
| **USAspending.gov** | api.usaspending.gov/api/v2/search/spending_by_award/ | בינונית — מודיעין תחרותי (חוזים **שכבר נחתמו**, לא מכרזים פתוחים) | JSON API (POST) | לא | לא ידוע | **HTTP 422** מובנה (`filters\|award_type_codes required`) — מאשר API חי | `us_usaspending`, `verified:false` **במכוון** — מחוץ לטווח (award, לא tender) |

## אירופה (Europe)

| פורטל | URL | רלוונטיות ביטחונית | סוג גישה | מפתח נדרש? | הגבלות קצב | תוצאת בדיקה חיה (2026-09-06) | סטטוס שילוב |
|---|---|---|---|---|---|---|---|
| **TED** (Tenders Electronic Daily) — EU | api.ted.europa.eu/v3/notices/search | גבוהה — כל מכרזי הביטחון/בטיחות של האיחוד | JSON API (OCDS-adjacent) | לא | לא ידוע | **HTTP 200**, כבר משולב; **הורחב** לרוטציית קודי CPV (`classification-cpv=38620000`/`35120000`/`35125000`) — אושר חי | ✅ `ted_eu` (קיים) + ✅ `ted_eu_cpv` (חדש) |
| **UK Find a Tender Service (FTS)** | find-tender.service.gov.uk/api/1.0/ocdsReleasePackages | גבוהה — מכרזי ה-MOD הבריטי מעל סף | JSON API — **OCDS 1.1** | **לא** | לא צוין | **HTTP 200**, OCDS תקני עם `buyer`/`tender.title`/`tenderPeriod`; אין פרמטר חיפוש מילות-מפתח פועל (`q=`/`keywords=` מתעלמים) | ✅ **`uk_find_tender`, `verified:true`** |
| **UK Contracts Finder** | contractsfinder.service.gov.uk | גבוהה — מתחת לסף (כולל MOD) | JSON API (OCDS) | לא | לא ידוע | כבר משולב | ✅ `uk_contracts_finder` (קיים) |
| **Germany** — bund.de / service.bund.de | service.bund.de/…/Ausschreibungen | גבוהה — MoD גרמני | RSS/טופס חיפוש | לא | — | **timeout** (curl exit 000) בטופס החיפוש | לא משולב; `de_search` (SearXNG) |
| **Germany** — evergabe-online.de | evergabe-online.de | בינונית | HTML | לא | — | **HTTP 302**, גוף ריק | לא משולב; `de_search` |
| **France BOAMP** (open data) | boamp-datadila.opendatasoft.com/api/records/1.0/search/ | גבוהה — כולל MINARM (משרד ההגנה הצרפתי) | **JSON API פתוח** (OpenDataSoft) | **לא** | לא צוין | **HTTP 200**, 22 תוצאות אמיתיות ל-"electro-optique", כולל מכרז MINARM/PFC BREST אמיתי לחיישני אלקטרו-אופטי/אינפרה-אדום לפיקוח ימי | ✅ **`fr_boamp`, `verified:true`** |
| **France PLACE** | marches-publics.gouv.fr | גבוהה | HTML, מאחורי הרשמה | לא | — | **HTTP 302** | לא משולב; מכוסה חלקית ע"י תוכן BOAMP (מפנה ל-PLACE להורדת מסמכים) |
| **Netherlands TenderNed** | tenderned.nl/papi/tenderned-rs-tns/v2/publicaties | בינונית | **JSON API פתוח** | **לא** | לא צוין | **HTTP 200**, JSON אמיתי (`aanbestedingNaam`/`opdrachtgeverNaam`/`sluitingsDatum`); אין פרמטר חיפוש פועל (נבדקו `zoekterm`/`trefwoord`/`q`/`aanbestedingNaam` — כולם התעלמו) | ✅ **`nl_tenderned`, `verified:true`** |
| **Italy** — Consip / MePA / AcquistInRetePA | consip.it, acquistinretepa.it | בינונית | HTML | לא | — | Consip: **HTTP 200** (שיווקי, ללא feed); AcquistInRetePA: **HTTP 302** (login) | לא משולב; `it_search` |
| **Spain PLACSP** | contrataciondelestado.es/sindicacion/…licitacionesPerfilesContratanteCompleto3.atom | בינונית-גבוהה | **Atom feed פתוח** | **לא** | ~2MB לכל הפלטפורמה | **HTTP 200**, `Content-Type: application/atom+xml`, מנותח בהצלחה (66 רשומות בדוגמה חלקית) | ✅ **`es_placsp_atom`, `verified:true`** |
| **Norway Doffin** | doffin.no, api.doffin.no/public/v2/search | בינונית | JSON API | **כן** (Ocp-Apim-Subscription-Key) | לא ידוע | **HTTP 401** `Access denied due to missing subscription key` | `no_doffin_api`, `verified:false`, `needs_key_env_var: DOFFIN_SUBSCRIPTION_KEY`; `no_search` (SearXNG) |
| **Finland Hilma** | hankintailmoitukset.fi | בינונית | HTML/RSS (מתועד) | לא | — | דף הבית וגם נתיב `/en/notice/rss` המתועד החזירו את אותו HTML client-hydrated (ללא feed בפועל) | לא משולב; `fi_search` |
| **Denmark Udbud.dk** | udbud.dk | בינונית | HTML | לא | — | **HTTP 302**, גוף ריק | לא משולב; `dk_search` |
| **Sweden TendSign** | tendsign.com | בינונית | HTML, מסחרי (Mercell/Opic) | לא (לצפייה) | — | **HTTP 503** "service not available" | לא משולב; אין SearXNG ייעודי (מכוסה חלקית ע"י `no_search`/`eda_search`) |
| **Poland** — eZamówienia / BZP | ezamowienia.gov.pl | בינונית | JSON API (מתועד) | לא (משוער) | לא ידוע | נתיב ה-API שנוסה החזיר **HTTP 200** אך `Content-Type: text/html` (מעטפת Angular, לא JSON) | `pl_ezamowienia_api`, `verified:false` — נתיב שגוי, לא בעיית מפתח; `pl_search` |
| **NATO NSPA** | nspa.nato.int | גבוהה — רכש נאט"ו | HTML | לא | — | **HTTP 403** Cloudflare "Just a moment…" | `nato_nspa`, `verified:false` (קיים); `nato_search` |
| **NATO NCIA** — Business | ncia.nato.int/business | גבוהה | HTML | לא | — | **HTTP 302** → 404 | `nato_ncia`, `verified:false` (קיים); `nato_search` |
| **EDA** (European Defence Agency) | eda.europa.eu/procurement-gateway | גבוהה | HTML | לא | — | **HTTP 404** — הנתיב הישן הוסר/שונה | לא משולב; `eda_search` |
| **EU Funding & Tenders Portal** — SEDIA search API | api.tech.ec.europa.eu/search-api/prod/rest/search | בינונית — כולל קולות קול EDF (European Defence Fund) | **JSON API פתוח** | **לא** (`apiKey=SEDIA` הוא מפתח ציבורי מתועד) | לא צוין | **HTTP 200**, JSON אמיתי (21,723 תוצאות ל-"electro-optical") | `eu_sedia_funding_tenders`, `verified:false` **במכוון** — אינדקס חיפוש כללי המערבב פרופילי ארגונים/נושאים/קולות קול באותה תשובה (הפגיעה הראשונה בדוגמה החיה הייתה עמוד פרופיל חברה, לא קול קול); דורש פילטר `type=call` וניקוי תגי `<b>` לפני שילוב אמיתי |

## ישראל (Israel)

| פורטל | URL | רלוונטיות ביטחונית | סוג גישה | מפתח נדרש? | הגבלות קצב | תוצאת בדיקה חיה (2026-09-06) | סטטוס שילוב |
|---|---|---|---|---|---|---|---|
| **מנו"ף** (מנהל רכש ממשלתי) | mr.gov.il | גבוהה — מכרזי ממשלה, כולל ביטחון | HTML | לא | — | **HTTP 307** (הפניה), גוף ריק | לא משולב ישירות; מכוסה חלקית ע"י `il_mod_search`/`rfi_rfp_news_he` |
| **gov.il** — פרסומים/מכרזים | gov.il/he/departments/publications | בינונית | HTML | לא | — | **HTTP 301** (Cloudflare) | לא משולב |
| **משרד הביטחון** — mod.gov.il | mod.gov.il | גבוהה | HTML (Next.js SPA) | לא | — | **HTTP 200** אך מעטפת React ללא תוכן מכרזים סטטי | `il_mod`, `verified:false` (קיים); מכוסה ע"י `il_mod_search` |
| **משרד הביטחון** — online.mod.gov.il | online.mod.gov.il | גבוהה | HTML | לא | — | **כשל DNS מוחלט** (timeout/000) | `il_mod`, `verified:false` (קיים) |
| **המשרד לביטחון הפנים / משטרת ישראל** | gov.il/he/departments/ministry_of_public_security | בינונית | HTML | לא | — | **HTTP 200**, HTML קטן (3KB) ללא רשימת מכרזים ישירה | לא משולב; מכוסה ע"י `il_mod_search`/`rfi_rfp_news_he` (חיפוש עברי כללי) |

**הערה:** הכיסוי הישראלי בפועל הוא באמצעות `il_mod_search` ו-`rfi_rfp_news_he` (SearXNG בעברית,
מוגבל לדומיינים/שאילתות רלוונטיות) — שני המקורות כבר `verified:true` וקיימים מלפני A15; לא נמצא
כאן אף פורטל ישראלי חדש עם API/feed קריא-למכונה שלא היה כבר מתועד.

## אחר / עולם (Others)

| פורטל | URL | רלוונטיות ביטחונית | סוג גישה | מפתח נדרש? | הגבלות קצב | תוצאת בדיקה חיה (2026-09-06) | סטטוס שילוב |
|---|---|---|---|---|---|---|---|
| **CanadaBuys** | canadabuys.canada.ca | בינונית-גבוהה | HTML (Drupal) + **CSV פתוח יומי** | לא | ~7MB/יום ל-CSV | HTML: **HTTP 200** (רשימה מוצגת בצד לקוח); CSV: **HTTP 206** (partial content, נגיש) | `canada_buys`, `verified:true` אך **לא נסרק ישירות** (`kind:html` תמיד מדולג) — ה-CSV מחוץ לטווח `since_days=3`; כיסוי בפועל דרך `canada_buys_search` |
| **AusTender** | tenders.gov.au/atm/rss | בינונית-גבוהה | RSS (מתועד) | לא | — | **HTTP 403** Akamai "Request blocked" | `austender`, `verified:false` (קיים); `austender_search` |
| **New Zealand GETS** | gets.govt.nz | בינונית | HTML (ASP קלאסי) | לא | — | **HTTP 200**, פורטל ASP ישן ללא API ציבורי | לא משולב; `nz_search` |
| **India GeM** | gem.gov.in | בינונית | HTML/API (בהרשמה) | לא ידוע | — | **כשל חיבור מוחלט** (timeout/000) | לא משולב; `in_search` |
| **India MoD** — RFI page | mod.gov.in/dod/acquisition-wing | גבוהה | HTML | לא | — | **כשל חיבור מוחלט** (timeout/000) | לא משולב; `in_search` |
| **Korea KONEPS** | g2b.go.kr | בינונית | HTML | לא | — | **HTTP 302**, גוף ריק | לא משולב; `kr_search` |
| **Korea D2B** (רכש ביטחוני) | d2b.go.kr | גבוהה | HTML | לא | — | **HTTP 200**, הפניית JS גרידא (`location.href`) | לא משולב; `kr_search` |
| **Japan ATLA/MoD** | mod.go.jp/atla/procurement | גבוהה | HTML | לא | — | **HTTP 404** (הנתיב שנוסה שגוי/הוסר) | לא משולב; `jp_search` |
| **Singapore GeBIZ** | gebiz.gov.sg | בינונית | HTML (XHTML legacy) | לא (לצפייה) | — | **HTTP 200**, מעטפת XHTML ישנה ללא API ציבורי | לא משולב; `sg_search` |
| **UAE/Saudi Etimad** | etimad.sa | בינונית | HTML | לא | — | **כשל חיבור מוחלט** (timeout/000) | לא משולב; `gcc_search` |
| **UN UNGM** (Global Marketplace) | ungm.org/Public/Notice | בינונית — כולל רכש הומניטרי/ביטחוני של גופי האו"ם | HTML | לא (לצפייה; פרסום דורש הרשמה) | — | **HTTP 200**, HTML אמיתי אך הרשימה נטענת בצד לקוח לאחר סשן | לא משולב; `ungm_search` |

---

## מסקנות והמלצות המשך

1. **6 אינטגרציות ישירות, ללא מפתח, נבדקו ואומתו היום**: BOAMP (צרפת — כולל MINARM), UK Find a
   Tender Service (OCDS), TenderNed (הולנד), PLACSP (ספרד, Atom), Grants.gov (ארה"ב), ו-TED
   בהרחבת קודי CPV. שתי פונקציות פרסור **גנריות חדשות** (`_parse_generic_ocds`,
   `_parse_generic_json_list`) ב-`eoa/tenders/scan.py` הופכות אינטגרציה עתידית של פורטל OCDS/JSON
   קריא-למכונה נוסף לשינוי קונפיגורציה בלבד (`parse_hints`), ללא קוד פייתון ייעודי.
2. **2 פורטלים ממתינים למפתח בפועל**: SAM.gov (`SAM_GOV_API_KEY`) ו-Doffin הנורווגי
   (`DOFFIN_SUBSCRIPTION_KEY`) — שניהם נבדקו חיים והחזירו שגיאת הרשאה מפורשת (404/401), לא כשל
   כללי; שדה חדש `TenderSource.needs_key_env_var` מייצג זאת באופן מובנה (במקום ניתוח טקסט חופשי
   של השדה `notes`).
3. **פורטל אחד נבדק חי, ללא מפתח, אך הוחלט במפורש לא לשלב עדיין**: SEDIA (EU Funding & Tenders) —
   אינדקס חיפוש כללי מערבב פרופילי חברות/נושאים/קולות-קול; דורש עבודת פילטור נוספת לפני שהוא
   "בטוח למכרזים" (ראו הערת `eu_sedia_funding_tenders` בקונפיג).
4. **USAspending** (ארה"ב) נבדק חי וקריא-למכונה, אך הוא **חוזי שכבר נחתמו**, לא מכרזים פתוחים —
   הוחלט במפורש שלא לשלב אותו ל-`tenders` (השער המחמיר ב-`scan.py` דוחה בכל מקרה `notice_type ==
   'award'`); רלוונטי יותר לתכונת מודיעין-תחרותי עתידית.
5. **15 מקורות `kind: search` חדשים** נוספו לכיסוי גיאוגרפי רחב (גרמניה, איטליה, נורווגיה,
   פינלנד, דנמרק, פולין, יפן, קוריאה, הודו, סינגפור, מפרץ פרסי, ניו זילנד, UNGM, EDA/EDF, וחדשנות
   ביטחונית אמריקאית) — כל אלה עוקפים חסימות בוט/JS-shell ספציפיות לאתר דרך מטא-החיפוש
   (SearXNG) הכבר-מאומת, בדיוק כמו הדפוס הקיים (`sam_gov_search`, `il_mod_search` וכו').
6. **לא נמצא אף מקור חדש מפתיע בישראל** מעבר למה שכבר תועד — הכיסוי הישראלי נשען כולו על
   SearXNG ממוקד עברית, מאחר שאף אחד מ-mr.gov.il / mod.gov.il / gov.il לא חושף feed/API סטטי
   קריא-למכונה.

## פורטלים שלא ניתן היה לאמת היום

הרשימה המלאה של HTTP status/כשל לכל פורטל נמצאת בטבלאות למעלה. באופן מרוכז — כשל חיבור מוחלם
(timeout, `curl` exit 000, לרוב חסימת רשת/גיאוגרפיה מהסביבה שבה רץ הבדיקה, לא בהכרח ראיה שהפורטל
עצמו מושבת): `service.bund.de` (גרמניה), `gem.gov.in` ו-`mod.gov.in` (הודו), `etimad.sa`
(סעודיה/איחוד האמירויות), `online.mod.gov.il` (ישראל — כשל DNS). אלה מועמדים טבעיים לבדיקה
חוזרת מסביבת רשת שונה (למשל דרך VPN אזורי) בסבב עתידי.
