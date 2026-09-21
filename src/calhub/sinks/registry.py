"""Maps a sink ``kind`` in the config to its adapter class."""

from __future__ import annotations

from ..config import Config, SinkConfig
from .base import Sink
from .google_sink import GoogleSink
from .ics_file import IcsFileSink
from .notion_sink import NotionSink

SINK_TYPES: dict[str, type[Sink]] = {
    GoogleSink.kind: GoogleSink,
    IcsFileSink.kind: IcsFileSink,
    NotionSink.kind: NotionSink,
}


def build_sink(cfg: SinkConfig, app: Config) -> Sink:
    try:
        cls = SINK_TYPES[cfg.kind]
    except KeyError:
        known = ", ".join(sorted(SINK_TYPES))
        raise ValueError(f"unknown sink kind {cfg.kind!r}. Known kinds: {known}") from None
    return cls(cfg, app)
