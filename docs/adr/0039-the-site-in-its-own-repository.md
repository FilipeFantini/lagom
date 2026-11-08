# ADR-0039 — The site at ofantini.com lives in its own repository

- **Status:** Accepted
- **Date:** 2026-11-08
- **Decided by:** the operator, in conversation on 2026-11-08: the site moves
  to its own repository, named `ofantini`.
- **Amends:** [ADR-0022](0022-platform-as-a-portfolio-of-architectures.md),
  *The pieces*: the site no longer lives under `products/`; the dashboards do.

## Context and problem

ADR-0022 put the site under `products/` because it was the platform's public
face. Since then the site's role has changed. `ofantini.com` is the operator's
personal site: a home that presents him, a list of projects, and articles.
Lagom is the first project on it, not the only one.

ADR-0022 also states when a separate repository is justified: by a separate
lifecycle. The site now has one. It changes when its design changes or an
article is published, including articles about projects that are not Lagom,
and none of that needs the platform's checks (resource budget, manifest
policy, dbt build).

## Requirements and constraints

- Lagom's repository stays the source of truth for Lagom.
- What changes together is reviewed together: a change to a gold model that
  breaks a dashboard must fail in the same pull request.
- The site must not depend on the cluster or on Lagom's repository at build
  time: one project failing must not stop the site from building.
- No new cost in GitHub Actions minutes.
- Both repositories are public. The site identifies the operator by design;
  neither may point at the infrastructure.

## Alternatives considered

**Keep the site in `products/`.** No new repository, but every future project
would publish its articles through Lagom's pull requests and checks, and the
personal home would sit inside a data platform's repository.

**Articles in each project's repository, gathered by the site at build
time.** Each article would sit next to the code it describes. The site's build
would then depend on every project's repository, and a home page would need a
fetch across repositories, or Git submodules, to render. Rejected: coupling
for a benefit that a permalink gives more simply (Decision 3).

**One repository for the site and every project.** Rejected for the reason
above: the projects do not share a lifecycle with each other or with the site.

## Decision

1. **The site lives in `FilipeFantini/ofantini`**, a public repository: the
   Astro project, its layout and design, the home, the projects list, one page
   per project, and the articles. Lagom no longer holds any of the site.
2. **Lagom keeps its dashboards.** Evidence stays under `products/`, built in
   the cluster after each run and published to Cloudflare Pages
   ([ADR-0023](0023-sme-stack-duckdb-on-ducklake.md), proposed), as its own
   Pages project at a stable address under `ofantini.com`. The hostname is set
   when the first dashboard ships.
3. **Articles are published in the site's repository.** They start as drafts
   in Notion ([ADR-0034](0034-notion-as-the-one-documentation-space-outside-the-repo.md))
   and become Markdown there when published. An article that describes Lagom
   links to the files and ADRs it describes by permalink at a commit, so it is
   a dated account, not a claim about today's code. Where an article and this
   repository disagree, this repository wins and the article is corrected or
   dated.
4. **The only interface between the two is a URL.** The site links to or
   embeds the published dashboards. It never reads Lagom's repository, its
   cluster, or its data at build time.
5. **The site builds on Cloudflare Pages' own Git integration**, not on GitHub
   Actions. Its branch protection and its rules for agents are set in that
   repository, approved by the operator in its first pull request; Lagom's
   [`AGENTS.md`](../../AGENTS.md) does not govern it.
6. **Each future project gets its own repository** and a page on the site.

## Consequences

**Positive.** Lagom's repository holds only Lagom. The site can change daily
without Lagom's checks or review in the way, and a new project adds a page,
not a dependency.

**Negative.**

- **Articles can drift from the code.** The permalink in Decision 3 makes the
  drift visible; nothing checks it.
- **A second repository to keep safe.** It needs its own branch protection and
  secret scanning, set by the operator.

## Risks, cost, and reversal

Cost: Cloudflare Pages' free plan, no Actions minutes. Reversal: copy the
site's directory back under `products/`; nothing in Lagom depends on it.
