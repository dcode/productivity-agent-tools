"""Tests for where a token comes from: token_cmd, token_env_file at each level, and expansion."""

import sys
from pathlib import Path

import pytest

from productivity_agent_tools import tokens
from productivity_agent_tools.config import apply_overrides, parse_settings
from productivity_agent_tools.errors import ConfigError, TokenNotFoundError
from productivity_agent_tools.tokens import TokenSources, resolve_token

PY = sys.executable


def command(code: str) -> tuple[str, ...]:
    return (PY, "-c", code)


@pytest.fixture(autouse=True)
def empty_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokens.keyring, "get_password", lambda service, name: None)


# --- token_cmd ---------------------------------------------------------------------------


async def test_the_command_prints_the_token_for_the_product_it_is_run_for() -> None:
    sources = TokenSources(
        command=(*command("import sys; print('tok-' + sys.argv[1])"), "{product}")
    )
    token = await resolve_token("jira", sources, {})
    assert token.value == "tok-jira"
    assert token.source == f"token_cmd ({PY})"
    assert "tok-jira" not in repr(token)


async def test_the_lookup_order_is_environment_then_command_then_file_then_keychain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "t.env"
    env_file.write_text("JIRA_TOKEN=from-file\n")
    both = TokenSources(env_file=env_file, command=command("print('from-cmd')"))
    assert (await resolve_token("jira", both, {"JIRA_TOKEN": "from-env"})).value == "from-env"
    assert (await resolve_token("jira", both, {})).value == "from-cmd"
    file_only = TokenSources(env_file=env_file)
    assert (await resolve_token("jira", file_only, {})).value == "from-file"
    monkeypatch.setattr(tokens.keyring, "get_password", lambda service, name: "from-keychain")
    assert (await resolve_token("jira", TokenSources(), {})).source == "the system keychain"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "import sys; print('denied', file=sys.stderr); sys.exit(3)",
            "exited with status 3: denied",
        ),
        ("import sys; sys.exit(2)", r"exited with status 2$"),
        ("print('')", "printed nothing"),
        ("print('one'); print('two')", "printed more than one line"),
    ],
)
async def test_a_failing_command_says_how_without_showing_its_output(
    code: str, message: str
) -> None:
    with pytest.raises(ConfigError, match=rf"token_cmd for jira \(.*\) {message}"):
        await resolve_token("jira", TokenSources(command=command(code)), {})


async def test_a_command_that_hangs_is_stopped() -> None:
    sources = TokenSources(command=command("import time; time.sleep(30)"), command_timeout=0.2)
    with pytest.raises(ConfigError, match=r"did not finish within 0\.2 seconds"):
        await resolve_token("jira", sources, {})


async def test_a_command_that_cannot_start_is_reported(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-program")
    with pytest.raises(ConfigError, match="Could not start token_cmd for jira"):
        await resolve_token("jira", TokenSources(command=(missing,)), {})


async def test_the_not_found_message_names_what_was_looked_in(tmp_path: Path) -> None:
    env_file = tmp_path / "t.env"
    env_file.write_text("OTHER_TOKEN=x\n")
    with pytest.raises(TokenNotFoundError) as info:
        await resolve_token("jira", TokenSources(env_file=env_file), {})
    message = str(info.value)
    assert f"env file {env_file} (no JIRA_TOKEN in it)" in message
    assert "set-token jira" in message
    assert "token_cmd or token_env_file" in message and "[jira]" in message


# --- config ------------------------------------------------------------------------------


def settings(raw: dict[str, object]) -> object:
    return parse_settings({"base_url": "https://host.example", **raw})


def test_paths_expand_environment_variables_and_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/pat")
    monkeypatch.setenv("SECRETS", "/vault")
    found = parse_settings(
        {
            "base_url": "https://host.example",
            "token_env_file": "${HOME}/.secrets/jira_token.env",
            "ca_bundle": "$SECRETS/ca.pem",
            "jira": {"token_env_file": "~/jira.env"},
        }
    )
    assert found.token_env_file == Path("/home/pat/.secrets/jira_token.env")
    assert found.ca_bundle == Path("/vault/ca.pem")
    assert found.products["jira"].token_env_file == Path("/home/pat/jira.env")


def test_an_unset_variable_is_an_error_naming_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    with pytest.raises(ConfigError, match=r"'jira\.token_env_file' uses \$\{NOT_SET_ANYWHERE\}"):
        parse_settings(
            {
                "base_url": "https://host.example",
                "jira": {"token_env_file": "${NOT_SET_ANYWHERE}/t.env"},
            }
        )


def test_token_cmd_is_a_command_line_or_a_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/pat")
    found = parse_settings(
        {
            "base_url": "https://host.example",
            "token_cmd": "sops -d --extract '[\"{product}\"]' ~/.secrets/tokens.yaml",
            "jira": {"token_cmd": ["pass", "show", "work/jira"]},
        }
    )
    assert found.token_cmd == (
        "sops",
        "-d",
        "--extract",
        '["{product}"]',
        "/home/pat/.secrets/tokens.yaml",
    )
    assert found.products["jira"].token_cmd == ("pass", "show", "work/jira")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (7, "must be a command line or a list of strings"),
        (["ok", 3], "must be a command line or a list of strings"),
        ("", "must name a program"),
        ([], "must name a program"),
        ("sops 'unclosed", "is not a valid command line"),
    ],
)
def test_a_bad_token_cmd_is_refused(value: object, message: str) -> None:
    with pytest.raises(ConfigError, match=rf"'jira\.token_cmd' {message}"):
        parse_settings({"base_url": "https://host.example", "jira": {"token_cmd": value}})


def test_each_source_comes_from_the_most_specific_level() -> None:
    found = parse_settings(
        {
            "base_url": "https://host.example",
            "token_env_file": "/top.env",
            "token_cmd": ["top-cmd"],
            "group": {"atlassian": {"token_env_file": "/group.env"}},
            "jira": {"group": "atlassian", "token_cmd": ["jira-cmd"]},
            "bitbucket": {"group": "atlassian"},
            "confluence": {},
        }
    )
    jira = found.token_sources_for(found.products["jira"])
    bitbucket = found.token_sources_for(found.products["bitbucket"])
    confluence = found.token_sources_for(found.products["confluence"])
    assert (jira.command, jira.env_file) == (("jira-cmd",), Path("/group.env"))
    assert (bitbucket.command, bitbucket.env_file) == (("top-cmd",), Path("/group.env"))
    assert (confluence.command, confluence.env_file) == (("top-cmd",), Path("/top.env"))


def test_the_command_line_env_file_overrides_every_level() -> None:
    found = parse_settings(
        {
            "base_url": "https://host.example",
            "group": {"atlassian": {"token_env_file": "/group.env"}},
            "jira": {"group": "atlassian", "token_env_file": "/jira.env"},
        }
    )
    forced = apply_overrides(found, token_env_file=Path("/flag.env"))
    assert forced.token_sources_for(forced.products["jira"]).env_file == Path("/flag.env")
