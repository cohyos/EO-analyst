"""Product-line status & business-development tracking (PL-backend, user request 2026-09-07).

Six EO/IR product lines (see ``config/product_lines.yaml`` for the frozen id list/definitions):
``targeting_pods``, ``mws_eo``, ``lorop_pods``, ``eo_air_defense_warning``, ``ball_gimbals_16in``,
``border_long_range_eo``. Submodules:

- :mod:`eoa.product_lines.registry` -- typed access to ``config/product_lines.yaml``.
- :mod:`eoa.product_lines.tagging` -- deterministic (no LLM) ``tag_product_lines`` classifier,
  called from ``eoa.pipeline.analyze.persist_analysis`` per item/event and from
  ``scripts/backfill_product_lines.py`` for everything already in the DB.
- :mod:`eoa.product_lines.stats` -- ``product_line_stats(line_id)`` (items/events/tenders/
  forecasts/patents/competitors counts) backing ``GET /api/product-lines``.
"""

from __future__ import annotations
