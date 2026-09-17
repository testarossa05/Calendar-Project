"""Cross-source duplicate removal.

The same meeting routinely arrives twice: an Outlook invitation that the user also
mirrored into Notion, or a Teams meeting that Google already holds. Mirroring both
produces a double-booked-looking calendar, which is exactly the problem this tool
exists to solve.

Two events are treated as the same meeting when their normalised title and their
start/end minute match. The copy from the highest-priority source (lowest
``priority`` number) wins; ties break on source id so a run is deterministic.
"""

from __future__ import annotations

from collections import defaultdict

from .config import SourceConfig
from .models import Event


def deduplicate(
    events: list[Event], source_cfgs: dict[str, SourceConfig]
) -> tuple[list[Event], int, list[tuple[Event, Event]]]:
    """Return (kept_events, dropped_count, dropped_pairs).

    ``dropped_pairs`` is ``(winner, loser)`` for each removed duplicate, so a
    dry run can explain precisely what it collapsed.
    """
    buckets: dict[tuple, list[Event]] = defaultdict(list)
    for event in events:
        buckets[event.dedupe_key].append(event)

    kept: list[Event] = []
    dropped_pairs: list[tuple[Event, Event]] = []

    for group in buckets.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        ordered = sorted(group, key=lambda e: _rank(e, source_cfgs))
        winner = ordered[0]
        kept.append(winner)
        for loser in ordered[1:]:
            dropped_pairs.append((winner, loser))

    kept.sort(key=lambda e: (e.start, e.title))
    return kept, len(dropped_pairs), dropped_pairs


def _rank(event: Event, source_cfgs: dict[str, SourceConfig]) -> tuple[int, str, str]:
    cfg = source_cfgs.get(event.ref.source_id)
    priority = cfg.priority if cfg else 100
    return (priority, event.ref.source_id, event.ref.uid)
