"""Tests for the DevTools pipe driver, against a stand-in browser process."""

import subprocess
import sys
from pathlib import Path

import pytest

from productivity_agent_tools.errors import ConfigError
from productivity_agent_tools.servicenow import browser as browser_module
from productivity_agent_tools.servicenow.browser import (
    Browser,
    BrowserCookie,
    BrowserError,
    find_browser,
)

FAKE = Path(__file__).parent / "fake_browser.py"
URL = "https://desk.example/"


@pytest.fixture
def binary(tmp_path: Path) -> str:
    script = tmp_path / "fake-browser"
    script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    script.chmod(0o755)
    return str(script)


def test_it_lists_tabs_on_the_host_and_reads_its_cookies(binary: str) -> None:
    with Browser(binary, URL) as browser:
        profile = Path(browser.profile)
        assert profile.is_dir()
        assert browser.version() == "Fake/1.0"
        assert browser.page_paths("desk.example") == ["/home?tab=1", "/"]
        assert browser.page_paths("other.example") == []
        cookies = {cookie.name: cookie.value for cookie in browser.cookies("desk.example")}
    assert cookies["JSESSIONID"] == "abc"
    assert cookies["route"] == "r1"
    assert "other" not in cookies and "" not in cookies
    assert "--remote-debugging-pipe" in cookies["flags"]
    assert f"--user-data-dir={profile}" in cookies["flags"]
    assert not profile.exists()


def test_extra_flags_are_passed_to_the_browser(binary: str) -> None:
    with Browser(binary, URL, ["--headless=new"]) as browser:
        (flags,) = [c.value for c in browser.cookies("desk.example") if c.name == "flags"]
    assert "--headless=new" in flags


def test_a_cookie_is_matched_by_domain_and_never_shows_its_value() -> None:
    cookie = BrowserCookie(name="a", value="s3cret", domain="example.mil")
    assert cookie.sent_to("desk.example.mil:8443")
    assert cookie.sent_to("EXAMPLE.mil")
    assert not cookie.sent_to("notexample.mil")
    assert "s3cret" not in repr(cookie)


def test_a_refused_command_names_the_command_and_reason(
    binary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_BROWSER", "refuse")
    with Browser(binary, URL) as browser:
        with pytest.raises(BrowserError, match=r"refused Storage\.getCookies: Not allowed"):
            browser.cookies("desk.example")


def test_a_closed_window_is_reported(binary: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_BROWSER", "vanish")
    with Browser(binary, URL) as browser:
        with pytest.raises(BrowserError, match="closed before the sign-in finished"):
            browser.page_paths("desk.example")
        with pytest.raises(BrowserError, match="closed before the sign-in finished"):
            browser.version()
        profile = Path(browser.profile)
    assert not profile.exists()


def test_a_browser_that_stops_answering_is_reported_and_stopped(
    binary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_BROWSER", "silent")
    monkeypatch.setattr(browser_module, "REPLY_SECONDS", 0.2)
    with Browser(binary, URL) as browser:
        with pytest.raises(BrowserError, match=r"did not answer Target\.getTargets"):
            browser.page_paths("desk.example")
        process = browser._process
    assert process.poll() is not None


def test_a_browser_that_ignores_being_asked_to_stop_is_killed(
    binary: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_BROWSER", "stubborn")
    monkeypatch.setattr(browser_module, "REPLY_SECONDS", 0.2)
    monkeypatch.setattr(browser_module, "EXIT_SECONDS", 0.2)
    with Browser(binary, URL) as browser:
        assert browser.version() == "Fake/1.0"
        process = browser._process
    assert process.poll() is not None


def test_a_browser_that_cannot_start_leaves_nothing_behind(tmp_path: Path) -> None:
    before = set(Path(browser_module.tempfile.gettempdir()).glob("servicenow-login-*"))
    with pytest.raises(BrowserError, match="Could not start the browser"):
        Browser(str(tmp_path / "missing"), URL)
    assert set(Path(browser_module.tempfile.gettempdir()).glob("servicenow-login-*")) == before


def test_find_browser_takes_the_configured_one_or_the_first_installed(tmp_path: Path) -> None:
    real = tmp_path / "browser"
    real.write_text("")
    assert find_browser(str(real)) == str(real)
    assert find_browser(None, ["no-such-browser-xyz", str(real)]) == str(real)
    assert find_browser("sh").endswith("/sh")
    with pytest.raises(ConfigError, match="configured browser 'no-such-browser-xyz'"):
        find_browser("no-such-browser-xyz")
    with pytest.raises(ConfigError, match="No Chrome or Edge was found"):
        find_browser(None, ["no-such-browser-xyz"])


def test_it_starts_whatever_descriptors_3_and_4_are_in_this_process(binary: str) -> None:
    """Descriptor 3 may be open here yet missing in the child, as an event loop's kqueue is."""
    script = (
        "import os, sys\n"
        "os.closerange(3, 64)\n"
        "held = os.open(os.devnull, os.O_RDONLY)\n"
        "assert held == 3\n"
        "os.register_at_fork(after_in_child=lambda: os.close(held))\n"
        "from productivity_agent_tools.servicenow.browser import Browser\n"
        "with Browser(sys.argv[1], 'https://desk.example/') as browser:\n"
        "    names = sorted(c.name for c in browser.cookies('desk.example'))\n"
        "    print(browser.version(), names)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, binary], capture_output=True, text=True, timeout=30
    )
    assert done.stdout.strip() == "Fake/1.0 ['JSESSIONID', 'flags', 'route']", done.stderr
