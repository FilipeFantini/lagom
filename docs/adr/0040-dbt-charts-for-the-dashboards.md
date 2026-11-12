# ADR-0040 — The dashboards are written in dbt Charts, not Evidence

- **Status:** Accepted, by the operator's approval of the pull request that
  added it (2026-11-10); decision 3 and *Open, for the publishing pull
  request* amended by
  [ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md),
  proposed: the static page is the fallback, not the target
- **Date:** 2026-11-10
- **Decided by:** the operator, in conversation on 2026-11-10, after a spike
  of the same day: the tool is dbt Charts, and the page gives up in-page
  filters.
- **Amends:** [ADR-0022](0022-platform-as-a-portfolio-of-architectures.md),
  *The pieces* (the dashboards row: Evidence becomes dbt Charts) and
  *Consequences* (the exit it names, the Observable Framework, had no commit
  from May 2026 to 2026-11-10, when it was checked);
  [ADR-0023](0023-sme-stack-duckdb-on-ducklake.md), *What the spike must show*
  item 2 ("Evidence reads DuckLake") and the row that lists Evidence among the
  pieces left unchanged; [ADR-0039](0039-the-site-in-its-own-repository.md),
  decision 2, the tool only: the dashboards still live under `products/`, are
  built in the cluster after each run and are published to Cloudflare Pages.

## Context and problem

ADR-0022 chose Evidence for the dashboards: SQL and Markdown reviewed in pull
requests, static output, no server. ADR-0023 asked the spike to show that
Evidence reads DuckLake, with a Parquet export as the fallback. On 2026-11-10
Evidence was tried against the lake, and it did not hold:

- **Frozen releases.** Its npm release has been 40.1.8 since February 2026,
  although the repository is active.
- **Cannot read the lake as it is.** The DuckDB inside that release is 1.4.2,
  and it refuses a DuckLake 1.0 catalog. Forcing DuckDB 1.5.6 into it with an
  npm override read the fixture lake, but that is a combination Evidence does
  not test, and it was never tried against a Postgres catalog or R2.
- **A strict build misses errors** in the queries behind dropdowns.
- **The date-range control shifts a day** east of UTC.
- **The default basemap needs a key.**
- **Telemetry is on by default.**
- **`npm audit` reported 36 findings,** 7 of them critical, in Evidence's own
  dependency chain.
- **A Node toolchain beside a Python platform:** a second package manager, a
  second lock file and a second set of CI steps for one directory.

The need stands: a few public dashboards of public data, as code, reviewed in
pull requests, built after each run, published as static files, nothing
resident on the node.

## Requirements and constraints

- Boards are text in Git; the SQL is plain SQL over gold, so a change of tool
  does not lose it.
- Static output: no server, no database reachable from the internet
  ([ADR-0022](0022-platform-as-a-portfolio-of-architectures.md)).
- Reads the lake the pipeline already writes, through the warehouse's own dbt
  target, with no export step to keep in step.
- One toolchain: the platform's Python, locked by `uv`.
- A wrong number on the page must fail a build, not ship
  (protocol §16.2).
- No resident memory; an ephemeral job's memory is measured and budgeted
  before it runs in the cluster ([`architecture.md` §11](../architecture.md#11-resource-budget)).

## Alternatives considered

**Evidence with a Parquet export.** Dagster exports gold to Parquet and
Evidence reads the files. Works around the DuckDB version. Rejected: it
keeps the frozen release, the audit findings and the Node toolchain, and adds
a copy of gold that can fall behind it.

**Evidence reading the lake with the DuckDB override.** Rejected: a
combination Evidence does not test, never tried against a Postgres catalog or
R2, and it leaves everything else in the list above.

**Streamlit.** A resident Python server, with a public route into the cluster.
Rejected: it breaks ADR-0022's rule that nothing on the node serves the public
face, and a server is the larger attack surface.

**A hand-built page:** Astro, Observable Plot or Vega-Lite, and DuckDB-WASM.
Full control, and the only option here with in-page filters and zoom. Rejected
for now: it is a front-end project of its own, written and maintained by hand,
for one board.

**dbt Charts** (`dbt-charts`, command `dct`, Apache-2.0, from dbt Labs). Boards
as YAML beside the warehouse project, queries as SQL over dbt models through
`ref()`, rendered to one self-contained HTML file. Chosen.

## Decision

1. **The dashboards are dbt Charts boards** under `products/dashboards/`,
   rendered to static HTML. `dbt-charts` is pinned to `0.11.1` in a dependency
   group of the orchestration project (`dashboards`), not in its
   `dependencies`: the image installs with `--no-dev` and does not grow until
   the step that renders the boards exists.
2. **Boards read gold through `ref()`** on the warehouse's `lake` target,
   after `dbt parse` wrote the manifest, and define no business rule of their
   own. A profile of the project's own adds the secret's `scope`, which `dct`
   needs to read Parquet ([`products/dashboards/README.md`](../../products/dashboards/README.md)).
3. **The page is rendered again after each run.** The "dynamic" the operator
   wants is that the page follows the data. **In-page filters are given up**:
   a static page cannot filter, and the operator accepted that on 2026-11-10.
   Every cut of the data is a chart of its own, and a window is written in the
   SQL.
4. **CI renders the boards against a lake built from the warehouse's
   fixture,** and `check_boards.py` fails where `dct` does not: on a query with
   no rows, and on any column of any query the board draws that differs from
   gold over the same window (the constant label of the table is the one thing
   left out).
5. **Telemetry is off:** `DO_NOT_TRACK=1` wherever `dct` runs, as in the
   orchestration image.

## Consequences

**Positive.** One toolchain: Python, `uv`, one lock file. 18 packages were
added to the orchestration lock, none changed, and DuckDB stays at 1.5.6. The
board is YAML and SQL, reviewed like the models. The render reads the lake the
platform already writes. The page is one file with no request to any other
host: 0.95 MB for the CI fixture, 1.2 MB for a window of 3,500 events, one
inline script, nothing fetched.

**Negative, and what it costs.**

- **Beta, and pre-1.0 syntax.** The package calls itself beta; its syntax may
  change between versions and `dct migrate` rewrites boards. Development is in
  a private upstream mirrored read-only: issues are welcome, pull requests are
  not accepted, so a blocker cannot be fixed by us. The exact pin is the
  mitigation; an upgrade is its own pull request.
- **No filters, no search, no map zoom** on the static page.
- **`dct render` exits 0 when a query returns no rows.** `check_boards.py` is
  the answer, and it is only as complete as the comparisons written in it: a
  new query or column of a board is unchecked until its comparison is added.
- **Reading Parquet needs a scope on the secret.** `dct` closes DuckDB's file
  access to the attached catalog and the secrets' scopes. The warehouse's
  profile has a secret with none, so a gold table with Parquet files cannot be
  read, and `dct validate --warehouse` does not notice. Found while building
  the first board: the fixture's tables of up to 10 rows are kept by DuckLake in
  the catalog, not in files, and passed. CI now writes the fixture's gold out as
  files first. The scope is the lake's data path; **tested with a local
  directory, not with R2.**
- **A failed attach prints the catalog's connection string,** password
  included (seen on 2026-11-10, with DuckDB 1.5.6). The publishing pull
  request must answer it in depth, in this order; none of it is done here:
  1. **Keep the password out of what the tool can echo.** The catalog string
     given to the render step carries no password; libpq reads it from
     `PGPASSWORD` or a password file. *To be verified in staging:* the DuckDB
     Postgres extension uses libpq, but nobody has tested that DuckLake's attach
     honours `PGPASSWORD` here.
  2. **Least privilege.** The render step uses a read-only catalog role and a
     read-only R2 token, not the catalog owner's, which every run Pod holds
     today ([ADR-0033](0033-dagster-alerts-through-healthchecks.md), decision 5;
     the "Credentials per job" trigger in the [roadmap](../roadmap.md#trigger-table)).
     A leak then reads a public dataset's index; it cannot write it.
  3. **Redaction** (`lagom_orchestration.redact`) of anything logged or sent to
     the alarm, as the safety net only: pattern-based masking is not a primary
     control.
  4. **The credential can be rotated,** automated and on a schedule: the
     operator's requirement of 2026-11-10, an open item of
     [M3](../roadmap.md#m3--the-8-gb-node-and-dagster) with no mechanism chosen
     yet. The weakness exists today, before the dashboards, and is tracked there
     too.
- **Cost of a render:** about 0.7 GB at its peak (resident set) and 4 s on the
  fixture; the same for a window of 3,500 synthetic events. It is not measured
  on real data and not in the budget: it must be measured and budgeted before
  it runs in the cluster.
- **`dbt-charts` does not pin DuckDB.** It works on the repository's 1.5.6;
  on 2.0.0 it fails to read Parquet sources. The orchestration project keeps
  DuckDB below 2.
- **Telemetry is on by default** (two requests per command, to dbt Labs, with
  no board content). It is off with `DO_NOT_TRACK=1`, which the CI job sets;
  the cluster step must set it too.

## Risks, cost, and reversal

Cost: none beyond CI minutes and, once the cluster step exists, an ephemeral
job. No new secret now; the publishing step will need a Cloudflare token.

Reversal: the SQL of a board is plain SQL over gold, so another tool reads the
same tables, and nothing else in the platform depends on `dct`. Remove the
`dashboards` group and `products/dashboards/`.

**Reconsider when** a blocker has no workaround, or the page needs
interactivity. Then the choice is a hand-built static page with DuckDB-WASM,
or a server behind its own ADR.

## Open, for the publishing pull request

- The Cloudflare Pages project, and its hostname: `lagom.ofantini.com`, one
  path per board, by the rule of
  [ADR-0030](0030-operator-uis-behind-cloudflare-access.md): one hostname per
  area, one path per item.
- The scope of the Cloudflare token the step holds.
- The memory of the render step, measured on real data and added to the
  budget.
- The four defences against a password echoed on a failed attach, above, and
  `DO_NOT_TRACK=1` in the step's environment.

Sources checked on 2026-11-10: `dbt-charts` 0.11.1 on PyPI and
[docs.dbtcharts.com](https://docs.dbtcharts.com/), `dct --help` and
`dct skills`; the spike's logs, kept outside the repository.
