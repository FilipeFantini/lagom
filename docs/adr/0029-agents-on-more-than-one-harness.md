# ADR-0029 — Agents on more than one harness: one rulebook, and who may merge

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decided by:** the operator, on 2026-11-05: other AIs (Codex first) to keep
  working when Claude's usage limit is reached; any agent allowed to merge
  after review; and, after trying it, no orchestration layer on top.
- **Supersedes:** [ADR-0013](0013-agent-driven-development-guardrails.md)
  rule 3 (autonomy earned per class of change), and amends its rule 2 (see
  Decision 4).
- **Amended by:** [ADR-0032](0032-agents-in-credential-free-containers.md)
  — agent runners (Tucano, rejected below, first) are adopted with a second
  boundary for Decision 3: a container that holds no credential.
  [ADR-0035](0035-agents-push-as-their-own-github-identity.md) — the
  deferred GitHub account of its own for agents is adopted.

## Context and problem

Every agent here has run on one **harness** — the program that runs a model
as an agent, gives it tools, and enforces what it may do. That harness is
Claude Code. When its usage limit is reached, work stops until the limit
resets. The operator wants to continue on another provider: Codex now, others
later.

Three things in the repository assumed one harness:

1. **The rules were in `CLAUDE.md`,** which only Claude Code loads. Codex,
   OpenCode, Pi and most other harnesses read `AGENTS.md`.
2. **The permission layer was Claude Code's.** The deny rules in
   `.claude/settings.json` (destructive git, cluster writes) bind no other
   harness, and the operator's Mac holds a cluster-admin kubeconfig, an SSH
   agent, a GitHub token with `workflow` scope, and a place on the tailnet,
   where Dagster's UI has no login
   ([ADR-0028](0028-dagster-ui-over-the-tailnet.md)).
3. **Who may merge was recorded in two contradictory places.** The repository
   said no agent merges until ADR-0013 rule 3's criteria are met; on
   2026-10-27 the operator had authorized the Claude session to merge after a
   reviewer's APPROVE with CI green. That authorization lived only in Claude's
   private memory, which other agents do not read.

## Requirements and constraints

- Nothing an agent on a new harness does can depend on rules it never loads.
- An agent on a harness without the deny rules must not be able to change the
  cluster or GitHub.
- No new software on the operator's Mac beyond the agent tools themselves.
- One source of truth for the rules ([`AGENTS.md`](../../AGENTS.md) §5).

## Alternatives considered

**Paseo, an orchestration layer, in a sealed container.** Built and removed
on 2026-11-05. Paseo runs Claude Code, Codex and others from one web page and
the phone. Run in an Apple `container` VM with nothing mounted from the Mac,
the tailnet blackholed and a GitHub token without workflow permission, it gave
every harness the same boundary. The operator judged it too much machinery
for the need, and removed it with its runtime. If the need returns — several
agents at once, or work from the phone — this is the design to start from.

**Paseo on the Mac directly.** Simpler, and with the phone app. Rejected: the
agents it starts run as the operator, with the kubeconfig, the SSH agent and
the GitHub token in reach, and a paired phone becomes a remote shell with
those credentials.

**[Tucano](https://tucano.sh).** Agents in chat channels. Rejected for now:
eight days old when evaluated, and its README says its Claude Code harness
leaves the user's permissions out.

**Grok Bot (xAI).** Rejected: it runs in xAI's cloud, so the platform's
credentials would have to live there, and its bots share one machine and one
set of logins.

**The deny rules copied into each harness's format.** Rejected as the
boundary: copies drift, Pi has no such layer, and a deny list matches commands
as written, so it stops mistakes, not a bypass.

**A GitHub account of its own for agents.** It would give agents an identity
apart from the operator's and a revocable token. Deferred: its trigger is a
second developer (M7), or an agent action that must be attributed apart from
the operator.

## Decision

**1. `AGENTS.md` is the rulebook.** `CLAUDE.md` holds one line,
`@AGENTS.md`, which Claude Code expands into the file's content. Rules that
had lived only in Claude's memory — plain explanations for infrastructure,
checking a value against the ADRs, minimal public exposure, who merges, commit
attribution — move into it. A rule every agent must follow is never kept in
one agent's memory.

**2. The roles stay in `.claude/agents/` as plain instructions.** Claude Code
runs them as subagents. Another harness runs a role as a separate session told
to read and follow the role file. A reviewer is never the session that wrote
the change.

**3. An agent on another harness runs inside that harness's own sandbox,
without network access for its commands.** For Codex that is the
`workspace-write` sandbox mode with `network_access` left off: commands may
read files and edit the checkout they were started in, and cannot open a
connection. So they cannot reach the cluster, GitHub, or the tailnet, whatever
credentials sit on the Mac. Codex also keeps `.git`, `.agents` and `.codex`
read-only in that mode, so the agent cannot commit — nor rewrite its own
instructions. A session outside the sandbox — the operator's Claude Code
session, or the operator — reads the diff, commits, pushes, opens the pull
request, and merges. A harness with no sandbox (Pi, today) is not used on
this repository.

**4. Merging.** Any agent that can reach GitHub, on any harness, may merge a
pull request once every required check is green and a reviewer returned
APPROVE; a sandboxed agent (Decision 3) cannot, so the session that pushed
merges. A guardrail change —
CI, agent definitions or instructions, permissions, RBAC, secrets — also needs
the operator's explicit go-ahead for that pull request. This replaces ADR-0013
rule 3: autonomy is no longer earned class by class; the reviewer and the
required checks are the gate for every class. ADR-0013 rule 2 stands in
substance — a guardrail change still needs the operator — but as an explicit
go-ahead per pull request, not necessarily the operator's own merge. Rules 1
and 4 stand unchanged.

## Consequences

**Positive.**

- Work continues when one provider's limit is reached.
- One rulebook for every agent, in the repository.
- Nothing new runs on the Mac; each harness brings its own boundary.

**Negative.**

- **The sandbox limits writes and network, not reads.** A sandboxed agent can
  still read the kubeconfig and other files, and what it reads goes to its
  model provider — as with Claude Code today.
- **The boundary is the harness's configuration.** If the sandbox is turned
  off or network access enabled, the boundary is gone; nothing in the
  repository can check how a harness on the operator's Mac is configured.
- **A sandboxed agent's work needs a second session.** It cannot commit,
  push, or open a pull request; the operator's Claude Code session or the
  operator does, after reading the diff.
- **A sandboxed agent cannot run every check.** Checks that download charts
  or packages (`helm template`, a first `uv sync`) need the network; the
  session that pushes runs them, and CI runs all of them.
- **The reviewer's APPROVE is enforced by instruction only.** The ruleset
  requires no approval, so a token that can merge can merge without it. That
  is the trade-off the operator accepted for throughput.
- **A reviewer may share the author's blind spots.** Two sessions of the same
  model family are not fully independent; nothing requires a reviewer from
  another provider yet.

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Nothing
verifies that a non-Claude harness runs with its sandbox on, that an agent
merged only after a reviewer's APPROVE, or that the reviewer was a separate
session. The required checks and the `main` ruleset are the only enforced
part of the merge rule.

## Risks, cost, and reversal

No cost beyond the providers' subscriptions.

Reversal: going back on the merge rule needs a new ADR. `AGENTS.md` is useful
for any harness either way. Adding an orchestration layer later is additive;
the Paseo design above is the starting point.
