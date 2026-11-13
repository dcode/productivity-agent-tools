#!/usr/bin/env python3
"""Microsoft Graph sign-in probe (throwaway spike, not product code).

Answers one question before any Graph adapter is built: can this managed
laptop sign in to its Microsoft 365 tenant with a *public* client id and
reach Graph, or does a Conditional Access / consent policy block it?

It signs in with MSAL and makes a few read-only Graph calls, then prints a
SUMMARY block to paste back. It changes nothing in the tenant and needs no
app registration of your own.

Run it (needs only MSAL, pulled in for the run; nothing is added to the
project):

    uv run --with msal python scripts/ms_graph_probe.py

Primary flow is authorization-code + PKCE: it opens your default browser,
you sign in as normal (smart card / MFA included), and it redirects to
http://localhost. If your default browser is locked down, MSAL prints a URL
you can paste into Safari. To try the headless device-code flow instead
(often blocked by default in corporate tenants since Feb 2025, which is
exactly what we want to find out):

    uv run --with msal python scripts/ms_graph_probe.py --device-code

Useful overrides:

    --tenant <id|organizations|common>   default: organizations
    --client-id <guid>                    default: Microsoft Graph Command Line Tools
    --scopes "User.Read Calendars.Read"   default: read-only me/calendar/mail
    --port <n>                            fix the loopback port (else ephemeral)

What it prints is status codes and counts, plus your own account name and the
exact AADSTS error if sign-in is refused. No message bodies or event details
are read. Redact the account line if you would rather not share it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# "Microsoft Graph Command Line Tools" — the public (secret-less), multi-tenant
# first-party client the Graph PowerShell SDK uses. Registered for loopback and
# device-code, with broad pre-consented delegated scopes. Overridable so your
# own registered app is a one-flag swap later.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
GRAPH = "https://graph.microsoft.com/v1.0"

# Read-only on purpose: delegated *.Read scopes are user-consentable, so the
# probe is not refused merely for wanting admin-consent permissions.
DEFAULT_SCOPES = ["User.Read", "Calendars.Read", "Mail.Read"]


def graph_get(path: str, token: str) -> tuple[int, dict | None, str | None]:
    """GET a Graph path. Returns (status, json_or_None, error_text_or_None)."""
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read() or b"null"), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, None, body
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return 0, None, str(exc)


def show_error(label: str, result: dict) -> None:
    """Print the MSAL/AAD error fields that tell us which policy refused us."""
    print(f"  {label}: FAILED")
    for key in ("error", "error_description", "error_codes", "suberror", "correlation_id"):
        if key in result:
            value = result[key]
            if key == "error_description" and isinstance(value, str):
                value = value.splitlines()[0]  # the AADSTS line; drop the trace
            print(f"    {key}: {value}")


def main() -> int:
    """Sign in with MSAL, run read-only Graph probes, and print a SUMMARY."""
    parser = argparse.ArgumentParser(description="Microsoft Graph sign-in probe.")
    parser.add_argument("--tenant", default="organizations")
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--scopes", default=" ".join(DEFAULT_SCOPES))
    parser.add_argument("--device-code", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    try:
        import msal
    except ImportError:
        print("msal is not available. Run with:")
        print("  uv run --with msal python scripts/ms_graph_probe.py")
        return 2

    scopes = args.scopes.split()
    authority = f"https://login.microsoftonline.com/{args.tenant}"
    flow_name = "device-code" if args.device_code else "auth-code + PKCE (interactive)"

    print("=== Microsoft Graph probe ===")
    print(f"client_id: {args.client_id}")
    print(f"authority: {authority}")
    print(f"scopes:    {scopes}")
    print(f"flow:      {flow_name}\n")

    app = msal.PublicClientApplication(args.client_id, authority=authority)

    if args.device_code:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            print("Could not start device-code flow:")
            show_error("initiate_device_flow", flow)
            _summary(flow_name, args, scopes, ok=False, result=flow)
            return 1
        print(flow["message"])  # tells you where to go and the code to enter
        print("\nWaiting for you to complete sign-in in the browser...\n")
        result = app.acquire_token_by_device_flow(flow)
    else:
        try:
            kwargs = {"port": args.port} if args.port is not None else {}
            print("Opening your default browser for sign-in")
            print("(watch for a pasteable URL if it doesn't open)...\n")
            result = app.acquire_token_interactive(scopes=scopes, **kwargs)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            print(f"Interactive flow raised: {detail}")
            print("If this is a headless/browser-locked issue, retry with --device-code.")
            _summary(flow_name, args, scopes, ok=False, result={"error": detail})
            return 1

    if "access_token" not in result:
        print("Sign-in did not return a token:")
        show_error("acquire_token", result)
        _summary(flow_name, args, scopes, ok=False, result=result)
        return 1

    token = result["access_token"]
    granted = result.get("scope", "")
    expires_in = result.get("expires_in")
    accounts = app.get_accounts()
    username = accounts[0]["username"] if accounts else "(none reported)"

    print("Sign-in OK.\n")

    # Prove silent refresh works from the cache MSAL just populated.
    silent = app.acquire_token_silent(scopes, account=accounts[0]) if accounts else None
    silent_ok = bool(silent and "access_token" in silent)

    # Read-only Graph probes, calendar first (highest priority).
    me_status, me, me_err = graph_get("/me", token)
    me_upn = me.get("userPrincipalName") if me else None
    me_name = me.get("displayName") if me else None

    cal_status, cal, cal_err = graph_get("/me/calendars", token)
    cal_names = [c.get("name") for c in cal.get("value", [])] if cal else []

    ev_status, ev, ev_err = graph_get("/me/events?$top=1&$select=id", token)
    ev_count = len(ev.get("value", [])) if ev else None

    msg_status, msg, msg_err = graph_get("/me/messages?$top=1&$select=id", token)
    msg_count = len(msg.get("value", [])) if msg else None

    print("--- paste everything below back ---")
    print("=== SUMMARY ===")
    print(f"flow:            {flow_name}")
    print(f"tenant:          {args.tenant}")
    print(f"client_id:       {args.client_id}")
    print("sign_in:         OK")
    print(f"granted_scopes:  {granted}")
    print(f"token_ttl_secs:  {expires_in}")
    print(f"silent_refresh:  {'OK' if silent_ok else 'FAILED'}")
    print(f"account:         {username}   (redact if you prefer)")
    print(f"GET /me:                  {me_status}  upn={me_upn} name={me_name}")
    if me_err:
        print(f"  /me error: {me_err[:300]}")
    print(f"GET /me/calendars:        {cal_status}  count={len(cal_names)} names={cal_names}")
    if cal_err:
        print(f"  /me/calendars error: {cal_err[:300]}")
    print(f"GET /me/events?$top=1:    {ev_status}  returned={ev_count}")
    if ev_err:
        print(f"  /me/events error: {ev_err[:300]}")
    print(f"GET /me/messages?$top=1:  {msg_status}  returned={msg_count}")
    if msg_err:
        print(f"  /me/messages error: {msg_err[:300]}")
    print("=== END SUMMARY ===")
    return 0


def _summary(
    flow_name: str,
    args: argparse.Namespace,
    scopes: list[str],
    *,
    ok: bool,
    result: dict,
) -> None:
    """Print a minimal SUMMARY on failure so a blocked tenant is still legible."""
    print("\n--- paste everything below back ---")
    print("=== SUMMARY ===")
    print(f"flow:            {flow_name}")
    print(f"tenant:          {args.tenant}")
    print(f"client_id:       {args.client_id}")
    print(f"scopes:          {scopes}")
    print("sign_in:         FAILED")
    for key in ("error", "error_description", "error_codes", "suberror", "correlation_id"):
        if key in result:
            value = result[key]
            if key == "error_description" and isinstance(value, str):
                value = value.splitlines()[0]
            print(f"{key}: {value}")
    print("=== END SUMMARY ===")


if __name__ == "__main__":
    sys.exit(main())
