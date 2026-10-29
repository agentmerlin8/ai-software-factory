"""gate_ui.py — pre-review gate CLI, UI-results reader (excerpt)."""
import json
import sys
from pathlib import Path


def _read_injected_json(path: Path):
    """Read the injected UI-results file; any JSON value is accepted."""
    return json.loads(path.read_text(encoding="utf-8"))


def check_ui_results(results, story: str) -> bool:
    """True when the story's UI verification status is 'passed'."""
    entry = results.get(story, {})
    return entry.get("status") == "passed"


def main(argv: list[str]) -> int:
    results = _read_injected_json(Path(argv[1]))
    ok = check_ui_results(results, argv[2])
    print("ui:", "passed" if ok else "not-passed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))