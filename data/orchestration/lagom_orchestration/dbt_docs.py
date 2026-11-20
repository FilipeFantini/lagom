"""dbt's documentation as one static page (ADR-0056).

`dbt docs generate --static` reads the project's manifest and asks the lake,
read-only, for each table's columns (the catalog), and writes one HTML file with
both embedded. The connection is the dashboards' profile, not the warehouse's:
it attaches the lake read-only (data/dashboards/profiles.yml), which is all
`docs generate` needs.

The page is ours to publish and it is a tool's output, so it is checked before
it leaves: dbt's usage statistics are off in the manifest and in the page, the
catalog is whole, and the markup asks nothing from another host. A failed check
raises with its reason and nothing is published.

The markup check (`external_loads`) is a best-effort guard on the shape of dbt's
page: it lists the tags and attributes that load something, and a page can load
in ways it does not list. The full check is CI's, which opens the page in a real
browser on the same pinned dbt version and fails on any request the page causes
to another origin (data/dashboards/ci/page_requests.py).

`python -m lagom_orchestration.dbt_docs <repo> <output.html>` runs the same
function, so CI exercises the job's own code.
"""

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lagom_orchestration.dashboards import run_command, subprocess_env

# One `dbt docs generate` of this project took 262 MiB at peak and a few
# seconds on the fixture. The Pod is stopped after 25 minutes; this is killed
# well before that, and the run fails with the command's name.
DOCS_TIMEOUT_SECONDS = 240

PROFILE = "lagom_dashboards"
TARGET = "lake"

# Tags whose src, href or data attribute makes the browser fetch something.
# `<a href>` is a link a reader clicks, not a load, and is not here.
_LOADING_TAGS = frozenset(
    {"script", "link", "img", "iframe", "source", "embed", "object", "audio", "video"}
)
_LOADING_ATTRIBUTES = frozenset({"src", "href", "data"})
_OUTSIDE = ("//", "http:", "https:")

_TRACKING_OFF = '"send_anonymous_usage_stats": false'
_TRACKING_ON = '"send_anonymous_usage_stats": true'


class _Loads(HTMLParser):
    """The loads in a page's markup. A script's text is not markup and is not read."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _LOADING_TAGS:
            return
        for name, value in attrs:
            if name in _LOADING_ATTRIBUTES and (value or "").strip().lower().startswith(_OUTSIDE):
                self.found.append(f"<{tag} {name}={value!r}>")


def external_loads(html: str) -> list[str]:
    """The tags in `html` that load something from another host, as `<tag attr='value'>`.

    Parsed as markup, not searched as text: dbt's page holds URLs inside its
    script (template strings, the names of hosts it could load), and those load
    nothing. The reports' own check is a text search that fits their markup,
    and gives false positives here and misses a tracker a script adds. A
    relative address, a fragment and a `data:` URL stay in the page.
    """
    parser = _Loads()
    parser.feed(html)
    parser.close()
    return parser.found


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"dbt docs generate did not write {path.name}")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _check_tracking(manifest: Mapping[str, Any], html: str) -> None:
    # `send_anonymous_usage_stats: true` in the embedded manifest makes the
    # page load a tracker from other hosts when a reader opens it (a script
    # from a CDN and a collector). `DO_NOT_TRACK=1` turns it off, and this
    # checks that it did, in the manifest and in the page that carries it.
    if manifest["metadata"].get("send_anonymous_usage_stats") is not False:
        raise RuntimeError("manifest.json metadata.send_anonymous_usage_stats is not false")
    if _TRACKING_ON in html or _TRACKING_OFF not in html:
        raise RuntimeError(
            f"the page does not say {_TRACKING_OFF} (or says it is true): a reader's "
            "browser would load a tracker from other hosts"
        )


def _check_catalog(manifest: Mapping[str, Any], catalog: Mapping[str, Any]) -> None:
    if catalog.get("errors"):
        raise RuntimeError(f"catalog.json has errors: {catalog['errors']}")
    nodes = catalog.get("nodes") or {}
    missing = sorted(
        node["unique_id"]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model"
        and node["config"].get("enabled", True)
        and not (nodes.get(node["unique_id"]) or {}).get("columns")
    )
    if missing:
        raise RuntimeError(f"catalog.json has no columns for: {', '.join(missing)}")


def generate_docs(repo: Path, work: Path, environ: Mapping[str, str]) -> bytes:
    """Generate dbt's static documentation under `work`, check it, return the page.

    Raises, naming the reason, if the command fails or times out, if usage
    statistics are on, if the catalog is not whole, or if the page asks
    anything of another host.
    """
    repo = repo.resolve()
    target = work / "target"
    # DO_NOT_TRACK keeps dbt's own telemetry off, as the image intends, and it
    # is what makes the page's embedded manifest say `false` (checked below).
    # ADR-0044 decision 9 (telemetry on) is about dbt Charts' `dct` only.
    env = {**subprocess_env(environ), "DO_NOT_TRACK": "1"}
    run_command(
        "dbt docs generate",
        [
            "dbt",
            "docs",
            "generate",
            "--static",
            "--profile",
            PROFILE,
            "--profiles-dir",
            str(repo / "data" / "dashboards"),
            "--target",
            TARGET,
            "--project-dir",
            str(repo / "data" / "warehouse"),
            "--target-path",
            str(target),
            "--log-path",
            str(work / "logs"),
        ],
        env=env,
        cwd=work,
        timeout=DOCS_TIMEOUT_SECONDS,
    )
    manifest = _load_json(target / "manifest.json")
    catalog = _load_json(target / "catalog.json")
    page = target / "static_index.html"
    if not page.is_file() or page.stat().st_size == 0:
        raise RuntimeError("dbt docs generate did not write static_index.html")
    body = page.read_bytes()
    # No `errors=`: a page that is not UTF-8 is an error, not a replacement.
    html = body.decode("utf-8")
    _check_tracking(manifest, html)
    _check_catalog(manifest, catalog)
    loads = external_loads(html)
    if loads:
        raise RuntimeError(f"the documentation page loads from outside: {', '.join(loads)}")
    return body


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m lagom_orchestration.dbt_docs <repo> <output.html>", file=sys.stderr)
        return 2
    output = Path(argv[1])
    with tempfile.TemporaryDirectory(prefix="dbt-docs-") as directory:
        body = generate_docs(Path(argv[0]), Path(directory), os.environ)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    print(f"wrote {output} ({len(body)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
