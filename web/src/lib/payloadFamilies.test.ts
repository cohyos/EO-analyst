import { describe, expect, it } from "vitest";
import type { PayloadRecord } from "@/types/api";
import {
  buildPayloadTree,
  defaultExpandedKeys,
  expandedKeysForSearch,
  familyNodeKey,
  filterPayloadTree,
  variantNodeKey,
  vendorNodeKey,
} from "./payloadFamilies";

// W19b (docs/REVIEW_2026-09-06_evening.md; user requirement 2026-09-06 21:20, verbatim: "group
// the manufacturers' products by families and allow drill-down, not flooding the operator").
// Client-side mirror of tests/unit/test_payload_families.py's `build_payload_tree` coverage, plus
// the search/expansion helpers that only exist on this side (no DOM/React involved -- pure
// functions over plain data, same convention as `payloadFamilies.ts` itself).

function makePayload(over: Partial<PayloadRecord> = {}): PayloadRecord {
  return {
    id: 1,
    canonical_name: "WESCAM MX-15",
    vendor_entity_name: "L3Harris WESCAM",
    family: "MX",
    variant: "MX-15",
    category: "gimbal",
    first_seen: "2026-08-01",
    last_seen: "2026-09-05",
    notes: null,
    image_url: null,
    spec_url: "https://wescam.com/products/mx-series/",
    spec_source: "wescam.com",
    spec_version_count: 2,
    price_ref_count: 1,
    latest_spec_date: "2026-09-05",
    latest_price_date: "2026-08-20",
    created_at: "2026-08-01T08:00:00+00:00",
    updated_at: "2026-09-05T08:00:00+00:00",
    ...over,
  };
}

describe("buildPayloadTree", () => {
  it("groups several variants of one family under one vendor", () => {
    const payloads = [
      makePayload({ id: 1, canonical_name: "WESCAM MX-15", family: "MX", variant: "MX-15" }),
      makePayload({
        id: 2,
        canonical_name: "WESCAM MX-20HD",
        family: "MX",
        variant: "MX-20HD",
        spec_version_count: 1,
        price_ref_count: 0,
      }),
      makePayload({
        id: 3,
        canonical_name: "Rafael Toplite",
        vendor_entity_name: "Rafael",
        family: "Toplite",
        variant: "Toplite",
        category: "pod",
        spec_version_count: 0,
        price_ref_count: 0,
        latest_spec_date: null,
        latest_price_date: null,
      }),
    ];
    const tree = buildPayloadTree(payloads);
    expect(tree.vendor_count).toBe(2);
    expect(tree.family_count).toBe(2);
    expect(tree.payload_count).toBe(3);

    const byVendor = new Map(tree.vendors.map((v) => [v.vendor, v]));
    expect([...byVendor.keys()].sort()).toEqual(["L3Harris WESCAM", "Rafael"]);

    const wescam = byVendor.get("L3Harris WESCAM")!;
    expect(wescam.family_count).toBe(1);
    expect(wescam.payload_count).toBe(2);
    const mx = wescam.families[0];
    expect(mx.family).toBe("MX");
    expect(mx.variant_count).toBe(2);
    expect(mx.spec_version_count).toBe(3);
    expect(mx.price_ref_count).toBe(1);
    expect(mx.variants.map((v) => v.canonical_name)).toEqual(["WESCAM MX-15", "WESCAM MX-20HD"]);
  });

  it("falls back to canonical_name when family/variant are unset", () => {
    const tree = buildPayloadTree([
      makePayload({ id: 9, canonical_name: "PVP Thermal Core", vendor_entity_name: "PVP Photonics", family: null, variant: null }),
    ]);
    const vendor = tree.vendors[0];
    expect(vendor.vendor).toBe("PVP Photonics");
    expect(vendor.families[0].family).toBe("PVP Thermal Core");
    expect(vendor.families[0].variants[0].variant).toBe("PVP Thermal Core");
  });

  it("returns an empty, zeroed tree for no payloads", () => {
    expect(buildPayloadTree([])).toEqual({ vendors: [], vendor_count: 0, family_count: 0, payload_count: 0 });
  });

  it("sorts vendors, families and variants alphabetically", () => {
    const tree = buildPayloadTree([
      makePayload({ id: 1, canonical_name: "Zed Z1", vendor_entity_name: "Zed", family: "Z", variant: "Z1" }),
      makePayload({ id: 2, canonical_name: "Aaa A1", vendor_entity_name: "Aaa", family: "A", variant: "A1" }),
    ]);
    expect(tree.vendors.map((v) => v.vendor)).toEqual(["Aaa", "Zed"]);
  });
});

describe("filterPayloadTree", () => {
  const tree = buildPayloadTree([
    makePayload({ id: 1, canonical_name: "WESCAM MX-15", vendor_entity_name: "L3Harris WESCAM", family: "MX", variant: "MX-15", category: "gimbal" }),
    makePayload({ id: 2, canonical_name: "Rafael Toplite", vendor_entity_name: "Rafael", family: "Toplite", variant: "Toplite", category: "pod" }),
  ]);

  it("passes everything through when query and category are blank", () => {
    const result = filterPayloadTree(tree, "", "");
    expect(result.payload_count).toBe(2);
  });

  it("matches on the variant's own canonical name, dropping non-matching branches entirely", () => {
    const result = filterPayloadTree(tree, "Toplite", "");
    expect(result.vendor_count).toBe(1);
    expect(result.vendors[0].vendor).toBe("Rafael");
  });

  it("matches on a vendor or family name, keeping every variant under it", () => {
    const result = filterPayloadTree(tree, "wescam", "");
    expect(result.vendor_count).toBe(1);
    expect(result.vendors[0].families[0].variants).toHaveLength(1);
  });

  it("filters by category, dropping a family/vendor left with zero variants", () => {
    const result = filterPayloadTree(tree, "", "pod");
    expect(result.vendor_count).toBe(1);
    expect(result.vendors[0].vendor).toBe("Rafael");
  });

  it("is case-insensitive", () => {
    expect(filterPayloadTree(tree, "TOPLITE", "").vendor_count).toBe(1);
  });
});

describe("expandedKeysForSearch / defaultExpandedKeys", () => {
  it("expandedKeysForSearch is empty for a blank query", () => {
    const tree = buildPayloadTree([makePayload()]);
    expect(expandedKeysForSearch(tree, "").size).toBe(0);
    expect(expandedKeysForSearch(tree, "   ").size).toBe(0);
  });

  it("expandedKeysForSearch expands every remaining vendor/family for a non-blank query", () => {
    const tree = buildPayloadTree([
      makePayload({ id: 1, vendor_entity_name: "L3Harris WESCAM", family: "MX", variant: "MX-15" }),
    ]);
    const keys = expandedKeysForSearch(tree, "mx");
    expect(keys.has(vendorNodeKey("L3Harris WESCAM"))).toBe(true);
    expect(keys.has(familyNodeKey("L3Harris WESCAM", "MX"))).toBe(true);
  });

  it("defaultExpandedKeys expands everything under the threshold", () => {
    const tree = buildPayloadTree([makePayload()]);
    const keys = defaultExpandedKeys(tree, 8);
    expect(keys.has(vendorNodeKey("L3Harris WESCAM"))).toBe(true);
    expect(keys.has(familyNodeKey("L3Harris WESCAM", "MX"))).toBe(true);
  });

  it("defaultExpandedKeys collapses everything once the catalogue is larger than the threshold", () => {
    const payloads = Array.from({ length: 10 }, (_, i) =>
      makePayload({ id: i + 1, canonical_name: `Vendor${i} Model${i}`, vendor_entity_name: `Vendor${i}`, family: `M${i}`, variant: `Model${i}` }),
    );
    const tree = buildPayloadTree(payloads);
    expect(defaultExpandedKeys(tree, 8).size).toBe(0);
  });
});

describe("node key helpers", () => {
  it("produce stable, distinct keys per vendor/family/variant", () => {
    expect(vendorNodeKey("L3Harris WESCAM")).toBe(vendorNodeKey("L3Harris WESCAM"));
    expect(familyNodeKey("L3Harris WESCAM", "MX")).not.toBe(vendorNodeKey("L3Harris WESCAM"));
    expect(variantNodeKey(1)).not.toBe(variantNodeKey(2));
  });
});
