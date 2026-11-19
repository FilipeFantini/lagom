# ADR-0053 — Repository layout: `infra/` and `data/`

- **Status:** Accepted, decided by the operator in conversation on 2026-11-19
  (final: the cost of moving paths, Argo CD included, is accepted).
- **Date:** 2026-11-19
- **Supersedes:** [ADR-0006](0006-monorepo-layout.md) (the three top-level
  directories `platform/`, `services/` and `products/`). Its reasoning about
  dbt staying one project, about a layout that is reviewable as text, and about
  directories created only when they have content still holds and is restated
  below.

## Context and problem

ADR-0006 divided the repository by blast radius and change frequency:
`platform/` (the shared backbone), `services/` (workloads) and `products/` (what
a consumer sees). In practice the line that matters to the people working here
is a different one. The AI agents lead the **infrastructure**: the node, Argo
CD, the manifests, the Helm values. The operator, a data professional, leads the
**data**: what is ingested, how it is modelled, which jobs run it, and what is
shown. `platform/` mixed the two (`platform/infra/` beside `platform/warehouse/`
and `platform/orchestration/`), so a reader could not tell from a path who
decides what, and the operator, who is newest to infrastructure, could not see
at a glance which half of the tree is theirs.

Prod is a portfolio, not a business-critical system, so the operator accepted the
cost of a move that rewrites paths in CI, in the image build and in Argo CD, in
exchange for a layout that is easy to understand.

## Requirements and constraints

- The tree shows at a glance what is infrastructure and what is data.
- Adding a source must not require reorganizing anything (ADR-0006's test,
  rewritten below).
- dbt-core has no cross-project `ref()`, so the transformation layer stays one
  project.
- Argo CD watches paths, so the infrastructure boundary stays one directory, and
  nothing outside it can change the cluster.
- No empty directories: a directory is created when it has content
  ([`../engineering-protocol.md`](../engineering-protocol.md) §2.2).
- What lands in the repository stays reviewable as text.
- The move must not stop Argo CD from reconciling itself (see *Decision*, the
  Argo CD transition).

## Alternatives considered

**Keep ADR-0006's layout.** No cost, and the blast-radius argument is real.
Rejected because it answers a question nobody asks daily (how often does this
change?) and hides the one they do (whose decision is this?).

**Three directories by pipeline stage** (`ingest/`, `transform/`, `serve/`).
Rejected: it groups by tool and stage, which is what ADR-0006 rejected as the
initial sketch, and it splits a source across directories again.

**Move only `platform/infra/` to `infra/`.** Smaller, but it leaves
`platform/warehouse/` and `platform/orchestration/` in a directory named for a
concept that no longer exists, and `services/` and `products/` do not say they
are the data side.

## Decision

Two top-level directories for the two kinds of work, beside `tools/` and
`docs/`:

| Before | After |
|---|---|
| `platform/infra/` | `infra/` |
| `services/usgs/` | `data/sources/usgs/` |
| `platform/warehouse/` | `data/warehouse/` |
| `platform/orchestration/` | `data/orchestration/` (the Dagster code: assets, jobs, schedules; Dagster's deployment values stay in `infra/dagster-*`) |
| `products/dashboards/` | `data/dashboards/` |
| `tools/`, `docs/`, `.github/`, `.claude/` | unchanged |

`platform/`, `services/` and `products/` no longer exist. Python package and
module names (`lagom_orchestration`) and the image name
(`lagom-orchestration`) are unchanged, so the Dagster code location's module
path in the Helm values does not change. The only import that changes is the one
that names the source's directory: `services.usgs.assets` becomes
`data.sources.usgs.assets`.

```
infra/               k8s manifests, Argo CD applications, Helm values
  apps/              one Argo CD Application per component; root watches this
data/
  sources/<source>/  ingestion (dlt), assets (Dagster), tests, README
  warehouse/         the single dbt project; models/staging/<source>/, models/marts/<domain>/
  orchestration/     the Dagster code location and its image
  dashboards/        dbt Charts boards, rendered to static HTML
tools/               development and operational scripts
docs/                architecture, protocol, ADRs, runbook
```

**The test this layout must pass:** adding a source creates one directory under
`data/sources/` and one directory of dbt models under `data/warehouse/models/`,
and changes nothing else. dbt stays a single project in `data/warehouse/`, with
`models/staging/<source>/` mirroring the source directory name; that naming is
the only thing connecting a source to its models.

**Argo CD watches `infra/` only.** Nothing outside that path can change the
cluster. `.github/CODEOWNERS` guards `/infra/` in place of `/platform/infra/`.

**What `data/` may hold stays form-agnostic**, as ADR-0006 required: a report,
an app or a BI project can live there, provided it is reviewable as text
(`.gitattributes` marks the known binaries). Directories are a convention, not
scaffolding; each is created when it has content.

### The Argo CD transition: two changes, in this order

The `root` Application watches `platform/infra/apps` and manages itself: it
creates every Application there, including its own. If one commit moved the
directory and changed root's `path` together, Argo CD would read the new
revision *at the old path*, find nothing, and stop with a comparison error. Root
would never update itself, and only a manual `kubectl apply` could recover it,
which agents may not run. So the move is two pull requests:

1. **Root reads `infra/apps/`.** Every Application file is copied to
   `infra/apps/`, byte for byte the same, with root's `path` set to `infra/apps`
   in both copies. The children still point at their `platform/infra/...`
   directories. Both directories declare the same Applications by name, so
   whichever one root reads, the cluster is the same; root re-points itself
   without a gap. A test failed the build if the two copies differed, and
   `tools/` kept reading the old directory only, so the resource budget was not
   counted twice.
2. **The move.** Once root reads `infra/apps/`, everything moves to the target
   layout, the old `platform/infra/apps/` copy and the drift test are deleted,
   and every child Application points at its new `infra/...` path. The Helm
   values files are byte-identical, so every rendered manifest is identical and
   no Pod restarts.

### CI, build and checks

Every path in `.github/workflows/`, the image's build context and `COPY` lines,
the `.dockerignore`, the `tools/` checks and their tests, the `uv` project
paths, the dbt project and profile paths, the dashboards' `dbt_charts.yml` and
the documentation moved with the code. The CI job `services (lint, types,
tests)` keeps its name: it is a required check in the repository ruleset, and a
renamed job waits for a check that never reports until the ruleset is renamed
too (CONTRIBUTING, *Branch protection*). It now checks `data/sources/`.

`tools/promotion_check.py` decides whether staging pinned a digest before prod by
searching Git history under staging's paths, and a path-limited `git log -S` does
not follow a rename. A digest staging pinned before this move is in history
only under `platform/infra/`, so the check also searches the old path for every
path under `infra/`. Without that, the move itself would fail the check for the
digests prod pins today.

## Consequences

**Positive.** The tree says who decides what. A source is additive in the two
places the test names. The infrastructure boundary is still one directory, now
at the top.

**Negative.**

- **Every path in a history of dated records changes.** Old ADRs keep the paths
  they were written with; links to moved files inside them are broken by
  history and are not edited. The ADR index works.
- **The image is rebuilt from the new paths.** The `images` workflow triggers on
  `data/**` and on itself, so the merge publishes a new `lagom-orchestration`
  digest whose code is the same but whose contents sit under `/app/data/...`.
  The running image is self-contained and keeps working, and the values files
  do not change on merge, so nothing restarts. The staging pin then moves to the
  new digest in a follow-up pull request, and prod's later by the usual
  promotion ([ADR-0015](0015-images-on-ghcr.md)).
- **`data` is a Python namespace package name** (`data.sources.usgs.assets`). A
  top-level module called `data` on the path would shadow it; none is installed.
- **The job name `services` outlives its directory** until the operator renames
  it in the ruleset in the same step.
- **The promotion check searches two paths** until no digest prod pins predates
  the move; the legacy search is then removed.

## Risks, cost, and reversal

The risk is Argo CD, and the two-step transition removes it: at no point does
root read a path that does not exist in the revision it is reading. If step 1
fails to re-point root, the cluster is unchanged and step 2 is not merged.

Reversal is another move: a new ADR and the same two steps in the other
direction (the Argo CD part is the hard one, and needs the same copy-first
order). The cost grows with the number of references, which is why the layout is
chosen early and recorded here.
