#!/usr/bin/env python3
"""Mint an access token from a refresh token (throwaway spike, not product code).

The replay stopgap (see docs/ROADMAP.md and ms_graph_token_probe.py) is only
useful if we are not re-pasting an access token every hour. The token response
the client session holds also carries a *refresh token* (~24 h here), and a
refresh token is not pinned to one resource: it can be redeemed for an access
token for any resource and scope the client is already consented for. So the
durable shape of the stopgap is — harvest a refresh token once, then mint
access tokens silently as they expire, and re-harvest only when the refresh
token itself ages out.

This does exactly that redemption and nothing else. It is a public-client
refresh grant (no client secret), a POST to the tenant token endpoint. It does
not write to the tenant, open a browser, or touch anything but the token
endpoint. It runs on the laptop that holds the refresh token and the gov-cloud
route; the refresh token never leaves that machine except to Microsoft's own
login host.

What it needs (none of these are secrets): the tenant, the client id, and the
scope string to request. Defaults match the DoD tenant and the Teams/Office
first-party client the tokens were observed from; override for anything else.
The refresh token itself comes from a file (keep it out of shell history).

    # Seed once from the captured token response (the refresh_token field):
    mkdir -p .secrets && chmod 700 .secrets
    cat > .secrets/refresh.txt        # paste refresh_token only; .secrets/ is gitignored

    # Mint an Outlook-resource token good for read-only mail + calendar:
    uv run python scripts/ms_token_refresh.py \
        --refresh-file .secrets/refresh.txt \
        --scope "https://outlook-dod.office365.us/Calendars.Read https://outlook-dod.office365.us/Mail.Read"

    # ...then probe it:
    uv run python scripts/ms_token_refresh.py --refresh-file .secrets/refresh.txt \
        --scope "https://outlook-dod.office365.us/Mail.Read" --probe

On success it writes the new access token to .secrets/access.jwt and the
*rotated* refresh token back over .secrets/refresh.txt (AAD rotates it on each
use), decodes the new access token's aud/scp/exp, and prints a SUMMARY. A
scope AAD will not grant (e.g. a Graph mail scope the client was never
consented for) comes back as an AADSTS error in the SUMMARY, spending nothing.
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
import urllib.parse
import urllib.request

# Non-secret identifiers observed from the captured tokens. The client id is a
# first-party public client (no secret); the tenant is the DoD tenant's GUID,
# which is public in every token's `iss`. Both are overridable.
DEFAULT_TENANT = "fae6d70f-954b-4811-92b6-0530d6f84c43"
DEFAULT_CLIENT_ID = "9199bf20-a13f-4107-85dc-02114787ef48"
DEFAULT_AUTHORITY = "login.microsoftonline.us"  # gov cloud; commercial is .com

PROBE_PATHS = ("/me", "/me/calendars", "/me/events?$top=1", "/me/messages?$top=1")


def _b64url(segment: str) -> bytes:
    """Decode one base64url JWT segment, padding as needed."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def decode_claims(token: str) -> dict | None:
    """Decode a JWT payload without verifying it; None if it is not a plain JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        return json.loads(_b64url(parts[1]))
    except ValueError:  # JSONDecodeError and base64 errors are both ValueError subclasses
        return None


def base_from_aud(aud: str) -> str | None:
    """Map a resource audience to its API base (Graph v1.0 or Outlook REST v2.0)."""
    if "graph.microsoft" in aud:
        host = aud.replace("https://", "").rstrip("/")
        return f"https://{host}/v1.0"
    if "outlook" in aud and "office365" in aud:
        host = aud.replace("https://", "").rstrip("/")
        return f"https://{host}/api/v2.0"
    return None


def refresh(
    authority: str, tenant: str, client_id: str, scope: str, refresh_token: str, ctx: ssl.SSLContext
) -> tuple[dict | None, str]:
    """Redeem a refresh token for an access token. Returns (token_response, note)."""
    if "offline_access" not in scope.split():
        scope = f"offline_access {scope}"  # ask for a rotated refresh token back
    url = f"https://{authority}/{tenant}/oauth2/v2.0/token"
    form = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "scope": scope,
            "refresh_token": refresh_token,
        }
    ).encode()
    req = urllib.request.Request(
        url, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return json.loads(resp.read()), "ok"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:  # AAD returns a JSON error body; surface the first AADSTS line
            err = json.loads(body)
            desc = str(err.get("error_description", "")).splitlines()[0]
            return None, f"{err.get('error')}: {desc}"
        except ValueError:  # non-JSON error body (JSONDecodeError is a ValueError)
            return None, f"HTTP {exc.code}: {body[:300]}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, str(exc)


def api_get(
    base: str, path: str, token: str, ctx: ssl.SSLContext
) -> tuple[int, int | None, str | None]:
    """GET read-only. Returns (status, item_count_or_None, error_text_or_None)."""
    req = urllib.request.Request(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            body = json.loads(resp.read() or b"null")
            values = body.get("value") if isinstance(body, dict) else None
            count = len(values) if isinstance(values, list) else None
            return resp.status, count, None
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return 0, None, str(exc)


def main() -> int:
    """Redeem the refresh token, store the rotation, and optionally probe."""
    parser = argparse.ArgumentParser(description="Mint an access token from a refresh token.")
    parser.add_argument("--refresh-file", help="file with the refresh token (else MS_REFRESH env)")
    parser.add_argument("--scope", required=True, help="space-separated scope string to request")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--authority", default=DEFAULT_AUTHORITY)
    parser.add_argument("--out-dir", default=".secrets", help="dir for access.jwt / refresh.txt")
    parser.add_argument(
        "--probe", action="store_true", help="also make read-only calls with the new token"
    )
    parser.add_argument("--ca-bundle", help="CA bundle for TLS (else SSL_CERT_FILE / system trust)")
    args = parser.parse_args()

    if args.refresh_file:
        with open(args.refresh_file, encoding="utf-8") as handle:
            refresh_token = handle.read().strip()
    else:
        refresh_token = os.environ.get("MS_REFRESH", "").strip()
    if not refresh_token:
        print("No refresh token. Use --refresh-file PATH or set MS_REFRESH.", file=sys.stderr)
        return 2

    cafile = args.ca_bundle or os.environ.get("SSL_CERT_FILE")
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    print("=== Microsoft refresh-token exchange ===")
    print(f"authority: https://{args.authority}/{args.tenant}")
    print(f"client_id: {args.client_id}")
    print(f"scope:     {args.scope}\n")

    result, note = refresh(
        args.authority, args.tenant, args.client_id, args.scope, refresh_token, ctx
    )
    if result is None or "access_token" not in result:
        print("--- paste everything below back ---")
        print("=== SUMMARY ===")
        print("refresh:    FAILED")
        print(f"error:      {note}")
        print("=== END SUMMARY ===")
        return 1

    access = result["access_token"]
    claims = decode_claims(access) or {}
    aud = str(claims.get("aud", ""))
    base = base_from_aud(aud)

    os.makedirs(args.out_dir, exist_ok=True)
    os.chmod(args.out_dir, 0o700)
    with open(os.path.join(args.out_dir, "access.jwt"), "w", encoding="utf-8") as handle:
        handle.write(access)
    rotated = result.get("refresh_token")
    if rotated:  # AAD rotates the refresh token on each use; keep the newest
        with open(os.path.join(args.out_dir, "refresh.txt"), "w", encoding="utf-8") as handle:
            handle.write(rotated)

    probes: list[tuple[str, int, int | None, str | None]] = []
    if args.probe and base:
        for path in PROBE_PATHS:
            status, count, err = api_get(base, path, access, ctx)
            probes.append((path, status, count, err))

    exp = claims.get("exp")
    print("--- paste everything below back ---")
    print("=== SUMMARY ===")
    print("refresh:    OK")
    print(f"aud:        {aud}")
    print(f"scp:        {claims.get('scp')}")
    if isinstance(exp, (int, float)):
        print(f"ttl_secs:   {int(exp - time.time())}")
    print(f"rotated_rt: {'yes' if rotated else 'no'}")
    print(f"api_base:   {base or 'undetermined (aud not Graph/Outlook)'}")
    if args.probe and not base:
        print("probes:     skipped (could not map aud to an API base)")
    for path, status, count, err in probes:
        tail = f"  count={count}" if count is not None else ""
        print(f"GET {path:<22} {status}{tail}")
        if err:
            print(f"  error: {err}")
    print("=== END SUMMARY ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
