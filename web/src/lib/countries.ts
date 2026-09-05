// Country/region catalog for the Feed's country filter + "מפת מדינות" panel
// (U7b/U7c). Mirrors the normalization vocabulary in
// `agent/eoa/report/geography.py` (`_ALIASES`) -- codes here are exactly the
// ISO-2/region codes that backend normalization produces, so a code coming
// back from `GET /api/items/by-country` always resolves to a display entry
// here (falling back to `OTHER_COUNTRY` for anything unrecognized).

export interface CountryOption {
  code: string;
  flag: string;
  nameHe: string;
  nameEn: string;
}

export const OTHER_COUNTRY: CountryOption = {
  code: "other",
  flag: "🌐",
  nameHe: "אחר / לא ידוע",
  nameEn: "Other / unknown",
};

// Only the codes actually reachable from `eoa.report.geography`'s alias
// table are listed with a flag; anything else falls back to `OTHER_COUNTRY`.
export const COUNTRY_CATALOG: CountryOption[] = [
  { code: "US", flag: "🇺🇸", nameHe: "ארה\"ב", nameEn: "United States" },
  { code: "IL", flag: "🇮🇱", nameHe: "ישראל", nameEn: "Israel" },
  { code: "GB", flag: "🇬🇧", nameHe: "בריטניה", nameEn: "United Kingdom" },
  { code: "EU", flag: "🇪🇺", nameHe: "האיחוד האירופי", nameEn: "European Union" },
  { code: "NATO", flag: "🛡️", nameHe: "נאט\"ו", nameEn: "NATO" },
  { code: "UN", flag: "🇺🇳", nameHe: "האו\"ם", nameEn: "United Nations" },
  { code: "DE", flag: "🇩🇪", nameHe: "גרמניה", nameEn: "Germany" },
  { code: "FR", flag: "🇫🇷", nameHe: "צרפת", nameEn: "France" },
  { code: "IT", flag: "🇮🇹", nameHe: "איטליה", nameEn: "Italy" },
  { code: "TR", flag: "🇹🇷", nameHe: "טורקיה", nameEn: "Turkey" },
  { code: "KR", flag: "🇰🇷", nameHe: "דרום קוריאה", nameEn: "South Korea" },
  { code: "KP", flag: "🇰🇵", nameHe: "צפון קוריאה", nameEn: "North Korea" },
  { code: "JP", flag: "🇯🇵", nameHe: "יפן", nameEn: "Japan" },
  { code: "CN", flag: "🇨🇳", nameHe: "סין", nameEn: "China" },
  { code: "IN", flag: "🇮🇳", nameHe: "הודו", nameEn: "India" },
  { code: "RU", flag: "🇷🇺", nameHe: "רוסיה", nameEn: "Russia" },
  { code: "UA", flag: "🇺🇦", nameHe: "אוקראינה", nameEn: "Ukraine" },
  { code: "PL", flag: "🇵🇱", nameHe: "פולין", nameEn: "Poland" },
  { code: "ES", flag: "🇪🇸", nameHe: "ספרד", nameEn: "Spain" },
  { code: "NL", flag: "🇳🇱", nameHe: "הולנד", nameEn: "Netherlands" },
  { code: "SE", flag: "🇸🇪", nameHe: "שוודיה", nameEn: "Sweden" },
  { code: "NO", flag: "🇳🇴", nameHe: "נורווגיה", nameEn: "Norway" },
  { code: "FI", flag: "🇫🇮", nameHe: "פינלנד", nameEn: "Finland" },
  { code: "CA", flag: "🇨🇦", nameHe: "קנדה", nameEn: "Canada" },
  { code: "AU", flag: "🇦🇺", nameHe: "אוסטרליה", nameEn: "Australia" },
  { code: "SA", flag: "🇸🇦", nameHe: "ערב הסעודית", nameEn: "Saudi Arabia" },
  { code: "AE", flag: "🇦🇪", nameHe: "איחוד האמירויות", nameEn: "UAE" },
  { code: "SG", flag: "🇸🇬", nameHe: "סינגפור", nameEn: "Singapore" },
  { code: "TW", flag: "🇹🇼", nameHe: "טייוואן", nameEn: "Taiwan" },
  { code: "BR", flag: "🇧🇷", nameHe: "ברזיל", nameEn: "Brazil" },
  { code: "GR", flag: "🇬🇷", nameHe: "יוון", nameEn: "Greece" },
];

const BY_CODE = new Map(COUNTRY_CATALOG.map((c) => [c.code, c]));

export function countryOption(code: string | null | undefined): CountryOption {
  if (!code) return OTHER_COUNTRY;
  return BY_CODE.get(code.toUpperCase()) ?? OTHER_COUNTRY;
}

export function countryLabel(code: string | null | undefined, locale: "he" | "en"): string {
  const c = countryOption(code);
  return locale === "he" ? c.nameHe : c.nameEn;
}

// Small mirror of `eoa.report.geography._ALIASES` (Python) for the mock API
// (`src/mocks/mockApi.ts`), which has no backend to normalize `geography`
// values for it — only needs to cover what `src/mocks/data/items.ts`
// actually generates (`GEOS`) plus a couple of common real-world spellings
// so a typed-in country filter behaves the same in mock and real mode.
const NORMALIZE_ALIASES: Record<string, string> = {
  us: "US", usa: "US", "united states": "US",
  uk: "GB", gb: "GB", "united kingdom": "GB",
  il: "IL", israel: "IL",
  eu: "EU",
  tr: "TR", turkey: "TR",
  kr: "KR",
  other: "other",
};

export function normalizeCountryCode(raw: string | null | undefined): string {
  if (!raw) return "other";
  const key = raw.trim().toLowerCase();
  if (key in NORMALIZE_ALIASES) return NORMALIZE_ALIASES[key];
  if (key.length === 2) return key.toUpperCase();
  return "other";
}
