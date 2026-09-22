"""Calendar-name matching for the EventKit source.

EventKit itself cannot run here, so the store is stubbed. What is being tested is
the name matching, which is where the real trap is: macOS returns Unicode in
either composed or decomposed form, and a Korean calendar name typed into the
config is composed. Comparing the two directly produces a "not found" error that
lists the exact name that was asked for.
"""

import sys
import unicodedata

import pytest

from calhub.config import Config, SourceConfig
from calhub.sources.base import SourceError
from calhub.sources.eventkit_source import EventKitSource


class FakeCalendar:
    def __init__(self, name):
        self._name = name

    def title(self):
        return self._name


class FakeStore:
    def __init__(self, names):
        self._calendars = [FakeCalendar(n) for n in names]

    def calendarsForEntityType_(self, _entity):
        return self._calendars


def select(available, wanted):
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    options = {} if wanted is None else {"calendars": wanted}
    source = EventKitSource(SourceConfig(id="mac", kind="eventkit", options=options), app)
    return [str(c.title()) for c in source._select_calendars(FakeStore(available))]


MAC_CALENDARS = ["직장", "대한민국 공휴일", "생일", "Calendar", "집"]


class TestUnicodeNormalisation:
    def test_composed_config_matches_decomposed_calendar(self):
        decomposed = [unicodedata.normalize("NFD", n) for n in MAC_CALENDARS]
        assert select(decomposed, ["직장"]) == [unicodedata.normalize("NFD", "직장")]

    def test_decomposed_config_matches_composed_calendar(self):
        wanted = unicodedata.normalize("NFD", "직장")
        assert select(MAC_CALENDARS, [wanted]) == ["직장"]

    def test_the_two_forms_really_are_different_strings(self):
        """Guards the premise: without folding, this test file proves nothing."""
        assert "직장" != unicodedata.normalize("NFD", "직장")


class TestSelection:
    def test_omitting_the_option_reads_every_calendar(self):
        assert select(MAC_CALENDARS, None) == MAC_CALENDARS

    def test_single_calendar_is_selected(self):
        assert select(MAC_CALENDARS, ["직장"]) == ["직장"]

    def test_several_calendars_are_selected(self):
        assert select(MAC_CALENDARS, ["직장", "집"]) == ["직장", "집"]

    def test_case_and_padding_are_ignored(self):
        assert select(MAC_CALENDARS, ["  calendar  "]) == ["Calendar"]

    def test_similar_names_are_still_distinguished(self):
        assert select(MAC_CALENDARS, ["집"]) == ["집"]


class TestErrors:
    def test_missing_calendar_is_reported_as_typed(self):
        with pytest.raises(SourceError, match="회사") as exc:
            select(MAC_CALENDARS, ["회사"])
        # The available list must be shown so the right name can be copied.
        assert "직장" in str(exc.value)

    def test_one_missing_among_several_is_named(self):
        with pytest.raises(SourceError, match="없는캘린더"):
            select(MAC_CALENDARS, ["직장", "없는캘린더"])

    def test_empty_store_is_reported(self):
        with pytest.raises(SourceError, match="no calendars visible"):
            select([], None)


@pytest.mark.skipif(sys.platform == "darwin", reason="guard only fires off macOS")
def test_source_refuses_to_run_off_macos():
    from datetime import datetime, timezone

    app = Config(sources=[], sinks=[])
    source = EventKitSource(SourceConfig(id="mac", kind="eventkit"), app)
    with pytest.raises(SourceError, match="only runs on macOS"):
        source.fetch(datetime.now(timezone.utc), datetime.now(timezone.utc))
