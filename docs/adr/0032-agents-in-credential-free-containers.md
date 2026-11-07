# ADR-0032 — Agents run in containers that hold no credential

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: the project's agents —
  several, in roles that may change — run through an agent runner, Tucano
  first. Neither the runner nor the roles are fixed, so the rules name
  neither.
- **Amends:** [ADR-0029](0029-agents-on-more-than-one-harness.md) — reverses
  its rejection of Tucano, and adds a second boundary to its Decision 3.
- **Amended by:** [ADR-0035](0035-agents-push-as-their-own-github-identity.md)
  — the container may hold one credential, the agents' own GitHub account's
  token (Decisions 2 and 5), and with it the agent pushes its own work
  instead of a patch (Decision 4).

## Context and problem

An **agent runner** is a desktop program on the operator's Mac that runs
several agents at once. Each agent gets a harness already on the Mac (Claude
Code, Codex and others), a model, a set of tools, and a place where its
commands run. A runner is a harness by
[ADR-0029](0029-agents-on-more-than-one-harness.md)'s definition: it
decides what the agent may do.

The first runner is [Tucano](https://tucano.sh), adopted on 2026-11-07.
The operator expects it may be replaced. Its facts, read in its README and
SECURITY.md at v0.2.15:

- It offers three places for an agent's commands: none (no shell), an
  isolated Linux — a Docker container holding only the agent's own folder and
  the folders lent to it — or the operator's own computer.
- Its Claude Code harness reads only the `env` block and `apiKeyHelper` of
  `~/.claude/settings.json`; "hooks, plugins and permissions stay out". So
  `.claude/settings.json` binds none of its agents.
- Each reply starts the harness afresh, with no built-in tools and no saved
  session; its tools come from Tucano's own gateway.

ADR-0029 rejected Tucano on 2026-11-05 for being eight days old and for
leaving the permissions out. Both still hold. What changes the answer is the
container: ADR-0029's boundary for another harness is a sandbox with no
network, and a container has network, so this ADR needs a different
boundary — and one that holds for whatever runner replaces Tucano.

**Measured on 2026-11-07.** A plain Docker container on the operator's Mac
(`docker run --rm alpine:3.20`, Docker Desktop's default network) opened TCP
connections to the node's port 6443 (the Kubernetes API) and port 22 (SSH)
over the tailnet, and to `github.com:443`. Docker Desktop sends a container's
traffic out through the Mac, so the tailnet sees it as the Mac, signed in as
the operator, and the Tailscale ACL lets it through. A container can reach
what the Mac reaches. What it lacks is the credentials.

## Requirements and constraints

- An agent with no deny rules must not be able to change the cluster or
  GitHub ([ADR-0029](0029-agents-on-more-than-one-harness.md)).
- Nothing an agent does may run code on the Mac outside its container.
- The rules stay in [`AGENTS.md`](../../AGENTS.md), not in a runner's
  per-agent instructions or memory.
- The runner, the agents' names and their roles change; the rules must not
  depend on any of them.

## Alternatives considered

**The agent's commands on the Mac itself.** Tucano offers it. The Mac holds a
cluster-admin kubeconfig, an SSH agent and a GitHub token with `workflow`
scope; with the permissions left out, nothing stands between an agent and
those. Rejected: the case ADR-0029 rejected Paseo on the Mac for.

**Lend the operator's checkout to the container.** Simple: the agent edits the
files the operator's Claude Code session commits from. Rejected: the agent can
write `.git` too. A hook in `.git/hooks`, or a `core.fsmonitor` or
`core.hooksPath` line in `.git/config`, runs on the Mac the next time any
git command runs there, and neither shows in a diff. Codex keeps `.git`
read-only for this reason.

**A container network that cannot reach the tailnet.** Would make the
boundary the network, as for Codex. Not available in Tucano, which starts the
containers and offers no network setting. A runner that offers it later is
better, not required.

**GitHub write access through a runner's connector.** Tucano can hold a
GitHub token and turn a merge into an approval card. Rejected for now: the
token would be the operator's identity in every agent's hands. The trigger is
the one ADR-0029 deferred: a GitHub account of its own for agents.

**An ADR per runner.** Rejected: the operator expects to change runners. The
boundary is what matters, so this ADR states it once, with the checks a
runner must pass (Decision 1).

## Decision

**1. Any agent runner may be used, for any number of agents in any roles,**
if it can keep Decisions 2 to 7. The repository's rules name no runner, no
agent and no persona. Before a runner is used for the first time, the
operator checks that:

- it can run an agent's commands in a container on the Mac, or nowhere;
- the harness inside it has its own shell and file tools turned off, so
  commands go only through the container — checked by asking the agent to run
  `hostname` and `ls ~` and seeing the container answer, not the Mac, once per
  harness;
- it can be set up with no credential in the container and no Mac folder
  lent.

A runner that fails one is not used, and changing that needs a new ADR.

**2. An agent's commands run in a container that holds no credential, or
nowhere.** Never on the Mac itself. No kubeconfig, SSH key, GitHub token, or
API key for any infrastructure provider enters the container. The agent can
reach the network, but nothing that would let it authenticate. In Tucano:
the isolated Linux, or none; never the operator's own computer.

**3. The agent clones the public repository inside its container.** No
folder of an existing checkout on the Mac is lent to it.

**4. The agent's work leaves as a patch, and the patch is untrusted input.**
The agent writes `git diff` or `git format-patch` output to a file in its
folder. A session outside the runner — the operator's Claude Code session, or
the operator — takes it through these steps, in order:

1. **Copy the patch out as data.** Read the file's bytes into a new file
   outside the agent's folder. The agent controls that folder, so the "patch"
   could be a symlink to a file elsewhere on the Mac. No git command is run on
   the Mac inside an agent's folder, since its `.git` is the agent's.
2. **List what it touches** with `git apply --stat --summary --check`,
   before applying anything. The list shows every path and any mode change.
3. **Stop at a guardrail path.** A patch that touches `.claude/`, `AGENTS.md`,
   `CLAUDE.md`, `.github/`, `.gitattributes`, anything a check executes (test
   files, `conftest.py`, scripts, `pyproject.toml`), or sets an executable
   bit, is a guardrail change: it needs the operator's go-ahead **before it
   is applied**, not only before merge
   ([`AGENTS.md`](../../AGENTS.md) §6).
4. **Apply it in a fresh worktree** (`git apply` or `git am`), never in the
   operator's main checkout.
5. **Run nothing from it until the whole patch is read** — no test, build,
   hook, or agent started in that worktree. Applying writes files; running
   them is what would put the agent's code on the Mac, which holds the
   credentials.
6. Then commit, push, open the pull request and merge under
   [`AGENTS.md`](../../AGENTS.md) §6.

**5. Connectors are read-only, and none reaches infrastructure.** No
connector a runner holds gets write access to GitHub, and none connects to
the cluster, Cloudflare, Hetzner, Tailscale or the password manager.

**6. Each agent's instructions in the runner say only where the rules are:**
read `AGENTS.md` in the clone before any task and follow it, and for a role
defined in [`.claude/agents/`](../../.claude/agents/), that file too. A
runner may build each turn's context itself — Tucano does — so `AGENTS.md`
is not loaded unless the agent reads it. A rule kept in a runner's
instructions or memory is not a rule of this repository (ADR-0029
Decision 1).

**7. A review by another agent in the runner counts as a separate session**
when the runner starts each reply afresh, with no saved session, and the
reviewer reads the role file.

## Consequences

**Positive.**

- Several agents at once, each with a harness and model — the need ADR-0029
  said would bring back an orchestration layer.
- Their commands cannot touch the Mac's files or credentials.
- Changing runners needs no change to the repository, only the checks in
  Decision 1.

**Negative.**

- **The container reaches the cluster's ports.** The node's API and SSH are
  open to it through the tailnet. Both need credentials it does not hold, but
  the boundary is one control, not two. A service later exposed on the
  tailnet without a login would be open to every agent.
- **The boundary is the operator's setup of the runner.** Nothing in the
  repository can check where an agent's commands run, its connectors, or
  what is lent to it.
- **A patch is applied by hand, and read before anything runs.** Every agent
  change takes several steps from the outside session; an agent cannot open a
  pull request. The reader may itself be a model, so a large patch, or text
  in it written to steer the reader, is a risk that the steps above reduce
  but do not remove.
- **Checks needing credentials stay outside.** `kubectl diff` against staging
  and anything on GitHub run in the outside session or in CI.
- **What an agent reads goes to its model provider,** as with every harness.
  The container holds only the public repository, so this is less than on the
  Mac.
- **Reviewers in one channel share context.** A reviewer that sees the
  conversation that produced the change is less independent than a fresh
  session.
- **The runner is young software.** Tucano was ten days old, at version
  0.2.15, when adopted. A flaw in a runner's container isolation would put the
  Mac's files in reach; Tucano's security model is in its
  [SECURITY.md](https://github.com/eduwxyz/tucano/blob/main/SECURITY.md).

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Nothing
verifies where an agent's commands run, its connectors, what is lent to it,
that it read `AGENTS.md`, or that the outside session followed the patch
steps in Decision 4. For Tucano, that the built-in shell and files are off
for every harness is taken from its README, which confirms it per harness
only for Cursor and Grok; the `hostname` check in Decision 1 is what tests
it. The repository's enforced part is unchanged: the required checks and the
`main` ruleset, which apply to whatever patch the outside session pushes.

## Risks, cost, and reversal

No cost: Tucano is free and open source, and runs on the providers'
subscriptions. Docker Desktop is already on the Mac.

Reversal: stop using a runner; nothing in the repository depends on one.
Running an agent's commands on the Mac, or giving a connector GitHub write
access, needs a new ADR.
