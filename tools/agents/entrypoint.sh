#!/usr/bin/env bash
# Runs as root once, at container start, then hands over to Paseo's own
# entrypoint, which drops the daemon and every agent to the user `paseo`.
# That user has no capabilities, so it cannot undo anything done here.
set -euo pipefail

# The container's traffic leaves through the Mac, and the Mac is on the
# tailnet. Network position there is authority: Dagster's UI has no login
# (ADR-0028). Blackhole routes make the tailnet unreachable from inside the
# container (ADR-0029). Fails, and stops the container, without NET_ADMIN.
ip route add blackhole 100.64.0.0/10
if [[ -e /proc/net/if_inet6 ]]; then
  ip -6 route add blackhole fd7a:115c:a1e0::/48
fi

# A fresh named volume is owned by root; Paseo's entrypoint prepares $HOME
# but not the workspace.
if [[ "$(stat -c %u /workspace)" == "0" ]]; then
  chown paseo:paseo /workspace
fi

exec /usr/bin/tini -- /usr/local/bin/paseo-docker-entrypoint "$@"
