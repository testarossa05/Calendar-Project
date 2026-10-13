"""CalDAV sink tests.

No iCloud account is reachable here, so the CalDAV layer is replaced by an
in-memory server that behaves the way a real one does on the points that matter:
save_event upserts on UID, search returns stored resources, and delete removes
one. What is being tested is the sink's own logic -- which events it decides to
create, update, leave alone or remove.
"""

from datetime import datetime, timedelta, timezone

import icalendar
import pytest

from calhub.config import Config, SinkConfig
from calhub.icalgen import event_uid
from calhub.models import Event, SourceRef
from calhub.sinks.base import SinkError
from calhub.sinks.caldav_sink import CalDavSink

UTC = timezone.utc
WINDOW = (datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


def make_event(title="POSCO Kickoff", uid="1", **kw):
    start = kw.pop("start", datetime(2026, 10, 15, 5, 0, tzinfo=UTC))
    return Event(
        ref=SourceRef(source_id="family", kind="local", uid=uid),
        title=title,
        start=start,
        end=kw.pop("end", start + timedelta(hours=1)),
        **kw,
    )


class FakeResource:
    def __init__(self, server, data):
        self.server = server
        self.data = data

    def delete(self):
        self.server.resources = [r for r in self.server.resources if r is not self]


class FakeCalendar:
    def __init__(self, name):
        self.name = name
        self.resources: list[FakeResource] = []

    def get_display_name(self):
        return self.name

    def save_event(self, ical_text):
        """Upsert on UID, which is what a CalDAV server does."""
        uid = _uid_of(ical_text)
        for existing in self.resources:
            if _uid_of(existing.data) == uid:
                existing.data = ical_text
                return existing
        resource = FakeResource(self, ical_text)
        self.resources.append(resource)
        return resource

    def search(self, start=None, end=None, event=None):
        return list(self.resources)

    def add_foreign(self, summary):
        """An event a human created: no UID or X- properties from this tool."""
        text = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//human//EN\r\n"
            f"BEGIN:VEVENT\r\nUID:handmade-1\r\nSUMMARY:{summary}\r\n"
            "DTSTART;TZID=Asia/Seoul:20261015T140000\r\n"
            "DTEND;TZID=Asia/Seoul:20261015T150000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        self.resources.append(FakeResource(self, text.encode()))


class FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars
        self.created: list[str] = []

    def calendars(self):
        return self._calendars

    def make_calendar(self, name):
        self.created.append(name)
        calendar = FakeCalendar(name)
        self._calendars.append(calendar)
        return calendar


def _uid_of(data) -> str:
    text = data.decode() if isinstance(data, bytes) else data
    calendar = icalendar.Calendar.from_ical(text)
    for component in calendar.walk("VEVENT"):
        return str(component.get("UID", ""))
    return ""


def build(monkeypatch, calendars=None, **options):
    calendars = calendars if calendars is not None else [FakeCalendar("Unified")]
    principal = FakePrincipal(calendars)

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def principal(self):
            return principal

    import caldav

    monkeypatch.setattr(caldav, "DAVClient", FakeClient)
    opts = {
        "username": "key@example.com",
        "password": "abcd-efgh-ijkl-mnop",
        "calendar_name": "Unified",
        **options,
    }
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    return CalDavSink(SinkConfig(kind="caldav", options=opts), app), principal


class TestCalendarSelection:
    def test_existing_calendar_is_used(self, monkeypatch):
        sink, principal = build(monkeypatch)
        sink.apply(sink.plan([make_event()], *WINDOW))
        assert principal.created == []

    def test_missing_calendar_is_created(self, monkeypatch):
        sink, principal = build(monkeypatch, calendars=[FakeCalendar("Home")])
        sink.apply(sink.plan([make_event()], *WINDOW))
        assert principal.created == ["Unified"]

    def test_creation_can_be_refused(self, monkeypatch):
        sink, _ = build(
            monkeypatch, calendars=[FakeCalendar("Home")], create_if_missing=False
        )
        with pytest.raises(SinkError, match="not found and create_if_missing"):
            sink.plan([], *WINDOW)

    def test_korean_calendar_name_matches_either_unicode_form(self, monkeypatch):
        import unicodedata

        decomposed = unicodedata.normalize("NFD", "통합")
        sink, principal = build(
            monkeypatch, calendars=[FakeCalendar(decomposed)], calendar_name="통합"
        )
        sink.plan([], *WINDOW)
        assert principal.created == []


class TestIdempotency:
    def test_new_event_is_created(self, monkeypatch):
        sink, _ = build(monkeypatch)
        plan = sink.plan([make_event()], *WINDOW)
        assert len(plan.to_create) == 1

    def test_second_run_changes_nothing(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        events = [make_event()]

        sink, _ = build(monkeypatch, calendars=[calendar])
        sink.apply(sink.plan(events, *WINDOW))
        assert len(calendar.resources) == 1

        sink2, _ = build(monkeypatch, calendars=[calendar])
        plan = sink2.plan(events, *WINDOW)
        assert (len(plan.to_create), len(plan.to_update), plan.unchanged) == (0, 0, 1)
        sink2.apply(plan)
        assert len(calendar.resources) == 1, "a rerun must not duplicate"

    def test_edited_event_is_updated_in_place(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        sink, _ = build(monkeypatch, calendars=[calendar])
        sink.apply(sink.plan([make_event(title="Before")], *WINDOW))

        sink2, _ = build(monkeypatch, calendars=[calendar])
        plan = sink2.plan([make_event(title="After")], *WINDOW)
        assert len(plan.to_update) == 1
        sink2.apply(plan)
        assert len(calendar.resources) == 1
        assert b"After" in calendar.resources[0].data

    def test_vanished_event_is_removed(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        sink, _ = build(monkeypatch, calendars=[calendar])
        sink.apply(sink.plan([make_event(title="Cancelled thing")], *WINDOW))

        sink2, _ = build(monkeypatch, calendars=[calendar])
        plan = sink2.plan([], *WINDOW)
        assert [s for _, s in plan.to_delete] == ["Cancelled thing"]
        sink2.apply(plan)
        assert calendar.resources == []

    def test_uid_is_stable_across_runs(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        event = make_event()
        sink, _ = build(monkeypatch, calendars=[calendar])
        sink.apply(sink.plan([event], *WINDOW))
        assert _uid_of(calendar.resources[0].data) == event_uid(event)


class TestHandMadeEvents:
    def test_an_event_added_by_hand_is_never_removed(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        calendar.add_foreign("My own note")
        sink, _ = build(monkeypatch, calendars=[calendar])
        plan = sink.plan([], *WINDOW)
        assert plan.to_delete == []
        sink.apply(plan)
        assert len(calendar.resources) == 1

    def test_hand_made_and_managed_events_coexist(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        calendar.add_foreign("My own note")
        sink, _ = build(monkeypatch, calendars=[calendar])
        sink.apply(sink.plan([make_event()], *WINDOW))
        assert len(calendar.resources) == 2

        sink2, _ = build(monkeypatch, calendars=[calendar])
        sink2.apply(sink2.plan([], *WINDOW))
        summaries = [_uid_of(r.data) for r in calendar.resources]
        assert summaries == ["handmade-1"], "only the managed event may be removed"


class TestAllDay:
    def test_all_day_is_written_as_dates_in_the_display_zone(self, monkeypatch):
        calendar = FakeCalendar("Unified")
        sink, _ = build(monkeypatch, calendars=[calendar])
        event = make_event(
            title="Summer Leave",
            all_day=True,
            start=datetime(2026, 10, 14, 15, 0, tzinfo=UTC),  # 10-15 00:00 KST
            end=datetime(2026, 10, 17, 15, 0, tzinfo=UTC),
        )
        sink.apply(sink.plan([event], *WINDOW))
        vevent = list(
            icalendar.Calendar.from_ical(calendar.resources[0].data).walk("VEVENT")
        )[0]
        assert vevent["DTSTART"].dt == datetime(2026, 10, 15).date()
        assert vevent["DTEND"].dt == datetime(2026, 10, 18).date()


def test_required_options_are_enforced():
    app = Config(sources=[], sinks=[])
    with pytest.raises(ValueError, match="username"):
        CalDavSink(SinkConfig(kind="caldav", options={}), app)
    with pytest.raises(ValueError, match="calendar_name"):
        CalDavSink(
            SinkConfig(kind="caldav", options={"username": "u", "password": "p"}), app
        )
