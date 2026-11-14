#!/usr/bin/env python3
"""ServiceNow access probe (throwaway spike, not product code).

Answers one question before any ServiceNow adapter is built: once you have
signed in through the browser (SAML, smart card), will the instance's REST
API answer a plain HTTP client that presents the same session, and which of
the request, catalog and incident endpoints can your account read?

It only reads: GET requests, plus two GraphQL *queries* (GraphQL is spoken
over POST, but neither is a mutation). It orders nothing, changes nothing,
and never follows a redirect, so the session cookie is only ever sent to the
one host you name.

Stage 1, no sign-in needed. Shows where the instance sends an anonymous
visitor and what the API says to an anonymous caller:

    uv run --with truststore python scripts/servicenow_probe.py <instance-host>

Stage 2, with your browser session. Sign in as usual, open DevTools on the
instance, Network tab, click any request to the instance, and copy the value
of its `Cookie` request header (`document.cookie` is not enough: the session
cookie is HttpOnly). Then, with it on the clipboard (macOS):

    SNOW_COOKIE="$(pbpaste)" \
      uv run --with truststore python scripts/servicenow_probe.py <instance-host>

That keeps the cookie out of your shell history and off the command line.
The probe looks for the CSRF token (`g_ck`) itself; if it cannot find one,
type `g_ck` in the browser console and pass the value as SNOW_USER_TOKEN.

Useful overrides:

    --portal-path /esc     page to read the CSRF token from (default: /sp)
    --search software      catalog search term (default: software)
    --ca-bundle file.pem   trust this PEM instead of the system store

What it prints is the host, status codes, counts, header and cookie *names*,
field names, GraphQL root field names, and the titles of catalogs and catalog
items. No cookie or token
values, and no ticket contents. Read it before pasting it anywhere, and redact
the catalog titles if you would rather not share them.

The session cookie is a live sign-in to your account. Do not save it to a
file or paste it into a chat; it stops working when the session times out or
you log out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from email.message import Message

TIMEOUT_SECONDS = 20
LOCAL_HOSTS = {"localhost", "127.0.0.1"}
HOST_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d+)?$")
TOKEN_PATTERN = re.compile(r"g_ck\s*[=:]\s*['\"]([0-9a-f]{32,})['\"]")
# The "Me" dynamic filter is not available to every role, so ask the server
# for the caller's own id instead.
MY_ID = "javascript:gs.getUserID()"
GRAPHQL_PATH = "/api/now/graphql"
GRAPHQL_ROOTS = "{ __schema { queryType { fields { name } } mutationType { fields { name } } } }"
GRAPHQL_ROWS = (
    "{ GlideRecord_Query { sc_req_item(pagination: {limit: 1}) "
    "{ _rowCount _results { number { value } } } } }"
)

type Json = dict[str, "Json"] | list["Json"] | str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class Reply:
    """What one GET told us. `error` is set when no HTTP reply arrived."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    cookie_names: tuple[str, ...] = ()
    body: bytes = b""
    error: str | None = None

    @property
    def content_type(self) -> str:
        """The media type without its parameters."""
        return self.headers.get("content-type", "").split(";")[0]

    def json(self) -> Json:
        """Parse the body as JSON, or None when it is something else."""
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    def records(self) -> list[dict[str, Json]] | None:
        """Return the `result` list of a Table or Catalog API reply."""
        data = self.json()
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, list):
            return None
        return [row for row in result if isinstance(row, dict)]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report redirects instead of following them (cookies stay on one host)."""

    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        """Decline every redirect, so the 3xx reply itself is returned."""


class Probe:
    """GET paths on one instance, optionally with a browser session."""

    def __init__(self, base: str, context: ssl.SSLContext, cookie: str | None) -> None:
        """Remember the instance and session; nothing is sent yet."""
        self.base = base
        self.cookie = cookie
        self.user_token: str | None = None
        self._opener = urllib.request.build_opener(
            _NoRedirect, urllib.request.HTTPSHandler(context=context)
        )

    def get(self, path: str, *, accept: str = "application/json", auth: bool = True) -> Reply:
        """GET one path. Redirects come back as the 3xx reply itself."""
        return self._send(path, accept=accept, auth=auth, data=None)

    def graphql(self, query: str, *, auth: bool = True) -> Reply:
        """Send one GraphQL query (a read, though GraphQL travels as a POST)."""
        if not query.lstrip().startswith("{"):
            raise ValueError("Only anonymous GraphQL queries are sent, never a mutation.")
        data = json.dumps({"query": query, "variables": {}}).encode()
        return self._send(GRAPHQL_PATH, accept="application/json", auth=auth, data=data)

    def _send(self, path: str, *, accept: str, auth: bool, data: bytes | None) -> Reply:
        headers = {"Accept": accept, "User-Agent": "servicenow-probe"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if auth and self.cookie:
            headers["Cookie"] = self.cookie
            if self.user_token:
                headers["X-UserToken"] = self.user_token
        request = urllib.request.Request(f"{self.base}{path}", data=data, headers=headers)
        try:
            with self._opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                return _reply(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return _reply(exc.code, exc.headers, exc.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return Reply(status=0, error=f"{type(exc).__name__}: {exc}")


def _reply(status: int, headers: Message, body: bytes) -> Reply:
    names = tuple(value.split("=", 1)[0].strip() for value in headers.get_all("Set-Cookie") or [])
    return Reply(
        status=status,
        headers={
            key.lower(): value for key, value in headers.items() if key.lower() != "set-cookie"
        },
        cookie_names=names,
        body=body,
    )


def where(location: str | None) -> str:
    """Reduce a redirect target to host and path: its query can hold a SAML request."""
    if not location:
        return "(none)"
    parts = urllib.parse.urlsplit(location)
    return f"{parts.netloc or '(same host)'}{parts.path}"


def describe(reply: Reply) -> str:
    """One line for a reply: status, type, and whichever detail explains it."""
    if reply.error:
        return f"no reply  {reply.error}"
    notes = [str(reply.status), reply.content_type or "(no content type)"]
    if 300 <= reply.status < 400:
        notes.append(f"-> {where(reply.headers.get('location'))}")
    if challenge := reply.headers.get("www-authenticate"):
        notes.append(f"challenge={challenge!r}")
    if logged_in := reply.headers.get("x-is-logged-in"):
        notes.append(f"x-is-logged-in={logged_in}")
    if total := reply.headers.get("x-total-count"):
        notes.append(f"total={total}")
    if (records := reply.records()) is not None:
        notes.append(f"returned={len(records)}")
    elif reply.status >= 400 and isinstance(data := reply.json(), dict):
        error = data.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        notes.append(f"error={str(message)[:160]!r}")
    return "  ".join(notes)


def table_path(table: str, query: str, fields: str, limit: int = 1) -> str:
    """Build a Table API path that also asks for the total row count."""
    params = urllib.parse.urlencode(
        {
            "sysparm_query": query,
            "sysparm_fields": fields,
            "sysparm_limit": limit,
            "sysparm_exclude_reference_link": "true",
        }
    )
    return f"/api/now/table/{table}?{params}"


def trust(ca_bundle: str | None) -> ssl.SSLContext:
    """Verify TLS against a PEM file, else the system store, else Python's default."""
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    try:
        import truststore
    except ImportError:
        print("note: truststore not installed; an internal CA may fail verification.")
        print("      Run with `uv run --with truststore ...` or pass --ca-bundle.\n")
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def base_url(host: str) -> str:
    """Turn the host argument into a base URL, refusing anything but a bare host."""
    host = host.strip().lower().removeprefix("https://").rstrip("/")
    if host.startswith("http://") and host[7:].split(":")[0] in LOCAL_HOSTS:
        return host  # plain HTTP is for testing the probe itself against a local fake
    if not HOST_PATTERN.fullmatch(host):
        raise SystemExit(f"Expected a bare host name such as example.service-now.com, got {host!r}")
    return f"https://{host}"


def clean_cookie(raw: str | None) -> str | None:
    """Accept a pasted `Cookie: a=b; c=d` line or just its value."""
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if value.lower().startswith("cookie:"):
        value = value[7:].strip()
    if "\n" in value or "=" not in value:
        raise SystemExit("SNOW_COOKIE is not one Cookie header value (name=value; ...).")
    return value


def stage_one(probe: Probe) -> None:
    """What an anonymous visitor and an anonymous API caller are told."""
    print("--- stage 1: anonymous ---")
    print(f"GET /                      {describe(probe.get('/', accept='text/html', auth=False))}")
    api = probe.get(table_path("sys_user", "", "sys_id"), auth=False)
    print(f"GET table API (no session) {describe(api)}")
    print(f"GraphQL (no session)       {describe(probe.graphql('{ __typename }', auth=False))}")
    print(f"  server={api.headers.get('server', '(not sent)')}")
    print(f"  cookies set: {', '.join(api.cookie_names) or '(none)'}")


def find_user_token(probe: Probe, portal_path: str) -> str:
    """Read the CSRF token from a signed-in page; report where it was found."""
    for path in dict.fromkeys((portal_path, "/sp", "/navpage.do")):
        page = probe.get(path, accept="text/html")
        if match := TOKEN_PATTERN.search(page.body.decode("utf-8", "replace")):
            probe.user_token = match.group(1)
            return f"found in {path}"
        print(f"  token page {path}: {describe(page)}")
    return "NOT FOUND (pass SNOW_USER_TOKEN from the browser console's g_ck)"


def stage_two(probe: Probe, portal_path: str, search: str) -> None:
    """What the API tells a plain client holding the browser's session."""
    assert probe.cookie is not None
    names = sorted(part.split("=", 1)[0].strip() for part in probe.cookie.split(";"))
    print("\n--- stage 2: with the browser session ---")
    print(f"cookie names sent: {', '.join(names)}")

    me_path = table_path("sys_user", f"sys_id={MY_ID}", "sys_id")
    print(f"GET my user, cookie only   {describe(probe.get(me_path))}")

    if env_token := os.environ.get("SNOW_USER_TOKEN", "").strip():
        probe.user_token, source = env_token, "from SNOW_USER_TOKEN"
    else:
        source = find_user_token(probe, portal_path)
    print(f"csrf token (g_ck):         {source}")
    me = probe.get(me_path)
    print(f"GET my user, with token    {describe(me)}")
    if not me.records():
        print("  The session was not accepted, so the remaining calls are skipped.")
        return

    checks = {
        "sc_request (mine)": ("sc_request", f"requested_for={MY_ID}"),
        "sc_req_item (mine)": ("sc_req_item", f"requested_for={MY_ID}^ORopened_by={MY_ID}"),
        "sc_req_item (visible)": ("sc_req_item", ""),
        "sc_task (visible)": ("sc_task", ""),
        "incident (mine)": ("incident", f"caller_id={MY_ID}^ORopened_by={MY_ID}"),
        "sys_journal_field": ("sys_journal_field", "element=comments"),
    }
    item_fields: list[str] = []
    for label, (table, query) in checks.items():
        # No sysparm_fields on the item check, so its field *names* can be listed.
        fields = "" if label == "sc_req_item (mine)" else "sys_id"
        reply = probe.get(table_path(table, query, fields))
        print(f"GET {label:<22} {describe(reply)}")
        if not fields and (records := reply.records()):
            item_fields = sorted(records[0])
    print(f"sc_req_item field names ({len(item_fields)}): {', '.join(item_fields) or '(no row)'}")

    catalogs = probe.get("/api/sn_sc/servicecatalog/catalogs")
    print(f"GET catalog API: catalogs  {describe(catalogs)}")
    for catalog in (catalogs.records() or [])[:20]:
        print(f"  catalog: {catalog.get('title')!r}")

    params = urllib.parse.urlencode({"sysparm_text": search, "sysparm_limit": 10})
    items = probe.get(f"/api/sn_sc/servicecatalog/items?{params}")
    print(f"GET catalog items ~{search!r}  {describe(items)}")
    for item in items.records() or []:
        print(f"  item: {item.get('name')!r}  type={item.get('type')}")
    if first := next(iter(items.records() or []), None):
        detail = probe.get(f"/api/sn_sc/servicecatalog/items/{first.get('sys_id')}")
        body = detail.json()
        result = body.get("result") if isinstance(body, dict) else None
        found = result.get("variables") if isinstance(result, dict) else None
        variables = [v for v in found if isinstance(v, dict)] if isinstance(found, list) else []
        kinds = sorted({str(v.get("friendly_type") or v.get("type")) for v in variables})
        print(f"GET first item's form      {describe(detail)}")
        print(f"  variables={len(variables)} kinds={kinds}")

    graphql_checks(probe)


def dig(data: Json, *keys: str) -> Json:
    """Walk nested JSON objects by key, giving None as soon as one is missing."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data


def graphql_checks(probe: Probe) -> None:
    """Whether GraphQL answers, what its root fields are, and one table read."""
    roots = probe.graphql(GRAPHQL_ROOTS)
    print(f"GraphQL introspection      {describe(roots)}{graphql_errors(roots)}")
    for kind in ("queryType", "mutationType"):
        listed = dig(roots.json(), "data", "__schema", kind, "fields")
        names = sorted(
            str(entry.get("name"))
            for entry in (listed if isinstance(listed, list) else [])
            if isinstance(entry, dict)
        )
        print(f"  {kind} fields ({len(names)}): {', '.join(names[:60]) or '(none listed)'}")

    rows = probe.graphql(GRAPHQL_ROWS)
    count = dig(rows.json(), "data", "GlideRecord_Query", "sc_req_item", "_rowCount")
    print(f"GraphQL sc_req_item rows   {describe(rows)}  _rowCount={count}{graphql_errors(rows)}")


def graphql_errors(reply: Reply) -> str:
    """The first GraphQL error message, which arrives inside a 200 reply."""
    errors = dig(reply.json(), "errors")
    if not isinstance(errors, list) or not errors:
        return ""
    first = errors[0]
    message = first.get("message") if isinstance(first, dict) else first
    return f"  graphql_error={str(message)[:160]!r} (+{len(errors) - 1} more)"


def main() -> int:
    """Run stage 1, then stage 2 when SNOW_COOKIE is set, and print a SUMMARY."""
    parser = argparse.ArgumentParser(description="ServiceNow access probe (reads only).")
    parser.add_argument("host", help="the instance host, such as example.service-now.com")
    parser.add_argument("--portal-path", default="/sp")
    parser.add_argument("--search", default="software")
    parser.add_argument("--ca-bundle", default=None)
    args = parser.parse_args()

    base = base_url(args.host)
    cookie = clean_cookie(os.environ.get("SNOW_COOKIE"))
    probe = Probe(base, trust(args.ca_bundle), cookie)

    print("--- paste everything below back (read it first) ---")
    print("=== SERVICENOW PROBE SUMMARY ===")
    print(f"instance: {base}")
    stage_one(probe)
    if cookie:
        stage_two(probe, args.portal_path, args.search)
    else:
        print("\nstage 2 skipped: SNOW_COOKIE is not set (see the docstring).")
    print("=== END SUMMARY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
