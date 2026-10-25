#!/usr/bin/env python3
"""Tests for tools/dispatch_prep.py — the ALREADY DECIDED block for
coder/fixer dispatches: contract slice verbatim, named-test EXISTS/
MISSING inventory, scope boundary, the hard-read gate block (allowed
reads + STOP rule), optional seams/ripple/traps sections (declared-only),
seam_verify status matrix, shared git/grep helpers, and CLI refusals.

All state lives in temp git repos (fixed author env); no network, no
live repo reads. Fictional STORY-101/102 ids throughout.
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import contract_lib as C
from tools import dispatch_prep as P

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prep"
STORY = "STORY-101"
GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.test",
           "GIT_COMMITTER_NAME": "test",
           "GIT_COMMITTER_EMAIL": "test@example.test"}

PARSER_PY = """\
\"\"\"Parser — lookup-table parsing.\"\"\"


class Parser:
    def parse_data(self, raw):
        return {"raw": raw}


def load(parser):
    return parser.parse_data(b"x")  # call site
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], env=GIT_ENV,
                          check=True, capture_output=True,
                          text=True).stdout.strip()


def make_repo(td: str) -> tuple[Path, str]:
    """Base tree: parser source, a test with ONE named test present, and
    the STORY-101 plan from the fixture. Returns (root, base sha)."""
    root = Path(td)
    _git(root, "init", "-q", "-b", "main")
    (root / "src" / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "stories" / STORY).mkdir(parents=True)
    (root / "src" / "app" / "parser.py").write_text(PARSER_PY,
                                                    encoding="utf-8")
    (root / "tests" / "test_parser.py").write_text(
        "import unittest\n\n\nclass ParserTests(unittest.TestCase):\n"
        "    def test_rejects_malformed_input(self):\n"
        "        pass\n", encoding="utf-8")
    (root / "stories" / STORY / "impl-plan.md").write_text(
        (FIXTURES / "impl-plan.md").read_text(encoding="utf-8"),
        encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=test", "-c", "user.email=test@example.test",
         "commit", "-q", "-m", "base")
    return root, _git(root, "rev-parse", "HEAD")


class PrepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tdc = tempfile.TemporaryDirectory()
        cls.root, cls.sha = make_repo(cls._tdc.name)
        cls.out = Path(cls._tdc.name) / "out" / "dispatch-prep.md"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.rc = P.main([STORY, "--root", str(cls.root),
                             "--sha", cls.sha, "--out", str(cls.out)])
        cls.text = cls.out.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tdc.cleanup()

    def test_exit_zero(self):
        self.assertEqual(self.rc, 0)

    def test_sections_present_verbatim(self):
        self.assertIn(f"# ALREADY DECIDED — {STORY}", self.text)
        self.assertIn("(dispatch_prep @ ", self.text)
        self.assertIn("_Deterministic seam recon. Do NOT re-verify "
                      "anything in this block._", self.text)
        for s in ("## 1. Contract slice (binding)",
                  "## 2. Named-test inventory",
                  "## 4. Scope boundary",
                  "## 5. Hard read gate"):
            self.assertIn(s, self.text)

    def test_contract_slice_is_the_fence_verbatim(self):
        block = self.text.split("## 1. Contract slice (binding)\n\n"
                                "```yaml\n")[1].split("\n```")[0]
        raw = C.CONTRACT_FENCE_RE.search(
            (FIXTURES / "impl-plan.md").read_text(encoding="utf-8"))
        self.assertIsNotNone(raw)
        self.assertEqual(block, raw.group(1).strip())

    def test_named_test_inventory_exists_missing(self):
        # EXISTS test keeps its siblings-intact action; MISSING is a
        # CREATE-class TDD target (both verbatim)
        self.assertIn("| AC-1 | `test_rejects_malformed_input` | `tests` | "
                      "EXISTS @ tests/test_parser.py:", self.text)
        self.assertIn("EXTEND that file — keep existing tests intact",
                      self.text)
        self.assertIn("| AC-2 | `test_round_trip_preserves_order` | "
                      "`tests` | MISSING | CREATE |", self.text)

    def test_scope_boundary_lists_manifest_paths(self):
        boundary = self.text.split("## 4. Scope boundary")[1] \
                          .split("## 5.")[0]
        self.assertIn("`src/app/cache.py`", boundary)
        self.assertIn("`src/app/parser.py`", boundary)
        self.assertIn("belongs to a LATER task — do not touch it.",
                      boundary)

    def test_hard_read_gate_block(self):
        # the frozen term: allowed-reads list + STOP rule, built from the
        # plan manifest + the named-test inventory
        gate = self.text.split("## 5. Hard read gate")[1]
        self.assertIn("Read ONLY these paths (plus files you CREATE):",
                      gate)
        self.assertIn("- `src/app/cache.py`", gate)
        self.assertIn("- `src/app/parser.py`", gate)
        self.assertIn("- `tests/test_parser.py`", gate)  # from inventory
        self.assertIn("STOP and report a SPECIFIC retrieval request", gate)
        self.assertIn("No repo exploration.", gate)

    def test_optional_sections_omitted_when_undeclared(self):
        for s in ("Ripple call sites", "Known traps", "## 3. Seams"):
            self.assertNotIn(s, self.text)

    def test_trailing_newline_and_lf(self):
        raw = self.out.read_bytes()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"\r", raw)


class DeclaredSectionsTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)
        self.out = Path(self._td.name) / "dispatch-prep.md"

    def tearDown(self):
        self._td.cleanup()

    def _run(self, *extra: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha,
                         "--out", str(self.out), *extra])
        self.assertEqual(rc, 0)
        return self.out.read_text(encoding="utf-8")

    def test_ripple_section_only_with_declared_patterns(self):
        text = self._run("--ripple", "{name}")
        self.assertIn("## 6. Ripple call sites (design constraint)", text)
        # parser.py stem is `parser`; the call site line is located
        ripple = text.split("## 6. Ripple call sites")[1]
        self.assertIn("`parser`", ripple)
        self.assertIn("src/app/parser.py:", ripple)

    def test_traps_section_inlines_bullets_only(self):
        text = self._run("--traps", str(FIXTURES / "traps.md"))
        self.assertIn("## 7. Known traps (verified lessons — do not "
                      "re-derive these)", text)
        traps = text.split("## 7. Known traps")[1]
        self.assertIn("Seed the cache fixture with an empty dict", traps)
        self.assertNotIn("must NOT be inlined", traps)

    def test_deterministic_output(self):
        a = self._run("--traps", str(FIXTURES / "traps.md"))
        b = self._run("--traps", str(FIXTURES / "traps.md"))
        self.assertEqual(a, b)


class SeamVerifyTests(unittest.TestCase):
    """seam_verify()/seam_check(): status matrix on a declared `seams`
    extension (not part of the frozen v1 schema — section renders only
    when the plan declares it)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)
        self.contract = C.extract_contract(
            (FIXTURES / "impl-plan-seams.md").read_text(encoding="utf-8"))

    def tearDown(self):
        self._td.cleanup()

    def test_declared_seams_render_section(self):
        plan = self.root / "stories" / STORY / "impl-plan.md"
        plan.write_text((FIXTURES / "impl-plan-seams.md").read_text(
            encoding="utf-8"), encoding="utf-8")
        out = Path(self._td.name) / "seams.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha,
                         "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("## 3. Seams (exact signatures — make ZERO API-shape "
                      "decisions)", text)
        self.assertIn("- `Parser.parse_data` → `parse_data(self, raw) -> "
                      "dict` (EXTEND `src/app/parser.py`)", text)
        self.assertIn("- `CacheLoader` → `class CacheLoader` (CREATE "
                      "`src/app/cache.py`)", text)
        self.assertFalse([l for l in text.splitlines()
                          if l.startswith("- FAIL:")])

    def test_ops_match_contract_creates(self):
        creates, _ = C.contract_paths(self.contract)
        for r in P.seam_verify(self.root, self.contract, self.sha):
            expected = "CREATE" if r["file"] in creates else "EXTEND"
            self.assertEqual(r["op"], expected, f"seam {r['symbol']}")

    def test_extend_seams_carry_evidence(self):
        rows = P.seam_verify(self.root, self.contract, self.sha)
        extend = [r for r in rows if r["op"] == "EXTEND"]
        self.assertTrue(extend)
        for r in extend:
            self.assertEqual(r["status"], "OK")
            self.assertTrue(r["evidence"], f"{r['symbol']} has no evidence")
            self.assertRegex(r["evidence"][0],
                             r"^[\w./-]+\.py:\d+ — ")

    def test_extend_bogus_member_fails(self):
        contract = {"files": [], "seams": [{
            "symbol": "Parser.BogusMember",
            "signature": "def BogusMember()",
            "file": "src/app/parser.py"}]}
        rows = P.seam_verify(self.root, contract, self.sha)
        self.assertEqual(rows[0]["status"], "FAIL")
        self.assertIn("does not resolve", rows[0]["detail"])
        rendered = P.seam_check(self.root, contract, self.sha)
        self.assertTrue(rendered[0].startswith("- FAIL: seam "))
        self.assertIn("STOP and report", rendered[0])

    def test_extend_nonexistent_file_fails(self):
        contract = {"files": [], "seams": [{
            "symbol": "Nothing.DoWork",
            "signature": "def DoWork()",
            "file": "src/app/does_not_exist.py"}]}
        rows = P.seam_verify(self.root, contract, self.sha)
        self.assertEqual(rows[0]["status"], "FAIL")
        self.assertIn("does not exist", rows[0]["detail"])

    def test_create_seam_with_file_already_in_tree_fails(self):
        contract = {"files": [{"path": "src/app/parser.py", "op": "CREATE"}],
                    "seams": [{"symbol": "Parser.parse_data",
                               "signature": "def parse_data()",
                               "file": "src/app/parser.py"}]}
        rows = P.seam_verify(self.root, contract, self.sha)
        self.assertEqual(rows[0]["status"], "FAIL")
        self.assertIn("already exists", rows[0]["detail"])

    def test_create_seam_absent_file_ok(self):
        rows = P.seam_verify(self.root, self.contract, self.sha)
        create = [r for r in rows if r["op"] == "CREATE"]
        self.assertEqual(len(create), 1)
        self.assertEqual(create[0]["status"], "OK")
        self.assertIn("CREATE confirmed", create[0]["detail"])

    def test_create_seam_symbol_collision_warns(self):
        # Parser already resolves tree-wide — a CREATE seam for the same
        # symbol must surface as WARN, not OK.
        contract = {"files": [{"path": "src/app/other.py", "op": "CREATE"}],
                    "seams": [{"symbol": "Parser.parse_data",
                               "signature": "def parse_data()",
                               "file": "src/app/other.py"}]}
        rows = P.seam_verify(self.root, contract, self.sha)
        self.assertEqual(rows[0]["status"], "WARN")
        self.assertTrue(rows[0]["evidence"])
        rendered = P.seam_check(self.root, contract, self.sha)
        self.assertFalse([l for l in rendered if l.startswith("- FAIL:")])
        self.assertTrue(any(l.startswith("- WARNING: seam")
                            for l in rendered))


class CliErrorTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_missing_plan_exits_two(self):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = P.main(["STORY-102", "--root", str(self.root),
                         "--sha", self.sha])
        self.assertEqual(rc, 2)

    def test_unparseable_contract_exits_two(self):
        plan = self.root / "stories" / STORY / "impl-plan.md"
        plan.write_text("# plan without contract\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha])
        self.assertEqual(rc, 2)

    def test_unreadable_sha_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root),
                         "--sha", "deadbeefdeadbeef"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
