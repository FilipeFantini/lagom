#!/usr/bin/env bash
# Keep addresses out of stdout/stderr before Connect sees a message.
set -euo pipefail
export LC_ALL=C
case "${1:-}" in
  gateway) target="${PING_GATEWAY:?}" ;;
  resolver-a) target="${PING_RESOLVER_A:?}" ;;
  resolver-b) target="${PING_RESOLVER_B:?}" ;;
  node) target="${PING_NODE:?}" ;;
  *) echo 'Unknown ping label' >&2; exit 2 ;;
esac
# Literal IPs only: no DNS failure can masquerade as packet loss, no option injection.
if [[ ! "$target" =~ ^[0-9a-fA-F:.]+$ ]]; then
  echo 'Ping target must be a literal IP address' >&2
  exit 2
fi
# -O reports a missing reply before sending the next probe; -D timestamps it.
# Never send ping's raw output (which contains the target) to the pipeline.
if ! ping -n -D -O -i 1 -W 1 "$target" 2>/dev/null |
    awk -v target="$1" -f /etc/lagom-pi/ping.awk; then
  echo "Ping input failed for label $1" >&2
  exit 1
fi
