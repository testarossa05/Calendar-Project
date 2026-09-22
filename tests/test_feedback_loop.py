"""The tool must not re-ingest its own output.

Subscribing to the merged calendar on the same Mac that EventKit reads, or
importing the .ics into Calendar.app to check it, puts calhub's own events back
in front of it. Without a guard every sync re-prefixes them and writes them
again, so a single event multiplies on each run.
"""

from datetime import datetime, timedelta, timezone

from calhub.config import Config, SinkConfig, SourceConfig
from calhub.models import MANAGED_MARKER, Event, SourceRef
from calhub.sinks.ics_file import IcsFileSink
from calhub.sources.ics_source import IcsSource

UTC = timezone.utc
WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC))


def make_events():
    start = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)
    return [
        Event(
            ref=SourceRef(source_id="family", kind="local", uid="a"),
            title="[가족] 어린이집 상담",
            start=start,
            end=start + timedelta(hours=1),
        ),
        Event(
            ref=SourceRef(source_id="family", kind="local", uid="b"),
            title="[가족] 가족 여행",
            start=datetime(2026, 11, 6, 15, 0, tzinfo=UTC),
            end=datetime(2026, 11, 7, 15, 0, tzinfo=UTC),
            all_day=True,
        ),
    ]


def write_unified(tmp_path):
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    out = tmp_path / "unified.ics"
    sink = IcsFileSink(SinkConfig(kind="ics_file", options={"path": str(out)}), app)
    sink.apply(sink.plan(make_events(), *WINDOW))
    return out


def read_back(path):
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    source = IcsSource(SourceConfig(id="mac", kind="ics", options={"file": str(path)}), app)
    return source.fetch(*WINDOW)


def test_output_carries_the_marker(tmp_path):
    text = write_unified(tmp_path).read_text(encoding="utf-8")
    assert MANAGED_MARKER in text


def test_reading_our_own_output_yields_nothing(tmp_path):
    assert read_back(write_unified(tmp_path)) == []


def test_a_foreign_calendar_is_still_read(tmp_path):
    """The guard must key on the marker, not on anything incidental."""
    foreign = tmp_path / "foreign.ics"
    foreign.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//other//EN\r\n"
        "BEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Real meeting\r\n"
        "DESCRIPTION:Source: somewhere else\r\n"
        "DTSTART;TZID=Asia/Seoul:20261015T140000\r\n"
        "DTEND;TZID=Asia/Seoul:20261015T150000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    assert [e.title for e in read_back(foreign)] == ["Real meeting"]


def test_round_trip_does_not_multiply_events(tmp_path):
    """The failure this prevents: each sync re-adding its own previous output."""
    unified = write_unified(tmp_path)
    first = unified.read_text(encoding="utf-8").count("BEGIN:VEVENT")

    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    for _ in range(3):
        fed_back = read_back(unified)
        sink = IcsFileSink(SinkConfig(kind="ics_file", options={"path": str(unified)}), app)
        sink.apply(sink.plan(make_events() + fed_back, *WINDOW))

    assert unified.read_text(encoding="utf-8").count("BEGIN:VEVENT") == first
