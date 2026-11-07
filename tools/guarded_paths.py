"""Guarded paths: does a change touch a path listed in .github/CODEOWNERS?

The `guardrail approval` workflow (ADR-0035) runs this on every pull request,
from main, to decide whether the operator's approval is required. It reads
the changed paths, one per line, and prints `guarded=true` or
`guarded=false` on stdout, for $GITHUB_OUTPUT; the guarded paths it found go
to stderr.

CODEOWNERS is GitHub's format; this script reads the subset the file uses,
and refuses the rest rather than guess:

- `/dir/` — anchored at the repository root; matches everything under `dir/`.
- `/path` — anchored; matches the file `path` and, if it is a directory,
  everything under it.
- `name` (no slash) — matches a file or directory called `name` at any depth.

Every line must name at least one owner. In CODEOWNERS a line without owners
removes ownership for what it matches, which would make "guarded if any
pattern matches" wrong; a pattern with a wildcard, a negation, an escape, or
a slash in the middle would need matching rules this script does not
implement. Either fails the run (exit 2), so a CODEOWNERS edit that this
script cannot read stops the gate instead of opening it.

What it does not check:
- Whether CODEOWNERS lists every path that should be guarded: it reads the
  list, it does not judge it.
- Mode changes (an executable bit): it sees paths, not file modes.

Run from the repository root:

    git diff --name-only origin/main... | python3 tools/guarded_paths.py

Standard library only, so the workflow runs it without installing anything.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

UNSUPPORTED = set("*?[]!\\")


class CodeownersError(ValueError):
    """A CODEOWNERS line this script cannot read safely."""


@dataclass(frozen=True)
class Pattern:
    text: str
    anchored: bool
    parts: tuple[str, ...]

    def matches(self, path: str) -> bool:
        path_parts = tuple(p for p in path.split("/") if p)
        if self.anchored:
            return path_parts[: len(self.parts)] == self.parts
        return self.parts[0] in path_parts


def parse_line(line: str, number: int) -> Pattern | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    fields = stripped.split()
    text, owners = fields[0], fields[1:]
    where = f"CODEOWNERS line {number} ({text!r})"
    if not owners:
        raise CodeownersError(f"{where}: no owner; it would remove ownership")
    if UNSUPPORTED & set(text):
        raise CodeownersError(f"{where}: wildcards, negations and escapes are not supported")
    anchored = text.startswith("/")
    parts = tuple(p for p in text.strip("/").split("/") if p)
    if not parts:
        raise CodeownersError(f"{where}: matches the whole repository")
    if not anchored and len(parts) > 1:
        raise CodeownersError(f"{where}: a slash in the middle needs a leading slash here")
    return Pattern(text=text, anchored=anchored, parts=parts)


def parse(text: str) -> list[Pattern]:
    patterns = [
        p for n, line in enumerate(text.splitlines(), start=1) if (p := parse_line(line, n))
    ]
    if not patterns:
        raise CodeownersError("CODEOWNERS has no pattern: nothing would be guarded")
    return patterns


def guarded(paths: list[str], patterns: list[Pattern]) -> list[str]:
    return [path for path in paths if any(p.matches(path) for p in patterns)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--codeowners",
        type=Path,
        default=Path(".github/CODEOWNERS"),
        help="the CODEOWNERS file to read (default: .github/CODEOWNERS)",
    )
    args = parser.parse_args(argv)

    try:
        patterns = parse(args.codeowners.read_text(encoding="utf-8"))
    except (OSError, CodeownersError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    paths = [line.strip() for line in sys.stdin if line.strip()]
    if not paths:
        print("error: no changed paths on stdin; a change touches at least one", file=sys.stderr)
        return 2

    found = guarded(paths, patterns)
    for path in found:
        print(f"guarded: {path}", file=sys.stderr)
    print(f"guarded={'true' if found else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
