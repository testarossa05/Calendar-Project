"""Graph adapter tests.

No credential is available here, so the token call and the HTTP layer are
replaced. The response payloads below are shaped exactly as Microsoft Graph
returns them for /me/calendarView, including the detail that matters most:
``start.dateTime`` carries no UTC offset and the zone arrives separately in
``start.timeZone``.
"""

from datetime import datetime, timezone

import pytest

from calhub.config import Config, SourceConfig
from calhub.sources.base import SourceError
from calhub.sources.msgraph_source import MsGraphSource

UTC = timezone.utc
WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))


def graph_event(**overrides):
    base = {
        "id": "AAMkAGI1_event_1",
        "iCalUId": "040000008200E00074C5B7101A82E008",
        "subject": "POSCO 3CC Kickoff",
        "bodyPreview": "Slab caster modernization",
        "start": {"dateTime": "2026-09-21T05:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-21T07:00:00.0000000", "timeZone": "UTC"},
        "isAllDay": False,
        "isCancelled": False,
        "location": {"displayName": "Teams"},
        "organizer": {"emailAddress": {"name": "Key", "address": "key@example.com"}},
        "webLink": "https://outlook.office365.com/calendar/item/1",
        "showAs": "busy",
        "responseStatus": {"response": "organizer"},
        "type": "singleInstance",
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = str(payload)

    def json(self):
        return self._payload


def build(monkeypatch, pages, **options):
    """Wire a source whose token and HTTP calls are replaced by canned pages."""
    monkeypatch.setattr(
        "calhub.sources.msgraph_source.acquire_token", lambda opts: "fake-token"
    )
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params, "headers": headers})
        return pages[len(calls) - 1]

    monkeypatch.setattr("calhub.sources.msgraph_source.requests.get", fake_get)
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    source = MsGraphSource(SourceConfig(id="outlook", kind="msgraph", options=options), app)
    return source, calls


def test_parses_a_normal_meeting(monkeypatch):
    source, _ = build(monkeypatch, [FakeResponse({"value": [graph_event()]})])
    event = source.fetch(*WINDOW)[0]
    assert event.title == "POSCO 3CC Kickoff"
    assert event.start == datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 9, 21, 7, 0, tzinfo=UTC)
    assert event.location == "Teams"
    assert event.organizer == "key@example.com"
    assert event.ref.uid == "AAMkAGI1_event_1"


def test_offsetless_datetime_uses_the_declared_zone(monkeypatch):
    # Graph honours Prefer: outlook.timezone, and never puts an offset in the string.
    event_payload = graph_event(
        start={"dateTime": "2026-09-21T14:00:00.0000000", "timeZone": "Asia/Seoul"},
        end={"dateTime": "2026-09-21T15:00:00.0000000", "timeZone": "Asia/Seoul"},
    )
    source, _ = build(monkeypatch, [FakeResponse({"value": [event_payload]})])
    event = source.fetch(*WINDOW)[0]
    assert event.start == datetime(2026, 9, 21, 5, 0, tzinfo=UTC)


def test_cancelled_event_is_skipped(monkeypatch):
    source, _ = build(monkeypatch, [FakeResponse({"value": [graph_event(isCancelled=True)]})])
    assert source.fetch(*WINDOW) == []


def test_series_master_is_skipped_but_occurrences_are_kept(monkeypatch):
    payload = {
        "value": [
            graph_event(id="master", type="seriesMaster"),
            graph_event(id="occ1", type="occurrence", seriesMasterId="master"),
            graph_event(id="occ2", type="occurrence", seriesMasterId="master"),
        ]
    }
    source, _ = build(monkeypatch, [FakeResponse(payload)])
    events = source.fetch(*WINDOW)
    assert [e.ref.uid for e in events] == ["occ1", "occ2"]


def test_declined_event_is_skipped_by_default(monkeypatch):
    payload = {"value": [graph_event(responseStatus={"response": "declined"})]}
    source, _ = build(monkeypatch, [FakeResponse(payload)])
    assert source.fetch(*WINDOW) == []


def test_declined_event_is_kept_when_configured(monkeypatch):
    payload = {"value": [graph_event(responseStatus={"response": "declined"})]}
    source, _ = build(monkeypatch, [FakeResponse(payload)], skip_declined=False)
    assert len(source.fetch(*WINDOW)) == 1


def test_free_time_is_mirrored_as_tentative(monkeypatch):
    source, _ = build(monkeypatch, [FakeResponse({"value": [graph_event(showAs="free")]})])
    assert source.fetch(*WINDOW)[0].status == "tentative"


def test_zero_length_event_gets_a_floor(monkeypatch):
    payload = {
        "value": [
            graph_event(
                start={"dateTime": "2026-09-21T05:00:00.0000000", "timeZone": "UTC"},
                end={"dateTime": "2026-09-21T05:00:00.0000000", "timeZone": "UTC"},
            )
        ]
    }
    source, _ = build(monkeypatch, [FakeResponse(payload)])
    event = source.fetch(*WINDOW)[0]
    assert (event.end - event.start).total_seconds() == 30 * 60


class TestPagination:
    def test_follows_next_link_and_sends_params_only_once(self, monkeypatch):
        pages = [
            FakeResponse(
                {
                    "value": [graph_event(id="a")],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/next?skip=1",
                }
            ),
            FakeResponse({"value": [graph_event(id="b")]}),
        ]
        source, calls = build(monkeypatch, pages)
        events = source.fetch(*WINDOW)
        assert [e.ref.uid for e in events] == ["a", "b"]
        assert calls[0]["params"] is not None
        # nextLink already carries the query string; re-sending params duplicates them.
        assert calls[1]["params"] is None

    def test_empty_first_page_still_does_not_resend_params(self, monkeypatch):
        pages = [
            FakeResponse({"value": [], "@odata.nextLink": "https://graph/next"}),
            FakeResponse({"value": [graph_event(id="b")]}),
        ]
        source, calls = build(monkeypatch, pages)
        assert len(source.fetch(*WINDOW)) == 1
        assert calls[1]["params"] is None


class TestErrors:
    @pytest.mark.parametrize(
        "status, expected",
        [
            (403, "Calendars.Read"),
            (401, "ms-auth"),
            (500, "Graph API 500"),
        ],
    )
    def test_http_errors_are_explained(self, monkeypatch, status, expected):
        source, _ = build(monkeypatch, [FakeResponse({"error": "x"}, status=status)])
        with pytest.raises(SourceError, match=expected):
            source.fetch(*WINDOW)

    def test_auth_failure_is_surfaced_as_a_source_error(self, monkeypatch):
        from calhub.msauth import MsAuthError

        def boom(_options):
            raise MsAuthError("no token cache")

        monkeypatch.setattr("calhub.sources.msgraph_source.acquire_token", boom)
        app = Config(sources=[], sinks=[])
        source = MsGraphSource(SourceConfig(id="outlook", kind="msgraph"), app)
        with pytest.raises(SourceError, match="no token cache"):
            source.fetch(*WINDOW)


class TestAllDay:
    """All-day events are where timezone handling usually breaks."""

    @pytest.mark.parametrize(
        "start_dt, end_dt, zone",
        [
            # Graph may hand back midnight UTC ...
            ("2026-09-25T00:00:00.0000000", "2026-09-26T00:00:00.0000000", "UTC"),
            # ... or the same day already converted from the originating zone.
            ("2026-09-24T15:00:00.0000000", "2026-09-25T15:00:00.0000000", "UTC"),
        ],
    )
    def test_lands_on_the_correct_local_day(self, monkeypatch, start_dt, end_dt, zone):
        from calhub.util import get_tz

        payload = {
            "value": [
                graph_event(
                    subject="Summer Leave",
                    isAllDay=True,
                    start={"dateTime": start_dt, "timeZone": zone},
                    end={"dateTime": end_dt, "timeZone": zone},
                )
            ]
        }
        source, _ = build(monkeypatch, [FakeResponse(payload)])
        event = source.fetch(*WINDOW)[0]
        assert event.all_day
        seoul = get_tz("Asia/Seoul")
        assert event.start.astimezone(seoul).date() == datetime(2026, 9, 25).date()

    def test_all_day_survives_the_round_trip_to_the_ics_sink(self, monkeypatch, tmp_path):
        import icalendar

        from calhub.sinks.ics_file import IcsFileSink
        from calhub.config import SinkConfig

        payload = {
            "value": [
                graph_event(
                    subject="Summer Leave",
                    isAllDay=True,
                    start={"dateTime": "2026-09-25T00:00:00.0000000", "timeZone": "UTC"},
                    end={"dateTime": "2026-09-28T00:00:00.0000000", "timeZone": "UTC"},
                )
            ]
        }
        source, _ = build(monkeypatch, [FakeResponse(payload)])
        events = source.fetch(*WINDOW)

        out = tmp_path / "u.ics"
        app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
        sink = IcsFileSink(SinkConfig(kind="ics_file", options={"path": str(out)}), app)
        sink.apply(sink.plan(events, *WINDOW))

        vevent = list(icalendar.Calendar.from_ical(out.read_bytes()).walk("VEVENT"))[0]
        assert vevent["DTSTART"].dt == datetime(2026, 9, 25).date()
        assert vevent["DTEND"].dt == datetime(2026, 9, 28).date()
