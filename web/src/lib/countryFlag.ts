// ISO-3166 alpha-2 -> regional-indicator flag emoji. Used for the tenders
// table's "agency/country" column and the forecast cards' buyer_country.

const REGIONAL_INDICATOR_BASE = 0x1f1e6; // 🇦
const WHITE_FLAG = "🏳️";

export function countryFlagEmoji(code: string | null | undefined): string {
  if (!code || code.length !== 2) return WHITE_FLAG;
  const upper = code.toUpperCase();
  const points = [...upper].map((c) => REGIONAL_INDICATOR_BASE + (c.charCodeAt(0) - 65));
  if (points.some((p) => p < REGIONAL_INDICATOR_BASE || p > REGIONAL_INDICATOR_BASE + 25)) {
    return WHITE_FLAG;
  }
  return String.fromCodePoint(...points);
}
