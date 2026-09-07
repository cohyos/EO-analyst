"""R10-graph (docs/qa/loop/round_10_fixes.md): the analyst-facing entity graph.

Distinct from `eoa.memory.graph` (the low-level `graph_edges` CRUD + BFS primitives used by the
pipeline to *write* edges). This package holds the *read* queries the `/api/graph/*` and
`/api/entities/{id}/detail` routes serve to the UI -- see `eoa.graph.queries`.
"""

from __future__ import annotations
