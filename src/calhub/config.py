"""Configuration loading.

Secrets never live in the YAML file. Any string value may instead be written as
``${ENV_VAR}``, which is resolved from the environment at load time. That keeps
``config.yaml`` safe to share with a colleague and lets GitHub Actions inject
credentials as repository secrets.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

# A value that is *entirely* one reference, which may carry a ${VAR:-default}.
_ENV_WHOLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*))?\}$")
# A reference embedded in a longer string, e.g. "public/${SLUG}/unified.ics".
_ENV_INLINE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception):
    """Raised for a malformed or incomplete configuration."""


def _resolve(value: Any, path: str) -> Any:
    """Recursively expand ``${VAR}`` references against the environment."""
    if isinstance(value, dict):
        return {k: _resolve(v, f"{path}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, f"{path}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, str):
        match = _ENV_WHOLE.match(value.strip())
        if match:
            name, default = match.group(1), match.group(2)
            found = os.environ.get(name)
            if found is None or found == "":
                if default is not None:
                    return default
                raise ConfigError(
                    f"{path}: environment variable {name!r} is referenced but not set"
                )
            return found

        if "${" in value:
            def _sub(m: "re.Match[str]") -> str:
                name = m.group(1)
                found = os.environ.get(name)
                if found is None or found == "":
                    raise ConfigError(
                        f"{path}: environment variable {name!r} is referenced but not set"
                    )
                return found

            return _ENV_INLINE.sub(_sub, value)
    return value


@dataclass
class SourceConfig:
    id: str
    kind: str  # ics | notion | google | caldav
    enabled: bool = True
    # Priority decides which copy survives when the same meeting arrives twice.
    # Lower number wins.
    priority: int = 100
    # "full" mirrors title and details; "busy" replaces them with a placeholder so
    # confidential company meetings never leave the corporate boundary in clear text.
    privacy: str = "full"
    busy_title: str = "• Busy"
    prefix: Optional[str] = None  # e.g. "[PTKR] " prepended to the mirrored title
    options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.privacy not in ("full", "busy"):
            raise ConfigError(
                f"source {self.id!r}: privacy must be 'full' or 'busy', got {self.privacy!r}"
            )


@dataclass
class SinkConfig:
    kind: str  # google | ics_file
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    sources: list[SourceConfig]
    sinks: list[SinkConfig]
    timezone: str = "Asia/Seoul"
    back_days: int = 14
    forward_days: int = 120
    dedupe: bool = True
    # Events shorter than this are usually reminders, not meetings.
    drop_shorter_than_minutes: int = 0
    max_description_chars: int = 4000
    # Safety valve: abort rather than delete more than this share of the
    # target calendar in one run. 1.0 disables the guard.
    max_delete_ratio: float = 0.5
    delete_guard_min_events: int = 10
    # When a source fails, publishing the remaining events would quietly drop that
    # source's meetings from the unified calendar. Refusing to write leaves the
    # previous, complete calendar in place instead, which is the safer failure.
    allow_partial: bool = False

    @property
    def active_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    @property
    def active_sinks(self) -> list[SinkConfig]:
        return [s for s in self.sinks if s.enabled]


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"config file not found: {path}\n"
            "Copy config.example.yaml to config.yaml and fill it in."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    raw = _resolve(raw, "config")

    sources_raw = raw.get("sources") or []
    if not isinstance(sources_raw, list):
        raise ConfigError("config.sources must be a list")

    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(sources_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"config.sources[{i}] must be a mapping")
        entry = dict(item)
        for required in ("id", "kind"):
            if not entry.get(required):
                raise ConfigError(f"config.sources[{i}]: missing required key {required!r}")
        sid = str(entry.pop("id"))
        if sid in seen:
            raise ConfigError(f"duplicate source id {sid!r}")
        seen.add(sid)
        known = {"kind", "enabled", "priority", "privacy", "busy_title", "prefix"}
        options = {k: v for k, v in entry.items() if k not in known}
        sources.append(
            SourceConfig(
                id=sid,
                kind=str(entry["kind"]),
                enabled=bool(entry.get("enabled", True)),
                priority=int(entry.get("priority", 100)),
                privacy=str(entry.get("privacy", "full")),
                busy_title=str(entry.get("busy_title", "• Busy")),
                prefix=entry.get("prefix"),
                options=options,
            )
        )

    sinks_raw = raw.get("sinks") or []
    if not isinstance(sinks_raw, list):
        raise ConfigError("config.sinks must be a list")
    sinks: list[SinkConfig] = []
    for i, item in enumerate(sinks_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"config.sinks[{i}] must be a mapping")
        entry = dict(item)
        if not entry.get("kind"):
            raise ConfigError(f"config.sinks[{i}]: missing required key 'kind'")
        options = {k: v for k, v in entry.items() if k not in {"kind", "enabled"}}
        sinks.append(
            SinkConfig(
                kind=str(entry["kind"]),
                enabled=bool(entry.get("enabled", True)),
                options=options,
            )
        )

    if not sources:
        raise ConfigError("config.sources is empty - nothing to sync")
    if not sinks:
        raise ConfigError("config.sinks is empty - nowhere to sync to")

    return Config(
        sources=sources,
        sinks=sinks,
        timezone=str(raw.get("timezone", "Asia/Seoul")),
        back_days=int(raw.get("back_days", 14)),
        forward_days=int(raw.get("forward_days", 120)),
        dedupe=bool(raw.get("dedupe", True)),
        drop_shorter_than_minutes=int(raw.get("drop_shorter_than_minutes", 0)),
        max_description_chars=int(raw.get("max_description_chars", 4000)),
        max_delete_ratio=float(raw.get("max_delete_ratio", 0.5)),
        delete_guard_min_events=int(raw.get("delete_guard_min_events", 10)),
        allow_partial=bool(raw.get("allow_partial", False)),
    )
