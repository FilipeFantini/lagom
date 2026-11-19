# Lagom

Lagom is an open-source batch data platform for running real analytics on a
single small server. Jobs ingest public data, build and reconcile warehouse
models, and publish static reports. The first project follows earthquakes
reported by the United States Geological Survey.

The shared platform handles deployment, scheduling and recovery. Each data
project adds its own source, models and outputs. The [public portfolio](https://ofantini.com)
presents the projects and the infrastructure behind them.

## Repository layout

| Path | What it holds | Who leads |
|---|---|---|
| [`infra/`](infra/README.md) | Cluster manifests, Argo CD applications and chart values | Agents lead infrastructure and explain the mechanism |
| [`data/`](data/README.md) | Sources, warehouse models, orchestration code and report definitions | The operator leads data decisions, with options and trade-offs from agents |
| [`tools/`](tools/README.md) | Checks used in continuous integration (CI) and operational scripts | |
| [`docs/`](docs/platform.md) | Current design, operating procedures and decisions | |

See [Platform](docs/platform.md) for deployment, access and the memory
ceiling, and [Data](docs/data.md) for how a source becomes a report. The
[decision records](docs/adr/README.md) explain why the system is built this
way. The [GitHub Project](https://github.com/users/FilipeFantini/projects/2)
tracks work in progress.

## Start here

- To explore a source locally, start with the [USGS source](data/sources/usgs/README.md)
  and the [warehouse](data/warehouse/README.md). Their tests can run against
  fixtures.
- To provision or operate the cluster, follow the [runbook](docs/runbook.md).
- To make a change, start with the [contribution workflow](CONTRIBUTING.md)
  and the [repository rules](AGENTS.md).

## License

[MIT](LICENSE).
