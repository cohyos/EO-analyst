import { describe, expect, it } from "vitest";
import {
  commonVocabulary,
  effectiveVocabulary,
  groupVocabulary,
  PERFORMANCE_ROUTED_KEYS,
  SPEC_GROUP_ORDER,
  specParamByKey,
  vocabularyForTable,
} from "./specVocabulary";

// PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §7 item 1): re-asserts the exact key/count
// contract the vocabulary doc's own acceptance checks specify for config/spec_vocabulary.yaml --
// "121 keys (24 common + 19/17/16/15/15/15 per line), zero duplicate keys" -- as a standing
// regression test against this file's hand-synced TS mirror, so a future hand-edit here that
// drifts from the YAML fails a test rather than silently rotting (this file's own header comment
// says the same thing).
const LINE_COUNTS: Record<string, number> = {
  targeting_pods: 19,
  mws_eo: 17,
  lorop_pods: 16,
  eo_air_defense_warning: 15,
  ball_gimbals_16in: 15,
  border_long_range_eo: 15,
};

describe("specVocabulary (PD-vocab-ui)", () => {
  it("common carries exactly 24 parameters", () => {
    expect(commonVocabulary().length).toBe(24);
  });

  it("each product line carries its documented parameter count", () => {
    for (const [line, count] of Object.entries(LINE_COUNTS)) {
      expect(effectiveVocabulary(line).length - commonVocabulary().length).toBe(count);
    }
  });

  it("totals 121 keys across common + all six lines, zero duplicates", () => {
    const allKeys = [
      ...commonVocabulary().map((p) => p.key),
      ...Object.keys(LINE_COUNTS).flatMap((line) => effectiveVocabulary(line).slice(commonVocabulary().length).map((p) => p.key)),
    ];
    expect(allKeys.length).toBe(121);
    expect(new Set(allKeys).size).toBe(121);
  });

  it("effectiveVocabulary falls back to common alone for a null/unknown product line", () => {
    expect(effectiveVocabulary(null)).toEqual(commonVocabulary());
    expect(effectiveVocabulary("not_a_real_line")).toEqual(commonVocabulary());
  });

  it("effectiveVocabulary is common followed by the line's own params, common first", () => {
    const eff = effectiveVocabulary("targeting_pods");
    expect(eff.slice(0, commonVocabulary().length).map((p) => p.key)).toEqual(commonVocabulary().map((p) => p.key));
    expect(eff[commonVocabulary().length].key).toBe("laser_designation_accuracy");
  });

  it("every group_he value is one of the fixed 8 groups, in SPEC_GROUP_ORDER", () => {
    expect(SPEC_GROUP_ORDER).toHaveLength(8);
    for (const p of effectiveVocabulary("targeting_pods")) {
      expect(SPEC_GROUP_ORDER).toContain(p.groupHe);
    }
  });

  it("groupVocabulary orders groups per SPEC_GROUP_ORDER and keeps declaration order within a group", () => {
    const grouped = groupVocabulary(commonVocabulary());
    const seenOrder = grouped.map((g) => g.groupHe);
    const expectedOrder = SPEC_GROUP_ORDER.filter((g) => seenOrder.includes(g));
    expect(seenOrder).toEqual(expectedOrder);
    // field_of_view is declared before optical_aperture within "אופטיקה" in the source YAML.
    const optics = grouped.find((g) => g.groupHe === "אופטיקה")!;
    expect(optics.params[0].key).toBe("field_of_view");
  });

  it("specParamByKey resolves a known key and omits an unknown one", () => {
    const byKey = specParamByKey("targeting_pods");
    expect(byKey.get("weight")?.labelHe).toBe("משקל");
    expect(byKey.get("pod_class_diameter")?.groupHe).toBe("מכניקה וסביבה");
    expect(byKey.has("totally_made_up_key")).toBe(false);
  });

  it("vocabularyForTable splits performance-routed keys from specification keys with no overlap", () => {
    const spec = vocabularyForTable("border_long_range_eo", "specifications");
    const perf = vocabularyForTable("border_long_range_eo", "performance");
    expect(spec.length + perf.length).toBe(effectiveVocabulary("border_long_range_eo").length);
    const specKeys = new Set(spec.map((p) => p.key));
    for (const p of perf) expect(specKeys.has(p.key)).toBe(false);
    // The doc's own worked example: the "20-in-15-in" fact routes to performance, always.
    expect(PERFORMANCE_ROUTED_KEYS.has("size_to_performance_ratio")).toBe(true);
    expect(perf.some((p) => p.key === "dri_at_long_range")).toBe(true);
  });
});
