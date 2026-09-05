// Hebrew labels for `events.kind` (agent/eoa/llm/schemas/analysis.py EventOut.kind),
// used by the entity card's "אירועי עסקים" section (U10).
export const EVENT_KIND_LABEL_HE: Record<string, string> = {
  contract_award: "זכייה בחוזה",
  m_and_a: "מיזוג/רכישה",
  partnership: "שותפות",
  investment: "השקעה",
  launch: "השקה",
  test: "ניסוי",
  deployment: "פריסה",
  regulation: "רגולציה",
  other: "אחר",
};

export function eventKindLabel(kind: string | null | undefined): string {
  if (!kind) return "אחר";
  return EVENT_KIND_LABEL_HE[kind] ?? kind;
}

// Hebrew labels for `entities.kind` (company/program/system/person/org/country).
export const ENTITY_KIND_LABEL_HE: Record<string, string> = {
  company: "חברה",
  program: "תוכנית",
  system: "מערכת",
  person: "אדם",
  org: "סוכנות/ארגון",
  country: "מדינה",
};

export function entityKindLabel(kind: string | null | undefined): string {
  if (!kind) return "—";
  return ENTITY_KIND_LABEL_HE[kind] ?? kind;
}

// Hebrew labels for `graph_edges.label` (eoa.memory.graph.EDGE_LABELS), used to group
// the "קשרים" list.
export const EDGE_LABEL_HE: Record<string, string> = {
  COMPETITOR_OF: "מתחרה של",
  SUPPLIER_OF: "ספק של",
  PARTNER_OF: "שותף של",
  ACQUIRED: "רכש/נרכש",
  INTEGRATES_WITH: "משתלב עם",
  BIDS_AGAINST: "מתמודד מול",
  DERIVED_FROM: "נגזר מ",
};

export function edgeLabelHe(label: string): string {
  return EDGE_LABEL_HE[label] ?? label;
}
