"""The alarm sensors (ADR-0033): what they send, when, and what they never show."""

import logging

import pytest
import requests
import requests_mock
from dagster import (
    DagsterInstance,
    DefaultSensorStatus,
    RunFailureSensorContext,
    build_run_status_sensor_context,
    define_asset_job,
    job,
    op,
)

from lagom_orchestration import alarm

# A placeholder: the ".invalid" TLD never resolves, and a real ping URL is a
# secret that must not be committed.
SECRET = "00000000-0000-0000-0000-000000000000"
URL = f"https://hc-ping.invalid/{SECRET}"
FAIL_URL = f"{URL}/fail"

# What definitions.py passes: an asset job named "usgs".
USGS = define_asset_job("usgs", selection="*")


@op
def fine() -> None:
    pass


@job(name="usgs")
def succeeds() -> None:
    fine()


@op
def boom() -> None:
    raise ValueError("the load failed: unexpected field")


@op
def boom_long() -> None:
    raise ValueError("x" * 50_000 + " the real cause")


@job
def explodes() -> None:
    boom()


@job
def explodes_long() -> None:
    boom_long()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retries do not wait in tests; the pauses are recorded."""
    pauses: list[float] = []
    monkeypatch.setattr(alarm.time, "sleep", pauses.append)
    return pauses


@pytest.fixture
def sensors():  # type: ignore[no-untyped-def]
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(alarm.HEARTBEAT_URL_VAR, URL)
        return {s.name: s for s in alarm.build_alarm_sensors(USGS)}


def failed_context(failing_job=explodes) -> RunFailureSensorContext:  # type: ignore[no-untyped-def]
    instance = DagsterInstance.ephemeral()
    result = failing_job.execute_in_process(instance=instance, raise_on_error=False)
    assert not result.success
    context = build_run_status_sensor_context(
        sensor_name="run_failure_alarm",
        dagster_instance=instance,
        dagster_run=result.dagster_run,
        dagster_event=result.get_run_failure_event(),
    )
    return context.for_run_failure()


def test_both_sensors_run_by_default(sensors) -> None:  # type: ignore[no-untyped-def]
    """A sensor that is off sends nothing, and the failure sensor's silence is
    caught by nothing else (ADR-0033, Risks)."""
    assert set(sensors) == {"usgs_success_heartbeat", "run_failure_alarm"}
    for sensor in sensors.values():
        assert sensor.default_status == DefaultSensorStatus.RUNNING


def test_the_code_location_does_not_load_without_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(alarm.HEARTBEAT_URL_VAR, raising=False)
    with pytest.raises(RuntimeError, match=alarm.HEARTBEAT_URL_VAR):
        alarm.build_alarm_sensors(USGS)
    monkeypatch.setenv(alarm.HEARTBEAT_URL_VAR, "")
    with pytest.raises(RuntimeError, match=alarm.HEARTBEAT_URL_VAR):
        alarm.build_alarm_sensors(USGS)


def test_the_success_sensor_watches_only_the_usgs_job(sensors) -> None:  # type: ignore[no-untyped-def]
    # Dagster applies this filter when it evaluates the sensor; a direct call
    # skips it, so the declaration itself is checked.
    monitored = sensors["usgs_success_heartbeat"]._monitored_jobs
    assert [monitored_job.name for monitored_job in monitored] == ["usgs"]


def test_success_pings_the_check(sensors) -> None:  # type: ignore[no-untyped-def]
    instance = DagsterInstance.ephemeral()
    result = succeeds.execute_in_process(instance=instance)
    assert result.success
    context = build_run_status_sensor_context(
        sensor_name="usgs_success_heartbeat",
        dagster_instance=instance,
        dagster_run=result.dagster_run,
        dagster_event=result.get_run_success_event(),
    )
    with requests_mock.Mocker() as mock:
        mock.post(URL, text="OK")
        sensors["usgs_success_heartbeat"](context)
    assert mock.call_count == 1
    assert mock.last_request.url == URL
    assert "job: usgs" in mock.last_request.text
    assert result.dagster_run.run_id in mock.last_request.text


def test_failure_calls_fail_with_the_job_the_run_and_the_error(sensors) -> None:  # type: ignore[no-untyped-def]
    context = failed_context()
    with requests_mock.Mocker() as mock:
        mock.post(FAIL_URL, text="OK")
        sensors["run_failure_alarm"](context)
    assert mock.call_count == 1
    request = mock.last_request
    assert request.url == FAIL_URL
    assert request.headers["Content-Type"].startswith("text/plain")
    assert "job: explodes" in request.text
    assert f"run: {context.dagster_run.run_id}" in request.text
    assert "ValueError: the load failed: unexpected field" in request.text


def test_a_huge_failure_reaches_the_check_within_the_limit(sensors) -> None:  # type: ignore[no-untyped-def]
    context = failed_context(explodes_long)
    with requests_mock.Mocker() as mock:
        mock.post(FAIL_URL, text="OK")
        sensors["run_failure_alarm"](context)
    body = mock.last_request.body
    assert len(body) <= alarm.MAX_BODY_BYTES
    text = body.decode("utf-8")
    assert text.startswith("job: explodes_long\nrun: ")
    assert "the real cause" in text
    assert alarm.TRUNCATION_MARK in text


def test_truncation_keeps_both_ends_and_whole_characters() -> None:
    text = "job: usgs\nrun: abc\n" + "é" * 50_000 + "\nValueError: the real cause"
    cut = alarm.truncate(text)
    assert len(cut.encode("utf-8")) <= alarm.MAX_BODY_BYTES
    assert cut.startswith("job: usgs\nrun: abc\n")
    assert cut.endswith("ValueError: the real cause")
    assert alarm.truncate("short") == "short"


def test_a_failed_ping_is_retried_until_it_goes_through() -> None:
    with requests_mock.Mocker() as mock:
        mock.post(URL, [{"status_code": 502}, {"exc": requests.ConnectTimeout}, {"text": "OK"}])
        alarm.send(URL, "x")
    assert mock.call_count == 3


def test_three_failed_attempts_raise_without_the_url(no_sleep, caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    with requests_mock.Mocker() as mock:
        # Real request errors carry the URL in their text; this one does too.
        mock.post(URL, exc=requests.ConnectionError(f"cannot reach {URL}"))
        with pytest.raises(RuntimeError) as raised:
            alarm.send(URL, "x")
    assert mock.call_count == alarm.ATTEMPTS == 3
    assert no_sleep == [alarm.RETRY_DELAY_SECONDS] * 2
    assert "ConnectionError" in str(raised.value)
    assert URL not in str(raised.value) and SECRET not in str(raised.value)
    # Nor does the traceback, which would chain the exception that was caught.
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert SECRET not in caplog.text


def test_an_http_error_status_fails_the_tick_without_the_url() -> None:
    with requests_mock.Mocker() as mock:
        mock.post(URL, status_code=404, text="not found")
        with pytest.raises(RuntimeError, match="HTTP 404") as raised:
            alarm.send(URL, "x")
    assert mock.call_count == 3
    assert SECRET not in str(raised.value)


def test_a_sensor_tick_fails_when_the_check_is_unreachable(sensors) -> None:  # type: ignore[no-untyped-def]
    context = failed_context()
    with requests_mock.Mocker() as mock:
        mock.post(FAIL_URL, status_code=500)
        with pytest.raises(RuntimeError, match="HTTP 500") as raised:
            sensors["run_failure_alarm"](context)
    assert mock.call_count == 3
    assert SECRET not in str(raised.value)


def test_the_missing_variable_message_shows_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(alarm.HEARTBEAT_URL_VAR, "")
    with pytest.raises(RuntimeError) as raised:
        alarm.heartbeat_url()
    assert "https://" not in str(raised.value)
