import { describe, expect, it } from "vitest";
import { groupDuplicateItems } from "./dedupGroups";
import type { ItemCard } from "@/types/api";

function makeItem(overrides: Partial<ItemCard>): ItemCard {
  return {
    id: 1,
    title: "t",
    url: "https://example.test",
    source_name: "Source",
    published_at: "2026-09-05T10:00:00Z",
    lang: "en",
    domain: "airborne_pods",
    subdomain: null,
    report_kind: "verified_report",
    trl: null,
    geography: null,
    score: 50,
    level: "yellow",
    triage_reason: null,
    summary_he: null,
    so_what_he: null,
    entities_mentioned: [],
    tags: [],
    security_status: "clean",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    tech_maturity: null,
    tech_actor_kind: null,
    tech_readiness_note_he: null,
    israel_relevance: null,
    israel_reasons: [],
    ...overrides,
  };
}

describe("groupDuplicateItems (W9, feed side)", () => {
  it("passes through items with no duplicates unchanged, in order", () => {
    const items = [makeItem({ id: 1 }), makeItem({ id: 2 })];
    const { primaries, duplicatesById } = groupDuplicateItems(items);
    expect(primaries.map((p) => p.id)).toEqual([1, 2]);
    expect(duplicatesById.size).toBe(0);
  });

  it("folds items sharing a dedup_of target into one card, keyed on the primary's id", () => {
    const primary = makeItem({ id: 10, source_name: "Defense News", score: 80 });
    const dupe1 = makeItem({ id: 11, dedup_of: 10, source_name: "Breaking Defense", score: 70 });
    const dupe2 = makeItem({ id: 12, dedup_of: 10, source_name: "Naval News", score: 60 });
    const other = makeItem({ id: 20, score: 90 });
    const { primaries, duplicatesById } = groupDuplicateItems([primary, dupe1, other, dupe2]);

    // First-seen order of clusters: primary's cluster (key 10) first, then item 20's own cluster.
    expect(primaries.map((p) => p.id)).toEqual([10, 20]);
    expect(duplicatesById.get(10)?.map((d) => d.id).sort()).toEqual([11, 12]);
    expect(duplicatesById.has(20)).toBe(false);
  });

  it("falls back to the highest-scored member as the representative when the real primary isn't loaded", () => {
    // Both loaded items point at dedup_of=999, but item 999 itself isn't among the loaded items
    // (e.g. it's on a page that hasn't been fetched yet).
    const a = makeItem({ id: 21, dedup_of: 999, score: 40 });
    const b = makeItem({ id: 22, dedup_of: 999, score: 65 });
    const { primaries, duplicatesById } = groupDuplicateItems([a, b]);

    expect(primaries.map((p) => p.id)).toEqual([22]);
    expect(duplicatesById.get(22)?.map((d) => d.id)).toEqual([21]);
  });

  it("does not merge two independent single items even when scores tie", () => {
    const items = [makeItem({ id: 1, score: 50 }), makeItem({ id: 2, score: 50 })];
    const { primaries, duplicatesById } = groupDuplicateItems(items);
    expect(primaries).toHaveLength(2);
    expect(duplicatesById.size).toBe(0);
  });
});
