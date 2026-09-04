"""FR-12.7: iCalendar export of the conference horizon.

One VEVENT per conference span, plus one all-day VEVENT per published critical date
(registration opens / early-bird deadline / CFP deadline) so they show up on an imported personal
calendar. No VALARM components — reminder timing is left to the importing calendar app; FR-12.4's
ntfy reminders are the system's own notification channel. UTF-8 throughout; summaries are
RTL-safe Hebrew ("כנס: <name>") since iCalendar TEXT values carry Unicode without escaping beyond
the standard comma/semicolon/backslash rules that the ``icalendar`` library already handles.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from icalendar import Calendar, Event

# field -> Hebrew label for the standalone reminder-date VEVENTs
_REMINDER_FIELDS = {
    "registration_opens": "פתיחת הרשמה",
    "early_bird_deadline": "מועד אחרון Early Bird",
    "cfp_deadline": "מועד אחרון Call for Papers",
}


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def build_ical(confs: list[dict[str, Any]]) -> str:
    """Build a ``text/calendar`` (RFC 5545) payload for ``confs`` (as returned by
    ``eoa.conferences.tracker.conference_card`` — plain dicts with ``start_date``/``end_date`` or
    the legacy ``starts_at``/``ends_at`` both accepted). Conferences without a known start date are
    skipped (nothing to put on a calendar yet); this is expected for freshly-discovered candidates.
    """
    cal = Calendar()
    cal.add("prodid", "-//EO-Analyst//conferences//")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    for conf in confs:
        start = _as_date(conf.get("start_date") or conf.get("starts_at"))
        if start is None:
            continue
        end = _as_date(conf.get("end_date") or conf.get("ends_at")) or start
        name = conf.get("name") or ""
        uid_base = f"conf-{conf.get('id')}"

        main = Event()
        main.add("uid", f"{uid_base}@eo-analyst")
        main.add("summary", f"כנס: {name}")
        main.add("dtstart", start)
        main.add("dtend", end + dt.timedelta(days=1))  # DTEND is exclusive for all-day events
        location = conf.get("city") or conf.get("location")
        if location:
            main.add("location", location)
        url = conf.get("registration_url") or conf.get("url")
        if url:
            main.add("url", url)
        cal.add_component(main)

        for field, label_he in _REMINDER_FIELDS.items():
            due = _as_date(conf.get(field))
            if due is None:
                continue
            rem = Event()
            rem.add("uid", f"{uid_base}-{field}@eo-analyst")
            rem.add("summary", f"כנס: {name} — {label_he}")
            rem.add("dtstart", due)
            rem.add("dtend", due + dt.timedelta(days=1))
            if location:
                rem.add("location", location)
            cal.add_component(rem)

    return cal.to_ical().decode("utf-8")
