"""Product dossier ("סקירת שוק עמוקה למוצר", PD-backend, user request 2026-09-08).

See ``docs/PLAN_PRODUCT_DOSSIER.md`` (the frozen contract). Pipeline stages, each its own module:

  1. ``corpus`` -- gather everything the DB already has for the product's aliases (items, events,
     patents, tenders/forecasts, entities + graph edges, previous dossier) and seed the citation
     registry from it.
  2. ``plan`` -- run the fixed research plan (one ``eoa.search.deep_search.investigate()`` call per
     topic) and extend the registry with every web source read.
  3. ``extract`` -- one structured-extraction LLM call (``eoa.llm.schemas.product_dossier
     .ProductDossierOut``) over the corpus + topic findings, then deterministic grounding
     post-checks that drop an ungrounded field to null rather than raising.
  4. ``diff`` -- "מה השתנה" vs. the previous dossier of the same ``product_key``.
  5. ``report`` -- persist ``product_dossiers`` + ``reports`` rows and render md/html/docx.

``eoa.orchestrator.jobs.run_product_dossier`` is the job-kind entry point; ``eoa.api.services``
exposes the five REST endpoints on top of :func:`eoa.dossier.report.build_product_dossier`.
"""

from __future__ import annotations
