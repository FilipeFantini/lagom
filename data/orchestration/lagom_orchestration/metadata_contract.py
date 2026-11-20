"""The metadata contract: what gold and the boards must declare (ADR-0056).

The documentation page is only as good as the metadata under it. This reads
the dbt manifest and the boards and lists every place the contract is not
met, one line each:

- every gold model has a description, an enforced contract and an owner
  (`meta.owner`);
- every board has an exposure of the same name as its `id`, with a url and an
  owner, that depends on exactly the models the board reads, through its own
  file and every file it `extends`.

It never names a source or a model: a new source's gold models and boards are
held to the same rules without a change here. CI runs it (job `dashboards
(render)`); the `dashboards` job does not, since the manifest is the one CI
already checked.

`python -m lagom_orchestration.metadata_contract <manifest.json> <charts dir>`
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from lagom_orchestration.dashboards import board_files

GOLD_SCHEMA = "gold"
_REF = re.compile(r"""\bref\(\s*['"]([^'"]+)['"]\s*\)""")


def _blank(value: Any) -> bool:
    return not isinstance(value, str) or not value.strip()


def _owner(node: dict[str, Any]) -> Any:
    # dbt 1.12 writes `meta` under `config` and, as a copy, on the node itself.
    meta = (node.get("config") or {}).get("meta")
    if not isinstance(meta, dict) or "owner" not in meta:
        meta = node.get("meta")
    return meta.get("owner") if isinstance(meta, dict) else None


def _read_board(path: Path) -> tuple[Any, set[str]]:
    """The board's `id`, and the models `ref()`ed in it and in what it extends."""
    models: set[str] = set()
    seen: set[Path] = set()
    board_id: Any = None
    current: Path | None = path
    while current is not None:
        current = current.resolve()
        if current in seen:
            raise RuntimeError(f"{path.name}: `extends` loops back to {current.name}")
        seen.add(current)
        text = current.read_text(encoding="utf-8")
        models |= set(_REF.findall(text))
        document = yaml.safe_load(text)
        if not isinstance(document, dict):
            raise RuntimeError(f"{current}: not a YAML mapping")
        if current == path.resolve():
            board_id = document.get("id")
        parent = document.get("extends")
        current = current.parent / parent if isinstance(parent, str) else None
    return board_id, models


def _model_names(exposure: dict[str, Any], manifest: dict[str, Any]) -> set[str]:
    nodes = manifest["nodes"]
    return {
        nodes[unique_id]["name"] if unique_id in nodes else unique_id
        for unique_id in exposure["depends_on"].get("nodes", [])
        if unique_id.startswith("model.")
    }


def violations(manifest: dict[str, Any], charts: Path) -> list[str]:
    """Every place the contract is not met, one line each; an empty list means it is met."""
    found: list[str] = []
    gold = sorted(
        (
            node
            for node in manifest["nodes"].values()
            if node["resource_type"] == "model" and node["schema"] == GOLD_SCHEMA
        ),
        key=lambda node: node["name"],
    )
    if not gold:
        found.append(f"the manifest has no model in the {GOLD_SCHEMA} schema")
    for node in gold:
        name = node["name"]
        if _blank(node.get("description")):
            found.append(f"model {name}: no description")
        if (node.get("contract") or {}).get("enforced") is not True:
            found.append(f"model {name}: the contract is not enforced")
        if _blank(_owner(node)):
            found.append(f"model {name}: no meta.owner")

    exposures: dict[str, dict[str, Any]] = {}
    for exposure in manifest.get("exposures", {}).values():
        exposures[exposure["name"]] = exposure
    boards = board_files(charts)
    for path in boards:
        board_id, refs = _read_board(path)
        if _blank(board_id):
            found.append(f"board {path.name}: no id")
            continue
        exposure = exposures.get(board_id)
        if exposure is None:
            found.append(f"board {board_id}: no exposure with that name")
            continue
        if _blank(exposure.get("url")):
            found.append(f"exposure {board_id}: no url")
        if _blank((exposure.get("owner") or {}).get("name")):
            found.append(f"exposure {board_id}: no owner name")
        declared = _model_names(exposure, manifest)
        if declared != refs:
            found.append(
                f"exposure {board_id}: depends on {sorted(declared)}, "
                f"but the board reads {sorted(refs)}"
            )
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "usage: python -m lagom_orchestration.metadata_contract <manifest.json> <charts dir>",
            file=sys.stderr,
        )
        return 2
    manifest = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    charts = Path(argv[1])
    found = violations(manifest, charts)
    for line in found:
        print(line)
    if found:
        return 1
    gold = sum(
        1
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model" and node["schema"] == GOLD_SCHEMA
    )
    print(f"metadata contract met: {gold} gold model(s), {len(board_files(charts))} board(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
