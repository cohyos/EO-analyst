/**
 * W19b (docs/REVIEW_2026-09-06_evening.md; user requirement 2026-09-06 21:20, verbatim: "group
 * the manufacturers' products by families and allow drill-down, not flooding the operator").
 *
 * Pure, DB/network-free grouping + search/expansion helpers for the payloads screen's collapsed
 * vendor -> family -> variant tree (`@/components/payloads/PayloadTree`, driven from
 * `PayloadsPage`). Deliberately independent of React so it is unit-testable with plain vitest
 * (`payloadFamilies.test.ts`) the same way `agent/eoa/payloads/models.py`'s
 * `build_payload_tree`/`parse_family_variant` are unit-tested in
 * `tests/unit/test_payload_families.py` -- this module is the client-side mirror of that file's
 * `build_payload_tree`, built directly from the already-fetched `GET /api/payloads` flat list
 * (`@/types/api`'s `PayloadRecord[]`) rather than a second round trip to
 * `GET /api/payloads/tree`.
 *
 * One deliberate simplification vs. the backend: `eoa.payloads.models.canonical_vendor` merges
 * corporate siblings (Raytheon/Collins Aerospace -> RTX) via `config/watchlist.yaml`, which isn't
 * loaded client-side -- `buildPayloadTree` below groups by each row's raw `vendor_entity_name`
 * as-is instead.
 */
import type {
  PayloadCategory,
  PayloadRecord,
  PayloadTreeFamily,
  PayloadTreeResponse,
  PayloadTreeVariant,
  PayloadTreeVendor,
} from "@/types/api";

const UNKNOWN_LABEL = "—";

function latestOf(dates: Array<string | null>): string | null {
  const present = dates.filter((d): d is string => Boolean(d));
  if (present.length === 0) return null;
  return present.reduce((max, d) => (d > max ? d : max));
}

/** Groups a flat `GET /api/payloads` result into vendor -> family -> variant, with counts and
 * "latest" rollups at each level -- shape-identical to
 * `eoa.payloads.models.build_payload_tree`'s return value. */
export function buildPayloadTree(payloads: PayloadRecord[]): PayloadTreeResponse {
  const vendorMap = new Map<string, Map<string, PayloadTreeVariant[]>>();

  for (const p of payloads) {
    const vendorName = p.vendor_entity_name || UNKNOWN_LABEL;
    const familyName = p.family || p.canonical_name || UNKNOWN_LABEL;
    const variantLabel = p.variant || p.canonical_name || UNKNOWN_LABEL;

    let familyMap = vendorMap.get(vendorName);
    if (!familyMap) {
      familyMap = new Map();
      vendorMap.set(vendorName, familyMap);
    }
    let variants = familyMap.get(familyName);
    if (!variants) {
      variants = [];
      familyMap.set(familyName, variants);
    }
    variants.push({
      id: p.id,
      canonical_name: p.canonical_name,
      variant: variantLabel,
      category: p.category,
      image_url: p.image_url,
      spec_url: p.spec_url,
      spec_source: p.spec_source,
      spec_version_count: p.spec_version_count || 0,
      price_ref_count: p.price_ref_count || 0,
      latest_spec_date: p.latest_spec_date,
      latest_price_date: p.latest_price_date,
      // R06 (SOL-REVIEW2-2026-09-24): carried through so `variantMatches` can search it -- see
      // that function and `PayloadTreeVariant.notes`'s doc comment.
      notes: p.notes,
    });
  }

  const vendors: PayloadTreeVendor[] = [...vendorMap.keys()]
    .sort((a, b) => a.localeCompare(b))
    .map((vendorName) => {
      const familyMap = vendorMap.get(vendorName)!;
      const families: PayloadTreeFamily[] = [...familyMap.keys()]
        .sort((a, b) => a.localeCompare(b))
        .map((familyName) => {
          const variants = [...familyMap.get(familyName)!].sort((a, b) =>
            a.canonical_name.localeCompare(b.canonical_name),
          );
          return {
            family: familyName,
            variant_count: variants.length,
            spec_version_count: variants.reduce((s, v) => s + v.spec_version_count, 0),
            price_ref_count: variants.reduce((s, v) => s + v.price_ref_count, 0),
            latest_spec_date: latestOf(variants.map((v) => v.latest_spec_date)),
            latest_price_date: latestOf(variants.map((v) => v.latest_price_date)),
            variants,
          };
        });
      return {
        vendor: vendorName,
        family_count: families.length,
        payload_count: families.reduce((s, f) => s + f.variant_count, 0),
        spec_version_count: families.reduce((s, f) => s + f.spec_version_count, 0),
        price_ref_count: families.reduce((s, f) => s + f.price_ref_count, 0),
        families,
      };
    });

  return {
    vendors,
    vendor_count: vendors.length,
    family_count: vendors.reduce((s, v) => s + v.family_count, 0),
    payload_count: vendors.reduce((s, v) => s + v.payload_count, 0),
  };
}

// -----------------------------------------------------------------------------------------
// Tree-node key helpers -- shared between PayloadTree's render, its expanded-state map, and
// keyboard navigation's roving-tabindex bookkeeping. Never persisted/serialized anywhere.
// -----------------------------------------------------------------------------------------

export function vendorNodeKey(vendor: string): string {
  return `v:${vendor}`;
}

export function familyNodeKey(vendor: string, family: string): string {
  return `v:${vendor}|f:${family}`;
}

export function variantNodeKey(variantId: number): string {
  return `p:${variantId}`;
}

function variantMatches(variant: PayloadTreeVariant, query: string): boolean {
  // R06 (SOL-REVIEW2-2026-09-24 review): must search the same field set as the server's `q`
  // filter (`eoa.api.routes.payloads.list_payloads`: canonical_name/vendor_entity_name/family/
  // variant/notes -- vendor and family are already checked one level up, by
  // `filterPayloadTree`'s own `vendorNameMatches`/`familyNameMatches`). Before this fix `notes`
  // was searched server-side but never here, so a payload matched ONLY by its notes text was
  // dropped from the tree by this re-filter even though the server had already matched it.
  return (
    variant.canonical_name.toLowerCase().includes(query) ||
    variant.variant.toLowerCase().includes(query) ||
    (variant.notes ?? "").toLowerCase().includes(query)
  );
}

/** `tree` narrowed to vendors/families/variants matching free-text `query` (matched against the
 * vendor name, the family name, or the variant's own canonical name/variant label) and/or an
 * exact `category`. A vendor/family with zero remaining variants after filtering is dropped
 * entirely rather than rendered empty. `query=""` and `category=""` is a no-op (still returns a
 * fresh, independently-mutable tree). */
export function filterPayloadTree(
  tree: PayloadTreeResponse,
  query: string,
  category: PayloadCategory | "" = "",
): PayloadTreeResponse {
  const q = query.trim().toLowerCase();
  const vendors: PayloadTreeVendor[] = [];

  for (const vendorNode of tree.vendors) {
    const vendorNameMatches = q !== "" && vendorNode.vendor.toLowerCase().includes(q);
    const families: PayloadTreeFamily[] = [];

    for (const familyNode of vendorNode.families) {
      const familyNameMatches = q !== "" && familyNode.family.toLowerCase().includes(q);
      const variants = familyNode.variants.filter((v) => {
        if (category && v.category !== category) return false;
        if (q === "" || vendorNameMatches || familyNameMatches) return true;
        return variantMatches(v, q);
      });
      if (variants.length === 0) continue;
      families.push({
        family: familyNode.family,
        variant_count: variants.length,
        spec_version_count: variants.reduce((s, v) => s + v.spec_version_count, 0),
        price_ref_count: variants.reduce((s, v) => s + v.price_ref_count, 0),
        latest_spec_date: latestOf(variants.map((v) => v.latest_spec_date)),
        latest_price_date: latestOf(variants.map((v) => v.latest_price_date)),
        variants,
      });
    }
    if (families.length === 0) continue;
    vendors.push({
      vendor: vendorNode.vendor,
      family_count: families.length,
      payload_count: families.reduce((s, f) => s + f.variant_count, 0),
      spec_version_count: families.reduce((s, f) => s + f.spec_version_count, 0),
      price_ref_count: families.reduce((s, f) => s + f.price_ref_count, 0),
      families,
    });
  }

  return {
    vendors,
    vendor_count: vendors.length,
    family_count: vendors.reduce((s, v) => s + v.family_count, 0),
    payload_count: vendors.reduce((s, v) => s + v.payload_count, 0),
  };
}

/** Every vendor/family node key that must be force-expanded so a search match in `tree` (already
 * narrowed by `filterPayloadTree`) is visible. Empty when `query` is blank -- nothing to reveal,
 * so the caller's own default/manual expansion state applies instead. */
export function expandedKeysForSearch(tree: PayloadTreeResponse, query: string): Set<string> {
  const keys = new Set<string>();
  if (!query.trim()) return keys;
  for (const vendorNode of tree.vendors) {
    keys.add(vendorNodeKey(vendorNode.vendor));
    for (const familyNode of vendorNode.families) {
      keys.add(familyNodeKey(vendorNode.vendor, familyNode.family));
    }
  }
  return keys;
}

/** Default expanded-key set for a freshly loaded (unfiltered) tree: every vendor/family expanded
 * when the whole catalogue is small enough that collapsing it buys nothing (nothing to "flood"
 * with a handful of rows); fully collapsed -- every vendor/family starts closed -- once the
 * catalogue is larger than `threshold`, which is the actual "not flooding the operator" default
 * the user asked for once the real payload count grows past a small screenful. */
export function defaultExpandedKeys(tree: PayloadTreeResponse, threshold = 8): Set<string> {
  if (tree.payload_count > threshold) return new Set();
  const keys = new Set<string>();
  for (const vendorNode of tree.vendors) {
    keys.add(vendorNodeKey(vendorNode.vendor));
    for (const familyNode of vendorNode.families) {
      keys.add(familyNodeKey(vendorNode.vendor, familyNode.family));
    }
  }
  return keys;
}
