# ADR-0035 — Agents push as their own GitHub identity; code owners guard what reaches the cluster

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: agents in the runner work
  autonomously — they open and merge their own pull requests — from a place
  that does not reach the operator's files; the operator comes in for
  changes that need special configuration. The paths that need the
  operator's approval are the architect's proposal, accepted with this ADR.
- **Amends:** [ADR-0032](0032-agents-in-credential-free-containers.md)
  Decisions 2, 4 and 5; meets the trigger [ADR-0029](0029-agents-on-more-than-one-harness.md)
  deferred ("a GitHub account of its own for agents").

## Context and problem

Under [ADR-0032](0032-agents-in-credential-free-containers.md) an agent in a
runner holds no credential, so its work leaves as a patch that a session
outside the runner reads, applies and pushes. Every change waits for that
session. The operator wants the agents to finish their own work: open a pull
request, get a review, merge.

That needs a credential that can write to GitHub, and in this repository
**writing to `main` is writing to the cluster.** Argo CD watches `main` and
applies `platform/infra/` with `automated` sync and `selfHeal`, prod
included. Measured on 2026-11-07 (`gh api repos/FilipeFantini/lagom/rulesets/25948914`):

- the `main` ruleset requires a pull request and the seven CI checks, but
  **zero approvals** — whoever can merge can merge without anyone reading;
- every Argo CD Application is in the `default` project, with no AppProject
  restricting where it deploys or which kinds it may create;
- the CI checks are code in the repository (`tools/`), so a pull request can
  weaken the check that judges it.

So a token that can merge is, today, a token that can change prod, the CI
gates, and the agents' own rules. The operator's token cannot be the one:
GitHub would not tell the operator's actions from an agent's, and any rule
that asks for "the operator's approval" would be met by the agent itself.

## Requirements and constraints

- An agent's change reaches the cluster, CI, or the rules only after the
  operator approves it **on GitHub**, enforced by GitHub, not by instruction.
- Everything else an agent can finish alone.
- The container still holds nothing that reaches the cluster or the
  infrastructure providers ([ADR-0032](0032-agents-in-credential-free-containers.md)).
- No new software on the operator's Mac; no cost.

## Alternatives considered

**The operator's token in the container.** Rejected: see above.

**A fine-grained personal access token on an agents' account.** The
narrowest token GitHub has — one repository, chosen permissions. Not
possible: a fine-grained token reaches only repositories owned by its own
account or organisation, and GitHub lists contributing as a repository
collaborator as unsupported
([GitHub docs](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens),
read 2026-11-07). `lagom` is owned by the operator's personal
account.

**A GitHub App installed on `lagom`.** Its tokens are scoped to the one
repository and to Contents and Pull requests, and expire in an hour.
Deferred: the container would hold the App's private key, a long-lived
credential like a token, plus a script to mint tokens from it. It becomes
worth it when the classic token's breadth (below) matters — for instance if
the agents' account gains access to a second repository.

**Moving `lagom` to an organisation,** which would allow a fine-grained
token. Rejected for now: it changes the repository's URL and owner for one
token.

**Requiring one approval on every pull request.** The simplest gate, but
every agent change would wait for the operator — the patch flow again, one
click shorter.

## Decision

**1. Agents have one GitHub account of their own,** a machine account
(`<agent-github-user>`) created and owned by the operator, as GitHub's terms
allow alongside a personal account. It is a collaborator on `lagom` with the
**Write** role, and on nothing else. Its commits use its own noreply address,
so the history shows which identity wrote what.

**2. Its token is a classic personal access token with only `public_repo`,**
an expiry of at most 90 days, and no `workflow` scope. Without `workflow`
GitHub refuses any push that touches `.github/workflows/`. Without
`write:packages` it cannot publish images; only `images.yml` on `main` does.
This token is the one credential that may enter an agent's container, or a
runner's GitHub connector (amending ADR-0032 Decisions 2 and 5). Nothing
else changes there: no kubeconfig, SSH key, cloud or provider token, and no
Mac folder lent.

**3. A `CODEOWNERS` file names the operator as owner of the guarded paths**
([`.github/CODEOWNERS`](../../.github/CODEOWNERS)), and a second ruleset,
`code owners`, requires a code owner's approval on `main` for any pull request
that touches one. The guarded paths are those that reach the cluster, judge
a change, or state the rules:

- `platform/infra/` — everything Argo CD applies, both environments;
- `.github/` (CI and `CODEOWNERS` itself) and `tools/` (the checks CI runs);
- `pyproject.toml` and `uv.lock` anywhere — installing a package runs its
  code — and the configuration files lint, type and test tools read,
  anywhere (`.sqlfluff`, `ruff.toml`, `mypy.ini`, `pytest.ini` and the like),
  since a nested one overrides the guarded one above it;
- `.gitattributes`, and the secret scan's `.gitleaks.toml` and
  `.gitleaksignore` — the second silences a finding by its fingerprint;
- the rules: `AGENTS.md`, `CLAUDE.md`, `.claude/`, `CONTRIBUTING.md`,
  `docs/engineering-protocol.md`, `docs/adr/`, `docs/roadmap.md`, and
  `docs/architecture.md`, which holds the runbook the operator runs by hand.

The `code owners` ruleset also dismisses an approval when new commits are
pushed, so an approved pull request cannot change before it merges.

**4. The operator's own pull requests bypass only `code owners`.** GitHub does
not let an author approve their own pull request, and the operator is the only
code owner, so the ruleset lets the Repository admin role bypass it, for pull
requests only. The `main` ruleset keeps no bypass: the CI checks bind
everyone. The agents' account has Write, not admin, so it cannot bypass.
A session on the operator's Mac pushes as the operator and so shares the
bypass; for it the go-ahead for a guardrail change stays the convention of
[`AGENTS.md`](../../AGENTS.md) §6.

**5. Agents merge what is not guarded.** For a pull request that touches no
guarded path, the rule of ADR-0029 Decision 4 holds: green checks and a
reviewer's APPROVE, then any agent merges. The reviewer's APPROVE remains an
instruction there, but what such a pull request can change is bounded: it
cannot reach the cluster, CI, or the rules. An image built from it runs only
once a guarded pin under `platform/infra/` names its digest — so **the pull
request that moves a pin is where the operator reads the code.** Its
description links the comparison of the commit last pinned with the new one
(`https://github.com/FilipeFantini/lagom/compare/<old-sha>...<new-sha>`),
and the operator approves that diff, not the 64 characters of a digest.

**6. The patch flow stays for an agent without the token** — any agent on a
runner that has not passed the setup below, and every agent on a harness
sandbox ([ADR-0029](0029-agents-on-more-than-one-harness.md) Decision 3).

**7. The token is handed over only after the setup is shown to work:** with
`CODEOWNERS` on `main` and the `code owners` ruleset active, a pull request
from the agents' account that touches a guarded path must show the merge
blocked for want of a code owner's review, and one that touches none must not.
The order and the commands are in
[CONTRIBUTING](../../CONTRIBUTING.md#branch-protection). If GitHub does not
block the guarded pull request — a code owner's review not enforced with the
approval count at zero — the token is not handed over, and a new ADR chooses
between one required approval on every pull request and a GitHub App.

## Consequences

**Positive.**

- Agents finish their own work; the operator is called for what reaches the
  cluster, CI, or the rules — and GitHub, not an instruction, enforces that.
- The history and the pull requests tell agent work from the operator's.
- Revoking the agents is one action: remove the collaborator or the token.

**Negative.**

- **No deploy is autonomous yet.** Staging pins live under `platform/infra/`,
  so every deploy — staging too — waits for the operator's approval. Staging
  can lose its code owner once an AppProject confines staging's Applications
  to their namespace and no cluster-scoped kind: until then a manifest in a
  staging folder can name the prod namespace and Argo CD applies it.
- **The classic token is broad in kind.** `public_repo` lets the account act
  on any public repository on GitHub — comment, open issues, push to its own
  forks. It can write only where it is a collaborator, which is `lagom` alone.
- **The cluster runs code the operator approved as a diff, not line by
  line as it merged.** Pipeline code and Dockerfiles merge unguarded; the
  human step before they run in staging or prod is the pin pull request
  (Decision 5). An operator who approves a pin without opening the
  comparison approves code nobody but an agent read.
- **Test code is not guarded.** A test can be weakened — or a `conftest.py`
  can skip tests — in an unguarded pull request; the reviewer is the only
  control there.
- **Unguarded code can still run on the Mac.** An agent can merge service or
  test code without the operator reading it, and the operator's session later
  runs those tests on the Mac, which holds the credentials. Dependencies are
  guarded; the code itself is read by the reviewer agent only.
- **The machine account's token is in the container,** so what the agent
  reads it can send to its model provider. Its reach is the repository, which
  is public.
- **An executable bit is not a path.** `CODEOWNERS` matches paths only, so a
  mode change on an unguarded file needs no approval.
- **One more ruleset to keep in step.** A path added to the guarded list goes
  into `CODEOWNERS`; a new top-level location that reaches the cluster must be
  added, or it is unguarded.

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Nothing
verifies that a reviewer approved an unguarded pull request before an agent
merged it, that only the agents' token is in a container, or that the
guarded list is complete. GitHub enforces: the required checks, a code
owner's approval for guarded paths for every non-admin author, the `workflow`
scope on workflow files, and the Write role's limits (no settings, rulesets,
secrets or collaborators). Whether a code owner's approval is required with
the approval count at zero is shown by the test in Decision 7, not assumed.

## Risks, cost, and reversal

No cost: machine accounts and rulesets are free on a public repository.

Reversal: remove the agents' account from the repository and revoke its
token; agents fall back to the patch flow of ADR-0032. Removing a path from
`CODEOWNERS` is a guardrail change; dropping the `code owners` ruleset needs a
new ADR.
