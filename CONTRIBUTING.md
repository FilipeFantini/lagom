# Contributing

The workflow is in place from the first commit, even with one developer, so that
adding the second changes nothing.

Start with [`AGENTS.md`](AGENTS.md). For anything larger than a single-file
change, read [`docs/engineering-protocol.md`](docs/engineering-protocol.md)
first.

## Language

Conversation happens in Brazilian Portuguese. **Everything written into the
repository is in English** — code, comments, commits, docs, PR descriptions, log
messages.

## Workflow

1. Branch from `main`. Only `main` is reconciled by Argo CD, so nothing reaches
   the cluster from a branch.
2. Commit in small, coherent steps. One concern per commit.
3. Open a pull request and let CI run.
4. Get a review. Working alone, the review is the `reviewer` agent
   ([`.claude/agents/reviewer.md`](.claude/agents/reviewer.md)): an
   independent pass that runs the adversarial review of
   [`docs/engineering-protocol.md`](docs/engineering-protocol.md) §12 Phase E.
   The required `agent review` check runs it in CI on every push and posts
   its verdict on the pull request
   ([ADR-0036](docs/adr/0036-the-reviewer-as-a-required-check.md)); a
   reviewer subagent before opening the pull request is optional. Blocker and
   major findings are fixed before merge. A pull request that touches a path
   in [`.github/CODEOWNERS`](.github/CODEOWNERS) has a **For the operator**
   section in its description — what changes in behaviour, what it touches,
   what could go wrong, how to undo it, in plain words — or the check fails.
5. Merge to `main`. Argo CD converges the cluster. Who may merge — any agent,
   once the required checks are green and a reviewer returned APPROVE, with the
   operator's explicit go-ahead for a guardrail change — is
   [`AGENTS.md`](AGENTS.md) §6
   ([ADR-0029](docs/adr/0029-agents-on-more-than-one-harness.md)).

**More than one harness.** Agents can run on Claude Code, Codex, or another
harness. Everything below about `.claude/settings.json` binds Claude Code only.
An agent on any other harness runs inside its harness's own sandbox with
network access off — for Codex, `workspace-write` — so its commands can edit
the checkout but cannot commit, reach the cluster, GitHub, or the tailnet; a
session outside the sandbox commits and pushes its changes
([`AGENTS.md`](AGENTS.md) §10). An agent started by an agent runner works in a
container that has network but no credential, on its own clone, and hands its
work over as a patch file; that session treats the patch as untrusted input,
applies it in a fresh worktree, and opens the pull request
([ADR-0032](docs/adr/0032-agents-in-credential-free-containers.md)). Once
the setup below is shown to work, a runner's agent may instead hold the
agents' own GitHub account's token and push, open and merge its own pull
requests, with GitHub requiring the operator's approval for the guarded paths
([ADR-0035](docs/adr/0035-agents-push-as-their-own-github-identity.md)).

**Agents.** The main Claude session acts as architect. It can hand a scoped
task to the `implementer` agent
([`.claude/agents/implementer.md`](.claude/agents/implementer.md)) with a brief —
files, acceptance criteria, constraints. The implementer commits on a local
branch, never pushes or changes the cluster, and reports validation evidence;
the architect reads the diff, opens the pull request, and sends it to review.
**Most of these limits are instructions, not enforced permissions.** One part is
enforced: [`.claude/settings.json`](.claude/settings.json) denies, to every
Claude Code session and agent, the git commands that discard uncommitted work
or rewrite shared history — `reset --hard`, `checkout --` and `checkout HEAD`,
`restore`, `clean`, `switch -f`, `stash drop`/`clear`, `branch -D`/`-f`, force
pushes in any flag position or `+` refspec, remote deletes — and pushes to
`main`. Two harmless forms are blocked with them, as accepted friction:
`git restore --staged` (use `git reset <file>`) and `git clean -n`. Claude Code
cannot scope these
rules to one agent, so they bind the main session too. They match the command
as written: `git -C . reset --hard` is not caught, so they stop mistakes, not a
determined bypass.

The same file denies **writes to the cluster** (since 2026-10-27): the
`kubectl` verbs that change or enter running objects (`apply`, `delete`,
`edit`, `patch`, `replace`, `scale`, `label`, `annotate`, `exec`, `cp`,
`attach`, `run`, `debug`, `set`, `rollout`, `drain`, `cordon`, `uncordon`,
`taint`), in either position (`kubectl -n prod delete …` too); `kubectl
create` from a file or kustomization (`-f`, `--filename`, `-k`), of a Secret
or a token, and of a Job from an image (`--image`); `helm install`, `upgrade`,
`uninstall`, `rollback`, in either position; and `argocd app sync`, `delete`,
`set`, `rollback`, `terminate-op`. Denying `create secret` also blocks the
sealing recipe in the runbook (`create secret … --dry-run=client | kubeseal`),
on purpose: an agent never handles a secret's value, so sealing stays the
operator's step. Argo CD is the cluster's writer, and a
change reaches it through a merge. What stays allowed is what the runbook
needs an agent for: reading (`get`, `describe`, `logs`, `top`), `kubectl
create job --from=cronjob/…` to run a pipeline on demand, `port-forward`, and
`kubectl diff -n <namespace> -f` (manifests here carry no namespace; Argo CD sets it), which replaces `apply --dry-run=server` as the server-side
check of a manifest. What it does not stop: commands run through `ssh` on the
node (`sudo k3s kubectl …`, `systemctl`), `kubectl` reached through another
program, the bare `create` forms not listed (`create namespace`,
`configmap`, `serviceaccount`), `kubectl proxy` and `certificate approve`,
and any verb not listed. The rules match the command as written, like the git
ones. Everything else — no push from the
implementer — is still instruction only. Changing the file is a guardrail
change ([`engineering-protocol.md`](docs/engineering-protocol.md) §18 rule 3).

Branch naming: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/` followed by a
short description.

## Commits

Conventional Commits:

```
feat(ingestion): add USGS earthquake source
fix(dbt): correct timezone handling in silver layer
docs(adr): record table format decision
```

Explain **why** in the body when the change is not self-evident. The subject
says what changed; the body says what it is protecting against.

## Checks

CI runs on every pull request and on `main`
([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): lint, types, and tests
for `tools/` and for every project under `services/`; lint and parse of the dbt
project; a secret scan of the whole history (see [Secrets](#secrets)); the
promotion check (prod pins only digests staging pinned first,
[ADR-0015](docs/adr/0015-images-on-ghcr.md)); the resource budget check; and the
manifest policy check (explicit prune, pinned chart versions and images,
[`tools/README.md`](tools/README.md#manifest-policy-check)).
**A red check is not merged.**

### Branch protection

`main` is protected by a repository ruleset named `main`, enabled on
2026-10-27 when the repository became public
([ADR-0017](docs/adr/0017-public-repository-from-a-snapshot.md)). GitHub
enforces, with no bypass — the operator follows the same rules:

- changes reach `main` only through a pull request;
- the CI checks above must pass, named exactly as their jobs report:
  `tools (lint, types, tests)`, `services (lint, types, tests)`,
  `warehouse (lint, parse)`, `secret scan`, `resource budget`,
  `promotion (prod pins only staging's digests)`, and `manifest policy` — a
  renamed job must be renamed in the ruleset too, or every pull request waits
  for a check that never reports (`manifest policy` joined the ruleset on
  2026-10-27);
- `main` cannot be force-pushed or deleted.

A second ruleset, `code owners`, requires a code owner's approval for any pull
request that touches a path in [`.github/CODEOWNERS`](.github/CODEOWNERS) —
what Argo CD applies, CI and its checks, dependencies, and the rules — and
dismisses that approval when new commits are pushed. The operator is the only
code owner and cannot approve their own pull requests, so the Repository admin
role bypasses this ruleset, for pull requests only; the agents' account has
Write and cannot
([ADR-0035](docs/adr/0035-agents-push-as-their-own-github-identity.md)).

Inspect what applies with
`gh api repos/FilipeFantini/lagom/rules/branches/main`, and check
`CODEOWNERS` for syntax errors with
`gh api repos/FilipeFantini/lagom/codeowners/errors`.

**Setting up the agents' account,** in this order — each step depends on the
one before:

1. `CODEOWNERS` is on `main`.
2. The `code owners` ruleset is active.
3. The operator creates the machine account `<agent-github-user>` (with
   two-factor authentication), invites it as a collaborator with the Write
   role, and creates its classic token: `public_repo` only, at most 90 days.
4. From that account, a pull request touching a guarded path shows the merge
   blocked waiting for a code owner's review; one touching no guarded path
   does not. Both are closed unmerged.
5. Only then does the token go to the runner, into its GitHub connector.

What it does not enforce:

- **Review of unguarded paths.** Zero approvals are required outside
  `CODEOWNERS`. A pull request there cannot reach the cluster, CI or the rules
  without touching a guarded path. Code reaches the cluster when a pin under
  `platform/infra/` moves. Like every guarded pull request, a pin's carries a
  **For the operator** section in plain words — what changes in behaviour
  since the last pin, what it touches, what could go wrong, how to undo it —
  because the operator approves on that section and the reviewer's verdict,
  not by reading code
  ([ADR-0035](docs/adr/0035-agents-push-as-their-own-github-identity.md)).
- **A gate approving a change to itself, on the operator's side.** A pull
  request runs the workflow as it edits it, so a change under
  `.github/workflows/` can change what its own required checks do. For the
  agents' account `code owners` blocks that; the operator's pull requests —
  and a Mac session pushing as the operator — bypass it. Changes to CI, agent
  definitions or instructions, permissions, RBAC, and secrets therefore need
  the operator's explicit go-ahead before merge, by convention
  ([ADR-0029](docs/adr/0029-agents-on-more-than-one-harness.md)).
- **The reviewer's APPROVE, until `agent review` is required.** Agents may
  merge after it ([`AGENTS.md`](AGENTS.md) §6). Once the check is in the
  ruleset GitHub enforces it; until then a token that can merge an unguarded
  pull request can merge it without it.

**Making `agent review` required,** in this order
([ADR-0036](docs/adr/0036-the-reviewer-as-a-required-check.md) Decision 7):

1. [`agent-review.yml`](.github/workflows/agent-review.yml) is on `main`
   (it runs on `pull_request_target`, so it reviews nothing before then).
2. The operator creates a token with `claude setup-token` and stores it as
   the repository secret `CLAUDE_CODE_OAUTH_TOKEN`.
3. A pull request shows the `agent review` check run with a verdict comment.
4. Only then is `agent review` added to the `main` ruleset's required checks;
   a required check that never reports blocks every pull request.

Until 2026-10-27 `main` had no protection: on a private repository it needs a
paid plan, and the operator accepted that as an exception (2026-10-13) and as
debt for ADR-0013 (2026-10-16).

## Pull requests

A pull request states:

- **What changed and why**, in prose a reader can follow without the diff.
- **How it was validated** — the command run and its real result, not an
  assurance. A green build is not a working feature.
- **Which documents were updated**, or an explicit statement that none were
  affected and why.
- **Open items and accepted risks**, if any.
- **For a fix to a defect that reached `main`:** the escape, and the
  deterministic check that now catches it — or why none can
  ([ADR-0013](docs/adr/0013-agent-driven-development-guardrails.md) rule 1).

Guardrail changes need the operator's explicit go-ahead before merge
([`engineering-protocol.md`](docs/engineering-protocol.md) §18 rule 3).

Do not include unrelated refactoring. A diff that touches files the description
does not mention will be sent back.

## Required checks

A pull request is not ready while any of these is true:

- Build, lint, type check, or a required test fails.
- Acceptance criteria are not validated by evidence.
- A secret appears in the diff.
- Declared resource requests exceed the ceiling in
  [`docs/architecture.md`](docs/architecture.md).
- Documentation required by [`AGENTS.md`](AGENTS.md) §5 was not updated.

Never weaken a check to get a green result. If a check is wrong, fix the check
in its own commit, with the reason stated.

## Adding a data source

This is the most common change, and the platform is designed so it is
configuration rather than code:

1. Choose the pattern — batch goes through dlt, continuous events through
   Redpanda Connect ([ADR-0003](docs/adr/0003-ingestion-split-dlt-benthos.md)).
2. Declare the source config. Credentials go to the cluster secret mechanism,
   never into the repository.
3. Add dbt models for bronze, silver, and gold, with the schema contract the
   next layer can rely on.
4. Add reconciliation and freshness tests —
   [`docs/engineering-protocol.md`](docs/engineering-protocol.md) §16.4.
5. Add fixtures for tests. **Tests never call the live API.**
6. Declare resource requests and limits.
7. Update [`docs/architecture.md`](docs/architecture.md) if the data flow or the
   budget changed.

## Secrets

Never commit a credential, token, or connection string — not even a
placeholder that looks real. Secrets are committed only as Sealed Secrets
(`*-sealed.yaml`; how to seal one is in the runbook), and never appear in logs,
including inside error messages and tracebacks.

If a secret is committed by accident, treat it as compromised: rotate it first,
then clean the history.

**CI scans for secrets** with [gitleaks](https://github.com/gitleaks/gitleaks)
over every commit in the history, with its default rules and one exception for
Sealed Secrets ciphertext, explained in [`.gitleaks.toml`](.gitleaks.toml). A
finding fails the build. A false positive is answered by narrowing a rule in
that file, with the reason written next to it — never by a `.gitleaksignore`
entry nobody can explain, and never by removing the job.

What the scan does not check: secrets that match no rule (a password with low
entropy, or an unusual format), anything outside Git (shell history, CI logs,
the cluster), and whether a sealed file is really a `SealedSecret`. The
ciphertext exception matches the shape of a line, not the file it is in, so a
real credential written as `key: Ag` followed by 500 or more base64 characters
would pass the generic rule in any file. It lowers
the chance that a credential reaches the history; it does not prove none did.
