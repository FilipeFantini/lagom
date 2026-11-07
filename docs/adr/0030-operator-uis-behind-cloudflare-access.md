# ADR-0030 — ofantini.com as the domain; operator UIs at ops.ofantini.com behind Cloudflare Access

- **Status:** Accepted
- **Date:** 2026-11-07
- **Decided by:** the operator, on 2026-11-05 and 2026-11-07.
  - **2026-11-05:** they chose Cloudflare Tunnel + Access over
    `tailscale serve` after seeing the latter running, because a port in the
    URL is not how the platform should present its tools, other orchestrators
    such as Airflow will follow, and those tools should be reachable from
    anywhere, behind a login.
  - **2026-11-07:** they bought `ofantini.com`, at Cloudflare's registrar, to
    replace `getlagom.app`. They also chose one hostname with a path per tool
    (`/dagster`, later `/airflow`) over one hostname per tool, and on the
    architect's advice put the tools on `ops.ofantini.com` rather than on the
    apex.
- **Supersedes:**
  - [ADR-0028](0028-dagster-ui-over-the-tailnet.md), in full;
  - in [ADR-0022](0022-platform-as-a-portfolio-of-architectures.md), the
    domain `getlagom.app`.

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

The platform's public domain was `getlagom.app`
([ADR-0022](0022-platform-as-a-portfolio-of-architectures.md)). No site was
ever published on it. On 2026-11-07 the operator replaced it with
`ofantini.com`, under which the portfolio site and the tools both live.

## Requirements and constraints

- **Address:** one HTTPS address for the operator's tools, one path per tool.
- **Login:** a login before any tool, since the tools have none.
- **Reach:** works from any device, without Tailscale.
- **No inbound port on the node:** it has none today, and the Hetzner
  firewall has no inbound rule.
- **The public site must not depend on the node.** A static site keeps
  working when the cluster is down.
- **Fits the budget:** Dagster in both environments leaves 98 MiB under the
  5,632 MiB CI ceiling until the CronJobs on Postgres are retired
  ([architecture.md §11](../architecture.md#11-resource-budget)).
- **Reviewed:** the list of what the internet can reach is in Git, reviewed
  like code.
- **Free certificate:** Cloudflare's free certificate covers the apex and one
  level of subdomain (`*.ofantini.com`). A deeper name such as
  `dagster.ops.ofantini.com` needs the paid Advanced Certificate Manager.

## Alternatives considered

**Keep ADR-0028, `tailscale serve`.** Rejected by the operator for the
reasons above.

**Tailscale with HTTPS and per-service names.** Tailscale Services, or its
Kubernetes operator, give each tool an HTTPS name. Rejected:

- **Wrong domain:** the names are under `ts.net`, not the platform's domain.
- **Public tailnet name:** each certificate puts the tailnet's name in the
  public Certificate Transparency logs.
- **Tailnet only:** still reachable only from tailnet devices.
- **Memory:** the operator variant needs about 128 MiB.

**Our own login proxy in the cluster** (oauth2-proxy behind an ingress, with
cert-manager certificates). Rejected:

- **Opens a port:** port 443 on the node would have to be open to the
  internet, which ends "zero inbound ports".
- **More to run:** two resident components to run and patch.

**Cloudflare Tunnel + Access.** Chosen. The domain is registered and served
at Cloudflare. The agent is light, ~30 MB by Cloudflare's figure. It only
dials out.

**Where the tools live under the domain:**

- **One hostname per tool** (`dagster.ofantini.com`, `airflow.ofantini.com`).
  Each tool sits at the root of its own address, with no change to the tool.
  Rejected by the operator: one address with a path per tool reads cleaner.
- **Paths on the apex** (`ofantini.com/ops/dagster`). Rejected. A DNS name
  points at one destination. The apex would have to point at the tunnel, so
  the public site would be served through the node and go down with it, or a
  Cloudflare Worker would have to split the apex by path in front of both.
- **Paths on `ops.ofantini.com`.** Chosen.
  - The apex stays free for the static site.
  - Every tool shares one hostname, one DNS record and one Access
    application.
  - The cost: each tool must serve itself under its prefix, so its links,
    assets and WebSocket carry the prefix. Dagster supports this natively
    (`--path-prefix`), and so does Airflow (`base_url`).

## Decision

- **The domain is `ofantini.com`.** It replaces `getlagom.app` everywhere the
  platform names a public address. The portfolio site will be at the apex
  (M4).
  - **Exposure:** the domain carries the operator's surname. The operator
    accepted it as an exception to minimal public exposure, beside the
    GitHub account name and the LICENSE holder
    ([`AGENTS.md`](../../AGENTS.md) §3).
- **The operator's tools are at `https://ops.ofantini.com/<tool>`.**
  - `https://ops.ofantini.com/dagster` is prod's Dagster;
  - `https://ops.ofantini.com/dagster-staging` is staging's;
  - a later tool takes the next path, such as `/airflow`;
  - anything else, `/` included, gets a 404 from the tunnel.

  Each Dagster serves itself under its path: `dagsterWebserver.pathPrefix` in
  its `values.yaml`. Its readiness probe follows the prefix, since the chart's
  default asks for `/server_info` at the root, which a prefixed webserver
  does not answer.
- **One `cloudflared`**, a Deployment in its own namespace `cloudflared`
  ([`platform/infra/cloudflared/`](../../platform/infra/cloudflared/)),
  running one **locally-managed** tunnel.
  - *Locally managed* means its routing rules are a file we keep, not
    settings in Cloudflare's dashboard:
    [`config.yaml`](../../platform/infra/cloudflared/config.yaml).
  - Each rule matches the hostname and a regular expression on the path, and
    passes the path on unchanged.
  - The tunnel's ID and secret are in a token, sealed
    ([ADR-0010](0010-sealed-secrets.md)).
- **One Access application** ("self-hosted" in Cloudflare's terms) covers the
  whole of `ops.ofantini.com`, every path included.
  - Its policy allows the operator's e-mail address.
  - The login is a one-time code Cloudflare e-mails to that address.
  - Anyone else gets the login page and no further.
- **`cloudflared` checks the login itself.** After a login, Access adds a
  signed token (a JWT) to every request. `cloudflared` refuses any request
  whose token is missing or not signed for this application
  (`originRequest.access.required`, with the team name and the application's
  audience tag).
  - If the Access application were deleted or set to bypass by mistake, the
    tools would fail closed: no access.
  - The team name and audience tag are not secrets. Access puts both in the
    URL of every redirect to its login page.
- **DNS:** one proxied CNAME, `ops.ofantini.com`, created by `cloudflared
  tunnel route dns`. Public DNS answers with Cloudflare's addresses. The
  node's address and the tunnel's ID do not appear in it.
- **NetworkPolicy:**
  - `cloudflared` accepts no inbound connection.
  - Prod's webserver admits `cloudflared` on its port, in addition to prod's
    existing policy. Staging has no NetworkPolicy.
- **Tailscale:** it stays the channel for SSH and the Kubernetes API. Its ACL
  is unchanged; the port ADR-0028 would have opened was never granted.
  `kubectl port-forward` stays as the fallback for the tools.

**How a request travels.**

1. `cloudflared` opens outbound connections to Cloudflare's edge, on port
   7844, and keeps them open.
2. A browser asks for `https://ops.ofantini.com/dagster`. The request reaches
   Cloudflare, which terminates HTTPS.
3. Access looks for its login cookie. Without one, the browser is sent to the
   login page.
4. With one, Cloudflare sends the request down one of the tunnel's
   connections, with the signed token in a header.
5. `cloudflared` checks the token against Access's public keys.
6. `cloudflared` picks the first rule whose path matches, and forwards the
   request, path unchanged, to that webserver's Service over plain HTTP
   inside the cluster.

**Adding a tool, Airflow say:**

- a rule in `config.yaml`, with its path;
- the tool configured to serve under that path.

The hostname, its DNS record and the Access application already cover it.

## Consequences

**Positive.**

- One HTTPS address for every tool, behind a login, from any device.
- No inbound port.
- The list of what is reachable is in Git.
- The public site at the apex never depends on the node.
- A new tool needs no DNS or Access change.
- The trigger *Cloudflare Access (login)* in the roadmap is met: Dagster's UI
  is the first content that must not be public.

**Negative.**

- **Memory:** 48 MiB of requests, not yet measured. The headroom under the CI
  ceiling falls from 98 to 50 MiB until the CronJobs on Postgres are retired,
  which frees 512 MiB.
- **Every tool must run under a path prefix,** and some tools handle that
  less well than others. A tool that builds a link without its prefix fails
  for that link only: its 404 or its redirect shows in the browser. The
  first visit after each tool is added is the test (runbook).
- **One login for every tool.** One Access application means one policy for
  all of `ops.ofantini.com`. A tool that needs a different audience gets a
  path-scoped Access application of its own.
- **Cloudflare sees the traffic.** It terminates TLS. What passes is Dagster's
  UI: run metadata and logs, which may include sample rows of public data. The
  platform already depends on Cloudflare for DNS, R2 and Pages.
- **Configuration outside Git:**
  - the Access application and its policy;
  - the DNS record;
  - the Zero Trust account;
  - the zone's security settings.

  This is like the Tailscale ACL. Terraform takes these over by its trigger
  ([roadmap](../roadmap.md#trigger-table)).
- **Two new credentials:**
  - **The tunnel token, sealed in the cluster.** Whoever holds it can run a
    connector and receive the traffic for `ops.ofantini.com`.
  - **The `cert.pem` from `cloudflared tunnel login`.** It can create tunnels
    and DNS records in the zone. It sits on the operator's Mac during setup
    and is deleted after.
- **The hostname is public in DNS.** `ops.ofantini.com` says operator tools
  exist. Nothing is reachable through it without the login, and it names
  neither the node nor the tailnet.
- **The team name and audience tag are in the repository.** Both are public by
  construction, as above.
- **One component serves both environments,** like Argo CD does.
- **Restarts are manual for config changes.** `cloudflared` reads
  `config.yaml` at start only, so a change to it must also bump an annotation
  on the Deployment to restart it. Nothing checks this yet.

## Risks, cost, and reversal

**Cost:** the domain's registration, which the operator pays at Cloudflare's
registrar, and Cloudflare's Zero Trust Free plan, up to 50 users. Sign-up may
ask for a payment method without charging it. No setting this ADR uses is
paid.

**Risks:**

- **Access misconfigured:** the JWT check in `cloudflared` fails closed. The
  runbook verifies the login from a private browser window and with `curl`.
- **The Cloudflare account taken over:** whoever controls it controls the
  DNS, the Access policy and the tunnel. Two-factor authentication on the
  account is a setup step (runbook).
- **Cloudflare outage, or the token revoked:** the tools are unreachable, and
  `kubectl port-forward` still works.
- **`cloudflared` compromised:** its egress is not restricted. It can reach
  staging, which has no NetworkPolicy, and prod's webserver, but nothing else
  in prod.

**Reversal:**

1. Remove `apps/cloudflared.yaml` from Git. `root` then deletes the
   Application, but not its objects: no Application here carries Argo CD's
   resources finalizer.
2. The operator deletes them by hand: the namespace `cloudflared`, and the
   NetworkPolicy `allow-cloudflared-to-dagster-webserver` in `prod`.
3. Delete the DNS record, the Access application and the tunnel in
   Cloudflare.
4. Remove `pathPrefix` and the probe path from each Dagster's `values.yaml`.
