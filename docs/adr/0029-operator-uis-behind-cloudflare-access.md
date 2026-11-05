# ADR-0029 — Operator UIs at getlagom.app hostnames, behind Cloudflare Access

- **Status:** Accepted
- **Date:** 2026-11-05
- **Decided by:** the operator, on 2026-11-05. They chose Cloudflare Tunnel +
  Access over `tailscale serve` after seeing the latter running, for three
  reasons:
  - a port in the URL is not how the platform should present its tools;
  - other orchestrators, such as Airflow, will follow;
  - those tools should be reachable from anywhere, behind a login.
- **Supersedes:** [ADR-0028](0028-dagster-ui-over-the-tailnet.md).

## Context and problem

[ADR-0028](0028-dagster-ui-over-the-tailnet.md) gave staging's Dagster UI the
address `http://<node-name>:8080`, by `tailscale serve` on the node. It was set
up on 2026-11-05 and turned off the same day, before the ACL opened the port
(ADR-0028 notes). Its limits were:

- one port per tool;
- plain HTTP;
- reachable only from tailnet devices.

Dagster's open-source UI has no login of its own. Whoever reaches it can start,
cancel and delete runs.

## Requirements and constraints

- **Address:** each tool gets its own HTTPS address under `getlagom.app`.
- **Login:** a login before any tool, since the tools have none.
- **Reach:** works from any device, without Tailscale.
- **No inbound port on the node:** it has none today, and the Hetzner
  firewall has no inbound rule.
- **Fits the budget:** Dagster in both environments leaves 98 MiB under the
  5,632 MiB CI ceiling until the CronJobs on Postgres are retired
  ([architecture.md §11](../architecture.md#11-resource-budget)).
- **Reviewed:** the list of what the internet can reach is in Git, reviewed
  like code.

## Alternatives considered

**Keep ADR-0028, `tailscale serve`.** Rejected by the operator for the
reasons above.

**Tailscale with HTTPS and per-service names.** Tailscale Services, or its
Kubernetes operator, give each tool an HTTPS name. Rejected:

- **Wrong domain:** the names are under `ts.net`, not `getlagom.app`.
- **Public tailnet name:** each certificate puts the tailnet's name in the
  public Certificate Transparency logs.
- **Tailnet only:** still reachable only from tailnet devices.
- **Memory:** the operator variant needs about 128 MiB.

**Our own login proxy in the cluster** (oauth2-proxy behind an ingress, with
cert-manager certificates). Rejected:

- **Opens a port:** port 443 on the node would have to be open to the
  internet, which ends "zero inbound ports".
- **More to run:** two resident components to run and patch.

**Cloudflare Tunnel + Access.** Chosen. The domain's DNS is already at
Cloudflare. The agent is light, ~30 MB by Cloudflare's figure. It only dials
out.

## Decision

- **One `cloudflared`**, a Deployment in its own namespace `cloudflared`
  ([`platform/infra/cloudflared/`](../../platform/infra/cloudflared/)),
  running one **locally-managed** tunnel.
  - *Locally managed* means its routing rules are a file we keep, not
    settings in Cloudflare's dashboard: [`config.yaml`](../../platform/infra/cloudflared/config.yaml).
  - The rules today:
    - `dagster-staging.getlagom.app` goes to staging's webserver;
    - `dagster.getlagom.app` goes to prod's;
    - any other hostname gets a 404 from the tunnel.
  - The tunnel's ID and secret are in a token, sealed
    ([ADR-0010](0010-sealed-secrets.md)).
- **One Access application** ("self-hosted" in Cloudflare's terms) covers the
  operator's UIs: both hostnames now, Airflow's later.
  - Its policy allows the operator's e-mail address.
  - The login is a one-time code Cloudflare e-mails to that address.
  - Anyone else gets the login page and no further.
- **`cloudflared` checks the login itself.** After a login, Access adds a
  signed token (a JWT) to every request. `cloudflared` refuses any request
  whose token is missing or not signed for this application
  (`originRequest.access.required`, with the team name and the application's
  audience tag).
  - If the Access application were deleted or set to bypass by mistake, the
    UIs would fail closed: no access.
  - The team name and audience tag are not secrets. Access puts both in the
    URL of every redirect to its login page.
- **DNS:** one proxied CNAME per hostname, created by `cloudflared tunnel route
  dns`. Public DNS answers with Cloudflare's addresses. The node's address and
  the tunnel's ID do not appear in it.
- **NetworkPolicy:**
  - `cloudflared` accepts no inbound connection.
  - Prod's webserver admits `cloudflared` on its port, in addition to prod's
    existing policy. Staging has no NetworkPolicy.
- **Tailscale:** it stays the channel for SSH and the Kubernetes API. Its ACL
  is unchanged; the port ADR-0028 would have opened was never granted.
  `kubectl port-forward` stays as the fallback for the UIs.

**How a request travels.**

1. `cloudflared` opens outbound connections to Cloudflare's edge, on port
   7844, and keeps them open.
2. A browser asks for `dagster.getlagom.app`. The request reaches Cloudflare,
   which terminates HTTPS.
3. Access looks for its login cookie. Without one, the browser is sent to the
   login page.
4. With one, Cloudflare sends the request down one of the tunnel's
   connections, with the signed token in a header.
5. `cloudflared` checks the token against Access's public keys.
6. `cloudflared` forwards the request to the webserver's Service over plain
   HTTP inside the cluster.

**Adding a tool, Airflow say:**

- a rule in `config.yaml`;
- a hostname in the Access application;
- a `route dns` for the hostname.

## Consequences

**Positive.**

- One named HTTPS address per tool, behind a login, from any device.
- No inbound port.
- The list of what is reachable is in Git.
- The trigger *Cloudflare Access (login)* in the roadmap is met: Dagster's UI
  is the first content that must not be public.

**Negative.**

- **Memory:** 48 MiB of requests, not yet measured. The headroom under the CI
  ceiling falls from 98 to 50 MiB until the CronJobs on Postgres are retired,
  which frees 512 MiB.
- **Cloudflare sees the traffic.** It terminates TLS. What passes is Dagster's
  UI: run metadata and logs, which may include sample rows of public data. The
  platform already depends on Cloudflare for DNS, R2 and Pages.
- **Configuration outside Git:**
  - the Access application and its policy;
  - the DNS records;
  - the Zero Trust account.

  This is like the Tailscale ACL. Terraform takes these over by its trigger
  ([roadmap](../roadmap.md#trigger-table)).
- **Two new credentials:**
  - **The tunnel token, sealed in the cluster.** Whoever holds it can run a
    connector and receive the traffic for these hostnames.
  - **The `cert.pem` from `cloudflared tunnel login`.** It can create tunnels
    and DNS records in the zone. It sits on the operator's Mac during setup
    and is deleted after.
- **The hostnames are public in DNS.** `dagster.getlagom.app` says a Dagster
  exists. Nothing is reachable through it without the login, and it names
  neither the node nor the tailnet.
- **The team name and audience tag are in the repository.** Both are public by
  construction, as above.
- **One component serves both environments,** like Argo CD does.
- **Restarts are manual for config changes.** `cloudflared` reads
  `config.yaml` at start only, so a change to it must also bump an annotation
  on the Deployment to restart it. Nothing checks this yet.

## Risks, cost, and reversal

**Cost:** Cloudflare's Zero Trust Free plan, up to 50 users. Sign-up may ask
for a payment method without charging it.

**Risks:**

- **Access misconfigured:** the JWT check in `cloudflared` fails closed. The
  runbook verifies this from a private browser window and with `curl`.
- **Cloudflare outage, or the token revoked:** the UIs are unreachable, and
  `kubectl port-forward` still works.
- **`cloudflared` compromised:** its egress is not restricted. It can reach
  staging, which has no NetworkPolicy, and prod's webserver, but nothing else
  in prod.

**Reversal:**

1. Delete the `cloudflared` Application. `prune` removes its objects.
2. Delete the DNS records, the Access application and the tunnel in
   Cloudflare.
