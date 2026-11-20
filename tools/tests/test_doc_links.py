import subprocess
from pathlib import Path

import pytest

from doc_links import anchors, check_file, links, maintained_markdown, slug


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Resource budget", "resource-budget"),
        ("Postgres — CloudNativePG", "postgres--cloudnativepg"),
        ("16.5 Resource budget as a quality gate", "165-resource-budget-as-a-quality-gate"),
        ("The `dashboards` job", "the-dashboards-job"),
        ("See [Data](data.md) here", "see-data-here"),
        ("Deliberately absent, and when it enters", "deliberately-absent-and-when-it-enters"),
        ("_italic_ word", "italic-word"),
        ("**bold** word", "bold-word"),
        ("The snake_case name", "the-snake_case-name"),
    ],
)
def test_slug_follows_github(heading: str, expected: str) -> None:
    assert slug(heading) == expected


def test_anchors_number_repeated_headings_and_read_html_ids() -> None:
    text = '# Notes\n## Notes\n<a id="custom"></a>\n```\n# not a heading\n```\n'
    assert anchors(text) == {"notes", "notes-1", "custom"}


def test_fence_closes_only_on_its_own_kind() -> None:
    text = "````md\n```\n# fake\n```\n````\n[z](nope.md)\n~~~\n[in](code.md)\n~~~\n"
    assert [target for _, target in links(text)] == ["nope.md"]
    assert anchors(text) == set()


def test_links_skip_code() -> None:
    text = "[a](one.md) `[b](two.md)`\n```\n[c](three.md)\n```\n[d](<four five.md>)\n"
    assert [target for _, target in links(text)] == ["one.md", "four five.md"]


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_check_file_reports_missing_file_and_anchor(tmp_path: Path) -> None:
    write(tmp_path, "docs/data.md", "# Data\n## Add a source\n")
    page = write(
        tmp_path,
        "docs/page.md",
        "# Page\n"
        "[ok](data.md#add-a-source)\n"
        "[ok, self](#page)\n"
        "[ok, web](https://example.com/x.md#nowhere)\n"
        "[ok, dir](.)\n"
        "[gone](roadmap.md)\n"
        "[bad anchor](data.md#trigger-table)\n",
    )
    problems = check_file(tmp_path, page, {})
    assert problems == [
        "docs/page.md:6: roadmap.md: no such file",
        "docs/page.md:7: data.md#trigger-table: no such anchor",
    ]


def test_maintained_markdown_leaves_out_adr_bodies(tmp_path: Path) -> None:
    for name in ("README.md", "docs/adr/README.md", "docs/adr/0051-handoff.md", "notes.txt"):
        write(tmp_path, name, "# x\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    names = [path.relative_to(tmp_path).as_posix() for path in maintained_markdown(tmp_path)]
    assert names == ["README.md", "docs/adr/README.md"]
