# data/sources/

One directory per data source being ingested. A source changes often and should
affect only itself.

```
data/sources/<name>/
  ingestion/     dlt pipeline config
  assets/        Dagster assets for this source
  tests/         fixtures and tests — never calls the live source
  README.md      what it is, its source, its freshness threshold
```

**The test this layout must keep passing:** adding a source creates one
directory here and one under `data/warehouse/models/`, and changes nothing
else. If a new source ever requires touching more, the layout needs revisiting
with a new ADR ([ADR-0053](../../docs/adr/0053-repository-layout-infra-and-data.md)).

Checklist for adding one: [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md).

First source: [`usgs`](usgs/) (earthquakes), in M2. Its `assets/` arrived
with Dagster in M3 and are loaded by the platform's code location
([`data/orchestration/`](../orchestration/README.md)), which also
type-checks them, since they import Dagster.
