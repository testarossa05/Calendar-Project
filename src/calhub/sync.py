"""Orchestration: fetch every source, merge, then drive each sink."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .config import Config
from .dedupe import deduplicate
from .models import Event, SyncPlan
from .sources.base import SourceError
from .sources.registry import build_source
from .sinks.registry import build_sink
from .transform import apply_source_rules, should_keep
from .util import parse_window


@dataclass
class SourceResult:
    source_id: str
    kind: str
    count: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class RunResult:
    window_start: datetime
    window_end: datetime
    sources: list[SourceResult] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    duplicates_dropped: int = 0
    dropped_pairs: list[tuple[Event, Event]] = field(default_factory=list)
    plans: dict[str, SyncPlan] = field(default_factory=dict)
    applied: dict[str, dict] = field(default_factory=dict)

    @property
    def failed_sources(self) -> list[SourceResult]:
        return [s for s in self.sources if not s.ok]

    @property
    def any_source_succeeded(self) -> bool:
        return any(s.ok for s in self.sources)


def collect(config: Config, window_start: datetime, window_end: datetime, verbose: bool = True):
    """Fetch and normalise every enabled source.

    A source that fails is recorded and skipped rather than aborting the run: a
    stale Outlook token must not stop Notion and Google from syncing.
    """
    results: list[SourceResult] = []
    events: list[Event] = []

    for cfg in config.active_sources:
        result = SourceResult(source_id=cfg.id, kind=cfg.kind)
        try:
            source = build_source(cfg, config)
            raw = source.fetch(window_start, window_end)
            kept = []
            for event in raw:
                if not should_keep(event, config):
                    continue
                kept.append(apply_source_rules(event, cfg, config))
            events.extend(kept)
            result.count = len(kept)
            if verbose:
                print(f"  ✓ {cfg.id:<16} {cfg.kind:<8} {len(kept):>4} events")
        except Exception as exc:  # report and continue; never abort the whole run
            result.error = str(exc)
            if verbose:
                print(f"  ✗ {cfg.id:<16} {cfg.kind:<8} FAILED: {exc}", file=sys.stderr)
        results.append(result)

    return results, events


def run(
    config: Config,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    verbose: bool = True,
    force: bool = False,
) -> RunResult:
    window_start, window_end = parse_window(config.back_days, config.forward_days, now)
    if verbose:
        print(
            f"Window: {window_start.date()} → {window_end.date()} "
            f"({config.back_days}d back, {config.forward_days}d forward)"
        )
        print("Sources:")

    source_results, events = collect(config, window_start, window_end, verbose=verbose)
    result = RunResult(window_start=window_start, window_end=window_end, sources=source_results)

    if not result.any_source_succeeded:
        raise RuntimeError(
            "every source failed; refusing to continue. Syncing an empty set would "
            "delete the whole unified calendar."
        )

    if config.dedupe:
        source_cfgs = {c.id: c for c in config.sources}
        events, dropped, pairs = deduplicate(events, source_cfgs)
        result.duplicates_dropped = dropped
        result.dropped_pairs = pairs
        if verbose and dropped:
            print(f"\nDeduplicated: {dropped} duplicate(s) collapsed")
            for winner, loser in pairs[:10]:
                print(
                    f"  kept {winner.ref.source_id:<12} dropped {loser.ref.source_id:<12} "
                    f"{winner.start.date()} {winner.title[:48]!r}"
                )
            if len(pairs) > 10:
                print(f"  ... and {len(pairs) - 10} more")
    else:
        events.sort(key=lambda e: (e.start, e.title))

    result.events = events
    if verbose:
        print(f"\nMerged total: {len(events)} events")

    for sink_cfg in config.active_sinks:
        sink = build_sink(sink_cfg, config)
        plan = sink.plan(events, window_start, window_end)
        plan.duplicates_dropped = result.duplicates_dropped
        result.plans[sink_cfg.kind] = plan
        if verbose:
            print(f"\nSink [{sink_cfg.kind}]: {plan.summary()}")
        if dry_run:
            if verbose:
                _print_plan_detail(plan)
            continue

        guard = _partial_guard(config, result, force=force) or _deletion_guard(
            plan, config, result, force=force
        )
        if guard is not None:
            result.applied[sink_cfg.kind] = {"skipped": guard}
            print(f"  ! SKIPPED: {guard}", file=sys.stderr)
            continue

        outcome = sink.apply(plan)
        result.applied[sink_cfg.kind] = outcome
        if verbose:
            print(f"  applied: {outcome}")

    return result


def _print_plan_detail(plan: SyncPlan, limit: int = 15) -> None:
    for event in plan.to_create[:limit]:
        print(f"  + {event.start.date()} {event.title[:60]}")
    if len(plan.to_create) > limit:
        print(f"  + ... and {len(plan.to_create) - limit} more")
    for _, event in plan.to_update[:limit]:
        print(f"  ~ {event.start.date()} {event.title[:60]}")
    if len(plan.to_update) > limit:
        print(f"  ~ ... and {len(plan.to_update) - limit} more")
    for _, summary in plan.to_delete[:limit]:
        print(f"  - {summary[:60]}")
    if len(plan.to_delete) > limit:
        print(f"  - ... and {len(plan.to_delete) - limit} more")


def _partial_guard(config: Config, result: RunResult, force: bool) -> Optional[str]:
    """Refuse to publish an incomplete merge after a source failed."""
    if force or config.allow_partial or not result.failed_sources:
        return None
    names = ", ".join(s.source_id for s in result.failed_sources)
    return (
        f"source(s) failed: {names}. Writing now would drop their events from the "
        "unified calendar, so the previous complete version has been left in place. "
        "Fix the source, or set allow_partial: true / pass --force to publish anyway."
    )


def _deletion_guard(
    plan: SyncPlan, config: Config, result: RunResult, force: bool
) -> Optional[str]:
    """Refuse a run that would wipe out most of the unified calendar.

    A source that briefly returns nothing -- an expired Outlook publish link, a
    revoked Notion token, a network blip -- otherwise looks exactly like "the user
    deleted all their meetings", and the mirror would faithfully delete them.
    """
    if force or config.max_delete_ratio >= 1.0:
        return None

    existing = len(plan.to_delete) + len(plan.to_update) + plan.unchanged
    if existing < config.delete_guard_min_events:
        return None

    ratio = len(plan.to_delete) / existing
    if ratio <= config.max_delete_ratio:
        return None

    failed = ", ".join(s.source_id for s in result.failed_sources) or "none"
    return (
        f"plan would delete {len(plan.to_delete)}/{existing} events "
        f"({ratio:.0%} > max_delete_ratio {config.max_delete_ratio:.0%}). "
        f"Failed sources: {failed}. "
        "Re-run with --force once you have confirmed this is intended."
    )
