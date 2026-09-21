import os

import pytest

from calhub.config import ConfigError, load_config

BASE = """
timezone: Asia/Seoul
sources:
  - id: work
    kind: ics
    url: https://example.com/a.ics
sinks:
  - kind: ics_file
    path: out/u.ics
"""


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_a_minimal_config(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.timezone == "Asia/Seoul"
    assert cfg.active_sources[0].id == "work"
    assert cfg.active_sources[0].options["url"] == "https://example.com/a.ics"
    assert cfg.active_sinks[0].kind == "ics_file"


def test_missing_file_explains_the_fix(tmp_path):
    with pytest.raises(ConfigError, match="config.example.yaml"):
        load_config(tmp_path / "nope.yaml")


def test_env_var_is_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_ICS", "https://secret.example.com/feed.ics")
    text = BASE.replace("https://example.com/a.ics", "${MY_ICS}")
    cfg = load_config(write(tmp_path, text))
    assert cfg.active_sources[0].options["url"] == "https://secret.example.com/feed.ics"


def test_unset_env_var_is_reported_with_its_path(tmp_path, monkeypatch):
    monkeypatch.delenv("ABSENT_VAR", raising=False)
    text = BASE.replace("https://example.com/a.ics", "${ABSENT_VAR}")
    with pytest.raises(ConfigError, match="ABSENT_VAR"):
        load_config(write(tmp_path, text))


def test_env_var_default_is_used_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("ABSENT_VAR", raising=False)
    text = BASE.replace("https://example.com/a.ics", "${ABSENT_VAR:-https://fallback/a.ics}")
    cfg = load_config(write(tmp_path, text))
    assert cfg.active_sources[0].options["url"] == "https://fallback/a.ics"


def test_duplicate_source_ids_are_rejected(tmp_path):
    text = """
sources:
  - id: work
    kind: ics
    url: https://example.com/a.ics
  - id: work
    kind: ics
    url: https://example.com/b.ics
sinks:
  - kind: ics_file
    path: out/u.ics
"""
    with pytest.raises(ConfigError, match="duplicate source id"):
        load_config(write(tmp_path, text))


def test_missing_required_key_is_reported(tmp_path):
    text = BASE.replace("    kind: ics\n", "")
    with pytest.raises(ConfigError, match="missing required key 'kind'"):
        load_config(write(tmp_path, text))


def test_empty_sources_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="sources is empty"):
        load_config(write(tmp_path, "sources: []\nsinks:\n  - kind: ics_file\n"))


def test_empty_sinks_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="sinks is empty"):
        load_config(write(tmp_path, BASE.replace("sinks:\n  - kind: ics_file\n    path: out/u.ics\n", "sinks: []\n")))


def test_invalid_privacy_value_is_rejected(tmp_path):
    text = BASE.replace("    kind: ics\n", "    kind: ics\n    privacy: secret\n")
    with pytest.raises(ConfigError, match="privacy must be"):
        load_config(write(tmp_path, text))


def test_disabled_source_is_excluded(tmp_path):
    text = BASE.replace("    kind: ics\n", "    kind: ics\n    enabled: false\n")
    cfg = load_config(write(tmp_path, text))
    assert cfg.active_sources == []
    assert len(cfg.sources) == 1


def test_safety_defaults(tmp_path):
    cfg = load_config(write(tmp_path, BASE))
    assert cfg.max_delete_ratio == 0.5
    assert cfg.delete_guard_min_events == 10


def test_inline_env_var_inside_a_path_is_substituted(tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISH_SLUG", "a1b2c3")
    text = BASE.replace("    path: out/u.ics", "    path: public/${PUBLISH_SLUG}/unified.ics")
    cfg = load_config(write(tmp_path, text))
    assert cfg.active_sinks[0].options["path"] == "public/a1b2c3/unified.ics"


def test_inline_env_var_that_is_unset_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLISH_SLUG", raising=False)
    text = BASE.replace("    path: out/u.ics", "    path: public/${PUBLISH_SLUG}/unified.ics")
    with pytest.raises(ConfigError, match="PUBLISH_SLUG"):
        load_config(write(tmp_path, text))


def test_multiple_inline_refs_in_one_value(tmp_path, monkeypatch):
    monkeypatch.setenv("A", "x")
    monkeypatch.setenv("B", "y")
    text = BASE.replace("    path: out/u.ics", "    path: ${A}/mid/${B}.ics")
    cfg = load_config(write(tmp_path, text))
    assert cfg.active_sinks[0].options["path"] == "x/mid/y.ics"


class TestDisabledEntriesAreFree:
    """A disabled source must not demand the credentials it will never use.

    The runbook tells the reader to switch sources off while bringing them up one
    at a time, so requiring their environment variables anyway would force them to
    delete the blocks instead.
    """

    DISABLED = """
sources:
  - id: work
    kind: msgraph
    enabled: false
    client_id: ${ABSENT_CLIENT_ID}
  - id: family
    kind: local
    file: events.yaml
sinks:
  - kind: ics_file
    path: out/u.ics
"""

    def test_disabled_source_ignores_unset_variables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABSENT_CLIENT_ID", raising=False)
        cfg = load_config(write(tmp_path, self.DISABLED))
        assert [s.id for s in cfg.active_sources] == ["family"]

    def test_enabled_source_still_requires_them(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABSENT_CLIENT_ID", raising=False)
        text = self.DISABLED.replace("    enabled: false\n", "")
        with pytest.raises(ConfigError, match="ABSENT_CLIENT_ID"):
            load_config(write(tmp_path, text))

    def test_disabled_sink_ignores_unset_variables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABSENT_DB", raising=False)
        text = self.DISABLED + """  - kind: notion
    enabled: false
    database_id: ${ABSENT_DB}
    token: ${ABSENT_DB}
"""
        cfg = load_config(write(tmp_path, text))
        assert [s.kind for s in cfg.active_sinks] == ["ics_file"]

    def test_enabled_source_keeps_its_resolved_values(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ABSENT_CLIENT_ID", "real-value")
        text = self.DISABLED.replace("    enabled: false\n", "")
        cfg = load_config(write(tmp_path, text))
        assert cfg.sources[0].options["client_id"] == "real-value"


def test_shipped_example_config_works_on_a_fresh_copy(tmp_path, monkeypatch):
    """Copying config.example.yaml must produce a usable config with no setup.

    install-macos.sh copies it and then runs the diagnostics, so an example that
    needs credentials makes a correct install look broken.
    """
    from pathlib import Path

    for name in list(os.environ):
        if name.startswith(("MS_", "NOTION_", "GOOGLE_", "ICLOUD_", "OUTLOOK_", "PUBLISH_")):
            monkeypatch.delenv(name, raising=False)

    example = Path(__file__).parent.parent / "config.example.yaml"
    cfg = load_config(example)
    assert cfg.active_sources, "at least one source must work without credentials"
    for source in cfg.active_sources:
        assert source.kind == "local", (
            f"source {source.id!r} is enabled by default but needs credentials"
        )
    assert cfg.active_sinks
    for sink in cfg.active_sinks:
        for value in sink.options.values():
            assert "${" not in str(value), f"default sink still needs {value!r}"


def test_example_config_has_no_duplicate_ids_across_commented_alternatives():
    """The commented-out alternatives must not collide with the active entries.

    They are alternative routes to the same calendar, so they used to share an
    id; uncommenting one then failed with a duplicate-id error at exactly the
    point the runbook tells the reader to switch routes.
    """
    import re
    from pathlib import Path

    text = (Path(__file__).parent.parent / "config.example.yaml").read_text(encoding="utf-8")
    ids = re.findall(r"^\s*#?\s*- id:\s*(\S+)", text, re.M)
    assert len(ids) == len(set(ids)), f"duplicate id among example entries: {ids}"
