# ADR-0050 — The Pi streaming runtime and its private link to the hub

- **Status:** Proposed; deployment requires the operator's PR approval.
- **Date:** 2026-11-17
- **Amends:** [ADR-0045](0045-streaming-from-a-raspberry-pi-outside-the-cluster.md), decisions 1–4 and 11, and its open implementation questions.
- **Related:** [ADR-0047](0047-streaming-without-a-stream-processor-and-staging-on-or-off.md); the processing design is unchanged.

## Context

Staging already consumes synthetic events. The Pi experiments proved local
persistence and recovery but used a synthetic producer and temporary processes.
They did not implement hardware collection, systemd services or a restricted
hub credential. M6 step 5 prepares these and points the Pi at staging first.

## Decisions

1. Configuration lives in `platform/edge/pi/`, outside Argo CD's watched
   directories. Two systemd services run as separate unprivileged users.
   NATS persists to its own state directory; Connect starts only after
   systemd-timesyncd has synchronized the clock in this boot. A missing clock
   marker fails startup and is retried. The leaf stays up while offline.
2. Keep Redpanda Connect as the only event tool. Its `subprocess` inputs run
   continuous Linux `ping`; a `command` processor reads hardware each second.
   The small shell/awk adapters sanitize Linux output before Connect sees it;
   they have no broker client, scheduling daemon or persistence of their own.
   Reply, timeout and ICMP error each yield an observation. A late reply after
   a reported timeout is ignored. `collected_at` is observation time, not an
   inferred send time. Failure to read hardware is an error, never a zero.
3. Use `source=pi`; identifiers include the ping target. Only MIT or Apache-2.0
   Connect components are allowed and telemetry is disabled. Keep target IPs
   and the selected network interface in a root-owned file on the Pi, outside
   Git. Collector output contains an explicit set of numeric fields and labels.
4. The leaf's stream is `EDGE`, domain `leaf`, subjects `events.pi.>`, with a
   512 MiB cap and discard-old retention. This is an estimated three-day outage
   buffer, not an unlimited loss guarantee. The server's file cap is 640 MB,
   above the stream cap. Both services have memory limits and swap disabled.
5. Tailscale Serve on the existing node forwards tailnet TCP port 7422 to the
   staging NATS Service's private cluster address. The Pi gets TCP 7422 only
   for broker traffic, plus ICMP to the node's tailnet address so its configured
   `PING_NODE` measurement can run; ICMP is not broker access. No public listener, NodePort,
   LoadBalancer or new resident process. Its setting is host configuration:
   reapply it after Service recreation or node recovery. Confirm the installed
   Tailscale accepts a non-loopback TCP target before enabling it; the previous
   private-UI implementation used this capability (ADR-0028), but this TCP
   route still needs validation on the node. The network policy admits no Pod
   to 7422; host-originated traffic on this single node reaches its local Pod.
   Validate this with the actual k3s network policy before admitting the Pi.
6. NATS authenticates internal clients and the leaf through its main user list.
   The Pi credential may send only its sourcing delivery and protocol replies;
   it receives only consumer-creation and flow-control messages for `EDGE`.
   It cannot call the hub's API, subscribe to hub events or publish directly to
   another source. Do not duplicate the leaf user in `leafnodes.authorization`:
   the experiment showed that doing so bypassed the main user's permissions.
   `no_advertise` keeps the leaf from trying private cluster addresses.
   Internal clients need a separate password, held only in the cluster.
7. The hub copies `EDGE` with a dedicated `PI.DELIVER` prefix while continuing
   to accept synthetic events. Temporary double load needs a 3 GiB stream cap
   inside the existing 4 GiB volume: at 10 events/s and the spike's 458 B/event,
   seven days are 2.58 GiB. Measure again with real events, including catch-up;
   if seven days do not fit, stop rollout and revisit capacity. Memory requests
   remain unchanged until measured (hub 64 MiB, Connect 128 MiB).
8. Step 6 moves the Pi to prod permanently. Remove staging's Serve forwarder,
   source configuration, leaf listener and Pi credential then. Staging keeps
   its synthetic producer. Its aggregate freshness test is temporarily accepted;
   acceptance in step 5 reconciles the Pi's identifiers independently.

## Alternatives and trade-offs

A second collector (Telegraf) adds an event tool where Connect can adapt the
Linux commands. A custom NATS publisher duplicates delivery logic. Both are
rejected. A host port or public Service would require another exposure rule;
Serve reuses the existing private transport with no added memory request.
A Kubernetes Tailscale operator would add resident machinery for one port.

The Pi owns its local disk and can fabricate its readings: broker permissions
are not attestation of physical measurements. The remote credential gives it
no database access. Local loopback access can inspect its own buffer; the
no-read restriction concerns the hub's data, not root on the Pi's own disk.
The transport is encrypted by Tailscale; there is no second TLS layer inside
this single-node cluster. Host configuration can drift outside Git, so the
runbook checks both Serve state and connectivity.

## Validation and reversal

`platform/edge/pi/tests/run.sh` runs actual NATS, Connect and Postgres in an
internal Docker network. It checks restricted access, persistence across a hub
outage and leaf restart, exact identifiers, and replay into an existing table.
Parser fixtures cover timeout, late duplicate, ICMP failure, malformed output
and sequence wrap. These do not prove Pi hardware, systemd startup, Tailscale,
k3s network policy or 24-hour resource usage; those are deployment gates in the
runbook, not claims of this PR.

Rollback: disable only Serve's 7422 forwarder, stop the Pi's collector and leaf,
and revert this PR through Git. Keep the Pi's state and the hub's volume until
reconciliation confirms all events are archived. Do not reset all Serve
settings or delete either stream to roll back connectivity.
