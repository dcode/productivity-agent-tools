"""Starts the installed browser for a sign-in and asks it for the resulting cookies.

The browser is spoken to over the DevTools protocol on a private pipe
(``--remote-debugging-pipe``), not a TCP port, so no other program on the
machine can attach while the user is signed in. Only browser-level commands
are used: a managed browser can refuse to let a tool attach to a page, and
attaching is not needed to list the open tabs or read the cookie store.

The browser runs on a new, empty profile in a temporary directory, which is
deleted when the browser closes. The user's everyday profile is not touched.

This module is POSIX-only (it passes file descriptors to the child), which
covers macOS and Linux.
"""

import fcntl
import json
import os
import select
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

from pydantic import JsonValue

from productivity_agent_tools.errors import AgentToolsError, ConfigError
from productivity_agent_tools.payload import objects, text, value

BROWSERS: tuple[str, ...] = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "chromium",
)
REPLY_SECONDS: float = 10.0
EXIT_SECONDS: float = 10.0


class BrowserError(AgentToolsError):
    """The browser could not be started, stopped answering, or was closed."""


@dataclass(frozen=True, slots=True)
class BrowserCookie:
    """One cookie from the browser's store.

    Attributes:
        name: The cookie's name.
        value: The cookie's value. Excluded from ``repr``.
        domain: The domain it is sent to, without a leading dot.
    """

    name: str
    value: str = field(repr=False)
    domain: str

    def sent_to(self, host: str) -> bool:
        """Tell whether the browser would send this cookie to a host.

        Args:
            host: Host name, with or without a port.

        Returns:
            True if the host is the cookie's domain or a sub-domain of it.
        """
        name = host.split(":")[0].lower()
        return name == self.domain or name.endswith(f".{self.domain}")


def find_browser(chosen: str | None = None, candidates: Sequence[str] = BROWSERS) -> str:
    """Find the browser to start.

    Args:
        chosen: A path or command name from the config, or None to look for one.
        candidates: Paths and command names to try, in order, when none is chosen.

    Returns:
        Path of the browser binary.

    Raises:
        ConfigError: If the chosen browser, or any browser, cannot be found.
    """
    for candidate in (chosen,) if chosen else candidates:
        expanded = Path(candidate).expanduser()
        if expanded.is_file():
            return str(expanded)
        if found := shutil.which(candidate):
            return found
    if chosen:
        raise ConfigError(f"The configured browser '{chosen}' was not found.")
    raise ConfigError(
        "No Chrome or Edge was found. Set browser under [servicenow] in the config to the "
        "browser's path."
    )


class Browser:
    """A browser running on a throwaway profile, controlled over a pipe.

    Use it as a context manager: leaving the block closes the browser and
    deletes the profile. The methods block for at most ``REPLY_SECONDS``, so
    call them from a worker thread in async code.

    Attributes:
        profile: Directory of the temporary profile.
    """

    def __init__(self, binary: str, url: str, extra_args: Sequence[str] = ()) -> None:
        """Start the browser with one tab open on ``url``.

        Args:
            binary: Path of the browser binary.
            url: Address the first tab opens.
            extra_args: Extra command-line flags for the browser.

        Raises:
            BrowserError: If the browser cannot be started.
        """
        self.profile = tempfile.mkdtemp(prefix="servicenow-login-")
        self._next_id = 0
        self._buffer = b""
        # The browser reads commands on descriptor 3 and writes replies on 4.
        to_browser, self._write = os.pipe()
        self._read, from_browser = os.pipe()
        # Copies numbered 10 or higher, so that renumbering them to 3 and 4 in
        # the child cannot overwrite one with the other. Like every descriptor
        # Python opens they close on exec, so the browser inherits only 3 and 4.
        child_in = fcntl.fcntl(to_browser, fcntl.F_DUPFD_CLOEXEC, 10)
        child_out = fcntl.fcntl(from_browser, fcntl.F_DUPFD_CLOEXEC, 10)

        def renumber() -> None:  # pragma: no cover - runs in the child process
            os.dup2(child_in, 3)
            os.dup2(child_out, 4)

        command = [
            binary,
            f"--user-data-dir={self.profile}",
            "--remote-debugging-pipe",
            "--no-first-run",
            "--no-default-browser-check",
            *extra_args,
            url,
        ]
        try:
            self._process = subprocess.Popen(
                command,
                # Not pass_fds=(3, 4): that needs 3 and 4 to be open in the child before
                # renumber runs, and whatever holds those numbers here may not survive
                # the fork (an event loop's kqueue does not).
                close_fds=False,
                preexec_fn=renumber,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            for descriptor in (self._read, self._write):
                os.close(descriptor)
            shutil.rmtree(self.profile, ignore_errors=True)
            raise BrowserError(f"Could not start the browser {binary}: {exc.strerror}.") from exc
        finally:
            for descriptor in (to_browser, from_browser, child_in, child_out):
                os.close(descriptor)

    def __enter__(self) -> Self:
        """Enter the context.

        Returns:
            This browser.
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the browser on leaving the context.

        Args:
            exc_type: Exception type, if one was raised.
            exc: Exception instance, if one was raised.
            traceback: Traceback, if an exception was raised.
        """
        self.close()

    def call(self, method: str, params: dict[str, JsonValue] | None = None) -> JsonValue:
        """Send one DevTools command and return its result.

        Args:
            method: Command name, such as ``"Target.getTargets"``.
            params: The command's parameters.

        Returns:
            The command's result.

        Raises:
            BrowserError: If the browser refuses the command, closes, or does not answer.
        """
        self._next_id += 1
        message = {"id": self._next_id, "method": method, "params": params or {}}
        try:
            os.write(self._write, json.dumps(message).encode() + b"\0")
        except OSError as exc:
            raise BrowserError("The browser was closed before the sign-in finished.") from exc
        for reply in self._messages(time.monotonic() + REPLY_SECONDS):
            if isinstance(reply, dict) and reply.get("id") == self._next_id:
                if "error" in reply:
                    refusal = text(reply, "error", "message") or "no reason given"
                    raise BrowserError(f"The browser refused {method}: {refusal}.")
                return reply.get("result")
        raise BrowserError(f"The browser did not answer {method}.")

    def _messages(self, deadline: float) -> Iterator[JsonValue]:
        """Yield the browser's messages until the deadline passes.

        Args:
            deadline: Time, on the monotonic clock, to stop waiting.

        Yields:
            Each decoded message, replies and events alike.

        Raises:
            BrowserError: If the browser closes its end of the pipe.
        """
        while True:
            while b"\0" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\0", 1)
                yield json.loads(raw)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._read], [], [], remaining)[0]:
                return
            chunk = os.read(self._read, 65536)
            if not chunk:
                raise BrowserError("The browser was closed before the sign-in finished.")
            self._buffer += chunk

    def version(self) -> str:
        """Return the browser's product and version, such as ``"Chrome/156.0"``."""
        return text(self.call("Browser.getVersion"), "product") or "unknown browser"

    def page_paths(self, host: str) -> list[str]:
        """List the open tabs that are on a host.

        Args:
            host: Host name (with port, if any) to match.

        Returns:
            The path and query of each such tab.
        """
        paths: list[str] = []
        for target in objects(value(self.call("Target.getTargets"), "targetInfos")):
            url = urlsplit(text(target, "url") or "")
            if text(target, "type") == "page" and url.netloc == host:
                paths.append(f"{url.path or '/'}{'?' + url.query if url.query else ''}")
        return paths

    def cookies(self, host: str) -> list[BrowserCookie]:
        """Read the cookies the browser would send to a host, HttpOnly ones included.

        Args:
            host: Host name, with or without a port.

        Returns:
            The cookies.
        """
        found = [
            BrowserCookie(
                name=text(cookie, "name") or "",
                value=text(cookie, "value") or "",
                domain=(text(cookie, "domain") or "").lstrip(".").lower(),
            )
            for cookie in objects(value(self.call("Storage.getCookies"), "cookies"))
        ]
        return [cookie for cookie in found if cookie.name and cookie.sent_to(host)]

    def close(self) -> None:
        """Quit the browser, make sure it has gone, and delete its profile."""
        try:
            self.call("Browser.close")
        except BrowserError:
            self._process.terminate()
        try:
            self._process.wait(timeout=EXIT_SECONDS)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        for descriptor in (self._read, self._write):
            os.close(descriptor)
        shutil.rmtree(self.profile, ignore_errors=True)
