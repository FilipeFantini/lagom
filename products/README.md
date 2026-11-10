# products/

One directory per thing a consumer sees. Products change often and affect one
audience each.

| Product | Form | Born in |
|---|---|---|
| [`dashboards/`](dashboards/README.md) | dbt Charts boards, rendered to static HTML; the project and its CI check exist, publishing does not yet ([ADR-0040](../docs/adr/0040-dbt-charts-for-the-dashboards.md)) | M4 |

Planned:

| Product | Form | Born in |
|---|---|---|
| the globe | Streamlit with pydeck | M6 |

A product reads from the gold layer, which is in the lake
([ADR-0026](../docs/adr/0026-dagster-one-instance-per-environment.md)); the
Postgres gold is no longer updated
([ADR-0033](../docs/adr/0033-dagster-alerts-through-healthchecks.md)). It never
reaches into a service's internals.

This directory is deliberately agnostic about form: a report, an app, a notebook,
or a BI project are all products. Whatever lands here must be reviewable as
text — see [ADR-0006](../docs/adr/0006-monorepo-layout.md) and
[`../.gitattributes`](../.gitattributes).
