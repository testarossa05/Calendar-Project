from datetime import datetime, timedelta, timezone

from calhub.models import Event, SourceRef

UTC = timezone.utc


def make(title="Meeting", start=None, source="a", uid="1", **kw):
    start = start or datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
    return Event(
        ref=SourceRef(source_id=source, kind="ics", uid=uid),
        title=title,
        start=start,
        end=kw.pop("end", start + timedelta(hours=1)),
        **kw,
    )


def test_end_before_start_is_clamped():
    start = datetime(2026, 9, 21, 5, 0, tzinfo=UTC)
    event = make(start=start, end=start - timedelta(hours=2))
    assert event.end == start


def test_key_is_stable_and_source_scoped():
    assert make(source="a", uid="x").key == make(source="a", uid="x").key
    assert make(source="a", uid="x").key != make(source="b", uid="x").key
    assert make(source="a", uid="x").key != make(source="a", uid="y").key


def test_dedupe_key_matches_same_meeting_from_two_sources():
    a = make(title="[Teams] POSCO Kickoff", source="outlook", uid="1")
    b = make(title="POSCO Kickoff", source="notion", uid="2")
    assert a.dedupe_key == b.dedupe_key


def test_dedupe_key_tolerates_second_level_drift():
    base = datetime(2026, 9, 21, 5, 0, 0, tzinfo=UTC)
    a = make(start=base)
    b = make(start=base + timedelta(seconds=40))
    assert a.dedupe_key == b.dedupe_key


def test_dedupe_key_uses_match_title_when_presentation_rewrote_title():
    masked = make(title="• Busy", match_title="POSCO Kickoff", source="outlook", uid="1")
    plain = make(title="POSCO Kickoff", source="notion", uid="2")
    assert masked.dedupe_key == plain.dedupe_key


def test_content_hash_changes_only_with_mirrored_content():
    a = make()
    assert a.content_hash() == make().content_hash()
    assert a.content_hash() != make(title="Different").content_hash()
    assert a.content_hash() != make(location="Seoul").content_hash()
    # The source uid is not mirrored, so it must not move the hash.
    assert a.content_hash() == make(uid="other").content_hash()
