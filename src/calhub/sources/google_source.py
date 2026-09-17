"""Google Calendar source (read side)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from dateutil import parser as dateparser

from ..gauth import AuthError, build_service
from ..models import Event, SourceRef
from ..util import get_tz, to_utc
from .base import Source, SourceError


class GoogleSource(Source):
    kind = "google"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        calendar_id = self.option("calendar_id", "primary")
        default_tz = get_tz(self.option("timezone", self.app.timezone))
        try:
            service = build_service(self.cfg.options, readonly=True)
        except AuthError as exc:
            raise SourceError(self.cfg.id, str(exc)) from exc

        items: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        try:
            while True:
                response = (
                    service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=window_start.isoformat(),
                        timeMax=window_end.isoformat(),
                        singleEvents=True,  # expand recurrences server-side
                        showDeleted=False,
                        maxResults=2500,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
                items.extend(response.get("items", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except Exception as exc:
            raise SourceError(self.cfg.id, f"Google Calendar read failed: {exc}") from exc

        skip_declined = bool(self.option("skip_declined", True))
        events: list[Event] = []
        for item in items:
            event = self._to_event(item, default_tz, skip_declined, calendar_id)
            if event is not None:
                events.append(event)
        return events

    def _to_event(
        self, item: dict[str, Any], default_tz, skip_declined: bool, calendar_id: str
    ) -> Optional[Event]:
        if item.get("status") == "cancelled":
            return None
        # Never re-ingest events this tool itself wrote, or a two-way loop forms.
        if (item.get("extendedProperties", {}).get("private") or {}).get("calhub_key"):
            return None
        if skip_declined and self._self_declined(item):
            return None

        start_block = item.get("start") or {}
        end_block = item.get("end") or {}
        all_day = "date" in start_block

        if all_day:
            start = to_utc(dateparser.isoparse(start_block["date"]).date(), default_tz)
            end_date = end_block.get("date")
            end = (
                to_utc(dateparser.isoparse(end_date).date(), default_tz)
                if end_date
                else start + timedelta(days=1)
            )
        else:
            start_str = start_block.get("dateTime")
            if not start_str:
                return None
            start = to_utc(dateparser.isoparse(start_str), default_tz)
            end_str = end_block.get("dateTime")
            end = (
                to_utc(dateparser.isoparse(end_str), default_tz)
                if end_str
                else start + timedelta(hours=1)
            )

        status = "tentative" if item.get("status") == "tentative" else "confirmed"
        organizer = (item.get("organizer") or {}).get("email")

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=item["id"]),
            title=item.get("summary") or "(no title)",
            start=start,
            end=end,
            all_day=all_day,
            description=item.get("description"),
            location=item.get("location"),
            url=item.get("htmlLink"),
            organizer=organizer,
            status=status,
            tzid=start_block.get("timeZone") or self.option("timezone", self.app.timezone),
        )

    @staticmethod
    def _self_declined(item: dict[str, Any]) -> bool:
        for attendee in item.get("attendees") or []:
            if attendee.get("self") and attendee.get("responseStatus") == "declined":
                return True
        return False
