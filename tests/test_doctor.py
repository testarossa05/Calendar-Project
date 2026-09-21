"""Diagnostics tests.

The point of `doctor` is that a broken environment produces an actionable line
rather than a stack trace, so these assert on the remedy text as much as on the
status.
"""

from pathlib import Path

import pytest

from calhub import doctor

FIXTURES = Path(__file__).parent / "fixtures"


class TestIndividualChecks:
    def test_python_version_passes_on_a_supported_interpreter(self):
        assert doctor.check_python().status == "ok"

    def test_installed_module_is_reported_ok(self):
        assert doctor.check_module("icalendar", "icalendar", "ICS parsing").status == "ok"

    def test_missing_module_names_the_install_command(self):
        check = doctor.check_module("no_such_module_xyz", "ghost-pkg", "nothing")
        assert check.status == "fail"
        assert "pip install ghost-pkg" in check.remedy

    def test_eventkit_is_skipped_off_macos(self, monkeypatch):
        monkeypatch.setattr(doctor.sys, "platform", "linux")
        check = doctor.check_eventkit()
        assert check.status == "skip"
        assert "macOS" in check.detail or "Mac" in check.remedy


class TestConfigCheck:
    def test_missing_config_points_at_the_example(self, tmp_path):
        check, config = doctor.check_config(str(tmp_path / "absent.yaml"))
        assert check.status == "fail"
        assert "config.example.yaml" in check.remedy
        assert config is None

    def test_valid_config_is_summarised(self):
        check, config = doctor.check_config(str(FIXTURES / "config_test.yaml"))
        assert check.status == "ok"
        assert "2 source" in check.detail
        assert config is not None

    def test_malformed_config_fails_without_raising(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("sources: []\nsinks: []\n", encoding="utf-8")
        check, config = doctor.check_config(str(bad))
        assert check.status == "fail"
        assert config is None


class TestSourceProbe:
    def test_reachable_sources_report_their_counts(self):
        _, config = doctor.check_config(str(FIXTURES / "config_test.yaml"))
        checks = doctor.check_sources(config)
        assert {c.status for c in checks} == {"ok"}
        assert any("events" in c.detail for c in checks)

    def test_broken_source_is_reported_with_a_remedy(self):
        _, config = doctor.check_config(str(FIXTURES / "config_test.yaml"))
        config.sources[0].options["file"] = "/nonexistent.ics"
        checks = doctor.check_sources(config)
        failed = [c for c in checks if c.status == "fail"]
        assert len(failed) == 1
        assert failed[0].remedy


class TestRemedyMapping:
    @pytest.mark.parametrize(
        "error, expected",
        [
            ("Graph API 403: Calendars.Read missing", "Calendars.Read"),
            ("Graph returned 401", "ms-auth"),
            ("Notion returned 404", "Connections"),
            ("CalDAV connection failed: app-specific password", "appleid.apple.com"),
            ("the URL did not return an iCalendar feed", "ICS subscribe link"),
            ("the eventkit source only runs on macOS", "msgraph"),
        ],
    )
    def test_known_errors_map_to_specific_advice(self, error, expected):
        assert expected in doctor._remedy_for(error)

    def test_unknown_error_falls_back_to_the_docs(self):
        assert "SETUP-ko.md" in doctor._remedy_for("something unexpected happened")


class TestRun:
    def test_healthy_environment_returns_zero(self, capsys):
        code = doctor.run(str(FIXTURES / "config_test.yaml"))
        assert code == 0
        assert "All checks passed" in capsys.readouterr().out

    def test_missing_config_returns_nonzero(self, tmp_path, capsys):
        code = doctor.run(str(tmp_path / "absent.yaml"))
        assert code == 2
        assert "problem(s) found" in capsys.readouterr().out

    def test_no_probe_skips_contacting_sources(self, capsys):
        doctor.run(str(FIXTURES / "config_test.yaml"), probe_sources=False)
        assert "Sources:" not in capsys.readouterr().out


def test_check_renders_remedy_only_for_problems():
    ok = doctor.Check("x", "ok", "fine", "do something")
    bad = doctor.Check("x", "fail", "broken", "do something")
    assert "do something" not in ok.render()
    assert "do something" in bad.render()


class TestSinkChecks:
    """A misconfigured sink is invisible to the source probe.

    Pasting a source block under `sinks:` is an easy mistake -- the two lists sit
    next to each other in the config -- and it used to pass every check, leaving
    the failure to surface much later at `sync`.
    """

    def _config(self, sink_kind, **options):
        from calhub.config import Config, SinkConfig, SourceConfig

        return Config(
            sources=[SourceConfig(id="family", kind="local", options={"file": "x.yaml"})],
            sinks=[SinkConfig(kind=sink_kind, options=options)],
        )

    def test_source_kind_used_as_a_sink_says_where_the_block_belongs(self):
        checks = doctor.check_sinks(self._config("eventkit"))
        assert checks[0].status == "fail"
        assert "is a SOURCE, not a sink" in checks[0].remedy
        assert "sources:" in checks[0].remedy

    def test_unknown_kind_lists_the_valid_ones(self):
        checks = doctor.check_sinks(self._config("carrier-pigeon"))
        assert checks[0].status == "fail"
        assert "ics_file" in checks[0].detail

    def test_valid_sink_passes(self, tmp_path):
        checks = doctor.check_sinks(self._config("ics_file", path=str(tmp_path / "u.ics")))
        assert checks[0].status == "ok"

    def test_missing_required_option_is_reported(self):
        checks = doctor.check_sinks(self._config("google"))
        assert checks[0].status == "fail"
        assert "calendar_id" in checks[0].detail

    def test_disabled_sink_is_skipped_not_validated(self):
        from calhub.config import Config, SinkConfig, SourceConfig

        config = Config(
            sources=[SourceConfig(id="f", kind="local", options={"file": "x"})],
            sinks=[SinkConfig(kind="google", enabled=False, options={})],
        )
        checks = doctor.check_sinks(config)
        assert checks[0].status == "skip"

    def test_run_reports_a_bad_sink_as_a_problem(self, tmp_path, capsys):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "sources:\n"
            "  - id: family\n"
            "    kind: local\n"
            f"    file: {tmp_path / 'events.yaml'}\n"
            "sinks:\n"
            "  - kind: ics_file\n"
            f"    path: {tmp_path / 'u.ics'}\n"
            "  - id: outlook-mac\n"
            "    kind: eventkit\n",
            encoding="utf-8",
        )
        (tmp_path / "events.yaml").write_text("events: []\n", encoding="utf-8")
        code = doctor.run(str(config_file))
        assert code == 2
        assert "is a SOURCE, not a sink" in capsys.readouterr().out
