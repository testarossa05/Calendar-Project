"""Command line entry point.

    python -m calhub sync            # merge and write
    python -m calhub sync --dry-run  # show exactly what would change
    python -m calhub check           # validate config and reachability only
    python -m calhub agenda          # print the merged agenda to the terminal
    python -m calhub google-auth ... # one-off OAuth token generation
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import __version__
from .config import ConfigError, load_config
from .msauth import MsAuthError
from .sync import collect, run
from .util import get_tz, parse_window

DEFAULT_CONFIG = "config.yaml"


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG, help=f"config file (default: {DEFAULT_CONFIG})"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="only report errors")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="calhub", description="Unify calendars scattered across Outlook, Notion, Google and iCloud."
    )
    parser.add_argument("--version", action="version", version=f"calhub {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="fetch, merge and write to every sink")
    _add_common(p_sync)
    p_sync.add_argument(
        "-n", "--dry-run", action="store_true", help="show the plan without writing"
    )
    p_sync.add_argument(
        "--force", action="store_true", help="bypass the mass-deletion safety guard"
    )

    p_check = sub.add_parser("check", help="validate config and confirm every source loads")
    _add_common(p_check)

    p_doctor = sub.add_parser(
        "doctor", help="diagnose the environment, dependencies, permissions and sources"
    )
    _add_common(p_doctor)
    p_doctor.add_argument(
        "--no-probe", action="store_true", help="skip contacting the sources"
    )

    p_agenda = sub.add_parser("agenda", help="print the merged agenda without writing anywhere")
    _add_common(p_agenda)
    p_agenda.add_argument("-d", "--days", type=int, default=14, help="days ahead (default: 14)")

    p_auth = sub.add_parser("google-auth", help="run the Google OAuth flow and save a token")
    p_auth.add_argument("client_secret", help="path to the downloaded OAuth client JSON")
    p_auth.add_argument(
        "-o", "--out", default="secrets/google_token.json", help="where to write the token"
    )

    p_cal = sub.add_parser("google-calendars", help="list Google calendars the credential can see")
    _add_common(p_cal)

    p_ms = sub.add_parser(
        "ms-auth", help="sign in to Microsoft Graph once and save a reusable token cache"
    )
    p_ms.add_argument("--client-id", required=True, help="Entra ID application (client) id")
    p_ms.add_argument(
        "--tenant-id",
        default="organizations",
        help="tenant id or domain (default: organizations)",
    )
    p_ms.add_argument(
        "-o", "--out", default="secrets/ms_token_cache.json", help="where to write the cache"
    )

    return parser


def cmd_sync(args) -> int:
    config = load_config(args.config)
    result = run(config, dry_run=args.dry_run, verbose=not args.quiet, force=args.force)
    if result.failed_sources:
        names = ", ".join(s.source_id for s in result.failed_sources)
        print(f"\nCompleted with failing source(s): {names}", file=sys.stderr)
        return 2
    if not args.quiet:
        print("\nDone.")
    return 0


def cmd_check(args) -> int:
    config = load_config(args.config)
    window_start, window_end = parse_window(config.back_days, config.forward_days)
    get_tz(config.timezone)
    print(f"Config OK: {len(config.active_sources)} source(s), {len(config.active_sinks)} sink(s)")
    print(f"Window: {window_start.date()} → {window_end.date()}  (tz {config.timezone})")
    print("Sources:")
    results, _ = collect(config, window_start, window_end, verbose=True)
    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} source(s) failed.", file=sys.stderr)
        return 2
    print("\nAll sources reachable.")
    return 0


def cmd_doctor(args) -> int:
    from . import doctor

    return doctor.run(args.config, probe_sources=not args.no_probe)


def cmd_agenda(args) -> int:
    config = load_config(args.config)
    config.forward_days = args.days
    config.back_days = 0
    window_start, window_end = parse_window(0, args.days)
    results, events = collect(config, window_start, window_end, verbose=not args.quiet)

    if config.dedupe:
        from .dedupe import deduplicate

        events, _, _ = deduplicate(events, {c.id: c for c in config.sources})

    zone = get_tz(config.timezone)
    print()
    current_day = None
    for event in sorted(events, key=lambda e: e.start):
        local = event.start.astimezone(zone)
        day = local.date()
        if day != current_day:
            current_day = day
            print(f"\n{day:%Y-%m-%d (%a)}")
        when = "all day" if event.all_day else f"{local:%H:%M}-{event.end.astimezone(zone):%H:%M}"
        print(f"  {when:<12} {event.title[:58]:<58} [{event.ref.source_id}]")
    if not events:
        print("(no events in window)")
    return 0 if all(r.ok for r in results) else 2


def cmd_google_auth(args) -> int:
    from .gauth import run_local_oauth

    path = run_local_oauth(args.client_secret, args.out)
    print(f"Token written to {path}")
    print("Point 'token_json' at this path, or paste its contents into a GitHub secret.")
    return 0


def cmd_google_calendars(args) -> int:
    from .gauth import build_service

    config = load_config(args.config)
    options: dict = {}
    for sink in config.sinks:
        if sink.kind == "google":
            options = sink.options
            break
    else:
        for source in config.sources:
            if source.kind == "google":
                options = source.options
                break
    service = build_service(options, readonly=True)
    entries = service.calendarList().list().execute().get("items", [])
    for entry in entries:
        role = entry.get("accessRole", "?")
        print(f"{entry['id']}\n    {entry.get('summary', '')}  (access: {role})")
    if not entries:
        print("(no calendars visible to this credential)")
    return 0


def cmd_ms_auth(args) -> int:
    from .msauth import device_code_login

    path = device_code_login(args.client_id, args.tenant_id, args.out)
    print(f"\nToken cache written to {path}")
    print("Point 'token_cache' at this path, or paste its contents into the")
    print("MS_TOKEN_CACHE secret for automated runs.")
    print("\nThe cache holds a refresh token. Treat it like a password: it is")
    print("git-ignored here, and must not be committed or shared.")
    return 0


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "sync": cmd_sync,
        "check": cmd_check,
        "doctor": cmd_doctor,
        "agenda": cmd_agenda,
        "google-auth": cmd_google_auth,
        "google-calendars": cmd_google_calendars,
        "ms-auth": cmd_ms_auth,
    }
    try:
        return handlers[args.command](args)
    except MsAuthError as exc:
        print(f"Microsoft sign-in error: {exc}", file=sys.stderr)
        return 1
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
