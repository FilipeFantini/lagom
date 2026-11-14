# ADR-0042 — The dashboards return to dbt Charts; the server of our own is stopped

- **Status:** Accepted, by the operator in conversation on 2026-11-14
- **Date:** 2026-11-14
- **Decided by:** the operator, in conversation on 2026-11-14, after two merged
  pull requests in the server's repository and a second look at what it adds.
- **Supersedes:** [ADR-0041](0041-a-serving-layer-in-postgres-for-the-dashboards.md),
  decisions 6 and 7 (a server of our own, and nothing published until it
  serves a board).
- **Amends:** ADR-0041, decisions 1, 3 and 8 (what must read `serving`, when
  the publish step is built, and whether `dct serve` may be tried again);
  [ADR-0040](0040-dbt-charts-for-the-dashboards.md), whose static page is the
  target again unless the spike of decision 3 says otherwise.

## Context and problem

ADR-0041 decided that the dashboards would be served by a tool of our own,
built in `FilipeFantini/enlightenviz`. By 2026-11-13 that repository had a
server that validates boards written in YAML, answers their queries from
Postgres read-only, and draws a page with filters: 3.7 MiB of resident memory
and a 5.9 MB image, measured in its own tests. It was never deployed.

On 2026-11-14 the operator asked what it adds to dbt Charts. The answer:

- **What a visitor sees is nearly the same thing:** a board declared in YAML
  over SQL, drawn as charts, checked in CI. dbt Charts also has what the
  server deferred, the link to dbt (`ref()`, the profile, the adapters).
- **The one thing it would do that dbt Charts does not is live data,** and that
  part was not built.
- **The scope was two projects in one.** The earthquakes board is batch and
  does not need a resident server. The streaming showcase
  ([M6](../roadmap.md#m6--streaming)) is a separate piece of work with its own
  pipeline.
- ADR-0041 already named the risk: "building a tool is the classic way to stop
  building the product."

**Checked on 2026-11-14** (the package's metadata on PyPI and the page of
`dct serve` in the documentation):

- `dbt-charts` 0.11.1 is Apache-2.0 and has a `postgresql` extra
  (`dbt-postgres`). ADR-0041's spike ran `dct serve` against the lake only, so
  the open connections it found were never measured against Postgres.
- The documentation calls `dct serve` the "local dashboard server", for live
  previews while editing: it listens on `localhost` by default and ships with
  debug pages on. The vendor's way to publish is its hosted platform, which
  connects to the warehouse; this platform does not hand its data to a hosted
  service.
- `dct serve` turns a URL's query parameters into a board's variables, and
  caches query results on disk by default. ADR-0041 says "it has no cache";
  that is corrected here, not there.

## Requirements and constraints

- The first board is public, with no login
  ([roadmap, M4](../roadmap.md#m4--ofantinicom)).
- Resident memory is measured before it is deployed, and inside the budget:
  562 MiB are free today
  ([`architecture.md` §11](../architecture.md#11-resource-budget)).
- Dashboards stay text in Git, checked in CI; a wrong number fails a build.
- The dbt standard is worth keeping: one way to declare a model, a source and a
  board.
- A resident public reader holds no privilege on the lake's catalog (ADR-0041).

## Alternatives considered

**Keep building the server.** Rejected: its only difference is live data, which
belongs to another showcase, and its maintenance would be ours alone.

**Fork dbt Charts.** Rejected: the fork inherits what made `dct serve` heavy
(Python, dbt-core and DuckDB in one 877 MiB image, 178 MiB at start), follows a
pre-1.0 upstream that changes quickly, and still has no model for live data. A
defect found in it is reported upstream.

**Lightdash, on the node.** Not measured. Rejected for the node on what it is:
a Node.js server with a Postgres database of its own, expected at 600 MiB to
1 GB ([`architecture.md` §9](../architecture.md#9-visualization)), no DuckDB
connection, and built for users who sign in. Tried locally instead
(decision 4).

**One tool for batch and streaming.** None found that is declarative, small and
safe to expose. Two tools, each used for what it is built for, is the outcome.

## Decision

1. **The server of our own is stopped.** `FilipeFantini/enlightenviz` stays
   public as it is. Lagom does not consume it and nothing deploys it.
2. **The batch dashboards are dbt Charts.** The target is ADR-0040's static
   page again: `dct render` after each run, published to Cloudflare Pages. The
   page has no in-page filters, as ADR-0040 accepted.
3. **One spike before the publishing pull request** compares the static page
   with `dct serve` behind Cloudflare Access, reading Postgres through the
   `postgresql` extra. It measures open connections and memory after 1,000
   page views, lists the routes the server answers, and states what Access
   costs: a board behind Access is not public, which M4 requires of the first
   board. `dct serve` stays undeployed (ADR-0041, decision 8) unless that
   spike's result is accepted by the operator in a new ADR.
4. **Lightdash is tried locally,** in containers on the operator's machine,
   against a Postgres with the gold tables, to learn the tool and to measure
   its memory. It is not a candidate for the node unless that measurement fits
   the budget. The result is written where the spike of decision 3 is written.
5. **`serving` is for a resident reader.** ADR-0041's decisions 1, 2 and 5
   hold for any server that answers visitors from the node. A static page
   rendered inside the run is not such a reader: it reads gold as ADR-0040
   describes. The database, its roles and the publish step (ADR-0041,
   decision 3) are built when a resident reader is, not before.
6. **Streaming gets a tool of its own.** Grafana, already named in
   `architecture.md` §9, is the candidate; the showcase's ADR decides, as
   ADR-0041's decision 4 already says for the events' destination.

## Consequences

**Positive.**

- No tool of our own to maintain, and no deadline spent on one.
- The boards stay in the dbt standard, in the tool dbt Labs publishes.
- With the static page, nothing resident is added to the node and no public
  reader exists at all.

**Negative, and what it costs.**

- **No in-page filters,** unless the spike of decision 3 changes the path.
  This is the limit ADR-0040 accepted and ADR-0041 tried to remove.
- **The fourth change to the dashboards in two weeks:** Evidence, the static
  page, a server of our own, and the static page again. Nothing is public yet.
- **Two visualization tools to learn and run,** one for batch and one for
  streaming.
- **The work in the server's repository is not used.** What it showed stays
  true and reusable: a read-only server over Postgres can stay under 4 MiB.

## Risks, cost, and reversal

Cost: none on the node. The render step's memory is measured and budgeted in
the publishing pull request, as ADR-0040 requires.

Reversal: the server's repository is intact, with its design document and
tests. Resuming it takes a new ADR that supersedes this one.

**Reconsider when** the streaming showcase needs a public page that neither
Grafana nor a static page can give.

Sources checked on 2026-11-14: the metadata of `dbt-charts` 0.11.1 on PyPI;
`docs.dbtcharts.com/cli/serve`. Lightdash was not installed or measured; what
is said of it above is from its documentation as remembered, and decision 4
replaces that with a measurement.
