"""CalDAV sink -- writes the merged calendar into iCloud, or any CalDAV server.

This is the shortest route to an iPhone. It needs one app-specific password from
appleid.apple.com and nothing else: no cloud project, no service account, no
public hosting. Events land in a real calendar that iOS syncs natively, so they
arrive by push rather than on a subscription refresh interval, and they show up
in widgets, notifications and Siri like any other event.

Writes go only to a calendar this tool owns. ``calendar_name`` must name a
calendar used for nothing else; it is created on first run if missing. Within it,
only events whose UID this tool issued are ever modified or removed, so anything
added there by hand survives.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import icalendar

from ..icalgen import HASH_PROP, KEY_PROP, event_uid, read_prop, single_event_calendar
from ..models import OWNER_TAG, Event, SyncPlan
from ..util import UTC, fold_name, get_tz
from .base import Sink, SinkError

ICLOUD_URL = "https://caldav.icloud.com/"


class CalDavSink(Sink):
    kind = "caldav"

    def __init__(self, cfg, app) -> None:
        super().__init__(cfg, app)
        self._url = str(self.option("url", ICLOUD_URL))
        self._username = str(self.option("username", required=True))
        self._password = str(self.option("password", required=True))
        self._calendar_name = str(self.option("calendar_name", required=True))
        self._create_if_missing = bool(self.option("create_if_missing", True))
        self._calendar = None
        # key -> (caldav object, content hash)
        self._existing: dict[str, tuple[Any, Optional[str]]] = {}

    # -- connection ---------------------------------------------------------

    def _connect(self):
        if self._calendar is not None:
            return self._calendar
        import caldav

        try:
            client = caldav.DAVClient(
                url=self._url, username=self._username, password=self._password
            )
            principal = client.principal()
            calendars = principal.calendars()
        except Exception as exc:
            raise SinkError(
                f"CalDAV connection failed: {exc}. For iCloud, confirm you are using an "
                "app-specific password from appleid.apple.com, not your Apple ID password."
            ) from exc

        wanted = fold_name(self._calendar_name)
        for calendar in calendars:
            if fold_name(_display_name(calendar)) == wanted:
                self._calendar = calendar
                return self._calendar

        if not self._create_if_missing:
            available = ", ".join(sorted(_display_name(c) for c in calendars))
            raise SinkError(
                f"calendar {self._calendar_name!r} not found and create_if_missing is "
                f"false. Available: {available}"
            )

        try:
            self._calendar = principal.make_calendar(name=self._calendar_name)
        except Exception as exc:
            raise SinkError(
                f"could not create calendar {self._calendar_name!r}: {exc}. Create it by "
                "hand in the Calendar app and re-run, or set create_if_missing: false."
            ) from exc
        return self._calendar

    # -- planning -----------------------------------------------------------

    def _load_existing(self, window_start: datetime, window_end: datetime) -> None:
        calendar = self._connect()
        try:
            results = calendar.search(start=window_start, end=window_end, event=True)
        except Exception as exc:
            raise SinkError(f"could not list events in {self._calendar_name!r}: {exc}") from exc

        existing: dict[str, tuple[Any, Optional[str]]] = {}
        for result in results:
            key, content_hash = _read_markers(result)
            if key is None:
                continue  # added by hand; never touched
            if key in existing:
                # An interrupted run can leave a second copy; keep the first and
                # let the rest be removed as orphans.
                existing[f"__dupe__{len(existing)}:{key}"] = (result, content_hash)
                continue
            existing[key] = (result, content_hash)
        self._existing = existing

    def plan(self, events: list[Event], window_start, window_end) -> SyncPlan:
        self._load_existing(window_start, window_end)
        plan = SyncPlan()
        desired: set[str] = set()

        for event in events:
            desired.add(event.key)
            current = self._existing.get(event.key)
            if current is None:
                plan.to_create.append(event)
                continue
            _, content_hash = current
            if content_hash == event.content_hash():
                plan.unchanged += 1
            else:
                plan.to_update.append((event.key, event))

        for key, (result, _) in self._existing.items():
            if key.startswith("__dupe__") or key not in desired:
                plan.to_delete.append((key, _summary_of(result)))
        return plan

    # -- applying -----------------------------------------------------------

    def apply(self, plan: SyncPlan) -> dict[str, int]:
        calendar = self._connect()
        display_tz = str(self.option("timezone", self.app.timezone))
        get_tz(display_tz)  # validate before writing anything
        stamp = datetime.now(UTC)

        created = updated = deleted = failed = 0

        # save_event is an upsert keyed on UID, so creates and updates are the
        # same call; they are counted separately only for the report.
        for event in plan.to_create:
            try:
                calendar.save_event(single_event_calendar(event, stamp, display_tz))
                created += 1
            except Exception as exc:
                failed += 1
                _warn(f"create failed for {event.title!r}: {exc}")

        for _, event in plan.to_update:
            try:
                calendar.save_event(single_event_calendar(event, stamp, display_tz))
                updated += 1
            except Exception as exc:
                failed += 1
                _warn(f"update failed for {event.title!r}: {exc}")

        for key, summary in plan.to_delete:
            entry = self._existing.get(key)
            if entry is None:
                continue
            try:
                entry[0].delete()
                deleted += 1
            except Exception as exc:
                failed += 1
                _warn(f"delete failed for {summary!r}: {exc}")

        return {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "failed": failed,
            "unchanged": plan.unchanged,
        }


def _read_markers(result) -> tuple[Optional[str], Optional[str]]:
    """Pull this tool's key and content hash out of a stored event."""
    try:
        raw = result.data
    except Exception:
        return None, None
    try:
        calendar = icalendar.Calendar.from_ical(raw)
    except Exception:
        return None, None

    for component in calendar.walk("VEVENT"):
        key = read_prop(component, KEY_PROP)
        if key is None:
            # Fall back to the UID shape, in case a server dropped the X- property.
            uid = read_prop(component, "UID") or ""
            if uid.startswith(f"{OWNER_TAG}-") and uid.endswith("@calhub"):
                key = uid[len(OWNER_TAG) + 1 : -len("@calhub")]
        if key is not None:
            return key, read_prop(component, HASH_PROP)
    return None, None


def _summary_of(result) -> str:
    try:
        calendar = icalendar.Calendar.from_ical(result.data)
        for component in calendar.walk("VEVENT"):
            return str(component.get("SUMMARY", "(no title)"))
    except Exception:
        pass
    return "(no title)"


def _display_name(calendar) -> str:
    try:
        name = calendar.get_display_name()
    except Exception:
        name = None
    return str(name or getattr(calendar, "name", "") or "")


def _warn(message: str) -> None:
    import sys

    print(f"  ! {message}", file=sys.stderr)
