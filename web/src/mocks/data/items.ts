import type { ItemCard, TriageLevel } from "@/types/api";

// Deterministic PRNG (mulberry32) so mock data — and any test asserting on
// it — is stable across runs.
function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(20260904);

const SOURCES = [
  "Janes Defence Weekly",
  "Defense News",
  "Shephard Media",
  "Globes",
  "Calcalist",
  "TASS",
  "Yonhap",
  "European Defence Review",
  "GovTribe (RFP)",
  "USPTO Patents",
  "Company PR",
  "LinkedIn — הודעת חברה",
];

const DOMAINS: Array<{ domain: string; sub: string; label: string }> = [
  { domain: "airborne_pods", sub: "targeting_pods", label: "פודי כיוון" },
  { domain: "airborne_pods", sub: "isr_pods", label: "פודי ISR" },
  { domain: "airborne_pods", sub: "uav_gimbals", label: "גימבלים לכטב\"מים" },
  { domain: "airborne_pods", sub: "eo_warfare", label: "לוחמה אלקטרו-אופטית" },
  { domain: "land_surveillance", sub: "border_towers", label: "תצפית גבולות" },
  { domain: "land_surveillance", sub: "vehicle_sights", label: "מטע\"די רק\"ם" },
  { domain: "land_surveillance", sub: "night_vision", label: "ראיית לילה" },
  { domain: "naval_surveillance", sub: "naval_directors", label: "EO Directors ימיים" },
  { domain: "naval_surveillance", sub: "optronic_masts", label: "תורנים אופטרוניים" },
  { domain: "air_defense", sub: "iir_seekers", label: "ראשי ביות IIR" },
  { domain: "air_defense", sub: "hel", label: "לייזר בעוצמה גבוהה" },
  { domain: "c_uas", sub: "detect_track", label: "גילוי-סיווג-עקיבה" },
  { domain: "c_uas", sub: "effectors", label: "אפקטורים נגד כטב\"ם" },
  { domain: "computer_vision", sub: "atr", label: "זיהוי מטרות אוטומטי" },
  { domain: "computer_vision", sub: "edge_ai", label: "Edge AI" },
  { domain: "secondary", sub: "detectors_fpa", label: "גלאים ומישורי מוקד" },
];

const ENTITY_NAMES = [
  "Elbit Systems",
  "Rafael Advanced Defense Systems",
  "Leonardo DRS",
  "HENSOLDT",
  "Teledyne FLIR",
  "Safran Electronics & Defense",
  "Anduril Industries",
  "Aselsan",
  "IAI (Israel Aerospace Industries)",
  "משרד הביטחון — מפא\"ת",
  "European Defence Fund",
  "SPARK Vision Labs",
];

const REPORT_KINDS = [
  "verified_report",
  "company_pr",
  "rumor_speculation",
  "tender",
  "patent",
  "regulatory",
];

const GEOS = ["US", "EU", "UK", "IL", "TR", "KR", "other"];

const TITLE_TEMPLATES: Array<(e: string, d: string) => string> = [
  (e, d) => `${e} חושפת דור חדש של ${d}`,
  (e, d) => `${e} זכתה במכרז לאספקת ${d}`,
  (e, d) => `פטנט חדש של ${e} בתחום ${d}`,
  (e, d) => `${e} ו${"שותפה"} חותמות הסכם פיתוח משותף ל-${d}`,
  (e, d) => `דיווח: ${e} מרחיבה קו ייצור של ${d}`,
  (e, d) => `${e} מציגה יכולות ${d} בתערוכה בינלאומית`,
  (e, d) => `בחינת ביצועים: ${d} של ${e} בניסוי שדה`,
  (e, d) => `${e} מגייסת הון להאצת פיתוח ${d}`,
  (e, d) => `רכישה: ${e} רוכשת סטארטאפ בתחום ${d}`,
  (e, d) => `עדכון רגולציה משפיע על ייצוא ${d} מטעם ${e}`,
];

function pick<T>(arr: T[], i: number): T {
  return arr[i % arr.length];
}

function levelForScore(score: number): TriageLevel {
  if (score >= 85) return "red";
  if (score >= 65) return "orange";
  if (score >= 40) return "yellow";
  return "archive";
}

function buildItem(i: number): ItemCard {
  const domainInfo = pick(DOMAINS, i * 3 + 1);
  const entity = pick(ENTITY_NAMES, i * 5 + 2);
  const source = pick(SOURCES, i * 2 + 1);
  const titleFn = pick(TITLE_TEMPLATES, i * 7 + 3);
  const title = titleFn(entity, domainInfo.label);
  const score = Math.round(20 + rand() * 80);
  const level = levelForScore(score);
  const daysAgo = rand() * 9;
  const publishedAt = new Date(Date.now() - daysAgo * 86400000 - i * 3600000);
  const reportKind = pick(REPORT_KINDS, i * 11 + 4);
  const geography = pick(GEOS, i * 13 + 5);
  const trl = pick(["academic", "demo", "prototype", "operational"], i * 17 + 6);
  const secondaryEntity = pick(
    ENTITY_NAMES.filter((n) => n !== entity),
    i * 19 + 7,
  );

  return {
    id: i + 1,
    title,
    url: `https://example-source.test/articles/${2000 + i}`,
    source_name: source,
    published_at: publishedAt.toISOString(),
    lang: pick(["he", "en", "en", "en"], i),
    domain: domainInfo.domain,
    subdomain: domainInfo.sub,
    report_kind: reportKind,
    trl,
    geography,
    score,
    level,
    triage_reason:
      level === "red"
        ? `ציון גבוה (${score}) — מתאם ישיר עם רשימת מעקב, מקור אמין ואירוע מסוג ${reportKind}, רלוונטיות טכנולוגית ותפעולית גבוהה לתחום ${domainInfo.label}.`
        : level === "orange"
          ? `ציון בינוני-גבוה (${score}) — התאמה חלקית לרשימת מעקב, מקור מוסמך, נדרשת מעקב.`
          : level === "yellow"
            ? `ציון רקע (${score}) — רלוונטיות עקיפה, מיועד לסקירה שבועית.`
            : `ציון נמוך (${score}) — לא עומד בסף רלוונטיות; מיועד לארכיון.`,
    summary_he: `${entity} דיווחה על התפתחות בתחום ${domainInfo.label}, הכוללת שיפור ביצועים וממשק עם ${secondaryEntity}. הפרסום מגיע מ${source} ומתייחס להיבטים טכנולוגיים ותפעוליים.`,
    so_what_he: `בהינתן הפעילות של ${entity} בתחום ${domainInfo.label}, מומלץ לעקוב אחר השפעה על מתחרים ותוכניות רכש רלוונטיות בטווח הקצר.`,
    entities_mentioned: [entity, secondaryEntity],
    tags:
      i % 3 === 0
        ? ["contract_award", "sensor"]
        : i % 3 === 1
          ? ["maturity_trl", "conops"]
          : ["patent", "algorithm"],
    security_status: i % 17 === 0 ? "flagged" : i % 11 === 0 ? "quarantined" : "clean",
    dedup_of: i % 23 === 0 && i > 0 ? i - 1 : null,
    key_facts: [
      `${entity} מדווחת על ${domainInfo.label} עם ציון TRL "${trl}"`,
      `גיאוגרפיה: ${geography}; סוג פרסום: ${reportKind}`,
      `מקור: ${source}, פורסם ${publishedAt.toLocaleDateString("he-IL")}`,
    ],
    uncertainty_he: i % 5 === 0 ? "המקור לא מציין את היקף ההתקשרות הכספי." : null,
  };
}

export const mockItems: ItemCard[] = Array.from({ length: 40 }, (_, i) => buildItem(i));

export function findMockItem(id: number): ItemCard | undefined {
  return mockItems.find((it) => it.id === id);
}
