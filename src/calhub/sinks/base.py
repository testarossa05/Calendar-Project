"""Sink adapter contract."""

from __future__ import annotations

import abc
from typing import Any

from ..config import Config, SinkConfig
from ..models import Event, SyncPlan


class Sink(abc.ABC):
    """Writes the merged event set to one destination."""

    kind: str = "base"

    def __init__(self, cfg: SinkConfig, app: Config) -> None:
        self.cfg = cfg
        self.app = app

    @abc.abstractmethod
    def plan(self, events: list[Event], window_start, window_end) -> SyncPlan:
        """Compare desired state against current state without writing anything."""

    @abc.abstractmethod
    def apply(self, plan: SyncPlan) -> dict[str, int]:
        """Execute a plan. Returns counts of what actually happened."""

    def option(self, name: str, default: Any = None, required: bool = False) -> Any:
        value = self.cfg.options.get(name, default)
        if required and (value is None or value == ""):
            raise ValueError(
                f"sink {self.kind!r}: missing required option {name!r}"
            )
        return value


class SinkError(Exception):
    pass
