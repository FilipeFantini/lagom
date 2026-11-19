# data/dashboards/

The platform's dashboards, written as YAML for [dbt Charts](https://docs.dbtcharts.com/)
(`dbt-charts`, command `dct`) and rendered to one static HTML page per board
([ADR-0040](../../docs/adr/0040-dbt-charts-for-the-dashboards.md)). A board is
plain SQL over the warehouse's gold tables plus a description of the charts;
`dct` runs the SQL against the lake and draws the page.

This is M4's first slice, and its publishing path: the project, the gate that
keeps a wrong page from passing CI, and the Dagster job `dashboards` that renders
the boards against the real lake after each pipeline run and uploads them to an
R2 bucket ([ADR-0044](../../docs/adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md);
the job's code is `data/orchestration/lagom_orchestration/dashboards.py`).
The job runs in staging and prod since 2026-11-15, after each pipeline run. It is the path the dashboards take: a server of our own was designed between
2026-11-12 and 2026-11-14 and then stopped
([ADR-0042](../../docs/adr/0042-the-dashboards-return-to-dbt-charts.md)).

| Path | What it is |
|---|---|
| `partials/earthquakes.yml` | What the earthquakes boards share: the look, the queries and the charts. Not a board: it is outside `charts/`, so nothing renders it on its own |
| `charts/earthquakes.yml` | The first board: the earthquakes of the last 30 UTC days, the partial plus the table of the largest and the notes on the numbers |
| `charts/earthquakes_short.yml` | The same board without the table and the notes, for an article on ofantini.com to frame; its own layout over the same partial, checked like the full one |
| `dbt_charts.yml` | The project: the source the boards read, `lake`, through the dbt `lake` target |
| `profiles.yml` | The connection `dct` uses: the warehouse's `lake` target plus the secret's `scope` ([why](#known-limits)), attached read-only |
| `check_boards.py` | The gate: compares a render with gold, and fails where `dct render` does not |
| `tests/` | The gate's tests, and a real render of the CI fixture as their fixture |

## The flow

```
gold in the lake → dbt parse → dct validate → dct render → check_boards.py
```

1. **Gold** is what Dagster's `dbt build` made in the lake. A board reads only
   gold, through `{{ ref('fct_earthquakes') }}`: it filters and aggregates
   gold's columns and defines no business rule of its own (the classes, the
   region and the counts are gold's).
2. **`dbt parse`** writes `data/warehouse/target/manifest.json`, which `dct`
   resolves `ref()` against. It opens no connection.
3. **`dct validate --strict --warehouse`** checks the YAML and describes every
   query against the lake, so a column that does not exist fails here. `--strict`
   turns warnings into failures; without it, a lake that cannot be reached is
   only a warning.
4. **`dct render`** runs every query and writes the page (`--format html`). The
   same render with `--format data` writes the rows the page was drawn from, for
   the next step. `--no-cache` always: a cached result is not a render of this lake.
5. **`check_boards.py`** reads the rows and the lake and fails, with one line per
   problem, when:
   - any query on the board returned zero rows (`dct render` only warns and exits 0);
   - any column of any query the board draws differs from what gold holds over
     the same window, compared in the order the page draws it: the headline
     figures (earthquakes, largest magnitude, magnitude 4.0 or more, reviewed
     share, the two times in the first line), each bar of the daily chart by
     day and class, the class and depth charts, the top 15 regions, every
     circle on the map, and the 100 rows of the table;
   - the events per UTC day differ from `sum(event_count)` of
     `agg_earthquakes_daily_region`, the count of magnitude 4.0 or more from its
     `sum(count_magnitude_4_plus)`, or the top regions from its counts summed per
     region (a second path: the board reads `fct_earthquakes`);
   - a board has no comparison in `BOARD_CHECKS`, or its render lacks a query.

   Not compared: the table's constant `usgs_page` label, the board's static text,
   and how a chart draws its rows. The window is restated in the check, apart
   from the board's: the last 30 UTC days up to the latest earthquake in gold.
   The reviewed share is compared to 1e-9, because the board and the check divide
   in different types.

## In the cluster

The image `lagom-orchestration` carries `charts/`, `partials/`, `dbt_charts.yml`, `profiles.yml`
and `check_boards.py`, and `dct`. The job `dashboards` runs the same commands as
CI, against the cluster's lake:

1. `dct validate --strict --warehouse`, `dct render` (HTML and rows) and
   `check_boards.py`, as above. The lake is attached **read-only** (`read_only:
   true` in `profiles.yml`, and `attach_lake(read_only=True)` in the check) as the
   catalog role `lake_reader`, whose password is `PGPASSWORD` and not part of the
   connection string. The dbt manifest is the one the image's build wrote.
2. The **watermark**: the board's `style.timestamp` prints `Page built at <UTC
   time>` above the title. The step fails a page that has the marker zero or
   twice, or a time more than 15 minutes from now. If you change the format in a
   board, change `WATERMARK` in `dashboards.py`; a test keeps them together.
3. The **upload** of each page to the environment's bucket, `Content-Type:
   text/html; charset=utf-8` and `Cache-Control: public, max-age=300`, then the
   removal of `.html` objects that are no longer boards. A page is one file that
   loads nothing from outside, so nothing else is uploaded.

`dct` runs with the lake's variables and nothing else: not the bucket's token,
not the ping URL. Telemetry is on there (below); every command has a timeout.
What the step proves and what is still to verify in staging are in
[`runbook.md`](../../docs/runbook.md#the-dashboards-publishing-path).

## Run it locally

Everything runs in a container; nothing is installed on your machine. From the
repository root, with Docker running. The lake is built from the CI fixture
(14 hand-made rows, no real data) in the container's `/tmp`, and the rendered
files land in `/tmp/lagom-dashboards` on your machine:

```sh
mkdir -p /tmp/lagom-dashboards
docker run --rm --name lagom-dbtcharts-render -v "$PWD:/repo" -w /repo \
  -v /tmp/lagom-dashboards:/out \
  -e HOME=/tmp/home -e UV_PROJECT_ENVIRONMENT=/tmp/venv -e UV_LINK_MODE=copy \
  -e PYTHONDONTWRITEBYTECODE=1 -e DO_NOT_TRACK=1 \
  -e PYTHONPATH=data/dashboards:data/orchestration \
  -e LAGOM_LAKE_METADATA_SCHEMA=main -e LAGOM_R2_ACCESS_KEY_ID=local-placeholder \
  -e LAGOM_R2_SECRET_ACCESS_KEY=local-placeholder \
  -e LAGOM_R2_ENDPOINT=https://local-placeholder.invalid \
  ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -euc '
    export LAGOM_LAKE_CATALOG="ducklake:/tmp/lake/catalog.ducklake"
    export LAGOM_LAKE_DATA_PATH="/tmp/lake/data/"
    mkdir -p /tmp/home /tmp/lake
    u="uv run --project data/orchestration --locked --group dashboards"
    $u python data/warehouse/ci/load_fixture.py
    $u dbt build --target lake --no-partial-parse \
      --project-dir data/warehouse --profiles-dir data/warehouse
    $u python - <<PY
from lagom_orchestration.lake import attach_lake
attach_lake().execute("CALL ducklake_flush_inlined_data(\x27lake\x27)")
PY
    $u dbt parse --target lake \
      --project-dir data/warehouse --profiles-dir data/warehouse
    export TZ=Pacific/Auckland
    $u dct validate --strict --warehouse data/dashboards/charts \
      --project-dir data/dashboards
    for format in html data; do
      $u dct render data/dashboards/charts/*.yml --project-dir data/dashboards \
        --no-cache --ignore-warning WARN-CATEGORY-COLOR-PIN-UNSEEN --format $format \
        --output "/out/{stem}.$format"
    done
    $u python data/dashboards/check_boards.py /out/*.data
  '
```

It is the job `dashboards (render)` of [`ci.yml`](../../.github/workflows/ci.yml),
step for step; the CI job also lints, type-checks and tests `check_boards.py`
(with `ruff`, `mypy` and `pytest` from the orchestration project) and checks
that the page loads nothing from outside. The flush step writes gold out as
Parquet files: DuckLake keeps a table of up to 10 rows in its catalog, and the
fixture's daily aggregate has 10, so it would be read from the catalog and not
from files, as it is not in the cluster. `TZ=Pacific/Auckland` runs the validation, the
render and the check 13 hours from UTC. `dct` runs its queries in UTC whatever
the machine's zone, so this does not test the board's `at time zone 'UTC'`; it
tests that the check's own reading of gold does not depend on the zone.
`dbt` writes `target/` and `logs/` under `data/warehouse/`; both are ignored
by Git. `dct` writes nothing inside the repository when `--output` is given.

Open `/tmp/lagom-dashboards/earthquakes.html` in a browser. To look at a board
as a picture without a browser, render `--format png` the same way.

Against a real lake, set `LAGOM_LAKE_CATALOG`, `LAGOM_LAKE_DATA_PATH` and the
`LAGOM_R2_*` variables to its values instead (the table in
[`data/orchestration/README.md`](../orchestration/README.md)),
and skip the first three commands. The R2 case is tested only by the job's first
run in staging.

## Add a board

1. Write `charts/<name>.yml`. Set `id: <name>` and `source: lake`; read gold
   with `{{ ref('<model>') }}`; give every query a purpose in a comment. The
   YAML reference is `dct skills chart`, `dct examples`, and
   [docs.dbtcharts.com](https://docs.dbtcharts.com/).
2. Add its comparison to `BOARD_CHECKS` in `check_boards.py`, and tests that
   fail when it is wrong. A board with no comparison fails the check.
3. Run the flow above. Look at the PNG: the checks prove the numbers, not the
   look.

A file whose name starts with `_` is a fragment (a theme, a shared block), not
a board: `dct validate` does not treat it as one.

## What the gate proves, and what it does not

Proves, on the CI fixture: the boards are valid YAML for this version of `dct`;
every query runs against a DuckLake whose gold is in Parquet files; no query is
empty; every column of every query the board draws is what gold holds over the
same window; the edge of the 30-day window (the fixture has an earthquake a
second inside it and one a second outside); and the page is one file that loads
nothing from outside (no script, stylesheet, image or frame by address, no
`@import`, no `url()` to another host; a missing or empty page fails).

Does not prove:

- **Real data.** The fixture has 12 earthquakes, 11 of them in the window. A real window has thousands:
  how many bars, circles and rows a board can carry, and how long and how much
  memory a render takes, are known only from a render on real data. On the
  fixture, one render takes about 4 s and a peak of about 0.7 GB, and a window of
  3,500 synthetic events took the same (2026-11-10). The cluster budget
  ([`architecture.md` §11](../../docs/architecture.md#11-resource-budget)) has no
  line for it yet.
- **How the page looks** in a browser. CI draws no picture; the legend, the
  labels and the map are reviewed by eye.
- **Publishing.** CI uploads nothing and uses no secret. The upload is tested
  against botocore's stubber (parameters, order, failures), not against R2.
- **The R2 path.** The secret's scope is the lake's data path, which in the
  cluster is a bucket. Tested with a directory only.
- **A comparison that is not written.** The check covers what the earthquakes
  board draws; a new query or column is unchecked until its comparison is added.
- **The board's own `at time zone 'UTC'`.** `dct` runs queries in UTC (the board's
  `regional.time_zone`, UTC by default), so removing the conversions from the
  board changes nothing, even rendered in another zone, and nothing can fail.
  They stay as the board's statement of what it means.

## A static page: no filters

The page is one HTML file with the data in it. Its filter controls (date range,
region, class) do not filter in that form: they only render the state the page
was built with. A control that cannot filter must not be shown, so the boards
have none and no `variables:`. Every cut of the data is a chart of its own, and
a window is written in the SQL (the last 30 UTC days up to the latest earthquake
in gold, so a stalled pipeline shows its last 30 days, not an empty page).
Adding interactivity means another tool ([ADR-0040](../../docs/adr/0040-dbt-charts-for-the-dashboards.md)).

The default footer stamp, "Data as of <time>", is the render's clock, not the
data's. It is replaced by the watermark `Page built at <UTC time>`
(`style.timestamp`, at the top of the page), which says when the page was built
and, because the job checks it, that the page is not a stale one. The page's
first line gives the times of the data itself: the latest earthquake and the
latest USGS revision, in UTC. The footer's "made with dbt Charts" stays.

## Telemetry

`dct` sends anonymous usage statistics to dbt Labs unless `DO_NOT_TRACK=1` is
set. Two events per command: the command's name (never its arguments), the
`dbt-charts` and Python versions, the operating system's name, a hash of the dbt
project's name, a random user id kept in `~/.dbt/.user.yml`, and on the end event
the result and duration. Never board contents, queries, paths or credentials.
It is on by default and was seen to send two requests per command in the spike;
with `DO_NOT_TRACK=1` it sent none, and the user file is not created. The CI job
and the commands above set it, as the orchestration image does. The setting
belongs to the shell, not to the project: set it wherever `dct` runs.

**In the cluster it is on, by the operator's choice**
([ADR-0044](../../docs/adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md),
decision 9): the job starts `dct` without `DO_NOT_TRACK`, whatever the image sets
for the other tools, and gives every command a timeout, so a telemetry request
that never answers kills the command and fails the run with its name instead of
hanging it. Whether the node's network lets it through, and how long it takes,
is read from the first staging run; if it ever fails or stalls a render,
`DO_NOT_TRACK=1` goes back.

## Known limits

- **Beta.** `dbt-charts` is pre-1.0 and pinned to `0.11.1`
  (`data/orchestration/pyproject.toml`, group `dashboards`). Its syntax can
  change between versions; `dct migrate` rewrites older boards. It is developed
  in a private repository and mirrored; pull requests are not accepted.
- **`dct render` exits 0 when a query returns nothing.** It prints
  `WARN-QUERY-RETURNED-ZERO-ROWS` and writes an empty chart (a map with no rows
  is an error, exit 1). `check_boards.py` is what fails it. `dct validate`
  without `--warehouse` does not see a missing column; without `--strict`, a lake
  it cannot reach is only a warning.
- **The shared profile does not work on Parquet.** `dct` locks DuckDB's file
  access to the attached catalog and to the secrets' scopes. The warehouse's
  `lake` target has a secret with no scope, so reading a gold table that has
  Parquet files fails (`file system operations are disabled by configuration`),
  and `dct validate --warehouse` still passes. `profiles.yml` here adds the scope.
  Found when the first board met a lake bigger than the fixture; an earlier look
  at the fixture's 10-row gold, which DuckLake keeps in the catalog, had not
  shown it.
- **A failed attach prints the catalog's connection string,** password
  included. Seen in the spike. The cluster step has the defences of
  [ADR-0040](../../docs/adr/0040-dbt-charts-for-the-dashboards.md): the password is
  not in the string (`PGPASSWORD`; a failed attach against a real Postgres printed
  neither the right nor the wrong password, 2026-11-14), the role is read-only, the
  step's errors go through `redact_secrets`, and the password can be rotated (runbook).
  The check goes through `attach_lake`, which redacts.
- **Legends cannot be ordered on stacked bars and maps.** `style.legend.values`
  orders the legend of a plain bar chart and has no effect on a stacked one or
  on a map. A stacked bar's legend follows the stack, top to bottom: great first,
  with micro at the bottom of the bars. The map has no class colours and so no
  class legend; the size says the magnitude. The class and depth charts name
  each bar on the axis and have no legend.
- **The map's size legend cannot be hidden.** dct 0.11.1 draws it even with
  `legend.visible: false`. It is titled "Circle area", the column the circles
  are sized by, (magnitude - 3) squared, and the map's subtitle reads those
  values back into magnitudes.
- **A map circle's card shows its coordinates twice,** as the map's channels
  and as columns; dct 0.11.1 offers no way to leave them out of a map's card.
  They are rounded to a tenth of a degree, so they read short.
- **The site's look, not its fonts.** The board takes ofantini.com's paper, ink
  and accent colours. Its fonts (Archivo, IBM Plex Mono) are not embedded: a
  page that loads nothing can only use the fonts dct ships, so the board uses
  Inter and Source Code Pro, the nearest of them.
- **No zoom or pan on the map**, and none of the page's controls beyond the
  table's pages.
- **A class absent from the window** is absent from the legend, and `dct` warns
  that a colour is pinned for it; the render commands ignore
  `WARN-CATEGORY-COLOR-PIN-UNSEEN` for that reason. A new class in gold needs
  its colour added to the board.
- **DuckDB is not pinned by `dbt-charts`.** It works on the repository's 1.5.6;
  the orchestration project holds it below 2.
