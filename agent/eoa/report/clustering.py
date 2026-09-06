"""Round 4 (2026-09-06, W9): group items that are really the same underlying story covered by
more than one outlet, so a report table shows one row (the richest item as primary) instead of one
row per outlet.

Two items are considered the same story when either:
  - they already share a ``dedup_of`` cluster (one is the other's ``dedup_of`` target, or both
    point at the same target) -- the pipeline's own embedding-based dedup already identified them
    as duplicates upstream (``eoa.pipeline.dedup``), just not strictly enough to collapse into a
    single ``items`` row; or
  - their titles are near-identical (normalized similarity >= :data:`SIMILARITY_THRESHOLD`) -- the
    same headline paraphrased slightly differently by two outlets, which the embedding dedup stage
    does not always catch (different embedding, same fact).

This module is deliberately presentation-only: it never removes or renumbers anything from a
caller's already-numbered item/citation list (see e.g. ``eoa.report.daily._extend_citation_registry``)
-- it only re-groups an existing, already-``n``-numbered list into :class:`ItemCluster`s for a
render step to fold into one row with the group's other members cited as "extra sources".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

SIMILARITY_THRESHOLD = 0.85

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize_title(title: str | None) -> str:
    text = (title or "").strip().casefold()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _title_similarity(a: str | None, b: str | None) -> float:
    norm_a, norm_b = _normalize_title(a), _normalize_title(b)
    if not norm_a or not norm_b:
        return 0.0
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _dedup_target(item: dict[str, Any]) -> Any:
    """The id this item's ``dedup_of`` cluster is keyed on -- its own id when it has no
    ``dedup_of`` (i.e. it is itself a potential cluster anchor)."""
    return item.get("dedup_of") if item.get("dedup_of") is not None else item.get("id")


def _richness(item: dict[str, Any]) -> tuple[int, float]:
    """More populated fields, then higher score, wins as a cluster's primary row."""
    fields = sum(1 for k in ("summary_he", "so_what_he", "url", "published_at") if item.get(k))
    score = item.get("score")
    try:
        score_val = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score_val = 0.0
    return (fields, score_val)


@dataclass
class ItemCluster:
    primary: dict[str, Any]
    extra: list[dict[str, Any]] = field(default_factory=list)

    @property
    def all_items(self) -> list[dict[str, Any]]:
        return [self.primary, *self.extra]


def cluster_items(items: list[dict[str, Any]]) -> list[ItemCluster]:
    """Group ``items`` into :class:`ItemCluster`s. Order-preserving: a new group is anchored at the
    position of the first item that starts it. Never raises; ``[]`` in, ``[]`` out. Items already
    carrying an ``n`` (or any other key) keep it untouched -- this function only regroups, it never
    mutates an item dict."""
    if not items:
        return []
    groups: list[list[dict[str, Any]]] = []

    for it in items:
        target = _dedup_target(it)
        placed = False
        for group in groups:
            for member in group:
                same_cluster = target is not None and _dedup_target(member) == target
                similar_title = (
                    _title_similarity(it.get("title"), member.get("title")) >= SIMILARITY_THRESHOLD
                )
                if same_cluster or similar_title:
                    group.append(it)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            groups.append([it])

    clusters: list[ItemCluster] = []
    for group in groups:
        ordered = sorted(group, key=_richness, reverse=True)
        clusters.append(ItemCluster(primary=ordered[0], extra=ordered[1:]))
    return clusters


def extra_sources_note_he(cluster: ItemCluster) -> str:
    """``" (+2 מקורות נוספים)"`` suffix for a clustered row -- empty string when the cluster has no
    extra members. The extra items' own ``[n]`` markers are expected to be added to the caller's
    ``cites`` list separately (this only supplies the human-readable count note)."""
    if not cluster.extra:
        return ""
    count = len(cluster.extra)
    word = "מקור נוסף" if count == 1 else "מקורות נוספים"
    return f" (+{count} {word})"


def cluster_extra_ns(cluster: ItemCluster) -> list[int]:
    """The ``n`` of every extra (non-primary) member that actually has one -- ready to append to a
    ``Sentence.cites``/table-row citation list alongside the primary's own ``n``."""
    return [int(it["n"]) for it in cluster.extra if it.get("n") is not None]
