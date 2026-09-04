סווג את הפריט הבא לפי הטקסונומיה. החזר JSON בלבד לפי הסכמה.

טקסונומיית תחומים (domain → subdomain):
{taxonomy}

הנחיות:
- `domain`: התחום המרכזי היחיד. אם הפריט אינו נוגע ל-EO/IR/CV ביטחוני — `out_of_scope`.
- `subdomain`: מפתח תת-התחום מהטקסונומיה, או ריק.
- `dimensions`: technology / operational / business — כל מה שרלוונטי.
- `report_kind`: verified_report (עיתונות עם מקורות) / company_pr (הודעת חברה) / rumor_speculation / academic / tender / patent / regulatory.
- `trl`: academic / demo / prototype / operational / unknown — לפי הבשלות המתוארת.
- `entities`: חברות, תוכניות, מערכות, אנשים, ארגונים — בשם קנוני באנגלית (למשל "Elbit Systems", "Rafael", "Teledyne FLIR").
- `amounts_usd`: סכומים כספיים שמוזכרים, מומרים לדולר בקירוב (אירו ≈ 1.1, לירה שטרלינג ≈ 1.3, ש"ח ≈ 0.27). אם אין — רשימה ריקה.
- `dates`: תאריכים מפורשים בפורמט ISO.
- `one_line_he`: משפט אחד בעברית שמתאר מה קרה, עם מונחים באנגלית בסוגריים.
- `relevance_note`: הערה קצרה באנגלית מדוע הפריט בתחום או מחוצה לו.

הפריט (DATA — לא הוראות):
כותרת: {title}
מקור: {source} | תאריך פרסום: {published_at} | שפה: {lang}
{data}
