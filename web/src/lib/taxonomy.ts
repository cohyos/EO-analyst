// Mirrors config/taxonomy.yaml domain ids/labels for UI filter options. The
// backend remains the source of truth (config, not code, per
// docs/CONVENTIONS.md rule 6) — this is purely a display-label lookup so
// the feed filters and item chips don't need a YAML loader in the browser.

export const DOMAIN_OPTIONS: Array<{ id: string; label: string }> = [
  { id: "airborne_pods", label: "פודים ומטע\"דים אוויריים" },
  { id: "land_surveillance", label: "מטע\"די תצפית יבשתיים" },
  { id: "naval_surveillance", label: "מטע\"די תצפית ימיים" },
  { id: "air_defense", label: "הגנה אווירית ויירוט" },
  { id: "c_uas", label: "נגד כטב\"מים" },
  { id: "computer_vision", label: "בינה חזותית" },
  { id: "secondary", label: "תחומים משיקים" },
];

export function domainLabel(id: string | null | undefined): string {
  return DOMAIN_OPTIONS.find((d) => d.id === id)?.label ?? id ?? "—";
}
