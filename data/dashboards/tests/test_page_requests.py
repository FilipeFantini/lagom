"""The browser check's own logic: reading a net log and deciding what is foreign.

The browser is not run here. A small net log in the shape Chromium writes,
cut off at the tail as a stopped browser leaves it, stands in for one.
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[1] / "ci" / "page_requests.py"
NETLOG = Path(__file__).parent / "fixtures" / "netlog.json"
PAGE = "http://127.0.0.1:4000/page.html"
OWN = "http://127.0.0.1:4000"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("page_requests", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["page_requests"] = module
    spec.loader.exec_module(module)
    return module


page_requests = _load()


def test_the_urls_are_read_from_a_log_that_was_cut_off() -> None:
    urls = page_requests.net_log_urls(NETLOG.read_text())
    assert urls == [
        PAGE,
        "http://127.0.0.1:4000/favicon.ico",
        "HTTPS://Cdn.example.invalid/sp.js",
        "https://redirect.example.invalid/next",
        "wss://collect.example.invalid/socket",
        "http://127.0.0.1:40001/other-port.js",
    ]
    # Not requests: the event that was cut mid-URL (not guessed at), a mailto:,
    # and the sites and hosts that other keys name.
    assert not [u for u in urls if "tail" in u or "mailto" in u or "only" in u or "sockets" in u]


def test_a_complete_log_gives_the_same_urls_as_a_cut_one() -> None:
    complete = json.dumps(
        {"events": [{"params": {"url": PAGE}}, {"params": {"headers": ["x"]}}, {"type": 3}]}
    )
    assert page_requests.net_log_urls(complete) == [PAGE]


def test_a_text_that_is_not_a_net_log_is_an_error() -> None:
    with pytest.raises(ValueError, match="no events array"):
        page_requests.net_log_urls("<html>not a log</html>")


def test_the_origin_is_scheme_host_and_port_in_lower_case() -> None:
    assert page_requests.origin("HTTPS://Cdn.Example.invalid/a?b#c") == (
        "https://cdn.example.invalid"
    )
    assert page_requests.origin(PAGE) == OWN


def test_only_other_origins_are_foreign() -> None:
    urls = page_requests.net_log_urls(NETLOG.read_text())
    assert page_requests.foreign_urls(urls, OWN) == [
        "HTTPS://Cdn.example.invalid/sp.js",
        "https://redirect.example.invalid/next",
        "wss://collect.example.invalid/socket",
        # Same host, another port: another origin.
        "http://127.0.0.1:40001/other-port.js",
    ]


def test_every_foreign_request_is_listed_by_the_check() -> None:
    found = page_requests.problems(NETLOG.read_text(), PAGE)
    assert found == [
        "request to another origin: HTTPS://Cdn.example.invalid/sp.js",
        "request to another origin: https://redirect.example.invalid/next",
        "request to another origin: wss://collect.example.invalid/socket",
        "request to another origin: http://127.0.0.1:40001/other-port.js",
    ]


def test_a_log_without_the_pages_own_address_fails_even_with_nothing_foreign() -> None:
    log = json.dumps({"events": [{"params": {"url": "http://127.0.0.1:4000/favicon.ico"}}]})
    assert page_requests.problems(log, PAGE) == [
        f"the net log does not hold the page's own address {PAGE}"
    ]


def test_a_page_that_asks_for_nothing_else_passes() -> None:
    log = json.dumps({"events": [{"params": {"url": PAGE}}]})
    assert page_requests.problems(log, PAGE) == []


def test_a_missing_or_empty_page_fails_without_starting_a_browser(tmp_path: Path) -> None:
    empty = tmp_path / "empty.html"
    empty.write_text("")
    for page in (empty, tmp_path / "missing.html"):
        found = page_requests.check_page("/no/such/browser", page)
        assert found == [f"{page} is missing or empty"]
