from pathlib import Path

import pytest

from productivity_agent_tools.config import (
    Connection,
    Settings,
    apply_overrides,
    load_settings,
    parse_settings,
)
from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.jira.settings import DEFAULT_PRESETS, JiraSettings


def _jira(settings: Settings) -> JiraSettings:
    found = settings.product("jira", JiraSettings)
    assert found is not None
    return found


def test_safe_defaults() -> None:
    settings = parse_settings({"base_url": "https://host.example/", "jira": {}})
    assert settings.base_url == "https://host.example"
    assert settings.dry_run is True
    assert settings.cookies == {}
    assert _jira(settings).path == "/jira"
    assert _jira(settings).presets == DEFAULT_PRESETS


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("", ""), ("/", ""), ("jira", "/jira"), ("/jira/", "/jira"), ("/apps/jira", "/apps/jira")],
)
def test_jira_path_is_normalized_including_site_root(configured: str, expected: str) -> None:
    settings = parse_settings({"base_url": "https://host.example", "jira": {"path": configured}})
    assert _jira(settings).path == expected


def test_integer_booleans_are_accepted() -> None:
    settings = parse_settings({"base_url": "https://host.example", "dry_run": 0})
    assert settings.dry_run is False
    assert parse_settings({"base_url": "https://host.example", "dry_run": 1}).dry_run is True


@pytest.mark.parametrize(
    "raw",
    [
        {"jira": {}},
        {"bitbucket": {}},
        {"base_url": "http://host.example"},
        {"base_url": "https://host.example", "dry_run": "yes"},
        {"base_url": "https://host.example", "dry_run": 2},
        {"base_url": "https://host.example", "jira": {"presets": {"x": 1}}},
        {"base_url": "https://host.example", "jira": {"default_max_results": 0}},
        {"base_url": "https://host.example", "cookies": "consent=yes"},
        {"base_url": "https://host.example", "cookies": {"consent": 1}},
        {"base_url": "https://host.example", "cookies": {"a;b": "x"}},
        {"base_url": "https://host.example", "log_level": "chatty"},
        {"base_url": "https://host.example", "log_level": 10},
        {"jira": {"path": ""}},
        {"jira": {"base_url": "http://jira.example"}},
        {"jira": {"base_url": 5}},
        {"base_url": "https://host.example", "jira": {"timeout_seconds": 0}},
        {"base_url": "https://host.example", "jira": {"cookies": {"consent": 1}}},
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
    assert _jira(settings).path == "/tracker"
    assert _jira(settings).presets == {"mine": "assignee = currentUser()"}


async def test_missing_config_file_explains_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"config\.example\.toml"):
        await load_settings(tmp_path / "nope.toml")


def test_log_level_defaults_to_info_and_flag_overrides_file() -> None:
    assert parse_settings({"base_url": "https://host.example"}).log_level == "info"
    settings = parse_settings({"base_url": "https://host.example", "log_level": "WARNING"})
    assert settings.log_level == "warning"
    assert apply_overrides(settings).log_level == "warning"
    assert apply_overrides(settings, log_level="Debug").log_level == "debug"
    with pytest.raises(ConfigError, match="debug, info, warning, error"):
        apply_overrides(settings, log_level="loud")


def test_product_inherits_the_top_level_connection() -> None:
    settings = parse_settings(
        {
            "base_url": "https://host.example/atlassian/",
            "ca_bundle": "/etc/roots.pem",
            "timeout_seconds": 12,
            "cookies": {"consent": "yes"},
            "jira": {},
        }
    )
    assert settings.connection_for(_jira(settings)) == Connection(
        base_url="https://host.example/atlassian",
        ca_bundle=Path("/etc/roots.pem"),
        cookies={"consent": "yes"},
        timeout_seconds=12.0,
    )


def test_product_on_its_own_host_overrides_each_connection_value() -> None:
    settings = parse_settings(
        {
            "base_url": "https://host.example",
            "ca_bundle": "/etc/roots.pem",
            "cookies": {"consent": "yes"},
            "jira": {
                "base_url": "https://jira.example/",
                "path": "",
                "ca_bundle": "/etc/jira.pem",
                "timeout_seconds": 5,
                "cookies": {"route": "a1"},
            },
        }
    )
    assert settings.connection_for(_jira(settings)) == Connection(
        base_url="https://jira.example",
        ca_bundle=Path("/etc/jira.pem"),
        cookies={"route": "a1"},
        timeout_seconds=5.0,
    )


def test_top_level_base_url_is_optional_when_the_product_names_its_own() -> None:
    settings = parse_settings({"jira": {"base_url": "https://jira.example", "path": ""}})
    assert settings.base_url is None
    assert settings.connection_for(_jira(settings)).base_url == "https://jira.example"
    with pytest.raises(ConfigError, match="No base_url is set"):
        parse_settings({"cookies": {"consent": "yes"}, "jira": {}})


def test_empty_product_cookie_table_sends_none_and_flag_cookies_still_apply() -> None:
    settings = parse_settings(
        {"base_url": "https://host.example", "cookies": {"consent": "yes"}, "jira": {"cookies": {}}}
    )
    assert settings.connection_for(_jira(settings)).cookies == {}
    changed = apply_overrides(settings, cookies=["route=a1"])
    assert changed.connection_for(_jira(changed)).cookies == {"route": "a1"}
    assert changed.cookies == {"consent": "yes", "route": "a1"}


def test_a_product_inherits_its_group_when_it_sets_nothing_itself() -> None:
    # The top level names no host and no cookies: the group supplies both.
    settings = parse_settings(
        {
            "group": {
                "atlassian": {
                    "base_url": "https://stormwind.example/",
                    "ca_bundle": "/etc/group.pem",
                    "timeout_seconds": 45,
                    "cookies": {"dodacknowledged": "1"},
                }
            },
            "jira": {"group": "atlassian", "path": "/jira"},
        }
    )
    assert list(settings.groups) == ["atlassian"]
    assert settings.connection_for(_jira(settings)) == Connection(
        base_url="https://stormwind.example",
        ca_bundle=Path("/etc/group.pem"),
        cookies={"dodacknowledged": "1"},
        timeout_seconds=45.0,
    )


def test_resolution_order_is_product_then_group_then_top_level() -> None:
    settings = parse_settings(
        {
            "base_url": "https://global.example",
            "ca_bundle": "/etc/global.pem",
            "timeout_seconds": 10,
            "cookies": {"global": "g"},
            "group": {
                "atlassian": {
                    "base_url": "https://group.example",
                    "timeout_seconds": 20,
                    "cookies": {"group": "x"},
                }
            },
            # Product overrides only base_url; ca_bundle falls to the top level (group sets
            # none), timeout and cookies come from the group.
            "jira": {"group": "atlassian", "path": "", "base_url": "https://jira.example"},
        }
    )
    assert settings.connection_for(_jira(settings)) == Connection(
        base_url="https://jira.example",
        ca_bundle=Path("/etc/global.pem"),
        cookies={"group": "x"},
        timeout_seconds=20.0,
    )


def test_a_product_without_a_group_still_uses_the_top_level() -> None:
    settings = parse_settings(
        {
            "base_url": "https://global.example",
            "cookies": {"global": "g"},
            "group": {"atlassian": {"base_url": "https://group.example"}},
            "jira": {},
        }
    )
    conn = settings.connection_for(_jira(settings))
    assert conn.base_url == "https://global.example" and conn.cookies == {"global": "g"}


def test_a_group_can_supply_the_base_url_with_no_top_level_one() -> None:
    settings = parse_settings(
        {
            "group": {"ms": {"base_url": "https://outlook.example/api/v2.0"}},
            "jira": {"group": "ms", "path": ""},
        }
    )
    assert settings.base_url is None
    assert settings.connection_for(_jira(settings)).base_url == "https://outlook.example/api/v2.0"


def test_cli_cookie_overrides_reach_group_inherited_cookies() -> None:
    settings = parse_settings(
        {
            "group": {"atlassian": {"base_url": "https://h.example", "cookies": {"a": "1"}}},
            "jira": {"group": "atlassian"},
        }
    )
    changed = apply_overrides(settings, cookies=["route=b2"])
    assert changed.connection_for(_jira(changed)).cookies == {"a": "1", "route": "b2"}
    # The source settings are untouched.
    assert settings.connection_for(_jira(settings)).cookies == {"a": "1"}


@pytest.mark.parametrize(
    "raw",
    [
        # A product names a group that has no table.
        {"base_url": "https://h.example", "jira": {"group": "nope"}},
        # [group] is not a table of tables.
        {"base_url": "https://h.example", "group": "atlassian", "jira": {}},
        # A [group.x] entry is not a table.
        {"base_url": "https://h.example", "group": {"atlassian": "x"}, "jira": {}},
        # A group value is invalid.
        {
            "group": {"atlassian": {"base_url": "http://insecure.example"}},
            "jira": {"group": "atlassian"},
        },
        {"group": {"atlassian": {"cookies": {"a": 1}}}, "jira": {"group": "atlassian"}},
    ],
)
def test_invalid_group_config_is_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_settings(raw)


def test_a_product_is_on_only_when_its_table_is_in_the_config() -> None:
    neither = parse_settings({"base_url": "https://host.example"})
    assert neither.products == {}
    assert neither.product("jira", JiraSettings) is None
    jira_only = parse_settings({"base_url": "https://host.example", "jira": {}})
    assert list(jira_only.products) == ["jira"]
    both = parse_settings({"base_url": "https://host.example", "bitbucket": {}, "jira": {}})
    assert list(both.products) == ["bitbucket", "jira"]
    untouched = apply_overrides(neither, cookies=["route=a1"])
    assert (untouched.products, untouched.cookies) == ({}, {"route": "a1"})
