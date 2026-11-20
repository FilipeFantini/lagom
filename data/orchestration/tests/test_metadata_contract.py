"""The metadata contract, on synthetic manifests and boards.

Each test breaks one rule of a valid pair and shows the check names it. The
names here (`fct_things`, `things`) are made up on purpose: the check must not
depend on any source.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from lagom_orchestration.metadata_contract import main, violations


def _model(name: str, schema: str = "gold", **overrides: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "unique_id": f"model.p.{name}",
        "resource_type": "model",
        "schema": schema,
        "description": f"What {name} is.",
        "contract": {"enforced": True},
        "config": {"meta": {"owner": "someone"}},
        "meta": {"owner": "someone"},
    }
    return {**node, **overrides}


def _exposure(name: str, models: list[str], **overrides: Any) -> dict[str, Any]:
    exposure: dict[str, Any] = {
        "name": name,
        "url": f"/{name}.html",
        "owner": {"name": "someone", "email": None},
        "depends_on": {"nodes": [f"model.p.{model}" for model in models]},
    }
    return {**exposure, **overrides}


@pytest.fixture
def charts(tmp_path: Path) -> Path:
    """One board that reads one model, and one that extends a partial that reads two."""
    directory = tmp_path / "charts"
    directory.mkdir()
    (tmp_path / "partials").mkdir()
    (directory / "things.yml").write_text(
        "id: things\nqueries:\n  - sql: select * from {{ ref('fct_things') }}\n"
    )
    (directory / "wide.yml").write_text("extends: ../partials/shared.yml\nid: wide\n")
    (tmp_path / "partials" / "shared.yml").write_text(
        "queries:\n  - sql: select * from {{ ref(\"fct_things\") }}, {{ ref( 'agg_things' ) }}\n"
    )
    (directory / "_fragment.yml").write_text("x: {{ ref('not_a_board') }}\n")
    return directory


@pytest.fixture
def manifest() -> dict[str, Any]:
    models = [_model("fct_things"), _model("agg_things"), _model("stg_things", schema="silver")]
    exposures = [
        _exposure("things", ["fct_things"]),
        _exposure("wide", ["fct_things", "agg_things"]),
    ]
    return {
        "nodes": {model["unique_id"]: model for model in models},
        "exposures": {f"exposure.p.{e['name']}": e for e in exposures},
    }


def test_a_valid_manifest_and_boards_have_no_violations(
    manifest: dict[str, Any], charts: Path
) -> None:
    assert violations(manifest, charts) == []


@pytest.mark.parametrize("description", ["", "   ", None])
def test_a_gold_model_without_a_description_fails(
    manifest: dict[str, Any], charts: Path, description: Any
) -> None:
    manifest["nodes"]["model.p.fct_things"]["description"] = description
    assert violations(manifest, charts) == ["model fct_things: no description"]


@pytest.mark.parametrize("owner", [None, "", "  ", 7])
def test_a_gold_model_without_an_owner_fails(
    manifest: dict[str, Any], charts: Path, owner: Any
) -> None:
    node = manifest["nodes"]["model.p.agg_things"]
    node["config"]["meta"] = {"owner": owner}
    node["meta"] = {"owner": owner}
    assert violations(manifest, charts) == ["model agg_things: no meta.owner"]


def test_the_owner_is_read_from_the_nodes_meta_when_config_has_none(
    manifest: dict[str, Any], charts: Path
) -> None:
    manifest["nodes"]["model.p.fct_things"]["config"] = {}
    assert violations(manifest, charts) == []


@pytest.mark.parametrize("contract", [{"enforced": False}, {}, None])
def test_a_gold_model_without_an_enforced_contract_fails(
    manifest: dict[str, Any], charts: Path, contract: Any
) -> None:
    manifest["nodes"]["model.p.fct_things"]["contract"] = contract
    assert violations(manifest, charts) == ["model fct_things: the contract is not enforced"]


def test_a_model_outside_gold_needs_none_of_it(manifest: dict[str, Any], charts: Path) -> None:
    silver = manifest["nodes"]["model.p.stg_things"]
    silver.update(description="", contract={"enforced": False}, meta={}, config={})
    assert violations(manifest, charts) == []


def test_a_manifest_without_gold_models_fails(manifest: dict[str, Any], charts: Path) -> None:
    for model in manifest["nodes"].values():
        model["schema"] = "silver"
    assert violations(manifest, charts)[0] == "the manifest has no model in the gold schema"


def test_a_board_without_an_exposure_fails(manifest: dict[str, Any], charts: Path) -> None:
    del manifest["exposures"]["exposure.p.wide"]
    assert violations(manifest, charts) == ["board wide: no exposure with that name"]


def test_a_board_without_an_id_fails(manifest: dict[str, Any], charts: Path) -> None:
    (charts / "things.yml").write_text("queries:\n  - sql: select * from {{ ref('fct_things') }}\n")
    assert violations(manifest, charts) == ["board things.yml: no id"]


def test_an_exposure_without_a_url_or_an_owner_name_fails(
    manifest: dict[str, Any], charts: Path
) -> None:
    manifest["exposures"]["exposure.p.things"]["url"] = " "
    manifest["exposures"]["exposure.p.wide"]["owner"] = {"name": None, "email": "a@b.invalid"}
    assert violations(manifest, charts) == [
        "exposure things: no url",
        "exposure wide: no owner name",
    ]


def test_an_exposure_that_depends_on_other_models_than_the_board_reads_fails(
    manifest: dict[str, Any], charts: Path
) -> None:
    manifest["exposures"]["exposure.p.things"]["depends_on"]["nodes"].append("model.p.agg_things")
    assert violations(manifest, charts) == [
        "exposure things: depends on ['agg_things', 'fct_things'], "
        "but the board reads ['fct_things']"
    ]


def test_a_ref_only_in_the_extended_partial_must_be_in_the_exposure(
    manifest: dict[str, Any], charts: Path
) -> None:
    """`wide.yml` has no ref of its own: `agg_things` is read through its partial."""
    manifest["exposures"]["exposure.p.wide"]["depends_on"]["nodes"] = ["model.p.fct_things"]
    assert violations(manifest, charts) == [
        "exposure wide: depends on ['fct_things'], but the board reads ['agg_things', 'fct_things']"
    ]


def test_a_fragment_is_not_a_board(manifest: dict[str, Any], charts: Path) -> None:
    # `_fragment.yml` refs a model that does not exist and has no exposure.
    assert violations(manifest, charts) == []


def test_no_boards_fails(manifest: dict[str, Any], tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="no board found"):
        violations(manifest, empty)


def test_an_extends_loop_fails_by_name(manifest: dict[str, Any], charts: Path) -> None:
    (charts / "things.yml").write_text("id: things\nextends: things.yml\n")
    with pytest.raises(RuntimeError, match="loops back"):
        violations(manifest, charts)


def test_the_command_prints_one_line_per_violation_and_exits_1(
    manifest: dict[str, Any], charts: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = copy.deepcopy(manifest)
    broken["nodes"]["model.p.fct_things"]["description"] = ""
    del broken["exposures"]["exposure.p.wide"]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(broken))
    assert main([str(path), str(charts)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "model fct_things: no description",
        "board wide: no exposure with that name",
    ]


def test_the_command_exits_0_with_a_summary(
    manifest: dict[str, Any], charts: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert main([str(path), str(charts)]) == 0
    assert capsys.readouterr().out == "metadata contract met: 2 gold model(s), 2 board(s)\n"
