"""Stage: story_id assignment (2026-09-17, "improve same-story grouping across outlets and
languages" task).

User complaint (verbatim intent): items covering the exact same real-world story from different
outlets, sometimes in different languages, were not grouped together anywhere the app shows a
"story" -- the daily/weekly/bd reports (``eoa.report.clustering.cluster_items``) and the feed
(``GET /api/items``) each ran their own narrow, presentation-only grouping (``dedup_of`` cluster
membership or near-identical title text), which missed the common case of a paraphrased headline
in a *different* outlet, or the same story covered in Hebrew and English. Measured evidence: the
Rafael SPICE 1000 / F-35 integration story landed as SIX items (ids 22396 he, 22798/23002/24089/
25948/26284 en) but only two ``dedup_of`` pairs -- the daily report showed it as four separate
rows instead of one.

This module computes a *persisted* story key (``items.story_id``, migration 0035) once, as its
own pipeline stage, so every consumer (reports, feed, chat/ask retrieval if it ever needs "same
story" grouping) reads the same answer instead of each re-deriving a narrower one. Deliberately
NOT a replacement for ``dedup_of`` -- ``dedup_of`` still hides an item from most report queries
(intentional: near-identical text, one canonical copy kept); ``story_id`` never hides anything, it
only groups already-visible items for a single combined row/card. See the hard rule in this task's
brief: "do not change dedup_of semantics; a story cluster must NOT hide anything."

Story = connected component (union-find) over four edge kinds, computed pairwise within a
candidate pool (``eoa.memory.relational.get_items_for_story_clustering``, ``since_days`` window):

  a) **dedup_of** -- an item and its ``dedup_of`` target are definitionally the same story (the
     embedding-dedup stage, ``eoa.pipeline.dedup.run_dedup``, already decided this).
  b) **corroboration** -- ``item_corroboration.sources[].item_id`` (``eoa.pipeline.corroboration``
     already found these are the same event/duplicate via its own deterministic matcher).
  c) **embedding similarity** -- cosine >= ``config.clustering.story_embedding_threshold`` (see
     ``ClusteringCfg`` in ``eoa.config`` for how 0.80 was calibrated against the live SPICE case)
     within +/- ``config.clustering.story_window_days`` of each other's publication date.
  d) **cross-language title/entity match** -- different ``lang``, >=2 shared distinctive Latin
     tokens between the two titles (model numbers/program names/acronyms embedded in a Hebrew
     headline survive translation -- "SPICE 1000", "F-35" -- unlike the surrounding prose) AND >=2
     shared ``entities_mentioned``, within the same window. This is the fallback for exactly the
     case embedding similarity is weakest at: a Hebrew headline scores measurably lower cosine
     similarity against its English counterparts than the English articles score against each
     other (see the calibration note in ``ClusteringCfg``) -- entity/token overlap catches it even
     when the embedding edge alone would not.

``story_id`` is the *minimum item id* in each resulting component -- stable across re-runs
(idempotent: given the same edges, the same id always wins) and cheap to compute (no separate
"pick a representative" step; :func:`eoa.report.clustering.cluster_items` and the API's own
primary-selection logic pick the display-primary independently, by richness, not by this id).

Re-running can only ever *merge* two previously-separate stories (a newly discovered edge -- e.g.
a corroboration hit that arrived late), never split one -- by design: nothing here ever removes an
edge that was valid on a previous run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost
from eoa.execution import checkpoint
from eoa.memory.relational import (
    apply_story_clustering_updates,
    get_corroboration_edges_for_items,
    get_items_for_story_clustering,
    mark_stage,
)

log = structlog.get_logger(__name__)

STAGE = "stories"

#: Default lookback for the nightly stage call (right after ``corroborate`` in
#: ``eoa.orchestrator.jobs.run_daily``) -- matches ``eoa.pipeline.corroboration.WINDOW_DAYS``, the
#: sibling stage this one runs directly after. The backfill/CLI path overrides this explicitly
#: (``eo run stories --since-days 60``).
DEFAULT_SINCE_DAYS = 7

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
#: A Latin "word" of 2+ chars -- model numbers ("F-35"), program/product names ("SPICE"), unit
#: designators and the like, exactly the tokens that survive verbatim inside an otherwise-Hebrew
#: (or Arabic/Russian/...) headline. Deliberately not the full ``_normalize_title`` machinery
#: ``eoa.report.clustering`` uses for same-language paraphrase matching -- that compares two
#: normalized strings as a whole and only ever helps within one language.
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]+")


@dataclass
class StoryClusteringStats:
    items_processed: int = 0
    edges_dedup: int = 0
    edges_corroboration: int = 0
    edges_embedding: int = 0
    edges_title: int = 0
    stories_total: int = 0
    stories_multi_member: int = 0
    reassigned: int = 0
    historical_remapped: int = 0


class _UnionFind:
    """Plain union-find keyed on item id, path-compressing, union-by-min-id (so ``find(x)`` is
    already the component's minimum id -- no separate pass needed to compute ``story_id``).

    F20: ``find``/``union`` auto-register an id they haven't seen yet (rather than requiring every
    id up front via ``__init__``) so a node can represent an item's *persisted* ``story_id`` even
    when that id's own row isn't in the current candidate pool (e.g. the story's root aged out of
    the ``since_days`` window) -- see :func:`_build_components`'s edge (e)."""

    def __init__(self, ids: list[int]) -> None:
        self._parent: dict[int, int] = {i: i for i in ids}

    def _ensure(self, x: int) -> None:
        if x not in self._parent:
            self._parent[x] = x

    def find(self, x: int) -> int:
        self._ensure(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        self._ensure(a)
        self._ensure(b)
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb


def _latin_tokens(title: str | None) -> set[str]:
    if not title:
        return set()
    return {t.casefold() for t in _LATIN_TOKEN_RE.findall(title)}


def _within_window(a, b, days: int) -> bool:
    if a is None or b is None:
        return False
    try:
        return abs((a - b).total_seconds()) <= days * 86400
    except TypeError:
        return False


def _cosine(a: list[float] | None, b: list[float] | None) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return None
    return float(np.dot(va, vb) / (na * nb))


def _build_components(
    items: list[dict], *, embedding_threshold: float, window_days: int
) -> tuple[dict[int, int], StoryClusteringStats]:
    """Pure function (no DB) over an already-loaded item pool -- the part unit tests exercise
    directly with hand-built fixtures. Returns ``{item_id: story_id}`` and edge-count stats."""
    stats = StoryClusteringStats(items_processed=len(items))
    if not items:
        return {}, stats

    by_id = {it["id"]: it for it in items}
    ids = list(by_id)
    uf = _UnionFind(ids)

    # -- edge (e): preserve prior persisted story membership -----------------------------------
    # F20: a nightly re-run only ever sees the current since_days candidate pool -- when a story's
    # oldest member (often the root, since story_id == the component's min item id) ages out of
    # that window, none of the edges below (a-d) are recomputable for it, and the remaining
    # in-window member(s) would otherwise fall back to their own id as a "new" story, silently
    # splitting a previously persisted group. Seeding each item's own persisted ``story_id`` as a
    # union edge (to a virtual/possibly-out-of-pool node) keeps every in-window member anchored to
    # its prior group even when the anchor itself isn't in ``items`` this run -- consistent with
    # this module's own idempotence contract ("nothing here ever removes an edge that was valid on
    # a previous run").
    for it in items:
        prior_story_id = it.get("story_id")
        if prior_story_id is not None:
            uf.union(it["id"], prior_story_id)

    # -- edge (a): dedup_of -------------------------------------------------------------------
    for it in items:
        target = it.get("dedup_of")
        if target is not None and target in by_id:
            uf.union(it["id"], target)
            stats.edges_dedup += 1

    # -- edge (b): corroboration links ---------------------------------------------------------
    for a, b in get_corroboration_edges_for_items(ids):
        if a in by_id and b in by_id:
            uf.union(a, b)
            stats.edges_corroboration += 1

    # -- edges (c) embedding similarity + (d) cross-language title/entity match ----------------
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if uf.find(a["id"]) == uf.find(b["id"]):
                continue  # already linked -- skip the expensive checks
            pub_a = a.get("published_at") or a.get("fetched_at")
            pub_b = b.get("published_at") or b.get("fetched_at")
            if not _within_window(pub_a, pub_b, window_days):
                continue
            sim = _cosine(a.get("embedding"), b.get("embedding"))
            if sim is not None and sim >= embedding_threshold:
                uf.union(a["id"], b["id"])
                stats.edges_embedding += 1
                continue
            if (a.get("lang") or None) == (b.get("lang") or None):
                continue  # edge (d) is cross-language only
            shared_latin = _latin_tokens(a.get("title")) & _latin_tokens(b.get("title"))
            shared_entities = set(a.get("entities_mentioned") or []) & set(
                b.get("entities_mentioned") or []
            )
            if len(shared_latin) >= 2 and len(shared_entities) >= 2:
                uf.union(a["id"], b["id"])
                stats.edges_title += 1

    assignment = {item_id: uf.find(item_id) for item_id in ids}

    sizes: dict[int, int] = {}
    for story_id in assignment.values():
        sizes[story_id] = sizes.get(story_id, 0) + 1
    stats.stories_total = len(sizes)
    stats.stories_multi_member = sum(1 for n in sizes.values() if n >= 2)
    return assignment, stats


def assign_story_ids(since_days: int = DEFAULT_SINCE_DAYS) -> StoryClusteringStats:
    """Stage entrypoint -- also the ``eo run stories --since-days N`` CLI path and the backfill
    entrypoint (``since_days=60``). Idempotent for a fixed edge set: re-running assigns every item
    the same ``story_id`` it already had. A NEW edge found since the last run (e.g. a corroboration
    hit computed after this stage last touched an item) can *merge* two previously-separate
    stories under the lower of their two existing ids -- expected, not a bug, per the module
    docstring."""
    checkpoint()
    cfg = settings().clustering
    items = get_items_for_story_clustering(since_days)
    assignment, stats = _build_components(
        items,
        embedding_threshold=cfg.story_embedding_threshold,
        window_days=cfg.story_window_days,
    )
    if not items:
        return stats

    by_id = {it["id"]: it for it in items}
    changed = {
        item_id: story_id
        for item_id, story_id in assignment.items()
        if by_id[item_id].get("story_id") != story_id
    }

    # F20 (SOL-AUDIT-2026-09-24 review): when this run's edges MERGE two previously-separate
    # persisted stories, `changed` above only covers items in THIS run's since_days pool -- a
    # historical member of either old story that aged out of the pool keeps pointing at its old,
    # now-abandoned root and silently falls out of the merged group. Detect every old_root ->
    # new_root merge from the edges just computed so it can be propagated to every row still on
    # that old root, in or out of the pool.
    root_remap: dict[int, int] = {}
    for item_id, new_story_id in assignment.items():
        prior_story_id = by_id[item_id].get("story_id")
        if prior_story_id is not None and prior_story_id != new_story_id:
            root_remap[prior_story_id] = new_story_id

    # R05/F20 (SOL-REVIEW2-2026-09-24): `changed`'s current-pool reassignment and `root_remap`'s
    # historical-root propagation now run in ONE transaction (`apply_story_clustering_updates`)
    # instead of two separately-committed calls -- a crash between the old two commits left the
    # current pool on the new story_id while historical out-of-pool members stayed on the old,
    # now-abandoned root, an unrecoverable split (retrying recomputes a fresh assignment with no
    # way to reconstruct which old root pointed at which new one).
    if changed or root_remap:
        n_reassigned, n_remapped = apply_story_clustering_updates(changed, root_remap)
        stats.reassigned = n_reassigned
        stats.historical_remapped = n_remapped

    for item_id in by_id:
        try:
            mark_stage(item_id, STAGE)
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:  # a stage-mark failure must never abort the whole batch
            log.warning("story_clustering_mark_stage_failed", item_id=item_id, error=str(exc)[:200])

    log.info("story_clustering_done", **stats.__dict__)
    return stats
