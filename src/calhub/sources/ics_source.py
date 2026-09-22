"""ICS/iCalendar feed source.

This is the workhorse adapter. It covers every calendar that can publish a feed
URL without an administrator granting API access:

* Outlook / Microsoft 365 -- "Publish a calendar" produces an .ics URL.
* Notion -- each database view offers a "Copy link to calendar" .ics URL.
* iCloud -- a shared calendar exposes a webcal:// URL.
* Almost anything else (Google, Confluence, Jira, KTX, school calendars...).

Recurring events are expanded into concrete occurrences inside the sync window,
which is what makes the merged calendar actually usable on a phone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import icalendar
import recurring_ical_events
import requests

from ..models import MANAGED_MARKER, Event, SourceRef
from ..util import get_tz, is_date_only, to_utc
from .base import Source, SourceError

_STATUS_MAP = {
    "CONFIRMED": "confirmed",
    "TENTATIVE": "tentative",
    "CANCELLED": "cancelled",
}

# Outlook rejects a plain requests default UA on some tenants' publish endpoints.
_UA = "calhub/0.1 (+https://github.com/testarossa05/PTKR-Mechanical-Sales-Ontology-Hub)"


class IcsSource(Source):
    kind = "ics"

    def _load_bytes(self) -> bytes:
        url: Optional[str] = self.option("url")
        file_path: Optional[str] = self.option("file")
        if not url and not file_path:
            raise SourceError(self.cfg.id, "either 'url' or 'file' must be set")

        if file_path:
            path = Path(file_path).expanduser()
            if not path.exists():
                raise SourceError(self.cfg.id, f"ics file not found: {path}")
            return path.read_bytes()

        # webcal:// is just https:// with a scheme that tells a phone to subscribe.
        if url.startswith("webcal://"):
            url = "https://" + url[len("webcal://") :]
        timeout = int(self.option("timeout_seconds", 60))
        try:
            response = requests.get(
                url, timeout=timeout, headers={"User-Agent": _UA}
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceError(self.cfg.id, f"could not fetch ICS feed: {exc}") from exc
        if not response.content.lstrip().upper().startswith(b"BEGIN:VCALENDAR"):
            raise SourceError(
                self.cfg.id,
                "the URL did not return an iCalendar feed. Check that the link is the "
                "ICS/subscribe URL and not the HTML page.",
            )
        return response.content

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        raw = self._load_bytes()
        try:
            calendar = icalendar.Calendar.from_ical(raw)
        except Exception as exc:  # icalendar raises bare ValueError subclasses
            raise SourceError(self.cfg.id, f"could not parse ICS feed: {exc}") from exc

        default_tz = get_tz(self.option("timezone", self.app.timezone))
        skip_declined = bool(self.option("skip_declined", True))
        min_minutes = int(self.option("min_duration_minutes", 30))

        # The expansion library rewrites each occurrence: it drops RRULE and stamps
        # RECURRENCE-ID on every component, recurring or not. Recurrence therefore has
        # to be decided from the original calendar, before expansion -- otherwise every
        # event looks recurring, its key absorbs the start time, and simply moving a
        # meeting would delete and recreate it instead of updating it in place.
        recurring_uids = _recurring_uids(calendar)

        try:
            occurrences = recurring_ical_events.of(
                calendar, skip_bad_series=True
            ).between(window_start, window_end)
        except Exception as exc:
            raise SourceError(
                self.cfg.id, f"could not expand recurring events: {exc}"
            ) from exc

        events: list[Event] = []
        for component in occurrences:
            if component.name != "VEVENT":
                continue
            event = self._to_event(
                component, default_tz, skip_declined, recurring_uids, min_minutes
            )
            if event is not None:
                events.append(event)
        return events

    def _to_event(
        self,
        component,
        default_tz,
        skip_declined: bool,
        recurring_uids: set[str],
        min_minutes: int,
    ) -> Optional[Event]:
        dtstart = component.get("DTSTART")
        if dtstart is None:
            return None
        start_raw = dtstart.dt
        all_day = is_date_only(start_raw)

        dtend = component.get("DTEND")
        if dtend is not None:
            end_raw = dtend.dt
        else:
            duration = component.get("DURATION")
            if duration is not None:
                end_raw = start_raw + duration.dt
            elif all_day:
                end_raw = start_raw + timedelta(days=1)
            else:
                end_raw = start_raw + timedelta(hours=1)

        start = to_utc(start_raw, default_tz)
        end = to_utc(end_raw, default_tz)

        # RFC 5545 gives a timed VEVENT with no DTEND zero duration, and the expansion
        # library materialises that faithfully. A zero-length event is invisible in a
        # phone calendar, so give it a floor.
        if not all_day and end <= start and min_minutes > 0:
            end = start + timedelta(minutes=min_minutes)

        description = _text(component.get("DESCRIPTION"))
        if description and MANAGED_MARKER in description:
            return None  # our own output, arriving back through a subscription

        status = _STATUS_MAP.get(str(component.get("STATUS", "")).upper(), "confirmed")
        if status == "cancelled":
            return None

        if skip_declined and self._is_declined(component):
            return None

        # An expanded occurrence shares its UID with every sibling, so the recurrence
        # start must be part of the identity or they collapse into one event.
        uid = str(component.get("UID", ""))
        if not uid:
            uid = f"{component.get('SUMMARY', '')}|{start.isoformat()}"
        # Only a recurring series needs the occurrence time in its identity; a one-off
        # keeps a stable key so that moving it is an update, not a delete-and-recreate.
        native_uid = f"{uid}|{start.isoformat()}" if uid in recurring_uids else uid

        tzid = self.option("timezone", self.app.timezone)
        raw_tzid = getattr(getattr(start_raw, "tzinfo", None), "key", None)
        if isinstance(raw_tzid, str):
            tzid = raw_tzid

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=native_uid),
            title=str(component.get("SUMMARY", "(no title)")),
            start=start,
            end=end,
            all_day=all_day,
            description=description,
            location=_text(component.get("LOCATION")),
            url=_text(component.get("URL")),
            organizer=_mail(component.get("ORGANIZER")),
            status=status,
            tzid=tzid,
        )

    @staticmethod
    def _is_declined(component) -> bool:
        """True when every listed attendee response is a decline.

        Outlook keeps declined meetings in a published feed; mirroring them would
        fill the unified calendar with meetings the user is not attending.
        """
        attendees = component.get("ATTENDEE")
        if attendees is None:
            return False
        if not isinstance(attendees, list):
            attendees = [attendees]
        partstats = []
        for attendee in attendees:
            params = getattr(attendee, "params", {})
            partstat = str(params.get("PARTSTAT", "")).upper()
            if partstat:
                partstats.append(partstat)
        return bool(partstats) and all(p == "DECLINED" for p in partstats)


def _recurring_uids(calendar) -> set[str]:
    """UIDs that define a recurring series in the un-expanded calendar."""
    uids: set[str] = set()
    for component in calendar.walk("VEVENT"):
        if component.get("RRULE") or component.get("RDATE") or component.get("RECURRENCE-ID"):
            uid = str(component.get("UID", ""))
            if uid:
                uids.add(uid)
    return uids


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _mail(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if text.upper().startswith("MAILTO:"):
        return text[7:]
    return text or None
