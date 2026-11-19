# ADR-0055 — Superset 6.1.0 is rejected for Lagom's dashboards

- **Status:** Rejected
- **Date:** 2026-11-19
- **Spike:** [#137](https://github.com/FilipeFantini/lagom/issues/137)

## Context and problem

Superset could give the operator interactive exploration of the earthquakes
gold tables. The prototype used one Superset web worker with DuckDB in the same
process. DuckDB reads DuckLake, which keeps Parquet files in R2 and its catalog
in Postgres. Superset keeps its own metadata in a separate database on the same
Postgres instance. Cloudflare Access supplies the signed login token. The
public site would continue to depend only on static assets.

## Requirements and constraints

The spike required a single instance within the measured memory budget, Git
YAML imports on deploy, Cloudflare Access and read-only lake credentials with
bounded queries, an agent-authored earthquakes dashboard, and a screenshot
published after a batch run. The
[engineering protocol](../engineering-protocol.md#8-metrics-and-quality-gates)
also requires a formal exception for new high or critical security findings.

## Alternatives considered

- **Deploy the pinned Superset 6.1.0 image.** The fixture showed that
  in-process DuckDB can serve the dashboard within the proposed memory limit.
  The image introduces dependencies with unresolved security findings.
- **Patch the stable image.** Some individual dependencies can be upgraded
  within the upstream constraints. The scan also found fixes outside those
  constraints. Superset 6.1.0 declares `msgpack<1.1` and `pyarrow<19`.
  Two reported fixes require [msgpack 1.2.1](https://github.com/advisories/GHSA-6v7p-g79w-8964)
  and [pyarrow 23.0.1](https://github.com/advisories/GHSA-rgxp-2hwp-jwgg).
  Taking ownership of a divergent application build would require maintaining
  and retesting its dependency set.
- **Keep dbt Charts.** The existing batch job reconciles and publishes static
  reports. It gives up interactive queries and adds no resident service.

## Decision

Reject the tested Superset build for deployment. Keep the publishing path
defined in [ADR-0042](0042-the-dashboards-return-to-dbt-charts.md) and
[ADR-0044](0044-per-job-credentials-and-the-dashboards-publishing-path.md).

Grype 0.122.1 scanned the local ARM64 prototype and its official base,
`apache/superset:6.1.0@sha256:16b50bbef6648912a79e3293d418fefa743ce555d36c5af6b85d859119ed7f88`.
Both scans reported 49 critical and 474 high matches after deduplication by
severity, package, package version and advisory ID. The sets were identical.
These are scanner matches. An example confirmed against the installed package
and the published advisory is PyJWT 2.10.1, used by the Access token validator,
with a [critical advisory fixed in 2.14.0](https://github.com/advisories/GHSA-ffc3-869f-jxw9).
The registry's stable 6.1.0 tag resolved to the tested digest on the spike date.

## Consequences

The [local fixture measurements](https://github.com/FilipeFantini/lagom/issues/149#issuecomment-7017433099)
support further investigation of the design:

| Spike criterion | Evidence and remaining gap |
|---|---|
| Memory and Postgres | One worker without Celery or Redis. About 235 MiB idle, 489 MiB peak on the fixture after 50 dashboard loads, and 560 MiB peak at 500,000 events. Postgres 18.6 stored the metadata. The checked platform had 898 MiB of memory requests available. Staging memory remains unmeasured. |
| YAML from Git | Three imports preserved asset counts and IDs. A change in YAML took effect on the next import. The deploy hook remains untested. |
| Access and read-only queries | A local signing-key server stood in for Cloudflare Access. The local catalog received a 30 s Postgres `statement_timeout`. DuckDB interrupted a slow query after about 10 s. Cloudflare Access and the authenticated path prefix remain unverified on a deployed instance. |
| Earthquakes dashboard | The agent-authored dashboard reconciled all nine charts with fixture gold. Reconciliation on real gold remains unverified. |
| Static publication | The screenshot and publication were not exercised. |

## Risks, cost, and reversal

The existing report has no interactive BI service. Revisit the spike when a
candidate image passes the security gate. Use staging to measure memory and
verify Access, the YAML deploy hook, real gold and batch screenshot publication
before considering promotion.
