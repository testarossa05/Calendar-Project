from datetime import date, datetime, timezone

import pytest

from calhub.util import get_tz, is_date_only, normalize_title, parse_window, to_utc, truncate

KST = get_tz("Asia/Seoul")


class TestNormalizeTitle:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("POSCO 3CC Kickoff", "posco 3cc kickoff"),
            ("RE: POSCO 3CC Kickoff", "posco 3cc kickoff"),
            ("[Teams] POSCO 3CC Kickoff", "posco 3cc kickoff"),
            ("FW: [Work] POSCO 3CC Kickoff - Microsoft Teams", "posco 3cc kickoff"),
            ("【사내】주간회의 (Zoom Meeting)", "주간회의"),
            ("", ""),
        ],
    )
    def test_variants_of_one_meeting_fold_together(self, raw, expected):
        assert normalize_title(raw) == expected

    def test_distinct_meetings_stay_distinct(self):
        assert normalize_title("Dangjin FS review") != normalize_title("Pohang FS review")


class TestToUtc:
    def test_naive_datetime_uses_default_zone(self):
        got = to_utc(datetime(2026, 9, 21, 14, 0), KST)
        assert got == datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)

    def test_aware_datetime_is_respected(self):
        aware = datetime(2026, 9, 21, 14, 0, tzinfo=KST)
        assert to_utc(aware, get_tz("UTC")) == datetime(2026, 9, 21, 5, 0, tzinfo=timezone.utc)

    def test_bare_date_anchors_at_local_midnight(self):
        # An all-day event on the 25th in Seoul starts at 15:00Z on the 24th.
        assert to_utc(date(2026, 9, 25), KST) == datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)

    def test_rejects_other_types(self):
        with pytest.raises(TypeError):
            to_utc("2026-09-21", KST)


def test_is_date_only_distinguishes_date_from_datetime():
    assert is_date_only(date(2026, 9, 21))
    assert not is_date_only(datetime(2026, 9, 21))


def test_get_tz_rejects_unknown_zone():
    with pytest.raises(ValueError, match="unknown timezone"):
        get_tz("Mars/Olympus_Mons")


def test_truncate_marks_elision():
    assert truncate("abcdef", 4).endswith("…")
    assert truncate("abc", 10) == "abc"
    assert truncate(None, 10) is None


def test_parse_window_is_anchored_to_midnight():
    start, end = parse_window(2, 5, now=datetime(2026, 9, 17, 13, 45, tzinfo=timezone.utc))
    assert start == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 22, tzinfo=timezone.utc)
