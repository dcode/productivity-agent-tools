"""A stand-in browser for tests: speaks the DevTools pipe protocol on descriptors 3 and 4.

Run as the "browser binary" by the tests of ``productivity_agent_tools.browser``. Its
behaviour is chosen with the FAKE_BROWSER environment variable.
"""

import json
import os
import signal
import sys
import time

MODE = os.environ.get("FAKE_BROWSER", "normal")


def send(message: dict[str, object]) -> None:
    raw = json.dumps(message).encode() + b"\0"
    # Split every message in two writes, so the reader has to reassemble it.
    os.write(4, raw[:7])
    os.write(4, raw[7:])


def answer(request: dict[str, object]) -> dict[str, object] | None:
    method = request["method"]
    if method == "Browser.getVersion":
        return {"product": "Fake/1.0"}
    if method == "Target.getTargets":
        if MODE == "vanish":
            sys.exit(0)
        opened = sys.argv[-1]
        return {
            "targetInfos": [
                {"type": "page", "url": opened + "home?tab=1"},
                {"type": "page", "url": opened.rstrip("/")},
                {"type": "page", "url": "https://sign-in.example/sso?SAMLRequest=x"},
                {"type": "service_worker", "url": opened + "worker.js"},
                "not an object",
            ]
        }
    if method == "Target.setDiscoverTargets":
        return None if MODE == "refuse" else {}
    if method == "Storage.getCookies":
        if MODE == "refuse":
            return None
        flags = [arg for arg in sys.argv if arg.startswith("--")]
        return {
            "cookies": [
                {"name": "JSESSIONID", "value": "abc", "domain": "desk.example"},
                {"name": "route", "value": "r1", "domain": ".example"},
                {"name": "other", "value": "no", "domain": "elsewhere.test"},
                {"name": "", "value": "nameless", "domain": "desk.example"},
                {"name": "flags", "value": "|".join(sorted(flags)), "domain": "desk.example"},
            ]
        }
    return {}


def main() -> None:
    if MODE == "stubborn":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    buffer = b""
    while True:
        chunk = os.read(3, 65536)
        if not chunk:
            return
        buffer += chunk
        while b"\0" in buffer:
            raw, buffer = buffer.split(b"\0", 1)
            request = json.loads(raw)
            if MODE in ("silent", "stubborn") and request["method"] != "Browser.getVersion":
                if MODE == "stubborn":
                    time.sleep(30)
                continue
            if request["method"] == "Browser.close" and MODE != "deaf":
                send({"id": request["id"], "result": {}})
                return
            send({"method": "Target.targetCreated", "params": {}})
            if request["method"] == "Target.setDiscoverTargets" and MODE != "refuse":
                # A redirect a tab passed through, then a worker that is not a page.
                fleeting = {"type": "page", "url": "https://app.example/back#code=fleeting"}
                worker = {"type": "service_worker", "url": "https://app.example/sw.js"}
                send({"method": "Target.targetInfoChanged", "params": {"targetInfo": fleeting}})
                send({"method": "Target.targetCreated", "params": {"targetInfo": worker}})
            result = answer(request)
            if result is None:
                send({"id": request["id"], "error": {"code": -32000, "message": "Not allowed"}})
            else:
                send({"id": request["id"], "result": result})


if __name__ == "__main__":
    main()
