from pathlib import Path

import pytest

from productivity_agent_tools.config import (
    DEFAULT_PRESETS,
    apply_overrides,
    load_settings,
    parse_settings,
)
from productivity_agent_tools.errors import ConfigError


def test_safe_defaults() -> None:
    settings = parse_settings({"base_url": "https://host.example/"})
    assert settings.base_url == "https://host.example"
    assert settings.dry_run is True
    assert settings.cookies == {}
    assert settings.jira.path == "/jira"
    assert settings.jira.presets == DEFAULT_PRESETS


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", ""), ("/", ""), ("jira", "/jira"), ("/jira/", "/jira"), ("/apps/jira", "/apps/jira")],
)
def test_jira_path_is_normalized_including_site_root(configured: str, expected: str) -> None:
    settings = parse_settings({"base_url": "https://host.example", "jira": {"path": configured}})
    assert settings.jira.path == expected


def test_integer_booleans_are_accepted() -> None:
    settings = parse_settings({"base_url": "https://host.example", "dry_run": 0})
    assert settings.dry_run is False
    assert parse_settings({"base_url": "https://host.example", "dry_run": 1}).dry_run is True


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"base_url": "http://host.example"},
        {"base_url": "https://host.example", "dry_run": "yes"},
        {"base_url": "https://host.example", "dry_run": 2},
        {"base_url": "https://host.example", "jira": {"presets": {"x": 1}}},
        {"base_url": "https://host.example", "jira": {"default_max_results": 0}},
        {"base_url": "https://host.example", "cookies": "consent=yes"},
        {"base_url": "https://host.example", "cookies": {"consent": 1}},
        {"base_url": "https://host.example", "cookies": {"a;b": "x"}},
    ],
)
def test_invalid_config_is_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_settings(raw)


def test_cli_overrides_win_and_none_keeps_file_value() -> None:
    settings = parse_settings(
        {"base_url": "https://host.example", "cookies": {"consent": "yes", "route": "a1"}}
    )
    unchanged = apply_overrides(settings)
    assert (unchanged.dry_run, unchanged.cookies) == (True, {"consent": "yes", "route": "a1"})
    changed = apply_overrides(settings, dry_run=False, cookies=["route=b2", " extra = v=1 "])
    assert changed.dry_run is False
    assert changed.cookies == {"consent": "yes", "route": "b2", "extra": "v=1"}
    assert settings.cookies == {"consent": "yes", "route": "a1"}


@pytest.mark.parametrize("pair", ["novalue", "=x", "bad name=x", "a=b;c", "a=b\nSet-Cookie: x"])
def test_malformed_cookie_overrides_are_rejected(pair: str) -> None:
    settings = parse_settings({"base_url": "https://host.example"})
    with pytest.raises(ConfigError):
        apply_overrides(settings, cookies=[pair])


async def test_load_settings_reads_file_and_custom_presets(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'base_url = "https://host.example"\n'
        'ca_bundle = "~/roots.pem"\n'
        "[jira]\n"
        'path = "tracker/"\n'
        "[jira.presets]\n"
        'mine = "assignee = currentUser()"\n'
    )
    settings = await load_settings(path)
    assert settings.ca_bundle == Path("~/roots.pem").expanduser()
    assert settings.jira.path == "/tracker"
    assert settings.jira.presets == {"mine": "assignee = currentUser()"}


async def test_missing_config_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.example\.toml"):
        await load_settings(tmp_path / "nope.toml")
