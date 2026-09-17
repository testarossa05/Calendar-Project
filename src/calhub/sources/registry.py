"""Maps a source ``kind`` in the config to its adapter class."""

from __future__ import annotations

from ..config import Config, SourceConfig
from .base import Source
from .caldav_source import CalDavSource
from .eventkit_source import EventKitSource
from .google_source import GoogleSource
from .ics_source import IcsSource
from .local_source import LocalSource
from .msgraph_source import MsGraphSource
from .notion_source import NotionSource

SOURCE_TYPES: dict[str, type[Source]] = {
    IcsSource.kind: IcsSource,
    LocalSource.kind: LocalSource,
    MsGraphSource.kind: MsGraphSource,
    NotionSource.kind: NotionSource,
    GoogleSource.kind: GoogleSource,
    CalDavSource.kind: CalDavSource,
    EventKitSource.kind: EventKitSource,
}


def build_source(cfg: SourceConfig, app: Config) -> Source:
    try:
        cls = SOURCE_TYPES[cfg.kind]
    except KeyError:
        known = ", ".join(sorted(SOURCE_TYPES))
        raise ValueError(
            f"source {cfg.id!r}: unknown kind {cfg.kind!r}. Known kinds: {known}"
        ) from None
    return cls(cfg, app)
