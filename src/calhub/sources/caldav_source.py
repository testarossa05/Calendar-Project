"""CalDAV source -- used for iCloud, and for any other CalDAV server.

For iCloud you need an app-specific password (appleid.apple.com -> Sign-In and
Security -> App-Specific Passwords). Your normal Apple ID password will not work
while two-factor authentication is on, which it always is.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import icalendar

from ..models import Event, SourceRef
from ..util import fold_name, get_tz, is_date_only, to_utc
from .base import Source, SourceError

ICLOUD_URL = "https://caldav.icloud.com/"


class CalDavSource(Source):
    kind = "caldav"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        import caldav

        url = self.option("url", ICLOUD_URL)
        username = self.option("username", required=True)
        password = self.option("password", required=True)
        wanted = self.option("calendars")  # list of display names; None -> all
        default_tz = get_tz(self.option("timezone", self.app.timezone))

        try:
            client = caldav.DAVClient(url=url, username=username, password=password)
            principal = client.principal()
            calendars = principal.calendars()
        except Exception as exc:
            raise SourceError(
                self.cfg.id,
                f"CalDAV connection failed: {exc}. For iCloud, confirm you are using an "
                "app-specific password.",
            ) from exc

        if wanted:
            wanted_map = {fold_name(w): str(w) for w in wanted}
            calendars = [c for c in calendars if fold_name(_display_name(c)) in wanted_map]
            found = {fold_name(_display_name(c)) for c in calendars}
            missing = [original for key, original in wanted_map.items() if key not in found]
            if missing:
                available = ", ".join(sorted(_display_name(c) for c in principal.calendars()))
                raise SourceError(
                    self.cfg.id,
                    f"calendar(s) not found: {sorted(missing)}. Available: {available}",
                )

        events: list[Event] = []
        for calendar in calendars:
            cal_name = _display_name(calendar)
            try:
                # expand=True asks the server to materialise recurrences in-window.
                results = calendar.search(
                    start=window_start, end=window_end, event=True, expand=True
                )
            except Exception as exc:
                raise SourceError(
                    self.cfg.id, f"search failed on calendar {cal_name!r}: {exc}"
                ) from exc

            for result in results:
                events.extend(self._parse(result, cal_name, default_tz))
        return events

    def _parse(self, result: Any, cal_name: str, default_tz) -> list[Event]:
        try:
            raw = result.data
        except Exception:
            return []
        try:
            calendar = icalendar.Calendar.from_ical(raw)
        except Exception:
            return []

        out: list[Event] = []
        for component in calendar.walk("VEVENT"):
            if str(component.get("STATUS", "")).upper() == "CANCELLED":
                continue
            dtstart = component.get("DTSTART")
            if dtstart is None:
                continue
            start_raw = dtstart.dt
            all_day = is_date_only(start_raw)

            dtend = component.get("DTEND")
            if dtend is not None:
                end_raw = dtend.dt
            elif component.get("DURATION") is not None:
                end_raw = start_raw + component.get("DURATION").dt
            else:
                end_raw = start_raw + (timedelta(days=1) if all_day else timedelta(hours=1))

            start = to_utc(start_raw, default_tz)
            end = to_utc(end_raw, default_tz)

            uid = str(component.get("UID", "")) or f"{cal_name}|{start.isoformat()}"
            recurrence_id = component.get("RECURRENCE-ID")
            if recurrence_id is not None or component.get("RRULE") is not None:
                uid = f"{uid}|{start.isoformat()}"

            out.append(
                Event(
                    ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=uid),
                    title=str(component.get("SUMMARY", "(no title)")),
                    start=start,
                    end=end,
                    all_day=all_day,
                    description=_text(component.get("DESCRIPTION")),
                    location=_text(component.get("LOCATION")),
                    status="confirmed",
                    tzid=self.option("timezone", self.app.timezone),
                    extra={"calendar": cal_name},
                )
            )
        return out


def _display_name(calendar) -> str:
    try:
        name = calendar.get_display_name()
    except Exception:
        name = None
    return str(name or getattr(calendar, "name", "") or "")


def _text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
