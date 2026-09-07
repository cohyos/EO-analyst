// R10-graph (docs/qa/loop/round_10_fixes.md): shared color/shape tokens for the entity graph
// explorer. Kept in sync with `web/src/components/entities/EntityGraph.tsx`'s `KIND_COLOR` (not
// imported from there to avoid coupling this new module to that smaller, still-in-use compact
// widget) -- both must agree with the real `entities.kind` values: company/program/system/
// person/org/country (migration 0007 widened the DB CHECK to allow "country").
export const KIND_COLOR: Record<string, string> = {
  company: "#17909f",
  program: "#8a5cf5",
  system: "#4c9f70",
  person: "#d97a3f",
  org: "#c4561b",
  country: "#5b7fa6",
  default: "#8492a6",
};

export const ENTITY_KIND_OPTIONS = [
  "company",
  "program",
  "system",
  "person",
  "org",
  "country",
];

// `eoa.memory.graph.EDGE_LABELS` -- kept in sync manually (no shared codegen between the Python
// and TS sides in this repo, same convention `KIND_OPTIONS` in EntitiesPage.tsx already follows).
export const RELATION_TYPE_OPTIONS = [
  "COMPETITOR_OF",
  "SUPPLIER_OF",
  "PARTNER_OF",
  "ACQUIRED",
  "INTEGRATES_WITH",
  "BIDS_AGAINST",
  "DERIVED_FROM",
];

export function kindColor(kind: string | null | undefined): string {
  return KIND_COLOR[kind ?? ""] ?? KIND_COLOR.default;
}
