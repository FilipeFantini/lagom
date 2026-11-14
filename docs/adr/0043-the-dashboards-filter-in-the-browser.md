# ADR-0043 — The dashboards filter in the visitor's browser: a static page drawn with Vega-Lite

- **Status:** Rejected, by the operator in conversation on 2026-11-14. The
  batch dashboards stay ADR-0040's static page drawn by `dct render`
  (ADR-0042, decision 2), designed with no filters; its hover tooltips are
  enough. The spike's measurements below stay true and are the record of
  why `dct serve` is not deployed. The section *Corrects* still holds.
- **Date:** 2026-11-14
- **Proposed by:** the architect, after the spike of ADR-0042's decisions 3
  and 4, and a fourth option the operator asked to prototype in the same spike.
- **Would amend:** [ADR-0040](0040-dbt-charts-for-the-dashboards.md), decisions
  1 and 3 (the tool that draws the page, and "in-page filters are given up");
  [ADR-0042](0042-the-dashboards-return-to-dbt-charts.md), decision 2 (the
  page is drawn by `dct render`).
- **Corrects:** ADR-0042, *Alternatives considered*, on Lightdash: it has a
  DuckDB and DuckLake connection, and its memory is measured below. Corrected
  here, not there.

## Context and problem

ADR-0042 made `dct render`'s static page the target again and asked one spike
to compare it with `dct serve` behind Cloudflare Access, and to try Lightdash
locally. During the spike the operator set three requirements for any board,
not only the earthquakes one:

1. **SQL injection must be impossible by construction,** not prevented by an
   escape function.
2. **No cache may grow without a limit.**
3. **The page must be interactive.** A page with no filters is a weak
   visualization layer; charts that filter in the browser are common today.

Realistic traffic is a few dozen simultaneous visitors.

## The spike (2026-11-14)

Local only, in containers on the operator's machine, removed afterwards.
Disposable Postgres 18.6 with `max_connections=25`, holding the gold tables of
the CI fixture (12 earthquakes, 11 in the window) copied from the fixture lake,
read through a read-only role. Every board drawn in the spike matched gold:
the Postgres render matched the lake render on all 7 queries, and Lightdash and
the prototype matched Part 1's rows. The scripts and raw logs are kept outside
the repository; each figure below names how it was taken.

### `dct serve` 0.11.1 on Postgres

`dbt-charts` 0.11.1 with `dbt-postgres` 1.11.0 added to the locked
environment; 1 GiB container limit. Sampled every 100 page views from
`/proc/<pid>/status` and `pg_stat_activity`; a page view is the HTML, its four
font files and its live-reload stream, read from the page's template.

| Configuration | Postgres connections | RSS at start → after 1,000 views |
|---|---|---|
| cache on, default workers | 7, flat | 243 → 425 MiB |
| cache on, `--max-workers 1` | 1, flat | 246 → 394 MiB |
| cache off, default workers | 7, flat | 238 → 435 MiB |
| cache off, `--max-workers 1` | 1, flat | 238 → 386 MiB |

- **No connection leak on Postgres:** one persistent connection per worker.
  ADR-0041's leak was in DuckDB's lake attach.
- **URL parameters work as filters,** and reach SQL as text: the value is
  rendered into the statement as a literal with its quotes doubled
  (`core/dialects/base.py:373`); 0.11.1 has no bound-parameter path for
  Postgres. Ten breaking values stayed inside their literal, under
  `log_statement=all`.
- **Allowed values are not enforced:** `serve` passes `strict_variables=False`
  (`core/serve/server.py:536`), so a value outside a variable's options, up to
  100 KB, reaches SQL.
- **The role's `statement_timeout` does not hold:** `dct` sends
  `SET statement_timeout = '120s'` on every connection
  (`core/dialects/postgres.py:51`; the 120 s default from
  `core/compile/config.py`, seen in the Postgres log). Only the role's connection limit is a
  ceiling; at the limit every view is an error page, and the next view after
  the limit is lifted succeeds.
- **The cache is keyed by the URL's values, has no size limit, and serves
  stale numbers:** 301 distinct values made 601 entries; after one gold row
  changed the page kept the old figure (TTL 24 h by default). `--no-cache`
  writes nothing to disk.
- **Exposed with `server.debug` and `server.nav` off:** a directory listing at
  `/`, the board's YAML with its SQL, `/health` with the process id and project
  path, `/docs` and `/openapi.json`, and a full traceback on an internal error.
  With a native `postgres` source, `/data/*` and `/inspector/*` list every
  schema the role can read and return its rows; no setting turns them off.
- `dct validate --strict --warehouse` fails on Postgres (no result schema from
  `EXPLAIN`), so the CI gate would not pass as written.

### Behind Cloudflare Access

Read from [ADR-0030](0030-operator-uis-behind-cloudflare-access.md); nothing
was created in Cloudflare. Access is real authentication at the edge, and
`cloudflared` refuses a request without its signed token, failing closed. It
decides **who reaches** the server. It does not change what the server does
for whoever passes: the escape-only filters, the stale cache, the `/data` and
`/inspector` routes and about 400 MiB resident are the same for a logged-in
visitor. And the board stops being public, which
[M4](../roadmap.md#m4--ofantinicom) requires of the first board. A policy that
lets any e-mail in puts a login step before every visitor and protects nothing.

### Lightdash 3.116.0, locally

The tag's own compose; telemetry off (`RUDDERSTACK_ANALYTICS_DISABLED`,
`LIGHTDASH_DISABLE_UPDATE_CHECK`). The board was rebuilt as code (`lightdash
upload`) over a minimal dbt project: 10 charts, all matching dbt Charts row for
row. Memory by `docker stats` and `/proc` RSS.

- **The image is amd64 only;** the node is arm64. It ran emulated, so its
  memory here is indicative.
- **Memory:** about 1.1 GiB at rest (app, its own Postgres, MinIO), 2,054 MiB
  after 300 dashboard views, 1.1 GiB again three minutes later. The headless
  browser, needed only for exports, adds 563 MiB. Images: 14.4 GiB for the app.
- **Connections:** one per query, closed after it; 7 at most with 8 viewers.
- **A login is required** for every dashboard call (401 without a session).
  Public and embedded dashboards and the results cache are in
  `packages/backend/src/ee`, under a commercial licence.
- **As code:** metrics in dbt YAML, charts and dashboards as YAML through
  `lightdash upload`/`download`. An upload silently overwrites an edit made in
  the UI.
- **Reads DuckDB and DuckLake** (a local DuckLake was read; a Postgres catalog
  with R2 was not tried).
- **Filters reach SQL as escaped text,** with a stronger escape than `dct`'s
  (comments and NUL removed), which alters some values silently (`Ala--ska`
  became `Ala`). The SQL runner gives arbitrary SQL to developers and admins,
  and cannot be turned off.

### The fourth option: a static page that filters in the browser

A prototype: one HTML file with Vega 6.2.0, Vega-Lite 6.4.3 and the world-atlas
110m map inlined (pinned, SHA-256 recorded), the board as a 119-line Vega-Lite
specification, 8 lines of page JavaScript, and the window's event rows from
gold, read with the board's own `base` SQL through `attach_lake`.

- **Filters in the browser:** a minimum-magnitude slider, a region list, the
  legend's classes and a date-range brush on the daily chart; each filters the
  KPIs and every chart. Hover tooltips. No server and no SQL at view time.
- **Numbers:** 40 checks of Vega's own datasets (run headless in Node with the
  page's libraries) against Part 1's data and the lake, default and filtered
  state, all passed. PNGs drawn by `vl-convert`, with no browser.
- **Size:** 884 KB (298 KB gzip) on the fixture, of which 871 KB is libraries
  and map; 13 MB (1.4 MB gzip) for a synthetic window of 30,000 events, about
  406 B a row as JSON objects.
- **The data step:** 0.9 s, 266 MiB peak for the container, on the fixture.
- **The CI gate's regex** passes; with `vega-embed` included it would not
  (its tooltip library holds the literal `<img src=`), so it was left out.
- **Not done or not measured:** the table of the largest earthquakes (needs
  page code to follow the filters), behaviour in a real browser (none may be
  opened in the spike), and real mouse interaction.
- **Reference only:** DuckDB-WASM 1.32.0 would add 34 MB (7.6 MB gzip) for SQL
  in the browser, for boards too large for rows in JSON.

## Comparison

| Criterion | `dct render` page | `dct serve` behind Access | Lightdash | Page with browser filters |
|---|---|---|---|---|
| Resident memory (562 MiB free) | 0 | 386–435 MiB | ~1.1 GiB at rest, ~2 GiB in use (emulated) | 0 |
| Postgres connections | 0 at view time | 1 or 7, held | 1 per query, closed | 0 at view time |
| Filters on the page | none | yes, by URL | yes | yes, in the browser |
| Public, no login | yes | no | no (embed is commercial) | yes |
| Board as code | YAML | YAML | dbt YAML + chart YAML; UI edits overwritten | Vega-Lite spec |
| dbt integration | `ref()`, profile | `ref()`, profile | dbt project, metrics in YAML | the data step reads gold; no `ref()` |
| SQL injection | none at view time | escaped text | escaped text; SQL runner | none at view time |
| Cache | none | unbounded, stale | commercial only | none (CDN) |
| Exposed surface | static files on a CDN | a server, debug routes, `/data` | a server, its database, MinIO | static files on a CDN |

## Decision (proposed)

1. **The batch dashboards become static pages that filter in the visitor's
   browser,** drawn with Vega-Lite from a specification kept in Git beside the
   board's SQL. The page is still rendered inside the run and published to
   Cloudflare Pages: ADR-0040's path, ADR-0022's static public face, nothing
   resident on the node.
2. **No SQL runs at view time.** A board ships rows its SQL selected from gold;
   a visitor filters those rows and nothing else. This is the property that
   holds for every future board, whatever its data.
3. **A board's data has a ceiling.** Rows as JSON fit up to tens of thousands
   of events (1.4 MB gzip at 30,000); a board above that ships aggregates or a
   columnar file, and DuckDB-WASM is weighed then, in its own ADR.
4. **The gate follows the tool.** CI computes the page's datasets with the
   page's own pinned libraries and compares them with gold, as
   `check_boards.py` does today, and keeps the check that the page loads
   nothing from another host.
5. **`dct serve` is not deployed,** with or without Access, for the reasons
   measured above. ADR-0041's decision 8 stands.
6. **Lightdash enters by its existing trigger** ("a client needs ad-hoc,
   self-service exploration, behind login", [roadmap](../roadmap.md#trigger-table)),
   never as the public face, and only on a node or budget of its own: it does
   not fit 562 MiB and its image does not run natively on arm64.
7. **`dbt-charts` stays** until the first Vega-Lite board and its gate replace
   the earthquakes board; then the `dashboards` group and the dct project are
   removed in that pull request.

## Consequences

**Positive.** Filters on a public page with no server, no database reachable
from a visit, and no cache of ours. Injection is not blocked; there is nothing
to inject into. A second board is a new specification and a new query; the
page code does not change. Vega-Lite is a stable, BSD-licensed grammar with
releases since 2016, not a beta tool.

**Negative, and what it costs.**

- **Leaves the dbt standard for drawing.** `dct` resolves `ref()` and reads
  the profile; the data step must do that itself (here, the board's SQL with
  the gold table named).
- **Ships rows, not aggregates.** Anything in a board's rows is public; a
  board must select only columns meant to be shown.
- **The fifth change to the dashboards in two weeks,** after Evidence, the
  static page, a server of our own, and the static page again.
- **The page is ours to assemble:** a build script (about 120 lines in the
  prototype) and a few lines of page code, small but maintained by us.
- **Not proven in a browser.** The first board must be looked at by eye
  before it is published, as ADR-0040 already says of the look.

## Risks, cost, and reversal

Cost: none on the node; the render step's memory is measured and budgeted in
the publishing pull request, as ADR-0040 requires.

Reversal: the board's SQL is plain SQL over gold; returning to `dct render`
means restoring its YAML, which stays in Git history.

**Reconsider when** a board's rows outgrow the page, or a board needs data a
visitor must not download in full.

## Open, for the operator

- Accept, change or reject this proposal. If rejected, the next pull request
  is ADR-0040's publishing pull request, as ADR-0042 planned.
- If accepted, the next pull request replaces the earthquakes board with a
  Vega-Lite board under `products/dashboards/`, with its gate in CI; the
  publishing pull request follows it.

Sources checked on 2026-11-14: the installed `dbt-charts` 0.11.1 package
(paths above are inside it); Lightdash's repository at tag 3.116.0 and its
licence files; the npm metadata of vega, vega-lite, world-atlas and
@duckdb/duckdb-wasm; the spike's logs, kept outside the repository.
