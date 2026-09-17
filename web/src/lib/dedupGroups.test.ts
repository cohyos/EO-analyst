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

  // -- 2026-09-17 (story-clustering task) -------------------------------------------------------

  it("folds items sharing a story_id even with no dedup_of link and unrelated titles", () => {
    const a = makeItem({ id: 30, title: "Rafael Integrates SPICE 1000 With F-35", story_id: 30, score: 8 });
    const b = makeItem({
      id: 31,
      title: "SPICE 1000 של רפאל משולב במטוסי F-35",
      lang: "he",
      story_id: 30,
      score: 5,
    });
    const unrelated = makeItem({ id: 32, title: "Unrelated submarine news", score: 6 });
    const { primaries, duplicatesById } = groupDuplicateItems([a, b, unrelated]);

    expect(primaries.map((p) => p.id).sort()).toEqual([30, 32]);
    expect(duplicatesById.get(30)?.map((d) => d.id)).toEqual([31]);
  });

  it("the six-item SPICE-1000/F-35 case (task brief's measured evidence) becomes one row", () => {
    // Two pre-existing dedup_of pairs (22798->23002, 25948->22396) plus a shared story_id across
    // all six -- the exact shape the live backfill produces.
    const items = [
      makeItem({ id: 22396, lang: "he", story_id: 22396, score: 5 }),
      makeItem({ id: 22798, dedup_of: 23002, story_id: 22396, score: 4 }),
      makeItem({ id: 23002, story_id: 22396, score: 6 }),
      makeItem({ id: 24089, story_id: 22396, score: 9 }),
      makeItem({ id: 25948, dedup_of: 22396, story_id: 22396, score: 3 }),
      makeItem({ id: 26284, story_id: 22396, score: 2 }),
    ];
    const { primaries, duplicatesById } = groupDuplicateItems(items);

    expect(primaries).toHaveLength(1);
    const [primary] = primaries;
    expect(duplicatesById.get(primary.id)).toHaveLength(5);
  });

  it("merges cross-page members from a primary's own story_members", () => {
    const primary = makeItem({
      id: 40,
      score: 9,
      story_id: 40,
      story_members: [{ id: 41, title: "other outlet", source_name: "X", lang: "en", url: "u" }],
    });
    const { primaries, duplicatesById } = groupDuplicateItems([primary]);
    expect(primaries.map((p) => p.id)).toEqual([40]);
    expect(duplicatesById.get(40)?.map((d) => d.id)).toEqual([41]);
  });

  it("an item without story_id still only merges via dedup_of, unchanged", () => {
    const a = makeItem({ id: 50, score: 8 });
    const b = makeItem({ id: 51, dedup_of: 50, score: 3 });
    const c = makeItem({ id: 52, score: 7 }); // no story_id, no dedup_of link to a/b
    const { primaries, duplicatesById } = groupDuplicateItems([a, b, c]);
    expect(primaries.map((p) => p.id).sort()).toEqual([50, 52]);
    expect(duplicatesById.get(50)?.map((d) => d.id)).toEqual([51]);
  });
});
