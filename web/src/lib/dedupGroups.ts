import type { ItemCard, StoryMember } from "@/types/api";

export interface DedupGroups {
  /** One representative item per cluster, in first-seen order -- what the feed actually renders. */
  primaries: ItemCard[];
  /** primary item id -> the other outlets covering the same story (empty when there's only one). */
  duplicatesById: Map<number, StoryMember[]>;
}

/**
 * W9 (docs/REVIEW_2026-09-06_evening.md round 4, feed side): `GET /api/items` returns every row
 * `eoa.pipeline.dedup` has linked via `dedup_of` -- duplicates of the same story from different
 * outlets are NOT filtered out server-side by default, so the feed can show the same story once
 * per outlet. Folds same-story items into one card, so "+N מקורות" replaces N-1 near-duplicate
 * rows.
 *
 * 2026-09-17 (story-clustering task): buckets are now ALSO merged when items share a `story_id`
 * (`eoa.pipeline.story_clustering.assign_story_ids`: a connected component over dedup_of/
 * corroboration/embedding-similarity/cross-language-title edges), on top of the original
 * `dedup_of`-bucket step below. `dedup_of` alone missed the common real case this task was written
 * to fix -- a paraphrased headline in a different outlet, or the same story covered in Hebrew and
 * English (measured evidence: the Rafael SPICE 1000/F-35 story, 6 items across 5 outlets and 2
 * languages, rendered as FOUR separate feed rows under the old `dedup_of`-only key). Keeping the
 * original `dedup_of` step (rather than replacing it) means an item ingested before the nightly
 * `stories` stage has swept it (`story_id` still null) still folds correctly, same as before this
 * change -- no regression during that window, including the case where a `dedup_of` target isn't
 * itself among the currently-loaded items (both items still share that target as their bucket key,
 * whether or not the target row is loaded -- unchanged from the original behavior).
 *
 * When a primary's own `story_members` is already populated (the backend's `_attach_story_info`
 * looks up a story's FULL membership, not just this page's -- see `GET /api/items`'s
 * `group_stories` mode), those cross-page members are merged into the chip too, so "+N" is
 * accurate even when a sibling outlet's item lives on a page not currently loaded -- the one
 * limitation this function otherwise still has (a duplicate whose primary lives on a not-yet-
 * fetched page still gets its own card here; only the server-side full lookup covers that case).
 */
export function groupDuplicateItems(items: ItemCard[]): DedupGroups {
  // Phase 1 (original, unchanged): each item's bucket key is its own `dedup_of` target, or its
  // own id when it has none -- two items sharing a target fall into the same bucket even when the
  // target itself isn't among the loaded items.
  const bucketKeyByItemId = new Map<number, number>();
  for (const it of items) bucketKeyByItemId.set(it.id, it.dedup_of ?? it.id);

  // Phase 2 (2026-09-17): union-find over BUCKET KEYS (not raw item ids) -- merges two phase-1
  // buckets whenever a member of each shares a `story_id`.
  const parent = new Map<number, number>();
  for (const key of bucketKeyByItemId.values()) parent.set(key, key);
  const find = (x: number): number => {
    let root = x;
    while (parent.get(root) !== root) root = parent.get(root)!;
    let cur = x;
    while (parent.get(cur) !== root) {
      const next = parent.get(cur)!;
      parent.set(cur, root);
      cur = next;
    }
    return root;
  };
  const union = (a: number, b: number): void => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(Math.max(ra, rb), Math.min(ra, rb));
  };

  const bucketKeysByStoryId = new Map<number, number[]>();
  for (const it of items) {
    if (it.story_id == null) continue;
    const bucketKey = bucketKeyByItemId.get(it.id)!;
    const keys = bucketKeysByStoryId.get(it.story_id);
    if (keys) {
      if (!keys.includes(bucketKey)) keys.push(bucketKey);
    } else {
      bucketKeysByStoryId.set(it.story_id, [bucketKey]);
    }
  }
  for (const keys of bucketKeysByStoryId.values()) {
    for (let i = 1; i < keys.length; i++) union(keys[0], keys[i]);
  }

  const clusters = new Map<number, ItemCard[]>();
  const firstSeenOrder: number[] = [];
  for (const it of items) {
    const key = find(bucketKeyByItemId.get(it.id)!);
    const bucket = clusters.get(key);
    if (bucket) {
      bucket.push(it);
    } else {
      clusters.set(key, [it]);
      firstSeenOrder.push(key);
    }
  }

  const primaries: ItemCard[] = [];
  const duplicatesById = new Map<number, StoryMember[]>();
  for (const key of firstSeenOrder) {
    const members = clusters.get(key)!;
    // Prefer the actual dedup target (id === key, itself not further deduped) as the
    // representative card; if it isn't among the currently-loaded items, fall back to the
    // highest-scored member so the best summary/triage data is what's shown.
    const target = members.find((m) => m.id === key && m.dedup_of == null);
    const primary = target ?? [...members].sort((a, b) => (b.score ?? 0) - (a.score ?? 0))[0];
    const others = members.filter((m) => m.id !== primary.id);

    const merged = new Map<number, StoryMember>();
    for (const o of others) merged.set(o.id, o);
    for (const m of primary.story_members ?? []) {
      if (m.id !== primary.id) merged.set(m.id, m);
    }
    primaries.push(primary);
    if (merged.size > 0) duplicatesById.set(primary.id, [...merged.values()]);
  }

  return { primaries, duplicatesById };
}
