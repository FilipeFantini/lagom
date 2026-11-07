# ADR-0037 — The reviewer runs on Sonnet, to spend less of the subscription

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07, with the Claude subscription's
  usage near its limit.
- **Amends:** [ADR-0036](0036-the-reviewer-as-a-required-check.md)
  Decision 6 — the model, not the mechanism.

## Context and problem

[ADR-0036](0036-the-reviewer-as-a-required-check.md) made the reviewer a
required check and chose the Opus model, since it is the only reader of the
code. It runs on the operator's Claude subscription, on every push to every
pull request. On the day the check became required, the subscription's usage
was near its limit; when the limit is reached the check fails and nothing
merges until it resets.

## Alternatives considered

**Keep Opus.** The strongest reader, at the highest usage per review.
Rejected for now: a reviewer that blocks every merge for want of usage is a
gate nobody can pass.

**Review only some pull requests.** Rejected: "every pull request is
reviewed" is the point of ADR-0036.

## Decision

The `agent review` job runs on **Sonnet** (`--model sonnet` in
[`agent-review.yml`](../../.github/workflows/agent-review.yml)), the model
[`.claude/agents/reviewer.md`](../../.claude/agents/reviewer.md) names.
Everything else in ADR-0036 stands.

## Consequences

- Each review spends less of the subscription, so more pull requests pass
  before the limit.
- **A weaker reader of the code, and the only one.** Sonnet may miss a
  defect Opus would catch; the operator does not read code, so a miss
  reaches `main` unless a required check catches it.

## Risks, cost, and reversal

No cost change beyond the subscription. Reversal: `--model opus` in the
workflow, a guarded change, with a new ADR.
