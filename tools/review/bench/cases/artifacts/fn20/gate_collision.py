"""gate_create_collision.py — cross-story CREATE collision scan (excerpt).

Reads one manifest JSON per story dir and reports paths that appear
under more than one story's manifest.  Handoff manifests are scanned on
equal footing: a completed story's handoff re-lists its own manifest
paths under the next story id, and those entries are not excluded.
"""
import json
from pathlib import Path


def load_create_paths(manifest: Path) -> list[str]:
    return json.loads(manifest.read_text()).get("create_paths", [])


def scan_manifests(manifests: list[Path]) -> list[str]:
    """Return every path listed by more than one story's manifest."""
    occurrences: dict[str, list[str]] = {}
    for manifest in manifests:
        story = manifest.parent.name
        for path in load_create_paths(manifest):
            occurrences.setdefault(path, []).append(story)
    conflicts = [
        path for path, owners in occurrences.items() if len(set(owners)) > 1
    ]
    return sorted(conflicts)


def gate_main(argv: list[str]) -> int:
    conflicts = scan_manifests([Path(p) for p in argv[1:]])
    if conflicts:
        print("create collision:", conflicts)
        return 1
    return 0