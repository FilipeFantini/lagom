# Runbook

Procedures for operating and recovering [Lagom's platform](platform.md). Run
commands from the repository root unless stated otherwise. The operator runs
cluster writes, shell access to the node and credential operations. Check
the [agent rules](../AGENTS.md) and set the correct Kubernetes context before
working against a live environment.

**Placeholders.** Keep identifying values out of the public repository.
Replace `<admin-user>`, `<operator-ssh-public-key>`, `<operator-tailscale-login>`,
`<tailnet>`, `<node-tailnet-ip>`, `<operator-email>` and `<team-name>` locally.
Tailscale lists the tailnet addresses. The Hetzner Console lists the public
addresses (`<ip>`, `<public-ip>`, `<public-ipv4>`, `<public-ipv6>`).
`<node-name>` is the target node and `<old-node-name>` the one it replaces.
Credentials come from the operator's password manager and stay out of chat,
shell history and unencrypted files.

## Provision the node

In Hetzner Console → Add Server, select a Debian 13 ARM CAX21 in `fsn1`,
IPv4 and IPv6 enabled, with local disk and no private network or server
backup. Attach a firewall allowing only Secure Shell (SSH, TCP port 22)
from your public address until [Tailscale access](#tailscale-acl-and-zero-inbound-ports)
works. Enable delete and rebuild protection.

Select your SSH key to avoid generating a root password. Paste the
configuration below as **Cloud config**, including its first line. cloud-init
reads it at first boot to create the operator's passwordless account and
disable root and password login. Verify that the selected key grants
administrative access before closing your bootstrap connection.

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

## Join the tailnet

Tailscale is the private network used to reach the node. On the node, install
it from Tailscale's official Debian repository:

```bash
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install -y tailscale
sudo tailscale up --hostname=<node-name>
```

Open the login URL, sign in and approve the machine without an auth key.
In *Machines →* `<node-name>`, disable key expiry so access survives the
normal re-authentication interval. On the operator's machine, install the
client with `brew install --cask tailscale-app` and join the same tailnet.

**Verify:**

```bash
tailscale status                   # both machines listed
tailscale ping <node-name>     # "via <public-ip>:41641" is direct; "via DERP(...)" is relayed
ssh <admin-user>@<node-name> hostname
tailscale status --json | jq '.Peer[] | select(.HostName=="<node-name>") | {Tags, KeyExpiry}'
```

The last command must print `"tag:lagom-node"` (after the ACL step below) and
`"KeyExpiry": null`, which is what *Disable key expiry* sets.

## Install k3s

k3s runs Kubernetes on the node. Give its server certificate the Tailscale
name and IP address, enable encryption at rest for Secret objects, and let
Argo CD install the storage class and metrics server. Write the configuration
and Go runtime memory cap before running the pinned installer. On the node:

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

**Check the limit still has headroom** (after configuring the kubeconfig) whenever the cluster gains a component.
If collections run several times per second and the node's CPU is near 100%
while pods use little, the runtime has hit the limit — raise it and record
the measurement ([ADR-0011](adr/0011-memory-viability-on-4gb.md) notes):

```bash
kubectl get --raw /metrics | grep -E '^go_(memstats_sys_bytes|gc_gomemlimit_bytes|goroutines|gc_duration_seconds_count) '
kubectl get --raw /api/v1/nodes/<node-name>/proxy/stats/summary   # CPU of "kubelet" (k3s) vs "pods"
```

**Verify:**

```bash
sudo k3s kubectl get nodes        # <node-name>   Ready
sudo k3s secrets-encrypt status   # Encryption Status: Enabled
sudo k3s kubectl get --raw /metrics | grep '^go_gc_gomemlimit_bytes'   # 9.437184e+08
# from a machine outside the tailnet path, against the public IP:
for p in 22 80 443 6443 10250; do nc -z -G 5 -w 5 <public-ip> $p && echo "$p OPEN" || echo "$p closed"; done
```

Expected: only `22 OPEN` while the firewall still admits SSH.

## Tailscale ACL and zero inbound ports

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

After the private connection works, remove the inbound rule for port 22.
Keep the firewall attached with zero inbound rules on both address families.
It shields the Kubernetes API (6443) and node agent (10250).

If Tailscale fails, add a temporary rule for port 22 from your current public
address, fix access over SSH, then remove the rule and repeat the checks.
Use the Hetzner rescue system if the node cannot boot.

**Verify** — from outside the tailnet, then through it:

```bash
for p in 22 80 443 6443 10250; do nc -z -G 5 -w 5 <public-ipv4> $p && echo "$p OPEN" || echo "$p closed"; done
for p in 22 80 443 6443 10250; do nc -6 -z -G 5 -w 5 <public-ipv6> $p && echo "$p OPEN" || echo "$p closed"; done
nc -z -G 5 -w 5 1.1.1.1 443                  # control: proves the probe can see an open port
for p in 22 6443 10250; do nc -z -G 5 -w 5 <node-name> $p && echo "$p OPEN" || echo "$p closed"; done
```

Expected: every public port closed while the control succeeds; over the tailnet
22 and 6443 open, 10250 closed.

## Operator kubeconfig

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

## Install Argo CD

Argo CD is installed once by hand, from the operator's machine over the
tailnet; after the handover below it manages itself
([ADR-0009](adr/0009-argocd-bootstrap-and-self-management.md)).

```bash
helm repo add argo https://argoproj.github.io/argo-helm && helm repo update argo
helm template argocd argo/argo-cd --version 10.10.3 -n argocd \
  -f infra/argocd/values.yaml > /tmp/argocd.yaml   # inspect before installing
helm install argocd argo/argo-cd --kube-context <node-name> --version 10.10.3 -n argocd --create-namespace \
  -f infra/argocd/values.yaml --wait
kubectl -n argocd get pods
```

Expected: `argocd-application-controller-0`, `argocd-repo-server` and
`argocd-redis` Running; `argocd-redis-secret-init` Completed. No Dex,
notifications, ApplicationSet or server Pods: the server's Deployment exists
at zero replicas, since the operator uses no UI and the CLI works with
`--core` ([ADR-0046](adr/0046-memory-for-streaming-staging-on-demand-and-one-run-slot.md)).

**Break-glass.** If a change to the values file breaks Argo CD, it cannot apply
its own fix. Revert the commit, then re-apply the chart by hand with the
`helm upgrade` form of the install command above (`helm upgrade argocd ...`
with the same flags); Argo CD resumes from `main`. Verify the same Pod and Application status.

## Hand over to Argo CD

Once `infra/` is on `main`, apply the root Application — the last
command run by hand against the cluster:

```bash
kubectl --context <node-name> apply -f infra/apps/root.yaml
kubectl -n argocd get applications
```

Expected within a few minutes: every Application in `infra/apps/`
`Synced` and `Healthy`. `root` syncing is also the proof that Argo CD can read
the repository.

On a fresh cluster, the database and sealed Secret Applications may try to
apply objects before their controllers install the new object types. Wait
for those controllers. If a sync remains failed after retries, the operator
can run `argocd app sync <app> --core --kube-context <node-name>` then check
for `Synced` and `Healthy`.

On first sync, Argo CD adopts the Helm bootstrap objects. If its own
Application stays out of sync after the controller restarts, run
`argocd app diff argocd --core --kube-context <node-name>` with the config's
namespace set to `argocd`. An empty diff means there is no change to apply.

## Deploy

Merge the reviewed pull request. The [reconciliation loop](platform.md#gitops--argo-cd)
applies the changes from `main`. Verify the revision and affected workload:

```bash
kubectl -n argocd get applications
kubectl -n argocd get application root -o jsonpath='{.status.sync.revision}'
```

The revision must match the merge or a later commit. Check the affected
Application is `Synced` and `Healthy` and run its procedure below.

**Promote an image.** Read the published orchestration image digest in the
`images` workflow summary for the intended `main` commit. In a pull request
from `main`, set `dagster-user-deployments.deployments[].image.tag` in
`infra/dagster-staging/values.yaml` to `<commit-sha>@sha256:<digest>`. After merge, confirm the code server and the next
`usgs` and `dashboards` runs use it and succeed ([Dagster](#dagster)).
Then promote the exact same digest to `infra/dagster-prod/values.yaml` in a
separate pull request from updated `main`. Confirm the same checks in prod.
A rollback is a new reviewed pull request for a previously tested digest.

**Remove an Application or a non-pruned object.** Before merging, identify
objects that [Argo CD leaves behind](platform.md#gitops--argo-cd) and plan
their retirement. Prefer a declarative cleanup where one is available.
After sync, confirm the object disappeared or that Argo CD no longer manages
it before the operator deletes any remaining approved object. Verify
`NotFound` for the intended object and health for its former dependencies.
Never delete a namespace as a shortcut unless its entire contents and data
are approved for removal.

## Sealed Secrets

Installed by Argo CD from `infra/apps/sealed-secrets.yaml`
([ADR-0010](adr/0010-sealed-secrets.md)). The operator needs `kubeseal`
(`brew install kubeseal`).

**Seal a secret.** The value is read without echo, so it stays out of the
terminal and the shell history; the file name ends in `-sealed.yaml`, the only
secret-like name `.gitignore` lets through:

```bash
read -rs VALUE
kubectl --context <node-name> create secret generic <name> -n <namespace> --from-literal=<key>="$VALUE" \
  --dry-run=client -o yaml | kubeseal --context <node-name> --format yaml > infra/<component>/<name>-sealed.yaml
unset VALUE
```

Verify `kind: SealedSecret`, the name and the namespace in the sealed file.
After merge, check `kubectl -n <namespace> get sealedsecrets` for
`SYNCED True` on that object.

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

If the API rejects the exported cluster's metadata, remove the object's
`resourceVersion`, `uid` and `creationTimestamp` before retrying. If the
controller is already running after key import, the operator restarts it
by deleting its Pod and waits for the replacement to become ready.

After Argo CD syncs, verify all SealedSecrets report `SYNCED True`. If
the old cluster is available, compare certificate fingerprints:

```bash
kubectl --context <node-name> get sealedsecrets -A
for c in <old-node-name> <node-name>; do kubeseal --context $c --fetch-cert | openssl x509 -noout -fingerprint -sha256; done
```

## Postgres

Prod runs as the CloudNativePG `Cluster` `postgres` in namespace `prod`
([ADR-0012](adr/0012-cloudnative-pg.md)), created by Argo CD from
`infra/postgres-prod/`. Services: `postgres-rw` (the primary, for
writes), `postgres-ro` and `postgres-r` (reads), all ClusterIP.

**Check it is healthy:**

```bash
kubectl -n argocd get application postgres-prod      # Synced / Healthy
kubectl -n prod get cluster postgres                 # "Cluster in healthy state"
```

**Run SQL as administrator.** There is no superuser password
(`enableSuperuserAccess: false`); the local socket inside the Pod is the way in:

```bash
kubectl --context <node-name> -n prod exec -it <postgres-pod> -c postgres -- psql
```

Run `SELECT current_user;` and confirm `postgres` before issuing SQL.

List credentials by name only with `kubectl -n prod get secrets -o name`.
Expect the generated `secret/postgres-app`. The operator handles values.

### Staging

Staging runs the same way in namespace `staging`, from
`infra/postgres-staging/`: a `Cluster` `postgres` with the same
settings as prod's, the managed role `lagom_dev` and the `Database` `lagom-dev`
([ADR-0014](adr/0014-develop-against-staging.md)). Prod's NetworkPolicy
(`infra/postgres-prod/networkpolicy.yaml`) drops traffic from staging.

**Check it is healthy:**

```bash
kubectl -n argocd get application postgres-staging  # Synced / Healthy
kubectl -n staging get cluster postgres             # "Cluster in healthy state"
kubectl -n staging get database lagom-dev           # APPLIED true
kubectl -n staging describe resourcequota staging   # Used against Hard
```

The operator checks staging isolation with this probe. Expect `BLOCKED`;
the same command in prod must print `REACHED` as a control.

```bash
kubectl --context <node-name> -n staging run nettest --rm -i --quiet --restart=Never \
  --image=nginxinc/nginx-unprivileged:1.30.5-alpine@sha256:56b8272e43a0525f67aed68d1e0794a712d10e3e45a563e118e9a77c817e6f86 -- \
  sh -c 'nc -z -w 4 postgres-rw.prod.svc.cluster.local 5432 && echo REACHED || echo BLOCKED'
```

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

Expected: `('lagom_dev', 'lagom_dev')`. Repeat with `dbname='lagom'` to
verify that the developer is refused access. Use `sslmode=disable` on the
loopback side of port-forward because Postgres TLS can tear down the forward
when a session closes. The tailnet and Kubernetes API still encrypt the path.
Close the forward and unset `PGPASSWORD` when finished.

### Pull credentials

Every Pod that pulls a private image needs the Secret `ghcr-pull` (a sealed
`dockerconfigjson`, [ADR-0015](adr/0015-images-on-ghcr.md)) in its namespace;
without it the Pod stays in `ImagePullBackOff`.

**Seal a pull credential** — a classic token with only `read:packages`, one per
namespace. Paste the token at the prompt. Check the history search prints zero matches.

```bash
read -rs TOKEN && kubectl --context <node-name> create secret docker-registry ghcr-pull -n <namespace> \
  --docker-server=ghcr.io --docker-username=FilipeFantini --docker-password="$TOKEN" --dry-run=client -o yaml \
  | kubeseal --context <node-name> --format yaml > infra/postgres-<namespace>/ghcr-pull-sealed.yaml; unset TOKEN
grep -a -c 'ghp_' ~/.zsh_history ~/.zsh_sessions/* 2>/dev/null   # all 0: the token is in no history
```

After merge, verify the sealed Secret syncs and a Pod using the image
becomes ready without `ImagePullBackOff`. Revoke a token immediately if
it enters history or is exposed elsewhere, then seal a replacement.

### Pipeline heartbeat

Each environment's Dagster reports to its own check at Healthchecks.io
([ADR-0018](adr/0018-pipeline-heartbeat.md),
[ADR-0033](adr/0033-dagster-alerts-through-healthchecks.md)). The sensor
`usgs_success_heartbeat` pings it after each successful `usgs` run, so a
missing ping is the alarm for "nothing runs"; `run_failure_alarm` calls its
`/fail` with the job, the run ID and the error as soon as any run fails, so
the check goes down at once. Configure these checks in its console:

- One check per environment, `lagom-usgs-staging` and `lagom-usgs-prod`.
- Schedule: *Cron*, `*/30 * * * *`, time zone UTC, the `usgs` schedule.
- Grace: 20 minutes. Review duration in Dagster if normal runs approach it.
- Integration: e-mail to the operator.

**Seal the ping URL**, one per namespace. The URL is a secret — whoever has it
can report a success — so it is pasted when the command waits, never typed on
the command line. It reaches the code server as `HEARTBEAT_URL`
(`infra/dagster-<env>/values.yaml`):

```bash
read -rs HC_URL && kubectl --context <node-name> create secret generic pipeline-heartbeat -n <namespace> \
  --from-literal=url="$HC_URL" --dry-run=client -o yaml \
  | kubeseal --context <node-name> --format yaml > infra/postgres-<namespace>/pipeline-heartbeat-sealed.yaml; unset HC_URL
```

**Verify** after the change reaches the namespace:

1. The code server Pod is ready: it does not start without `HEARTBEAT_URL`.
   In prod, *Automation → Sensors* shows both sensors running.
2. After the next `usgs` run, the daemon logs
   `Sensor "usgs_success_heartbeat" acted on run status SUCCESS of run <id>`
   (`kubectl -n <namespace> logs deploy/dagster-daemon`), and the check turns
   *up*, with the run's ID in the ping's body.
3. To prove `/fail`, use an approved failure drill in an environment with
   a UI. Launch one failing run: on the `usgs` job page,
   *Materialize all* ▾ → *Open launchpad* → *Edit tags*, and give the tag
   `dagster-k8s/config` this value, which keeps the run's resources and points
   the catalog at a host that does not exist, so dlt fails before writing
   anything:

   ```json
   {"container_config": {"resources": {"requests": {"cpu": "50m", "memory": "768Mi"}, "limits": {"memory": "1Gi"}}, "env": [{"name": "DESTINATION__DUCKLAKE__CREDENTIALS__CATALOG", "value": "postgresql://alarm-test.invalid/none"}]}}
   ```

   The daemon logs `Sensor "run_failure_alarm" acted on run status FAILURE`,
   the check goes *down* and e-mails, and the next scheduled run clears it.

**When the e-mail arrives:** a `/fail` carries the job, the run ID and the
error in its body (*Events* of the check in the console). A missing ping
means no `usgs` run succeeded: check the run list in the UI, then
*Automation → Sensors* for a failed tick, then the daemon and the code server
Pods. A `/fail` that could not be sent after three attempts leaves only a
failed tick in the UI (ADR-0033, decision 6).

### The catalog's read-only role

The operator seals the catalog reader's password in each namespace
([reader permissions](data.md#reader-permissions)). The plot renderer reads
it from the Secret. Run with shell pipeline failure propagation enabled
(`set -o pipefail`):

```bash
for ns in staging prod; do
  f="infra/postgres-$ns/lake-reader-credentials-sealed.yaml"
  openssl rand -hex 24 | { read -r PW && kubectl --context <node-name> create secret generic lake-reader-credentials -n "$ns" \
    --type=kubernetes.io/basic-auth --from-literal=username=lake_reader --from-literal=password="$PW" \
    --dry-run=client -o yaml | kubeseal --context <node-name> --format yaml > "$f.tmp" && mv "$f.tmp" "$f" || rm -f "$f.tmp"; unset PW; }
done
```

Verify both outputs are nonempty SealedSecrets before committing them.
After merge, read the role's reconciliation status without reading
its password. Let the [maintenance job](data.md#orchestration--dagster-jobs)
refresh the grants. The operator can then verify them on the local socket:

```bash
kubectl -n <namespace> get cluster postgres -o jsonpath='{.status.managedRolesStatus.byStatus}'
kubectl -n <namespace> exec <postgres-pod> -c postgres -- psql -d lagom -At -c   "select has_schema_privilege('lake_reader','ducklake','USAGE'),
          has_database_privilege('lake_reader','lagom','CONNECT'),
          (select count(*) from information_schema.tables t
           where t.table_schema='ducklake'
             and not has_table_privilege('lake_reader', format('%I.%I', t.table_schema, t.table_name), 'SELECT'))"
```

Expect `lake_reader` reconciled, `grant_lake_reader` successful and
`t|t|0` from SQL. If grant renewal fails, check the role status and the
SealedSecret's sync. To rotate the password, seal for that namespace only,
merge, and verify the next render. Restart readers that hold the old value.

### The dashboards' publishing path

Configure the dashboards bucket as public through its custom domain,
with `r2.dev` disabled and a minimum TLS version of 1.2. The public
hostnames are `lagom-staging.ofantini.com` and `lagom.ofantini.com`. In
each environment, create a token with read access only to
`lagom-<env>-lake`, and a token with read/write access only to
`lagom-<env>-dashboards`. Confirm each token's scope in Cloudflare.

Seal the tokens from the repository root with the correct cluster context
and `set -o pipefail`. Use the prompts for the access key ID, secret and
account endpoint. The function validates their shape before saving:

```bash
seal_r2() {  # seal_r2 <namespace> <secret name> <file name>
  local ns="$1" name="$2" f="infra/postgres-$1/$3" key secret endpoint
  # printf, not `read -p`: in zsh, macOS's shell, -p reads from a coprocess.
  printf '%s access key ID (%s): ' "$name" "$ns"; read -rs key; echo
  printf '%s secret access key (%s): ' "$name" "$ns"; read -rs secret; echo
  printf '%s endpoint (%s): ' "$name" "$ns"; read -rs endpoint; echo
  if [[ $key =~ ^[0-9a-f]{32}$ && $secret =~ ^[0-9a-f]{64}$ &&
        $endpoint =~ ^https://[0-9a-f]{32}\.r2\.cloudflarestorage\.com$ ]]; then
    kubectl --context <node-name> create secret generic "$name" -n "$ns" --from-literal=ACCESS_KEY_ID="$key" \
      --from-literal=ACCESS_SECRET_KEY="$secret" --from-literal=ENDPOINT="$endpoint" \
      --dry-run=client -o yaml | kubeseal --context <node-name> --format yaml > "$f.tmp" && mv "$f.tmp" "$f" && echo "ok: $f" \
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

Check all four outputs are nonempty SealedSecrets before committing them.
After merge, verify each SealedSecret is `SYNCED True`. Promote the image
through [staging then prod](#deploy). Check:

1. The `dashboards` run succeeds and logs both `earthquakes.html` and
   `earthquakes_short.html` as published.
   In staging, wait for the next `usgs` run. In prod, also check the job's
   configuration in the Launchpad. It names the reader and dashboards
   credentials and has no `PG_URI`.
2. `curl -fsSI https://lagom-staging.ofantini.com/earthquakes.html` returns
   200 with HTML and `cache-control: public, max-age=300`. Repeat for prod.
   Open the page and confirm its UTC build timestamp is near the run time.
3. Read the run's logged memory peak, which includes subprocesses and short
   spikes that `kubectl top` can miss. Compare it with the
   [job limit](platform.md#resource-budget). Investigate within 100 MiB
   of the limit before changing resource requests.
4. In an approved failure drill with a UI, launch `dashboards` with a copy of
   its job tag and set `PGPASSWORD` to `wrong-password-for-test`. Confirm
   validation fails, logs and the alert omit the test and real passwords,
   and the previously published page is still served.

The `dashboards` run also publishes dbt's documentation
([ADR-0056](adr/0056-the-data-console.md)), after the reports. In
the same run, check:

5. The log says `published docs/index.html` and holds two `memory peak` lines,
   one after the reports and one after the documentation. The second is the run's
   peak so far; the documentation's own cost is read from the two, and compared
   with the job limit as in step 3.
6. `curl -fsSI https://lagom-staging.ofantini.com/docs/index.html` returns 200
   with HTML and `cache-control: public, max-age=300`. Repeat for prod.
7. `curl -fsS https://lagom-staging.ofantini.com/docs/index.html | grep -c
   '"send_anonymous_usage_stats": false'` prints `1`: the page does not load a
   tracker. The reports still answer 200 at the root (step 2).

A run that fails after the reports leaves them current and the documentation
as it was; the failure names the check that refused the page.

If a command times out, check its duration and which render stage failed
before changing the image. If telemetry stalls, ADR-0044 decision 9 describes
the setting to disable it. To rotate a token, seal its replacement and
verify a fresh run before revoking the old token.

## Operator UIs behind Cloudflare Access

Use this order so [Access](platform.md#access-channels) protects the hostname
before it serves a tool. Bookmark `https://ops.ofantini.com/dagster/` with
the trailing slash to avoid a plain HTTP redirect.

1. Enable two-factor authentication on the Cloudflare account and the
   mailbox that receives codes. Store recovery codes in the password manager.
2. In **Zero Trust**, use a neutral team name and the Free plan. Add
   **One-time PIN** under *Integrations → Identity providers*.
3. In *Access controls → Applications*, add a self-hosted application
   **Lagom operator tools** for `ops.ofantini.com`, with the path empty to
   cover the whole hostname. Allow only `<operator-email>`, with no
   *Everyone* or *Bypass* policy. Select One-time PIN alone, instant
   authentication, a 24-hour session and Binding Cookie.
4. Read the **Application Audience (AUD) Tag** from the application's
   additional settings and the team name from Zero Trust settings.
   Use them in `infra/cloudflared/config.yaml`. For the zone, enable
   **Full (strict)**, **Always Use HTTPS**, minimum TLS 1.2, DNSSEC
   (signed Domain Name System records), and registrar lock. Keep the
   hostname's record proxied to the tunnel, with no node address in DNS.

**5. The tunnel and its token**, on the operator's Mac, from the repository's
root. The token goes straight from `cloudflared` into `kubeseal` and is never
shown:

```bash
brew install cloudflared
cloudflared tunnel login                  # browser: authorise the zone ofantini.com; writes ~/.cloudflared/cert.pem
cloudflared tunnel create lagom           # writes ~/.cloudflared/<tunnel-id>.json
cloudflared tunnel route dns lagom ops.ofantini.com
cloudflared tunnel token lagom \
  | kubectl --context <node-name> create secret generic cloudflared-token -n cloudflared --from-file=token=/dev/stdin --dry-run=client -o yaml \
  | kubeseal --context <node-name> --format yaml > infra/cloudflared/token-sealed.yaml
grep -c 'token:' infra/cloudflared/token-sealed.yaml   # 1: one sealed key
rm ~/.cloudflared/cert.pem ~/.cloudflared/<tunnel-id>.json
```

Once `config.yaml` holds the team name and AUD tag, check that `cloudflared`
reads its rules as intended. The rules sit inside a ConfigMap, so the first
command extracts them:

```bash
sed -e '1,/config.yaml: |/d' -e 's/^    //' infra/cloudflared/config.yaml > /tmp/cloudflared.yaml
cloudflared tunnel --config /tmp/cloudflared.yaml ingress validate                                   # OK
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/dagster/runs   # prod's rule
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/dagster-staging  # the default 404 rule
cloudflared tunnel --config /tmp/cloudflared.yaml ingress rule https://ops.ofantini.com/               # the 404 rule
rm /tmp/cloudflared.yaml
```

**6. Merge** the pull request holding `token-sealed.yaml`, the filled-in
`config.yaml` and the Dagster path prefixes. Argo CD creates the namespace,
the Secret and the Pod, and updates the prod webserver.

**Verify:**

```bash
kubectl --context <node-name> -n cloudflared get pods                       # cloudflared 1/1 Running
kubectl --context <node-name> -n cloudflared logs deploy/cloudflared | grep -c 'Registered tunnel connection'   # 4
kubectl --context <node-name> -n prod get pods -l component=dagster-webserver      # 1/1 Running: the probe follows the prefix
kubectl --context <node-name> -n staging get pods -l component=dagster-webserver   # no staging webserver
curl -sS -o /dev/null -w '%{http_code}\n' https://ops.ofantini.com/dagster/server_info
# 302: unauthenticated login redirect
```

In a private window, verify Access asks for a code and an approved address
can open Dagster. An address outside the policy receives no code. Navigate to
Runs, Jobs and Assets under the same prefix. Confirm an active run updates
without reloading. The ingress tests check configuration, while the browser
check verifies the access path. A direct bypass of Access is untested.

**Add a tool**:

1. Configure the tool and its health probe to serve under the chosen prefix.
2. Add a rule in `config.yaml`, before the final 404:
   - `hostname: ops.ofantini.com`;
   - `path: ^/<tool>(/.*)?$`;
   - the tool's Service.

   Then bump `lagom.dev/config-revision` in `deployment.yaml`.
3. If the tool runs in `prod`, add a NetworkPolicy like
   `allow-cloudflared-to-dagster-webserver`.

Repeat the ingress and authenticated browser checks for the new path.

Troubleshoot by symptom: 1033 means the tunnel is disconnected; 404 means
the route did not match; 502 means the Service did not answer; a 403 after
login calls for checking `audTag`. If styles or links break, confirm the
tool's path prefix matches the tunnel rule. To reach prod over the tailnet
during a tunnel outage:

```bash
kubectl --context <node-name> -n prod port-forward svc/dagster-dagster-webserver 3000:80
```

Open `http://localhost:3000/dagster/` and close the forward when done.

## Backup and restore

Only prod has a [database backup](platform.md#backups). For a new backup
destination, create `lagom-prod-backups` in Western Europe and grant a new
R2 token read and write on that bucket alone. Seal it with the
[R2 helper above](#the-dashboards-publishing-path):

```bash
seal_r2 prod r2-backups r2-backups-sealed.yaml
```

Check the output is a nonempty SealedSecret. Reuse prod's `ObjectStore`,
`ScheduledBackup` and `spec.plugins` declarations and merge. Verify the
SealedSecret syncs and the Postgres Pod runs with its backup sidecar.

**Check it works:**

```bash
kubectl --context <node-name> -n prod get cluster postgres -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
kubectl --context <node-name> -n prod get backups
kubectl --context <node-name> -n prod logs <postgres-pod> -c plugin-barman-cloud --since=10m | grep -E 'Archived WAL file|Backup completed'
```

`ContinuousArchiving=True` and `LastBackupSucceeded=True` are the pass. If
archiving fails, Postgres keeps the WAL it could not ship on its volume, so
the volume grows: read the sidecar's log.

If the immediate backup runs before its sidecar is ready, the operator
takes a one-shot backup after the Pod is ready:

```bash
N=manual-$(date -u +%Y%m%d%H%M)
kubectl --context <node-name> -n prod apply -f - <<YAML
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata: {name: $N}
spec:
  cluster: {name: postgres}
  method: plugin
  pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}
YAML
kubectl --context <node-name> -n prod wait backup/"$N" --for=jsonpath='{.status.phase}'=completed --timeout=600s
```

**Test a restore** into a temporary Cluster in prod with the operator's
go-ahead. It reads the bucket and does not archive back to it. Before
starting, confirm the node has enough memory and disk for a second database.
Use the prod image digest in the manifest. Then verify the restored catalog
and database against a known recovery point before removing the copy.

```bash
kubectl --context <node-name> -n prod apply -f - <<'YAML' && kubectl --context <node-name> -n prod wait cluster/restore-test --for=condition=Ready --timeout=900s
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
kubectl --context <node-name> -n prod get cluster restore-test
kubectl --context <node-name> -n prod exec <restore-test-pod> -c postgres -- psql -d lagom -At -c "select count(*) from ducklake.ducklake_snapshot"
```

Expect a healthy Cluster and the catalog snapshots up to the last archived
write-ahead log (WAL) segment. Compare snapshot IDs and the stored data
against the selected recovery point. Recent changes on the live Cluster
may be newer than that point. The operator deletes the test Cluster with
`kubectl --context <node-name> -n prod delete cluster restore-test` after verification, then
checks that the Cluster and its volume claim are gone.

To check retention, compare expired backups with the bucket's
`postgres/base/` listing in Cloudflare. Verify the ObjectStore's
`status.serverRecoveryWindow.firstRecoverabilityPoint` advances and the
sidecar log shows deletion without `BadDigest` or `DeleteObjects` errors.

## Dagster

[Deployment](platform.md#dagsters-deployment) describes the resident Pods and
[data](data.md#orchestration--dagster-jobs) lists the jobs. Check the Application
and filter the Pods in the intended namespace:

```bash
kubectl -n argocd get application dagster-<env>
kubectl -n <env> get pods | grep dagster
```

Expect `Synced` and `Healthy`, a ready daemon and code server, plus a
webserver in prod. To find a run before its Pod is collected:

```bash
kubectl -n <env> get jobs | grep dagster-run
kubectl -n <env> logs job/dagster-run-<run-id> | grep -E ' - (RUN_SUCCESS|RUN_FAILURE|STEP_SUCCESS|STEP_FAILURE) - |loaded .* rows|PASS=|published '
kubectl -n <env> logs deploy/dagster-daemon --since=1h | grep -E 'schedule|Launched'
```

Compare the code server and run Pod's `spec.containers[].image` digest
with the pull request. `usgs` must complete ingestion and dbt. The
dashboard run must publish its board. Use these logs and the
[pipeline heartbeat](#pipeline-heartbeat) for staging.

In prod, open [Dagster](https://ops.ofantini.com/dagster/), then *Runs*.
For a manual run, use the named job's page and its Launchpad. The
asset catalog can start an implicit job without the named job's resource
or credential tags. After a code image change, reload the code location
under *Deployment* before launching a run. Verify the job's tags and
that the run appears in *Runs*. If a run is already active the second
run queues.

For a Pod in `ContainerCreating`, inspect `kubectl describe pod`: a
first image pull must progress to ready. If the daemon or webserver
restarts repeatedly, check `kubectl logs --previous` and the readiness
of the `dagster` database and code server. If the pipeline runs but the
alarm is stale, inspect the daemon's sensor ticks as described in
[Pipeline heartbeat](#pipeline-heartbeat).

## Disaster recovery

Use this procedure after losing or replacing a node. The Git bootstrap and
USGS reload were exercised with the original Postgres pipeline
([ADR-0020](adr/0020-move-to-the-8-gb-node.md)). A full rebuild with DuckLake
still needs a drill. Confirm the sealing and Secure Shell (SSH) keys are
available before provisioning.

1. [Provision the node](#provision-the-node), [join the tailnet](#join-the-tailnet)
   and apply the existing `tag:lagom-node` [policy](#tailscale-acl-and-zero-inbound-ports).
2. [Install k3s](#install-k3s), verify the firewall and add the new
   [kubeconfig context](#operator-kubeconfig).
3. [Install Argo CD](#install-argo-cd) and [restore the sealing key](#sealed-secrets)
   before applying the root Application.
4. Before [handing over](#hand-over-to-argo-cd), choose how each lake
   comes back. To recover the old lake, restore its Postgres catalog and
   retain the files at the paths the catalog records. To reload from USGS
   into a fresh catalog, prepare an empty lake location per environment
   and review both `LAGOM_LAKE_DATA_PATH` and
   `DESTINATION__DUCKLAKE__CREDENTIALS__STORAGE__BUCKET_URL` in
   `infra/dagster-<env>/values.yaml` together. Ensure the sealed
   `r2-lake` writer and `r2-lake-reader` reader credentials cover that
   location. Preserve the old prod backup archive as a restore source.
   Give the replacement its own [archive destination](../infra/postgres-prod/objectstore.yaml)
   and matching `r2-backups` credential. Review those targets before applying
   the root Application, which enables scheduled writers. A merge to
   `main` reaches both clusters while both reconcile it. During a move,
   stop the old cluster before changing shared settings, or isolate both
   the writers and their heartbeat checks. Use explicit contexts throughout.
5. Verify all Applications are healthy, both Postgres Clusters are
   ready, and staging then prod succeed. Verify dbt reports zero errors,
   reconciliation passes, the [dashboard is published](#the-dashboards-publishing-path)
   and the [heartbeat](#pipeline-heartbeat) comes from the new runs.
6. Verify that [prod Dagster](#operator-uis-behind-cloudflare-access)
   prompts for login. Its tunnel uses the sealed token from Git.

### Retire the old node

**When:** first power it off in the Hetzner Console to stop its jobs and pings.
Power off is reversible. After the new cluster is operational, wait
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

Check only the expected node remains in the Hetzner Console, Tailscale
and `kubectl config get-contexts`. Verify the new cluster still serves prod.
