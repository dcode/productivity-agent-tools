"""Tests for the [microsoft] config table."""

from pathlib import Path

import pytest

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.microsoft.settings import (
    load_microsoft_settings,
    parse_microsoft_settings,
    redeem_config,
)

TABLE = {
    "client_id": "client-123",
    "tenant": "tenant-abc",
    "authority": "login.microsoftonline.us",
    "origin": "https://webmail.example.mil",
    "redirect_uri": "https://webmail.example.mil/redirect.html",
    "scope": "https://outlook.example.us/.default",
}

CONFIG_TEXT = """
[microsoft]
client_id = "client-123"
tenant = "tenant-abc"
authority = "login.microsoftonline.us"
origin = "https://webmail.example.mil"
redirect_uri = "https://webmail.example.mil/redirect.html"
scope = "https://outlook.example.us/.default"
base_url = "https://outlook.example.us/api/v2.0"
"""


def test_parse_reads_all_the_oauth_fields() -> None:
    settings = parse_microsoft_settings(TABLE)
    assert settings.client_id == "client-123"
    assert settings.scope == "https://outlook.example.us/.default"


def test_parse_reads_the_base_url_override() -> None:
    settings = parse_microsoft_settings(
        {**TABLE, "base_url": "https://outlook.example.us/api/v2.0"}
    )
    assert settings.base_url == "https://outlook.example.us/api/v2.0"


@pytest.mark.parametrize("missing", list(TABLE))
def test_parse_requires_each_oauth_field(missing: str) -> None:
    table = {key: value for key, value in TABLE.items() if key != missing}
    with pytest.raises(ConfigError, match=rf"microsoft\.{missing}"):
        parse_microsoft_settings(table)


def test_parse_rejects_a_blank_field() -> None:
    with pytest.raises(ConfigError, match=r"microsoft\.origin"):
        parse_microsoft_settings({**TABLE, "origin": "   "})


def test_redeem_config_carries_the_oauth_fields() -> None:
    config = redeem_config(parse_microsoft_settings(TABLE))
    assert config.client_id == "client-123"
    assert config.token_endpoint == (
        "https://login.microsoftonline.us/tenant-abc/oauth2/v2.0/token"
    )


async def test_load_reads_the_microsoft_table(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(CONFIG_TEXT)
    settings = await load_microsoft_settings(path)
    assert settings.tenant == "tenant-abc"
    assert settings.base_url == "https://outlook.example.us/api/v2.0"


async def test_load_without_a_microsoft_table_explains(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('base_url = "https://host.example"\n[jira]\n')
    with pytest.raises(ConfigError, match=r"No \[microsoft\] table"):
        await load_microsoft_settings(path)


async def test_load_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        await load_microsoft_settings(tmp_path / "nope.toml")


async def test_load_reports_an_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Could not read"):
        await load_microsoft_settings(tmp_path)  # a directory, not a file


async def test_load_reports_bad_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[microsoft]\nclient_id = \n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        await load_microsoft_settings(path)
