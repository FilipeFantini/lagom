# ADR-0031 — The project is renamed Robusta; internal identifiers keep `lagom`

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: "robusta is a resilient coffee
  with a reference to Brazil; the repo is a lab of open-source tools for
  testing how different tools work, showing that not everything needs
  expensive big-data solutions."
- **Number:** 0030 is held by the open pull request for ADR-0030 (operator UIs
  behind Cloudflare Access); numbers are never reused, so this one is 0031.

## Context and problem

The project was named Lagom Data Solutions, after the Swedish word for "just
enough". The operator renamed it Robusta: a coffee that survives where others
do not, grown widely in Brazil, and a fit for a lab of light, open-source
tools. The name `lagom` is not only in prose. It is in the GitHub repository
name, which every Argo CD Application reads; in three container image names
that promotion by digest pins ([ADR-0015](0015-images-on-ghcr.md)); in a
Kubernetes annotation that the resource budget reads; and in names that live
outside Git — the node, the Tailscale tag, the R2 buckets, the Postgres
databases and roles, the DuckLake catalog, Dagster's code location.

A rename that changed every occurrence at once would move state for no gain:
renaming a bucket or a database is a data migration, and a Dagster code
location's name is stored with every run and schedule. A rename that changed
only the README would leave the repository name and the images behind. Each
occurrence needs a group and, where the change has a runtime effect, an order.

## Requirements and constraints

- Argo CD keeps reconciling throughout: no window in which an Application
  points at a URL that does not exist.
- Promotion by digest is not bypassed: a renamed image reaches staging in one
  pull request and prod in a later one, as any new image does.
- No state is migrated (no bucket, database, role, Secret or node renamed).
- History is not rewritten: superseded and historical ADR text keeps its
  words, including the index titles.
- The domain is out of scope: every `getlagom.app` reference is left to the
  domain decision of ADR-0030, which handles the move to the operator's new
  domain.
- Agents cannot rename the repository, change GHCR packages or write to the
  cluster; those steps are the operator's, given as exact commands.

## Alternatives considered

**Rename everything, state included.** Rename the R2 buckets, the databases
`lagom` and `lagom_dev`, the role `lagom`, the Secrets, the node and its
Tailscale tag. Each is a migration with its own failure mode — a bucket is
copied, not renamed; a database rename breaks every connection string and
the DuckLake catalog's stored data paths; a new node name means a new
Tailscale identity. Rejected: the names are invisible to a reader of the
project, and the cost is real.

**Rename only the prose.** Keep the repository and the images as they are.
Rejected: the repository name is the project's public name, and an image
called `lagom-*` built from a repository called `robusta` is a mismatch every
reader meets.

**Keep the old name.** Rejected by the operator.

## Decision

1. **The project is Robusta.** The GitHub repository is
   `FilipeFantini/robusta`. Every occurrence of `lagom` falls into one of
   three groups.

2. **Group (a) — changes now, in one pull request.** Nothing at runtime
   depends on these beyond the repository URL, whose order is in decision 5.
   - The repository URL, `https://github.com/FilipeFantini/robusta.git`: the
     `repoURL` of every Application under `platform/infra/apps/`; `REPO_URL`
     in `tools/resource_budget.py`, which `manifest_policy.py` holds every
     Git source to; the image label `org.opencontainers.image.source` in the
     three Dockerfiles; the `gh api` path in `CONTRIBUTING.md`.
   - Project prose: the README, `AGENTS.md`'s title, the agent role files,
     `architecture.md`, `roadmap.md`, the component READMEs, the Argo CD
     values comment, the `hello` page.
   - Names that never leave the image or the repository: the Python
     distribution names (`robusta-tools`, `robusta-usgs`, `robusta-warehouse`,
     `robusta-orchestration`, with their `uv.lock` entries), the dbt project
     and profile name (`robusta`), and the image's Unix user (`robusta`,
     still UID 10001). dbt's project name is not part of a model's relation
     name nor of the asset keys Dagster derives from it, so nothing stored
     changes; the new name reaches an environment with the next image it
     pins.

3. **Group (b) — changes in a planned order.**
   - **Image names.** `ghcr.io/filipefantini/lagom-{usgs,warehouse,orchestration}`
     become `ghcr.io/filipefantini/robusta-{usgs,warehouse,orchestration}`.
     The workflow change is in the same pull request as group (a); the pins
     follow ADR-0015's order (decision 5, steps 6–8). The old packages stay
     until prod has run on the new ones, because they are the rollback.
   - **The annotation prefix.** `lagom.dev/ephemeral-memory-request` becomes
     `robusta.ofantini.com/ephemeral-memory-request`. A prefix is a DNS name by
     Kubernetes convention, so that it cannot collide with someone else's:
     `lagom.dev` was never the project's, and `robusta.dev` belongs to an
     unrelated Kubernetes project, so the prefix sits under `ofantini.com`,
     the domain the operator owns. Only repository code reads it — the
     budget tool and the code location's test; Argo CD and Kubernetes store
     it without acting on it — so its order is one commit that changes every
     writer and reader together.

4. **Group (c) — stays `lagom`.** Each is an identifier held in state outside
   Git, or a contract between an image and the manifests that pin it:
   - **State:** the node names `lagom-shared-01`, `lagom-shared-02` (and the
     reserved `lagom-prod-01`), the Hetzner label `project=lagom`, the
     Tailscale tag `tag:lagom-node`, the R2 buckets `lagom-{staging,prod}-lake`
     and `lagom-{staging,prod}-backups`, the Postgres databases and roles
     `lagom` and `lagom_dev`, the Secret `lagom-dev-credentials` and the
     `Database` `lagom-dev`, the DuckLake name `lagom`, the Dagster code
     location `lagom` (stored with every run and schedule; the code server's
     Deployment name derives from it), the Healthchecks.io checks
     `lagom-usgs-{staging,prod}`, and the private archive repository
     `lagom-data-solutions-archive` ([ADR-0017](0017-public-repository-from-a-snapshot.md)).
   - **Image–manifest contracts:** the environment variables `LAGOM_PG_*`,
     `LAGOM_LAKE_*`, `LAGOM_R2_*`, the module `lagom_orchestration` (named in
     each Dagster values file as `-m lagom_orchestration.definitions`), and
     the run tag `lagom/pipeline`, which the daemon's queue limits. Renaming
     one means the manifests and the image change in the same pull request,
     so a prod promotion would stop being a copy of staging's digest and
     become a contract change. Each may be renamed later, in its own change,
     when that contract changes for another reason.
   - **History:** superseded and historical ADR text, and the ADR index's
     titles.

5. **Order.**
   1. This pull request is reviewed and approved, with CI green, but not
      merged.
   2. The operator renames the repository on GitHub. GitHub then redirects
      the old URL, for the web and for Git: clone and fetch of
      `lagom-data-solutions` keep working against `robusta`.
   3. Argo CD keeps syncing through the redirect. Its repo-server reads the
      repository anonymously over HTTPS: it resolves `main` to a commit with
      a GET of the repository's `info/refs`, which Go's HTTP client follows
      through a redirect, and fetches with the `git` binary, which follows a
      redirect on its first request and then talks to the new address. This
      is expected from how both clients behave, not yet observed here; step
      4 checks it.
   4. The operator confirms every Application is still `Synced` and
      `Healthy` from the old URL.
   5. This pull request is merged. The root Application manages itself, so
      Argo CD reads the new `root.yaml` through the redirect, then every
      Application's new `repoURL` from the repository's real address. The
      operator confirms each Application shows the new URL and is `Synced`.
      From here the redirect is no longer used.
   6. The merge publishes `robusta-*` images from `main` (the workflow runs on
      a change to itself). Staging and prod still pin the `lagom-*` digests,
      which stay in GHCR.
   7. A pull request pins the three `robusta-*` digests in staging; the
      operator confirms staging's runs succeed.
   8. A later pull request copies those digests to prod;
      `promotion_check.py` enforces that staging had them first.
   9. After prod has run on the new images for a week, the operator deletes
      the `lagom-*` packages.

   Merging before the rename is the wrong order: every Application would
   point at a repository that does not exist yet, and Argo CD would stop
   reconciling until the rename. Workloads keep running in that state — Argo
   CD does not delete on a failed fetch — but nothing new arrives.

## Consequences

- The project's public name, its repository and its images say Robusta.
- Names a reader will still meet as `lagom`: the environment variables, the
  orchestration module's directory, the code location, the run tag, and every
  name in group (c). This ADR is where they are explained.
- New packages on GHCR are private by default, as the old ones were. The
  `ghcr-pull` tokens are classic tokens with `read:packages`, which covers
  every package of the account, so no pull credential changes.
- Applications added by a pull request that was branched before this one —
  ADR-0030's `cloudflared` Application — carry the old URL;
  `manifest_policy.py` fails that pull request until it is updated, so the
  old URL cannot return to `main` unnoticed. The same pull request uses the
  annotation `lagom.dev/config-revision`, which no tool reads; it is renamed
  to the new prefix there or right after it merges.
- "Robusta" is also the name of an unrelated open-source Kubernetes
  troubleshooting project (`robusta.dev`). Searches for the name will mix
  the two; the repository and images are under the operator's account, so
  nothing collides technically, and the annotation prefix avoids their
  domain.

## What it does not check

Nothing enforces that `lagom` does not reappear in prose. The checks that do
hold are `manifest_policy.py` for the repository URL, and
`test_resource_budget.py` and the code location's tests for the annotation.

## Risks, cost, and reversal

- **The redirect does not carry Argo CD.** If step 4 shows an Application
  failing to fetch, merging this pull request (step 5) fixes it, because it
  points the Applications at the real address. Nothing is lost in between:
  Argo CD keeps the last applied state.
- **The redirect breaks if a new repository takes the old name.** None will;
  after step 5 nothing depends on it.
- **A missed `lagom-*` image pin.** Staging and prod keep pulling the old
  packages until steps 7 and 8; deleting them earlier (step 9) would break
  the next pull. Step 9 waits for that reason.
- **Reversal:** rename the repository back on GitHub (the redirect works the
  other way too), revert this pull request, and pin the `lagom-*` digests
  again. Group (c) needs no reversal because it never moved.
