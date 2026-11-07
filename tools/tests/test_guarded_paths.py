import io
from pathlib import Path

import pytest

import guarded_paths
from guarded_paths import CodeownersError, guarded, parse

REPO = Path(__file__).resolve().parents[2]

SAMPLE = """\
# comment

/platform/infra/        @owner
/AGENTS.md              @owner
pyproject.toml          @owner
.sqlfluff               @owner
"""


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("platform/infra/apps/root.yaml", True),
        ("platform/infra", True),
        ("platform/infrastructure/x.yaml", False),
        ("platform/warehouse/models/x.sql", False),
        ("AGENTS.md", True),
        ("docs/AGENTS.md", False),
        ("pyproject.toml", True),
        ("services/usgs/pyproject.toml", True),
        ("services/usgs/pyproject.toml.bak", False),
        ("platform/warehouse/models/.sqlfluff", True),
        ("README.md", False),
    ],
)
def test_matching(path: str, expected: bool) -> None:
    assert bool(guarded([path], parse(SAMPLE))) is expected


@pytest.mark.parametrize(
    "line",
    [
        "/docs/adr/",  # no owner: would remove ownership
        "/docs/adr/ # todo",  # a trailing comment is not an owner
        "*.yaml @owner",
        "/docs/**/x.md @owner",
        "!/AGENTS.md @owner",
        "docs/adr @owner",  # slash in the middle without a leading slash
        "/ @owner",
    ],
)
def test_refuses_what_it_cannot_read(line: str) -> None:
    with pytest.raises(CodeownersError):
        parse(SAMPLE + line + "\n")


def test_refuses_an_empty_file() -> None:
    with pytest.raises(CodeownersError):
        parse("# nothing\n")


def test_the_repository_codeowners_is_readable() -> None:
    patterns = parse((REPO / ".github" / "CODEOWNERS").read_text(encoding="utf-8"))
    assert guarded(["platform/infra/apps/root.yaml", ".github/workflows/ci.yml"], patterns)
    assert not guarded(["services/usgs/ingestion/pipeline.py", "README.md"], patterns)


def run(monkeypatch: pytest.MonkeyPatch, stdin: str, codeowners: Path) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return guarded_paths.main(["--codeowners", str(codeowners)])


def test_main_prints_the_output_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    codeowners = tmp_path / "CODEOWNERS"
    codeowners.write_text(SAMPLE)

    assert run(monkeypatch, "README.md\nplatform/infra/x.yaml\n", codeowners) == 0
    out, err = capsys.readouterr()
    assert out == "guarded=true\n"
    assert "guarded: platform/infra/x.yaml" in err

    assert run(monkeypatch, "README.md\n", codeowners) == 0
    assert capsys.readouterr().out == "guarded=false\n"


def test_main_fails_without_paths_or_codeowners(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    codeowners = tmp_path / "CODEOWNERS"
    codeowners.write_text(SAMPLE)
    assert run(monkeypatch, "\n", codeowners) == 2
    assert run(monkeypatch, "README.md\n", tmp_path / "missing") == 2
    assert capsys.readouterr().out == ""
