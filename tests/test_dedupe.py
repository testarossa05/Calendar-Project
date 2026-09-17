from datetime import datetime, timedelta, timezone

from calhub.config import SourceConfig
from calhub.dedupe import deduplicate
from calhub.models import Event, SourceRef

UTC = timezone.utc
BASE = datetime(2026, 9, 21, 5, 0, tzinfo=UTC)


def ev(title, source, uid, start=BASE, hours=1):
    return Event(
        ref=SourceRef(source_id=source, kind="ics", uid=uid),
        title=title,
        start=start,
        end=start + timedelta(hours=hours),
    )


def cfgs(**priorities):
    return {sid: SourceConfig(id=sid, kind="ics", priority=p) for sid, p in priorities.items()}


def test_highest_priority_source_wins():
    events = [ev("Kickoff", "notion", "n1"), ev("Kickoff", "outlook", "o1")]
    kept, dropped, pairs = deduplicate(events, cfgs(outlook=10, notion=50))
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0].ref.source_id == "outlook"
    assert pairs[0][0].ref.source_id == "outlook"
    assert pairs[0][1].ref.source_id == "notion"


def test_three_way_duplicate_collapses_to_one():
    events = [
        ev("Kickoff", "notion", "n1"),
        ev("Kickoff", "google", "g1"),
        ev("Kickoff", "outlook", "o1"),
    ]
    kept, dropped, _ = deduplicate(events, cfgs(outlook=10, google=20, notion=50))
    assert dropped == 2
    assert [e.ref.source_id for e in kept] == ["outlook"]


def test_different_times_are_not_duplicates():
    events = [ev("Standup", "a", "1"), ev("Standup", "b", "2", start=BASE + timedelta(days=1))]
    kept, dropped, _ = deduplicate(events, cfgs(a=10, b=20))
    assert dropped == 0
    assert len(kept) == 2


def test_different_durations_are_not_duplicates():
    events = [ev("Review", "a", "1", hours=1), ev("Review", "b", "2", hours=2)]
    kept, dropped, _ = deduplicate(events, cfgs(a=10, b=20))
    assert dropped == 0


def test_result_is_sorted_and_deterministic():
    events = [
        ev("Later", "a", "1", start=BASE + timedelta(hours=3)),
        ev("Earlier", "a", "2", start=BASE),
    ]
    kept, _, _ = deduplicate(events, cfgs(a=10))
    assert [e.title for e in kept] == ["Earlier", "Later"]


def test_tie_on_priority_breaks_deterministically():
    events = [ev("X", "bbb", "1"), ev("X", "aaa", "2")]
    first, _, _ = deduplicate(list(events), cfgs(aaa=10, bbb=10))
    second, _, _ = deduplicate(list(reversed(events)), cfgs(aaa=10, bbb=10))
    assert first[0].ref.source_id == second[0].ref.source_id == "aaa"


def test_unknown_source_falls_back_to_default_priority():
    events = [ev("X", "known", "1"), ev("X", "ghost", "2")]
    kept, dropped, _ = deduplicate(events, cfgs(known=10))
    assert dropped == 1
    assert kept[0].ref.source_id == "known"
