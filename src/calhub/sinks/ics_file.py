"""Merged .ics file sink.

Writes every event into a single iCalendar file. Host that file anywhere with a
stable URL -- GitHub Pages is free and works well -- and subscribe to it on the
iPhone with Settings -> Calendar -> Accounts -> Add Account -> Other -> Add
Subscribed Calendar.

This path needs no Apple Developer account, no App Store review, and no
administrator consent anywhere. It is read-only by design: the merged calendar is
a view, and each event still belongs to its own system.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import icalendar

from ..icalgen import PRODID, to_vevent
from ..models import SyncPlan
from ..util import UTC, get_tz
from .base import Sink, SinkError


class IcsFileSink(Sink):
    kind = "ics_file"

    def plan(self, events: list[Event], window_start, window_end) -> SyncPlan:
        # A file is rewritten wholesale, so every event is a "create" and there is
        # nothing to diff. The plan still exists so --dry-run reports consistently.
        return SyncPlan(to_create=list(events))

    def apply(self, plan: SyncPlan) -> dict[str, int]:
        path = Path(str(self.option("path", "out/unified.ics"))).expanduser()
        calendar_name = str(self.option("calendar_name", "Unified Calendar"))
        # iOS honours X-PUBLISHED-TTL as a hint for how often to re-fetch.
        ttl = str(self.option("refresh_interval", "PT15M"))
        display_tz = str(self.option("timezone", self.app.timezone))
        get_tz(display_tz)  # validate early

        calendar = icalendar.Calendar()
        calendar.add("prodid", PRODID)
        calendar.add("version", "2.0")
        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")
        calendar.add("x-wr-calname", calendar_name)
        calendar.add("x-wr-timezone", display_tz)
        calendar.add("x-published-ttl", ttl)
        calendar.add("refresh-interval;value=duration", ttl)

        stamp = datetime.now(UTC)
        for event in plan.to_create:
            calendar.add_component(to_vevent(event, stamp, display_tz))

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(calendar.to_ical())
        except OSError as exc:
            raise SinkError(f"could not write {path}: {exc}") from exc

        return {"written": len(plan.to_create), "path": str(path)}
