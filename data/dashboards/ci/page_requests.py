"""What a browser asks the network for because of a published page.

CI's text search over a page (ci.yml) cannot see what a script adds at run
time, and gives false positives on URLs inside a script's text. This opens
each page in a real browser, with its network log on, and fails on any request
the page caused to an origin that is not the one serving it.

    python page_requests.py <browser> <page.html>...

Each page's directory is served on 127.0.0.1 (a random port, in a thread of
this process) and the browser opens that address, headless.

A browser also makes requests of its own (component updates, sync, the
sign-in and autofill services) that no flag fully silences, and which differ
by browser and version. They are told apart from the page's by the net log
itself, not by a list of hosts: every request is a `URL_REQUEST_START_JOB`
event, and its `initiator` is the origin of the page that caused it, or
"not an origin" for the browser's own requests and for the top-level
navigation. So, of those events:

- a request whose initiator is the server's origin and whose URL is on another
  origin is a finding: the page asked for it;
- a request whose initiator is any other real origin is a finding too;
- a request with no initiator, or "not an origin", is the browser's own and is
  not looked at. Nothing is allowlisted.

The log must hold the page's own address among those events, which proves the
log works and the page was asked for; a page that is never requested must not
pass as one that asks for nothing else.
"""

import functools
import json
import re
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import urlsplit

# --virtual-time-budget lets the page's timers and requests run for 15 s of
# virtual time, then --dump-dom prints the page and the browser exits, which
# is when the network log is complete. The timeout bounds the real time.
BROWSER_TIMEOUT_SECONDS = 90
SCHEMES = ("http", "https", "ws", "wss")


REQUEST_EVENT = "URL_REQUEST_START_JOB"
# What `initiator` holds when no page caused the request.
NO_ORIGIN = "not an origin"


class Request(NamedTuple):
    url: str
    initiator: str | None


def _is_request_url(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.split(":", 1)[0].lower() in SCHEMES
        and re.fullmatch(r"[a-zA-Z]+://[^\s<>]+", value) is not None
    )


def net_log_requests(text: str) -> list[Request]:
    """The requests in a Chromium net log: the `url` and `initiator` of each URL_REQUEST_START_JOB.

    The log is a JSON object: `constants` first (it names the event types by
    number, and the numbers change with the browser), then the `events` array,
    written as the browser runs and closed when it exits cleanly. A browser
    that is stopped leaves the array open, so the events are read one by one
    and the first that is cut off ends the read: what was written is kept.
    """
    decoder = json.JSONDecoder()
    marker = text.find('"constants"')
    if marker < 0:
        raise ValueError("not a net log: no constants")
    constants, _ = decoder.raw_decode(text, text.find("{", marker))
    event_type = constants.get("logEventTypes", {}).get(REQUEST_EVENT)
    if not isinstance(event_type, int):
        raise ValueError(f"not a net log: constants name no {REQUEST_EVENT} event")
    marker = text.find('"events"')
    if marker < 0:
        raise ValueError("not a net log: no events array")
    requests: list[Request] = []
    position = text.find("[", marker) + 1
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "]":
            break
        try:
            event, position = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            break  # The tail of a log that was cut off.
        params = event.get("params") if isinstance(event, dict) else None
        if isinstance(event, dict) and event.get("type") == event_type and isinstance(params, dict):
            url, initiator = params.get("url"), params.get("initiator")
            if isinstance(url, str) and _is_request_url(url):
                requests.append(Request(url, initiator if isinstance(initiator, str) else None))
    return requests


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def findings(requests: list[Request], own_origin: str) -> list[str]:
    """The requests a page caused that it should not have, one line each."""
    found: list[str] = []
    for url, initiator in requests:
        if initiator is None or initiator == NO_ORIGIN:
            continue  # The browser's own request, or the navigation to the page.
        if origin(initiator) != own_origin:
            found.append(f"request initiated by another origin {initiator}: {url}")
        elif origin(url) != own_origin:
            found.append(f"request to another origin: {url}")
    return found


def problems(log: str, page_url: str) -> list[str]:
    """What is wrong with the requests in `log` for the page at `page_url`; empty if nothing."""
    requests = net_log_requests(log)
    found: list[str] = []
    if page_url not in [request.url for request in requests]:
        found.append(f"the net log does not hold the page's own address {page_url}")
    return found + findings(requests, origin(page_url))


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def check_page(browser: str, page: Path) -> list[str]:
    """Open `page` in `browser` through a local server; return the problems found."""
    if not page.is_file() or page.stat().st_size == 0:
        return [f"{page} is missing or empty"]
    handler = functools.partial(_Quiet, directory=str(page.parent))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        page_url = f"http://127.0.0.1:{server.server_address[1]}/{page.name}"
        try:
            with tempfile.TemporaryDirectory(prefix="page-requests-") as profile:
                log = Path(profile) / "netlog.json"
                done = subprocess.run(
                    [
                        browser,
                        "--headless=new",
                        "--disable-gpu",
                        # The sandbox needs privileges a CI runner's container
                        # may not give. This browser opens only our own fixture
                        # page, from localhost, in a runner that is thrown away.
                        "--no-sandbox",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-sync",
                        "--disable-extensions",
                        "--metrics-recording-only",
                        f"--user-data-dir={profile}/data",
                        f"--log-net-log={log}",
                        "--virtual-time-budget=15000",
                        "--dump-dom",
                        page_url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=BROWSER_TIMEOUT_SECONDS,
                )
                if done.returncode != 0:
                    return [f"the browser exited {done.returncode}: {done.stderr[-500:]}"]
                if not log.is_file():
                    return ["the browser wrote no net log"]
                return problems(log.read_text(encoding="utf-8", errors="replace"), page_url)
        except subprocess.TimeoutExpired:
            return [f"the browser did not finish within {BROWSER_TIMEOUT_SECONDS} s"]
        finally:
            server.shutdown()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: page_requests.py <browser> <page.html>...", file=sys.stderr)
        return 2
    browser, pages = argv[0], [Path(argument) for argument in argv[1:]]
    failed = False
    for page in pages:
        found = check_page(browser, page)
        for line in found:
            print(f"{page}: {line}")
        if not found:
            print(f"{page}: requested nothing from outside")
        failed = failed or bool(found)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
