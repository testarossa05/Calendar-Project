"""Microsoft Graph source -- reads the signed-in user's Outlook/Teams calendar.

Uses the ``calendarView`` endpoint, which expands recurring series server-side and
returns concrete occurrences inside the requested window. That is exactly the
shape this tool needs, and it avoids reimplementing RRULE handling.

This is the route to try when "Publish a calendar" is disabled by policy: it needs
a delegated permission on the user's own mailbox, not a sharing policy change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import requests
from dateutil import parser as dateparser

from ..models import Event, SourceRef
from ..msauth import MsAuthError, acquire_token
from ..util import get_tz, to_utc
from .base import Source, SourceError

GRAPH = "https://graph.microsoft.com/v1.0"

# Graph's own free/busy vocabulary, mapped onto the unified status field.
_SHOW_AS = {
    "free": "tentative",
    "tentative": "tentative",
    "busy": "confirmed",
    "oof": "confirmed",
    "workingElsewhere": "confirmed",
    "unknown": "confirmed",
}


class MsGraphSource(Source):
    kind = "msgraph"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        try:
            token = acquire_token(self.cfg.options)
        except MsAuthError as exc:
            raise SourceError(self.cfg.id, str(exc)) from exc

        calendar_id = self.option("calendar_id")
        base = f"{GRAPH}/me/calendars/{calendar_id}/calendarView" if calendar_id else f"{GRAPH}/me/calendarView"

        headers = {
            "Authorization": f"Bearer {token}",
            # Ask Graph to return every timestamp already in UTC.
            "Prefer": 'outlook.timezone="UTC", outlook.body-content-type="text"',
        }
        params: dict[str, Any] = {
            "startDateTime": window_start.replace(tzinfo=None).isoformat(),
            "endDateTime": window_end.replace(tzinfo=None).isoformat(),
            "$top": 100,
            "$select": (
                "id,iCalUId,subject,bodyPreview,start,end,isAllDay,isCancelled,"
                "location,organizer,webLink,showAs,responseStatus,type,seriesMasterId"
            ),
            "$orderby": "start/dateTime",
        }

        timeout = int(self.option("timeout_seconds", 60))
        items: list[dict[str, Any]] = []
        url: Optional[str] = base
        # @odata.nextLink already carries the query string, so params are sent only
        # on the first request; re-applying them would duplicate every parameter.
        next_params: Optional[dict[str, Any]] = params
        for _ in range(200):  # page cap
            try:
                response = requests.get(
                    url, headers=headers, params=next_params, timeout=timeout
                )
            except requests.RequestException as exc:
                raise SourceError(self.cfg.id, f"Graph request failed: {exc}") from exc

            if response.status_code == 403:
                raise SourceError(
                    self.cfg.id,
                    "Graph returned 403. The app registration is missing the delegated "
                    "Calendars.Read permission, or consent was not granted for it.",
                )
            if response.status_code == 401:
                raise SourceError(
                    self.cfg.id,
                    "Graph returned 401. The cached sign-in is no longer valid; run "
                    "'python -m calhub ms-auth' again.",
                )
            if not response.ok:
                raise SourceError(
                    self.cfg.id, f"Graph API {response.status_code}: {response.text[:300]}"
                )

            body = response.json()
            items.extend(body.get("value", []))
            url = body.get("@odata.nextLink")
            next_params = None
            if not url:
                break

        default_tz = get_tz(self.option("timezone", self.app.timezone))
        skip_declined = bool(self.option("skip_declined", True))
        min_minutes = int(self.option("min_duration_minutes", 30))

        events: list[Event] = []
        for item in items:
            event = self._to_event(item, default_tz, skip_declined, min_minutes)
            if event is not None:
                events.append(event)
        return events

    def _to_event(
        self, item: dict[str, Any], default_tz, skip_declined: bool, min_minutes: int
    ) -> Optional[Event]:
        if item.get("isCancelled"):
            return None
        # A seriesMaster is the template, not a real slot; calendarView already
        # returned its occurrences separately.
        if item.get("type") == "seriesMaster":
            return None
        if skip_declined and (item.get("responseStatus") or {}).get("response") == "declined":
            return None

        all_day = bool(item.get("isAllDay"))
        start = self._parse_boundary(item.get("start"), default_tz)
        end = self._parse_boundary(item.get("end"), default_tz)
        if start is None:
            return None
        if end is None:
            end = start + (timedelta(days=1) if all_day else timedelta(minutes=min_minutes))
        if not all_day and end <= start and min_minutes > 0:
            end = start + timedelta(minutes=min_minutes)

        organizer = ((item.get("organizer") or {}).get("emailAddress") or {}).get("address")
        location = (item.get("location") or {}).get("displayName") or None
        status = _SHOW_AS.get(str(item.get("showAs", "busy")), "confirmed")

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=str(item["id"])),
            title=item.get("subject") or "(no title)",
            start=start,
            end=end,
            all_day=all_day,
            description=item.get("bodyPreview") or None,
            location=location,
            url=item.get("webLink"),
            organizer=organizer,
            status=status,
            tzid=(item.get("start") or {}).get("timeZone") or "UTC",
        )

    @staticmethod
    def _parse_boundary(block: Optional[dict[str, Any]], default_tz) -> Optional[datetime]:
        """Graph returns {'dateTime': '...', 'timeZone': 'UTC'} with no offset in the string."""
        if not block or not block.get("dateTime"):
            return None
        parsed = dateparser.isoparse(block["dateTime"])
        if parsed.tzinfo is None:
            zone_name = block.get("timeZone") or "UTC"
            try:
                zone = get_tz(zone_name)
            except ValueError:
                zone = default_tz
            parsed = parsed.replace(tzinfo=zone)
        return to_utc(parsed, default_tz)
