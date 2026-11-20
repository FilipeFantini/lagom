# Contributing

Start with [AGENTS.md](AGENTS.md) and, for a change larger than one file,
the [engineering protocol](docs/engineering-protocol.md).

## Workflow

1. Create a worktree with one branch from `main`. Use a descriptive branch
   under `feat/`, `fix/`, `docs/`, `chore/` or `refactor/`.
2. Commit one coherent change at a time. Follow [Conventional Commits](#commits).
3. Open a pull request into `main`, with the [required evidence](#pull-requests)
   and a closing reference to the issue where applicable.
4. Read the independent verdict posted by `agent review`, for example with
   `gh pr view <number> --comments`. The
   [reviewer](.claude/agents/reviewer.md) exercises the adversarial review
   defined in the engineering protocol. Resolve blocker and major findings
   before merge and address substantive minor findings. Markdown-only changes
   outside the agents' instructions pass the review gate without a model run
   ([ADR-0036](docs/adr/0036-the-reviewer-as-a-required-check.md),
   [ADR-0038](docs/adr/0038-leaner-agent-gates.md)).
5. Any agent that can reach GitHub may merge after every required check passes,
   the independent reviewer returns APPROVE where required, and the operator
   approves a guarded pull request. The session that pushed a sandboxed agent's
   work handles the merge. Only `main` is reconciled into the cluster.
6. Remove clean, finished worktrees and merged branches with the safe Git
   commands allowed by the [agent rules](AGENTS.md#10-more-than-one-harness).

The main session owns the design. A scoped
[implementer](.claude/agents/implementer.md) works from a brief with paths,
constraints and acceptance criteria, commits locally and hands the result
back with evidence. The architect audits the diff and opens the pull request.

## What is enforced for agents

The Claude Code [deny list](.claude/settings.json) blocks specific Git forms
that discard work, rewrite history or push to `main`. It blocks forms that
write to the cluster, enter workloads or generate a Secret. Those restrictions
also bind Mac handoff agents under [AGENTS.md](AGENTS.md#10-more-than-one-harness).
Inspect with `get`, `describe`, `logs`, `top` or
`kubectl diff -n <namespace> -f <manifest>` where authorized. The operator
handles sealing and the manual steps in the runbook.

The deny list matches written commands. It does not constrain their
equivalents invoked indirectly, unlisted writes or the GitHub permissions
of the Mac session. Agents remain bound by the
[harness rules](docs/engineering-protocol.md#18-agent-driven-development).

## Commits

Use a Conventional Commit such as `docs(adr): record table format decision`.
Explain why in the body when it is not self-evident. Use the GitHub noreply
address and the attribution trailer in [AGENTS.md](AGENTS.md#6-execution-flow).

## Checks

[CI](.github/workflows/ci.yml) runs on pull requests and `main`. It checks
formatting, types and tests for tools, sources and orchestration. It lints
and parses dbt and builds a fixture lake with its tests. The dashboard job
renders and reconciles the boards against fixture gold, checks the metadata
contract, builds dbt's documentation, and opens every page in a browser to
check that it requests nothing from outside. It is a fixture
check and does not publish. The [tool checks](tools/README.md) cover the
resource budget, manifest policy, image promotion and the relative links
and anchors of the maintained Markdown. The whole history
is scanned for [secrets](#secrets).

### Branch protection

The `main` repository ruleset requires a pull request and blocks force
push and deletion, with no bypass. Its required status names are
`tools (lint, types, tests)`, `services (lint, types, tests)`,
`warehouse (lint, parse)`, `secret scan`, `resource budget`,
`promotion (prod pins only staging's digests)`, `manifest policy`,
`guardrail approval` and `agent review`. Job renames must be coordinated
with the operator's ruleset update. The dashboard render and `doc links`
run in CI but their names are outside this required list, so GitHub does
not block a merge on them.

Pull requests touching [CODEOWNERS paths](.github/CODEOWNERS) wait in
[`guardrail approval`](.github/workflows/guardrail-approval.yml). The
operator selects **Review deployments → Approve** after reviewing the
**For the operator** section. Approvals require Deployments permission
and are renewed on every push. Editing the description alone keeps an
existing approval. A pull request stacked on a different branch gets
these checks after it targets `main` and receives a new push.

The merge gates run `main`'s workflows on `pull_request_target` and read
pull request files as data. The repository Actions policy **Allow the
merge gates on pull_request_target** permits that event only for
[`guardrail-approval.yml`](.github/workflows/guardrail-approval.yml) and
[`agent-review.yml`](.github/workflows/agent-review.yml). Recreate the
policy on a rebuild. Inspect the configuration with:

```bash
gh api repos/FilipeFantini/lagom/rules/branches/main
gh api repos/FilipeFantini/lagom/environments/guardrail
gh api repos/FilipeFantini/lagom/actions/policies
```

Check for the required status names, the operator as required environment
reviewer, and the policy limiting the trigger to the two workflows.

**Recreate the gates**, as operator, before requiring their check names:

1. Create the `guardrail` environment with the operator as required
   reviewer, **Prevent self-review** off and administrator bypass off.
2. Put both workflows on `main`. Use `claude setup-token` and save the
   token as repository secret `CLAUDE_CODE_OAUTH_TOKEN`. Keep it out of the
   terminal history and out of the Dependabot secrets store.
3. Test one guarded pull request waiting for approval, one unguarded pull
   request passing that gate, and a substantive pull request receiving an
   `agent review` verdict. Close the test requests unmerged. Then require
   the checks in the `main` ruleset.
4. If using a runner connector, the operator first proves its scoped token
   cannot approve the waiting deployment. Use a fine-grained token limited
   to this repository, expiring in at most 90 days. Grant Contents and Pull
   requests read and write, and Issues read and write only if needed. Set
   every other permission to **No access**. The expected result of the pending-deployment approval request is
   refusal. Only then configure the connector
   ([ADR-0035](docs/adr/0035-agents-use-the-operators-scoped-token.md)).

The gate does not substitute for review of risks outside guarded paths.
The Mac's broader token can administer the repository, so the operator's
go-ahead also holds by instruction for that session. A verdict covers the
reviewer's findings, with the limitations in
[ADR-0036](docs/adr/0036-the-reviewer-as-a-required-check.md).

### Repository security settings

These settings live on GitHub. Recreate and verify them on a rebuild.
[ADR-0048](docs/adr/0048-dependency-and-code-scanning-for-the-public-repository.md)
and [ADR-0049](docs/adr/0049-the-reviewer-runs-on-dependabots-pull-requests.md)
record the setup and exceptions.

| Setting | Expected state |
|---|---|
| Dependabot alerts and security updates | Enabled. No Dependabot secret copy |
| CodeQL default setup | Actions and Python, on pull requests and weekly |
| Code scanning as a merge requirement | Unset because the default setup does not analyze Dependabot pull requests |
| CodeQL alert 1 on the reviewer workflow | Dismissed with the rationale in ADR-0048 |
| Actions | Require full commit SHA pins |
| CI from forks | Approval for all external contributors |
| Secret scanning and push protection | Enabled |

## Pull requests

Write the description in English. Include what changes and why, affected
paths, evidence from actual commands, the documentation updated or why none
applies, security, residual risks and pending checks. For a defect that
reached `main`, describe the gate that now catches it or why none can.

For guarded paths, add a section titled **For the operator**, in plain words.
Say what changes, what it touches, what could go wrong and how to reverse
it. For an image pin, cover the changes since the previous pin. Keep the
diff scoped to the description and omit tool-generated credit trailers.

## Adding a data source

Follow [Add a source](docs/data.md#add-a-source) and its contract and
verification requirements.

## Secrets

Use [Sealed Secrets](docs/runbook.md#sealed-secrets) when a cluster secret
belongs in Git. If an actual secret enters history, rotate first. Then ask
the operator to handle history remediation.

The [gitleaks configuration](.gitleaks.toml) describes the narrowly shaped
exception for encrypted values. Whole-history scanning cannot detect
patterns it does not know, credentials outside Git, or whether text shaped
like sealed ciphertext is in a valid SealedSecret. Investigate each finding.
Document and narrow any false-positive exception at its source.
