# ADR-0049 — The reviewer runs on Dependabot's pull requests: the action allows `dependabot[bot]`, and no Dependabot secret

- **Status:** Accepted
- **Date:** 2026-11-17
- **Decided by:** the operator, on 2026-11-17: Dependabot's pull requests
  still failed `agent review` after the Dependabot secret of ADR-0048 was
  set, with both the old and a new token. The cause and the fix are the
  architect's, accepted with this ADR.
- **Supersedes:** [ADR-0048](0048-dependency-and-code-scanning-for-the-public-repository.md)
  Decision 2.

## Context and problem

ADR-0048 Decision 2 said that `agent review` failed on Dependabot's pull
requests (#107, #108) because a run Dependabot triggers gets no repository
secret, and stored the Claude token as a Dependabot secret. The diagnosis
read an empty `ANTHROPIC_API_KEY` in the log as the missing token. That
variable is empty on every run: the reviewer authenticates with
`CLAUDE_CODE_OAUTH_TOKEN`.

The first attempt of run 43969125110, before any Dependabot secret
existed, shows `CLAUDE_CODE_OAUTH_TOKEN: ***`, set. A `pull_request_target`
run into `main` gets the repository's secrets whoever triggered it. The
error in every attempt is the action's own:

> Workflow initiated by non-human actor: dependabot (type: Bot). Add bot to
> allowed_bots list or use '*' to allow all bots.

`anthropics/claude-code-action` refuses to run when the account that
triggered the workflow is not a human, unless that account is in its
`allowed_bots` input (`src/github/validation/actor.ts` at the pinned
commit). It compares names without the `[bot]` suffix.

## Decision

**1. `agent-review.yml` passes `allowed_bots: "dependabot[bot]"`** to the
action. Only Dependabot is added, not `*`. The account that triggered a run
is set by GitHub, so a pull request cannot claim to be Dependabot's.

**2. No Dependabot secret.** The job already gets the repository's token.
The copy made on 2026-11-16 is deleted, so the token is stored once.

## Consequences

- A Dependabot pull request gets a verdict and, when it touches no guarded
  path, merges like an agent's. A bump of an action touches `.github/` and
  waits for the operator.
- **The reviewer reads text a dependency's maintainer wrote.** Dependabot's
  descriptions quote upstream release notes. They reach the reviewer as data,
  like any pull request's description (ADR-0036 Decision 2), and the
  reviewer has no shell, no network and no write access.
- ADR-0048's other decisions stand.
