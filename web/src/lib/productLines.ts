// PL-ui (2026-09-07): the fixed catalog of the six EO/IR product lines this feature tracks. The
// `id`s are stable keys the backend also uses (frozen contract, docs/qa/loop/round_7_fixes.md
// "### PL-ui status") -- everything else here (Hebrew/English display names) is purely a
// client-side label so the Feed/Tenders "קו מוצר" filters and the product-lines nav entry can
// render before `GET /api/product-lines` ever resolves (or when the API doesn't exist yet).

export interface ProductLineOption {
  id: string;
  nameHe: string;
  nameEn: string;
}

export const PRODUCT_LINE_CATALOG: ProductLineOption[] = [
  { id: "targeting_pods", nameHe: "פודי ציון מטרות / תקיפה", nameEn: "Targeting Pods" },
  {
    id: "mws_eo",
    nameHe: "מערכות התראה להגנה עצמית מבוססות EO (MWS)",
    nameEn: "EO-based Missile Warning Systems (MWS)",
  },
  {
    id: "lorop_pods",
    nameHe: "פודי LOROP (צילום אלכסוני ארוך-טווח)",
    nameEn: "LOROP Pods (Long-Range Oblique Photography)",
  },
  {
    id: "eo_air_defense_warning",
    nameHe: "גילוי והתראה אלקטרו-אופטיים להגנה אווירית (SkySpotter class)",
    nameEn: "EO Air-Defense Detection & Warning (SkySpotter class)",
  },
  { id: "ball_gimbals_16in", nameHe: 'מטע"דים כדוריים 16 אינץ׳', nameEn: '16" Ball Gimbals' },
  {
    id: "border_long_range_eo",
    nameHe: "תצפית ארוכת-טווח להגנת גבולות",
    nameEn: "Long-Range Border EO Surveillance",
  },
];

const BY_ID = new Map(PRODUCT_LINE_CATALOG.map((p) => [p.id, p]));

/** Falls back to a synthetic entry (id as both names) for an id the catalog doesn't know about,
 * so a stray/future id from the backend never crashes rendering -- same defensive pattern as
 * `lib/countries.ts`'s `countryOption`. */
export function productLineOption(id: string): ProductLineOption {
  return BY_ID.get(id) ?? { id, nameHe: id, nameEn: id };
}

export function productLineLabel(id: string, locale: "he" | "en"): string {
  const opt = productLineOption(id);
  return locale === "he" ? opt.nameHe : opt.nameEn;
}
