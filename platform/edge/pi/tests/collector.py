"""Offline parser fixtures and configuration checks, run in a Linux container."""

import json
import subprocess
from pathlib import Path

ROOT = Path("/repo/platform/edge/pi")
result = subprocess.run(
    ["awk", "-v", "target=gateway", "-f", str(ROOT / "ping.awk"), str(ROOT / "tests/ping.txt")],
    check=True,
    text=True,
    capture_output=True,
)
rows = [json.loads(line) for line in result.stdout.splitlines()]
assert len(rows) == 5, rows
assert [r["lost"] for r in rows] == [False, True, True, True, False]
assert [r["rtt_ms"] for r in rows] == [1.25, None, None, None, 0.001]
assert "192.0.2.1" not in result.stdout
bad = subprocess.run(
    ["awk", "-v", "target=gateway", "-f", str(ROOT / "ping.awk")],
    input="[1794910000.100000] 64 bytes from 192.0.2.1: icmp_seq=1 ttl=64 time=bad ms\n",
    text=True,
    capture_output=True,
)
assert bad.returncode != 0
assert not bad.stdout
assert "192.0.2.1" not in bad.stderr
print(
    "PASS: replies, timeout, late duplicate, host unreachable, "
    "TTL exceeded, malformed input and privacy"
)

# Bound memory over more than a full ICMP sequence cycle.
long_input = "\n".join(
    f"[{1794910000 + i}.100000] 64 bytes from 192.0.2.1: icmp_seq={i % 65536} ttl=64 time=1 ms"
    for i in range(65538)
)
wrapped = subprocess.run(
    ["awk", "-v", "target=node", "-f", str(ROOT / "ping.awk")],
    input=long_input,
    text=True,
    capture_output=True,
    check=True,
)
assert len(wrapped.stdout.splitlines()) == 65538
print("PASS: sequence wrap, 65538 observations")

connect_lines = (ROOT / "connect.yaml").read_text().splitlines()
pipeline_start = connect_lines.index("pipeline:")
output_start = connect_lines.index("output:")
pipeline_yaml = "\n".join(connect_lines[pipeline_start:output_start])
health_map_start = connect_lines.index("          - mapping: |")
health_sub_start = connect_lines.index("      - subprocess:")
health_proc_lines = [line[10:] for line in connect_lines[health_map_start:health_sub_start]]
health_pipeline_yaml = "pipeline:\n  processors:\n" + "\n".join(
    "    " + line for line in health_proc_lines
)

unit_tests = [
    {
        "name": "ping identifier includes its target, with no raw timestamp field",
        "input_batch": [
            {
                "json_content": {
                    "signal": "ping",
                    "target": "gateway",
                    "observed_at": 1794910000.25,
                    "lost": False,
                    "rtt_ms": 1.25,
                }
            }
        ],
        "output_batches": [
            [
                {
                    "json_equals": {
                        "source": "pi",
                        "signal": "ping",
                        "target": "gateway",
                        "lost": False,
                        "rtt_ms": 1.25,
                        "collected_at": "2026-11-17T10:06:40.250000000Z",
                        "event_id": "pi/ping.gateway/2026-11-17T10:06:40.250000000Z",
                    }
                }
            ]
        ],
    },
    {
        "name": "health preserves its collection time",
        "input_batch": [
            {
                "json_content": {
                    "signal": "health",
                    "collected_at": "2026-11-17T00:00:00.000000000Z",
                    "throttled": False,
                }
            }
        ],
        "output_batches": [
            [
                {
                    "json_equals": {
                        "source": "pi",
                        "signal": "health",
                        "collected_at": "2026-11-17T00:00:00.000000000Z",
                        "throttled": False,
                        "event_id": "pi/health/2026-11-17T00:00:00.000000000Z",
                    }
                }
            ]
        ],
    },
]
Path("/lab/collector-test.yaml").write_text(f"{pipeline_yaml}\ntests: {json.dumps(unit_tests)}\n")

health_tests = [
    {
        "name": "health command integer flags become booleans",
        "input_batch": [{"json_content": {"signal": "health", "throttled": 1, "under_voltage": 0}}],
        "output_batches": [
            [{"json_contains": {"signal": "health", "throttled": True, "under_voltage": False}}]
        ],
    }
]
Path("/lab/health-test.yaml").write_text(
    f"{health_pipeline_yaml}\ntests: {json.dumps(health_tests)}\n"
)
