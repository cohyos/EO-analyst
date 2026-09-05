"""Memory layer: relational (psycopg3), graph (plain SQL `graph_edges`), and vector (numpy) access.

2026-09-05 (ADR-004): Apache AGE and pgvector are no longer required -- see
`eoa.memory.graph` and `eoa.memory.vector`.
"""

from __future__ import annotations
