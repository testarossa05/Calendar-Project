"""Source adapter contract."""

from __future__ import annotations

import abc
from datetime import datetime

from ..config import Config, SourceConfig
from ..models import Event


class Source(abc.ABC):
    """Reads events from one upstream calendar and normalises them."""

    kind: str = "base"

    def __init__(self, cfg: SourceConfig, app: Config) -> None:
        self.cfg = cfg
        self.app = app

    @abc.abstractmethod
    def fetch(self, window_start: datetime, window_end: datetime) -> list[Event]:
        """Return events overlapping [window_start, window_end), as aware UTC."""

    def option(self, name: str, default=None, required: bool = False):
        value = self.cfg.options.get(name, default)
        if required and (value is None or value == ""):
            raise ValueError(
                f"source {self.cfg.id!r} ({self.kind}): missing required option {name!r}"
            )
        return value


class SourceError(Exception):
    """A source failed to load. One bad source must not abort the whole run."""

    def __init__(self, source_id: str, message: str) -> None:
        super().__init__(f"[{source_id}] {message}")
        self.source_id = source_id
