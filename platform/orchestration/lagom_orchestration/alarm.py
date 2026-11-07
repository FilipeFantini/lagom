"""Dagster raises the alarm through Healthchecks.io (ADR-0033, ADR-0018).

Two sensors, both running in the code server:

- after each successful `usgs` run, a ping to the environment's check: a
  missing ping is the alarm for "nothing runs";
- after any failed run in this code location, a call to the check's `/fail`
  with the job, the run ID and the error, so the e-mail says why.

The ping URL is a secret: whoever has it can silence the alarm. It is read
from `HEARTBEAT_URL` when the code location loads, and nothing here prints it,
logs it, or puts it in an exception message.
"""

import os
import time

import requests
from dagster import (
    DagsterRunStatus,
    DefaultSensorStatus,
    RunFailureSensorContext,
    RunStatusSensorContext,
    SensorDefinition,
    run_failure_sensor,
    run_status_sensor,
)

# define_asset_job returns this type; dagster does not export it at the top level.
from dagster._core.definitions.unresolved_asset_job_definition import UnresolvedAssetJobDefinition

HEARTBEAT_URL_VAR = "HEARTBEAT_URL"

ATTEMPTS = 3
TIMEOUT_SECONDS = 10
RETRY_DELAY_SECONDS = 2

# Healthchecks.io documents that it stores the first 100 kB of a ping's body
# and ignores the rest (https://healthchecks.io/docs/http_api/, read
# 2026-11-07; the response header Ping-Body-Limit says 100000). The cap is a
# tenth of that: an e-mail is read by a person, and the start of the error and
# the end of a traceback are what they need.
MAX_BODY_BYTES = 10_000
TRUNCATION_MARK = "\n[... truncated ...]\n"


def heartbeat_url() -> str:
    """The check's ping URL, or an error that does not name its value."""
    url = os.environ.get(HEARTBEAT_URL_VAR)
    if not url:
        raise RuntimeError(
            f"environment variable {HEARTBEAT_URL_VAR} is not set or is empty: "
            "the code location cannot raise the alarm (ADR-0033)"
        )
    return url


def send(url: str, body: str) -> None:
    """POST a plain-text body to `url`; raise once `ATTEMPTS` have failed.

    The exception of a failed request carries the URL in its text, so it is
    never chained or printed: the last failure is reduced to its class name or
    its HTTP status, and raised outside the `except` block.
    """
    reason = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            reason = type(error).__name__
        else:
            if response.ok:
                return
            reason = f"HTTP {response.status_code}"
        if attempt < ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Healthchecks.io was not reached after {ATTEMPTS} attempts: {reason}")


def truncate(text: str, limit: int = MAX_BODY_BYTES) -> str:
    """Keep the first and the last of `text` within `limit` bytes of UTF-8.

    The head holds the job and the run ID, the tail holds the exception a
    traceback ends with; the middle goes.
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    mark = TRUNCATION_MARK.encode("utf-8")
    head = (limit - len(mark)) // 2
    tail = limit - len(mark) - head
    # `ignore` drops a multi-byte character cut in half.
    return (
        raw[:head].decode("utf-8", errors="ignore")
        + TRUNCATION_MARK
        + raw[len(raw) - tail :].decode("utf-8", errors="ignore")
    )


def failure_report(context: RunFailureSensorContext) -> str:
    """What the e-mail says: the job, the run ID, and why the run failed."""
    run = context.dagster_run
    lines = [
        f"job: {run.job_name}",
        f"run: {run.run_id}",
        f"message: {context.failure_event.message}",
    ]
    for event in context.get_step_failure_events():
        error = event.step_failure_data.error
        if error is not None:
            lines.append(f"step {event.step_key} failed:\n{error.to_string()}")
    return truncate("\n".join(lines))


def build_alarm_sensors(usgs_job: UnresolvedAssetJobDefinition) -> list[SensorDefinition]:
    """The two sensors, reading `HEARTBEAT_URL` now: a code location without it does not load."""
    url = heartbeat_url()

    @run_status_sensor(
        name="usgs_success_heartbeat",
        run_status=DagsterRunStatus.SUCCESS,
        monitored_jobs=[usgs_job],
        default_status=DefaultSensorStatus.RUNNING,
    )
    def usgs_success_heartbeat(context: RunStatusSensorContext) -> None:
        run = context.dagster_run
        send(url, f"job: {run.job_name}\nrun: {run.run_id}\nsucceeded")

    @run_failure_sensor(
        name="run_failure_alarm",
        default_status=DefaultSensorStatus.RUNNING,
    )
    def run_failure_alarm(context: RunFailureSensorContext) -> None:
        send(f"{url.rstrip('/')}/fail", failure_report(context))

    return [usgs_success_heartbeat, run_failure_alarm]
