from pathlib import Path

import keyring
import pytest

from productivity_agent_tools.errors import ConfigError, TokenNotFoundError
from productivity_agent_tools.tokens import (
    ResolvedToken,
    TokenSources,
    parse_env_file,
    resolve_token,
)


@pytest.fixture(autouse=True)
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], str]:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda service, user: store.get((service, user)))
    return store


def test_parse_env_file_handles_export_quotes_and_comments() -> None:
    text = "# comment\nexport JIRA_TOKEN='abc'\nOTHER=\"x y\"\nPLAIN=1\n\nnot a pair\n"
    assert parse_env_file(text) == {"JIRA_TOKEN": "abc", "OTHER": "x y", "PLAIN": "1"}


async def test_precedence_env_then_file_then_keychain(
    tmp_path: Path, keychain: dict[tuple[str, str], str]
) -> None:
    env_file = tmp_path / "jira_token.env"
    env_file.write_text("JIRA_TOKEN=from-file\n")
    keychain[("productivity-agent-tools", "jira")] = "from-keychain"

    token = await resolve_token("jira", TokenSources(env_file=env_file), {"JIRA_TOKEN": "from-env"})
    assert (token.value, token.source) == ("from-env", "environment variable JIRA_TOKEN")

    token = await resolve_token("jira", TokenSources(env_file=env_file), {})
    assert token.value == "from-file"
    assert str(env_file) in token.source

    token = await resolve_token("jira", None, {})
    assert (token.value, token.source) == ("from-keychain", "the system keychain")


async def test_no_token_anywhere_explains_the_options() -> None:
    with pytest.raises(TokenNotFoundError, match="set-token"):
        await resolve_token("jira", None, {})


async def test_unreadable_env_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        await resolve_token("jira", TokenSources(env_file=tmp_path / "missing.env"), {})


def test_token_value_is_not_in_repr() -> None:
    assert "s3cret" not in repr(ResolvedToken(value="s3cret", source="test"))
