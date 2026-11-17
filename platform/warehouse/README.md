# warehouse — the dbt project

The single dbt project ([ADR-0006](../../docs/adr/0006-monorepo-layout.md)). It
reads bronze, which dlt loads, and builds silver and gold in the same Postgres
([ADR-0008](../../docs/adr/0008-postgres-for-all-layers.md)).

| Layer | Schema | Models | What it guarantees |
|---|---|---|---|
| bronze | `bronze` | none: declared as a `source` | raw, typed, every revision of an event |
| silver | `silver` | `models/staging/usgs/stg_usgs__events` | one row per event, its latest revision, typed and renamed, times in UTC `timestamptz`; every event type |
| gold | `gold` | `models/marts/seismic/fct_earthquakes`, `agg_earthquakes_daily_region` | the earthquakes only, with magnitude and depth classes; and per UTC day and region counts. **Contract enforced** |

Models are tables, rebuilt whole on each run: at about 300 000 events a year
that costs seconds, and it makes a re-run give the same result by construction
(protocol 16.1). `macros/generate_schema_name.sql` makes the schemas exactly
`silver` and `gold`, and stops the run for a model that names none.

## What the models decide

- **Latest revision.** Bronze keeps every revision of an event. Silver ranks
  them by `updated` and keeps the first, so a revised magnitude or a change from
  `automatic` to `reviewed` replaces the older row.
- **Region** is the text after the last comma of `place`, trimmed; a place with
  no comma is its own region (`Fiji region`).
- **Classes.** Magnitude: below 2 micro, 4 minor, 5 light, 6 moderate, 7 strong,
  8 major, from 8 great. Lower bounds are inclusive. Depth: below 70 km shallow,
  up to 300 km intermediate, deeper deep. A null stays null, so the not-null
  tests on magnitude, depth, and both classes fail instead of filing it under a
  class. A place ending in a comma gives a null region, not an empty one.
- **Time.** `to_timestamp` reads epoch milliseconds as UTC; no timestamp is
  naive. The daily aggregate cuts the day explicitly at UTC. No SQL here reads
  the clock; only the freshness check below compares with `now`, because that
  is what freshness means.

## Tests

`dbt build` runs them in dependency order and stops a model's dependents when it
fails.

| Kind | Where | What |
|---|---|---|
| Contract | `_seismic__models.yml` | both gold models: column names and types are compared with the SQL before the table is written |
| Generic | the `_*__models.yml` files | `unique`, `not_null`, `accepted_values` on keys and classes; `value_between` (`tests/generic/`) on latitude and longitude |
| Reconciliation | `tests/reconcile_*.sql` | silver rows equal distinct ids in bronze; gold rows equal silver earthquakes; the daily counts add up to gold rows; no gold id is missing from silver |
| Privileges | `tests/gold_reader_privileges.sql` | `gold_reader` can read every gold relation and use the `gold` schema, reads nothing in bronze or silver, and writes nowhere — checked in the database's catalogue, after both gold models are built |
| Unit | `unit_tests:` in the model files | given rows and expected rows: latest revision, region parsing, type conversion, class boundaries, UTC day boundary |
| Freshness | `_usgs__sources.yml` | newest `updated` in bronze: warn after 2 hours, error after 6. **Run separately**, `dbt source freshness`; `dbt build` does not |

A reconciliation test reads bronze live. If dlt loads between the silver build
and the test, it fails for a reason that is not a defect: run the build again.

**In CI**, every pull request runs `dbt build` on the `lake` target over a
hand-made bronze table, [`ci/bronze_usgs_events.sql`](ci/bronze_usgs_events.sql):
14 rows, no real data, in a DuckLake on the runner's disk, loaded by
[`ci/load_fixture.py`](ci/load_fixture.py). No credential, no R2, no Postgres.
It runs the models, the contracts, the unit tests, the data tests and the
reconciliations (`PASS=33` on 2026-11-07), with the same dbt and DuckDB
versions as the image, since dbt-duckdb is locked in
[`platform/orchestration/`](../orchestration/pyproject.toml). The rows are
chosen to reach each rule: two revisions of one event, every magnitude and
depth class, a quarry blast (silver, not gold), a status in capitals, a place
with no comma, an event after midnight UTC, and the two seconds either side of
the edge of the dashboards' 30-day window
([`products/dashboards/`](../../products/dashboards/README.md)). A fixture row that breaks a
test fails the check.

What CI does not cover: `gold_reader_privileges` (it needs a Postgres role, and
`dbt_project.yml` enables it on the `postgres` target only), freshness, and
anything only real data shows: a column dlt adds, renames or retypes reaches
the fixture only when someone edits it, so the first proof of such a change is
the next run in staging. Dagster runs `dbt build` on the `lake` target after
every dlt load, in staging and prod
([`platform/orchestration/`](../orchestration/README.md)).
The CronJobs that ran it on Postgres were retired on 2026-11-07
([ADR-0033](../../docs/adr/0033-dagster-alerts-through-healthchecks.md)).

## Run it against dev

Per [ADR-0014](../../docs/adr/0014-develop-against-staging.md) the code runs on
your machine and the data lives in `lagom_dev`, in staging's Postgres. Get the
password into the shell and forward the port as the runbook describes
([runbook.md, Postgres, Staging](../../docs/runbook.md#staging)); it
holds the password in `PGPASSWORD`. Then, from the repository root:

```sh
export LAGOM_PG_HOST=127.0.0.1 LAGOM_PG_PORT=15432 LAGOM_PG_USER=lagom_dev \
       LAGOM_PG_DBNAME=lagom_dev LAGOM_PG_SSLMODE=disable
export LAGOM_PG_PASSWORD="$PGPASSWORD"

dbt() { uv run --project platform/warehouse --locked dbt "$@" \
          --project-dir platform/warehouse --profiles-dir platform/warehouse; }
dbt build
dbt source freshness
```

`profiles.yml` is committed because it holds no value: every setting is read
from `LAGOM_PG_*` and none has a default. A missing variable stops dbt naming
it, with one dbt quirk: for `password` dbt falls back to the literal template
text, so a missing `LAGOM_PG_PASSWORD` shows up as Postgres refusing the login,
not as a message naming the variable.

**`sslmode=disable` is deliberate here.** `kubectl port-forward` closed the
tunnel after the first TLS session ended, so a second connection (dbt opens
several) failed. The hop it carries is already encrypted: Tailscale between your
machine and the node, TLS between `kubectl` and the API server. What is given up
is Postgres's own TLS inside that tunnel, the accepted residual. Do not copy
`disable` to a connection that does not go through a port-forward.

Lint, as CI runs it (dialect `postgres`, jinja templater, config in
`.sqlfluff`):

```sh
uv run --project platform/warehouse --locked sqlfluff lint platform/warehouse
```

## Two targets: Postgres and DuckLake

`dev` (and the CronJobs' environment) builds on Postgres. `lake` builds the
same models on DuckLake with DuckDB, for the SME stack run by Dagster
([ADR-0026](../../docs/adr/0026-dagster-one-instance-per-environment.md)). Its
settings are listed in `profiles.yml` and in the code location's
[README](../orchestration/README.md). Three things differ by target:

- The SQL is portable: `trim`, not Postgres's `btrim`.
- The `gold_reader` grants and the privileges test run on Postgres only.
  DuckLake has no per-table grants: read access there is an R2 token and a
  catalog role ([ADR-0023](../../docs/adr/0023-sme-stack-duckdb-on-ducklake.md)).
- **Partial parsing can carry one target's result into the other.** Pass
  `--no-partial-parse` when switching targets in the same checkout.

## The `serving` target: the streaming showcase

A third target, on the database `serving` of an environment's Postgres
([ADR-0047](../../docs/adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md)).
It reads the same `LAGOM_PG_*` variables as `dev`. `dbt_project.yml` gives
each folder `+enabled` by target name: under `serving` only
`models/streaming/` and `tests/streaming/` are built, and under every other
target they are not.

| What | Where | What it guarantees |
|---|---|---|
| live table | `live.events`, created by the `on-run-start` hook (`macros/create_live_events.sql`) if it is missing, never altered | `UNLOGGED`; one row per event, its key the event identifier; `received_at` is when the hub stored it, so a replay writes the same rows; the writer `events_writer` may insert and nothing else; an hour leaves it once all of it is older than 7 days and its rows are confirmed in the lake, by Dagster's `streaming_archive` ([`platform/orchestration/`](../orchestration/README.md)) |
| typed events | `streaming.stg_events__pings`, `stg_events__health` (views) | the payload's fields, typed |
| metrics | `metrics.*` (views), **contract enforced** | `link_minute`, `link_hour_profile`, `link_outages`, `source_health_minute`, `pipeline_minute`: what Grafana reads; grouped by `collected_at`, in UTC; SELECT on each, and USAGE on the schema, granted to Grafana's role `grafana_reader` on every build |

Views, not tables: each is computed when read, so a late event is a row with
an old `collected_at` that the next query counts. Two rules the views decide:
a lost ping has no round trip whatever its payload says, so it never enters
a percentile; and two pings to a target more than five seconds apart are not
consecutive, so an outage does not run across a gap with no events (the
source down) and jitter does not measure across one.

**Unit tests** (`unit_tests:` in the two YAML files) give rows and expect rows
for that logic: a lost ping's round trip; outages split by an answer and by a
gap, per target; the minute cut, percentiles and jitter across a minute
boundary and a lost ping; and the late rule at exactly one minute. Their
other tests are `not_null`,
`accepted_values`, `value_between` and `unique_combination` (the grain of each
view, `tests/generic/`), and `tests/streaming/live_window_is_full.sql`: it
fails when, over the last 60 whole minutes (less the last two), one of the
five streams holds fewer than 1,800 events of 3,600. It reads
`pipeline_minute`, so it runs last and stops no view. And
`tests/streaming/grafana_reader_privileges.sql` fails when `grafana_reader`
can read anything outside `metrics`, write anywhere, use `streaming` or
`live`, or has no connection limit or statement timeout (the timeout is set
by hand: runbook, *The streaming showcase in staging (M6), Grafana*); on
2026-11-17, against a local Postgres 18, it failed without the timeout and
with a grant on `live.events`, and passed as built (`PASS=28`). The table has no tests:
its primary key and `NOT NULL` hold them, and a test would scan a week of
rows.

**Tested on 2026-11-17 against a local Postgres 18**, with a NATS hub and
Redpanda Connect writing synthetic events (the configuration of the staging
manifests): `dbt build --target serving` passed 27 of 27 with an hour of
events, the four unit tests included; with six minutes of events only
`live_window_is_full` failed, as it should. No CI job runs this target yet: it needs a Postgres.

## Image

No image of its own since 2026-11-07: `lagom-warehouse` ran only in the
CronJobs on Postgres, retired that day
([ADR-0033](../../docs/adr/0033-dagster-alerts-through-healthchecks.md)). The
project is bundled into `lagom-orchestration`, whose `warehouse` asset runs
`dbt build` on the `lake` target, and whose `streaming` job runs it on
`serving` ([`platform/orchestration/`](../orchestration/README.md)).

## Known limits

- **`region` is USGS's text, not a normalized name.** It is whatever follows the
  last comma of `place`, so California appears as `CA` while other states are
  spelled out (`Alaska`, `Texas`). Seen in the first real build, 2026-10-17.
  Normalizing it needs a lookup the source does not provide; until then,
  aggregates by region compare like with like only within one naming style.
