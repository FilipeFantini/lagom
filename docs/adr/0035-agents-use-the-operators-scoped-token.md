# ADR-0035 — Agents use the operator's repository-scoped token; an environment approval guards what reaches the cluster

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: agents in the runner work
  autonomously — they open and merge their own pull requests — from a place
  that does not reach the operator's files; the operator comes in for
  changes that need special configuration. The operator does not read code:
  they rely on the reviewer and the gates, and approve a guardrail change
  only when it comes with a clear explanation of what it changes and
  touches. A machine account for agents was chosen first and suspended by
  GitHub on creation; the operator then chose their own repository-scoped
  token, with gates that do not depend on who acts. The mechanism and the
  guarded paths are the architect's proposal, accepted with this ADR.
- **Amends:** [ADR-0032](0032-agents-in-credential-free-containers.md)
  Decisions 2, 4 and 5.
- **Amended by:** [ADR-0036](0036-the-reviewer-as-a-required-check.md) — the
  reviewer's verdict in Decision 5 becomes a required check, which also
  enforces the For the operator section.

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

- the `main` ruleset requires a pull request and the seven CI checks, with
  **zero approvals** and no bypass;
- every Argo CD Application is in the `default` project, with no AppProject
  restricting where it deploys or which kinds it may create;
- the CI checks are code in the repository (`tools/`), so a pull request can
  weaken the check that judges it.

The only GitHub identity available is the operator's. A GitHub machine
account for agents was created on 2026-11-07 and suspended by GitHub at
once. So the agents act as the operator, and any gate that asks "did the
operator approve?" by looking at *who* acted would be met by the agent
itself. The gates must look at *what the token can do* instead.

## Requirements and constraints

- An agent's change reaches the cluster, CI, or the rules only after the
  operator approves it on GitHub, enforced by GitHub, not by instruction.
- Everything else an agent can finish alone.
- The container still holds nothing that reaches the cluster or the
  infrastructure providers ([ADR-0032](0032-agents-in-credential-free-containers.md)).
- No new software on the operator's Mac; no cost.

## Alternatives considered

**A machine account for agents.** A separate identity: GitHub would tell
agent from operator, and a code owner's review would be a real gate. Tried
and suspended by GitHub on creation; an appeal is possible but not needed by
this design. If the account returns, it can replace the operator's token in
the connector without changing the gates below.

**Code owners with a `code owners` ruleset.** The usual gate. Rejected: the
operator cannot approve a pull request opened in their own name, so the
ruleset would need an admin bypass — and an agent acting as the operator
would hold it.

**A GitHub App.** A separate identity whose tokens expire in an hour.
Rejected for now: the runner's connector takes a static token, and minting
App tokens needs the App's private key and a script next to the agent.

**One required approval on every pull request.** Rejected: the operator
cannot approve their own pull requests, and does not read code.

## Decision

**1. Agents use a fine-grained personal access token of the operator's,
scoped to `lagom` alone,** with an expiry of at most 90 days and only these
permissions: **Contents**, **Pull requests** and, optionally, **Issues**
read and write, and **Metadata** read (GitHub requires it). Everything else
is *No access* — notably **Administration** (rulesets, settings,
collaborators), **Actions**, **Deployments** (approving an environment),
**Environments**, **Secrets**, **Variables**, **Workflows** (editing
`.github/workflows/`) and **Webhooks**. It is the one credential a runner may
hold (amending ADR-0032 Decisions 2 and 5), and it goes in the runner's
GitHub connector, not the container: in Tucano the connector talks to
GitHub's remote MCP server, so the agent uses the tools without reading the
token. Nothing else changes: no kubeconfig, SSH key, cloud or provider
token, and no Mac folder lent. The broader token of the operator's Mac
session (`repo` and `workflow` scopes) never goes to a runner.

**2. Every gate is a required check on `main`, which has no bypass,** so it
binds the operator's identity — and an agent acting as it — like any other:
the CI checks, the reviewer ([ADR-0036](0036-the-reviewer-as-a-required-check.md)),
and the guardrail approval below.

**3. `.github/CODEOWNERS` is the list of guarded paths**
([`.github/CODEOWNERS`](../../.github/CODEOWNERS)): those that reach the
cluster, judge a change, or state the rules.

- `platform/infra/` — everything Argo CD applies, both environments;
- `.github/` (CI and this list) and `tools/` (the checks CI runs);
- `pyproject.toml` and `uv.lock` anywhere — installing a package runs its
  code — and the configuration files lint, type and test tools read,
  anywhere (`.sqlfluff`, `ruff.toml`, `mypy.ini`, `pytest.ini` and the like),
  since a nested one overrides the guarded one above it;
- `.gitattributes`, and the secret scan's `.gitleaks.toml` and
  `.gitleaksignore` — the second silences a finding by its fingerprint;
- the rules: `AGENTS.md`, `CLAUDE.md`, `.claude/`, `CONTRIBUTING.md`,
  `docs/engineering-protocol.md`, `docs/adr/`, `docs/roadmap.md`, and
  `docs/architecture.md`, which holds the runbook the operator runs by hand.

[`tools/guarded_paths.py`](../../tools/guarded_paths.py) reads it. It accepts
the subset of the format the file uses and fails on anything else — a
wildcard, a negation, a line without an owner — so an edit it cannot read
stops the gate instead of opening it.

**4. A guarded pull request waits for the operator's approval of an
environment.** The [`guardrail approval`](../../.github/workflows/guardrail-approval.yml)
workflow runs on `pull_request_target`, so it and the list come from the
base branch, and only for pull requests into `main` — on any other base they
would be that branch's, which the agents' token can push. It re-runs when a
pull request is edited, so changing a base to `main` starts a fresh run
against `main`'s list; without that, a pull request opened against a branch
that already holds a guarded change would pass there and keep its green
check after being retargeted. It reads only the pull request's file names,
both names of a rename, and fails when GitHub lists fewer than the pull
request changes (the API stops at 3000). When one is guarded, a job
waits on the `guardrail` environment, whose required reviewer is the
operator, with administrators' bypass turned off. **Approving needs the
Deployments permission, which the agents' token does not have,** and
GitHub's MCP server offers no tool for it, so the agent cannot approve for
the operator although it acts as them. A new push, or an edit — of the
description the operator approved on, too — starts a new run and needs a
new approval. The required check, `guardrail approval`, reports only after
that, and fails if the classification did not run — a skipped job would
otherwise count as passed.

**5. Agents merge what is not guarded; the operator approves on a
plain-language section.** A pull request that touches no guarded path merges
once every required check is green, the reviewer's verdict included. An
image built from it runs only once a guarded pin under `platform/infra/`
names its digest, so the pin pull request is the operator's step before code
runs in the cluster. **The operator does not read code.** Every guarded pull
request — a pin included — carries a section **For the operator**, in plain
words: what changes in behaviour, what it touches, what could go wrong, and
how to undo it; for a pin, every merged change since the previous pin. The
operator approves on that section and the reviewer's verdict, and asks for a
clearer one rather than approving a change they cannot explain.

**6. The patch flow stays for an agent without the token** — any agent on a
runner that has not passed the setup below, and every agent on a harness
sandbox ([ADR-0029](0029-agents-on-more-than-one-harness.md) Decision 3).

**7. The token goes to a runner only after the gate is shown to work:** the
`guardrail` environment exists as above; the workflow is on `main`; a pull
request touching a guarded path shows `guardrail approval` waiting, and one
touching none shows it passing; an attempt to approve the waiting run with
the agents' token is refused by GitHub; and `guardrail approval` is in the
`main` ruleset's required checks. The order and the commands are in
[CONTRIBUTING](../../CONTRIBUTING.md#branch-protection).

## Consequences

**Positive.**

- Agents finish their own work; the operator is called for what reaches the
  cluster, CI, or the rules — and GitHub enforces that through a permission
  the agents' token lacks.
- The same gate binds the operator's own Claude sessions: the operator's
  go-ahead for a guardrail change becomes a click, not a convention.
- Revoking the agents is one action: delete the token.

**Negative.**

- **The history does not tell agent from operator.** Both act as the
  operator; commits carry a `Co-Authored-By:` line naming the agent
  ([`AGENTS.md`](../../AGENTS.md) §6), by convention only.
- **The gate depends on the token's permissions,** which the operator sets
  in GitHub's settings. A token created with Deployments or Administration
  could approve or remove the gate. The refusal test in Decision 7 is what
  shows it; nothing re-checks it later.
- **The Mac session's token is broader.** With `repo` it can, as an
  administrator, edit rulesets and environments; for it the gate holds by
  the convention of [`AGENTS.md`](../../AGENTS.md) §6.
- **No deploy is autonomous yet.** Staging pins live under `platform/infra/`,
  so every deploy waits for the operator. Staging can lose its guard once an
  AppProject confines staging's Applications to their namespace and no
  cluster-scoped kind: until then a manifest in a staging folder can name the
  prod namespace and Argo CD applies it.
- **No human reads the code.** The operator approves pins and guardrail
  changes on a plain-language summary; the code is read by reviewer agents
  only, and a summary that is wrong, or written to mislead, is approved as
  if it were right.
- **Test code is not guarded.** A test can be weakened, or a `conftest.py`
  can skip tests, in an unguarded pull request; the reviewer is the only
  control there.
- **Unguarded code can still run on the Mac.** An agent can merge service or
  test code without the operator reading it, and the operator's session later
  runs those tests on the Mac, which holds the credentials.
- **An executable bit is not a path.** The list matches paths only, so a
  mode change on an unguarded file needs no approval.

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Nothing
verifies that the token in a runner has only the permissions in Decision 1
after the refusal test, that the guarded list is complete, or which agent
wrote a commit. GitHub enforces: the required checks on `main` with no
bypass, the `guardrail` environment's reviewer, and the token's permissions.

## Risks, cost, and reversal

No cost: environments with required reviewers are free on a public
repository.

Reversal: delete the token; agents fall back to the patch flow of ADR-0032.
Removing a path from `CODEOWNERS` is a guardrail change; removing the
`guardrail approval` check or the environment's reviewer needs a new ADR.
