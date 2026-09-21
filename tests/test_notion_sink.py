"""Notion sink tests.

No workspace is reachable here, so the HTTP layer is replaced. The payloads are
shaped as the Notion API actually returns them, and the assertions target the
two things most likely to be wrong: the all-day date convention and idempotency.
"""

from datetime import datetime, timedelta, timezone

import pytest

from calhub.config import Config, SinkConfig
from calhub.models import OWNER_TAG, Event, SourceRef
from calhub.sinks.base import SinkError
from calhub.sinks.notion_sink import NotionSink

UTC = timezone.utc
WINDOW = (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC))

GOOD_SCHEMA = {
    "properties": {
        "Name": {"type": "title"},
        "Date": {"type": "date"},
        "Source": {"type": "rich_text"},
        "Location": {"type": "rich_text"},
        "calhub Key": {"type": "rich_text"},
        "calhub Hash": {"type": "rich_text"},
    }
}


def event(title="POSCO Kickoff", uid="1", all_day=False, **kw):
    start = kw.pop("start", datetime(2026, 9, 21, 5, 0, tzinfo=UTC))
    end = kw.pop("end", start + timedelta(hours=2))
    return Event(
        ref=SourceRef(source_id="outlook", kind="ics", uid=uid),
        title=title,
        start=start,
        end=end,
        all_day=all_day,
        **kw,
    )


def page_for(evt, hash_override=None, page_id="page-1"):
    """Build a Notion page as the query endpoint would return it."""
    return {
        "id": page_id,
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": evt.title}]},
            "calhub Key": {
                "type": "rich_text",
                "rich_text": [{"plain_text": f"{OWNER_TAG}:{evt.key}"}],
            },
            "calhub Hash": {
                "type": "rich_text",
                "rich_text": [{"plain_text": hash_override or evt.content_hash()}],
            },
        },
    }


def build(monkeypatch, responses, **options):
    """Wire a sink whose HTTP calls return canned responses in order."""
    calls = []
    queue = list(responses)

    def fake_request(self, method, path, payload=None):
        calls.append({"method": method, "path": path, "payload": payload})
        if path.startswith("/databases/") and method == "GET":
            return GOOD_SCHEMA
        return queue.pop(0) if queue else {"results": [], "has_more": False}

    monkeypatch.setattr(NotionSink, "_request", fake_request)
    opts = {"database_id": "db-1", "token": "secret", **options}
    app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
    return NotionSink(SinkConfig(kind="notion", options=opts), app), calls


class TestAllDayDates:
    """Notion's all-day end is inclusive; the internal model is exclusive."""

    def test_multi_day_end_is_pulled_back_one_day(self, monkeypatch):
        sink, calls = build(monkeypatch, [])
        # 2026-09-25 through 2026-09-27 inclusive == exclusive end of the 28th.
        evt = event(
            title="Summer Leave",
            all_day=True,
            start=datetime(2026, 9, 24, 15, 0, tzinfo=UTC),  # 09-25 00:00 KST
            end=datetime(2026, 9, 27, 15, 0, tzinfo=UTC),  # 09-28 00:00 KST
        )
        sink.apply(sink.plan([evt], *WINDOW))
        date_prop = calls[-1]["payload"]["properties"]["Date"]["date"]
        assert date_prop["start"] == "2026-09-25"
        assert date_prop["end"] == "2026-09-27", "an exclusive end would stretch the event"

    def test_single_day_event_has_no_end(self, monkeypatch):
        sink, calls = build(monkeypatch, [])
        evt = event(
            title="Holiday",
            all_day=True,
            start=datetime(2026, 9, 24, 15, 0, tzinfo=UTC),
            end=datetime(2026, 9, 25, 15, 0, tzinfo=UTC),
        )
        sink.apply(sink.plan([evt], *WINDOW))
        date_prop = calls[-1]["payload"]["properties"]["Date"]["date"]
        assert date_prop["start"] == "2026-09-25"
        assert "end" not in date_prop

    def test_round_trip_through_the_notion_source_is_stable(self, monkeypatch):
        """What the sink writes, the source must read back unchanged."""
        from calhub.config import SourceConfig
        from calhub.sources.notion_source import NotionSource

        sink, calls = build(monkeypatch, [])
        original = event(
            title="Summer Leave",
            all_day=True,
            start=datetime(2026, 9, 24, 15, 0, tzinfo=UTC),
            end=datetime(2026, 9, 27, 15, 0, tzinfo=UTC),
        )
        sink.apply(sink.plan([original], *WINDOW))
        written = calls[-1]["payload"]["properties"]["Date"]["date"]

        app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
        source = NotionSource(SourceConfig(id="n", kind="notion"), app)
        row = {
            "id": "p1",
            "url": "https://notion.so/p1",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Summer Leave"}]},
                "Date": {"type": "date", "date": written},
            },
        }
        from calhub.util import get_tz

        parsed = source._to_event(row, "Date", "Name", get_tz("Asia/Seoul"))
        assert parsed.all_day
        assert parsed.start == original.start
        assert parsed.end == original.end


class TestTimedDates:
    def test_timed_event_is_written_in_the_display_timezone(self, monkeypatch):
        sink, calls = build(monkeypatch, [])
        sink.apply(sink.plan([event()], *WINDOW))
        date_prop = calls[-1]["payload"]["properties"]["Date"]["date"]
        assert date_prop["start"] == "2026-09-21T14:00:00+09:00"
        assert date_prop["end"] == "2026-09-21T16:00:00+09:00"


class TestIdempotency:
    def test_unchanged_page_is_left_alone(self, monkeypatch):
        evt = event()
        sink, _ = build(
            monkeypatch, [{"results": [page_for(evt)], "has_more": False}]
        )
        plan = sink.plan([evt], *WINDOW)
        assert (len(plan.to_create), len(plan.to_update), plan.unchanged) == (0, 0, 1)

    def test_changed_page_is_updated_not_recreated(self, monkeypatch):
        evt = event()
        sink, _ = build(
            monkeypatch, [{"results": [page_for(evt, hash_override="stale")], "has_more": False}]
        )
        plan = sink.plan([evt], *WINDOW)
        assert len(plan.to_create) == 0
        assert plan.to_update == [("page-1", evt)]

    def test_new_event_is_created(self, monkeypatch):
        sink, _ = build(monkeypatch, [{"results": [], "has_more": False}])
        plan = sink.plan([event()], *WINDOW)
        assert len(plan.to_create) == 1

    def test_orphan_is_archived_not_deleted(self, monkeypatch):
        stale = event(title="Cancelled thing", uid="gone")
        sink, calls = build(
            monkeypatch, [{"results": [page_for(stale)], "has_more": False}]
        )
        plan = sink.plan([], *WINDOW)
        assert plan.to_delete == [("page-1", "Cancelled thing")]
        sink.apply(plan)
        archive = [c for c in calls if c["payload"] == {"archived": True}]
        assert len(archive) == 1
        assert archive[0]["method"] == "PATCH"

    def test_hand_made_page_without_a_key_is_never_touched(self, monkeypatch):
        page = {
            "id": "human-page",
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "My own note"}]},
                "calhub Key": {"type": "rich_text", "rich_text": []},
            },
        }
        sink, _ = build(monkeypatch, [{"results": [page], "has_more": False}])
        plan = sink.plan([], *WINDOW)
        assert plan.to_delete == []

    def test_duplicate_keys_from_an_interrupted_run_are_cleaned_up(self, monkeypatch):
        evt = event()
        pages = [page_for(evt, page_id="first"), page_for(evt, page_id="second")]
        sink, _ = build(monkeypatch, [{"results": pages, "has_more": False}])
        plan = sink.plan([evt], *WINDOW)
        assert plan.unchanged == 1
        assert [pid for pid, _ in plan.to_delete] == ["second"]


class TestSchemaValidation:
    def test_missing_property_is_named(self, monkeypatch):
        def fake_request(self, method, path, payload=None):
            return {"properties": {"Name": {"type": "title"}}}

        monkeypatch.setattr(NotionSink, "_request", fake_request)
        app = Config(sources=[], sinks=[])
        sink = NotionSink(
            SinkConfig(kind="notion", options={"database_id": "d", "token": "t"}), app
        )
        with pytest.raises(SinkError, match="calhub Key"):
            sink.plan([], *WINDOW)

    def test_wrong_property_type_is_named(self, monkeypatch):
        schema = {
            "properties": {
                "Name": {"type": "title"},
                "Date": {"type": "rich_text"},  # should be date
                "calhub Key": {"type": "rich_text"},
                "calhub Hash": {"type": "rich_text"},
            }
        }
        monkeypatch.setattr(NotionSink, "_request", lambda self, m, p, payload=None: schema)
        app = Config(sources=[], sinks=[])
        sink = NotionSink(
            SinkConfig(kind="notion", options={"database_id": "d", "token": "t"}), app
        )
        with pytest.raises(SinkError, match="expected 'date'"):
            sink.plan([], *WINDOW)

    def test_custom_property_names_are_honoured(self, monkeypatch):
        schema = {
            "properties": {
                "제목": {"type": "title"},
                "날짜": {"type": "date"},
                "키": {"type": "rich_text"},
                "해시": {"type": "rich_text"},
            }
        }
        calls = []

        def fake_request(self, method, path, payload=None):
            calls.append(payload)
            if method == "GET":
                return schema
            return {"results": [], "has_more": False}

        monkeypatch.setattr(NotionSink, "_request", fake_request)
        app = Config(sources=[], sinks=[], timezone="Asia/Seoul")
        sink = NotionSink(
            SinkConfig(
                kind="notion",
                options={
                    "database_id": "d",
                    "token": "t",
                    "title_property": "제목",
                    "date_property": "날짜",
                    "key_property": "키",
                    "hash_property": "해시",
                    "source_property": "",
                    "location_property": "",
                },
            ),
            app,
        )
        sink.apply(sink.plan([event()], *WINDOW))
        assert "제목" in calls[-1]["properties"]
        assert "날짜" in calls[-1]["properties"]


def test_required_options_are_enforced():
    app = Config(sources=[], sinks=[])
    with pytest.raises(ValueError, match="database_id"):
        NotionSink(SinkConfig(kind="notion", options={}), app)
