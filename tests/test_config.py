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
