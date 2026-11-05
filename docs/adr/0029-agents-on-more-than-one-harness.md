# ADR-0029 — Agents on more than one harness: one rulebook, a sealed workstation, and who may merge

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decided by:** the operator, on 2026-11-05: Paseo as the place to work
  with every agent, Apple `container` to isolate it, Claude Code and Codex in
  it with room for more, and any agent allowed to merge after review.
- **Supersedes:** [ADR-0013](0013-agent-driven-development-guardrails.md)
  rule 3 (autonomy earned per class of change), and amends its rule 2 (see
  Decision 4).

## Context and problem

Every agent here has run on one **harness** — the program that runs a model
as an agent, gives it tools, and enforces what it may do. That harness is
Claude Code. When its usage limit is reached, work stops until the limit
resets. The operator wants to continue on another provider (Codex now, others
later) and to work with all of them from one place instead of a terminal per
tool.

Four things in the repository assumed one harness:

1. **The rules were in `CLAUDE.md`,** which only Claude Code loads. Codex,
   OpenCode, Pi and most other harnesses read `AGENTS.md`.
2. **The permission layer was Claude Code's.** The deny rules in
   `.claude/settings.json` (destructive git, cluster writes) bind no other
   harness. Pi has no permission prompts at all.
3. **The operator's Mac holds the platform's keys.** A kubeconfig with
   cluster-admin rights, an SSH agent, a GitHub token with `repo` and
   `workflow` scopes, and a place on the tailnet, where Dagster's UI has no
   login ([ADR-0028](0028-dagster-ui-over-the-tailnet.md)). Any agent running
   on the Mac can use all of them, whatever its instructions say.
4. **Who may merge was recorded in two contradictory places.** The repository
   said no agent merges until ADR-0013 rule 3's criteria are met; on
   2026-10-27 the operator had authorized the Claude session to merge after a
   reviewer's APPROVE with CI green. That authorization lived only in Claude's
   private memory, which other agents do not read.

## Requirements and constraints

- Nothing an agent on a new harness does can depend on rules it never loads.
- The operator's Mac stays clean: whatever is installed can be listed and
  removed in a few commands.
- An agent's authority is what its environment can reach, not what its
  instructions ask — the only boundary that holds across harnesses.
- No resident cost on the node: this runs on the operator's Mac.
- One source of truth for the rules ([`AGENTS.md`](../../AGENTS.md) §5).

## Alternatives considered

**Paseo on the Mac directly.** The simplest setup, and the agents would see
the Mac's tools. Rejected: they would also see its kubeconfig, SSH agent and
GitHub token, and only Claude Code would be held back by deny rules.

**Deny rules copied into each harness's own format.** Codex and OpenCode have
permission settings, so the list could be repeated. Rejected as the boundary:
copies drift, Pi has no such layer, and a deny list matches commands as
written, so it stops mistakes, not a determined bypass (CONTRIBUTING already
says so of Claude Code's).

**[Tucano](https://tucano.sh).** Agents in chat channels, approvals tied to
the exact action. Rejected for now: eight days old when evaluated, and its
README says its Claude Code harness leaves the user's permissions out.

**Grok Bot (xAI).** Rejected: it runs in xAI's cloud, so the platform's
credentials would have to live there, and its bots share one machine and one
set of logins, so a reviewer would have the author's authority.

**Docker Desktop, OrbStack, or Colima as the runtime.** All work. Apple's
`container` was chosen because it is one signed package from Apple with an
uninstall script, and runs each container as its own small virtual machine.
Colima needs several Homebrew packages; Docker Desktop and OrbStack are larger
and not open source.

**A separate GitHub account for agents.** It would give agents their own
identity, let the operator approve their pull requests, and make the token
revocable without touching the operator's account. Deferred: more to
administer for one operator. Its trigger is a second developer (M7), or an
agent action that must be attributed apart from the operator.

## Decision

**1. `AGENTS.md` is the rulebook.** `CLAUDE.md` holds one line,
`@AGENTS.md`, which Claude Code expands into the file's content. Rules that
had lived only in Claude's memory — plain explanations for infrastructure,
checking a value against the ADRs, minimal public exposure, who merges — move
into it. A rule every agent must follow is never kept in one agent's memory.

**2. The roles stay in `.claude/agents/` as plain instructions.** Claude Code
runs them as subagents. Another harness runs a role as a separate agent
session told to read and follow the role file. A reviewer is never the session
that wrote the change.

**3. Agents other than the operator's own Claude Code session run in the agent
workstation** ([`tools/agents/`](../../tools/agents/README.md)): Paseo and the
agent CLIs in one container under Apple `container`, with named volumes for
state and nothing mounted from the Mac.

| Layer | What it does |
|---|---|
| no host mounts | the Mac's files, kubeconfig and SSH keys are not in the container |
| blackhole routes for `100.64.0.0/10` and `fd7a:115c:a1e0::/48` | the tailnet is unreachable; set as root before Paseo starts, and the user `paseo` has no capability to remove them |
| a fine-grained GitHub token | this repository only; *Contents* and *Pull requests* write; no *Workflows*, no *Administration* |
| Paseo's port on `127.0.0.1`, behind a password | no other device opens the web page |
| Paseo's relay off (its default until pairing) | nothing reaches the daemon through Paseo's servers |

The operator's own Claude Code session on the Mac keeps its current access:
it is the one that runs the runbook's cluster reads.

**4. Merging.** Any agent, on any harness, may merge a pull request once every
required check is green and a reviewer returned APPROVE. A guardrail change —
CI, agent definitions or instructions, permissions, RBAC, secrets, the agent
workstation — also needs the operator's explicit go-ahead for that pull
request. This replaces ADR-0013 rule 3: autonomy is no longer earned class by
class; the reviewer and the required checks are the gate for every class.
ADR-0013 rule 2 stands in substance — a guardrail change still needs the
operator — but as an explicit go-ahead per pull request, not necessarily the
operator's own merge. Rules 1 and 4 stand unchanged.

## Consequences

**Positive.**

- Work continues when one provider's limit is reached, from one web page.
- The boundary for agents on other harnesses holds whatever their
  instructions say: they cannot reach what is not in the container.
- One rulebook for every agent, in the repository.
- Adding an AI is one pinned line in the `Containerfile` and a login.

**Negative.**

- **Agents in the workstation cannot read the cluster.** `kubectl get`,
  `logs` and `diff` stay with the operator's Claude Code session on the Mac.
  A read-only kubeconfig for the workstation is the next step when an agent
  there needs cluster state; it is a guardrail change (RBAC and a credential).
- **The token can merge.** With the ruleset requiring no approval, any agent
  holding it can merge a pull request whose checks pass. The reviewer's
  APPROVE is enforced by instruction only. That is the trade-off the operator
  accepted for throughput.
- **A reviewer may share the author's blind spots.** Two sessions of the same
  model family are not fully independent; nothing requires a reviewer from
  another provider yet.
- **Codex runs without deny rules.** Inside its own clone it can run
  `git reset --hard` or delete branches on GitHub (not `main`). The damage is
  bounded by the clone and by the ruleset, not prevented.
- **One more tool to keep current.** Paseo is pre-1.0 (0.15.2), and the image
  pins it, the agent CLIs, `uv`, `helm` and `gh`; nothing bumps them
  automatically.

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) Listed in
full in [`tools/agents/README.md`](../../tools/agents/README.md#what-this-does-not-isolate):
the token's own authority, agents sharing one home volume, open internet
egress, networks other than the tailnet, and deny rules applying to Claude
Code only. Whether GitHub also refuses to *merge* a pull request that changes
`.github/workflows/` with a token lacking *Workflows* was not tested; the
refusal of a *push* is GitHub's documented behaviour.

## Risks, cost, and reversal

Cost: no money beyond the providers' subscriptions. On the Mac: about 6 GB of
disk, and up to 4 GB of memory and 4 CPUs while the container runs.

Reversal: stop the container, delete its volumes and image, and run Apple's
uninstall script ([`tools/agents/README.md`](../../tools/agents/README.md#remove-everything));
revoke the token. `AGENTS.md` stays useful for any harness either way. Going
back on the merge rule needs a new ADR.
