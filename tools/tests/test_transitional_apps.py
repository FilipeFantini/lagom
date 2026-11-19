"""While the repository layout moves, the Applications are declared twice.

Root's path moves from platform/infra/apps to infra/apps in one change and the
directories move in the next (see the comment in infra/apps/root.yaml). Between
the two, whichever copy root reads must give the same cluster, so the copies
may not drift. This file is deleted with the old directory.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_the_two_copies_of_the_applications_are_identical() -> None:
    old = {p.name: p.read_bytes() for p in (REPO / "platform/infra/apps").glob("*.y*ml")}
    new = {p.name: p.read_bytes() for p in (REPO / "infra/apps").glob("*.y*ml")}
    assert old, "no Application found in platform/infra/apps"
    assert old == new
