import type { ItemCard } from "@/types/api";

export interface DedupGroups {
  /** One representative item per cluster, in first-seen order -- what the feed actually renders. */
  primaries: ItemCard[];
  /** primary item id -> the other outlets covering the same story (empty when there's only one). */
  duplicatesById: Map<number, ItemCard[]>;
}

/**
 * W9 (docs/REVIEW_2026-09-06_evening.md round 4, feed side): `GET /api/items` returns every row
 * `eoa.pipeline.dedup` has linked via `dedup_of` -- duplicates of the same story from different
 * outlets are NOT filtered out server-side (`list_items` has no `dedup_of IS NULL` clause, unlike
 * most report-building queries), so today's feed shows the same story once per outlet. Folds items
 * that share a cluster (`dedup_of` if set, else the item's own id) into one card, so "+N מקורות"
 * replaces N-1 near-duplicate rows.
 *
 * Only operates over whatever page(s) are currently loaded -- a duplicate whose primary lives on a
 * not-yet-fetched page still gets its own card (nothing else to merge it with yet), same tradeoff
 * `FeedPage`'s own country-grouping already makes.
 */
export function groupDuplicateItems(items: ItemCard[]): DedupGroups {
  const clusterKeyOf = (it: ItemCard): number => it.dedup_of ?? it.id;

  const clusters = new Map<number, ItemCard[]>();
  const firstSeenOrder: number[] = [];
  for (const it of items) {
    const key = clusterKeyOf(it);
    const bucket = clusters.get(key);
    if (bucket) {
      bucket.push(it);
    } else {
      clusters.set(key, [it]);
      firstSeenOrder.push(key);
    }
  }

  const primaries: ItemCard[] = [];
  const duplicatesById = new Map<number, ItemCard[]>();
  for (const key of firstSeenOrder) {
    const members = clusters.get(key)!;
    // Prefer the actual dedup target (id === key, itself not further deduped) as the
    // representative card; if it isn't among the currently-loaded items, fall back to the
    // highest-scored member so the best summary/triage data is what's shown.
    const target = members.find((m) => m.id === key && m.dedup_of == null);
    const primary =
      target ?? [...members].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    const others = members.filter((m) => m.id !== primary.id);
    primaries.push(primary);
    if (others.length > 0) duplicatesById.set(primary.id, others);
  }

  return { primaries, duplicatesById };
}
