# data/

Everything about the data itself: where it comes from, how it is modelled,
which jobs run it, and what a consumer sees. The operator leads here; the
cluster that runs it is in [`infra/`](../infra/README.md).

| Subdirectory | Contents | Born in |
|---|---|---|
| [`sources/`](sources/README.md) | one directory per data source: ingestion (dlt), its Dagster assets, its tests | M2 |
| [`warehouse/`](warehouse/README.md) | the single dbt project: silver and gold models, tests, and the profiles | M2 |
| [`orchestration/`](orchestration/README.md) | the Dagster code location: jobs, schedules, sensors, and the image that bundles the code. Dagster's deployment values stay in `infra/dagster-*` | M3 |
| [`dashboards/`](dashboards/README.md) | dbt Charts boards, rendered to static HTML; the project and its CI check exist, publishing does not yet ([ADR-0040](../docs/adr/0040-dbt-charts-for-the-dashboards.md)) | M4 |

`warehouse/` holds one dbt project rather than one per source, because dbt-core
has no cross-project `ref()`. Models are laid out as
`models/staging/<source>/`, mirroring the source directory name, and
`models/marts/<domain>/` for the modelled layer. That naming is the only thing
connecting a source to its models — see
[ADR-0053](../docs/adr/0053-repository-layout-infra-and-data.md).

The dashboards read from the gold layer, which is in the lake
([ADR-0026](../docs/adr/0026-dagster-one-instance-per-environment.md)); the
Postgres gold is no longer updated
([ADR-0033](../docs/adr/0033-dagster-alerts-through-healthchecks.md)). They never
reach into a source's internals.

The dashboards are one form of what a consumer sees; the directory is
deliberately agnostic about form. Whatever lands here must be reviewable as
text — see [ADR-0053](../docs/adr/0053-repository-layout-infra-and-data.md) and
[`../.gitattributes`](../.gitattributes).
