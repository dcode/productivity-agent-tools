import json
import logging
import sys

import pytest
import structlog

from productivity_agent_tools.logs import configure_logging


def test_levels_filter_and_json_goes_to_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("info")
    log = structlog.get_logger("test")
    log.debug("hidden")
    log.info("shown", status=302)
    captured = capsys.readouterr()
    assert captured.out == ""
    event = json.loads(captured.err)
    assert (event["event"], event["level"], event["status"]) == ("shown", "info", 302)
    assert event["timestamp"].endswith("Z")


def test_terminal_gets_readable_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    configure_logging("debug")
    structlog.get_logger("test").debug("http_response", status=302)
    line = capsys.readouterr().err
    assert "http_response" in line
    assert not line.lstrip().startswith("{")


@pytest.mark.parametrize(("level", "httpx_level"), [("debug", "WARNING"), ("error", "ERROR")])
def test_library_request_logging_stays_quiet(level: str, httpx_level: str) -> None:
    configure_logging(level)
    assert logging.getLevelName(logging.getLogger("httpx").level) == httpx_level
    assert logging.getLevelName(logging.getLogger("keyring").level) == httpx_level
    assert logging.getLevelName(logging.getLogger("mcp").level) == httpx_level
    assert logging.getLogger().level == logging.getLevelNamesMapping()[level.upper()]
