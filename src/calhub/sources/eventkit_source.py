"""macOS EventKit source -- reads calendars already synced to the Mac.

This is the route that needs no tenant change whatsoever. If Outlook or Apple
Calendar on the Mac is already signed in to the work account, the events are
sitting in the local EventKit store. Reading them is ordinary client access to
data the user is already authorised to see -- no calendar publishing, no app
registration, no administrator consent.

The trade-off is that it only runs on a Mac, and only while that Mac is awake.
Pair it with a scheduled local run (launchd or cron) writing to the Google sink,
so the phone still sees the merged calendar when the Mac is asleep.

Requires: pip install pyobjc-framework-EventKit
macOS will prompt for calendar access on first run; grant it to the terminal
application, otherwise the store returns zero calendars.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any, Optional

from ..models import Event, SourceRef
from ..util import UTC, fold_name, get_tz, to_utc
from .base import Source, SourceError

# EKEntityTypeEvent
_ENTITY_EVENT = 0
# EKAuthorizationStatus
_AUTH_AUTHORIZED = 3
_AUTH_FULL_ACCESS = 4  # macOS 14+ renamed the granted state


class EventKitSource(Source):
    kind = "eventkit"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        if sys.platform != "darwin":
            raise SourceError(
                self.cfg.id,
                "the eventkit source only runs on macOS. On another platform use the "
                "'msgraph' or 'ics' source instead.",
            )
        store = self._open_store()
        calendars = self._select_calendars(store)
        default_tz = get_tz(self.option("timezone", self.app.timezone))
        min_minutes = int(self.option("min_duration_minutes", 30))

        predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
            _to_nsdate(window_start), _to_nsdate(window_end), calendars
        )
        raw = store.eventsMatchingPredicate_(predicate) or []

        events: list[Event] = []
        for item in raw:
            event = self._to_event(item, default_tz, min_minutes)
            if event is not None:
                events.append(event)
        return events

    def _open_store(self):
        try:
            from EventKit import EKEventStore  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SourceError(
                self.cfg.id,
                "PyObjC is not installed. Run: pip install pyobjc-framework-EventKit",
            ) from exc

        status = EKEventStore.authorizationStatusForEntityType_(_ENTITY_EVENT)
        store = EKEventStore.alloc().init()

        if status not in (_AUTH_AUTHORIZED, _AUTH_FULL_ACCESS):
            granted = _request_access(store)
            if not granted:
                raise SourceError(
                    self.cfg.id,
                    "macOS denied calendar access. Grant it in System Settings -> "
                    "Privacy & Security -> Calendars for the application running this "
                    "command (Terminal, iTerm, or your scheduler).",
                )
        return store

    def _select_calendars(self, store):
        available = list(store.calendarsForEntityType_(_ENTITY_EVENT) or [])
        if not available:
            raise SourceError(
                self.cfg.id,
                "no calendars visible to EventKit. Confirm the account is signed in "
                "in Calendar.app and that calendar access has been granted.",
            )

        wanted = self.option("calendars")
        if not wanted:
            return available

        wanted_map = {fold_name(w): str(w) for w in wanted}
        selected = [c for c in available if fold_name(c.title()) in wanted_map]
        found = {fold_name(c.title()) for c in selected}
        missing = [original for key, original in wanted_map.items() if key not in found]
        if missing:
            names = ", ".join(sorted(str(c.title()) for c in available))
            raise SourceError(
                self.cfg.id, f"calendar(s) not found: {sorted(missing)}. Available: {names}"
            )
        return selected

    def _to_event(self, item: Any, default_tz, min_minutes: int) -> Optional[Event]:
        # EKEventStatusCanceled == 3
        try:
            if int(item.status()) == 3:
                return None
        except Exception:
            pass

        start = _from_nsdate(item.startDate())
        end = _from_nsdate(item.endDate())
        if start is None:
            return None
        all_day = bool(item.isAllDay())

        if end is None:
            end = start + (timedelta(days=1) if all_day else timedelta(minutes=min_minutes))
        if not all_day and end <= start and min_minutes > 0:
            end = start + timedelta(minutes=min_minutes)

        # EventKit gives a recurring occurrence the same eventIdentifier as its
        # series, so the occurrence start has to be part of the key or every
        # occurrence would collapse into one.
        identifier = str(item.eventIdentifier() or "")
        if not identifier:
            identifier = f"{item.title()}|{start.isoformat()}"
        elif item.hasRecurrenceRules():
            identifier = f"{identifier}|{start.isoformat()}"

        calendar_title = ""
        try:
            calendar_title = str(item.calendar().title())
        except Exception:
            pass

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=identifier),
            title=str(item.title() or "(no title)"),
            start=to_utc(start, default_tz),
            end=to_utc(end, default_tz),
            all_day=all_day,
            description=_optional_str(item.notes()),
            location=_optional_str(item.location()),
            url=_optional_str(item.URL().absoluteString() if item.URL() else None),
            status="confirmed",
            tzid=self.option("timezone", self.app.timezone),
            extra={"calendar": calendar_title},
        )


def _request_access(store) -> bool:
    """Block on the macOS permission prompt and return whether access was granted."""
    import threading

    done = threading.Event()
    result = {"granted": False}

    def handler(granted, error):  # noqa: ANN001 - ObjC callback signature
        result["granted"] = bool(granted)
        done.set()

    # macOS 14 split the old API into full/write-only variants.
    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(handler)
    else:
        store.requestAccessToEntityType_completion_(_ENTITY_EVENT, handler)

    done.wait(timeout=120)
    return result["granted"]


def _to_nsdate(value: datetime):
    from Foundation import NSDate  # type: ignore[import-not-found]

    return NSDate.dateWithTimeIntervalSince1970_(value.timestamp())


def _from_nsdate(value) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromtimestamp(value.timeIntervalSince1970(), tz=UTC)


def _optional_str(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
