# ADR-0028 — Dagster's UI over the tailnet, by `tailscale serve` on the node

- **Status:** Superseded by [ADR-0029](0029-operator-uis-behind-cloudflare-access.md)
- **Date:** 2026-11-05
- **Decided by:** the operator, who asked on 2026-11-05 for a stable address
  for the UI "protected by the tailnet"; the mechanism is the one this ADR
  recommends.
- **Supersedes:** in [ADR-0026](0026-dagster-one-instance-per-environment.md),
  "the UI is reached by `kubectl port-forward`". The port-forward stays as a
  fallback.

## Context and problem

Each environment runs its own Dagster
([ADR-0026](0026-dagster-one-instance-per-environment.md)). Today its UI is
opened with `kubectl port-forward` to `localhost`, which means:

- a command left running in a terminal for as long as the UI is open;
- the kubeconfig, a cluster-admin credential, needed just to read run history;
- an address that exists only on the machine running the command.

Dagster's open-source UI has no login. Whoever reaches the webserver can
start, cancel and delete runs and wipe assets. Reaching it is the access
control.

The node has no inbound port open to the internet. The operator reaches it
over Tailscale, where the node is `tag:lagom-node` and the ACL grants
operators SSH and the Kubernetes API only
([runbook](../architecture.md#tailscale-acl-and-zero-inbound-ports-m1)).

Memory is tight right now. Today's declared total is 3,966 MiB of the 5,632
MiB CI ceiling. Prod's Dagster brings it to about 5,534, leaving ~100 MiB
until the CronJobs on Postgres are retired
([architecture.md §11](../architecture.md#11-resource-budget)).

## Requirements and constraints

- Reachable from the operator's devices only, at an address that does not
  change. Staging now, prod once its Dagster exists.
- Nothing public: no inbound port, and no public record (DNS or otherwise)
  that names the tailnet or its addresses. The repository holds placeholders
  only ([ADR-0017](0017-public-repository-from-a-snapshot.md)).
- Fits the remaining budget. No new credential if it can be avoided.
- Reproducible on a rebuilt node from Git and the runbook.

## Alternatives considered

**1. The Tailscale Kubernetes operator.** A controller in the cluster. For
each Service marked for it, it starts a proxy Pod, which is a full
`tailscaled` (or a shared ProxyGroup). Each Service becomes a tailnet device
with its own hostname and tag. It is all GitOps, apart from the ACL and one
credential.

- **Memory:** at least two resident Pods, the operator and one proxy. Not
  measured here. Each proxy is a `tailscaled`, and the node's own measures
  47–55 MiB RSS (§11). Requests of ~128 MiB or more would not fit the
  ~100 MiB left after prod's Dagster.
- **Credential:** an OAuth client, sealed in the cluster, that can create
  auth keys for tagged devices. That would be the only credential in the
  cluster able to add machines to the tailnet.
- **Gains:** a hostname per service, an ACL per tag, HTTPS, and everything in
  Argo CD.

Not chosen now. It becomes the better option once several services need
their own names, or a second node joins, and the budget has room for it.

**2. `tailscale serve` on the node.** `tailscaled` already runs on the node,
since that is how the operator reaches it. `serve` makes it accept
connections on a tailnet port and pass them, as a reverse proxy, to an
address the node can reach. Here that address is a Service's cluster IP.

- **Memory:** no new process and no Pod: 0 MiB of requests.
- **Credential:** none.
- **Cost:** the serve setting is host configuration outside Argo CD, like the
  ACL, the firewall and k3s's `config.yaml`. Principle 3 does not hold
  outside the cluster yet ([architecture.md §3](../architecture.md#3-infrastructure)).
  `tailscaled` keeps the setting in its state across restarts and reboots,
  but a rebuilt node needs the runbook step again.
- **Address:** one hostname, the node's, with one port per environment, not
  a name per service.
- **Version:** needs Tailscale 1.92 or later. Earlier versions accept only
  `localhost` as a target. Checked in the source of `ipn/serve.go` at
  v1.92.0 and v1.104.5; Tailscale's documentation page still says
  localhost only.

**3. Cloudflare Tunnel + Access** (the roadmap row *Cloudflare Access
(login)*). `cloudflared`, ~30 MB, opens an outbound connection. A public
hostname such as `dagster.getlagom.app` serves the UI behind an Access login.
It fits the memory. Not chosen:

- **Wrong channel.** The row exists for people outside the tailnet: partners
  reading reports ([architecture.md §10](../architecture.md#10-access-channels)).
  The operator's channel is Tailscale.
- **It fails open.** It would put an admin UI that can start and delete runs
  at an internet hostname, with one policy between it and anyone. A
  misconfigured Access application means a public Dagster with no login.
  Tailscale fails closed: off the tailnet there is no route at all.
- **More to set up and keep:** a tunnel token sealed in the cluster, a Zero
  Trust setup, and a public DNS record.

It becomes right when someone without tailnet access needs the UI. That is
the row's trigger.

**Also considered:**

- **One port with a path per environment** (`/staging`, `/prod`). Rejected:
  it needs Dagster's `--path-prefix`, a change to each instance's chart
  values, for no gain over two ports.
- **Tailscale Services** (`svc:` names). These give each service its own
  tailnet name from the same `tailscale serve`, but need each service defined
  and the host approved in the admin console. They are the upgrade path if
  per-service names are wanted without the operator.

### A name under getlagom.app

**In public DNS, no.** Either record type gives away the identifiers the
public-exposure rule keeps out of public records:

- a CNAME to `<node-name>.<tailnet>.ts.net` publishes the tailnet name;
- an A record to the node's tailnet address publishes that address.

Neither grants access, because the address is unreachable off the tailnet.

**Privately, yes, in two ways, both deferred:**

- **Split DNS.** The tailnet sends lookups for a subdomain to a resolver we
  run. That is a new resident process, with records to maintain.
- **`extraDNSRecords` in the tailnet policy.** It is in alpha, Tailscale
  support turns it on, and it needs *Override DNS servers*. That sends every
  DNS lookup of every tailnet device through the tailnet's resolvers.

Over HTTPS, either name would also need a certificate of its own, since
Tailscale's covers only `ts.net` names. The MagicDNS name already does the
job:

- **private:** it resolves only on tailnet devices;
- **stable:** it is the node's name;
- **free.**

### HTTP, not HTTPS

`serve` can terminate HTTPS with a certificate Tailscale obtains for the
`ts.net` name. Every public certificate is recorded in Certificate
Transparency logs, which are public and permanent, and this one would carry
the node's full tailnet name. That is the identifier kept out of public
records.

**Plain HTTP over the tailnet is still encrypted.** WireGuard encrypts and
authenticates the connection end to end, so nothing crosses the internet in
the clear. The cost is in the browser:

- **"Not secure":** the browser labels the page this way.
- **Clipboard:** browser features that need a secure context are missing.
  The dagster-webserver 1.13.27 bundle was checked: the only such feature it
  uses is the clipboard. Its main copy helper falls back to another method,
  but some copy buttons may not work.

Switching to HTTPS is one command, if the operator accepts the certificate
log entry.

## Decision

- **`tailscale serve` on the node**, plain HTTP: tailnet port 8080 goes to
  staging's UI. Prod gets port 8081 the same way once its Dagster exists.
  The URL is `http://<node-name>:8080`. MagicDNS completes the short name on
  tailnet devices; the full name is `<node-name>.<tailnet>.ts.net`.
- **A Service per environment at a fixed cluster IP**, in a new Argo CD
  Application, `tailnet-access`
  ([`platform/infra/tailnet-access/`](../../platform/infra/tailnet-access/)).
  It selects the chart's webserver Pods. Staging is `10.43.0.30`; prod will
  be `10.43.0.31`.
  - **Why a fixed address:** a cluster IP is chosen when a Service is
    created. The chart's own Service gets a new one whenever it is
    recreated, for example after a rename, a recreated Application or a
    rebuild, and serve would then point at nothing.
  - **Why it is safe:** Kubernetes keeps the lower part of the service range
    for addresses that are asked for by number. For k3s's `10.43.0.0/16`
    that is `10.43.0.0/24`, and automatic allocation takes from the rest
    first (KEP-3070). If the address were taken anyway, creating the Service
    fails and Argo CD shows the error.
- **The ACL grants `tcp:8080` on `tag:lagom-node` to `group:operators`
  only.** The policy's `tests` assert it.
- **`kubectl port-forward` stays documented** as the fallback, for example
  when Tailscale on the node is down but the operator has another way in.

**Why the node can reach the address.** kube-proxy's rules translate a
cluster IP for every connection the node opens, including those of
processes on the node itself. Prod's NetworkPolicy admits traffic from the
local node: k3s's policy controller does so, tested on 2026-10-17
([`networkpolicy.yaml`](../../platform/infra/postgres-prod/networkpolicy.yaml)).

## Consequences

**Positive.**

- One URL per environment, no command left running, and no kubeconfig needed
  to look at Dagster.
- 0 MiB of requests, no new process in the cluster, no new secret.

**Negative.**

- **Outside Git:** the serve setting lives on the node. Argo CD does not see
  drift in it; `tailscale serve status` does.
- **Who gets in:** every device of every member of `group:operators` has full
  control of Dagster, the same as with port-forward. Dagster OSS has no
  per-user identity.
- **Duplicated labels:** the Service repeats the chart's selector labels. If
  a chart upgrade changes them, the URL answers 502 until the Service file is
  fixed.
- **HTTP:** the browser shows "Not secure", and some copy buttons may not
  work.

## Risks, cost, and reversal

Cost: zero.

**Memory:**

- **In the cluster:** 0 MiB, since a Service has no Pod.
  `tools/resource_budget.py` reports the same total before and after this change (3,966 MiB on 2026-11-05).
- **On the node:** `tailscaled`'s RSS, before and after serve is enabled,
  is measured by the operator in the runbook step and recorded in a note
  here. It is node overhead (§11), outside the CI ceiling.

**Risk:** a future Tailscale release could restrict `serve` targets again.
The URL would stop working loudly, and port-forward still works.

**Reversal:**

1. Run `tailscale serve --http=8080 off` on the node.
2. Delete the Service file.
3. Remove the port from the ACL.

## Notes

The text above is kept as decided. These notes record what changed since,
without rewriting it.

- **2026-11-05 — set up, measured, and withdrawn the same day.** On the node
  (Tailscale 1.104.4), the Service answered `/server_info` with 200 from the
  node, and `tailscale serve --bg --http=8080` listened only on the node's two
  tailnet addresses (`ss` showed both bound to `tailscale0`). `tailscaled`'s
  RSS went from 60,708 to 60,824 KiB, about 0.1 MiB, idle. From the Mac the
  URL timed out, as expected: the ACL had not yet granted the port, which
  also showed the default deny at work. The operator then rejected the
  approach (a port in the URL, plain HTTP, tailnet only) for Cloudflare
  Tunnel + Access, [ADR-0029](0029-operator-uis-behind-cloudflare-access.md).
  Serve was turned off on the node the same day, the ACL was never changed,
  and the Service `dagster-ui-tailnet` was removed with ADR-0029.
