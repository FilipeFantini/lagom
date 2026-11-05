# tools/agents — the agent workstation

One container on the operator's Mac that runs [Paseo](https://paseo.sh) and
the coding agents it drives — Claude Code and Codex today, any other CLI agent
by adding one line. The operator works with every agent from one web page, and
when one provider's usage limit is reached, the work continues on another.
Why a container, and what it deliberately cannot reach, is
[ADR-0029](../../docs/adr/0029-agents-on-more-than-one-harness.md).

## What each piece is

| Piece | What it is | Analogy |
|---|---|---|
| [Apple `container`](https://github.com/apple/container) | Apple's tool that runs a Linux container as a small virtual machine of its own | a separate, sealed room in the house |
| the image (`lagom-agents:dev`) | Paseo's official image plus the agent CLIs, `gh`, `helm` and `uv`, built from [`Containerfile`](Containerfile) | the furniture list for the room |
| two named volumes | virtual disks that outlive the container: `lagom-agents-home` (logins, Paseo's state) and `lagom-agents-workspace` (the clone of this repository) | the room's cupboards |
| the Paseo daemon | a background program in the container that starts agents, keeps their history and serves the web page | the room's receptionist |
| `paseo.env` | one file on the Mac holding the password of that web page | the door key |

## What it can and cannot reach

| Reaches | Does not reach |
|---|---|
| the internet: the AI providers, GitHub, package registries | the tailnet — blocked by routes [`entrypoint.sh`](entrypoint.sh) sets as root before any agent starts |
| GitHub through a token limited to this repository, without workflow permission | the cluster: no kubeconfig, no SSH key |
| its own clone of this repository | the Mac's files: nothing is mounted from the host |

The web page is published on `127.0.0.1` only, so no other device can open
it, and it asks for the password even there.

## On the Mac, after setup

Everything lives in four places, and [removing it](#remove-everything) clears
them all:

- `/usr/local` — the `container` tool itself;
- `~/Library/Application Support/com.apple.container` — images, the container
  and its volumes, about 6 GB;
- `~/.config/container/config.toml` — one setting: build without Rosetta;
- `~/lagom-agents/paseo.env` — the password, readable only by the operator.

The container takes up to 4 GB of memory and 4 CPUs while it runs, and nothing
when it is stopped.

## Set up, once

Commands run on the Mac from the repository root, unless they say *inside*.

1. **Install the runtime.** Download `container-1.6.1-installer-signed.pkg`
   from the [release page](https://github.com/apple/container/releases/tag/1.6.1),
   check it with `pkgutil --check-signature <file>` (signed by Apple,
   notarized), open it, and enter the admin password. Then:

   ```bash
   container system start --enable-kernel-install
   ```

2. **Build without Rosetta.** The builder asks for Rosetta by default, to build
   Intel images; nothing here needs one.

   ```bash
   mkdir -p ~/.config/container
   printf '[build]\nrosetta = false\n' > ~/.config/container/config.toml
   container system stop && container system start
   ```

3. **Build the image.** The builder gets a public DNS resolver: on this Mac the
   default resolver refused the builder's queries (2026-11-05). Deleting the
   builder afterwards frees its cache, about 6 GB.

   ```bash
   container builder start --dns 1.1.1.1
   container build --file tools/agents/Containerfile --tag lagom-agents:dev tools/agents
   container builder delete --force
   container image prune --all
   ```

4. **Create the volumes and the password.**

   ```bash
   container volume create -s 20G lagom-agents-home
   container volume create -s 20G lagom-agents-workspace
   mkdir -m 700 ~/lagom-agents
   (umask 077; printf 'PASEO_PASSWORD=%s\n' "$(openssl rand -hex 24)" > ~/lagom-agents/paseo.env)
   ```

5. **Start the container.** `CAP_NET_ADMIN` is what lets the entrypoint set
   the tailnet routes; Paseo then runs as the user `paseo`, which has no
   capabilities left. Without it the container stops at once, by design.

   ```bash
   container run -d --name lagom-agents \
     --cap-add CAP_NET_ADMIN --dns 1.1.1.1 --memory 4g --cpus 4 \
     -p 127.0.0.1:6767:6767 \
     -v lagom-agents-home:/home/paseo -v lagom-agents-workspace:/workspace \
     --env-file ~/lagom-agents/paseo.env \
     lagom-agents:dev
   ```

6. **Clone the repository and set the commit identity** (the GitHub noreply
   address, [AGENTS.md](../../AGENTS.md) §3):

   ```bash
   container exec --user paseo lagom-agents git clone https://github.com/FilipeFantini/lagom-data-solutions.git /workspace/lagom-data-solutions
   container exec --user paseo lagom-agents git config --global user.name "<name>"
   container exec --user paseo lagom-agents git config --global user.email "<id>+<user>@users.noreply.github.com"
   ```

7. **Log in, inside** — each tool prints a URL to open on the Mac and a code to
   confirm. The logins stay in the home volume.

   ```bash
   container exec -it --user paseo lagom-agents claude
   container exec -it --user paseo lagom-agents codex login --device-auth
   container exec -it --user paseo lagom-agents gh auth login
   container exec --user paseo lagom-agents gh auth setup-git
   ```

   For `gh`, paste a **fine-grained token** made at GitHub → Settings →
   Developer settings → Fine-grained tokens: repository access *only* this
   repository; permissions *Contents* and *Pull requests* read and write;
   nothing else — in particular not *Workflows* and not *Administration*;
   expiry 90 days. Without *Workflows*, GitHub refuses a push that changes
   `.github/workflows/`, so no agent can edit the gates that check it.

8. **Open** <http://127.0.0.1:6767>, add the direct connection
   `127.0.0.1:6767` with the password from `~/lagom-agents/paseo.env`, and add
   the project `/workspace/lagom-data-solutions`.

## Day to day

```bash
container start lagom-agents    # after a reboot or a stop
container stop lagom-agents     # frees its memory
```

An agent works on a branch in the container's clone, pushes it, and opens the
pull request; the review and merge rules are those of
[AGENTS.md](../../AGENTS.md) §6. To look at a branch on the Mac, fetch it from
GitHub like any other.

## Add an AI, or update one

1. Add the CLI, pinned to an exact version, to the `npm install` line in the
   [`Containerfile`](Containerfile) — or change a version there. Paseo lists
   the agents it supports in its
   [provider docs](https://paseo.sh/docs/supported-providers).
2. Rebuild (step 3), then recreate the container (step 5 after
   `container rm -f lagom-agents`). The volumes keep the logins and the clone.
3. Log in to the new CLI inside the container (as in step 7).

An agent whose provider needs an API key gets it the same way as a login,
inside the container — never from a file in this repository.

## Remove everything

```bash
container rm -f lagom-agents
container volume rm lagom-agents-home lagom-agents-workspace
container image rm lagom-agents:dev
container system stop
/usr/local/bin/uninstall-container.sh -d
rm -rf ~/lagom-agents ~/.config/container
```

Then revoke the fine-grained token on GitHub, and sign the container's Claude
and Codex sessions out from each provider's account page.

## What this does not isolate

So nobody reads more into it than it gives:

- **The token is the agents' whole GitHub authority.** It can push branches,
  delete or force-push any branch that is not `main`, open pull requests and
  merge them once the required checks pass. The `main` ruleset still applies.
- **Agents share one container.** Claude Code and Codex see the same home
  volume, so either could read the other's login files. Isolation is per
  container, not per agent.
- **The internet is open.** Anything an agent reads can try to steer it, and
  it can send what it holds — the clone, the logins — anywhere on the
  internet. The clone is public; the logins can be revoked.
- **Only the tailnet ranges are blocked.** A service elsewhere that trusts the
  operator's home network, rather than a login, would be reachable.
- **The deny rules in `.claude/settings.json` apply to Claude Code only.**
  Codex runs without them; the container is its boundary.
