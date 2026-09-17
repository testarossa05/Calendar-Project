from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from calhub.config import Config, SourceConfig
from calhub.sources.base import SourceError
from calhub.sources.ics_source import IcsSource

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc
WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))


def fetch(**options):
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    cfg = SourceConfig(id="outlook", kind="ics", options=options)
    return IcsSource(cfg, app).fetch(*WINDOW)


@pytest.fixture(scope="module")
def events():
    return sorted(fetch(file=str(FIXTURES / "outlook.ics")), key=lambda e: e.start)


def test_recurring_event_is_expanded_into_occurrences(events):
    weekly = [e for e in events if "Weekly Sales" in e.title]
    assert len(weekly) == 6  # RRULE COUNT=6
    assert len({e.key for e in weekly}) == 6, "each occurrence needs its own key"


def test_kst_times_convert_to_utc(events):
    weekly = [e for e in events if "Weekly Sales" in e.title][0]
    assert weekly.start == datetime(2026, 9, 7, 0, 0, tzinfo=UTC)  # 09:00 KST


def test_all_day_event_keeps_local_day_boundaries(events):
    leave = [e for e in events if "Summer Leave" in e.title][0]
    assert leave.all_day
    # 2026-09-25 00:00 KST == 2026-09-24 15:00 UTC
    assert leave.start == datetime(2026, 9, 24, 15, 0, tzinfo=UTC)


def test_declined_event_is_skipped(events):
    assert not any("declined" in e.title.lower() for e in events)


def test_cancelled_event_is_skipped(events):
    assert not any("Cancelled" in e.title for e in events)


def test_declined_event_is_kept_when_configured():
    events = fetch(file=str(FIXTURES / "outlook.ics"), skip_declined=False)
    assert any("declined" in e.title.lower() for e in events)


NO_END_ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    "BEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:No end\r\n"
    "DTSTART;TZID=Asia/Seoul:20260921T140000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def test_zero_length_event_gets_a_visible_floor(tmp_path):
    # RFC 5545 makes a timed VEVENT with no DTEND zero-length, which no phone will
    # render. It must be widened to the configured minimum.
    ics = tmp_path / "noend.ics"
    ics.write_text(NO_END_ICS, encoding="utf-8")
    event = fetch(file=str(ics))[0]
    assert (event.end - event.start).total_seconds() == 30 * 60


def test_zero_length_floor_is_configurable(tmp_path):
    ics = tmp_path / "noend.ics"
    ics.write_text(NO_END_ICS, encoding="utf-8")
    event = fetch(file=str(ics), min_duration_minutes=45)[0]
    assert (event.end - event.start).total_seconds() == 45 * 60


def test_one_off_event_keeps_a_stable_key_when_rescheduled(tmp_path):
    """Moving a non-recurring meeting must update it, not delete and recreate it."""
    before = tmp_path / "before.ics"
    after = tmp_path / "after.ics"
    template = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
        "BEGIN:VEVENT\r\nUID:stable-uid-1\r\nSUMMARY:Review\r\n"
        "DTSTART;TZID=Asia/Seoul:20260921T{t}0000\r\n"
        "DTEND;TZID=Asia/Seoul:20260921T{e}0000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    before.write_text(template.format(t="14", e="15"), encoding="utf-8")
    after.write_text(template.format(t="16", e="17"), encoding="utf-8")
    assert fetch(file=str(before))[0].key == fetch(file=str(after))[0].key


def test_recurring_occurrences_get_distinct_keys(tmp_path):
    ics = tmp_path / "rec.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
        "BEGIN:VEVENT\r\nUID:series-1\r\nSUMMARY:Standup\r\n"
        "DTSTART;TZID=Asia/Seoul:20260907T090000\r\n"
        "DTEND;TZID=Asia/Seoul:20260907T091500\r\n"
        "RRULE:FREQ=WEEKLY;COUNT=3\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )
    events = fetch(file=str(ics))
    assert len(events) == 3
    assert len({e.key for e in events}) == 3


def test_missing_file_is_reported_clearly():
    with pytest.raises(SourceError, match="ics file not found"):
        fetch(file="/nonexistent/path.ics")


def test_no_url_or_file_is_reported():
    with pytest.raises(SourceError, match="either 'url' or 'file'"):
        fetch()


def test_unparseable_feed_is_reported(tmp_path):
    bad = tmp_path / "bad.ics"
    bad.write_text("this is not a calendar", encoding="utf-8")
    with pytest.raises(SourceError, match="could not parse"):
        fetch(file=str(bad))
