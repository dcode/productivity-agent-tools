#!/usr/bin/env python3
"""ServiceNow browser sign-in probe (throwaway spike, not product code).

Answers one question before a `login servicenow` verb is built: can the tool
open the installed browser, let you sign in as usual (SAML, smart card), and
then collect the session cookies and CSRF token itself, with no DevTools
copy and paste?

What it does:

1. Starts Chrome with a new, empty, temporary profile, opened on the
   instance. Your everyday profile is not touched.
2. Waits while you sign in in that window.
3. Asks the browser for the instance's cookies and the address of the open
   tab, over the DevTools protocol. It never attaches to a page: a managed
   browser can refuse that ("Not allowed"), and it is not needed.
4. From plain Python, fetches that same page with the cookies to read the
   CSRF token (`g_ck`) from its HTML, then reads your own user record, to
   prove the collected session works outside the browser.
5. Closes the browser and deletes the temporary profile.

Run it (macOS or Linux):

    uv run --with truststore python scripts/servicenow_login_probe.py <instance-host>

It talks to the browser over a private pipe (`--remote-debugging-pipe`), not
a TCP port, so no other program on the machine can attach to the session
while the window is open. Nothing is stored: the cookies live in memory for
the one API call and are gone when the script ends. Closing the window does
not log you out on the server; the session ends on its own idle timeout.

Useful overrides:

    --browser <path>       the browser binary (default: Chrome, then Edge)
    --timeout 300          seconds to wait for you to finish signing in
    --ca-bundle file.pem   trust this PEM instead of the system store
    --chrome-arg=<flag>    extra browser flag, repeatable

What it prints is cookie *names* and attributes (HttpOnly, session or
expiry), whether the token was found, and status codes. No cookie or token
values.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

from servicenow_probe import MY_ID, TOKEN_PATTERN, Probe, base_url, describe, table_path, trust

if TYPE_CHECKING:
    from collections.abc import Iterator

type Json = dict[str, "Json"] | list["Json"] | str | int | float | bool | None

BROWSERS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
    "chromium",
)
POLL_SECONDS = 2.0
REPLY_SECONDS = 10.0


class Browser:
    """A browser started with a throwaway profile, spoken to over a pipe."""

    def __init__(self, binary: str, url: str, extra: list[str]) -> None:
        """Start the browser on `url`; it reads fd 3 and writes fd 4."""
        self.profile = tempfile.mkdtemp(prefix="servicenow-login-")
        self._next_id = 0
        self._buffer = b""
        to_browser, self._write = os.pipe()
        self._read, from_browser = os.pipe()
        # Copies numbered 10 or higher, so that renumbering them to 3 and 4 in
        # the child cannot overwrite one with the other.
        child_in = fcntl.fcntl(to_browser, fcntl.F_DUPFD, 10)
        child_out = fcntl.fcntl(from_browser, fcntl.F_DUPFD, 10)

        def renumber() -> None:
            os.dup2(child_in, 3)
            os.dup2(child_out, 4)

        command = [
            binary,
            f"--user-data-dir={self.profile}",
            "--remote-debugging-pipe",
            "--no-first-run",
            "--no-default-browser-check",
            *extra,
            url,
        ]
        try:
            self.process = subprocess.Popen(
                command,
                pass_fds=(3, 4),
                preexec_fn=renumber,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            for descriptor in (to_browser, from_browser, child_in, child_out):
                os.close(descriptor)

    def call(self, method: str, params: dict[str, Json] | None = None, session: str = "") -> Json:
        """Send one DevTools command and return its result, skipping events."""
        self._next_id += 1
        message: dict[str, Json] = {"id": self._next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        os.write(self._write, json.dumps(message).encode() + b"\0")
        deadline = time.monotonic() + REPLY_SECONDS
        for reply in self._messages(deadline):
            if isinstance(reply, dict) and reply.get("id") == self._next_id:
                if error := reply.get("error"):
                    raise RuntimeError(f"{method}: {error}")
                return reply.get("result")
        raise TimeoutError(f"The browser did not answer {method}.")

    def _messages(self, deadline: float) -> Iterator[Json]:
        while True:
            while b"\0" in self._buffer:
                raw, self._buffer = self._buffer.split(b"\0", 1)
                yield json.loads(raw)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self._read], [], [], remaining)[0]:
                return
            chunk = os.read(self._read, 65536)
            if not chunk:
                raise ConnectionError("The browser closed (was the window closed?).")
            self._buffer += chunk

    def close(self) -> None:
        """Ask the browser to quit, make sure it has, and delete the profile."""
        try:
            self.call("Browser.close")
        except RuntimeError, TimeoutError, OSError:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        for descriptor in (self._read, self._write):
            os.close(descriptor)
        shutil.rmtree(self.profile, ignore_errors=True)


def find_browser(chosen: str | None) -> str:
    """Return the browser to start: the one named, else the first one installed."""
    for candidate in (chosen,) if chosen else BROWSERS:
        if Path(candidate).is_file():
            return candidate
        if found := shutil.which(candidate):
            return found
    raise SystemExit("No Chrome or Edge found; pass --browser <path>.")


def rows(value: Json, key: str) -> list[dict[str, Json]]:
    """Return the list of objects under `key`, or nothing if the shape is off."""
    listed = value.get(key) if isinstance(value, dict) else None
    return [row for row in listed if isinstance(row, dict)] if isinstance(listed, list) else []


def instance_pages(browser: Browser, host: str) -> list[str]:
    """Return the path (with query) of each open tab that is on the instance."""
    paths = []
    for target in rows(browser.call("Target.getTargets"), "targetInfos"):
        url = urllib.parse.urlsplit(str(target.get("url")))
        if target.get("type") == "page" and url.netloc == host:
            paths.append(f"{url.path or '/'}{'?' + url.query if url.query else ''}")
    return paths


def page_token(probe: Probe, paths: list[str]) -> tuple[str, str]:
    """Fetch the open pages with the session and read `g_ck` from their HTML."""
    notes = []
    for path in paths:
        page = probe.get(path, accept="text/html")
        if match := TOKEN_PATTERN.search(page.body.decode("utf-8", "replace")):
            return match.group(1), f"found in the HTML of {path.split('?')[0]}"
        notes.append(f"{path.split('?')[0]}: {describe(page)}")
    return "", "; ".join(notes) or "(no tab on the instance)"


def instance_cookies(browser: Browser, host: str) -> list[dict[str, Json]]:
    """Return the browser's cookies that it would send to the instance."""
    name = host.split(":")[0]
    return [
        cookie
        for cookie in rows(browser.call("Storage.getCookies"), "cookies")
        if name == (domain := str(cookie.get("domain")).lstrip(".")) or name.endswith(f".{domain}")
    ]


def wait_for_session(browser: Browser, probe: Probe, host: str, timeout: float) -> list[str]:
    """Poll until the collected cookies and token read the user's own record."""
    me_path = table_path("sys_user", f"sys_id={MY_ID}", "sys_id")
    deadline = time.monotonic() + timeout
    seen = "(no tab on the instance yet)"
    while time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        # Nothing is sent to the instance while the tab is away at the sign-in host.
        if not (paths := instance_pages(browser, host)):
            continue
        cookies = instance_cookies(browser, host)
        probe.cookie = "; ".join(f"{c.get('name')}={c.get('value')}" for c in cookies)
        probe.user_token = None
        # Without the token the API answers 401 either way, but says who is asking.
        knock = probe.get(me_path)
        if knock.headers.get("x-is-logged-in") != "true":
            seen = f"not signed in yet: {describe(knock)}"
            continue
        token, source = page_token(probe, paths)
        if not token:
            seen = f"signed in, but no token: {source}"
            continue
        probe.user_token = token
        reply = probe.get(me_path)
        if reply.records():
            return [
                "sign_in:             OK",
                f"token (g_ck):        {source}, {len(token)} characters",
                f"GET my user (python) {describe(reply)}",
                *cookie_lines(cookies),
            ]
        seen = f"token rejected: {describe(reply)}"
    return ["sign_in:             TIMED OUT", f"last state:          {seen}"]


def cookie_lines(cookies: list[dict[str, Json]]) -> list[str]:
    """Describe each cookie by name and attributes, never by value."""
    lines = [f"cookies collected:   {len(cookies)}"]
    for cookie in sorted(cookies, key=lambda c: str(c.get("name"))):
        expires = cookie.get("expires")
        if cookie.get("session") or not isinstance(expires, (int, float)) or expires <= 0:
            life = "session"
        else:
            life = f"expires in {(expires - time.time()) / 3600:.1f}h"
        flags = [flag for flag in ("httpOnly", "secure") if cookie.get(flag)]
        lines.append(f"  {cookie.get('name')}: {life}  {' '.join(flags)}".rstrip())
    return lines


def main() -> int:
    """Open the browser, wait for sign-in, check the session, print a SUMMARY."""
    parser = argparse.ArgumentParser(description="ServiceNow browser sign-in probe.")
    parser.add_argument("host", help="the instance host, such as example.service-now.com")
    parser.add_argument("--browser", default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--ca-bundle", default=None)
    parser.add_argument("--chrome-arg", action="append", default=[])
    args = parser.parse_args()

    base = base_url(args.host)
    host = urllib.parse.urlsplit(base).netloc
    binary = find_browser(args.browser)
    probe = Probe(base, trust(args.ca_bundle), cookie=None)

    print(f"Opening {Path(binary).name} on {base}. Sign in there; this waits {args.timeout:.0f}s.")
    browser = Browser(binary, f"{base}/", args.chrome_arg)
    try:
        version = browser.call("Browser.getVersion")
        product = version.get("product") if isinstance(version, dict) else None
        lines = wait_for_session(browser, probe, host, args.timeout)
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        product, lines = None, [f"sign_in:             FAILED  {type(exc).__name__}: {exc}"]
    finally:
        browser.close()

    print("\n--- paste everything below back (read it first) ---")
    print("=== SERVICENOW LOGIN PROBE SUMMARY ===")
    print(f"browser:             {product or Path(binary).name}")
    print("channel:             --remote-debugging-pipe, temporary profile")
    print("\n".join(lines))
    print(f"profile deleted:     {not Path(browser.profile).exists()}")
    print("=== END SUMMARY ===")
    return 0 if lines[0].endswith("OK") else 1


if __name__ == "__main__":
    sys.exit(main())
