"""Suite tests for tools/check_genericization.py — the F3 tripwire.

Each case runs the real CLI in a subprocess (temp tree, non-git → the
tree-walk branch) so exit codes and exact output lines are asserted
against the shipped tool, not a copy of its logic.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
TOOL = REPO / "tools" / "check_genericization.py"


def run_tool(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root)],
        capture_output=True, text=True)


class CheckGenericizationTests(unittest.TestCase):

    def test_clean_temp_tree_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                "def widget():\n    return 'generic text'\n",
                encoding="utf-8")
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("genericization: 0 hit(s) across 0 tracked "
                          "file(s)", proc.stdout)

    def test_seeded_hit_fails_with_exact_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.md").write_text(
                "line one\nthis mentions a Podcast pipeline\n",
                encoding="utf-8")
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn(
                "HIT notes.md:2: podcast — this mentions a Podcast "
                "pipeline", proc.stdout)
            self.assertIn("genericization: 1 hit(s) across 1 tracked "
                          "file(s)", proc.stdout)

    def test_bare_agentmerlin_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "deploy.sh").write_text(
                "ORG=https://dev.azure.com/AgentMerlin\n", encoding="utf-8")
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("HIT deploy.sh:1: agentmerlin —", proc.stdout)

    def test_agentmerlin_with_factory_identity_passes(self):
        """Carve-out 1: agentmerlin on a line that also carries the
        kit's own repo name is public identity, not a leak."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text(
                "git clone https://github.com/agentmerlin8-ops/"
                "ai-software-factory\n", encoding="utf-8")
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_non_utf8_file_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "blob.bin").write_bytes(b"\xff\xfe\x00\x81 not text")
            (root / "ok.txt").write_text("clean\n", encoding="utf-8")
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_root_flag_honored_and_validated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "leak.md").write_text("a campaign of episodes\n",
                                          encoding="utf-8")
            # --root points at the dirty tree → rc 1 with both tokens
            proc = run_tool(root)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("campaign", proc.stdout)
            self.assertIn("episode", proc.stdout)
            # default (no --root) scans the REAL repo, not the temp tree
            proc = subprocess.run([sys.executable, str(TOOL)],
                                  capture_output=True, text=True,
                                  cwd=str(root))
            self.assertNotIn("leak.md", proc.stdout)
        # bad --root → usage rc 2
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--root", "/nonexistent-s3t6"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage:", proc.stderr)

    def test_real_repo_root_is_clean(self):
        """Self-check: the kit tree at HEAD passes its own tripwire."""
        proc = subprocess.run([sys.executable, str(TOOL),
                               "--root", str(REPO)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         f"tree carries banned tokens:\n{proc.stdout}")
        self.assertIn("genericization: 0 hit(s) across 0 tracked file(s)",
                      proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
