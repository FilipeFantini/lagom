# data/

The sources, warehouse models, orchestration definitions and reports.
[Data](../docs/data.md) explains their current flow and how to add a source.
The [platform](../infra/README.md) deploys and runs them.

| Subdirectory | Contents |
|---|---|
| [sources/](sources/README.md) | dlt source projects and assets |
| [warehouse/](warehouse/README.md) | Shared dbt models, contracts, tests and profiles |
| [orchestration/](orchestration/README.md) | Dagster jobs, schedules, sensors and orchestration image |
| [dashboards/](dashboards/README.md) | dbt Charts boards, render validation and reconciliation |

The shared dbt project keeps source models under `models/staging/<source>/`
and modelled outputs under `models/marts/<domain>/`. Each source's position
in this layout is defined in
[ADR-0053](../docs/adr/0053-repository-layout-infra-and-data.md).
