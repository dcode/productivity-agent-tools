"""Structured logging setup shared by the server and the setup commands.

Everything is written to stderr: the MCP server speaks its protocol on
stdout, so a stray line there would corrupt the conversation with the agent.
Events are rendered for people when stderr is a terminal and as one JSON
object per line otherwise, which is what an agent host's log file wants.
"""

import logging
import sys

import structlog

LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error")
DEFAULT_LOG_LEVEL: str = "info"

# httpx and httpcore log every request at INFO, including full URLs with query
# strings; the HTTP core logs its own, redacted, events instead. keyring lists
# every backend it tries at DEBUG, which says nothing about this tool.
_NOISY_LIBRARIES: tuple[str, ...] = ("httpx", "httpcore", "keyring")


def configure_logging(level: str = DEFAULT_LOG_LEVEL) -> None:
    """Send structured logs at or above a level to stderr.

    Args:
        level: One of ``LOG_LEVELS``.
    """
    number = logging.getLevelNamesMapping()[level.upper()]
    renderer: structlog.typing.Processor = (
        structlog.dev.ConsoleRenderer()
        if sys.stderr.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(number),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    logging.basicConfig(stream=sys.stderr, level=number, format="%(name)s: %(message)s", force=True)
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(max(number, logging.WARNING))
