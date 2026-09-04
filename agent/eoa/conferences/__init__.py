"""FR-12: rolling conference & exhibition tracker.

``tracker.py`` — horizon roll-forward, deep-search-lite verification, discovery of new
conferences, and the monthly scan that ties them together.
``reminders.py`` — FR-12.4 due-date computation and ntfy dispatch (deduped).
``ical.py`` — FR-12.7 iCalendar export.
"""

from __future__ import annotations
