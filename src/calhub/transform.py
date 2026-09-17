"""Per-source presentation rules applied before an event reaches a sink."""

from __future__ import annotations

from dataclasses import replace

from .config import Config, SourceConfig
from .models import Event
from .util import truncate


def apply_source_rules(event: Event, cfg: SourceConfig, app: Config) -> Event:
    """Apply privacy masking, title prefixing and length limits.

    ``privacy: busy`` exists so that confidential company meetings can be mirrored
    as an opaque block. The time is what makes the unified calendar useful; the
    subject line is what must not leak into a personal Google account.
    """
    title = event.title
    description = event.description
    location = event.location
    url = event.url

    if cfg.privacy == "busy":
        title = cfg.busy_title
        description = None
        location = None
        url = None

    if cfg.prefix:
        title = f"{cfg.prefix}{title}"

    return replace(
        event,
        match_title=event.match_title or event.title,
        title=truncate(title, 900) or "(no title)",
        description=truncate(description, app.max_description_chars),
        location=truncate(location, 900),
        url=url,
    )


def should_keep(event: Event, app: Config) -> bool:
    """Filter out entries that are reminders rather than real commitments."""
    if app.drop_shorter_than_minutes <= 0:
        return True
    if event.all_day:
        return True
    minutes = (event.end - event.start).total_seconds() / 60
    return minutes >= app.drop_shorter_than_minutes
