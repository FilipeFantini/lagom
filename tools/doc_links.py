"""Fail on a broken relative link or anchor in the maintained Markdown.

Reads every Markdown file Git tracks, except the bodies of numbered decision
records (docs/adr/NNNN-*.md): those keep their history, links included, and a
link that pointed at a moved document stays as it was (ADR-0054, "Risks").
The ADR index, docs/adr/README.md, is maintained and is checked.

For each inline link `[text](target)` outside code:
- a target with a scheme (`https:`, `mailto:`) is not checked: this is not a
  network check;
- a relative path must exist in the working tree;
- a fragment (`#section`) on a Markdown file, or alone, must match a heading
  of that file, using GitHub's anchor rules, or an explicit `<a id="...">`.

What it does not check: reference-style links (`[text][ref]`), paths written
as code, links in HTML elements other than `<a id>`, and anchors on files that
are not Markdown.
"""

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

ADR_BODY = re.compile(r"^docs/adr/\d{4}-[^/]+\.md$")
FENCE = re.compile(r"^\s{0,3}(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"`+[^`]*`+")
# [text](target "optional title"); target has no spaces, or is in <...>.
LINK = re.compile(r"\[[^\]]*\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
HTML_ID = re.compile(r"<a\s+(?:name|id)=\"([^\"]+)\"")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # a link keeps its text
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Lines outside fenced code blocks, numbered from 1."""
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield number, line


def anchors(text: str) -> set[str]:
    """Every anchor a Markdown file defines: headings (with GitHub's -1, -2 suffixes) and <a id>."""
    found: set[str] = set()
    seen: dict[str, int] = {}
    for _, line in prose_lines(text):
        found.update(HTML_ID.findall(line))
        match = HEADING.match(line)
        if match:
            base = slug(match.group(1))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f"{base}-{count}")
    return found


def links(text: str) -> Iterator[tuple[int, str]]:
    """Inline link targets outside code, with their line numbers."""
    for number, line in prose_lines(text):
        for match in LINK.finditer(INLINE_CODE.sub("", line)):
            yield number, match.group(1).strip("<>")


def check_file(root: Path, path: Path, cache: dict[Path, set[str]]) -> list[str]:
    """The broken links of one file, as `path:line: target: reason`."""
    problems: list[str] = []
    rel = path.relative_to(root)
    for number, target in links(path.read_text(encoding="utf-8")):
        if SCHEME.match(target) or target.startswith("//"):
            continue
        file_part, _, fragment = target.partition("#")
        if file_part.startswith("/"):
            resolved = root / file_part.lstrip("/")
        else:
            resolved = path.parent / file_part if file_part else path
        resolved = resolved.resolve()
        if not resolved.exists():
            problems.append(f"{rel}:{number}: {target}: no such file")
            continue
        if fragment and resolved.suffix == ".md" and resolved.is_file():
            if resolved not in cache:
                cache[resolved] = anchors(resolved.read_text(encoding="utf-8"))
            if fragment not in cache[resolved]:
                problems.append(f"{rel}:{number}: {target}: no such anchor")
    return problems


def maintained_markdown(root: Path) -> list[Path]:
    """Tracked Markdown files, minus the bodies of numbered ADRs."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout.decode()
    names = [name for name in listed.split("\0") if name and not ADR_BODY.match(name)]
    return [root / name for name in sorted(names)]


def main() -> int:
    root = Path(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, check=True, text=True
        ).stdout.strip()
    )
    files = maintained_markdown(root)
    if not files:
        print("no Markdown file found: is this the repository?", file=sys.stderr)
        return 1
    cache: dict[Path, set[str]] = {}
    problems = [problem for path in files for problem in check_file(root, path, cache)]
    for problem in problems:
        print(problem)
    print(f"{len(files)} file(s) checked, {len(problems)} broken link(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
