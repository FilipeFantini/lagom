# ADR-0048 — Dependency and code scanning for the public repository: Dependabot, CodeQL, pinned actions, approval for outside contributors' CI

- **Status:** Accepted
- **Date:** 2026-11-16
- **Decided by:** the operator, on 2026-11-16: with auto-merge on and the
  repository public, nothing malicious may reach `main` and, through it, the
  node. They chose GitHub's own protections over two external tools (Strix
  and Cloudflare's `security-audit-skill`), turned on Dependabot and CodeQL
  themselves that day, and asked for this ADR. Decisions 2, 4 and the
  merge gate in Decision 3 follow from what those two found on their first
  run; they are the architect's proposal, accepted with this ADR.
- **Amends:** [ADR-0036](0036-the-reviewer-as-a-required-check.md)
  Decision 6 — the reviewer's token is also a Dependabot secret.
- **Decision 2 superseded by:** [ADR-0049](0049-the-reviewer-runs-on-dependabots-pull-requests.md) —
  the cause was the action refusing a bot, not a missing secret; no
  Dependabot secret.

## Context and problem

Auto-merge opens no new path to `main`: GitHub merges only when every
required check is green, as a manual merge would. A stranger cannot reach
`main` either. They have no write access, so they cannot merge or turn on
auto-merge. A pull request from a fork fails `agent review` on purpose
([ADR-0036](0036-the-reviewer-as-a-required-check.md) Decision 3). The
`main` ruleset has no bypass actor. Code reaches the node only when a pin
under `platform/infra/` names its image, which waits for the operator's
approval ([ADR-0035](0035-agents-use-the-operators-scoped-token.md)).
Checked on 2026-11-16 with `gh api repos/FilipeFantini/lagom/rulesets/25948914`.

What is left comes from inside:

1. **Dependencies.** A package in a `uv.lock`, a base image, a chart or an
   action can carry a known vulnerability, or be compromised. Dependabot
   alerts were off.
2. **A workflow defect,** such as untrusted text interpolated into a script
   that holds a secret. No tool scanned the workflows.
3. **An agent steered by text it reads** — an issue, a dependency's files —
   into writing harmful code that it then opens as its own pull request. That
   code meets the same gates as any other. The reviewer is a model and can be
   steered too.

Two external tools were proposed. **Strix** is an automated penetration test:
it runs the application in a container and attacks it with agents. It finds
weaknesses in a running service, not harmful code in a diff. This platform
exposes little: the operator UIs sit behind Cloudflare Access
([ADR-0030](0030-operator-uis-behind-cloudflare-access.md)). On pull requests
it would need a paid LLM API key, and an agent with a shell running the
pull request's code next to that key — what ADR-0036 Decision 2 avoids.
**`security-audit-skill`** is a coding-agent skill for a full audit of a
repository in phases. It is not a pull request gate. Installing it would add
third-party instructions to the agents, a guarded change.

## Decision

**1. Dependabot alerts and Dependabot security updates are on** (done by the
operator on 2026-11-16). An alert names a dependency with a known
vulnerability; a security update is a pull request that bumps it to the
first fixed version. Version updates, which bump everything on a schedule,
are not turned on: they need a `.github/dependabot.yml` and bring churn with
no security reason.

**2. Dependabot's pull requests are reviewed like any other.** A workflow
that Dependabot triggers receives only Dependabot secrets, not the
repository's, so `agent review` ran with no token and failed on the first
two (#107 and #108). The operator stores the same Claude token,
`CLAUDE_CODE_OAUTH_TOKEN`, as a **Dependabot secret** (*Settings → Secrets
and variables → Dependabot*). It reaches only a job that names it, and only
`agent review` does. That job runs `main`'s code and reads the pull request
as data. A Dependabot pull request that touches no guarded path then merges
like an agent's. An image built from it still runs only after a guarded pin.

**3. CodeQL code scanning runs, and blocks a merge.** CodeQL is GitHub's
static analyser: it reads the code without running it and looks for known
patterns of defects. Its default setup is on for `actions` and `python`, on
every pull request and weekly (done by the operator on 2026-11-16). Its
results become a merge gate with the `main` ruleset's *Require code scanning
results* rule: tool CodeQL, security alerts *High or higher*, alerts
*Errors*. A pull request that adds an alert at those levels does not merge.
Alerts already on `main` do not block it. The rule is set last, and only
after CodeQL has reported on a Markdown-only pull request and on a
Dependabot one: a required result that never comes blocks every pull
request of that kind. Removing the rule undoes it.

**4. The first alert is dismissed as a false positive, with its reason.**
CodeQL's `actions/untrusted-checkout/high` flags the checkout of the pull
request's head in `agent-review.yml`, in a job that holds a secret. The
checkout is the design of ADR-0036 Decision 2: the files go into `pr-head/`
as data, no step runs anything from them, the reviewer has read-only tools
and no shell, and Claude Code's loading of `CLAUDE.md` is turned off. The
alert is dismissed as *won't fix* with a link to this ADR. A later step that
runs anything from `pr-head/` would make the alert true. The reviewer and
the operator check for that on any change to `agent-review.yml`, which is
guarded.

**5. Every action must be pinned to a full commit SHA,** enforced by the
repository's Actions setting (*Require actions to be pinned to a full-length
commit SHA*), not only by habit. A tag such as `v4` can be moved to other
code by whoever controls the action; a commit SHA cannot. Every workflow
already pins its actions.

**6. CI for every outside contributor waits for approval.** The fork pull
request approval policy moves from *first-time contributors* to *all
external contributors* (`all_external_contributors`). A fork's `pull_request` workflows run the fork's code
on GitHub's runners, with a read-only token and no secret. The risk is small,
and the approval costs a click only when someone outside opens a pull
request.

**7. Neither external tool is adopted.** Strix does not answer the risks
above and would put a model with a shell next to a secret.
`security-audit-skill` may serve later as a one-off audit of the whole
repository, run in a session the operator starts; that is its own decision,
taken after reading the skill.

## Consequences

**Positive.**

- Known vulnerabilities in dependencies are reported, and fixed by pull
  requests that go through the same gates.
- The workflows and the Python code are scanned on every pull request, and
  a new high-severity finding stops the merge without a human reading code.
- A moved tag on an action can no longer change what CI runs.

**Negative.**

- **None of this stops harmful code written on purpose.** CodeQL finds known
  patterns, Dependabot finds published vulnerabilities. A backdoor that looks
  like ordinary code passes both. For risk 3 the gates are still the
  reviewer and the operator's approval of each pin.
- **The Claude token is stored twice,** as a repository secret and a
  Dependabot secret. Rotating it means updating both.
- **A dismissed alert hides a pattern.** If `agent-review.yml` later runs
  code from `pr-head/`, CodeQL stays quiet about it; the guard is the review
  of that file.
- **The settings live outside Git.** The Dependabot secret, the code scanning
  rule, the pinning requirement and the approval policy are not in the
  repository; a rebuilt repository needs them set again. They are listed, with
  how to inspect them, in
  [CONTRIBUTING](../../CONTRIBUTING.md#repository-security-settings).

## Validation

- Dependabot and CodeQL: on 2026-11-16, `gh api repos/FilipeFantini/lagom`
  showed `dependabot_security_updates: enabled`,
  `vulnerability-alerts` answered 204, and
  `code-scanning/default-setup` showed `state: configured`, languages
  `actions` and `python`. They found two Dependabot alerts
  (GHSA-mf5v-w3w3-f6cg, `opentelemetry-api` 1.45.1, medium, in
  `platform/orchestration` and `platform/warehouse`) and the CodeQL alert of
  Decision 4.
- Decision 2 is shown when `agent review` reports a verdict on a Dependabot
  pull request, #107 or #108 re-run.
- Decision 3: CodeQL reported on #109, Markdown only (`CodeQL`,
  `Analyze (actions)` and `Analyze (python)` passed). On Dependabot's #107
  and #108, on 2026-11-17, CodeQL reported `NEUTRAL` with no analysis job,
  so the precondition is not met and the rule stays unset: it would likely
  block every Dependabot pull request.
- Decision 5 is shown when CodeQL and every workflow still run after the
  setting is on.
