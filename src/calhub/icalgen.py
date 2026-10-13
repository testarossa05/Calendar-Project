"""Shared iCalendar generation.

The .ics file sink and the CalDAV sink emit the same VEVENT; only the transport
differs. Keeping one builder means a fix to date handling or to the managed
marker lands in both.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import icalendar

from .models import MANAGED_MARKER, OWNER_TAG, Event
from .util import get_tz

PRODID = "-//calhub//Unified Calendar//EN"

# Properties a later run reads back to recognise and diff its own events.
KEY_PROP = "X-CALHUB-KEY"
HASH_PROP = "X-CALHUB-HASH"
SOURCE_PROP = "X-CALHUB-SOURCE"


def event_uid(event: Event) -> str:
    """The UID this tool gives an event, stable across runs."""
    return f"{OWNER_TAG}-{event.key}@calhub"


def to_vevent(event: Event, stamp: datetime, display_tz: str) -> icalendar.Event:
    vevent = icalendar.Event()
    vevent.add("uid", event_uid(event))
    vevent.add("dtstamp", stamp)
    vevent.add("summary", event.title)

    if event.all_day:
        # All-day boundaries must be DATE values in the display zone, or the
        # phone shifts them by the UTC offset.
        zone = get_tz(display_tz)
        vevent.add("dtstart", event.start.astimezone(zone).date())
        vevent.add("dtend", event.end.astimezone(zone).date())
    else:
        vevent.add("dtstart", event.start)
        vevent.add("dtend", event.end)

    body = compose_description(event)
    if body:
        vevent.add("description", body)
    if event.location:
        vevent.add("location", event.location)
    if event.url:
        vevent.add("url", event.url)
    if event.organizer:
        vevent.add("organizer", f"mailto:{event.organizer}")
    vevent.add("status", event.status.upper())
    vevent.add("transp", "TRANSPARENT" if event.status == "tentative" else "OPAQUE")
    vevent.add(SOURCE_PROP, event.ref.source_id)
    vevent.add(KEY_PROP, event.key)
    vevent.add(HASH_PROP, event.content_hash())
    return vevent


def compose_description(event: Event) -> str:
    parts = []
    if event.description:
        parts.append(event.description)
    # The marker lets a calendar source recognise this as our own output and skip
    # it, so adding the unified calendar to a Mac that is also a source does not
    # feed the tool its own events.
    parts.append(
        f"—\nSource: {event.ref.source_id} ({event.ref.kind}) {MANAGED_MARKER}"
    )
    if event.url:
        parts.append(event.url)
    return "\n\n".join(parts).strip()


def single_event_calendar(event: Event, stamp: datetime, display_tz: str) -> bytes:
    """A one-event VCALENDAR, which is what CalDAV stores per resource."""
    calendar = icalendar.Calendar()
    calendar.add("prodid", PRODID)
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add_component(to_vevent(event, stamp, display_tz))
    return calendar.to_ical()


def read_prop(component, name: str) -> Optional[str]:
    value = component.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
