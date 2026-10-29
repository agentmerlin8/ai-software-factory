"""guard_change.py — containment guard with separator normalization."""

from pathlib import Path


def is_under_root(candidate: str, root: str) -> bool:
    """True when `candidate` is the root or a path beneath it.

    Both sides are resolved and the root boundary is compared with a
    trailing separator so a sibling merely sharing the prefix fails.
    """
    root_path = Path(root).resolve()
    cand = Path(candidate).resolve()
    if cand == root_path:
        return True
    return str(cand).startswith(str(root_path) + "/")


def test_boundary_cases():
    assert is_under_root("config/live", "config/live")
    assert is_under_root("config/live/ledger.json", "config/live")
    assert not is_under_root("config/live-archive/ledger.json", "config/live")
    assert not is_under_root("config/other", "config/live")
    assert not is_under_root("config/live/../secret", "config/live")