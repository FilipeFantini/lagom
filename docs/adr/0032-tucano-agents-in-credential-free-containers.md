# ADR-0032 — Tucano agents work in containers that hold no credential

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: the project's agents —
  several, in roles that may change — run through Tucano, each on Tucano's
  isolated Linux computer.
- **Amends:** [ADR-0029](0029-agents-on-more-than-one-harness.md) — reverses
  its rejection of Tucano, and adds a second boundary to its Decision 3.

## Context and problem

[Tucano](https://tucano.sh) is a desktop app on the operator's Mac that runs a
team of agents in chat channels. Each agent picks a harness already installed
on the Mac (Claude Code, Codex and others), a model, a set of tools, and a
**computer** — the place where its commands run. The computer is one of three:

- **None** — no shell at all.
- **Isolated Linux** — a Docker container that Tucano starts for that agent,
  holding only the agent's own folder and the folders lent to it.
- **This computer** — the operator's Mac, as the operator.

ADR-0029 rejected Tucano on 2026-11-05: eight days old, and its Claude Code
harness leaves the user's permissions out. Both facts still hold. Its README,
read on 2026-11-07 at v0.2.15, says the Claude Code harness reads only the
`env` block and `apiKeyHelper` of `~/.claude/settings.json`; "hooks, plugins
and permissions stay out". So `.claude/settings.json` binds no Tucano agent.

What changed is the computer choice. ADR-0029's boundary for another harness
is a sandbox with no network. A Tucano container has network, so this ADR
needs a different boundary.

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
- The rules stay in [`AGENTS.md`](../../AGENTS.md), not in Tucano's
  per-agent instructions or memory.
- The agents' names and roles change; the rule must not depend on them.

## Alternatives considered

**This computer.** Each agent's commands run on the Mac, which holds a
cluster-admin kubeconfig, an SSH agent and a GitHub token with `workflow`
scope. With the permissions left out, nothing stands between an agent and
those. Rejected: the case ADR-0029 rejected Paseo on the Mac for.

**Lend the operator's checkout to the container.** Simple: the agent edits the
files the operator's Claude Code session commits from. Rejected: the agent can
write `.git` too. A hook in `.git/hooks`, or a `core.fsmonitor` or
`core.hooksPath` line in `.git/config`, runs on the Mac the next time any
git command runs there, and neither shows in a diff. Codex keeps `.git`
read-only for this reason.

**A Docker network that cannot reach the tailnet.** Would make the boundary
the network, as for Codex. Not available: Tucano starts the containers and
offers no network setting. Revisit if it does.

**GitHub write access through a Tucano connector.** Tucano can hold a GitHub
token and turn a merge into an approval card. Rejected for now: the token
would be the operator's identity in every agent's hands. The trigger is the
one ADR-0029 deferred: a GitHub account of its own for agents.

## Decision

**1. Tucano is a harness for this repository,** for any number of agents in
any roles. The repository records no agent names or personas; they change.

**2. Every Tucano agent's computer is Isolated Linux, or None.** Never This
computer. The boundary is that the container holds no credential: no
kubeconfig, no SSH key, no GitHub token, no API key for any infrastructure
provider, and no lent folder from the Mac's home that holds one. The agent
can reach the network, but nothing that would let it authenticate.

**3. The agent clones the public repository inside its own computer.** No
folder of an existing checkout on the Mac is lent to it.

**4. The agent's work leaves as a patch.** The agent writes `git diff` or
`git format-patch` output to a file in its folder. A session outside
Tucano — the operator's Claude Code session, or the operator — reads the
patch, applies it in its own checkout (`git apply` or `git am`), and then
commits, pushes, opens the pull request and merges under
[`AGENTS.md`](../../AGENTS.md) §6. No git command is run on the Mac inside an
agent's folder, since its `.git` is the agent's.

**5. Connectors are read-only, and none reaches infrastructure.** No Tucano
connector gets write access to GitHub, and none connects to the cluster,
Cloudflare, Hetzner, Tailscale or the password manager.

**6. Each agent's Tucano instructions say only where the rules are:** read
`AGENTS.md` in the clone before any task and follow it, and for a role defined
in [`.claude/agents/`](../../.claude/agents/), that file too. Tucano builds
each turn's context itself, so `AGENTS.md` is not loaded unless the agent
reads it. A rule kept in Tucano's instructions or memory is not a rule of
this repository (ADR-0029 Decision 1).

**7. A review by another Tucano agent counts as a separate session.** Each
Tucano reply starts the harness afresh, with no saved session, and the
reviewer reads the role file.

## Consequences

**Positive.**

- Several agents, in channels, with a harness and model each — the need
  ADR-0029 said would bring back an orchestration layer.
- Their commands cannot touch the Mac's files or credentials.
- The boundary does not depend on the harness: Claude Code, Codex or Pi
  inside Tucano all run in the same credential-free container.

**Negative.**

- **The container reaches the cluster's ports.** The node's API and SSH are
  open to it through the tailnet. Both need credentials it does not hold, but
  the boundary is one control, not two. A service later exposed on the
  tailnet without a login would be open to every agent.
- **The boundary is the operator's choice in Tucano's settings.** Nothing in
  the repository can check an agent's computer setting, connectors or lent
  folders.
- **A patch is applied by hand.** Every agent change takes a step from the
  outside session; an agent cannot open a pull request.
- **Checks needing credentials stay outside.** `kubectl diff` against staging
  and anything on GitHub run in the outside session or in CI.
- **What an agent reads goes to its model provider,** as with every harness.
  The container holds only the public repository, so this is less than on the
  Mac.
- **Reviewers in one channel share context.** A Tucano reviewer sees the
  conversation that produced the change, so its independence is weaker than a
  fresh session's.
- **Tucano is young.** Ten days old, version 0.2.15, at the time of
  writing. A flaw in its container isolation would put the Mac's files in
  reach; its security model is in its
  [SECURITY.md](https://github.com/eduwxyz/tucano/blob/main/SECURITY.md).

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Nothing
verifies an agent's computer setting, its connectors, what is lent to it, or
that it read `AGENTS.md`. The repository's enforced part is unchanged: the
required checks and the `main` ruleset, which apply to whatever patch the
outside session pushes.

## Risks, cost, and reversal

No cost: Tucano is free and open source, and runs on the providers'
subscriptions. Docker Desktop is already on the Mac.

Reversal: stop using Tucano; nothing in the repository depends on it. Moving
an agent to This computer, or giving a connector GitHub write access, needs a
new ADR.
