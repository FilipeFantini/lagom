# ADR-0031 — The project is renamed Robusta, everywhere, in stages

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: "robusta is a resilient coffee
  with a reference to Brazil; the repo is a lab of open-source tools for
  testing how different tools work, showing that not everything needs
  expensive big-data solutions." And, on reviewing a first version that kept
  internal names: rename as much as possible, so that no one wonders where
  "lagom" came from, and leave no leftovers.
- **Number:** 0030 is held by the open pull request for ADR-0030 (operator UIs
  behind Cloudflare Access); numbers are never reused, so this one is 0031.

## Context and problem

The project was named Lagom Data Solutions, after the Swedish word for "just
enough". The operator renamed it Robusta: a coffee that survives where others
do not, and one Brazil grows widely. That fits a lab of light, open-source
tools.

`lagom` is in more places than the prose, and each kind of place changes in a
different way:

- **The repository name.** Every Argo CD Application reads it.
- **Three container image names**, which promotion by digest pins
  ([ADR-0015](0015-images-on-ghcr.md)).
- **Names shared by an image and the manifests that run it**: environment
  variables, the Python module that Dagster loads, a run tag that the
  daemon's queue limits, the code location, and the DuckLake alias.
- **Names held in state outside Git**: Postgres databases, roles and Secrets,
  R2 buckets, the Tailscale tag, Hetzner labels, Healthchecks.io checks, and
  the node.

A first version of this ADR left the last two kinds alone, to avoid
migrations. The operator chose uniformity instead. This version renames
everything that can be renamed. The cost goes into an order of stages, each
safe to stop after, and none moves state before staging has shown the way.

## Requirements and constraints

- Argo CD keeps reconciling throughout. No Application may point at a URL
  that does not exist.
- Promotion by digest is not bypassed. A renamed image reaches staging in one
  pull request and prod in a later one, like any new image.
- A contract name changes on both sides together, per environment. The
  manifests that name it change in the same pull request that pins an image
  which expects the new name.
- Data that a source cannot give back is not put at risk. Every dataset here
  can be reloaded from USGS. Dagster's metadata lives in the database
  `dagster`, which has no `lagom` in it and does not move.
- History is not rewritten. Superseded and historical ADR text keeps its
  words, and so do the index titles.
- The domain is ADR-0030's: every `getlagom.app` reference is handled there.
- Agents cannot write to the cluster or reach Cloudflare, Tailscale or
  Hetzner, and they do not hold secret values. Steps that need any of these
  are the operator's, written as exact commands.

## Alternatives considered

**Keep internal names as `lagom`.** This was the first version of this ADR.
It was cheaper, but it left a second name a reader meets in every manifest.
Rejected by the operator.

**Rename everything in one change.** One pull request would change the
manifests, the images and the state together. Rejected. The images do not
exist until `main` builds them. A database rename cannot ride in a commit.
And one failure would leave every environment broken at once.

**Rename state by migrating it in place**, for example copying buckets
object by object and renaming roles with SQL. This is considered per item in
stage 4. Where the data can be rebuilt from the source, a fresh resource plus
a reload is the simpler and better-tested path.

## Decision

1. **The project is Robusta**, and the GitHub repository is
   `FilipeFantini/robusta`. The rename runs in four stages. Each one leaves
   the platform consistent.

2. **Stage 1 — the repository and the code** (pull request #49). Nothing
   running changes when it merges:
   - the repository URL: every Application's `repoURL`; `REPO_URL` in
     `tools/resource_budget.py`, against which `manifest_policy.py` checks
     every Git source; each image's `org.opencontainers.image.source` label;
     `CONTRIBUTING.md`;
   - the prose: the README, `AGENTS.md`, the agent role files, the docs, the
     `hello` page;
   - names inside the images and the repository: the Python distribution
     names, the dbt project and profile (`robusta`), and the images' Unix
     user (`robusta`, still UID 10001);
   - the images are published as
     `ghcr.io/filipefantini/robusta-{usgs,warehouse,orchestration}`;
   - the code side of each contract: the module `robusta_orchestration`, the
     variables `ROBUSTA_PG_*`, `ROBUSTA_LAKE_*` and `ROBUSTA_R2_*`, and the
     run tag `robusta/pipeline`;
   - the annotation `robusta.ofantini.com/ephemeral-memory-request`. By
     Kubernetes convention a prefix is a DNS name, so that it cannot collide
     with anyone else's. `lagom.dev` was never the project's, and
     `robusta.dev` belongs to an unrelated Kubernetes project, so the prefix
     is under the operator's domain. That domain is public in this repository
     by the operator's decision in ADR-0030, which adds it to `AGENTS.md`'s
     exposure exceptions. Only repository code reads the annotation, so every
     reader changes in this same commit.

   Until stage 3, `main`'s manifests still pin the `lagom-*` digests and
   pass them the old names. Those images expect the old names, so the
   running pairs match. This is the normal gap between a code change and the
   pull request that pins it.

3. **Stage 2 — the GitHub rename**, after stage 1 is approved and before it
   merges. GitHub then redirects the old URL for the web and for Git: clone
   and fetch of `lagom-data-solutions` keep working against `robusta`.

   Argo CD should keep syncing through the redirect. Its repo-server reads
   the repository anonymously over HTTPS in two ways. It resolves `main` to
   a commit with a GET of `info/refs`, and Go's HTTP client follows a
   redirect on a GET. It fetches with the `git` binary, which follows a
   redirect on its first request and then talks to the new address. This is
   what both clients are expected to do; it has not been observed here yet.
   The Applications are checked after the rename, and the merge of stage 1
   then points them at the real address, so the redirect is needed only for
   minutes.

   Merging stage 1 before the rename is the wrong order. Every Application
   would point at a repository that does not exist yet. Workloads would keep
   running, because Argo CD does not delete anything when a fetch fails, but
   nothing new would arrive.

4. **Stage 3 — the images and the contracts, per environment.** One pull
   request for staging and, after staging has run, one for prod. Each pins
   the three `robusta-*` digests that `main` built, and in the same commit
   switches that environment's manifest side of every contract:
   - the variables, from `LAGOM_*` to `ROBUSTA_*`;
   - `-m robusta_orchestration.definitions`;
   - the queue limit's key, `robusta/pipeline`;
   - the code location, `robusta`. Its Deployment and Service get new names,
     the old ones are pruned, and the schedules, whose default status is
     running, start under the new location. Past runs keep the name they
     ran under;
   - dlt's DuckLake alias, `robusta`. It names the attached catalog for the
     session only, and is not stored in the catalog.

   `promotion_check.py` holds prod to digests that staging pinned first.
   Prod's pull request also renames its contracts, so it is a digest copy
   plus a rename. This is the one exception, and it is only a rename. The
   `lagom-*` packages stay in GHCR as the rollback. They are deleted a week
   after prod runs on the new ones.

5. **Stage 4 — state, staging first.** Each item is done in staging, checked,
   and then done in prod. Its exact commands go into the runbook when staging
   has proved them. The agent prepares the Git side; the operator does what
   needs consoles or secret values.
   - **Postgres:** the database and owner `lagom` become `robusta`, and the
     developer's `lagom_dev` role, database, `Database` object and Secret
     `lagom-dev-credentials` become `robusta_dev`, `robusta-dev` and
     `robusta-dev-credentials`. A SealedSecret is bound to its name, and the
     username is inside it, so the developer's credential is sealed again
     under the new name. The path is whichever staging shows to be safe
     with CloudNativePG's generated `postgres-app` Secret: a rename in SQL,
     or a new database with the data reloaded from the source. The
     restore-test bootstrap names the new database.
   - **R2:** buckets cannot be renamed. The new buckets
     `robusta-{staging,prod}-{lake,backups}` are created with tokens scoped
     to them, and those tokens are sealed. The lake is reloaded from the
     source. Backups start a new archive in the new bucket. The old buckets
     are deleted after one full retention period of the new ones (7 days in
     staging, 14 in prod), so a restore point exists throughout.
   - **Tailscale:** `tag:robusta-node` is added beside `tag:lagom-node` in
     the policy, the node is retagged, and the old tag is removed only after
     SSH, the API and the Dagster UI work through the new one.
   - **Hetzner** label `project=robusta`; **Healthchecks.io** checks
     `robusta-usgs-{staging,prod}`. The ping URLs are unchanged, so both
     renames are only labels.
   - **The private archive repository** becomes `robusta-archive`. GitHub
     redirects it too. ADR-0017's text keeps the name it had.

6. **What keeps `lagom`:**
   - **The node, `lagom-shared-02`, until it is replaced.** A Kubernetes node's
     name is its hostname, and the local-path volumes — Postgres's data — are
     bound to that name. A renamed node would be a new node with unreachable
     volumes. The next node is `robusta-shared-03`, by the runbook's
     convention.
   - **History:** superseded and historical ADR text, the ADR index titles,
     past runs and measurements quoted with the names they had, and the
     operator's local clone folder, which is outside the repository.

## Consequences

- When stage 4 ends, `lagom` remains only in history and in the node's name.
- Stage 3 makes prod's promotion carry a rename. Staging runs that same
  rename first, so it is tested before it reaches prod.
- Stage 4 reloads the lake and starts new backup archives. For the length of
  one retention period, the old buckets hold the only restore points from
  before the move. They are kept for that long for this reason.
- New packages on GHCR are private by default, as the old ones were. The
  `ghcr-pull` tokens are classic tokens with `read:packages`, which covers
  every package in the account, so no pull credential changes.
- ADR-0030's pull request was branched before this one. It adds the
  `cloudflared` Application with the old URL, and `manifest_policy.py` fails
  it until that URL is updated, so the old URL cannot return to `main`
  unnoticed.
- "Robusta" is also the name of an unrelated open-source Kubernetes
  troubleshooting project (`robusta.dev`), so searches for the name will
  mix the two. Nothing collides technically: the repository and the images
  are under the operator's account, and the annotation prefix stays off
  their domain.

## What it does not check

Nothing stops `lagom` from coming back into prose. The checks that do hold
are `manifest_policy.py` for the repository URL, and the budget's and the
code location's tests for the annotation. Nothing checks that both sides of
a contract match before an environment runs them. Staging is where a mismatch
shows up, as a failed code server or run.

## Risks, cost, and reversal

- **The redirect does not carry Argo CD.** Merging stage 1 points the
  Applications at the real address. Until then Argo CD keeps the last state
  it applied.
- **A contract mismatch in stage 3** fails staging's code server or runs.
  Reverting staging's pull request restores the `lagom-*` pins and names.
- **Stage 4 loses restore points** if an old bucket is deleted early. Each
  deletion waits one full retention period for that reason.
- **The Tailscale retag locks the operator out** if the policy is wrong. The
  old tag stays until the new one is proven, and Hetzner's console reaches
  the node without the tailnet.
- **Reversal:** each stage reverts on its own. Rename the repository back
  (GitHub redirects the other way too), revert the pull requests, and pin the
  `lagom-*` digests again. State that stage 4 created next to the old state
  is dropped; the old state is still there until its deletion step.
