#!/usr/bin/env python3
"""tools/tests/test_make_context_bundle.py — line-counting + assembly
tests for tools/make_context_bundle.py (kit conventions: stdlib only,
throwaway git repos, deterministic assertions).

Covers the F6 fix: an N-line source reports N in the manifest and the
per-source header — with AND without a final newline — and an empty
source reports 0 (the old `count("\\n") + 1` over-counted both). Plus a
bundle-assembles smoke check (rc 0, manifest + sha256 present).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_context_bundle as MCB  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "make_context_bundle.py"


def make_repo(td: str, files: dict[str, str]) -> Path:
    """Throwaway git repo with `files` committed; returns its root."""
    root = Path(td)
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(root), *args], check=True,
                       capture_output=True)
    return root


def run_bundle(root: Path, out: Path,
               sources: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "STORY-NNN", "--root", str(root),
         "--sha", "HEAD", *sources, "-o", str(out)],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class LineCountingTests(unittest.TestCase):
    def test_n_lines_with_final_newline_reports_n(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "one\ntwo\nthree\n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| `src/a.py` |", text)
            self.assertIn("| 3 |", text)              # manifest cell
            self.assertIn("(3 lines, pinned at", text)  # source header

    def test_n_lines_without_final_newline_reports_n(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "one\ntwo\nthree"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/a.py"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| 3 |", text)
            self.assertIn("(3 lines, pinned at", text)

    def test_empty_file_reports_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/empty.txt": ""})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/empty.txt"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("| `src/empty.txt` | 0 | 0 |", text)
            self.assertIn("(0 lines, pinned at", text)

    def test_splitlines_is_the_counting_rule(self):
        """Direct unit check of the counting expression's semantics."""
        self.assertEqual(len("a\nb\nc\n".splitlines()), 3)
        self.assertEqual(len("a\nb\nc".splitlines()), 3)
        self.assertEqual(len("".splitlines()), 0)


class BundleAssemblyTests(unittest.TestCase):
    def test_bundle_assembles_rc0(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1\n",
                                  "docs/notes.md": "# notes\n"})
            out = Path(td) / "stories" / "STORY-NNN" / "context-bundle.md"
            rc, stdout, stderr = run_bundle(
                root, out, ["src/a.py", "docs/notes.md"])
            self.assertEqual(rc, 0, stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("# Context Bundle — STORY-NNN", text)
            self.assertIn("## Bundle manifest", text)
            self.assertIn("bundle sha256:", text)
            self.assertIn("## Source: `src/a.py`", text)
            self.assertIn("## Source: `docs/notes.md`", text)
            self.assertIn("wrote", stdout)
            # totals line uses the same splitlines rule
            total = len(text.splitlines())
            self.assertIn(f"total lines: {total}", stdout)

    def test_missing_source_refuses_rc2(self):
        with tempfile.TemporaryDirectory() as td:
            root = make_repo(td, {"src/a.py": "x = 1\n"})
            out = Path(td) / "bundle.md"
            rc, _stdout, stderr = run_bundle(root, out, ["src/ghost.py"])
            self.assertEqual(rc, 2)
            self.assertIn("not present at the pinned SHA", stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
