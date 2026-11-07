# ADR-0036 — The reviewer runs in CI as a required check

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: they do not read code and
  rely on the reviewer and the gates, so "every pull request is reviewed"
  must be something GitHub enforces; a guardrail change is approved only
  with a clear explanation of what it changes and touches. The mechanism is
  the architect's proposal, accepted with this ADR.
- **Amends:** [ADR-0029](0029-agents-on-more-than-one-harness.md)
  Decision 4 and [ADR-0035](0035-agents-use-the-operators-scoped-token.md)
  Decision 5 — the reviewer's APPROVE stops being an instruction.
- **Amended by:** [ADR-0037](0037-the-reviewer-runs-on-sonnet.md) — Decision 6:
  the reviewer runs on Sonnet, not Opus.
  [ADR-0038](0038-leaner-agent-gates.md) — Decisions 1, 2 and 4: Markdown-only
  changes pass unreviewed, no re-run on an edit, and the For the operator
  section no longer blocks.

## Context and problem

Since [ADR-0029](0029-agents-on-more-than-one-harness.md) any agent may
merge once the required checks are green and a reviewer returned APPROVE. The
APPROVE is an instruction: GitHub does not see it, so an agent that skips the
review can still merge. Under
[ADR-0035](0035-agents-use-the-operators-scoped-token.md) agents merge
their own unguarded pull requests, and the operator — who does not read
code — approves guarded ones on a plain-language section. Both rest on a
review that nothing enforces, and on a section that nothing checks.

## Requirements and constraints

- No pull request merges without a reviewer's APPROVE, whoever opened it.
- A pull request cannot change the review that judges it.
- A guarded pull request without a usable **For the operator** section does
  not merge.
- No code from a pull request runs where a secret is in reach.
- No cost beyond the subscriptions the operator already has.

## Alternatives considered

**A reviewer agent in the runner.** What ADR-0032 Decision 7 allows. Rejected
as the gate: the author's token can merge without waiting for it.

**The review in `ci.yml`, on `pull_request`.** The simplest workflow.
Rejected: on `pull_request` GitHub runs the workflow as the pull request
edits it, so a change could rewrite its own reviewer's prompt; and a secret
would sit next to code the pull request supplies.

**GitHub's own Copilot code review as a required review.** Rejected: it
comments but does not block, and it does not read this repository's role
file or know its guarded paths.

**One required approval from the operator on every pull request.** Rejected:
the operator does not read code, so the approval would not add a review.

## Decision

**1. A workflow, [`agent-review.yml`](../../.github/workflows/agent-review.yml),
runs the role in [`.claude/agents/reviewer.md`](../../.claude/agents/reviewer.md)
on every pull request** through Anthropic's `claude-code-action`, pinned by
commit to v1.0.262 (two weeks old when adopted). Its job, `agent review`,
fails unless the verdict is APPROVE, and is added to the `main` ruleset's
required checks.

**2. It runs on `pull_request_target`, for pull requests into `main`
only,** so the workflow, the prompt and the role come from `main`: a pull
request cannot weaken the review that judges it before it merges. On another
base they would come from that branch, which the agents' token can push; a
change of base to `main` re-runs the review (`edited`). Because that job holds a secret, nothing from the pull
request runs there: its files are checked out into `pr-head/` as data, its
diff and description are fetched into files by a step before the reviewer
starts, and the reviewer's tools are `Read` scoped to the workspace
(`Read(./**)`), `Grep` and `Glob` — no shell, no network, and no file outside
the workspace, `/proc` included. Claude Code's loading of `CLAUDE.md` files is
turned off (`CLAUDE_CODE_DISABLE_CLAUDE_MDS`): one placed in `pr-head/` would
otherwise be read as project instructions, letting the pull request instruct
its reviewer. The reviewer reads its role and `AGENTS.md` explicitly. The
other required checks, on `pull_request` with no secret, run the code,
linters and tests.

**3. A pull request from a fork fails the check** rather than skipping it — a
skipped required check counts as passed. The operator decides on those.

**4. The reviewer enforces the operator's section.** A change is guarded when
a changed file matches `.github/CODEOWNERS` on `main`. A guarded change
needs a **For the operator** section — what changes in behaviour, what it
touches, what could go wrong, how to undo it, in plain words; for a pin, the
code merged since the previous pin — and a missing, vague or mismatched
section is a major finding, which fails the check.

**5. The verdict is posted on the pull request** by a workflow step from the
reviewer's structured output, not by the reviewer itself, so the operator
reads the summary where they approve. The comment is the reviewer's one
channel to the outside, so the step refuses — and fails the job — when the
text holds what looks like an Anthropic or GitHub token, and the action's
public step summary is turned off. The workflow token has
`pull-requests: write` for that comment and cannot approve: the repository
does not let Actions approve pull requests.

**6. It authenticates with the operator's Claude subscription,** through a
`CLAUDE_CODE_OAUTH_TOKEN` repository secret made with `claude setup-token`,
and uses the Opus model: it is the only reader of the code, so it gets the
strongest model, at the cost of more of the subscription's usage.

**7. The order of setup:** the workflow on `main`, then the secret, then one
pull request showing the check run with a verdict, and only then the check
added to the ruleset. A required check that never reports blocks every pull
request.

## Consequences

**Positive.**

- "Every pull request is reviewed" is enforced by GitHub, for agents, for
  the operator, and for the operator's Claude sessions.
- The operator sees, on each pull request, a plain-language verdict, and for
  guarded ones a section written for them.
- A change to the reviewer's prompt or role is guarded (`.github/`,
  `.claude/`) and only takes effect after it merges.

**Negative.**

- **The gate is a model.** A reviewer can be wrong, or be steered by text the
  author wrote to mislead it — hidden in an HTML comment or invisible
  characters too, since the diff and description reach it unsanitised. The
  prompt treats such text as a finding; that is not a guarantee. Author and
  reviewer may share a model family's blind spots.
- **A secret sits in a job triggered by pull requests.** A token in the
  subscription's name, in the environment of a process that reads text an
  agent wrote. The reviewer cannot read outside the workspace, has no shell
  or network tool, and its one outward channel — the comment — is checked
  for tokens; fork pull requests are refused. A flaw in the action, or a
  token encoded past the check, would still put it in reach. Revoking it is
  one action on the operator's Claude account.
- **It spends the subscription.** Each pull request costs a review, and each
  push to it another; when the usage limit is reached the check fails and
  nothing merges until it resets — failing closed, by design.
- **It reads, it does not run.** A defect that only shows when the code runs
  is the other checks' job; the reviewer marks what it cannot see as
  PLAUSIBLE.
- **The action is young and moves fast.** Pinned by commit; an upgrade is a
  guarded change.

## What it does not check

([engineering-protocol](../engineering-protocol.md) §18 rule 5.) That the
verdict is right; that the For the operator section is true, beyond the
reviewer's reading of the diff; anything that needs the code to run. Whether
GitHub attaches a `pull_request_target` job's result to the pull request's
head commit as a required check is shown by the test in Decision 7, not
assumed.

## Risks, cost, and reversal

No cost beyond the Claude subscription, whose usage it shares.

Reversal: remove `agent review` from the ruleset's required checks; the
reviewer's APPROVE returns to an instruction. Removing the workflow or the
section requirement needs a new ADR.
