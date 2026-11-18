# ADR-0051 — Handing off to another provider through T3 Code, on the Mac

- **Status:** Proposed; accepted when the operator approves its pull request.
- **Date:** 2026-11-18
- **Decided by:** the operator, in conversation on 2026-11-18: the point of a
  second provider is not to run many agents at once but to keep working when
  Claude's usage limit is reached. Codex and Antigravity both run under the
  operator's own accounts, so "at the end of the day it is the operator
  managing them anyway"; the network-off sandbox was a rule for agents left
  running unattended.
- **Amends:** [ADR-0029](0029-agents-on-more-than-one-harness.md), Decision 3
  (an agent on another harness runs in a network-off sandbox; a harness with
  no sandbox is not used), for the handoff described here only.
- **Related:** [ADR-0032](0032-agents-in-credential-free-containers.md)
  (agent runners, several agents at once) stands unchanged.
  [ADR-0035](0035-agents-use-the-operators-scoped-token.md) and
  [ADR-0036](0036-the-reviewer-as-a-required-check.md) are the gates this
  decision leans on.

## Context and problem

ADR-0029 let Codex continue when Claude's limit is reached, but only inside
Codex's `workspace-write` sandbox with the network off. Such an agent cannot
commit, push or open a pull request, so a second session outside the sandbox
must take its diff through the workflow. When Claude is the session at its
limit, that second session is the operator, who is not a developer. The
handoff stalls exactly when it is needed.

On 2026-11-17 Antigravity, run from the Mac through another app, built and
pushed #124 under the operator's identity — outside ADR-0029's rule as
written. On 2026-11-18 the operator started using T3 Code, a desktop app that
runs Claude Code, Codex and Antigravity side by side on the Mac, each on the
operator's own subscription, with a thread's context copied from one provider
to the next.

What the sandbox protects is the Mac's credentials: a cluster-admin
kubeconfig, an SSH agent that reaches the node and the Pi, and a GitHub token.
Claude Code is kept from misusing them by the deny rules in
`.claude/settings.json`; another harness does not read that file.

## Requirements and constraints

- Work continues on another provider without the operator committing by hand.
- `main` and the cluster stay behind the same gates as today: required
  checks, the reviewer's verdict, and the operator's approval of a guardrail
  change, which no agent's token can give (ADR-0035).
- The repository, not a chat, carries the state a new agent starts from.

## Alternatives considered

**Keep ADR-0029's sandbox.** Safe, and the handoff stays blocked on the
operator committing. Rejected by the operator for the reason above.

**An agent runner in credential-free containers (ADR-0032).** Built for many
agents at once and still needs a session outside it to apply each patch.
Kept for that case; heavier than a sequential handoff needs.

**Copy the deny rules into each harness.** Codex and Antigravity each have
their own way to refuse commands, configured on the operator's machine, which
the repository cannot check. Adopted as advice, not as the boundary
(Decision 3).

## Decision

**1. The handoff runs through T3 Code, one agent at a time.** When Claude's
limit is near, the architect session brings the repository's status up to
date (Decision 4) and the operator continues in T3 Code on Codex or
Antigravity. Development stays sequential; several agents at once remain
ADR-0032's case.

**2. Codex and Antigravity may run there on the Mac, outside a network-off
sandbox, under the operator's accounts.** Such an agent works as the
architect session does: in a worktree, it commits with its own
`Co-Authored-By` line, pushes its branch, opens the pull request and merges
under `AGENTS.md` §6. A reviewer is still never the session that wrote the
change.

**3. The rules still bind it where its harness does not enforce them.** It
never runs a command that `.claude/settings.json` denies — no `kubectl`
write, no `helm` or `argocd` change, no force push or push to `main`, no
destructive `git` — and changes the cluster only through Git and Argo CD. The
operator may mirror those rules in each harness's own command settings; that
is a second layer, not the boundary.

**4. The repository is the handoff.** Before handing off, the architect
session leaves in the repository what the next agent needs: the roadmap's
status, the ADR's notes, and the open pull request's body with what is done
and what is next. The incoming agent starts from `AGENTS.md`, the
milestone's section of `docs/roadmap.md` and the open pull requests, not from
a copy of the chat.

## Consequences

**Positive.**

- Work continues on another provider without the operator committing by hand.
- The handoff that already happened in #124 is now within the rules.
- Status kept in the repository serves every agent and the operator alike.

**Negative.**

- **The Mac's credentials are within reach of an agent that does not read the
  deny rules.** It could, by mistake, run `kubectl delete` with the
  cluster-admin kubeconfig or reach the node over SSH. Decision 3 is an
  instruction, not an enforcement. The operator accepts this risk for a
  sequential handoff they watch.
- **The GitHub gates are the boundary for `main`.** They hold: a required
  check, the reviewer in CI, and CODEOWNERS with the `guardrail` environment,
  which no agent's token can approve.
- **What each harness sends to its provider** includes what it reads on the
  Mac, as with Claude Code today (ADR-0029, Consequences).

## Risks, cost, and reversal

- **Cost:** none in infrastructure; the subscriptions are the operator's.
- **Risk:** a direct change to the cluster by an agent without deny rules.
  Argo CD's self-heal reverts drift in what it manages; a deleted Secret or
  volume is not reverted. Recovery is the runbook's.
- **Reversal:** a new ADR restoring ADR-0029's Decision 3 for every harness
  other than Claude Code, and the paragraph in `AGENTS.md` §10 removed.
- **Reconsider when** an agent changes the cluster or `main` outside the
  gates, or agents are left running unattended.
