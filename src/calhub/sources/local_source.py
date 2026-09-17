"""Local file source -- anniversaries, birthdays and day-count milestones.

Written for the calendars that live inside a closed app and cannot be exported:
Between (비트윈) has a shared calendar and anniversary tracker but offers no ICS
feed, no API and no external sync, so nothing can be pulled out of it. Rather
than scrape a service that does not publish an interface, this source lets the
handful of dates that actually matter be declared in one small file and expanded
into the unified calendar.

It handles the three shapes such an app provides:

* a fixed one-off entry ("어린이집 상담, 14:00"),
* a yearly recurrence with a running count ("결혼기념일 (7주년)"),
* day-count milestones from a start date ("만난 지 1000일").
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional

import yaml
from dateutil import parser as dateparser

from ..models import Event, SourceRef
from ..util import get_tz, to_utc
from .base import Source, SourceError

# A file this size is hand-maintained; a bigger one means a mistake upstream.
_MAX_MILESTONES = 200


class LocalSource(Source):
    kind = "local"

    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        entries = self._load_entries()
        zone = get_tz(self.option("timezone", self.app.timezone))

        events: list[Event] = []
        for index, entry in enumerate(entries):
            events.extend(self._expand(entry, index, window_start, window_end, zone))
        return events

    def _load_entries(self) -> list[dict[str, Any]]:
        file_option = self.option("file", required=True)
        path = Path(str(file_option)).expanduser()
        if not path.exists():
            raise SourceError(
                self.cfg.id,
                f"events file not found: {path}. See events.example.yaml for the format.",
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SourceError(self.cfg.id, f"could not parse {path}: {exc}") from exc

        if isinstance(raw, list):
            entries = raw  # allow a bare list at the top level
        elif isinstance(raw, dict):
            entries = raw.get("events") or []
        else:
            raise SourceError(self.cfg.id, f"{path}: expected a mapping or a list")

        if not isinstance(entries, list):
            raise SourceError(self.cfg.id, f"{path}: 'events' must be a list")
        return entries

    def _expand(
        self,
        entry: Any,
        index: int,
        window_start: datetime,
        window_end: datetime,
        zone,
    ) -> list[Event]:
        where = f"events[{index}]"
        if not isinstance(entry, dict):
            raise SourceError(self.cfg.id, f"{where}: each event must be a mapping")

        title = entry.get("title")
        if not title:
            raise SourceError(self.cfg.id, f"{where}: missing required key 'title'")
        base = _parse_date(entry.get("date"), self.cfg.id, where)

        milestones = entry.get("day_milestones")
        if milestones:
            return self._expand_milestones(
                entry, base, milestones, index, window_start, window_end, zone, where
            )
        if entry.get("annual"):
            return self._expand_annual(entry, base, index, window_start, window_end, zone)
        occurrence = self._build(entry, base, base, index, zone, occurrence_kind="once")
        return [occurrence] if _overlaps(occurrence, window_start, window_end) else []

    def _expand_annual(
        self, entry: dict[str, Any], base: date, index: int, window_start, window_end, zone
    ) -> list[Event]:
        label = entry.get("anniversary_label")
        events: list[Event] = []
        # A window can straddle a year boundary, so walk one year either side of it.
        first = window_start.astimezone(zone).year - 1
        last = window_end.astimezone(zone).year + 1
        for year in range(first, last + 1):
            if year < base.year:
                continue
            occurrence_date = _shift_year(base, year)
            count = year - base.year
            title = entry["title"]
            if label:
                title = f"{title} ({_format_label(label, count)})"
            event = self._build(
                entry,
                occurrence_date,
                base,
                index,
                zone,
                occurrence_kind=f"annual-{year}",
                title_override=title,
            )
            if _overlaps(event, window_start, window_end):
                events.append(event)
        return events

    def _expand_milestones(
        self,
        entry: dict[str, Any],
        base: date,
        milestones: Any,
        index: int,
        window_start,
        window_end,
        zone,
        where: str,
    ) -> list[Event]:
        if not isinstance(milestones, list):
            raise SourceError(self.cfg.id, f"{where}: 'day_milestones' must be a list of integers")
        if len(milestones) > _MAX_MILESTONES:
            raise SourceError(
                self.cfg.id,
                f"{where}: {len(milestones)} milestones exceeds the limit of {_MAX_MILESTONES}",
            )

        label = entry.get("milestone_label", "{n}")
        events: list[Event] = []
        for value in milestones:
            try:
                days = int(value)
            except (TypeError, ValueError):
                raise SourceError(
                    self.cfg.id, f"{where}: milestone {value!r} is not an integer"
                ) from None
            # Day 1 is the start date itself, matching how these apps count.
            occurrence_date = base + timedelta(days=days - 1)
            title = f"{entry['title']} {_format_label(label, days)}".strip()
            event = self._build(
                entry,
                occurrence_date,
                base,
                index,
                zone,
                occurrence_kind=f"milestone-{days}",
                title_override=title,
            )
            if _overlaps(event, window_start, window_end):
                events.append(event)
        return events

    def _build(
        self,
        entry: dict[str, Any],
        occurrence_date: date,
        base: date,
        index: int,
        zone,
        occurrence_kind: str,
        title_override: Optional[str] = None,
    ) -> Event:
        start_time = _parse_time(entry.get("start"), self.cfg.id, index)
        end_time = _parse_time(entry.get("end"), self.cfg.id, index)
        all_day = start_time is None

        if all_day:
            start = to_utc(occurrence_date, zone)
            end = to_utc(occurrence_date + timedelta(days=1), zone)
        else:
            start = to_utc(datetime.combine(occurrence_date, start_time), zone)
            if end_time is not None:
                end_dt = datetime.combine(occurrence_date, end_time)
                if end_time <= start_time:
                    end_dt += timedelta(days=1)  # an entry crossing midnight
                end = to_utc(end_dt, zone)
            else:
                end = start + timedelta(hours=1)

        # The identity is the declaration, not the file position, so reordering the
        # file does not delete and recreate every event in the target calendar.
        uid = f"{entry['title']}|{base.isoformat()}|{occurrence_kind}"

        return Event(
            ref=SourceRef(source_id=self.cfg.id, kind=self.kind, uid=uid),
            title=title_override or str(entry["title"]),
            start=start,
            end=end,
            all_day=all_day,
            description=_optional_str(entry.get("description")),
            location=_optional_str(entry.get("location")),
            status="confirmed",
            tzid=self.option("timezone", self.app.timezone),
        )


def _format_label(label: str, n: int) -> str:
    """Render a label template. '{n}' is the count; a bare label is used as-is."""
    try:
        return str(label).format(n=n)
    except (KeyError, IndexError, ValueError):
        return str(label)


def _shift_year(base: date, year: int) -> date:
    """Move a date to another year, folding 29 February back to the 28th."""
    try:
        return base.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def _overlaps(event: Event, window_start: datetime, window_end: datetime) -> bool:
    return event.start < window_end and event.end > window_start


def _parse_date(value: Any, source_id: str, where: str) -> date:
    if value is None:
        raise SourceError(source_id, f"{where}: missing required key 'date'")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return dateparser.isoparse(str(value)).date()
    except (ValueError, TypeError):
        raise SourceError(
            source_id, f"{where}: could not read date {value!r}; use YYYY-MM-DD"
        ) from None


def _parse_time(value: Any, source_id: str, index: int) -> Optional[time]:
    if value is None:
        return None
    if isinstance(value, time):
        return value
    if isinstance(value, datetime):
        return value.time()
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise SourceError(source_id, f"events[{index}]: could not read time {value!r}; use HH:MM")


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
