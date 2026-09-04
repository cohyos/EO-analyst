"""Tender / RFI / RFP tracking and forecasting (section 5.2 / FR-5.2).

``scan.py`` ingests tender notices from ``config/tenders.yaml`` sources into the ``tenders`` +
``items`` tables. ``forecast.py`` derives tender-likelihood forecasts (``tender_forecasts``) from
recent platform-related ``events`` and ``platform_payloads.yaml``. ``report_section.py`` renders
both into the additive daily-report section/table hooks (``eoa.report.docx_builder``'s
``extra_sections``/``tables``).
"""

from __future__ import annotations
