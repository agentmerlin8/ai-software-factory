#!/usr/bin/env python3
"""Tests for tools/plan_prep.py — the planner verify-pack: mechanically
extracted "ALREADY VERIFIED" facts at path:line granularity plus
explicit negatives for misses, from the story text sources against a
temp git repo at a pinned SHA. Deterministic: identical inputs →
byte-identical output. CLI refusals: missing story / unreadable sha → 2.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import plan_prep as P

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prep"
STORY = "STORY-101"
GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.test",
           "GIT_COMMITTER_NAME": "test",
           "GIT_COMMITTER_EMAIL": "test@example.test"}

CONFIG_PY = """\
# NON-DATA-PRESERVING cache reset by design.
CACHE_TTL_DEFAULT = 300
cache_ttl = CACHE_TTL_DEFAULT
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], env=GIT_ENV,
                          check=True, capture_output=True,
                          text=True).stdout.strip()


def make_repo(td: str, *, with_digest: bool = True) -> tuple[Path, str]:
    root = Path(td)
    _git(root, "init", "-q", "-b", "main")
    (root / "src" / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "stories" / STORY).mkdir(parents=True)
    (root / "src" / "app" / "parser.py").write_text(
        "\"\"\"Parser.\"\"\"\n\n\nclass Parser:\n"
        "    def parse_data(self, raw):\n        return {'raw': raw}\n\n\n"
        "class Loader:\n"
        "    def LoadEntries(self, raw):\n        return raw\n\n\n"
        "def run(loader):\n    loader.LoadEntries(b'y')\n",
        encoding="utf-8")
    (root / "src" / "app" / "config.py").write_text(CONFIG_PY,
                                                    encoding="utf-8")
    (root / "tests" / "test_parser.py").write_text(
        "import unittest\n\n\nclass ParserTests(unittest.TestCase):\n"
        "    def test_rejects_malformed_input(self):\n        pass\n",
        encoding="utf-8")
    (root / "stories" / STORY / "impl-plan.md").write_text(
        (FIXTURES / "impl-plan.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    (root / "stories" / f"{STORY}.md").write_text(
        (FIXTURES / "story.md").read_text(encoding="utf-8"), encoding="utf-8")
    if with_digest:
        (root / "stories" / STORY / "context-digest.md").write_text(
            (FIXTURES / "context-digest.md").read_text(encoding="utf-8"),
            encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=test", "-c", "user.email=test@example.test",
         "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


class VerifyPackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tdc = tempfile.TemporaryDirectory()
        cls.root, cls.sha = make_repo(cls._tdc.name)
        cls.out = Path(cls._tdc.name) / "out" / "plan-prep.md"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.rc = P.main([STORY, "--root", str(cls.root),
                             "--sha", cls.sha, "--out", str(cls.out),
                             "--tree-glob", "src/", "--tree-glob", "tests/",
                             "--test-glob", "tests/",
                             "--vocab-glob", "src/"])
        cls.text = cls.out.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tdc.cleanup()

    def test_exit_zero(self):
        self.assertEqual(self.rc, 0)

    def test_header_and_preamble(self):
        self.assertIn(f"# ALREADY VERIFIED — {STORY} @ {self.sha[:12]} "
                      "(plan_prep)", self.text)
        self.assertIn("Do NOT re-verify", self.text)
        self.assertIn("Anything NOT in this block must be resolved from "
                      "the tree", self.text)

    def test_all_sections_present(self):
        for s in ("## 1. Vocabulary facts", "## 2. Symbol resolve",
                  "## 3. Call-site facts", "## 4. Named-test inventory",
                  "## 5. File-path facts", "## 6. Precedent extracts"):
            self.assertIn(s, self.text)

    def test_bounded_line_budget(self):
        self.assertLessEqual(len(self.text.splitlines()), 215)
        self.assertIn("candidate caps:", self.text)

    def test_vocab_facts_path_line_granularity(self):
        # cache_ttl is declared in the story AND present in src/app/config.py
        self.assertIn("- `cache_ttl` src/app/config.py:", self.text)

    def test_explicit_negative_for_vocab_miss(self):
        self.assertIn("`missing_vocab_token` — ABSENT in the declared "
                      "vocab globs at base", self.text)

    def test_symbol_resolve_found_and_not_found(self):
        self.assertIn("- Parser FOUND — src/app/parser.py:", self.text)
        self.assertIn("- CacheWarming NOT FOUND at base (CREATE-class or "
                      "misnamed — verify)", self.text)

    def test_one_production_call_site(self):
        self.assertIn("LoadEntries at src/app/parser.py:", self.text)
        self.assertIn("as the ONE production call site", self.text)

    def test_named_test_inventory(self):
        self.assertIn("- `test_rejects_malformed_input` — EXISTS @ "
                      "tests/test_parser.py:", self.text)
        self.assertIn("- `test_round_trip_missing` — MISSING "
                      "(plan/CREATE-class test)", self.text)

    def test_file_path_facts_exist_absent(self):
        self.assertIn("- src/app/parser.py — EXISTS at base", self.text)
        self.assertIn("- src/app/cache.py — ABSENT at base (CREATE-vs-"
                      "EXTEND determinant)", self.text)

    def test_marker_precedent(self):
        self.assertIn("- NON-DATA-PRESERVING — ", self.text)

    def test_deterministic_output(self):
        out2 = Path(self._tdc.name) / "out2.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha,
                         "--out", str(out2),
                         "--tree-glob", "src/", "--tree-glob", "tests/",
                         "--test-glob", "tests/", "--vocab-glob", "src/"])
        self.assertEqual(rc, 0)
        self.assertEqual(out2.read_text(encoding="utf-8"), self.text)

    def test_trailing_newline_and_lf(self):
        raw = self.out.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)


class UndeclaredVocabOmitsSectionTests(unittest.TestCase):
    """Parameterized sections: no --vocab-glob declaration → section 1
    omitted entirely (genericization rule)."""

    def test_section_omitted_without_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            root, sha = make_repo(td)
            out = Path(td) / "pack.md"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = P.main([STORY, "--root", str(root), "--sha", sha,
                             "--out", str(out), "--tree-glob", "src/"])
            self.assertEqual(rc, 0)
            text = out.read_text(encoding="utf-8")
            self.assertNotIn("## 1. Vocabulary facts", text)
            self.assertIn("## 2. Symbol resolve", text)


class DraftSmokeTests(unittest.TestCase):
    def test_spec_without_digest_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root, sha = make_repo(td, with_digest=False)
            out = Path(td) / "pack.md"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = P.main([STORY, "--root", str(root), "--sha", sha,
                             "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn(f"# ALREADY VERIFIED — {STORY}",
                          out.read_text(encoding="utf-8"))


class CliErrorTests(unittest.TestCase):
    def test_missing_story_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            root, sha = make_repo(td)
            with contextlib.redirect_stderr(io.StringIO()):
                rc = P.main(["STORY-999", "--root", str(root),
                             "--sha", sha])
            self.assertEqual(rc, 2)

    def test_unreadable_sha_exits_two(self):
        with tempfile.TemporaryDirectory() as td:
            root, _sha = make_repo(td)
            out = Path(td) / "pack.md"
            with contextlib.redirect_stderr(io.StringIO()):
                rc = P.main([STORY, "--root", str(root),
                             "--sha", "deadbeefdeadbeef",
                             "--out", str(out)])
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
