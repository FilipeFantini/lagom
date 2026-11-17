"""Docker-only integration checks. Uses NATS and Postgres wire protocols, no new dependency."""

import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path("/repo")
LAB = Path("/lab")


def extract_block(path: Path, key: str) -> str:
    lines = path.read_text().splitlines()
    start = lines.index(f"  {key}: |") + 1
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("    "):
            out.append(line[4:])
        elif not line:
            out.append("")
        else:
            break
    return "\n".join(out) + "\n"


def prepare() -> None:
    nats_manifest = ROOT / "platform/infra/streaming-staging/nats.yaml"
    (LAB / "hub.conf").write_text(
        extract_block(nats_manifest, "hub.conf").replace("/data", "/tmp/hub-data")
    )
    (LAB / "events.json").write_text(extract_block(nats_manifest, "events-stream.json"))
    leaf = (ROOT / "platform/edge/pi/leaf.conf").read_text()
    (LAB / "leaf.conf").write_text(
        leaf.replace("127.0.0.1", "0.0.0.0").replace("/var/lib/lagom-nats", "/tmp/leaf-data")
    )
    connect_manifest = ROOT / "platform/infra/streaming-staging/connect-config.yaml"
    (LAB / "consumer.yaml").write_text(extract_block(connect_manifest, "consumer.yaml"))


def pg_query(sql: str) -> list[tuple[str, ...]]:
    with socket.create_connection(("postgres", 5432), timeout=3) as sock:
        params = b"user\x00postgres\x00database\x00serving\x00\x00"
        sock.sendall(struct.pack("!II", 8 + len(params), 196608) + params)

        def recv_exact(n: int) -> bytes:
            buf = b""
            while len(buf) < n:
                chunk = sock.recv(n - len(buf))
                if not chunk:
                    raise RuntimeError("unexpected EOF from postgres")
                buf += chunk
            return buf

        def read_until_ready() -> list[tuple[str, ...]]:
            rows: list[tuple[str, ...]] = []
            while True:
                msg_type = recv_exact(1)
                (length,) = struct.unpack("!I", recv_exact(4))
                body = recv_exact(length - 4)
                if msg_type == b"D":
                    (ncols,) = struct.unpack("!H", body[:2])
                    pos = 2
                    row: list[str] = []
                    for _ in range(ncols):
                        (col_len,) = struct.unpack("!i", body[pos : pos + 4])
                        pos += 4
                        row.append(body[pos : pos + col_len].decode())
                        pos += col_len
                    rows.append(tuple(row))
                elif msg_type == b"E":
                    raise RuntimeError(body.decode(errors="replace"))
                elif msg_type == b"Z":
                    return rows

        read_until_ready()
        payload = sql.encode() + b"\x00"
        sock.sendall(b"Q" + struct.pack("!I", 4 + len(payload)) + payload)
        rows = read_until_ready()
        sock.sendall(b"X" + struct.pack("!I", 4))
        return rows


class Client:
    def __init__(self, host: str, user: str | None = None, password: str | None = None) -> None:
        self.socket = socket.create_connection((host, 4222), timeout=3)
        self.file = self.socket.makefile("rb")
        assert self.file.readline().startswith(b"INFO ")
        auth: dict[str, object] = {"verbose": False, "pedantic": True, "headers": True}
        if user:
            auth.update(user=user, **{"pass": password})
        self.send(b"CONNECT " + json.dumps(auth).encode() + b"\r\nPING\r\n")
        self.until_pong()
        self.sid = 0

    def send(self, data: bytes) -> None:
        self.socket.sendall(data)

    def until_pong(self) -> None:
        while True:
            line = self.file.readline()
            if line == b"PONG\r\n":
                return
            if line == b"PING\r\n":
                self.send(b"PONG\r\n")
            elif line.startswith(b"-ERR") or not line:
                raise RuntimeError(line.decode())

    def request(self, subject: str, body: object) -> dict[str, object]:
        self.sid += 1
        inbox = f"_INBOX.lab{os.getpid()}.{self.sid}"
        raw = json.dumps(body).encode()
        cmd = (
            f"SUB {inbox} {self.sid}\r\nUNSUB {self.sid} 1\r\nPUB {subject} {inbox} {len(raw)}\r\n"
        )
        self.send(cmd.encode() + raw + b"\r\n")
        while True:
            line = self.file.readline()
            if line.startswith((b"MSG ", b"HMSG ")):
                parts = line.split()
                payload = self.file.read(int(parts[-1]))
                assert self.file.read(2) == b"\r\n"
                if line.startswith(b"HMSG "):
                    header_length = int(parts[-2])
                    if payload.startswith(b"NATS/1.0 503"):
                        raise RuntimeError("no responders")
                    payload = payload[header_length:]
                res: dict[str, object] = json.loads(payload)
                return res
            if line == b"PING\r\n":
                self.send(b"PONG\r\n")
            elif line.startswith(b"-ERR") or not line:
                raise RuntimeError(line.decode())

    def api(self, subject: str, body: object) -> dict[str, object]:
        result = self.request(subject, body)
        assert "error" not in result, result
        return result


def clients() -> tuple[Client, Client]:
    return Client("hub", "internal", "lab-internal"), Client("leaf")


def event(i: int, source: str = "pi") -> dict[str, object]:
    return dict(
        event_id=f"{source}/health/2026-11-17T00:00:{i:02d}Z",
        source=source,
        signal="health",
        collected_at=f"2026-11-17T00:00:{i:02d}Z",
        cpu_temp_c=45,
        cpu_freq_mhz=600,
        throttled=False,
        under_voltage=False,
        load_1m=0.1,
        mem_used_bytes=10,
        mem_total_bytes=100,
        disk_used_bytes=10,
        disk_total_bytes=100,
    )


def publish(client: Client, start: int, stop: int, source: str = "pi") -> None:
    for i in range(start, stop):
        client.api(f"events.{source}.health", event(i, source))


def stream_messages(client: Client, domain: str, stream: str) -> int:
    state = client.api(f"$JS.{domain}.API.STREAM.INFO.{stream}", {})["state"]
    assert isinstance(state, dict)
    return int(state["messages"])


def settle(expected: int, rows_expected: int | None = None, pending: int = 0) -> None:
    if rows_expected is None:
        rows_expected = expected
    deadline = time.monotonic() + 45
    count = -1
    rows: list[tuple[str, ...]] = []
    while time.monotonic() < deadline:
        hub, _ = clients()
        count = stream_messages(hub, "hub", "EVENTS")
        rows = pg_query("select event_id from live.events order by event_id")
        consumer = hub.request("$JS.hub.API.CONSUMER.INFO.EVENTS.serving-writer", {})
        delivered = consumer.get("delivered")
        seq = int(delivered["stream_seq"]) if isinstance(delivered, dict) else 0
        caught_up = (
            "error" not in consumer
            and consumer.get("num_pending") == 0
            and consumer.get("num_ack_pending") == pending
            and seq >= expected
        )
        if count == expected and len(rows) == rows_expected and caught_up:
            actual = {r[0] for r in rows}
            wanted = {str(event(i)["event_id"]) for i in range(rows_expected - 1)} | {
                str(event(0, "synthetic")["event_id"])
            }
            assert actual == wanted, (actual, wanted)
            print(f"PASS: hub={count}, table={len(rows)}, exact identifiers match", flush=True)
            return
        time.sleep(0.5)
    raise AssertionError(
        f"catch-up failed: hub={count}, table={len(rows)}, "
        f"expected hub={expected}, rows={rows_expected}"
    )


def setup() -> None:
    hub, leaf = clients()
    edge_cfg = json.loads((ROOT / "platform/edge/pi/edge-stream.json").read_text())
    leaf.api("$JS.leaf.API.STREAM.CREATE.EDGE", edge_cfg)
    hub.api("$JS.hub.API.STREAM.CREATE.EVENTS", json.loads((LAB / "events.json").read_text()))
    pg_query("create schema live")
    pg_query(
        "create unlogged table live.events("
        "event_id text primary key, source text not null, signal text not null, "
        "collected_at timestamptz not null, received_at timestamptz not null, "
        "payload jsonb not null)"
    )
    publish(hub, 0, 1, "synthetic")
    publish(leaf, 0, 10)
    settle(11)
    # A local client on the Pi must not reach the hub's API through the leaf.
    try:
        Client("leaf").request("$JS.hub.API.STREAM.INFO.EVENTS", {})
    except (RuntimeError, TimeoutError):
        print("PASS: leaf cannot read the hub stream", flush=True)
    else:
        raise AssertionError("leaf read the hub stream")
    # Anonymous clients cannot authenticate to the hub.
    try:
        Client("hub")
    except RuntimeError:
        print("PASS: anonymous hub client denied", flush=True)
    else:
        raise AssertionError("anonymous hub access")
    # Publishing a foreign subject through the leaf must not affect the hub.
    try:
        Client("leaf").request("events.synthetic.health", event(1, "synthetic"))
    except (RuntimeError, TimeoutError):
        print("PASS: foreign publication has no responder", flush=True)
    else:
        raise AssertionError("foreign publication accepted")
    settle(11)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "prepare":
        prepare()
    elif mode == "setup":
        setup()
    elif mode == "offline":
        leaf = Client("leaf")
        publish(leaf, 10, 20)
        assert stream_messages(leaf, "leaf", "EDGE") == 20
        print("PASS: 10 more events persisted while hub is stopped", flush=True)
    elif mode == "settle":
        deadline = time.monotonic() + 45
        hub, _ = clients()
        count = -1
        while time.monotonic() < deadline:
            count = stream_messages(hub, "hub", "EVENTS")
            if count == 21:
                settle(21)
                break
            if count == 22:
                settle(22, rows_expected=21, pending=1)
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"expected 21 normal records, then 1 rejected forgery; hub={count}"
            )
    elif mode == "replay":
        hub, _ = clients()
        hub.api("$JS.hub.API.CONSUMER.DELETE.EVENTS.serving-writer", {})
        pg_query("delete from live.events where event_id = 'pi/health/2026-11-17T00:00:19Z'")
        # A leaf can choose the delivery suffix and payload independently. The
        # hub sources only events.pi.>, so this reaches the consumer as a Pi
        # subject carrying a forged synthetic identity.
        leaf = Client("leaf")
        leaf.api("events.pi.health", event(10, "synthetic"))
    else:
        raise ValueError(mode)
