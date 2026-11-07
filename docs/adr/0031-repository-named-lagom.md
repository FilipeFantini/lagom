# ADR-0031 — The repository is named `lagom`; a rename to Robusta was tried and reverted

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: the project keeps the name
  Lagom, with its explanation of balance, and drops "Data Solutions"; the
  domain stays `ofantini.com` (ADR-0030, pull request #46).
- **Number:** 0030 is held by the pull request for ADR-0030, so this one is
  0031.

## Context and problem

The repository was `FilipeFantini/lagom-data-solutions` and the project
"Lagom Data Solutions". "Data Solutions" reads like a consultancy's name, not
a lab's. On 2026-11-07 the operator decided to rename the project Robusta.
The repository was renamed `robusta`, and a pull request (#49) prepared the
rest. The same day the operator reverted that decision: the project stays
Lagom, Swedish for "just enough", which is the idea the README explains.
Only the repository name and the titles change.

`FilipeFantini/lagom` was already taken, by the operator's private iOS app.

The repository name matters beyond the README. Every Argo CD Application
reads the repository by URL. `tools/resource_budget.py` holds that URL as
`REPO_URL`, and `manifest_policy.py` fails any Git source that does not match
it. Each image also carries the URL in its `org.opencontainers.image.source`
label.

## Requirements and constraints

- Argo CD keeps reconciling throughout. No Application may point at a URL
  that does not exist.
- Images, annotations and names held in state do not change: the name stays
  Lagom, so they are already right.
- History is not rewritten.

## Alternatives considered

**Robusta.** Tried on 2026-11-07 and reverted the same day by the operator,
before any change reached `main`. Pull request #49 was closed. Its plan
renamed the images, the contracts between image and manifests, and the state
(databases, buckets, the Tailscale tag) in four stages. None of it is needed
while the name stays Lagom.

**Keep `lagom-data-solutions`.** No cost, but it keeps the name the operator
dropped.

**Another repository name, such as `lagom-platform`.** It would have left the
app with `lagom`. Rejected by the operator, who renamed the app instead.

## Decision

1. The repository is `FilipeFantini/lagom` and the project is **Lagom**. The
   operator's iOS app repository, which held the name, is now
   `FilipeFantini/lagom-ios`.
2. The URL `https://github.com/FilipeFantini/lagom.git` is set in every
   Application under `platform/infra/apps/`, in `REPO_URL`, in the three
   images' source label, and in `CONTRIBUTING.md`. The titles of the README,
   `AGENTS.md` and the agent role files say "Lagom".
3. **Order:** rename on GitHub first, then merge the URL change. After a
   rename, GitHub redirects the old names (`lagom-data-solutions`,
   `robusta`) for the web and for Git. `git ls-remote` through each old URL
   returned `main` after the renames (2026-11-07). So Argo CD, which reads
   the repository anonymously over HTTPS, keeps fetching through the
   redirect until the merge points it at the real address. Merging first
   would have pointed every Application at a repository that did not exist.
4. **Unchanged:**
   - the images `ghcr.io/filipefantini/lagom-*`;
   - the annotation prefix `lagom.dev/`;
   - every name held in state;
   - the archived repository `lagom-data-solutions-archive`
     ([ADR-0017](0017-public-repository-from-a-snapshot.md)). It is
     read-only, and it is the record of the old name.

## Consequences

- The project and its repository have one short name.
- The redirects from `lagom-data-solutions` and `robusta` last as long as no
  new repository takes either name on this account. Nothing in this
  repository depends on them after the merge.
- A clone of the iOS app made before its rename still points at
  `FilipeFantini/lagom`, which is now this public repository, so a push from
  such a clone would land here. Every such clone is pointed at `lagom-ios`.
  The operator's machine has none.
- Pull requests branched before this change — ADR-0030's — carry the old URL
  in any new Application. `manifest_policy.py` fails them until it is
  updated.

## Risks, cost, and reversal

- **The redirect does not carry Argo CD.** The merge fixes it by pointing
  the Applications at the real address. Until then Argo CD keeps the last
  state it applied and deletes nothing.
- **Reversal:** rename the repository on GitHub, then change the same URLs.
  Each rename leaves a redirect behind.
