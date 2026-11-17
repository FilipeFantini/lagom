#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(git rev-parse --show-toplevel)"
export REPO_DIR
LAB_DIR="$(mktemp -d)"
chmod 0755 "$LAB_DIR"
export LAB_DIR
project="lagom-pi-test-$$"
compose=(docker compose -p "$project" -f "$REPO_DIR/platform/edge/pi/tests/compose.yaml")
cleanup() {
  "${compose[@]}" down --volumes --remove-orphans
  rm -rf "$LAB_DIR"
}
trap cleanup EXIT
"${compose[@]}" run --rm --no-deps check prepare
"${compose[@]}" run --rm --no-deps --entrypoint python check /repo/platform/edge/pi/tests/collector.py
"${compose[@]}" run --rm --no-deps connect --disable-telemetry test /lab/collector-test.yaml
"${compose[@]}" run --rm --no-deps connect --disable-telemetry test /lab/health-test.yaml
"${compose[@]}" run --rm --no-deps connect --disable-telemetry lint /lab/consumer.yaml
docker run --rm --network none -v "$REPO_DIR/platform/edge/pi:/cfg:ro" \
  redpandadata/connect:4.116.1@sha256:47ff773082146b6e538c527509af1a736b53063e68f0eb1266253a0832b39396 \
  --disable-telemetry lint /cfg/connect.yaml
"${compose[@]}" up -d --wait hub leaf postgres
"${compose[@]}" up -d connect
"${compose[@]}" run --rm --no-deps check setup
"${compose[@]}" stop hub
"${compose[@]}" run --rm --no-deps check offline
"${compose[@]}" restart leaf
"${compose[@]}" start hub
"${compose[@]}" run --rm --no-deps check settle
"${compose[@]}" stop connect
"${compose[@]}" run --rm --no-deps check replay
"${compose[@]}" start connect
"${compose[@]}" run --rm --no-deps check settle
