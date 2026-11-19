# ADR-0054 — Documentation: one place per fact

- **Status:** Accepted
- **Date:** 2026-11-19
- **Supersedes:** [ADR-0034](0034-notion-as-the-one-documentation-space-outside-the-repo.md)
- **Amends:** [ADR-0039](0039-the-site-in-its-own-repository.md), decision 3

## Context and problem

The architecture guide, roadmap and runbook mixed the current system with
previous designs and dated measurements. Operators and agents had to compare
documents to find a current fact. The separate documentation space proposed
in ADR-0034 was never used. The project also needs a visible boundary between
infrastructure, where agents lead and explain, and data, where the operator
leads with domain expertise.

## Requirements and constraints

The operator must be able to find a current fact and follow its provenance.
Keep historical decision records intact. Keep weekly progress updates out of
the code review queue. The public site must tell the story without maintaining
a parallel technical reference.

## Alternatives considered

- **Keep the versioned roadmap and combine the guides.** Git would retain
  every edit, but status would still require a code review and one guide would
  mix audiences.
- **Put operating documentation in a workspace outside Git.** Status would
  be easy to edit but technical review, versioning and links to the exact
  implementation would drift.
- **Version technical documentation and track work in a GitHub Project.**
  A single repository holds the code and its technical facts, while a Project
  can track weekly changes in priority and progress.

## Decision

1. Keep current architecture, procedures and ADRs with the code. Put each
   current fact in one place and link to it elsewhere. Keep dated measurement
   evidence and design alternatives in ADRs, and use links from the current
   documents.
2. Split the current architecture into [Platform](../platform.md) and
   [Data](../data.md). Keep operating steps and verifications in the
   [runbook](../runbook.md). Put entry triggers in the relevant reference,
   next to the component or data flow they would change.
3. Use the [Lagom GitHub Project](https://github.com/users/FilipeFantini/projects/2)
   for priorities, status and issues. Agents can read it with
   `gh project item-list 2 --owner FilipeFantini` and read a work item with
   `gh issue view <number>`. A pull request references its issue so GitHub can
   close it on merge. Weekly status updates need no pull request.
4. Keep the operator's personal notes outside the project's sources of truth.
   The site presents the project and links to technical details in this
   repository by commit permalink. This replaces the docs-space and roadmap
   references in ADR-0039 decision 3.

## Consequences

Readers start with a shorter document for their role. Historical details
stay findable from the grouped ADR index or the source revision. The
[previous design](https://github.com/FilipeFantini/lagom/blob/412a694/docs/architecture.md),
[previous operational measurements](https://github.com/FilipeFantini/lagom/blob/412a694/docs/runbook.md)
and [previous roadmap](https://github.com/FilipeFantini/lagom/blob/412a694/docs/roadmap.md)
remain available as historical references. Current work
depends on a GitHub feature separate from the checkout. A clone can still
explain, run and rebuild the platform without the Project.

## Risks, cost, and reversal

- The initial move can break links or lose an operational step. Audit relative
  links and anchors outside historical ADR bodies and map removed sections to
  the current guide, ADR or source revision.
- An agent reading the Project needs GitHub access. The issues remain
  individually readable even when the Project view is unavailable.
- A future ADR can choose a different tracker or restore a versioned roadmap.
  The technical boundaries and historical decisions can stay with the code.
