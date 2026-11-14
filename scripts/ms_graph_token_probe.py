#!/usr/bin/env python3
"""Microsoft 365 *bearer-token replay* probe (throwaway spike, not product code).

A stopgap while the public "Microsoft Graph Command Line Tools" client waits on
admin consent (see docs/ROADMAP.md). The question this answers: if you sign in
through the browser the way you always do and lift one of the access tokens the
session already holds, can it read your calendar and mail read-only?

The token decides which API. A gov-cloud token audience'd for Graph
(``dod-graph.microsoft.us``) goes to Graph; one audience'd for Outlook
(``outlook-dod.office365.us``) goes to the Outlook REST API v2.0, which shares
Graph's OData shape for ``/me/events``, ``/me/messages`` and ``/me/calendars``.
In the Teams/Office first-party client the mail and calendar scopes ride the
Outlook resource, so the Outlook-audience token is the one that reads them; the
Graph token authenticates but 403s on mail/calendar for lack of those scopes.

It hinges on one claim. A bearer token is minted for a specific *audience*
(the ``aud`` claim). Graph accepts a replayed token only when ``aud`` is the
Graph resource for your cloud (DoD: ``https://dod-graph.microsoft.us``;
GCC High: ``https://graph.microsoft.us``). A token the Outlook web app uses is
usually audience'd for Exchange (``outlook.office365.us``) instead, and Graph
answers that with ``401 InvalidAudience``. Exchange tokens are also often
encrypted (an opaque JWE) and cannot be introspected at all.

So the probe decodes the token *locally first* (no network) and prints
``aud``/``iss``/``scp``/``exp``. Only if ``aud`` looks like a Graph resource
does it spend the token on read-only calls. It changes nothing and reads no
message bodies or event details — only status codes, counts, and your own
account name.

Run it on the laptop that holds the browser session and the gov-cloud route
(this is where the token and the network both live). The token is a live
credential for your own account: keep it in a file, not your shell history,
and do not paste it anywhere but here.

    # 1. In the browser dev tools (F12) -> Network, find a request whose host is
    #    dod-graph.microsoft.us (or graph.microsoft.us). Copy the value of its
    #    Authorization header *after* the word "Bearer", into a file:
    mkdir -p .secrets && chmod 700 .secrets
    # paste the token, no "Bearer " prefix, no quotes:
    cat > .secrets/graph.jwt      # (.secrets/ is gitignored)

    # 2. Decode only, to see what the token is good for (spends nothing):
    uv run python scripts/ms_graph_token_probe.py --token-file .secrets/graph.jwt --decode-only

    # 3. If aud is a Graph resource, make the read-only calls:
    uv run python scripts/ms_graph_token_probe.py --token-file .secrets/graph.jwt

The token may instead be handed in via the GRAPH_TOKEN environment variable.
No third-party packages are needed; it is urllib only, and honours HTTPS_PROXY
and SSL_CERT_FILE / --ca-bundle if your environment sets them.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# aud host -> API base. The token tells us which resource and cloud it is for; we
# do not guess. Two resource families reach mail/calendar in a gov tenant:
#   * Microsoft Graph        — directory/people/Teams, and mail/calendar *if* the
#                              token carries Mail.*/Calendars.* on the Graph resource.
#   * Outlook REST API v2.0  — mail/calendar on the Outlook resource; same OData
#                              shape (/me/events, /me/messages, /me/calendars).
# The first-party Teams/Office client requests mail/calendar on the Outlook
# resource, so an Outlook-audience token is the one that actually reads them.
# Override with --graph-base when aud is the bare Graph app GUID, not a URL.
API_BASES = {
    "dod-graph.microsoft.us": "https://dod-graph.microsoft.us/v1.0",
    "graph.microsoft.us": "https://graph.microsoft.us/v1.0",
    "graph.microsoft.com": "https://graph.microsoft.com/v1.0",
    "outlook-dod.office365.us": "https://outlook-dod.office365.us/api/v2.0",
    "outlook.office365.us": "https://outlook.office365.us/api/v2.0",
    "outlook.office365.com": "https://outlook.office365.com/api/v2.0",
}
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"

# Read-only, calendar first (the roadmap's priority order), then mail. No $select:
# the property names differ between Graph (camelCase) and Outlook REST (PascalCase),
# and we print only the item *count* from each response, never any field, so a body
# that crosses the wire under $top=1 is never shown.
PROBES = (
    ("GET /me", "/me"),
    ("GET /me/calendars", "/me/calendars"),
    ("GET /me/events?$top=1", "/me/events?$top=1"),
    ("GET /me/messages?$top=1", "/me/messages?$top=1"),
)


def _b64url(segment: str) -> bytes:
    """Decode one base64url JWT segment, padding as needed."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_claims(token: str) -> tuple[dict | None, str]:
    """Decode a JWT's payload without verifying it. Returns (claims, note).

    The signature is not checked — the Graph endpoint is the thing that verifies
    the token; here we only want to read the claims to decide whether it is even
    worth sending. A 5-segment token is an encrypted JWE whose payload is opaque.
    """
    parts = token.split(".")
    if len(parts) == 5:
        return None, "token is an encrypted JWE (5 segments) — claims are opaque"
    if len(parts) != 3:
        return None, f"not a JWS/JWT (got {len(parts)} segments, expected 3)"
    try:
        return json.loads(_b64url(parts[1])), "decoded"
    except (ValueError, json.JSONDecodeError) as exc:
        return None, f"could not decode payload: {exc}"


def base_from_claims(claims: dict) -> tuple[str | None, str]:
    """Pick the API base URL from the token's aud/iss. Returns (base, note)."""
    aud = str(claims.get("aud", ""))
    for host, base in API_BASES.items():
        if host in aud:
            kind = "Graph" if "graph" in host else "Outlook REST"
            return base, f"{kind}; aud resource host {host}"
    if aud in (GRAPH_APP_ID, f"api://{GRAPH_APP_ID}"):
        iss = str(claims.get("iss", ""))
        if ".microsoftonline.us" in iss:
            return None, "aud is the Graph app GUID on a gov tenant — pass --graph-base"
        if ".microsoftonline.com" in iss:
            return API_BASES["graph.microsoft.com"], "aud is the Graph app GUID (commercial)"
        return None, "aud is the Graph app GUID — pass --graph-base"
    return None, f"aud is not a known Graph or Outlook resource (aud={aud!r})"


def api_get(
    base: str, path: str, token: str, ctx: ssl.SSLContext
) -> tuple[int, dict | None, str | None]:
    """GET an API path read-only. Returns (status, json_or_None, error_text_or_None)."""
    req = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return resp.status, json.loads(resp.read() or b"null"), None
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return 0, None, str(exc)


def _claim_lines(claims: dict) -> list[str]:
    """The claims worth seeing before spending the token."""
    exp = claims.get("exp")
    ttl = f"{int(exp - time.time())}s from now" if isinstance(exp, (int, float)) else "unknown"
    upn = claims.get("upn") or claims.get("unique_name") or claims.get("preferred_username")
    return [
        f"aud:          {claims.get('aud')}",
        f"iss:          {claims.get('iss')}",
        f"tid:          {claims.get('tid')}",
        f"appid:        {claims.get('appid') or claims.get('azp')}",
        f"app name:     {claims.get('app_displayname')}",
        f"upn:          {upn}",
        f"scp (deleg.): {claims.get('scp')}",
        f"roles (app):  {claims.get('roles')}",
        f"expires:      {ttl}",
    ]


def _read_token(args: argparse.Namespace) -> str | None:
    """Pull the token from the file or the environment; keep it off argv."""
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as handle:
            return handle.read().strip()
    env = os.environ.get("GRAPH_TOKEN", "").strip()
    return env or None


def main() -> int:
    """Decode the token, and unless --decode-only and aud permits, probe Graph."""
    parser = argparse.ArgumentParser(description="Microsoft Graph bearer-token replay probe.")
    parser.add_argument("--token-file", help="file holding the bearer token (no 'Bearer ' prefix)")
    parser.add_argument(
        "--graph-base", help="override the Graph base URL, e.g. https://dod-graph.microsoft.us/v1.0"
    )
    parser.add_argument(
        "--decode-only", action="store_true", help="print the claims and stop; spend nothing"
    )
    parser.add_argument("--ca-bundle", help="CA bundle for TLS (else SSL_CERT_FILE / system trust)")
    args = parser.parse_args()

    token = _read_token(args)
    if not token:
        print("No token. Use --token-file PATH or set GRAPH_TOKEN.", file=sys.stderr)
        return 2
    if token.lower().startswith("bearer "):
        token = token[7:].strip()  # tolerate a pasted "Bearer " prefix

    claims, note = decode_claims(token)
    print("=== Microsoft 365 token probe (Graph / Outlook REST) ===")
    print(f"decode: {note}\n")
    if claims:
        for line in _claim_lines(claims):
            print(f"  {line}")
        print()

    base = args.graph_base
    base_note = "from --graph-base" if base else ""
    if not base and claims:
        base, base_note = base_from_claims(claims)

    if args.decode_only or base is None:
        if base is None and not args.decode_only:
            print(f"Not probing: {base_note or note}.")
            print("If you believe the token is for Graph, re-run with --graph-base <url>.")
        elif base:
            print(f"API base would be: {base}  ({base_note})")
        else:
            print(f"API base: undetermined ({base_note or note}).")
        _summary(claims, base, note, base_note, results=None)
        return 0 if base else 1

    cafile = args.ca_bundle or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    print(f"API base: {base}  ({base_note})\n")

    results = []
    for label, path in PROBES:
        status, body, err = api_get(base, path, token, ctx)
        count = len(body.get("value", [])) if isinstance(body, dict) and "value" in body else None
        results.append((label, status, count, err))

    _summary(claims, base, note, base_note, results=results)
    return 0


def _summary(
    claims: dict | None,
    base: str | None,
    decode_note: str,
    base_note: str,
    *,
    results: list | None,
) -> None:
    """Print the paste-back SUMMARY block."""
    print("\n--- paste everything below back ---")
    print("=== SUMMARY ===")
    print(f"decode:     {decode_note}")
    if claims:
        print(f"aud:        {claims.get('aud')}")
        print(f"iss:        {claims.get('iss')}")
        print(f"scp:        {claims.get('scp')}")
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            print(f"ttl_secs:   {int(exp - time.time())}")
    print(f"api_base:   {base or 'undetermined'}  ({base_note or 'n/a'})")
    if results is None:
        print("probes:     not run")
    else:
        for label, status, count, err in results:
            tail = f"  count={count}" if count is not None else ""
            print(f"{label:<26} {status}{tail}")
            if err:
                print(f"  error: {err[:300]}")
    print("=== END SUMMARY ===")


if __name__ == "__main__":
    sys.exit(main())
