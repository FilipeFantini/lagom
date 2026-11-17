# Runbook

How to run, deploy, and recover the platform described in
[`architecture.md`](architecture.md). Read the section for the task at hand;
the design and the reasons for it are in `architecture.md` and the ADRs.

Operational procedures. **Kept current as part of any change that affects how
the platform is run or recovered** — see [`AGENTS.md`](../AGENTS.md) §5.

**Placeholders.** The repository is public, so values that identify the
operator or address the node are written in angle brackets and never filled
in here ([ADR-0017](adr/0017-public-repository-from-a-snapshot.md)):
`<admin-user>` (the node's login user), `<operator-ssh-public-key>` (from
1Password), `<operator-tailscale-login>` (the identity the operator signs in to
Tailscale with), `<tailnet>` and `<node-tailnet-ip>` (both printed by
`tailscale status`), and `<ip>` / `<public-ip>` / `<public-ipv4>` (the Hetzner
Console). `<node-name>` is not a secret but changes with each node: it is
`lagom-shared-02` since 2026-10-28, and the next node takes the next index;
`<old-node-name>` is the one being replaced.

## Provision the node (M0)

Hetzner Console → Add Server, with the settings in
[`roadmap.md`](roadmap.md#provisioning-settings). Two fields matter beyond the
table:

- **SSH keys:** select the operator's key. It lands in root's
  `authorized_keys`, which the cloud-config below makes unusable — but with no
  key selected, Hetzner generates a root password and emails it, and that
  password should never exist.
- **Cloud config:** paste the block below, including the `#cloud-config` first
  line; without it cloud-init ignores the content.

```yaml
#cloud-config
users:
  - name: <admin-user>
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: true
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    ssh_authorized_keys:
      - <operator-ssh-public-key>
disable_root: true
ssh_pwauth: false
write_files:
  - path: /etc/ssh/sshd_config.d/10-hardening.conf
    content: |
      PermitRootLogin no
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      AllowUsers <admin-user>
runcmd:
  - systemctl restart ssh
```

**Why it works.** The cloud-config is the server's *user data*: Hetzner stores
it once, at creation, and serves it from the metadata endpoint
(`169.254.169.254`). On first boot cloud-init reads it and applies it. It is
therefore fixed at creation — a Rebuild re-runs cloud-init with the **original**
user data, so changing it means creating a new server.

**Never set `disable_root: true` without creating a user.** cloud-init then
replaces root's key with a forced command that only prints
`Please login as the user "NONE"`, and the node is unreachable. This happened on
the first attempt and cost a server recreation.

`NOPASSWD` is required because the user has no password (`lock_passwd`). The
accepted trade-off: whoever holds the SSH key has root. The key lives in
1Password and port 22 is open only to the operator's IP.

**Verify** — the host key changes on every creation, so remove the old entry
and compare the new fingerprint before trusting it:

```bash
ssh-keygen -R <ip>
ssh <admin-user>@<ip> 'sudo -n true && cloud-init status --long'
ssh <admin-user>@<ip> 'sudo sshd -T' | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowusers) '
ssh root@<ip> true
```

Expected: `status: done` with `errors: []`; `permitrootlogin no`,
`passwordauthentication no`, `kbdinteractiveauthentication no`,
`allowusers <admin-user>`; and root refused with `Permission denied (publickey)`.

## Join the tailnet (M1)

On the node, from Tailscale's official Debian repository:

```bash
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install -y tailscale
sudo tailscale up --hostname=<node-name>
```

`tailscale up` prints a login URL. The operator opens it, signs in, and approves
the machine. **No auth key is involved**, so no credential passes through a
script or a chat. The trade-off is that the step is interactive — fine for one
node; an unattended rebuild would use a single-use, short-lived auth key.

Then, in the Tailscale admin console: *Machines →* `<node-name>` *→ Disable key
expiry*. A device must re-authenticate every 180 days by default; on a server
that silently drops it from the tailnet, and the cluster's API with it.

On the operator's machine: install the Tailscale app
(`brew install --cask tailscale-app`, which asks for the macOS admin password)
and sign in to the same tailnet.

**Verify:**

```bash
tailscale status                   # both machines listed
tailscale ping <node-name>     # "via <public-ip>:41641" is direct; "via DERP(...)" is relayed
ssh <admin-user>@<node-name> hostname
tailscale status --json | jq '.Peer[] | select(.HostName=="<node-name>") | {Tags, KeyExpiry}'
```

The first pings may go through a DERP relay until NAT traversal finds a direct
path; on 2026-10-12 it became direct within seconds. A path that stays on DERP
works, only slower.

The last command must print `"tag:lagom-node"` (after the ACL step below) and
`"KeyExpiry": null`, which is what *Disable key expiry* sets.

The node is `<node-name>.<tailnet>.ts.net`, `<node-tailnet-ip>`; `tailscale
status` on either machine prints both.

## Install k3s (M1)

Configuration first, as files, so the install is declarative and re-runnable;
then the official installer with a pinned version. On the node:

```bash
IP=$(tailscale ip -4)
sudo install -d -m 0755 /etc/rancher/k3s
sudo tee /etc/rancher/k3s/config.yaml >/dev/null <<EOF
node-name: <node-name>
tls-san:
  - <node-name>.<tailnet>.ts.net
  - $IP
secrets-encryption: true
disable:
  - traefik
  - servicelb
  - local-storage
  - metrics-server
EOF
sudo chmod 0600 /etc/rancher/k3s/config.yaml
sudo install -d -m 0755 /etc/systemd/system/k3s.service.d
printf '%s\n' '[Service]' 'Environment=GOMEMLIMIT=900MiB' \
  | sudo tee /etc/systemd/system/k3s.service.d/memory.conf
curl -sfL https://get.k3s.io -o /tmp/k3s-install.sh
sudo INSTALL_K3S_VERSION="v1.36.5+k3s1" sh /tmp/k3s-install.sh
```

The heredoc is unquoted so the shell fills in `$IP`, the node's tailnet
address; nothing else in it starts with `$`.

**Why it works.** The installer creates the `k3s` systemd unit; on every start
k3s reads `config.yaml`, where each key is the long form of a command-line flag.

- `tls-san` adds names to the API server's certificate. `kubectl` checks that
  the name it connects to is in the certificate, so the tailnet name must be
  listed or every connection over Tailscale fails TLS verification.
- `disable` keeps k3s from installing those packaged components; a component
  disabled on a running cluster is uninstalled
  ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md), which also says
  in what order the switch was made).
- `secrets-encryption` encrypts Secret objects in the datastore (SQLite) with a
  key generated on the node. It protects a copy of the datastore — a stolen
  disk image or backup. It does not protect against root on the node, which
  holds the key.
- The version is the `stable` channel on 2026-10-12. Upgrading is a deliberate
  change, preceded by a staging rebuild ([ADR-0007](adr/0007-three-environments.md)).

**Memory limit for the Go runtime** — the `memory.conf` above. Without it,
`k3s-server` grew to 1.2 GB holding memory it did not use
([ADR-0011](adr/0011-memory-viability-on-4gb.md)). A systemd drop-in survives
reinstalls of k3s, unlike the installer's environment file, which the
installer rewrites. Written before the installer runs, it applies from k3s's
first start (exercised 2026-10-28); on a node where k3s already runs, add it
and run `sudo systemctl daemon-reload && sudo systemctl restart k3s`.

The figure is 900MiB on the 8 GB node too. `GOMEMLIMIT` bounds k3s's own heap,
which grows with the objects in the cluster, not with the node's RAM; the
cluster moved unchanged, so the figure that stopped GC thrash on 4 GB still
fits. Measured on 2026-10-28, two hours after k3s started: 115 garbage
collections since start, no thrash
([ADR-0024](adr/0024-ci-ceiling-on-the-8-gb-node.md)); re-checked with the
three-day measurement, by the check below.

**Check the limit still has headroom** whenever the cluster gains a component.
If collections run several times per second and the node's CPU is near 100%
while pods use little, the runtime has hit the limit — raise it and record
the measurement ([ADR-0011](adr/0011-memory-viability-on-4gb.md) notes):

```bash
kubectl get --raw /metrics | grep -E '^go_(memstats_sys_bytes|gc_gomemlimit_bytes|goroutines|gc_duration_seconds_count) '
kubectl get --raw /api/v1/nodes/<node-name>/proxy/stats/summary   # CPU of "kubelet" (k3s) vs "pods"
```

Restarting k3s does not stop running containers (`KillMode=process`); only the
API is unavailable for a few seconds.

Traefik and servicelb are not installed, and local-path and metrics-server are
installed by Argo CD instead of k3s ([ADR-0021](adr/0021-k3s-packaged-components-under-git.md)):
the `disable:` list in `config.yaml` above. metrics-server is what `kubectl top`
needs to measure the budget in [architecture.md §11](architecture.md#11-resource-budget). **On a fresh install the cluster has no
storage class until Argo CD syncs `local-path`**; the Postgres `Cluster`s wait
for it (their volumes bind on first use). The API (6443) and kubelet (10250)
listen on all interfaces. **The Hetzner firewall is what keeps them
private** — which is why the check below probes from outside.

**Verify:**

```bash
sudo k3s kubectl get nodes        # <node-name>   Ready
sudo k3s secrets-encrypt status   # Encryption Status: Enabled
sudo k3s kubectl get --raw /metrics | grep '^go_gc_gomemlimit_bytes'   # 9.437184e+08
# from a machine outside the tailnet path, against the public IP:
for p in 22 80 443 6443 10250; do nc -z -G 5 -w 5 <public-ip> $p && echo "$p OPEN" || echo "$p closed"; done
```

Expected: only `22 OPEN` while the firewall still admits SSH.

## Tailscale ACL and zero inbound ports (M1)

The tailnet's default policy lets every device reach every other. The policy
below, applied in the admin console (*Access controls*), replaces it:

```jsonc
{
  "groups": {
    "group:operators": ["<operator-tailscale-login>"],
  },
  "tagOwners": {
    "tag:lagom-node": ["group:operators"],
  },
  "grants": [
    // Operators reach the node: SSH (break-glass) and the Kubernetes API only.
    {"src": ["group:operators"], "dst": ["tag:lagom-node"], "ip": ["tcp:22", "tcp:6443"]},
    // Each person's own devices keep reaching each other.
    {"src": ["autogroup:member"], "dst": ["autogroup:self"], "ip": ["*"]},
  ],
  "tests": [
    {"src": "<operator-tailscale-login>", "accept": ["tag:lagom-node:22", "tag:lagom-node:6443"], "deny": ["tag:lagom-node:10250"]},
    {"src": "tag:lagom-node", "deny": ["<operator-tailscale-login>:22"]},
  ],
}
```

Then *Machines →* `<node-name>` *→ Edit ACL tags →* `tag:lagom-node`.

**Why it works.** A tagged machine belongs to its tag, not to a person, so rules
name roles — operators reach Lagom nodes — instead of people. Anything not
granted is denied, in both directions: operators get SSH and the API and nothing
else (not even the kubelet), and the node can open no connection to anyone's
device, so a compromised node cannot reach the operator's machine. `tests` are
assertions the console checks on every save; a policy that breaks them is
rejected.

**Then close the public firewall.** With the tailnet path verified, the inbound
rule for port 22 is removed from the Hetzner firewall. The firewall itself stays
attached with **no inbound rules, which denies all inbound traffic** — do not
delete or detach it: it is the only thing keeping the API (6443), the kubelet
(10250), and anything a future LoadBalancer Service publishes off the internet,
since k3s listens on every interface. (Until 2026-10-27 that included Traefik
on 80 and 443; [ADR-0021](adr/0021-k3s-packaged-components-under-git.md).) Outbound is unrestricted. Tailscale still connects directly, because
the firewall is stateful and NAT traversal starts from outbound packets.

**Break-glass.** If Tailscale fails on the node, re-add an inbound rule for port
22 from the operator's current IP in the Hetzner Console and SSH to the public
address; remove it afterwards. The Hetzner web console is no way in: the user
has no password (`lock_passwd`), so a console login cannot succeed. The last
resort is Hetzner's rescue system, which boots the node from a separate image.

**Verify** — from outside the tailnet, then through it:

```bash
for p in 22 80 443 6443 10250; do nc -z -G 5 -w 5 <public-ipv4> $p && echo "$p OPEN" || echo "$p closed"; done
for p in 22 80 443 6443 10250; do nc -6 -z -G 5 -w 5 <public-ipv6> $p && echo "$p OPEN" || echo "$p closed"; done
nc -z -G 5 -w 5 1.1.1.1 443                  # control: proves the probe can see an open port
for p in 22 6443 10250; do nc -z -G 5 -w 5 <node-name> $p && echo "$p OPEN" || echo "$p closed"; done
```

Expected: every public port closed while the control succeeds; over the tailnet
22 and 6443 open, 10250 closed. On 2026-10-12 a listener on the operator's
tailnet address was also probed from the node: refused, with a local control
connection to the same listener succeeding.

## Operator kubeconfig (M1)

Fetched from the node, with the server address and every `default` name
replaced by the node's, then merged into `~/.kube/config` so the contexts of
other clusters stay. kubectl merges the files listed in `KUBECONFIG`, and
`config view --flatten` writes the result as one file with the certificates
inline:

```bash
umask 077 && mkdir -p ~/.kube && touch ~/.kube/config
ssh <admin-user>@<node-name> 'sudo cat /etc/rancher/k3s/k3s.yaml' \
  | sed -e 's#https://127.0.0.1:6443#https://<node-name>.<tailnet>.ts.net:6443#' \
        -e 's#: default$#: <node-name>#' > ~/.kube/<node-name>.yaml
KUBECONFIG=~/.kube/config:~/.kube/<node-name>.yaml kubectl config view --flatten > ~/.kube/config.merged \
  && mv ~/.kube/config.merged ~/.kube/config && rm ~/.kube/<node-name>.yaml
kubectl config get-contexts
kubectl --context <node-name> get nodes
```

The merge keeps the current context, so on a machine that already knew
another cluster, `kubectl` without `--context` still talks to that one. While
two clusters are in the file, **name the context on every command**:
`--context <node-name>` for `kubectl` and `kubeseal`, `--kube-context
<node-name>` for `helm`. The commands elsewhere in this runbook that carry no
context assume the current one is the right cluster: add the flag to them, or
switch first with `kubectl config use-context <node-name>`.

This file is the **cluster-admin credential** — a client certificate with every
right in the cluster. It never goes to Git or chat, `umask 077` keeps it
readable only by its owner, and it is not shared: per-developer RBAC replaces it
for anyone else (M7).

## Install Argo CD (M1)

Argo CD is installed once by hand, from the operator's machine over the
tailnet; after the handover below it manages itself
([ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md)).

```bash
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update argo
helm template argocd argo/argo-cd --version 10.10.3 -n argocd \
  -f platform/infra/argocd/values.yaml > /tmp/argocd.yaml   # inspect before installing
helm install argocd argo/argo-cd --version 10.10.3 -n argocd --create-namespace \
  -f platform/infra/argocd/values.yaml --wait
kubectl -n argocd get pods
```

Expected: `argocd-application-controller-0`, `argocd-repo-server` and
`argocd-redis` Running; `argocd-redis-secret-init` Completed. No Dex,
notifications, ApplicationSet or server Pods: the server's Deployment exists
at zero replicas, since the operator uses no UI and the CLI works with
`--core` ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).

**Repository access — none needed.** The repository is public, and every
Application names it by its HTTPS URL, so Argo CD clones it anonymously, the
way any reader can. There is no repository Secret in the cluster and no key at
GitHub to create, rotate, or lose on a rebuild
([ADR-0017](adr/0017-public-repository-from-a-snapshot.md)). The trade-off:
anonymous reads are subject to GitHub's unauthenticated limits, which a poll
every few minutes stays far below.

**Break-glass.** If a change to the values file breaks Argo CD, it cannot apply
its own fix. Revert the commit, then re-apply the chart by hand with the
`helm upgrade` form of the install command above (`helm upgrade argocd ...`
with the same flags); Argo CD resumes from `main`.

## Hand over to Argo CD (M1)

Once `platform/infra/` is on `main`, apply the root Application — the last
command run by hand against the cluster:

```bash
kubectl apply -f platform/infra/apps/root.yaml
kubectl -n argocd get applications
```

Expected within a few minutes: every Application in `platform/infra/apps/`
`Synced` and `Healthy`. `root` syncing is also the proof that Argo CD can read
the repository.

**On an empty cluster the Applications race.** `root` creates them all at
once, with no sync order, so `postgres-prod` and `postgres-staging` can try to
create a `Cluster` and `SealedSecret`s before CloudNativePG and Sealed Secrets
have installed those types. That sync fails and Argo CD retries it: its
automated sync retries five times with backoff by default (Argo CD v3.5.5 —
the retry is not set in our manifests). On 2026-10-28 `postgres-prod` failed
twice and succeeded on the third try, 32 s after `root` was applied; all nine
Applications were `Synced/Healthy`, with both Postgres `Cluster`s ready, 65 s
after it. A sync still failed after five tries stays failed until the next
commit; sync it once by hand
(`argocd app sync <app> --core --kube-context <node-name>`).

The first sync of `argocd` adopts the objects that `helm install` created, and
takes longer than the others. When a change restarts the application controller
itself, the Application can show `OutOfSync` until the restarted controller
re-reads it. Check whether a difference is real before acting:

```bash
kubectl config set-context --current --namespace=argocd
argocd app diff argocd --core      # no output means no difference
kubectl config set-context --current --namespace=default
```

`argocd` is the CLI (`brew install argocd`). `--core` makes it talk to the
cluster through the kubeconfig instead of logging in to the Argo CD API.

## Deploy (M1)

**How a commit reaches the cluster:** pull request → merge to `main` → Argo CD
notices the new commit on its next poll → applies the difference → the workload
picks it up. Nothing is run against the cluster by hand.

Argo CD **polls Git** every 120 s plus up to 60 s of jitter (the chart defaults
for `timeout.reconciliation`), so a merge lands within about three minutes. It
**watches the cluster** continuously, so a manual change there is reverted
almost at once (`selfHeal`).

Measured on 2026-10-13, on `hello`, a trivial nginx kept for this test until
2026-11-16, when it was removed to save its memory
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md));
any Application now shows the same loop:

| Test | Result |
|---|---|
| Merge a one-line change to `platform/infra/hello/configmap.yaml` | merged 13:34:59, applied by Argo CD 13:36:05, served by nginx ~20 s later; the pod did not restart |
| `kubectl -n hello scale deployment hello --replicas=2` by hand | reverted to 1 replica by Argo CD after 2 s |

**Check state:**

```bash
kubectl -n argocd get applications                       # Synced / Healthy per Application
kubectl -n argocd get application root -o jsonpath='{.status.sync.revision}'    # commit applied
```

A manual change to anything under Argo CD's management is not a deployment: it
is undone on the next pass. To change the cluster, change `main`.

**Removing an Application.** Deleting its file from `platform/infra/apps/`
makes `root` prune the Application, but not what the Application created:
no Application here carries Argo CD's resources finalizer, so its objects
are left running, unmanaged, and a namespace made by `CreateNamespace=true`
is never pruned. The operator deletes them by hand after the merge. For
`hello`, on 2026-11-16:

```bash
kubectl -n argocd get application hello          # NotFound: root pruned it
kubectl delete namespace hello                   # its Deployment, Service and ConfigMap go with it
```

## Sealed Secrets (M1)

Installed by Argo CD from `platform/infra/apps/sealed-secrets.yaml`
([ADR-0010](adr/0010-sealed-secrets.md)). The operator needs `kubeseal`
(`brew install kubeseal`).

**Seal a secret.** The value is read without echo, so it stays out of the
terminal and the shell history; the file name ends in `-sealed.yaml`, the only
secret-like name `.gitignore` lets through:

```bash
read -rs VALUE
kubectl create secret generic <name> -n <namespace> --from-literal=<key>="$VALUE" \
  --dry-run=client -o yaml | kubeseal --format yaml > platform/infra/<component>/<name>-sealed.yaml
unset VALUE
```

The sealed file is bound to `<name>` and `<namespace>`: renaming it or applying
it elsewhere makes it undecryptable, by design.

**Back up the sealing key — the operator, once after install and after every
rotation.** Without this file a rebuilt cluster cannot decrypt anything in Git:

```bash
kubectl -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-key.yaml
# add the file to 1Password as a Document, then delete the local copy
rm sealed-secrets-key.yaml
```

**Restore on a rebuild** — after Argo CD is installed and before the root
Application, so the controller finds the key when Argo CD first starts it and
never generates one of its own. Save the Document from 1Password to the home
folder (not a folder iCloud syncs), apply it, delete it:

```bash
kubectl --context <node-name> apply --dry-run=server -f ~/sealed-secrets-key.yaml   # "created (server dry run)"
kubectl --context <node-name> apply -f ~/sealed-secrets-key.yaml
rm ~/sealed-secrets-key.yaml
kubectl --context <node-name> -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key   # exactly one
```

The exported file still carries its old cluster's `resourceVersion` and `uid`;
on 2026-10-28 the API server accepted it as it is. If it ever refuses
(`resourceVersion should not be set`), strip those lines first:
`sed -E '/^[[:space:]]+(resourceVersion|uid|creationTimestamp):/d'`. Only if
the controller was already running when the key arrived, restart it so it
reloads its keys:
`kubectl --context <node-name> -n kube-system delete pod -l app.kubernetes.io/name=sealed-secrets`.

**Verify** once Argo CD has synced `sealed-secrets`: every `SealedSecret` is
`SYNCED True`, and the controller's certificate is the one the backup was
taken from. With the old cluster still up, compare the two certificates;
without it, `SYNCED True` on every `SealedSecret` is the evidence, since no
other key can decrypt them:

```bash
kubectl --context <node-name> get sealedsecrets -A
for c in <old-node-name> <node-name>; do kubeseal --context $c --fetch-cert | openssl x509 -noout -fingerprint -sha256; done
```

Exercised 2026-10-28: one key Secret, five `SealedSecret`s `SYNCED True`, and
the same fingerprint on both nodes.

## Postgres (M2)

Prod runs as the CloudNativePG `Cluster` `postgres` in namespace `prod`
([ADR-0012](adr/0012-cloudnative-pg.md)), created by Argo CD from
`platform/infra/postgres-prod/`. Services: `postgres-rw` (the primary, for
writes), `postgres-ro` and `postgres-r` (reads), all ClusterIP.

**Check it is healthy:**

```bash
kubectl -n argocd get application postgres-prod      # Synced / Healthy
kubectl -n prod get cluster postgres                 # "Cluster in healthy state"
```

**Run SQL as administrator.** There is no superuser password
(`enableSuperuserAccess: false`); the local socket inside the Pod is the way in:

```bash
kubectl -n prod exec -it postgres-1 -c postgres -- psql
```

**Credentials** are generated by the operator in the Secret `postgres-app`,
never in Git. List secrets by name only — `kubectl get secret -o yaml` prints
the values, base64-encoded rather than encrypted:

```bash
kubectl -n prod get secrets -o name
```

Connecting to prod from the operator's machine (port-forward over the tailnet)
is *not yet exercised*; it is written here when dlt and dbt first connect.

### Staging

Staging runs the same way in namespace `staging`, from
`platform/infra/postgres-staging/`: a `Cluster` `postgres` with the same
settings as prod's, the managed role `lagom_dev` and the `Database` `lagom-dev`
([ADR-0014](adr/0014-develop-against-staging.md)). Prod's NetworkPolicy
(`platform/infra/postgres-prod/networkpolicy.yaml`) drops traffic from staging.

Exercised on 2026-10-17, after the first deploy.

**Check it is healthy:**

```bash
kubectl -n argocd get application postgres-staging  # Synced / Healthy
kubectl -n staging get cluster postgres             # "Cluster in healthy state"
kubectl -n staging get database lagom-dev           # APPLIED true
kubectl -n staging describe resourcequota staging   # Used against Hard
```

**Check that staging cannot reach prod** — `BLOCKED` is the pass; run the same
command with `-n prod` as the control, which must print `REACHED`. The image is
a small unprivileged nginx pinned by digest, the one the `hello` app used until
2026-11-16; the node pulls it if it is not cached:

```bash
kubectl -n staging run nettest --rm -i --quiet --restart=Never \
  --image=nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86 -- \
  sh -c 'nc -z -w 4 postgres-rw.prod.svc.cluster.local 5432 && echo REACHED || echo BLOCKED'
```

Result on 2026-10-17: staging → prod `BLOCKED`; prod → prod and staging →
staging `REACHED`.

**Connect as the developer.** The password is in the Secret
`lagom-dev-credentials`, unsealed by the Sealed Secrets controller. Put it in an
environment variable of the current shell, so it is never typed or printed, and
forward the port:

```bash
export PGPASSWORD=$(kubectl -n staging get secret lagom-dev-credentials \
  -o jsonpath='{.data.password}' | base64 -d)
kubectl -n staging port-forward svc/postgres-rw 15432:5432 &
```

dlt and dbt read these as their own connection settings. For an ad-hoc query
without installing `psql`, a throwaway `uv` environment is enough:

```bash
uv run --no-project --with 'psycopg[binary]' python -c \
  "import psycopg; print(psycopg.connect(host='127.0.0.1', port=15432, user='lagom_dev', dbname='lagom_dev', sslmode='disable').execute('select current_user, current_database()').fetchone())"
```

**Use `sslmode=disable` over the port-forward.** With Postgres TLS, `kubectl
port-forward` exits after the first session closes ("connection reset by
peer… lost connection to pod"), so the next connection fails — found on
2026-10-17 on the first dlt load, reproduced with psycopg2: four sessions
without TLS kept the tunnel up, the second session with TLS killed it. The
hop is still encrypted end to end except on loopback: WireGuard (Tailscale)
to the node, TLS to the API server and from it to the kubelet; the password
travels as a SCRAM exchange, never in clear. In-cluster clients keep TLS.

The trade-off: the command line is recorded in shell history, but it holds the
Secret's name, not the password; the password lives in the shell's environment
until the shell exits (`unset PGPASSWORD` sooner), where any process the shell
starts can read it. A `~/.pgpass` file avoids the environment but leaves the
password on disk.

Verified on 2026-10-17: `lagom_dev` connects to `lagom_dev` over TLS; connecting
to `lagom` is refused before authentication, and Postgres logs
`pg_hba.conf rejects connection for host "127.0.0.1", user "lagom_dev",
database "lagom"` for both TLS and plain connections.

### Pull credentials, and the retired pipeline CronJob

The CronJob `usgs-pipeline` ran dlt, then dbt, on Postgres every 30 minutes,
first in staging on 2026-10-18 (9,929 events, dbt `PASS=32`, 37 s), then in
prod with the same digests. It was retired in both environments on
2026-11-07, when Dagster took over the alarm
([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)); its manifests
and runbook are in Git history. Dagster runs the pipeline
([Dagster (M3)](#dagster-m3)); a run on demand is *Launch run* on the `usgs`
job page of its UI.

Every Pod that pulls a private image needs the Secret `ghcr-pull` (a sealed
`dockerconfigjson`, [ADR-0015](adr/0015-images-on-ghcr.md)) in its namespace;
without it the Pod stays in `ImagePullBackOff`.

**Seal a pull credential** — a classic token with only `read:packages`, one per
namespace. Paste the token **when the command stops and waits**, never into the
command line, which the shell keeps in its history; the first line prints only
its length (a classic token is 40, and seals to 956 characters):

```bash
read -rs TOKEN && echo "token length: ${#TOKEN}" && kubectl create secret docker-registry ghcr-pull -n <namespace> \
  --docker-server=ghcr.io --docker-username=FilipeFantini --docker-password="$TOKEN" --dry-run=client -o yaml \
  | kubeseal --format yaml > platform/infra/postgres-<namespace>/ghcr-pull-sealed.yaml; unset TOKEN
grep -a -c 'ghp_' ~/.zsh_history ~/.zsh_sessions/* 2>/dev/null   # all 0: the token is in no history
```

If a token ever lands in the history, revoke it in GitHub's settings, delete
the lines (`sed -i '' '/ghp_/d'` on each file, with every Terminal window
closed first, since an open shell rewrites its history on exit), and seal a new
one — what happened on 2026-10-18.

### Pipeline heartbeat

Each environment's Dagster reports to its own check at Healthchecks.io
([ADR-0018](adr/0018-pipeline-heartbeat.md),
[ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)). The sensor
`usgs_success_heartbeat` pings it after each successful `usgs` run, so a
missing ping is the alarm for "nothing runs"; `run_failure_alarm` calls its
`/fail` with the job, the run ID and the error as soon as any run fails, so
the check goes down at once. A database password in that error is removed
before the body is sent; the host remains. Until 2026-11-07 the ping came from the
CronJob's `heartbeat` container. The account and the checks are made by hand,
in its console:

- One check per environment, `lagom-usgs-staging` and `lagom-usgs-prod`.
- Schedule: *Cron*, `*/30 * * * *`, time zone UTC, the `usgs` schedule.
- Grace: 20 minutes. Two samples so far: staging's runs of 2026-11-07, 15:30
  and 16:00 UTC, took 49 and 47 s from Pod start to completion, and the
  sensor evaluates every 30 s, so a healthy ping arrives in the first two
  minutes of the slot. A run is stopped at 25 minutes
  (`activeDeadlineSeconds`). Kubernetes keeps a finished Job for an hour
  only, so the range is re-read from the run list in the UI after a week and
  the grace corrected if a run comes near it.
- Integration: e-mail to the operator.

**Seal the ping URL**, one per namespace. The URL is a secret — whoever has it
can report a success — so it is pasted when the command waits, never typed on
the command line. It reaches the code server as `HEARTBEAT_URL`
(`platform/infra/dagster-<env>/values.yaml`):

```bash
read -rs HC_URL && echo "url length: ${#HC_URL}" && kubectl create secret generic pipeline-heartbeat -n <namespace> \
  --from-literal=url="$HC_URL" --dry-run=client -o yaml \
  | kubeseal --format yaml > platform/infra/postgres-<namespace>/pipeline-heartbeat-sealed.yaml; unset HC_URL
```

A Healthchecks.io ping URL is `https://hc-ping.com/` (20 characters) plus a
36-character UUID, 56 in all.

**Verify** after the change reaches the namespace:

1. The code server Pod is ready: it does not start without `HEARTBEAT_URL`.
   *Automation → Sensors* in the UI shows both sensors running.
2. After the next `usgs` run, the daemon logs
   `Sensor "usgs_success_heartbeat" acted on run status SUCCESS of run <id>`
   (`kubectl -n <namespace> logs deploy/dagster-daemon`), and the check turns
   *up*, with the run's ID in the ping's body.
3. To prove `/fail`, launch one failing run: on the `usgs` job page,
   *Materialize all* ▾ → *Open launchpad* → *Edit tags*, and give the tag
   `dagster-k8s/config` this value, which keeps the run's resources and points
   the catalog at a host that does not exist, so dlt fails before writing
   anything:

   ```json
   {"container_config": {"resources": {"requests": {"cpu": "50m", "memory": "768Mi"}, "limits": {"memory": "1Gi"}}, "env": [{"name": "DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG", "value": "postgresql://alarm-test.invalid/none"}]}}
   ```

   The daemon logs `Sensor "run_failure_alarm" acted on run status FAILURE`,
   the check goes *down* and e-mails, and the next scheduled run clears it.
   The tag lasts for that run only. Done in staging on 2026-11-07.

**When the e-mail arrives:** a `/fail` carries the job, the run ID and the
error in its body (*Events* of the check in the console). A missing ping
means no `usgs` run succeeded: check the run list in the UI, then
*Automation → Sensors* for a failed tick, then the daemon and the code server
Pods. A `/fail` that could not be sent after three attempts leaves only a
failed tick in the UI (ADR-0033, decision 6).

**Retire a manifest from `postgres-<env>`.** Those Applications never prune
(removing the `Cluster` from Git must never delete the database), so deleting
a file there leaves its object running, marked *requires pruning* in Argo CD.
Delete it by hand **right after Argo CD has synced the change**, from the
operator's machine. For the CronJobs this was part of the cutover, not a
clean-up: while one still ran it pinged the same check, and its success would
have cleared a Dagster `/fail`. Deleting a CronJob removes its Jobs and Pods
with it:

```bash
kubectl -n <namespace> delete cronjob usgs-pipeline
```

Staging's backup was retired the same way on 2026-11-16
([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)). After Argo CD has synced the `Cluster` without its plugin,
delete the objects left behind; the `ScheduledBackup` takes its `Backup`
objects with it, and the `SealedSecret` its Secret:

```bash
kubectl -n staging get cluster postgres -o jsonpath='{.spec.plugins}'   # empty: synced
kubectl -n staging get pod postgres-1                                    # 1/1 Running: restarted without the sidecar
kubectl -n staging delete scheduledbackup daily
kubectl -n staging delete objectstore r2-backups
kubectl -n staging delete sealedsecret r2-backups
```

The files in the bucket `lagom-staging-backups` are deleted by hand in
Cloudflare's dashboard, with the bucket and its token, when the operator
chooses; nothing reads them.

### The catalog's read-only role

The login `lake_reader` reads DuckLake's metadata tables and writes nothing
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)).
Think of the catalog's owner as the key-holder of a filing room and
`lake_reader` as a visitor with a pass to look at the index cards: CloudNativePG
makes the pass from the `Cluster`'s `managed.roles` (`postgres-<env>/cluster.yaml`),
and the pass needs a password, which is a sealed Secret, `lake-reader-credentials`,
per namespace. The job `lake_maintenance` then tells Postgres which cards the
visitor may read.

**Seal the password, one per namespace, before the change is merged.** The
password is generated and sealed in one pipe, so it is never printed, typed or
kept in the shell history. It is a basic-auth Secret, the shape CloudNativePG
reads for a role; the same Secret gives the render step its `PGPASSWORD` later.
Run from the repository root, with the kubeconfig that reaches the node and
Tailscale connected, since `kubeseal` asks the cluster for the controller's
public key:

```bash
for ns in staging prod; do
  f="platform/infra/postgres-$ns/lake-reader-credentials-sealed.yaml"
  openssl rand -hex 24 | { read -r PW && kubectl create secret generic lake-reader-credentials -n "$ns" \
    --type=kubernetes.io/basic-auth --from-literal=username=lake_reader --from-literal=password="$PW" \
    --dry-run=client -o yaml | kubeseal --format yaml > "$f.tmp" && mv "$f.tmp" "$f" || rm -f "$f.tmp"; unset PW; }
done
```

The output goes to a temporary file that is renamed only when `kubeseal`
succeeds: a plain `>` creates the file before the command runs, so a failed run
would leave an empty file that could be committed by mistake. Each namespace
gets its own password. Commit the two `-sealed.yaml` files on the
pull request's branch. `kubeseal` reads the controller's public key from the
cluster, as for the other sealed Secrets.

**Rollout order.** The tags that give each job its Secrets are in the image, so
the image comes before the removal of the same variables from the values:

1. Merge the pull request that adds the tags, the role and the sealed Secrets.
   CI builds the image for that commit. The values still hold the credentials,
   so runs are unchanged: the tags' copies are identical and kept once.
2. In staging, pin that digest and remove the credentials from
   `dagster-staging/values.yaml` in one pull request (the variables the code
   server keeps are in ADR-0044, decision 2). Check a `usgs` run succeeds.
3. After the checks below pass in staging, the same for prod, with staging's
   digest (the promotion check enforces it).

**Reload the code location before a manual run.** After the image changes,
Dagster's webserver can keep the job definitions it loaded from the old one.
A run started from the Launchpad then carries the old tags, and with them the
old `dagster-k8s/config`: in staging on 2026-11-14 that tag held only the
resources, and `lake_maintenance` started with no credentials. The daemon,
which starts scheduled runs, reloads by itself, so the scheduled `usgs` run
had the new tags. In the UI, *Deployment → Code locations → lagom → Reload*,
then check that the `dagster-k8s/config` tag shown in the Launchpad begins with
`{"container_config": {"env": ...`.

**Check it in staging** (replace `staging` with `prod` after):

Since 2026-11-16 staging has no Dagster UI ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)): where a step below names
the Launchpad or a reload, it applies to prod; in staging, a new digest rolls
the code server by itself, and the scheduled run is the check.

```bash
# 1. CloudNativePG created the role: it is listed as reconciled, with no password shown
kubectl --context <node-name> -n staging get cluster postgres -o jsonpath='{.status.managedRolesStatus.byStatus}'
# 2. Start lake_maintenance (Jobs -> lake_maintenance -> Launchpad); its step grant_lake_reader succeeds.
#    Then read the privileges from inside Postgres (local socket, no password):
kubectl --context <node-name> -n staging exec postgres-1 -c postgres -- psql -d lagom -At -c \
  "select has_schema_privilege('lake_reader','ducklake','USAGE'), has_database_privilege('lake_reader','lagom','CONNECT'), \
   (select count(*) from information_schema.tables t where t.table_schema='ducklake' and not has_table_privilege('lake_reader', format('%I.%I', t.table_schema, t.table_name), 'SELECT'))"
# expected: t|t|0  (usage, connect, and no table in the schema without SELECT)
# 3. The role can read and cannot write: an attach with read_only=True and no password in the
#    string (PGPASSWORD from the Secret) lists the tables and refuses an INSERT. Not scripted here:
#    the job `dashboards` does it, and its staging run is this check (*The dashboards' publishing path*).
#    Done once, on 2026-11-14, against a throwaway Postgres: it reads, refuses INSERT and CREATE,
#    and a failed attach prints no password.
```

If `lake_maintenance` fails with `the role lake_reader does not exist`, the
Secret is not sealed in that namespace yet or CloudNativePG has not reconciled
it: step 1 shows which. The daily job fails and e-mails until then, by design
(ADR-0044, decision 6); compaction still runs.

**Rotate the password:** seal a new one with the command above for that
namespace only, commit, and let Argo CD apply it; CloudNativePG changes the
role's password to match. A reader holding the old one must restart.

### The dashboards' publishing path

The job `dashboards` renders the boards and uploads them to a public bucket
([ADR-0044](adr/0044-per-job-credentials-and-the-dashboards-publishing-path.md)).
Think of the bucket as a shop window: Cloudflare serves whatever is in it to
anyone, on the custom domain. The job holds a key that can change the window and
nothing else, and a second key that can only look at the lake. Four Secrets per
namespace make this work; the first is *The catalog's read-only role* above:

| Secret | Keys | What it is |
|---|---|---|
| `lake-reader-credentials` | `username`, `password` | the catalog role `lake_reader` |
| `r2-lake-reader` | `ACCESS_KEY_ID`, `ACCESS_SECRET_KEY`, `ENDPOINT` | an R2 token that can **read** the lake bucket (`lagom-<env>-lake`) |
| `r2-dashboards` | the same three keys | an R2 token that can read and write **only** the dashboards bucket (`lagom-<env>-dashboards`) |

**What exists in Cloudflare** (set up by the operator): per environment, a bucket
`lagom-staging-dashboards` or `lagom-prod-dashboards`, public through a custom
domain (`lagom-staging.ofantini.com`, `lagom.ofantini.com`), with the minimum TLS
version 1.2, the `r2.dev` address off, and the tokens above, each limited to one
bucket. Check these in the dashboard after any change: a token with account-wide
scope, or `r2.dev` left on, defeats the reason for the design.

**Seal the two R2 tokens, one pair per namespace, before the change is
rolled out.** Cloudflare shows a token's key and secret once, at creation. The
command reads them without echoing them (`read -s`), refuses a value of the
wrong shape before sealing it (an R2 key ID is 32 hex characters, its secret 64,
the endpoint `https://<account-id>.r2.cloudflarestorage.com`: a value pasted into
the wrong prompt is caught here, not by a failed run), seals them in one pipe, and
writes through a temporary file that is renamed only when `kubeseal` succeeds, so
a failed run leaves no empty file to commit. Run from the repository root (the
paths are relative to it), with the kubeconfig that reaches the node and
Tailscale connected:

```bash
seal_r2() {  # seal_r2 <namespace> <secret name> <file name>
  local ns="$1" name="$2" f="platform/infra/postgres-$1/$3" key secret endpoint
  # printf, not `read -p`: in zsh, macOS's shell, -p reads from a coprocess.
  printf '%s access key ID (%s): ' "$name" "$ns"; read -rs key; echo
  printf '%s secret access key (%s): ' "$name" "$ns"; read -rs secret; echo
  printf '%s endpoint (%s): ' "$name" "$ns"; read -rs endpoint; echo
  if [[ $key =~ ^[0-9a-f]{32}$ && $secret =~ ^[0-9a-f]{64}$ &&
        $endpoint =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com$ ]]; then
    kubectl create secret generic "$name" -n "$ns" --from-literal=ACCESS_KEY_ID="$key" \
      --from-literal=ACCESS_SECRET_KEY="$secret" --from-literal=ENDPOINT="$endpoint" \
      --dry-run=client -o yaml | kubeseal --format yaml > "$f.tmp" && mv "$f.tmp" "$f" && echo "ok: $f" \
      || { rm -f "$f.tmp"; echo "FAILED: $f"; }
  else
    echo "REJECTED $ns/$name: lengths ${#key} ${#secret} ${#endpoint}, expected 32 64 65"
  fi
  unset key secret endpoint
}
for ns in staging prod; do
  seal_r2 "$ns" r2-lake-reader r2-lake-reader-sealed.yaml
  seal_r2 "$ns" r2-dashboards r2-dashboards-sealed.yaml
done
```

Commit the four `-sealed.yaml` files. Each namespace takes its own tokens (the
read-only lake token and the dashboards token, each limited to that
environment's bucket). **Do not paste a token into a chat, a file or the shell
history**; if one was shown anywhere, revoke it in Cloudflare and make another.

**Rollout order.** The job, the sensor and the Secrets' names are in the image;
the bucket's name is in the values. Seal the Secrets first, then merge. Without
them a `dashboards` run's Pod stays in `CreateContainerConfigError` and the run
fails (the alarm e-mails; Dagster's run monitoring marks a run whose Pod never
starts as failed after its start timeout, the chart's default, to confirm in
staging). Then pin the new image's digest in staging's values, reload the code
location (*Deployment → Code locations → lagom → Reload*), and run the checks
below; prod takes staging's digest after they pass.

**Check it in staging** (replace `staging` with `prod` after):

Since 2026-11-16 staging has no Dagster UI ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)): where a step below names
the Launchpad or a reload, it applies to prod; in staging, a new digest rolls
the code server by itself, and the scheduled run is the check.

1. **The Secrets exist** (names only; never read their values):
   `kubectl --context <node-name> -n staging get secret lake-reader-credentials r2-lake-reader r2-dashboards`.
2. **The job is there with the right environment.** *Jobs → dashboards → Launchpad*
   shows a `dagster-k8s/config` tag whose `env` names `PGPASSWORD`,
   `LAGOM_R2_*` from `r2-lake-reader` and `LAGOM_DASHBOARDS_*` from
   `r2-dashboards`, and no `PG_URI`.
3. **The run succeeds.** Launch it from the Launchpad, or wait for the next
   `usgs` run to end. The log ends with `published 1 page(s): earthquakes.html`.
   Read `dct validate`, `dct render` and `check_boards` durations in the log: a
   command near its timeout (180, 360, 120 s) says something is stalling, and
   telemetry is the first suspect. If a blocked or slow telemetry endpoint makes
   any command fail or wait, put `DO_NOT_TRACK=1` back in the step (ADR-0044,
   decision 9). The unit tests prove a stalled command is killed; they do not prove
   how the node's network behaves, so this run is the evidence.
4. **The page is live with its watermark.**
   `curl -sI https://lagom-staging.ofantini.com/earthquakes.html` returns 200,
   `content-type: text/html; charset=utf-8` and `cache-control: public, max-age=300`;
   the page, opened in a browser, shows `Page built at <time> UTC` above the
   title, within minutes of the run. The root of the domain is not a page: R2
   serves no index document, so the address of a board is the file's name.
5. **The read-only attach works with the real role.** Step 3 passing is the
   proof: the catalog string has no password, so a connection means libpq read
   `PGPASSWORD`. To see that the role cannot write, no job is needed: the profile
   attaches read-only and a test refuses a write; the catalog role itself was
   checked in *The catalog's read-only role*.
6. **A failed attach does not leak the password.** From the Launchpad, launch
   `dashboards` with the run tag `dagster-k8s/config` set to the job's own tag
   (copied from step 2) with `PGPASSWORD` given as the plain value
   `wrong-password-for-test`. The run fails at `dct validate`. Read the run's log
   and the failure e-mail: neither the test string nor the real password may
   appear. The test string is not a secret; it is what the check looks for.
7. **Memory.** The run's log ends with "memory peak of the run's container", read
   from the container's cgroup (`/sys/fs/cgroup/memory.peak`): the run's process
   and every command it started. `kubectl top` does not do: the render's Pod
   lives less than a minute and metrics-server samples once a minute, so the
   first staging runs never showed in it (2026-11-15). The request is 768 MiB
   and the limit 1 GiB; the fixture peaked at 540 MiB, and staging on real data
   at 804 MiB (2026-11-15, the run of 18:31). Record a new peak here and in [architecture.md §11](architecture.md#11-resource-budget);
   if it comes within 100 MiB of the limit, raise the job's own resources, not
   `usgs`'s.
8. **The alarm still works for it.** After the above, a failed run in step 6 must
   have sent one e-mail naming the job `dashboards`.

**If a render fails,** the old pages stay up (nothing is deleted before the new
ones are uploaded) and show their age in their watermark. The run's log says which
command failed; its error has the password and keys removed.

**Rotate a token:** make a new one in Cloudflare, seal it with the function above
for that namespace, commit, let Argo CD apply it, then revoke the old one. The
next run reads the new value; a running Pod keeps the old until it ends.

## Operator UIs behind Cloudflare Access (M3)

The operator's tools share one HTTPS address, one path per tool, behind a
login ([ADR-0030](adr/0030-operator-uis-behind-cloudflare-access.md)):

- `https://ops.ofantini.com/dagster/` is prod's Dagster. Staging's,
  `/dagster-staging/`, was served until 2026-11-16, when staging's UI was
  removed ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)); the path now gets the tunnel's 404.
- `https://ops.ofantini.com/grafana/` is Grafana, for both environments
  ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md);
  *The streaming showcase in staging (M6), Grafana*). It serves itself under
  the prefix (`serve_from_sub_path`) and has no login of its own.

Bookmark them with the final slash. Without it, Dagster answers with a
redirect to the address with the slash, and since it does not know the
request came in over HTTPS, that redirect says `http://`. *Always Use HTTPS*
at the zone sends the browser back to HTTPS, at the cost of one extra hop.

**What each piece is:**

| Piece | What it is |
|---|---|
| Tunnel, `cloudflared` | A Pod that dials out to Cloudflare and keeps the connections open; requests for `ops.ofantini.com` come back down them. Nothing on the node listens for the internet, so no port opens. Think of a phone line we call out on, which then carries incoming calls |
| Ingress rule | One line in [`cloudflared/config.yaml`](../platform/infra/cloudflared/config.yaml): a hostname plus a pattern on the path, mapped to a Service in the cluster. The first rule that matches wins; anything unmatched gets a 404 |
| Path prefix | The part of the address that names the tool (`/dagster`). The tunnel passes it on unchanged, so each tool must serve its pages under it. Dagster's is `dagsterWebserver.pathPrefix` in its `values.yaml` |
| Access application | Cloudflare's doorman for `ops.ofantini.com`, every path included. It shows a login page and lets through only the e-mail addresses in its policy |
| One-time PIN | The login: Access e-mails a code to the address, and the code opens a session of 24 hours |
| JWT | A token Access signs and adds to every request after login. `cloudflared` checks it before forwarding, so a request that skipped the doorman is refused |
| Proxied CNAME | The DNS record for `ops.ofantini.com`. The world sees Cloudflare's addresses, never the node's |

**Set it up**, once, in this order, so that the hostname never answers
before its login exists. Most of Cloudflare's labels below were checked against its
documentation on 2026-11-07; the dashboard can rename them.

**1. Secure the Cloudflare account first.** Whoever holds the account holds
the domain, the DNS, the login policy and the tunnel.

- *My Profile → Authentication → Two-Factor Authentication*: turn it on, with
  a security key or an authenticator app, and store the backup codes in
  1Password.
- Keep two-factor on the e-mail account that receives the one-time PINs too:
  that inbox is the login to every tool.

**2. Zero Trust organisation**, in the Cloudflare dashboard:

1. Open **Zero Trust**. On the onboarding screen, choose the **team name**.
   It becomes `<team-name>.cloudflareaccess.com` and shows on the login page,
   so pick a neutral one, not a personal name.
2. Choose the **Zero Trust Free** plan. It asks for payment details and does
   not charge them.
3. **Zero Trust → Integrations → Identity providers**: if **One-time PIN** is
   not already under *Your identity providers*, select **Add new identity
   provider → One-time PIN**.

**3. The Access application:**

1. **Zero Trust → Access controls → Applications → Create new application →
   Self-hosted and private**.
2. Name: `Lagom operator tools`.
3. **Add public hostname**: subdomain `ops`, **Domain** `ofantini.com`, path
   empty, so that every path is covered.
4. **Access policies**: create a new one.
   - Policy name: `Operator only`.
   - Action: **Allow**.
   - Include, selector **Emails**: `<operator-email>`.
   - Never add *Everyone*, and never a *Bypass* policy.
5. **Login methods**: only **One-time PIN**. Turn on **Apply instant
   authentication**, which skips the choice of method.
6. **Session Duration**: `24 hours`.
7. **Additional settings → Cookie settings**: turn on **Binding Cookie**,
   which ties the session cookie to the browser that logged in.
8. **Create**.
9. Give the architect two values:
   - the **Application Audience (AUD) Tag**, from *Applications → Edit →
     Additional settings*;
   - the team name, from **Zero Trust → Settings**.

   Neither is a secret: both appear in the login page's URL. They replace
   `<access-aud-tag>` and `<team-name>` in `cloudflared/config.yaml`.

**4. The zone's settings**, under *ofantini.com* in the dashboard:

- **SSL/TLS → Overview**: encryption mode **Full (strict)**.
- **SSL/TLS → Edge Certificates**:
  - **Always Use HTTPS**: on.
  - **Minimum TLS Version**: 1.2.
- **DNS → Settings**: **DNSSEC** on. With Cloudflare as the registrar, this
  is one click.
- **Domain Registration → Manage**: **Registrar lock** stays on.
- **DNS → Records**: every record for a tool is a proxied CNAME to the
  tunnel (orange cloud). Never add a record with the node's public address.

**5. The tunnel and its token**, on the operator's Mac, from the repository's
root. The token goes straight from `cloudflared` into `kubeseal` and is never
shown:

```bash
brew install cloudflared
cloudflared tunnel login                  # browser: authorise the zone ofantini.com; writes ~/.cloudflared/cert.pem
cloudflared tunnel create lagom           # writes ~/.cloudflared/<tunnel-id>.json
cloudflared tunnel route dns lagom ops.ofantini.com
cloudflared tunnel token lagom \
  | kubectl create secret generic cloudflared-token -n cloudflared --from-file=token=/dev/stdin --dry-run=client -o yaml \
  | kubeseal --context <node-name> --format yaml > platform/infra/cloudflared/token-sealed.yaml
grep -c 'token:' platform/infra/cloudflared/token-sealed.yaml   # 1: one sealed key
rm ~/.cloudflared/cert.pem ~/.cloudflared/*.json
```

**Why it works:**

- `tunnel login` gives the Mac a certificate that can create tunnels and DNS
  records in the zone. It is deleted at the end.
- `tunnel create` registers the tunnel and its secret.
- `route dns` writes the proxied CNAME that points `ops.ofantini.com` at the
  tunnel.
- `tunnel token` prints the ID and secret as one string. `cloudflared` in the
  cluster reads it from a file (`--token-file`), while the routing rules stay
  in `config.yaml`, in Git.

Once `config.yaml` holds the team name and AUD tag, check that `cloudflared`
reads its rules as intended. The rules sit inside a ConfigMap, so the first
command extracts them:

```bash
sed -e '1,/config.yaml: |/d' -e 's/^    //' platform/infra/cloudflared/config.yaml > /tmp/cloudflared.yaml
cloudflared tunnel --config /tmp/cloudflared.yaml ingress validate                                   # OK
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/dagster/runs   # prod's rule
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/dagster-staging  # the 404 rule since 2026-11-16
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/grafana/d/streaming  # Grafana's rule
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/               # the 404 rule
rm /tmp/cloudflared.yaml
```

**6. Merge** the pull request holding `token-sealed.yaml`, the filled-in
`config.yaml` and the Dagster path prefixes. Argo CD creates the namespace,
the Secret and the Pod, and rolls each Dagster webserver.

**Verify:**

```bash
kubectl --context <node-name> -n cloudflared get pods                       # cloudflared 1/1 Running
kubectl --context <node-name> -n cloudflared logs deploy/cloudflared | grep -c 'Registered tunnel connection'   # 4
kubectl --context <node-name> -n prod get pods -l component=dagster-webserver      # 1/1 Running: the probe follows the prefix
kubectl --context <node-name> -n staging get pods -l component=dagster-webserver   # none since 2026-11-16: staging has no UI
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://ops.ofantini.com/dagster/server_info
# 302 to https://<team-name>.cloudflareaccess.com/...: the login comes first
```

Then, in a private browser window, open both addresses. Each must show the
Access login page, then take the code by e-mail, then show Dagster. Then, on
each:

- **Click through:** *Runs*, one run, *Assets*, *Jobs*. Every page and link
  stays under its prefix. A broken prefix shows as a 404 or as a jump to
  `ops.ofantini.com/` without it.
- **Watch it update:** the run list updates by itself while a run is in
  progress, which means the WebSocket goes through the tunnel.

A second private window with an address outside the policy must get no code.

**Not verified end to end: the tunnel's own JWT check.** Seeing it refuse a
request would mean turning Access off. What is checked instead: the rules
parse, and the `access` block applies to every rule (`ingress validate` and
`ingress rule` in step 5).

**Memory.** After a week:

```bash
kubectl --context <node-name> -n cloudflared top pod --containers
```

Set the request in `deployment.yaml` from the figure, as for every component
([architecture.md §11](architecture.md#11-resource-budget)).

**Add a tool** (Airflow, for instance):

1. Configure the tool to serve under its path (Airflow: `base_url`
   `https://ops.ofantini.com/airflow`). Point any HTTP probe it has at the
   prefixed path.
2. Add a rule in `config.yaml`, before the final 404:
   - `hostname: ops.ofantini.com`;
   - `path: ^/airflow(/.*)?$`;
   - the tool's Service.

   Then bump `lagom.dev/config-revision` in `deployment.yaml`.
3. If the tool runs in `prod`, add a NetworkPolicy like
   `allow-cloudflared-to-dagster-webserver`.

The hostname, DNS and Access need nothing new.

**If an address fails:**

- **Cloudflare error 1033:** the tunnel has no connection. Check the Pod and
  its logs.
- **404 from the tunnel:** no rule matches the path. Check the path against
  `config.yaml`.
- **502:** the rule matched, but the Service behind it does not answer. Check
  the tool's Pod.
- **A page that loads without its styles, or links that drop the prefix:**
  the tool's path prefix is not set, or not set to the same path as the rule.
- **403 from `cloudflared` after login:** the token's audience does not match
  `audTag`. Re-check the AUD tag.
- **In the meantime,** the port-forward still works. Run `kubectl --context
  <node-name> -n <namespace> port-forward svc/dagster-dagster-webserver
  3000:80`, then browse `http://localhost:3000/dagster` (prod). Staging has no
  webserver to forward to since 2026-11-16.

## Backup and restore (M3)

Prod's Postgres is backed up to its R2 bucket by the Barman Cloud plugin, and
staging's was until 2026-11-16 ([ADR-0025](adr/0025-postgres-backup-barman-cloud-to-r2.md)):

| Environment | Bucket | Retention |
|---|---|---|
| staging | none since 2026-11-16 ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)); `lagom-staging-backups` keeps the old files until they are deleted by hand | — |
| prod | `lagom-prod-backups` | 14 days ([ADR-0027](adr/0027-hourly-wal-archiving-and-14-day-prod-retention.md)) |

Two kinds of copy land there:

- **WAL**, continuously. Each write-ahead log segment is uploaded as soon as
  Postgres closes it: when its 16 MiB fill, or after an hour
  (`archive_timeout`, ADR-0027). CloudNativePG's default of 5 minutes shipped
  a whole segment each time and filled the bucket by the clock.
- **A base backup** every day at 03:00 UTC.

A restore replays WAL on top of a base backup. It can reach any moment up to
the last archived segment, so at most about **1 hour of writes** is lost
(the RPO). Pipeline data is rebuilt by a re-load; Dagster's run history of that
hour is not.

**Set it up for an environment** (exercised in staging, 2026-10-28):

1. In the Cloudflare console, R2: create the bucket `lagom-<env>-backups`
   with *Location hint: Western Europe*. Then create an API token with
   *Object Read & Write* on that bucket only, TTL *Forever*. Save the
   access key, the secret and the S3 endpoint (no bucket in it) in 1Password
   as `R2 Storage lagom-<env>-backups`.
2. Seal it. Each prompt waits for a value pasted from 1Password, and nothing
   is echoed. Expected lengths are 32, 64 and 65:

   ```bash
   printf 'Access Key ID: ' && read -rs KEY_ID && echo && printf 'Secret Access Key: ' && read -rs SECRET && echo && printf 'S3 endpoint: ' && read -rs ENDPOINT && echo && echo "lengths: ${#KEY_ID} ${#SECRET} ${#ENDPOINT}" && kubectl --context <node-name> create secret generic r2-backups -n <env> --from-literal=ACCESS_KEY_ID="$KEY_ID" --from-literal=ACCESS_SECRET_KEY="$SECRET" --from-literal=ENDPOINT="$ENDPOINT" --dry-run=client -o yaml | kubeseal --context <node-name> --format yaml > platform/infra/postgres-<env>/r2-backups-sealed.yaml; unset KEY_ID SECRET ENDPOINT
   ```

3. Add the `ObjectStore`, the `ScheduledBackup` and `spec.plugins` to the
   environment's directory, then merge. On a running Cluster the operator
   restarts the Postgres Pod once to add the sidecar. That took a few seconds
   in staging.

**Check it works:**

```bash
kubectl --context <node-name> -n <env> get cluster postgres -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
kubectl --context <node-name> -n <env> get backups
kubectl --context <node-name> -n <env> logs postgres-1 -c plugin-barman-cloud --since=10m | grep -E 'Archived WAL file|Backup completed'
```

`ContinuousArchiving=True` and `LastBackupSucceeded=True` are the pass. If
archiving fails, Postgres keeps the WAL it could not ship on its volume, so
the volume grows: read the sidecar's log.

**The first backup after enabling can fail.** The `ScheduledBackup` takes one
backup at once (`immediate: true`). On a Cluster that is still restarting to
add the plugin, that backup fails with `cannot proceed with the backup as the
cluster has no plugin configured`. This happened in staging on 2026-10-28.
Take one by hand once the Pod is back:

```bash
N=manual-$(date -u +%Y%m%d%H%M); kubectl --context <node-name> -n <env> apply -f - <<YAML && kubectl --context <node-name> -n <env> wait backup/$N --for=jsonpath='{.status.phase}'=completed --timeout=600s
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: $N
spec:
  cluster:
    name: postgres
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
YAML
```

The heredoc is unquoted so the shell fills in `$N`. Nothing else in it
starts with `$`.

**Test a restore** into a temporary Cluster in the same namespace: `prod`,
the only environment backed up since 2026-11-16. It reads the bucket and
archives nothing of its own. Its memory request is small, since it runs beside
prod's Postgres. Then compare the row counts and delete it:

```bash
kubectl --context <node-name> -n <env> apply -f - <<'YAML' && kubectl --context <node-name> -n <env> wait cluster/restore-test --for=condition=Ready --timeout=900s
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: restore-test
spec:
  instances: 1
  imageName: <the imageName of the environment's Cluster>
  storage: {size: 5Gi, storageClass: local-path}
  resources:
    requests: {cpu: 10m, memory: 128Mi}
    limits: {memory: 512Mi}
  bootstrap:
    recovery: {source: origin, database: lagom, owner: lagom}
  externalClusters:
    - name: origin
      plugin:
        name: barman-cloud.cloudnative-pg.io
        parameters: {barmanObjectName: r2-backups, serverName: postgres}
YAML
for c in postgres restore-test; do echo "$c | $(kubectl --context <node-name> -n <env> exec $c-1 -c postgres -- psql -d lagom -At -F' | ' -c "select (select count(*) from bronze.usgs_events), (select count(*) from silver.stg_usgs__events), (select count(*) from gold.fct_earthquakes)")"; done
kubectl --context <node-name> -n <env> delete cluster restore-test
```

Result in staging on 2026-10-28: ready 68 s after creation. The copy held
every row up to the last archived WAL (14:28 UTC). The 9 rows a pipeline run
wrote at 14:30, in a segment not yet closed, were missing, as the RPO above
predicts (the timeout was 5 minutes then). Deleting the Cluster deletes its volume: local-path released it
within two minutes.

**Retention** is enforced by the sidecar every 30 minutes. To check it: an
expired backup must be gone from `kubectl get backups` and from the bucket,
with no `BadDigest` or `DeleteObjects` error in the sidecar's log. Without R2
credentials the bucket is checked through the catalog, or by the operator
listing `postgres/base/` in the Cloudflare console (ADR-0025 notes):

```bash
kubectl --context <node-name> -n <env> logs postgres-1 -c plugin-barman-cloud --since=24h | grep -iE 'retention|delete|error'
```

A deletion logs `Deleting backup not in the catalog` with the backup's name,
and the ObjectStore's `status.serverRecoveryWindow` moves its
`firstRecoverabilityPoint` forward. Staging showed both on 2026-11-05.

**Memory measured** on 2026-10-28 is in [architecture.md §11](architecture.md#11-resource-budget): the sidecar used 18 Mi during a
base backup.

## Dagster (M3)

Each environment runs its own Dagster in its namespace
([ADR-0026](adr/0026-dagster-one-instance-per-environment.md)), from
`platform/infra/dagster-<env>/values.yaml`. Three Pods stay up:

- the **webserver**, the UI;
- the **daemon**, which evaluates the schedules and starts queued runs;
- the **code server** (`dagster-dagster-user-deployments-lagom-…`), which
  loads the code location and answers the other two.

A **run** is not one of them. For each run the daemon creates a Kubernetes
Job named `dagster-run-<run id>`, whose Pod uses the code server's image and
environment plus the Secrets its job names ([architecture.md §7](architecture.md#7-orchestration--dagster)); its memory is in [architecture.md §11](architecture.md#11-resource-budget). The Job and its Pod are deleted an hour after they
finish. The run's history stays in the database `dagster` of the same
environment's Postgres.

Exercised in staging on 2026-10-28; prod runs the same since 2026-11-05.
In prod, replace `staging` with `prod`.

**The jobs** (code in [`platform/orchestration/`](../platform/orchestration/README.md)):

| Job | Schedule (UTC) | What it does |
|---|---|---|
| `usgs` | every 30 minutes, at :00 and :30 | dlt loads the events USGS updated since the last cursor into DuckLake (`bronze.usgs_events`), then `dbt build` builds silver and gold on the `lake` target and runs their 33 tests |
| `lake_maintenance` | daily at 04:15 | merges small Parquet files, expires snapshots older than 7 days, deletes files nothing references; its second step, `grant_lake_reader`, gives `lake_reader` its grants |
| `dashboards` | after each successful `usgs` run (the sensor `render_dashboards_after_usgs`) | renders the boards against the lake, checks them, and publishes them to the environment's dashboards bucket ([architecture.md §9](architecture.md#9-visualization); runbook: *The dashboards' publishing path*) |

All three carry the tag `lagom/pipeline=usgs`, and the daemon's queue starts one
run with that tag at a time. A second run waits as *Queued* until the first
ends. In staging's first week (2026-10-28 to 11-05), 372 of 373 `usgs` runs
succeeded, in 43 s at the median and 136 s at most; the one failure was the
driver pitfall below. All 8 `lake_maintenance` runs succeeded, in 10 to 17 s.
In the 21:30 run of 2026-10-28, the load took 7 s and dbt 23 s; the rest was
Dagster's own start-up and per-step overhead.

**Check it is healthy:**

```bash
kubectl --context <node-name> -n argocd get application dagster-staging   # Synced / Healthy
kubectl --context <node-name> -n staging get pods | grep dagster           # two Running (no webserver since 2026-11-16), plus finished runs
```

**Open the UI.** Prod's is `https://ops.ofantini.com/dagster`, behind the
Access login
([Operator UIs behind Cloudflare Access](#operator-uis-behind-cloudflare-access-m3)).
Staging has no UI since 2026-11-16 ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)): its runs are read
from the terminal, below. When prod's is down, `kubectl port-forward` reaches
it, with the kubeconfig. The UI has no login of its own: whoever reaches the
port controls Dagster. Leave the command running, then browse
http://localhost:3000/dagster (the webserver serves under its path prefix);
stop it with Ctrl+C:

```bash
kubectl --context <node-name> -n prod port-forward svc/dagster-dagster-webserver 3000:80
```

*Runs* lists every run with its status and duration; open one to see each
step, its logs and the dbt tests as asset checks. *Jobs* and *Automation*
show the two jobs and their schedules. Times are shown in the browser's time
zone. The first visit shows a newsletter prompt: *Skip* closes it.

**Find and read a run from the terminal**, when the UI is not open or the
run's Pod still exists:

```bash
kubectl --context <node-name> -n staging get jobs | grep dagster-run            # Complete or Failed, with duration
kubectl --context <node-name> -n staging logs job/dagster-run-<run id> \
  | grep -E ' - (RUN_SUCCESS|RUN_FAILURE|STEP_SUCCESS|STEP_FAILURE) - |loaded .* rows|PASS='
kubectl --context <node-name> -n staging logs deploy/dagster-daemon --since=1h | grep -E 'schedule|Launched'
```

A healthy run shows `loaded N rows into usgs_events`, two `STEP_SUCCESS`
(`usgs__events`, then `warehouse`), dbt's `PASS=33`, and `RUN_SUCCESS`. The
daemon logs `Completed scheduled launch of run …` at each :00 and :30. After
an hour the Pod is gone, and the run is read in the UI.

**Start a run on demand** (prod; staging has no UI, so a change there is
validated by the next scheduled run: `usgs` every 30 minutes, `dashboards`
after it, `lake_maintenance` at 04:15 UTC). In the UI, *Jobs → usgs →
Materialize all*. For
`lake_maintenance`, which has no assets, *Jobs → lake_maintenance → Launchpad
→ Launch Run*. The run appears in *Runs*. If a scheduled run is in progress,
it waits as *Queued*. Exercised on 2026-11-05: a `usgs` run started at
11:17 UTC, loaded 4 rows, `PASS=33`, in under a minute.

Start runs from the job's page, not from *Catalog* or the asset graph. A run
started there belongs to Dagster's implicit asset job, which has none of the
jobs' tags: since 2026-11-16 the queue holds every run to one at a time, so
it no longer overlaps a scheduled load on dlt's cursor, but it still takes the code server's memory
instead of the run's ([architecture.md §11](architecture.md#11-resource-budget)), and it has no credentials ([architecture.md §7](architecture.md#7-orchestration--dagster)), so it fails at its
first asset with a missing variable.

**Pitfalls hit on the way in** (staging, 2026-10-28). Each made the code
server or the first runs fail:

- **An `ENTRYPOINT` in the image** (PR #33). The chart and the run launcher
  each pass a full command starting with `dagster`. With
  `ENTRYPOINT ["dagster"]` the container ran `dagster dagster api grpc …` and
  failed with `No such command 'dagster'`. The image has no `ENTRYPOINT`, and
  CI imports the definitions inside the built image.
- **psycopg 3 missing** (PR #34). SQLAlchemy 2.1 opens `postgresql://` URLs
  with psycopg 3, which `dagster-postgres` does not declare. The schedule's
  first tick failed with `ModuleNotFoundError: No module named 'psycopg'`, and
  no run was launched.
- **The wrong driver for Dagster's SQL** (PR #36). With psycopg 3 installed,
  the first run failed in Dagster's run storage with
  `syntax error at or near "$1"`: psycopg 3 sends parameters to the server
  separately, a form Dagster's statements were not written for.
  `postgresql.postgresqlScheme: postgresql+psycopg2` in the values selects
  psycopg2 for every Dagster Pod, without rebuilding the image.

**On a first deploy the daemon and webserver may restart once.** In prod, on
2026-11-05, they started before CloudNativePG had created the database
`dagster`, retried with `FATAL: database "dagster" does not exist`, exited,
and came up on the restart; the code server and the first runs were
unaffected. A restart count of 1 right after the first sync is this. One
that keeps rising is not: read `kubectl logs --previous`.

**The first pull is slow.** The image is 961 MiB ([§9a](architecture.md#9a-environments)). The first pull on the node
took **4 min 25 s**. Until it ends, the Pod is `ContainerCreating` and
`kubectl describe pod` shows `Pulling image`: that is not a failure. Runs use
`imagePullPolicy: IfNotPresent` and the image is pinned by digest, so later
runs start from the node's copy. A new digest is pulled once.

**The alarm sensors** ([ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)) are two entries under *Automation → Sensors* in the UI, both
running: `usgs_success_heartbeat` and `run_failure_alarm` (which also covers a
failed `dashboards` run). A third, `render_dashboards_after_usgs`, is not the alarm: it starts the render. They live in the
code server, which reads the check's ping URL from `HEARTBEAT_URL` when it
starts and **does not start without it**. A sensor whose call failed three
times shows a failed tick in the UI, with the HTTP status or the error class,
never the URL. Dagster does not retry the run that tick was for.
`HEARTBEAT_URL` comes from the Secret `pipeline-heartbeat`, set in each
environment's values since its CronJob was retired (both on 2026-11-07). An image with the sensors is never pinned in an environment
whose values do not set it: its code server would not start.

**Memory** of each Pod and of a run, measured, is in [architecture.md §11](architecture.md#11-resource-budget).

## The streaming showcase in staging (M6)

What runs ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md);
[`platform/infra/streaming-staging/`](../platform/infra/streaming-staging/)):

- **The NATS hub** (`nats-hub-0`) is a message store. It keeps every event in the
  stream `EVENTS` on its own 4Gi volume, for 7 days or 3 GiB, whichever
  comes first. It is the record of the events.
- **Redpanda Connect** (`connect-…`) runs two streams in one process:
  - `producer` publishes five synthetic events a second to the hub;
  - `consumer` reads the hub through the durable consumer `serving-writer`, a
    position the hub keeps for it, and inserts each event into
    `serving.live.events`. It acknowledges a message only after the write.
- **Dagster's `streaming` job** runs hourly at :45. It creates that table if
  it is missing, builds the views and tests them. The test
  `live_window_is_full` fails, and the alarm e-mails, when the last hour
  holds fewer than half its events.

Think of the hub as a tape recorder and the table as a whiteboard copied from
the tape. Wiping the whiteboard loses nothing: the tape is played again.

**Seal the two passwords, before the change is merged.** `serving` owns the
database and is what dbt runs as. `events_writer` may only insert. They are
generated and sealed in one pipe, as in *The catalog's read-only role*:

```bash
for pair in serving:serving-credentials events_writer:events-writer-credentials; do
  user="${pair%%:*}"; name="${pair##*:}"; f="platform/infra/postgres-staging/$name-sealed.yaml"
  openssl rand -hex 24 | { read -r PW && kubectl create secret generic "$name" -n staging \
    --type=kubernetes.io/basic-auth --from-literal=username="$user" --from-literal=password="$PW" \
    --dry-run=client -o yaml | kubeseal --format yaml > "$f.tmp" && mv "$f.tmp" "$f" || rm -f "$f.tmp"; unset PW; }
done
```

**After Argo CD syncs**, in this order:

1. CloudNativePG creates the two roles and the database `serving`.
2. The hub starts. Connect's init container creates the stream, and Connect
   starts publishing.
3. Until the first `streaming` run at :45 creates `live.events`, Connect's
   log repeats `relation "live.events" does not exist`. The hub keeps every
   event meanwhile, and the writer catches up within seconds of the table
   appearing.
4. That first run's `live_window_is_full` may fail, since the table holds
   less than an hour: one alarm e-mail. The next run passes.

```bash
kubectl -n staging get pods -l 'app in (nats-hub, connect)'          # 1/1 Running each
kubectl -n staging get database serving -o jsonpath='{.status.applied}'  # true
kubectl -n staging logs deploy/connect --tail=20                     # no error after the table exists
```

**Read the state.** On the hub, from its monitoring port:

```bash
kubectl -n staging port-forward pod/nats-hub-0 8222:8222 &   # the Service lists 4222 only
curl -s "localhost:8222/jsz?streams=true&consumers=true" | jq '.account_details[].stream_detail[]
  | {name, messages: .state.messages, bytes: .state.bytes,
     consumers: [.consumer_detail[] | {name, num_pending, num_ack_pending, num_redelivered}]}'
```

And in the table, as Postgres's superuser over the Pod's local socket:

```bash
kubectl -n staging exec postgres-1 -c postgres -- psql -d serving -Atc \
  "select count(*), max(collected_at), max(received_at - collected_at) from live.events"
```

The writer has caught up when `num_pending` and `num_ack_pending` are 0.
A message that fails the subject/identity contract in `consumer.yaml` is
logged at `ERROR`, counted in Connect's Prometheus metric
`events_rejected_total` (`localhost:4195/metrics` over a port-forward to
`deploy/connect`), and dropped on its first delivery so `num_pending` and
`num_ack_pending` still return to 0. With zero rejected events, the hub's
`messages` equals the table's count while the stream is less than 7 days old.
After that the table also holds up to an hour the hub has already dropped (the
archive deletes whole hours, below), so compare the count over the hub's own
window instead: rows with `received_at` after the hub's first message
(`.state.first_ts` in the same JSON).
In the spike an event took 458 bytes in the hub; locally 366.

NATS CLI operations below require the internal authentication environment
described in *The Raspberry Pi, Credentials*.

**Rebuild the live table from the hub** (ADR-0047, decision 4). Do it after
a crash of Postgres or a restore, both of which leave the `UNLOGGED` table
empty, or to prove the rebuild. It needs the NATS CLI on your machine
(`brew install nats-io/nats-tools/nats`):

```bash
kubectl -n staging port-forward svc/nats 4222:4222 &
kubectl -n staging exec postgres-1 -c postgres -- psql -d serving -c "truncate live.events"
nats --server nats://127.0.0.1:4222 consumer rm EVENTS serving-writer -f
kubectl -n staging delete pod -l app=connect
```

Connect's new Pod finds no consumer and creates it with `deliver: all`, so it
reads the stream from its first message. Rows written between the truncate
and the restart are written again unchanged, since the insert ignores a key it
has. It restores only what the hub still holds, the last 7 days; older
events are in the lake, archived by `streaming_archive` (below), which does
not copy the replayed rows again. `received_at` is the hub's timestamp, so
the rebuilt rows equal the old ones. Wait until `num_pending` is 0, then compare the counts as above.
Deleting the consumer is the step that matters: a Connect restart alone
resumes from the consumer's position (ADR-0045's spike notes, correction 5).

**The archive and the live table's retention** (ADR-0047, decision 6).
Dagster's `streaming_archive` runs every hour at minute 20
([`platform/orchestration/`](../platform/orchestration/README.md)): it copies
the closed hours of `received_at` to `bronze.streaming_events` in the lake,
confirms each row there by `event_id`, deletes from `live.events` an hour
whose rows are all older than 7 days, and deletes from the lake the days
older than 30. Read a run in its Pod's log, which Kubernetes keeps an hour:

```bash
kubectl -n staging get pods -l dagster/job=streaming_archive \
  -o custom-columns=POD:.metadata.name,RUN:.metadata.labels.dagster/run-id,STATUS:.status.phase
kubectl -n staging logs <pod> | grep -E 'window |window\(s\)|memory peak'
```

Each window it touched is a line: rows live, in the lake before, inserted,
in the lake after, and deleted from the live table. A normal run touches two:
the hour that just closed (inserted equals live) and the hour that turned
7 days old (inserted 0, deleted equals live). A run with nothing to do logs
`no window to archive or delete`. The summary line gives the lake's rows and
its Parquet bytes per UTC day, the figure the 1 GB of R2 is checked against
(ADR-0047's notes).

**If it fails**, nothing was deleted: a mismatch (`ArchiveMismatch`) is raised
before the delete, and the delete itself rolls back unless it removes exactly
the rows confirmed. The alarm's e-mail carries the window and the counts.
Until a run succeeds, the live table keeps every row, so it grows by about
200 MB a day and, past 7 days, holds rows the hub has dropped: a crash of
Postgres in that time loses them. So read the error the same day:

- `N of them not found in the lake after the insert`: the insert did not
  land; read the run's log for a DuckLake or R2 error above it.
- `event_id(s) in the lake more than once`: two copies of an event in the
  lake, which the archive never writes, so something else wrote to
  `bronze.streaming_events`. Find what before removing a copy; every run
  fails on that window until then, and deletes nothing.
- `the delete would remove N`: rows of the window changed between the read
  and the delete, which only a hand-made change to `live.events` does; the
  next run reads the window again.

A window that comes back from a rebuild with fewer rows than the lake holds
is expected, not a failure: the lake keeps what the table lost.

**Hold the synthetic events back for an hour** (ADR-0047, decision 8, the
second criterion). It reproduces what the Pi does when its link comes back:
an hour of events that arrive an hour late, all at once. It uses Connect's
streams API, which `--bind-http` enables and the NetworkPolicy keeps from
every Pod; a port-forward reaches it.

```bash
kubectl -n staging port-forward deploy/connect 4195:4195 &
curl -s -X DELETE -w "%{http_code}\n" localhost:4195/streams/producer   # 200: no more events
curl -s localhost:4195/streams | jq keys               # ["consumer"]: no producer
# A few seconds later, once the writer has caught up: the first second not produced.
kubectl -n staging exec postgres-1 -c postgres -- psql -d serving -Atc "select to_char(
  (date_trunc('second', max(collected_at)) + interval '1 second') at time zone 'UTC',
  'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') from live.events where source = 'synthetic'"
```

Check both answers before waiting. A port-forward to `deploy/connect` is tied
to the Pod that was running when it started, and stops working when that Pod
is replaced: the `DELETE` then prints `000`, the list prints nothing, and the
producer keeps running.
Start a new port-forward after any restart of Connect.

An hour later, release that hour and restart the producer:

```bash
HOLD_FROM=<the time the query above printed>
kubectl -n staging get configmap connect -o jsonpath='{.data.backfill\.yaml}' \
  | sed "s/\${HOLD_FROM}/$HOLD_FROM/" > /tmp/backfill.yaml
kubectl -n staging get configmap connect -o jsonpath='{.data.producer\.yaml}' > /tmp/producer.yaml
curl -s -X POST --data-binary @/tmp/backfill.yaml localhost:4195/streams/backfill   # 200
curl -s -X POST --data-binary @/tmp/producer.yaml localhost:4195/streams/producer   # 200
curl -s localhost:4195/streams | jq 'map_values(.active)'   # backfill false once done; producer true
curl -s -X DELETE localhost:4195/streams/backfill
```

The backfill emits 3,600 seconds of events and stops: 18,000 rows. Then
check, for the held hour, that `metrics.pipeline_minute` holds 60 events per
whole minute and stream, and that the hour's synthetic outages (minutes 7 and
37) are in `metrics.link_outages`. The events are counted as late except those
collected less than a minute before the release: they reached the hub less
than a minute after they were collected, which the rule calls on time. A Pod restart before the
release brings the producer back from its file, which ends the hold early.
Exercised against a local hub on 2026-11-17: 18,000 rows in 2 s, then
`PASS=23`; and in staging the same day (ADR-0047, *Notes*).

**The other criteria of decision 8**, each read as above: hub and table
counts equal, and no pending or unacknowledged message. Exercised in staging
on 2026-11-17, with the rebuild above; the figures are in ADR-0047's *Notes*.

- **Connect restarted while events flow:** `kubectl -n staging delete pod -l app=connect`.
- **Postgres's Pod deleted while events flow:**
  `kubectl -n staging delete pod postgres-1`. CloudNativePG creates it again,
  and Connect retries its batch until Postgres answers. A Pod that is deleted
  shuts Postgres down cleanly, which keeps an `UNLOGGED` table; only a crash
  empties it.
- **24 hours:**
  - `kubectl top pod -n staging` for the hub, Connect and `postgres-1`
    against their requests;
  - the hub's `bytes` above;
  - in `psql -d serving`, `\timing` on each `metrics` view over the whole
    table, which must stay under two seconds. The 7-day figure needs 7
    days of events.

### The Raspberry Pi (M6 step 5)

**Prepared, not yet deployed or measured on the real Pi.** This procedure
requires the guarded PR's approval and its sealed credential before merge.
Runtime files: [`platform/edge/pi/`](../platform/edge/pi/).
Design and trade-offs: [ADR-0050](adr/0050-the-pi-streaming-runtime-and-private-hub-link.md).
Agents carry out the remote work; the operator approves, supplies physical
access if needed and seals credentials. Never paste credentials into chat.

**Preflight on the Pi, before changing it.** Confirm the model from
`/proc/device-tree/model`, Debian release, `uname -m`, clock synchronization,
free disk and whether old spike processes still run. The recorded hardware is
Pi 3 Model B rev 1.2 with Debian 12 and an arm64 userspace; recheck it rather
than downloading a binary from the model name. Read `vcgencmd get_throttled`,
the two sysfs paths in `health.sh`, and `df --output=size,used /`. If a required
reading is unavailable, stop and fix the platform support; never substitute 0.
The experiment recorded kernel 6.1.21: verify the installed kernel is receiving
updates and resolve that before unattended operation. No prior Pi hardening
is asserted by this runbook.

Base hardening: operator's individual SSH key, root/password SSH login disabled,
security updates configured, no public inbound port, no swap on the SD card.
Verify another authorized SSH session works before changing SSH rules. Confirm
`systemd-timesyncd` is the active clock service; this unit relies on its
`/run/systemd/timesync/synchronized` marker. Enable `systemd-time-wait-sync`.
Record the checks and any changes without recording addresses or account names.

**Credentials — operator only.** Create two independent random hex passwords:
`internal-password` and `pi-password`. Seal them as the Secret
`staging/nats-credentials`, following *Sealed Secrets*, and commit the resulting
`platform/infra/streaming-staging/nats-credentials-sealed.yaml` before merge:

```bash
{ f="platform/infra/streaming-staging/nats-credentials-sealed.yaml"
  INT_PW=$(openssl rand -hex 24) && PI_PW=$(openssl rand -hex 24) \
  && kubectl create secret generic nats-credentials -n staging \
    --from-literal=internal-password="$INT_PW" --from-literal=pi-password="$PI_PW" \
    --dry-run=client -o yaml | kubeseal --format yaml > "$f.tmp" \
  && mv "$f.tmp" "$f" || rm -f "$f.tmp"; unset INT_PW PI_PW; }
grep -Ec '(internal|pi)-password:' platform/infra/streaming-staging/nats-credentials-sealed.yaml
```

Only the Pi password reaches the Pi, in `/etc/lagom-pi/leaf.env` (root, mode 0600);
read it from the cluster Secret once synced without printing it:

```text
LEAF_URL=nats-leaf://pi:<pi-password>@<node-name>.<tailnet>.ts.net:7422
```

Do not put the internal password into a URL: Connect uses its separate `auth`
fields. For the NATS CLI commands elsewhere in this section, the operator
loads `NATS_USER=internal` and `NATS_PASSWORD` into the local environment
without echoing the value (`export NATS_USER=internal NATS_PASSWORD=$(kubectl -n staging get secret nats-credentials -o jsonpath='{.data.internal-password}' | base64 -d)`);
never include it in a command argument or a report.
After inspection, unset both variables. The Pi's local CLI needs no password.
A credential rotation restarts the hub and its clients; use an approved PR and
update the Pi file as one operation. Existing streams and consumer positions
survive the restarts.

**Tailnet policy.** Add these entries to the existing policy, preserving its
operator rules. The Pi's tag is owned by `group:operators`; its outgoing
application grant is NATS, with a separate ICMP measurement grant. Operator-to-Pi
SSH is a separate incoming grant, not a permission for the Pi to reach the
operator's devices.

```jsonc
// tagOwners
"tag:lagom-pi": ["group:operators"]
// grants
{"src": ["tag:lagom-pi"], "dst": ["tag:lagom-node"], "ip": ["tcp:7422", "icmp:*"]},
{"src": ["group:operators"], "dst": ["tag:lagom-pi"], "ip": ["tcp:22"]}
// tests
{"src": "tag:lagom-pi", "accept": ["tag:lagom-node:7422"],
 "deny": ["tag:lagom-node:22", "tag:lagom-node:6443", "tag:lagom-node:4222", "tag:lagom-node:5432"]},
{"src": "tag:lagom-pi", "proto": "icmp", "accept": ["tag:lagom-node:0"]},
{"src": "<operator-tailscale-login>", "accept": ["tag:lagom-pi:22"],
 "deny": ["tag:lagom-pi:4222", "tag:lagom-pi:8222"]}
```

The Pi's only application access to the node is NATS over TCP 7422. The
separate ICMP grant permits `PING_NODE` to measure reachability to the node's
tailnet address; it does not permit broker or other application access. Verify
both flows independently: a ping to the configured node address and the NATS
leaf connection, while the policy tests continue to deny the other listed
ports.

Before merging the hub manifests, you can de-risk the two transport premises
on the node against staging's existing NATS port (or a throwaway ClusterIP
target behind a NetworkPolicy): verify that `sudo tailscale serve --bg --tcp=7422 tcp://<cluster-ip>:<port>`
is accepted by the node's Tailscale version and that a connection from the
node reaches the Pod under k3s's NetworkPolicy, then turn it off with
`sudo tailscale serve --tcp=7422 off`.

After the approved manifests reconcile, obtain staging's NATS Service address
with `kubectl -n staging get service nats -o jsonpath='{.spec.clusterIP}'`.
On the node, check `tailscale version` and `tailscale serve status`, then:

```bash
sudo tailscale serve --bg --tcp=7422 tcp://<staging-nats-cluster-ip>:7422
```

Confirm the forwarding destination in `tailscale serve status`. Test that the
Pi reaches 7422, that an unrelated Pod cannot, and that public IPv4 and IPv6
cannot (with a known-open control, as in *zero inbound ports*). Keep the public
firewall unchanged. If the node cannot reach the Pod under the existing network
policy, stop: do not open all Pod ingress to make it work. The address persists
across Pod restarts, but a recreated Service may get another; reapply Serve then.

**Install the runtime.** Pin NATS server 2.15.2 and Redpanda Connect 4.116.1,
the tested versions. Download the upstream release archives matching the
verified architecture and verify their published SHA256 checksums before
extracting. Install only the binaries into `/usr/local/bin`, root-owned 0755;
install the NATS CLI for the one-time stream configuration too (0.19.7 or the
spike's 0.20.1). Require Bash, awk, coreutils, iputils-ping and the working
`vcgencmd` package; do not replace the kernel blindly to obtain it.

From a reviewed checkout copied to the Pi, create the service identities if
absent, and install the files:

```bash
getent passwd lagom-nats || sudo useradd --system --no-create-home --shell /usr/sbin/nologin lagom-nats
getent passwd lagom-connect || sudo useradd --system --no-create-home --shell /usr/sbin/nologin lagom-connect
sudo install -d -m 0755 /etc/lagom-pi
sudo install -m 0644 platform/edge/pi/{leaf.conf,edge-stream.json,connect.yaml,ping.awk,health.sh,ping.sh} /etc/lagom-pi/
sudo install -m 0644 platform/edge/pi/lagom-{nats,connect}.service /etc/systemd/system/
```

Create `/etc/lagom-pi/targets.env` root-owned 0600, with literal target IPs and
the physical network interface. Values never enter Git:

```text
PING_GATEWAY=<gateway-address>
PING_RESOLVER_A=<resolver-a-address>
PING_RESOLVER_B=<resolver-b-address>
PING_NODE=<node-tailnet-address>
HEALTH_INTERFACE=<physical-interface>
```

`lagom-connect` needs access to `/dev/vchiq` through group `video`, and its
numeric group must fall inside `net.ipv4.ping_group_range`. Check the current
range with `sysctl`; if it excludes that group, add only that group to an
appropriate range in a reviewed local sysctl file. Test the datagram ping as
that user with no capabilities; do not grant root to the collector.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now systemd-time-wait-sync.service lagom-nats.service
nats --server nats://127.0.0.1:4222 --js-domain leaf stream add --config /etc/lagom-pi/edge-stream.json
sudo systemctl enable --now lagom-connect.service
```

On subsequent deployments, use `stream edit EDGE --config ... --force` instead
of `add`, and restart the two services after installing updated files. Never
remove the stream to update it. Verify both service states, the absence of
collection errors in the journal and `source='pi'` in `serving.live.events`.
Reboot once and prove collection starts only after the clock synchronizes.
The services retry on failure; an unavailable input must remain visibly missing,
not be replaced with synthetic readings. Grafana's source selector separates
`pi` from `synthetic`.

**Cut and restore, with the collector still running.** Record a bounded Pi-only
window, its event identifiers and per-stream counts before the cut. On the node,
turn off only the forwarder:

```bash
sudo tailscale serve --tcp=7422 off
```

Confirm the leaf disconnects; wait several minutes while its `EDGE` message
count rises. Restore the exact forwarder command above. Let the consumer catch
up, then compare the Pi's identifiers in that window across `EDGE`, hub `EVENTS`
and `live.events`, not just whole-stream totals. Require empty set differences,
zero duplicate identifiers and zero pending/unacknowledged consumer messages.
Check every ping target and health separately, including a minute inside the
cut. Repeat with the leaf restarted during the cut. Retain sanitized counts,
times and commands in ADR-0045 notes. The synthetic freshness test sums sources
and cannot prove this; a green scheduled dbt run is insufficient.

**Measurements before marking step 5 complete.** Over at least 24 hours,
including normal operation and catch-up, record each service's `MemoryCurrent`
and `MemoryPeak` through `systemctl show`. Also sample process RSS and
`/proc/<pid>/io` write_bytes, recording PID changes; a counter reset is not a
negative write rate. Read the SD device's sectors-written counter in
`/proc/diskstats` at both ends (sectors are 512 bytes), exclude partitions to
avoid double counting, and report both actual bytes and elapsed time. Compare
steady-state writes with the experiment's short-run estimate. Record NATS hub,
Connect and Postgres memory on the node and hub bytes per event/day; verify
seven days fit its 3 GiB cap and free volume space remains. Re-measure lake
bytes with real events before accepting its retention. Do not extrapolate
synthetic compression as a measured physical workload.

**Rollback and step 6.** Disable only Serve's 7422 entry, stop the collector,
then the leaf, and revert the manifests by PR if necessary. Keep the state
directory and volumes. Moving to prod changes `LEAF_URL`, restarts the leaf,
validates delivery, and removes staging's source, listener, credential and Serve
entry. Never leave the Pi connected to both environments. Staging's synthetic
producer stays. Do not delete staging's data until its archive is confirmed.

### Grafana

What runs ([ADR-0047](adr/0047-streaming-without-a-stream-processor-and-staging-on-or-off.md),
decision 7; [`platform/infra/grafana/`](../platform/infra/grafana/)):

- **Grafana** (`grafana-…`, namespace `grafana`) is the operator's viewer: one
  dashboard, *Streaming showcase*, one panel per question of ADR-0045's
  decision 5. Think of it as a window onto the views: it shows what they
  compute and holds nothing of its own that matters.
- It is served at `https://ops.ofantini.com/grafana/`, behind the same
  Access login as Dagster (*Operator UIs behind Cloudflare Access*). There is
  no Grafana login: everyone past Access is an anonymous *Viewer*, who can
  look and change nothing. The dashboard and the data source are files in
  Git; a change is a pull request.
- It reads staging's database `serving` as **`grafana_reader`**, a role that
  may read the views of the schema `metrics` and nothing else, with at most
  3 connections, and 10 seconds per query by default. The timeout stops a
  slow dashboard query; it is a default a session can change with `SET`, so
  it is no limit on someone who sends their own SQL through Grafana (a
  Viewer can). That someone is the operator, behind Access. `pg_hba` refuses it every other
  database, and a NetworkPolicy lets Grafana's Pod reach staging's Postgres
  on port 5432 and nowhere else in `staging`. Since this policy, staging's
  Postgres admits only `staging`, the CloudNativePG operator and Grafana.
- The grants are dbt's: the `streaming` job gives `grafana_reader` SELECT on
  each view every time it rebuilds them, and its test
  `grafana_reader_privileges` fails, and e-mails the alarm, if the role can
  read anything else, can write, or has lost its limit or its timeout.

**Seal the password, before the change is merged.** One random password,
sealed twice: once in `staging`, where CloudNativePG sets it on the role, and
once in `grafana`, where Grafana reads it. A Sealed Secret opens only in the
namespace it was sealed for, hence two files. Run from the repository's root:

```bash
openssl rand -hex 24 | { read -r PW && for pair in staging:postgres-staging grafana:grafana; do
  ns="${pair%%:*}"; f="platform/infra/${pair##*:}/grafana-reader-credentials-sealed.yaml"
  kubectl create secret generic grafana-reader-credentials -n "$ns" \
    --type=kubernetes.io/basic-auth --from-literal=username=grafana_reader --from-literal=password="$PW" \
    --dry-run=client -o yaml | kubeseal --format yaml > "$f.tmp" && mv "$f.tmp" "$f" || rm -f "$f.tmp"
done; unset PW; }
grep -c 'password:' platform/infra/postgres-staging/grafana-reader-credentials-sealed.yaml platform/infra/grafana/grafana-reader-credentials-sealed.yaml   # 1 each
```

**After Argo CD syncs**, in this order:

1. CloudNativePG creates `grafana_reader`; the Pod `grafana-…` starts.
2. Set the role's statement timeout, once, as Postgres's superuser.
   CloudNativePG's role spec has no field for it, so it is a statement;
   Postgres keeps it in the role, and the test above fails while it is
   missing:

   ```bash
   kubectl -n staging exec postgres-1 -c postgres -- psql -c "alter role grafana_reader set statement_timeout = '10s'"
   ```

   Repeat it after anything that creates the role again: a rebuilt
   Cluster, or a restore.
3. The grants arrive with the first `lagom-orchestration` image built from
   this change, once staging's values pin its digest (#119). Until then every panel says `permission denied for schema
   metrics`: the role exists, the grants do not yet. After the next
   `streaming` run at :45 the panels fill.

```bash
kubectl -n argocd get application grafana                       # Synced / Healthy
kubectl -n grafana get pods                                     # grafana-… 1/1 Running
kubectl -n staging get cluster postgres -o jsonpath='{.status.managedRolesStatus.byStatus}'   # grafana_reader among "reconciled"
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' https://ops.ofantini.com/grafana/   # 302 to the Access login
```

Then, in a private window, open `https://ops.ofantini.com/grafana/d/streaming`:
the Access login, then the dashboard, every panel with the synthetic data.
Over a held hour (above), *How late is an event* shows the backlog, and *How
much arrived* a bar of late events.

**Show what the role is refused.** As `grafana_reader`, over a port-forward
(which enters the Pod as `127.0.0.1`, so the `pg_hba` lines apply), with its
password in the shell's environment as in *Connect as the developer*:

```bash
export PGPASSWORD=$(kubectl -n staging get secret grafana-reader-credentials -o jsonpath='{.data.password}' | base64 -d)
kubectl -n staging port-forward svc/postgres-rw 15432:5432 &
uv run --no-project --with 'psycopg[binary]' python - <<'EOF'
import psycopg

def connect(db):
    return psycopg.connect(host="127.0.0.1", port=15432, user="grafana_reader", dbname=db, sslmode="disable", autocommit=True)

with connect("serving") as c:
    rows = c.execute("select count(*) from metrics.pipeline_minute").fetchone()[0]
    timeout = c.execute("show statement_timeout").fetchone()[0]
    print(f"reads metrics.pipeline_minute: {rows} rows; statement_timeout {timeout}")
    for sql in ("create table metrics.x (a int)", "insert into live.events (event_id) values ('x')",
                "select count(*) from live.events", "select pg_sleep(11)"):
        try:
            c.execute(sql)
            print("ALLOWED:", sql)
        except psycopg.Error as e:
            print("refused:", sql, "->", e.diag.message_primary)
for db in ("lagom", "postgres"):
    try:
        connect(db).close()
        print("ALLOWED: connect to", db)
    except psycopg.OperationalError as e:
        print("refused: connect to", db, "->", str(e).strip().splitlines()[-1])
EOF
unset PGPASSWORD; kill %1
```

Every line but the first must say `refused`: `permission denied for schema
metrics` (no table may be created), `permission denied for schema live`
(twice), `canceling statement due to statement timeout`, and `pg_hba.conf
rejects connection` for `lagom` and `postgres`. Run against a local Postgres
18 with the same roles and `pg_hba` lines on 2026-11-17, it printed exactly
that.

**Check that the policy closes staging's Postgres.** `BLOCKED` from `default`
is the pass; from `staging` the control must print `REACHED` (the nettest
image of *Postgres, Staging*):

```bash
for ns in default staging; do
  kubectl -n "$ns" run nettest --rm -i --quiet --restart=Never \
    --image=nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86 -- \
    sh -c "nc -z -w 4 postgres-rw.staging.svc.cluster.local 5432 && echo $ns REACHED || echo $ns BLOCKED"
done
```

**Memory, after a day:** `kubectl -n grafana top pod`. Set the request in
`grafana.yaml` from the figure, as for every component
([architecture.md §11](architecture.md#11-resource-budget)). Locally it held
185 MiB at rest and 220 MiB after reading 26 hours of events.

**If a panel fails:**

- *permission denied for schema metrics*: the grants are missing. The
  `streaming` job has not run with an image that has them (step 3), or its
  last run failed before the views.
- *canceling statement due to statement timeout*: the query took more than
  10 s. That is already five times ADR-0047's target of two seconds; the
  remedies are in its decision 8 (an index, then a per-minute table).
- *pg_hba.conf rejects connection* or *password authentication failed*:
  the role or its password differ between the two Secrets; seal both again
  from one password, as above.
- No data, no error: the time range is outside the events the table holds,
  or the producer is stopped (*Read the state*, above).

Grafana logs an error at each start, `plugin prometheus not found`, from a
migration service for a data source that is turned off on purpose
(`disable_plugins` in `config.yaml`). It is harmless.

## Disaster recovery

Used when the node is lost, or when the platform moves to a new node. The
path exercised is a **rebuild from Git plus a re-load from USGS**, on
2026-10-28, moving from `lagom-shared-01` to `lagom-shared-02`, with the old
node still up. Since the same day, Postgres is also backed up to R2, and a
restore into a new Cluster is tested ([Backup and restore](#backup-and-restore-m3)).
A rebuild that bootstraps each Cluster from its bucket would also keep the
event revisions a re-load loses, but it has not been run. Until it has, the
re-load below is the proven path.

**What survives the node, and where.** Everything the cluster runs is in Git.
The sealing key and the SSH key are in 1Password. The Healthchecks.io checks,
the GHCR images and the Tailscale policy live with their services. What is
lost: Postgres's data (the re-load rebuilds the last 30 days of events, see
below), dlt's cursor (rebuilt by the first run), and staging's `lagom_dev`
(disposable by design). The Postgres passwords change: CloudNativePG
generates new ones in `postgres-app`, and every consumer reads that Secret, so
nothing has to be updated.

**Procedure** — the sections above, in this order, on a node named with the
next index:

1. [Provision the node](#provision-the-node-m0). An ARM type: the images are
   built for arm64 only.
2. [Join the tailnet](#join-the-tailnet-m1), tag it `tag:lagom-node` and
   disable key expiry. The [ACL](#tailscale-acl-and-zero-inbound-ports-m1)
   already grants the tag, so it is not edited.
3. [Install k3s](#install-k3s-m1), including the public-port check.
4. [Add the kubeconfig context](#operator-kubeconfig-m1).
5. [Install Argo CD](#install-argo-cd-m1).
6. [Restore the sealing key](#sealed-secrets-m1) — before step 7.
7. [Apply the root Application](#hand-over-to-argo-cd-m1) and wait for every
   Application to be `Synced/Healthy` and both Postgres `Cluster`s ready.
8. Run the pipeline, staging first, then prod: in staging, wait for the
   scheduled `usgs` run (staging has no UI); in prod, *Launch run* on the
   `usgs` job page of its [Dagster UI](#dagster-m3). The run must
   succeed with `dbt build` reporting `ERROR=0`, and the environment's check
   must turn *up* ([Pipeline heartbeat](#pipeline-heartbeat), *Verify*).
   dbt's `reconcile_*` tests assert the relations between layers. With no
   cursor, the first run is the re-load.
9. Nothing to do for the [operator UIs](#operator-uis-behind-cloudflare-access-m3):
    the tunnel's token is sealed in Git and Argo CD starts `cloudflared` in
    step 7. Check that both addresses show the login page.

**While two clusters run at once** (a move, or a drill with the old node up):

- **Both reconcile the same `main`.** A merge reaches both. Merge nothing to
  `platform/infra/` that is meant for one node only.
- **Both run Dagster and ping the same Healthchecks.io checks**, because
  the sealed URLs decrypt with the same key on both. A check that stays *up*
  proves nothing about the new node: the old one's pings would hide its
  failures. Judge the new node by its own runs and sensor ticks, and power the old one
  off as soon as the new one passes (the *When* paragraph of
  [the checklist below](#retire-the-old-node)).
- Both call the USGS API. Four requests every 30 minutes is far below any limit.

**Measured on 2026-10-28** (timestamps from the drill's log, UTC):

| Step | Start | Took | Notes |
|---|---|---|---|
| 1. Tailnet, tag, key expiry | — | not timed | done before the drill; verified with `tailscale status --json` |
| 2. k3s (config, `GOMEMLIMIT`, install, checks) | 11:53:44 | 2 min 53 s | the installer itself took 26 s; the rest is verification and the port probes |
| 3. kubeconfig context | 11:56:47 | 2 s | |
| 4. Argo CD (`helm install --wait`) | 11:59:16 | 36 s | |
| 5. Sealing key | 12:01:30 | 2 s | the exported Document applied as it is |
| 6. Root Application to all `Synced/Healthy` | 12:01:32 | 65 s | `postgres-prod` retried twice (the race above) |
| 7a. Staging run | 12:10:12 | 49 s | 9,883 rows loaded, `PASS=34`, heartbeat 200 |
| 7b. Prod run | 12:13:45 | 30 s | 9,884 rows loaded, `PASS=34`, heartbeat 200 |

**RTO: 20 min 31 s**, from the first command on the node to prod's first green
run. About 6 minutes of it is the machine working; the rest is the operator
reading results and starting the next step. It **excludes provisioning** (the
server, cloud-init and the tailnet join were done before the drill and not
timed; M0 estimated half an hour), so a rebuild from nothing is about 50
minutes. That figure is an estimate, not a measurement. It also excludes
history older than 30 days, which cannot be re-loaded (below).

**What the re-load brings back.** Compared with the old node, both loaded by
their 12:30 runs:

| | bronze | silver | gold | oldest `updated` in bronze |
|---|---|---|---|---|
| old node, prod (loading since 2026-10-18) | 16,225 | 13,836 | 13,524 | 2026-09-18 |
| new node, prod (first run 12:14) | 9,884 | 9,884 | 9,633 | 2026-09-28 |

On the new node, bronze equals silver: a first load holds one revision per
event, and later revisions accumulate from the next runs. Within the node,
`bronze >= silver >= gold` holds, and dbt's `reconcile_*` tests passed in
every run (`PASS=34`), including the scheduled 12:30 runs. The new node has
3,952 events fewer in prod: those with an origin older than 30 days, which
the USGS API does not return without `starttime` (the API's own `count`:
9,474 events updated since 2026-10-01 without it, 12,318 with
`starttime=2026-08-01`). **The operator accepted this limit on 2026-10-28**
([ADR-0019](adr/0019-postgres-backup-deferred.md) notes): recovery brings
back the last 30 days of events. A comparison of event ids between the two
nodes over a shared window was not run.

**Gaps the drill found, and what changed:**

- The kubeconfig command overwrote `~/.kube/config`. It now merges, and
  every command while two clusters are known names its context.
- `GOMEMLIMIT` was added after the install, with a restart. The drop-in is now
  written before the installer runs, so k3s starts with it.
- The runbook hard-coded `lagom-shared-01`. It now uses `<node-name>`.
- The `GOMEMLIMIT` check ran `kubectl` before the kubeconfig existed. It now
  runs on the node (`sudo k3s kubectl`).
- The expected Applications after the hand-over listed three of nine, and the
  race on an empty cluster was undocumented. Both are now in
  [Hand over to Argo CD](#hand-over-to-argo-cd-m1).
- The sealing-key restore had never run. It worked as written. The section
  now says when to run it, how to verify it, and what to do if the API refuses
  the exported fields.
- The re-load reaches 30 days back at most, and normal runs never load a
  revision published after an event is 30 days old. This is a limit of the
  USGS API's default, not of the runbook, and the operator accepted it
  ([`services/usgs/README.md`](../services/usgs/README.md)).

### Retire the old node

**When:** first power it off, as soon as the new node's staging and prod runs
pass. In the Hetzner Console choose *Power off*, not *Delete*: it is
reversible, and it stops the old node's Dagster runs and their pings. Then wait
**24 hours**, in which both Healthchecks.io checks must stay *up* (48 pings
each, all from the new node). Delete only after that.

**What to remove**, in this order:

1. **Hetzner — the server.** Turn off its delete and rebuild protection, then
   delete it, together with its primary IPv4 and IPv6 (Hetzner offers to
   delete them with the server; nothing else uses them).
2. **Hetzner — its firewall.** Open *Firewalls* and check *Resources*: delete
   the firewall only if the old server was its only resource. If the new node
   uses the same firewall, keep it.
3. **Tailscale.** *Machines →* `<old-node-name>` *→ Remove*.
4. **The operator's kubeconfig** — the old cluster's context, cluster and
   credential, then make the new one current:

   ```bash
   kubectl config delete-context <old-node-name>
   kubectl config delete-cluster <old-node-name>
   kubectl config delete-user <old-node-name>
   kubectl config use-context <node-name>
   ```

5. **The operator's `known_hosts`:** `ssh-keygen -R <old-node-name>`.

Nothing in Git needs to change. Only records of the old node's history still
name it.

## Sections to fill as the platform is built

- **Disaster recovery from a backup** — rebuilding the node, then
  bootstrapping each Cluster from its bucket instead of `initdb`. The restore
  itself is exercised in *Backup and restore* above. A full rebuild that
  uses it is not exercised yet.
- **Secret rotation** — how to rotate a source credential and a database
  password. (M3, when a credential is first rotated: the drill rotated
  nothing)
- **On failure** — where to look: Argo CD sync state, Dagster run history, pod
  logs. (M3)

Each section is written when the corresponding milestone is implemented, with
commands that were actually run — never from memory.
