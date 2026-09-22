"""Google Calendar sink -- mirrors the merged event set into one calendar.

Every event written carries two private extended properties:

* ``calhub_owner``  -- marks the event as managed by this tool.
* ``calhub_key``    -- the stable source key, so a later run updates rather than
  duplicates.
* ``calhub_hash``   -- a content hash, so unchanged events cost no API calls.

Anything in the target calendar *without* ``calhub_owner`` was put there by a
human and is never modified or deleted. That invariant is what makes it safe to
point this at a calendar you also use by hand.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from ..gauth import AuthError, build_service
from ..models import MANAGED_MARKER, OWNER_TAG, Event, SyncPlan
from ..util import get_tz
from .base import Sink, SinkError

# Google rejects a colour id outside 1..11.
_VALID_COLORS = {str(i) for i in range(1, 12)}


class GoogleSink(Sink):
    kind = "google"

    def __init__(self, cfg, app) -> None:
        super().__init__(cfg, app)
        self._service = None
        self._existing: dict[str, dict[str, Any]] = {}
        self._calendar_id = str(self.option("calendar_id", required=True))

    @property
    def service(self):
        if self._service is None:
            try:
                self._service = build_service(self.cfg.options, readonly=False)
            except AuthError as exc:
                raise SinkError(str(exc)) from exc
        return self._service

    def _load_existing(self, window_start: datetime, window_end: datetime) -> None:
        """Index the events this tool already owns inside the window."""
        existing: dict[str, dict[str, Any]] = {}
        page_token: Optional[str] = None
        try:
            while True:
                response = (
                    self.service.events()
                    .list(
                        calendarId=self._calendar_id,
                        timeMin=window_start.isoformat(),
                        timeMax=window_end.isoformat(),
                        singleEvents=True,
                        showDeleted=False,
                        maxResults=2500,
                        privateExtendedProperty=f"calhub_owner={OWNER_TAG}",
                        pageToken=page_token,
                    )
                    .execute()
                )
                for item in response.get("items", []):
                    private = (item.get("extendedProperties") or {}).get("private") or {}
                    key = private.get("calhub_key")
                    if not key:
                        continue
                    # A duplicate key means an earlier run was interrupted midway.
                    # Keep the first and let the rest be deleted as orphans.
                    if key in existing:
                        existing[f"__dupe__{item['id']}"] = item
                        continue
                    existing[key] = item
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except SinkError:
            raise
        except Exception as exc:
            raise SinkError(
                f"could not read target calendar {self._calendar_id!r}: {exc}. "
                "Check the calendar id and that the credential has write access to it."
            ) from exc
        self._existing = existing

    def plan(self, events: list[Event], window_start, window_end) -> SyncPlan:
        self._load_existing(window_start, window_end)
        plan = SyncPlan()
        desired_keys: set[str] = set()

        for event in events:
            desired_keys.add(event.key)
            current = self._existing.get(event.key)
            if current is None:
                plan.to_create.append(event)
                continue
            private = (current.get("extendedProperties") or {}).get("private") or {}
            if private.get("calhub_hash") == event.content_hash():
                plan.unchanged += 1
            else:
                plan.to_update.append((current["id"], event))

        for key, item in self._existing.items():
            if key.startswith("__dupe__") or key not in desired_keys:
                plan.to_delete.append((item["id"], item.get("summary") or "(no title)"))

        return plan

    def apply(self, plan: SyncPlan) -> dict[str, int]:
        created = updated = deleted = failed = 0

        for event in plan.to_create:
            try:
                self.service.events().insert(
                    calendarId=self._calendar_id, body=self._body(event)
                ).execute()
                created += 1
            except Exception as exc:
                failed += 1
                _warn(f"create failed for {event.title!r}: {exc}")

        for event_id, event in plan.to_update:
            try:
                self.service.events().update(
                    calendarId=self._calendar_id, eventId=event_id, body=self._body(event)
                ).execute()
                updated += 1
            except Exception as exc:
                failed += 1
                _warn(f"update failed for {event.title!r}: {exc}")

        for event_id, summary in plan.to_delete:
            try:
                self.service.events().delete(
                    calendarId=self._calendar_id, eventId=event_id
                ).execute()
                deleted += 1
            except Exception as exc:
                # A 410 means it is already gone, which is the state we wanted.
                if "410" in str(exc):
                    deleted += 1
                    continue
                failed += 1
                _warn(f"delete failed for {summary!r}: {exc}")

        return {
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "failed": failed,
            "unchanged": plan.unchanged,
        }

    def _body(self, event: Event) -> dict[str, Any]:
        display_tz = str(self.option("timezone", self.app.timezone))
        zone = get_tz(display_tz)

        if event.all_day:
            start_block = {"date": event.start.astimezone(zone).date().isoformat()}
            end_block = {"date": event.end.astimezone(zone).date().isoformat()}
        else:
            start_block = {"dateTime": event.start.isoformat(), "timeZone": "UTC"}
            end_block = {"dateTime": event.end.isoformat(), "timeZone": "UTC"}

        body: dict[str, Any] = {
            "summary": event.title,
            "start": start_block,
            "end": end_block,
            "description": _compose_description(event),
            "status": "tentative" if event.status == "tentative" else "confirmed",
            # The mirror must never send invitations or alter anyone's free/busy
            # beyond the owner's own calendar.
            "transparency": "transparent" if event.status == "tentative" else "opaque",
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {
                "private": {
                    "calhub_owner": OWNER_TAG,
                    "calhub_key": event.key,
                    "calhub_hash": event.content_hash(),
                    "calhub_source": event.ref.source_id,
                }
            },
        }
        if event.location:
            body["location"] = event.location

        color = self.option("source_colors", {}).get(event.ref.source_id)
        if color is not None:
            if str(color) not in _VALID_COLORS:
                raise SinkError(
                    f"source_colors[{event.ref.source_id}] = {color!r} is invalid; "
                    "Google accepts colour ids '1'..'11'."
                )
            body["colorId"] = str(color)
        return body


def _compose_description(event: Event) -> str:
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


def _warn(message: str) -> None:
    import sys

    print(f"  ! {message}", file=sys.stderr)
