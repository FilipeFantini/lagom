"""The browser check's own logic: reading a net log and deciding what the page asked for.

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


def _log(*requests: tuple[str, str | None]) -> str:
    """A complete net log of URL_REQUEST_START_JOB events, as (url, initiator)."""
    events = [
        {
            "type": 2,
            "params": {"url": url, **({} if initiator is None else {"initiator": initiator})},
        }
        for url, initiator in requests
    ]
    return json.dumps(
        {"constants": {"logEventTypes": {"URL_REQUEST_START_JOB": 2}}, "events": events}
    )


def test_only_url_request_start_job_events_are_read_from_a_log_that_was_cut_off() -> None:
    requests = page_requests.net_log_requests(NETLOG.read_text())
    assert [(r.url, r.initiator) for r in requests] == [
        (PAGE, "not an origin"),
        ("http://127.0.0.1:4000/favicon.ico", OWN),
        ("https://clients2.browser.example.invalid/update", "not an origin"),
        ("HTTPS://Cdn.example.invalid/sp.js", OWN),
        ("http://127.0.0.1:4000/script.js", "http://127.0.0.1:40001"),
        ("https://no-initiator.example.invalid/x", None),
        ("wss://collect.example.invalid/socket", OWN),
    ]
    # Not read: another event type's url, a mailto:, and the event cut off mid-URL.


def test_the_event_type_is_found_by_name_not_by_number() -> None:
    log = json.dumps(
        {
            "constants": {"logEventTypes": {"URL_REQUEST_START_JOB": 7}},
            "events": [
                {"type": 7, "params": {"url": PAGE}},
                {"type": 2, "params": {"url": "https://other.example.invalid/"}},
            ],
        }
    )
    assert [r.url for r in page_requests.net_log_requests(log)] == [PAGE]


@pytest.mark.parametrize(
    "text", ["<html>not a log</html>", '{"constants": {}, "events": []}', '{"constants": {}}']
)
def test_a_text_that_is_not_a_net_log_is_an_error(text: str) -> None:
    with pytest.raises(ValueError, match="not a net log"):
        page_requests.net_log_requests(text)


def test_the_origin_is_scheme_host_and_port_in_lower_case() -> None:
    assert (
        page_requests.origin("HTTPS://Cdn.Example.invalid/a?b#c") == "https://cdn.example.invalid"
    )
    assert page_requests.origin(PAGE) == OWN


def test_a_foreign_request_the_browser_made_on_its_own_is_ignored() -> None:
    log = _log(
        (PAGE, "not an origin"), ("https://clients2.browser.example.invalid/u", "not an origin")
    )
    assert page_requests.problems(log, PAGE) == []
    # No initiator at all is the browser's own too.
    assert (
        page_requests.problems(_log((PAGE, None), ("https://x.example.invalid/", None)), PAGE) == []
    )


def test_a_foreign_request_the_page_caused_is_a_finding() -> None:
    log = _log((PAGE, "not an origin"), ("https://cdn.example.invalid/sp.js", OWN))
    assert page_requests.problems(log, PAGE) == [
        "request to another origin: https://cdn.example.invalid/sp.js"
    ]


def test_a_request_initiated_by_another_real_origin_is_a_finding() -> None:
    log = _log(
        (PAGE, "not an origin"), ("http://127.0.0.1:4000/a.js", "https://elsewhere.example.invalid")
    )
    assert page_requests.problems(log, PAGE) == [
        "request initiated by another origin https://elsewhere.example.invalid: "
        "http://127.0.0.1:4000/a.js"
    ]


def test_the_fixture_gives_exactly_the_page_caused_findings() -> None:
    assert page_requests.problems(NETLOG.read_text(), PAGE) == [
        "request to another origin: HTTPS://Cdn.example.invalid/sp.js",
        "request initiated by another origin http://127.0.0.1:40001: http://127.0.0.1:4000/script.js",
        "request to another origin: wss://collect.example.invalid/socket",
    ]


def test_a_same_origin_page_that_asks_for_nothing_else_passes() -> None:
    assert page_requests.problems(_log((PAGE, "not an origin"), (f"{OWN}/app.js", OWN)), PAGE) == []


def test_a_log_without_the_pages_own_address_fails_even_with_nothing_foreign() -> None:
    log = _log((f"{OWN}/favicon.ico", OWN))
    assert page_requests.problems(log, PAGE) == [
        f"the net log does not hold the page's own address {PAGE}"
    ]


def test_a_missing_or_empty_page_fails_without_starting_a_browser(tmp_path: Path) -> None:
    empty = tmp_path / "empty.html"
    empty.write_text("")
    for page in (empty, tmp_path / "missing.html"):
        assert page_requests.check_page("/no/such/browser", page) == [f"{page} is missing or empty"]
