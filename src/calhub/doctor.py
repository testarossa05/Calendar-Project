"""Environment diagnostics.

Most failures here are environmental rather than logical: a missing PyObjC, a
macOS permission prompt that was dismissed, an expired Microsoft sign-in, a
calendar id that was never shared with the service account. ``calhub doctor``
checks each of those in order and prints the exact remedy, so a problem is
identified in one run instead of by trial and error.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

OK = "✓"
WARN = "!"
FAIL = "✗"


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail | skip
    detail: str = ""
    remedy: str = ""

    def render(self) -> str:
        mark = {"ok": OK, "warn": WARN, "fail": FAIL, "skip": "-"}[self.status]
        line = f"  {mark} {self.name}"
        if self.detail:
            line += f": {self.detail}"
        if self.remedy and self.status in ("warn", "fail"):
            line += f"\n      → {self.remedy}"
        return line


def check_python() -> Check:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if version < (3, 11):
        return Check(
            "Python", "fail", text, "calhub needs Python 3.11 or newer."
        )
    return Check("Python", "ok", text)


def check_module(module: str, package: str, needed_for: str) -> Check:
    if importlib.util.find_spec(module) is None:
        return Check(
            f"{package}", "fail", "not installed",
            f"pip install {package}   (needed for {needed_for})",
        )
    return Check(f"{package}", "ok", "installed")


def check_platform() -> Check:
    return Check("Platform", "ok", f"{platform.system()} {platform.release()}")


def check_eventkit() -> Check:
    """EventKit needs both PyObjC and a granted macOS permission."""
    if sys.platform != "darwin":
        return Check(
            "EventKit", "skip", "not macOS",
            "The eventkit source only runs on a Mac.",
        )
    if importlib.util.find_spec("EventKit") is None:
        return Check(
            "EventKit", "fail", "PyObjC not installed",
            "pip install pyobjc-framework-EventKit",
        )
    try:
        from EventKit import EKEventStore  # type: ignore[import-not-found]

        status = int(EKEventStore.authorizationStatusForEntityType_(0))
    except Exception as exc:
        return Check("EventKit", "fail", f"could not load: {exc}", "Reinstall PyObjC.")

    # 0 notDetermined, 1 restricted, 2 denied, 3 authorized, 4 fullAccess
    if status in (3, 4):
        try:
            store = EKEventStore.alloc().init()
            names = [str(c.title()) for c in (store.calendarsForEntityType_(0) or [])]
        except Exception:
            names = []
        if not names:
            return Check(
                "EventKit", "warn", "access granted but no calendars visible",
                "Open Calendar.app and confirm the work account is signed in.",
            )
        return Check("EventKit", "ok", f"{len(names)} calendar(s): {', '.join(names[:6])}")
    if status == 0:
        return Check(
            "EventKit", "warn", "permission not yet requested",
            "Run 'calhub sync' once and approve the macOS prompt.",
        )
    return Check(
        "EventKit", "fail", "access denied or restricted",
        "System Settings → Privacy & Security → Calendars → enable your terminal app.",
    )


def check_config(path: str) -> tuple[Check, Optional[object]]:
    from .config import ConfigError, load_config

    if not Path(path).exists():
        return (
            Check(
                "Config", "fail", f"{path} not found",
                "cp config.example.yaml config.yaml, then fill it in.",
            ),
            None,
        )
    try:
        config = load_config(path)
    except ConfigError as exc:
        return Check("Config", "fail", str(exc).split("\n")[0], "Fix config.yaml."), None

    detail = f"{len(config.active_sources)} source(s), {len(config.active_sinks)} sink(s)"
    return Check("Config", "ok", detail), config


def check_sinks(config) -> list[Check]:
    """Construct every enabled sink without contacting anything.

    This catches a sink whose kind is unknown -- most often a source block pasted
    under ``sinks:`` by mistake -- and one missing a required option. Neither
    shows up in the source probe, so without this the run looks healthy right up
    until ``sync`` fails.
    """
    from .sinks.registry import build_sink

    checks = []
    for cfg in config.sinks:
        label = f"sink '{cfg.kind}'"
        if not cfg.enabled:
            checks.append(Check(label, "skip", "disabled"))
            continue
        try:
            build_sink(cfg, config)
        except ValueError as exc:
            message = str(exc)
            remedy = "See config.example.yaml for this sink's options."
            if "unknown sink kind" in message:
                from .sources.registry import SOURCE_TYPES

                if cfg.kind in SOURCE_TYPES:
                    remedy = (
                        f"{cfg.kind!r} is a SOURCE, not a sink. This block belongs "
                        "under 'sources:', not 'sinks:'."
                    )
            checks.append(Check(label, "fail", message.split("\n")[0], remedy))
        except Exception as exc:
            checks.append(Check(label, "fail", str(exc).split("\n")[0], "Check this sink's options."))
        else:
            checks.append(Check(label, "ok", "configured"))
    return checks


def check_sources(config) -> list[Check]:
    """Actually reach every source, because that is where the real failures are."""
    from .sync import collect
    from .util import parse_window

    window_start, window_end = parse_window(config.back_days, config.forward_days)
    results, _ = collect(config, window_start, window_end, verbose=False)

    checks = []
    for result in results:
        if result.ok:
            checks.append(Check(f"source '{result.source_id}'", "ok", f"{result.count} events"))
        else:
            first_line = result.error.split("\n")[0]
            checks.append(
                Check(f"source '{result.source_id}'", "fail", first_line, _remedy_for(result.error))
            )
    return checks


def _remedy_for(error: str) -> str:
    lowered = error.lower()
    if "403" in error and "calendars.read" in lowered:
        return "Add the delegated Calendars.Read permission to the app registration."
    if "401" in error or "silent token refresh" in lowered:
        return "python -m calhub ms-auth --client-id <id> --tenant-id <tenant>"
    if "404" in error and "notion" in lowered:
        return "Share the Notion database with the integration (... → Connections)."
    if "app-specific password" in lowered or "caldav" in lowered:
        return "Generate an app-specific password at appleid.apple.com."
    if "did not return an icalendar" in lowered:
        return "Copy the ICS subscribe link, not the HTML page link."
    if "only runs on macos" in lowered:
        return "Use the msgraph or ics source on this platform."
    return "See docs/SETUP-ko.md for this source."


def run(config_path: str, probe_sources: bool = True) -> int:
    print("calhub doctor\n")

    print("Environment:")
    env_checks = [check_python(), check_platform()]
    for module, package, purpose in [
        ("icalendar", "icalendar", "ICS parsing"),
        ("requests", "requests", "HTTP sources"),
        ("yaml", "PyYAML", "config"),
        ("msal", "msal", "the msgraph source"),
        ("googleapiclient", "google-api-python-client", "Google sources and sinks"),
        ("caldav", "caldav", "the caldav source"),
    ]:
        env_checks.append(check_module(module, package, purpose))
    env_checks.append(check_eventkit())
    for check in env_checks:
        print(check.render())

    print("\nConfiguration:")
    config_check, config = check_config(config_path)
    print(config_check.render())

    sink_checks: list[Check] = []
    source_checks: list[Check] = []
    if config is not None:
        print("\nSinks:")
        sink_checks = check_sinks(config)
        for check in sink_checks:
            print(check.render())

    if config is not None and probe_sources:
        print("\nSources:")
        source_checks = check_sources(config)
        for check in source_checks:
            print(check.render())

    everything = env_checks + [config_check] + sink_checks + source_checks
    failures = [c for c in everything if c.status == "fail"]
    warnings = [c for c in everything if c.status == "warn"]

    print()
    if failures:
        print(f"{len(failures)} problem(s) found. Fix the ✗ lines above.")
        return 2
    if warnings:
        print(f"Ready, with {len(warnings)} warning(s).")
        return 0
    print("All checks passed.")
    return 0
