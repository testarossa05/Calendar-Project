"""The unified event model every source is normalised into."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .util import normalize_title, sha1

# Marker written into the target calendar so a later run can tell which events it
# owns. Anything in the target calendar without this marker was created by a human
# and is never touched.
OWNER_TAG = "calhub"

# Written into the description of every event this tool emits, and skipped on the
# way back in. Without it, importing or subscribing to the unified calendar on the
# same Mac that EventKit reads makes the tool re-ingest its own output: every sync
# re-prefixes the events and writes them again.
MANAGED_MARKER = "[calhub]"


@dataclass(frozen=True)
class SourceRef:
    """Where an event came from."""

    source_id: str
    kind: str  # ics | notion | google | caldav
    uid: str  # native identifier within that source

    @property
    def key(self) -> str:
        """Stable, collision-free key used to correlate source -> target event."""
        return f"{self.source_id}:{sha1(self.uid)[:20]}"


@dataclass
class Event:
    """A calendar event, normalised. ``start``/``end`` are always aware UTC."""

    ref: SourceRef
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    description: Optional[str] = None
    location: Optional[str] = None
    url: Optional[str] = None
    organizer: Optional[str] = None
    status: str = "confirmed"  # confirmed | tentative | cancelled
    # Display timezone of the originating calendar, kept so the target can render
    # all-day events on the right local day.
    tzid: str = "UTC"
    # The title as it arrived from the source, kept intact for duplicate matching.
    # Presentation rules (prefixes, "busy" masking) rewrite ``title`` but must never
    # change how two copies of the same meeting are matched against each other.
    match_title: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end < self.start:
            self.end = self.start

    @property
    def key(self) -> str:
        return self.ref.key

    @property
    def dedupe_key(self) -> tuple[str, int, int]:
        """Identity used to spot the same meeting arriving from two sources.

        Start/end are bucketed to the minute: two clients can disagree by seconds
        on the same meeting, and that must not defeat the match.
        """
        return (
            normalize_title(self.match_title or self.title),
            int(self.start.timestamp()) // 60,
            int(self.end.timestamp()) // 60,
        )

    def content_hash(self) -> str:
        """Hash of everything mirrored to the target, so no-op updates are skipped."""
        payload = json.dumps(
            {
                "t": self.title,
                "s": self.start.isoformat(),
                "e": self.end.isoformat(),
                "a": self.all_day,
                "d": self.description or "",
                "l": self.location or "",
                "u": self.url or "",
                "st": self.status,
                "tz": self.tzid,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return sha1(payload)[:16]


@dataclass
class SyncPlan:
    """What a sync run intends to do. Produced before anything is written."""

    to_create: list[Event] = field(default_factory=list)
    to_update: list[tuple[str, Event]] = field(default_factory=list)  # (target_id, event)
    to_delete: list[tuple[str, str]] = field(default_factory=list)  # (target_id, summary)
    unchanged: int = 0
    duplicates_dropped: int = 0

    @property
    def is_empty(self) -> bool:
        return not (self.to_create or self.to_update or self.to_delete)

    def summary(self) -> str:
        return (
            f"create={len(self.to_create)} update={len(self.to_update)} "
            f"delete={len(self.to_delete)} unchanged={self.unchanged} "
            f"dupes_dropped={self.duplicates_dropped}"
        )
