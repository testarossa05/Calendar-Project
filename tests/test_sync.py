"""Engine-level tests: merge, guards and end-to-end behaviour through a real sink."""

from datetime import datetime, timezone
from pathlib import Path

import icalendar
import pytest

from calhub.config import Config, SinkConfig, SourceConfig, load_config
from calhub.models import SyncPlan
from calhub.sync import _deletion_guard, _partial_guard, RunResult, SourceResult, run

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


def make_config(tmp_path, **overrides):
    out = tmp_path / "unified.ics"
    cfg = Config(
        sources=[
            SourceConfig(
                id="outlook",
                kind="ics",
                priority=10,
                options={"file": str(FIXTURES / "outlook.ics")},
            ),
            SourceConfig(
                id="notion",
                kind="ics",
                priority=50,
                options={"file": str(FIXTURES / "notion_export.ics")},
            ),
        ],
        sinks=[SinkConfig(kind="ics_file", options={"path": str(out)})],
        timezone="Asia/Seoul",
        back_days=30,
        forward_days=90,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg, out


class TestEndToEnd:
    def test_merged_file_is_written_and_parseable(self, tmp_path):
        cfg, out = make_config(tmp_path)
        result = run(cfg, now=NOW, verbose=False)
        assert out.exists()
        calendar = icalendar.Calendar.from_ical(out.read_bytes())
        vevents = list(calendar.walk("VEVENT"))
        assert len(vevents) == len(result.events)
        uids = [str(v["UID"]) for v in vevents]
        assert len(set(uids)) == len(uids), "duplicate UIDs make iOS merge events"

    def test_duplicate_across_sources_is_collapsed(self, tmp_path):
        cfg, _ = make_config(tmp_path)
        result = run(cfg, now=NOW, verbose=False)
        assert result.duplicates_dropped == 1
        kickoffs = [e for e in result.events if "Kickoff" in e.title]
        assert len(kickoffs) == 1
        assert kickoffs[0].ref.source_id == "outlook"

    def test_short_events_are_dropped_when_configured(self, tmp_path):
        cfg, _ = make_config(tmp_path, drop_shorter_than_minutes=10)
        result = run(cfg, now=NOW, verbose=False)
        assert not any("Quick reminder" in e.title for e in result.events)

    def test_dry_run_writes_nothing(self, tmp_path):
        cfg, out = make_config(tmp_path)
        run(cfg, dry_run=True, now=NOW, verbose=False)
        assert not out.exists()

    def test_rerun_is_idempotent(self, tmp_path):
        cfg, out = make_config(tmp_path)
        run(cfg, now=NOW, verbose=False)
        first = out.read_bytes()
        run(cfg, now=NOW, verbose=False)
        second = out.read_bytes()
        # DTSTAMP is regenerated each run; everything else must be byte-identical.
        strip = lambda b: b"\n".join(  # noqa: E731
            line for line in b.split(b"\n") if not line.startswith(b"DTSTAMP")
        )
        assert strip(first) == strip(second)

    def test_all_sources_failing_aborts_rather_than_emptying_the_calendar(self, tmp_path):
        cfg, out = make_config(tmp_path)
        for source in cfg.sources:
            source.options["file"] = "/nonexistent.ics"
        with pytest.raises(RuntimeError, match="every source failed"):
            run(cfg, now=NOW, verbose=False)
        assert not out.exists()

    def test_partial_failure_leaves_the_previous_file_intact(self, tmp_path):
        cfg, out = make_config(tmp_path)
        run(cfg, now=NOW, verbose=False)
        good = out.read_bytes()

        cfg.sources[0].options["file"] = "/nonexistent.ics"  # Outlook now broken
        result = run(cfg, now=NOW, verbose=False)
        assert result.failed_sources
        assert out.read_bytes() == good, "a failed source must not silently drop events"
        assert "skipped" in result.applied["ics_file"]

    def test_partial_failure_publishes_when_explicitly_allowed(self, tmp_path):
        cfg, out = make_config(tmp_path, allow_partial=True)
        run(cfg, now=NOW, verbose=False)
        before = len(list(icalendar.Calendar.from_ical(out.read_bytes()).walk("VEVENT")))

        cfg.sources[0].options["file"] = "/nonexistent.ics"
        run(cfg, now=NOW, verbose=False)
        after = len(list(icalendar.Calendar.from_ical(out.read_bytes()).walk("VEVENT")))
        assert after < before


class TestGuards:
    def _result(self, failed=()):
        result = RunResult(window_start=NOW, window_end=NOW)
        result.sources = [SourceResult(source_id="ok", kind="ics")]
        for name in failed:
            result.sources.append(SourceResult(source_id=name, kind="ics", error="boom"))
        return result

    def test_partial_guard_is_silent_when_everything_succeeded(self):
        cfg = Config(sources=[], sinks=[])
        assert _partial_guard(cfg, self._result(), force=False) is None

    def test_partial_guard_names_the_failed_source(self):
        cfg = Config(sources=[], sinks=[])
        message = _partial_guard(cfg, self._result(["outlook"]), force=False)
        assert message and "outlook" in message

    def test_partial_guard_yields_to_force(self):
        cfg = Config(sources=[], sinks=[])
        assert _partial_guard(cfg, self._result(["outlook"]), force=True) is None

    def test_deletion_guard_blocks_a_mass_delete(self):
        cfg = Config(sources=[], sinks=[], max_delete_ratio=0.5, delete_guard_min_events=10)
        plan = SyncPlan(to_delete=[(str(i), "x") for i in range(20)], unchanged=0)
        message = _deletion_guard(plan, cfg, self._result(), force=False)
        assert message and "max_delete_ratio" in message

    def test_deletion_guard_allows_a_normal_churn(self):
        cfg = Config(sources=[], sinks=[], max_delete_ratio=0.5, delete_guard_min_events=10)
        plan = SyncPlan(to_delete=[("1", "x")], unchanged=30)
        assert _deletion_guard(plan, cfg, self._result(), force=False) is None

    def test_deletion_guard_ignores_tiny_calendars(self):
        cfg = Config(sources=[], sinks=[], max_delete_ratio=0.5, delete_guard_min_events=10)
        plan = SyncPlan(to_delete=[("1", "x"), ("2", "y")], unchanged=1)
        assert _deletion_guard(plan, cfg, self._result(), force=False) is None

    def test_deletion_guard_yields_to_force(self):
        cfg = Config(sources=[], sinks=[], max_delete_ratio=0.5, delete_guard_min_events=10)
        plan = SyncPlan(to_delete=[(str(i), "x") for i in range(20)])
        assert _deletion_guard(plan, cfg, self._result(), force=True) is None


def test_config_file_round_trip(tmp_path):
    cfg = load_config(FIXTURES / "config_test.yaml")
    assert cfg.timezone == "Asia/Seoul"
    assert {s.id for s in cfg.active_sources} == {"outlook", "notion"}


class TestPlanOutput:
    """The dry-run report is a diagnostic tool, so its dates must be local.

    Events are UTC internally; printing that raw puts a Seoul all-day event on
    the previous day, which reads exactly like an off-by-one bug.
    """

    def test_dry_run_dates_match_agenda_dates(self, tmp_path, capsys):
        from calhub.util import get_tz

        cfg, _ = make_config(tmp_path)
        run(cfg, dry_run=True, now=NOW, verbose=True)
        printed = capsys.readouterr().out

        result = run(cfg, dry_run=True, now=NOW, verbose=False)
        seoul = get_tz("Asia/Seoul")
        all_day = [e for e in result.events if e.all_day]
        assert all_day, "fixture must contain an all-day event for this to mean anything"
        for event in all_day:
            local = event.start.astimezone(seoul).date().isoformat()
            utc = event.start.date().isoformat()
            assert local != utc, "fixture must straddle midnight UTC to be a real test"
            # Match the whole line, since an unrelated event may legitimately sit
            # on the UTC date this one would wrongly print.
            assert f"+ {local} {event.title}" in printed
            assert f"+ {utc} {event.title}" not in printed
