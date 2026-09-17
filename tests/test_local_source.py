"""Local anniversary/milestone source tests.

These dates are the whole point of the source, so the arithmetic is checked
against independently computed values rather than against the implementation.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from calhub.config import Config, SourceConfig
from calhub.sources.base import SourceError
from calhub.sources.local_source import LocalSource
from calhub.util import get_tz

UTC = timezone.utc
KST = get_tz("Asia/Seoul")


def fetch(tmp_path, body, start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2027, 1, 1, tzinfo=UTC)):
    path = tmp_path / "events.yaml"
    path.write_text(body, encoding="utf-8")
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    source = LocalSource(SourceConfig(id="family", kind="local", options={"file": str(path)}), app)
    return sorted(source.fetch(start, end), key=lambda e: e.start)


def local_date(event):
    return event.start.astimezone(KST).date()


class TestAnnual:
    def test_anniversary_count_increments_each_year(self, tmp_path):
        body = """
events:
  - title: 결혼기념일
    date: 2019-05-20
    annual: true
    anniversary_label: "{n}주년"
"""
        event = fetch(tmp_path, body)[0]
        assert event.title == "결혼기념일 (7주년)"  # 2026 - 2019
        assert local_date(event) == date(2026, 5, 20)
        assert event.all_day

    def test_birth_year_renders_as_zero(self, tmp_path):
        body = """
events:
  - title: 예나 생일
    date: 2024-02-14
    annual: true
    anniversary_label: "{n}살"
"""
        events = fetch(
            tmp_path, body, datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)
        )
        assert events[0].title == "예나 생일 (0살)"

    def test_years_before_the_base_date_are_not_generated(self, tmp_path):
        body = """
events:
  - title: 결혼기념일
    date: 2019-05-20
    annual: true
"""
        events = fetch(
            tmp_path, body, datetime(2015, 1, 1, tzinfo=UTC), datetime(2018, 1, 1, tzinfo=UTC)
        )
        assert events == []

    def test_label_is_optional(self, tmp_path):
        body = """
events:
  - title: 결혼기념일
    date: 2019-05-20
    annual: true
"""
        assert fetch(tmp_path, body)[0].title == "결혼기념일"

    def test_a_window_spanning_two_years_yields_both_occurrences(self, tmp_path):
        body = """
events:
  - title: 생일
    date: 2000-03-10
    annual: true
"""
        events = fetch(
            tmp_path, body, datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 6, 1, tzinfo=UTC)
        )
        assert [local_date(e) for e in events] == [date(2026, 3, 10), date(2027, 3, 10)]

    def test_each_occurrence_has_its_own_stable_key(self, tmp_path):
        body = """
events:
  - title: 생일
    date: 2000-03-10
    annual: true
"""
        window = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2028, 1, 1, tzinfo=UTC))
        first = fetch(tmp_path, body, *window)
        second = fetch(tmp_path, body, *window)
        assert len({e.key for e in first}) == len(first)
        assert [e.key for e in first] == [e.key for e in second]


class TestLeapYear:
    def test_29_february_folds_to_the_28th_in_a_common_year(self, tmp_path):
        body = """
events:
  - title: 윤일 생일
    date: 2016-02-29
    annual: true
"""
        # 2026 is not a leap year.
        event = fetch(tmp_path, body)[0]
        assert local_date(event) == date(2026, 2, 28)

    def test_29_february_is_kept_in_a_leap_year(self, tmp_path):
        body = """
events:
  - title: 윤일 생일
    date: 2016-02-29
    annual: true
"""
        event = fetch(
            tmp_path, body, datetime(2028, 1, 1, tzinfo=UTC), datetime(2029, 1, 1, tzinfo=UTC)
        )[0]
        assert local_date(event) == date(2028, 2, 29)


class TestMilestones:
    def test_day_one_is_the_start_date_itself(self, tmp_path):
        body = """
events:
  - title: 우리
    date: 2026-03-01
    day_milestones: [1]
    milestone_label: "만난 지 {n}일"
"""
        event = fetch(tmp_path, body)[0]
        assert local_date(event) == date(2026, 3, 1)
        assert event.title == "우리 만난 지 1일"

    def test_milestone_matches_independently_computed_date(self, tmp_path):
        base = date(2015, 3, 1)
        body = """
events:
  - title: 우리
    date: 2015-03-01
    day_milestones: [4000]
    milestone_label: "만난 지 {n}일"
"""
        event = fetch(tmp_path, body)[0]
        assert local_date(event) == base + timedelta(days=3999)

    def test_milestones_outside_the_window_are_dropped(self, tmp_path):
        body = """
events:
  - title: 우리
    date: 2015-03-01
    day_milestones: [100, 4000, 9000]
"""
        events = fetch(tmp_path, body)
        assert len(events) == 1  # only the 4000th lands in 2026

    def test_too_many_milestones_is_rejected(self, tmp_path):
        body = f"""
events:
  - title: 우리
    date: 2015-03-01
    day_milestones: {list(range(1, 400))}
"""
        with pytest.raises(SourceError, match="exceeds the limit"):
            fetch(tmp_path, body)

    def test_non_integer_milestone_is_reported(self, tmp_path):
        body = """
events:
  - title: 우리
    date: 2015-03-01
    day_milestones: ["삼백"]
"""
        with pytest.raises(SourceError, match="not an integer"):
            fetch(tmp_path, body)


class TestOneOff:
    def test_timed_entry(self, tmp_path):
        body = """
events:
  - title: 어린이집 상담
    date: 2026-10-15
    start: "14:00"
    end: "15:00"
    location: 직장 어린이집
"""
        event = fetch(tmp_path, body)[0]
        assert not event.all_day
        assert event.start == datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # 14:00 KST
        assert event.end == datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
        assert event.location == "직장 어린이집"

    def test_entry_without_end_gets_an_hour(self, tmp_path):
        body = """
events:
  - title: 미팅
    date: 2026-10-15
    start: "14:00"
"""
        event = fetch(tmp_path, body)[0]
        assert (event.end - event.start).total_seconds() == 3600

    def test_entry_crossing_midnight_rolls_to_the_next_day(self, tmp_path):
        body = """
events:
  - title: 야간
    date: 2026-10-15
    start: "23:00"
    end: "01:00"
"""
        event = fetch(tmp_path, body)[0]
        assert (event.end - event.start).total_seconds() == 2 * 3600

    def test_all_day_entry_covers_exactly_one_local_day(self, tmp_path):
        body = """
events:
  - title: 가족 여행
    date: 2026-11-07
"""
        event = fetch(tmp_path, body)[0]
        assert event.all_day
        assert local_date(event) == date(2026, 11, 7)
        assert (event.end - event.start).total_seconds() == 24 * 3600

    def test_one_off_outside_the_window_is_dropped(self, tmp_path):
        body = """
events:
  - title: 지난 일
    date: 2020-01-01
"""
        assert fetch(tmp_path, body) == []


class TestErrors:
    def test_missing_file_is_reported(self, tmp_path):
        app = Config(sources=[], sinks=[])
        source = LocalSource(
            SourceConfig(id="family", kind="local", options={"file": str(tmp_path / "gone.yaml")}),
            app,
        )
        with pytest.raises(SourceError, match="events file not found"):
            source.fetch(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))

    def test_missing_title_is_reported_with_its_position(self, tmp_path):
        with pytest.raises(SourceError, match=r"events\[0\].*'title'"):
            fetch(tmp_path, "events:\n  - date: 2026-05-20\n")

    def test_missing_date_is_reported_with_its_position(self, tmp_path):
        with pytest.raises(SourceError, match=r"events\[0\].*'date'"):
            fetch(tmp_path, "events:\n  - title: x\n")

    def test_bad_date_is_reported(self, tmp_path):
        with pytest.raises(SourceError, match="YYYY-MM-DD"):
            fetch(tmp_path, "events:\n  - title: x\n    date: 언젠가\n")

    def test_bad_time_is_reported(self, tmp_path):
        body = 'events:\n  - title: x\n    date: 2026-05-20\n    start: "저녁"\n'
        with pytest.raises(SourceError, match="HH:MM"):
            fetch(tmp_path, body)

    def test_malformed_yaml_is_reported(self, tmp_path):
        with pytest.raises(SourceError, match="could not parse"):
            fetch(tmp_path, "events:\n  - title: [unclosed\n")

    def test_bare_list_at_top_level_is_accepted(self, tmp_path):
        events = fetch(tmp_path, "- title: 생일\n  date: 2026-05-20\n")
        assert events[0].title == "생일"


def test_example_file_parses(tmp_path):
    """The shipped example must stay valid."""
    from pathlib import Path

    example = Path(__file__).parent.parent / "events.example.yaml"
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    source = LocalSource(
        SourceConfig(id="family", kind="local", options={"file": str(example)}), app
    )
    events = source.fetch(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    titles = [e.title for e in events]
    assert "결혼기념일 (7주년)" in titles
    assert "예나 생일 (2살)" in titles
    assert any("만난 지" in t for t in titles)
