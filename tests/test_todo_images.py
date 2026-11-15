"""Screenshots in todo-images/ belong to an item in TODO.md. When the item is finished and deleted, its image goes with
it, so these tests fail on an image no item links to and on a link to an image that is gone."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "todo-images"


def linked() -> set[str]:
    """The file names TODO.md shows from todo-images/."""
    text = (REPO / "TODO.md").read_text(encoding="utf-8")
    return set(re.findall(r"!\[[^\]]*\]\(todo-images/([^)\s]+)\)", text))


def stored() -> set[str]:
    return {path.name for path in IMAGES.iterdir() if path.is_file()} if IMAGES.is_dir() else set()


def test_every_image_is_used_by_an_item() -> None:
    orphans = sorted(stored() - linked())
    assert not orphans, f"delete these images or link them from TODO.md: {orphans}"


def test_every_linked_image_exists() -> None:
    missing = sorted(linked() - stored())
    assert not missing, f"TODO.md links to images that are not in todo-images/: {missing}"
