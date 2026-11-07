# ADR-0038 — Leaner agent gates: the guarded list is the security core, and the reviewer runs less and blocks less

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-07: nothing critical runs on this
  platform, the aim is security, and basic protections are enough. What
  guarantees quality at no cost stays; what is protection in excess goes.
  The reviewer is the one gate that costs money — it spends the Claude
  subscription — so it must run less and not send pull requests back for
  small things.
- **Amends:** [ADR-0035](0035-agents-use-the-operators-scoped-token.md)
  Decisions 3 and 4, and [ADR-0036](0036-the-reviewer-as-a-required-check.md)
  Decisions 1, 2 and 4.

## Context and problem

Within a day of the agent gates going live, about half of the guarded list
(ADR-0035 Decision 3) turned out to protect process and quality rather than
security: the ADRs, roadmap, architecture and contributing docs, every
`pyproject.toml` and `uv.lock`, and the lint, type and test configuration.
Each change there waited for the operator's click. The reviewer
(ADR-0036) ran on every push and every description edit, sent back a
pull request for documentation points, and treated a missing For the
operator section as a major finding — though the operator, who approves on
that section, can simply decline.

The seven CI checks cost nothing — the repository is public, so GitHub
Actions minutes are free — run in parallel in a minute or two, and are
deterministic: the one layer the operator can trust without reading code.

## Decision

**1. The guarded list is the security core** ([`.github/CODEOWNERS`](../../.github/CODEOWNERS)):
`platform/infra/` (what reaches the cluster), `.github/` and `tools/` (the
gates and the checks they run), `.gitleaks.toml` and `.gitleaksignore` (the
secret scan), and `AGENTS.md`, `CLAUDE.md` and `.claude/` (the instructions
agents and the reviewer follow). Documentation, dependencies and lint
configuration leave it.

**2. Editing a pull request's description re-runs neither gate.** The
retargeting bypass that `edited` closed (ADR-0035 Decision 4) stays closed:
both gates run only for pull requests into `main`, so a pull request whose
base becomes `main` has no result for its commit and stays blocked until its
next push, which runs both gates from `main`. Closed, at no cost; and an
approval is not lost to a wording fix.

**3. A change to Markdown only, outside `.claude/`, `AGENTS.md` and
`CLAUDE.md`, passes `agent review` without calling the model.** Markdown
does not run. Renamed files count by both names, and a file list GitHub cut
short (over 3000 files) is never treated as Markdown only.

**4. The reviewer blocks only what would make the platform wrong or
unsafe:** a correctness defect that breaks something, a data-integrity or
security defect, or a resource-budget defect. Documentation, wording,
process and stronger-test suggestions are minor at most and never block; a
missing or mismatched For the operator section is reported in the summary
for the operator to weigh. It may take at most 20 turns, down from 40.

**5. The seven CI checks stay.** They are free, fast and deterministic.

## Consequences

- Most agent pull requests — code, docs, dependencies — merge on CI and the
  reviewer alone; the operator is called for the cluster, the gates and the
  agents' instructions.
- Fewer reviews, each cheaper: no review for documentation, none on
  description edits, and a shorter budget per review.
- **A dependency change merges without the operator.** It runs in CI, which
  holds no secret, and reaches the cluster only through a guarded pin; but
  it runs on the operator's Mac whenever tests run there after the merge.
- **The rules documents can change without the operator.** An agent can
  edit an ADR, the roadmap or the runbook; the reviewer reads it, and the
  instructions that bind agents (`AGENTS.md`, `.claude/`) stay guarded.
- **A retargeted pull request needs one more push** before it can merge.

## Risks, cost, and reversal

Lowers the subscription spent on reviews; no other cost. Reversal: add paths
back to `CODEOWNERS`, or `edited` back to the workflows' triggers — guarded
changes, with a new ADR.
