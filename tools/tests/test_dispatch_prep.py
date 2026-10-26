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


class GrepFailureTests(unittest.TestCase):
    """AB1 (round 27): grep exit 1 = NO MATCHES; any other grep failure
    is a named refusal BEFORE any pack write — an invalid ripple regex
    must never read as 'no call sites' with exit 0."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)
        self.out = Path(self._td.name) / "pack" / "dispatch-prep.md"

    def tearDown(self):
        self._td.cleanup()

    def _main(self, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha,
                         "--out", str(self.out), *extra])
        return rc, err.getvalue()

    def test_invalid_ripple_regex_refuses_without_writing(self):
        rc, err = self._main("--ripple", "[")
        self.assertEqual(rc, 2)
        self.assertIn("git grep failed for pattern '['", err)
        self.assertFalse(self.out.exists())

    def test_grep_tree_raises_on_invalid_regex(self):
        with self.assertRaises(P.GrepError):
            P.grep_tree(self.root, "[", self.sha)

    def test_grep_tree_no_match_is_empty_not_error(self):
        self.assertEqual(
            P.grep_tree(self.root, "def_no_such_symbol_zzz", self.sha), [])

    def test_no_match_ripple_still_writes_pack(self):
        rc, _ = self._main("--ripple", "no_such_call_site_zzz")
        self.assertEqual(rc, 0)
        text = self.out.read_text(encoding="utf-8")
        self.assertIn("## 6. Ripple call sites (design constraint)", text)
        self.assertIn("- none detected at this SHA", text)


PLAN_WIDGET = """\
# Impl plan — STORY-101 (widget)

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
tests:
  - ac: AC-1
    name: test_widget_refusal
    asserts: "named refusal + nothing written"
```
"""


class TestEvidenceTests(unittest.TestCase):
    """AB2 (round 27): existence evidence is a real test DEFINITION —
    the committed plan's OWN test-name declaration (with `project`
    omitted → whole-tree search) must never count as evidence."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, _ = make_repo(self._td.name)
        plan = self.root / "stories" / STORY / "impl-plan.md"
        plan.write_text(PLAN_WIDGET, encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=test",
             "-c", "user.email=test@example.test",
             "commit", "-q", "-m", "widget plan")
        self.sha = _git(self.root, "rev-parse", "HEAD")
        self.contract = C.extract_contract(PLAN_WIDGET)

    def tearDown(self):
        self._td.cleanup()

    def test_plan_declaration_alone_is_missing(self):
        # no `project` declared: whole-tree grep would hit the plan's own
        # `name: test_widget_refusal` line — that is NOT a definition
        inv = P.test_inventory(self.root, self.contract, self.sha)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["name"], "test_widget_refusal")
        self.assertEqual(inv[0]["status"], "MISSING")

    def test_real_definition_is_exists(self):
        (self.root / "tests" / "test_widget.py").write_text(
            "def test_widget_refusal():\n    pass\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=test",
             "-c", "user.email=test@example.test",
             "commit", "-q", "-m", "real test")
        sha = _git(self.root, "rev-parse", "HEAD")
        inv = P.test_inventory(self.root, self.contract, sha)
        self.assertEqual(inv[0]["status"], "EXISTS")
        self.assertTrue(inv[0]["at"][0].startswith("tests/test_widget.py:"))

    def test_explicit_project_glob_still_definition_only(self):
        # with `project: tests` the plan is outside the glob anyway —
        # a bare mention inside tests/ must still not count
        contract = {"files": [], "tests": [
            {"ac": "AC-1", "name": "test_widget_refusal", "project": "tests"}]}
        (self.root / "tests" / "notes.md").write_text(
            "we will add test_widget_refusal later\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "-c", "user.name=test",
             "-c", "user.email=test@example.test",
             "commit", "-q", "-m", "mention only")
        sha = _git(self.root, "rev-parse", "HEAD")
        inv = P.test_inventory(self.root, contract, sha)
        self.assertEqual(inv[0]["status"], "MISSING")


class DestConfinementTests(unittest.TestCase):
    """AB3 (round 27): the DEFAULT destination must resolve (following
    symlinks) strictly under <root>/stories before any mkdir/write —
    same rule as make_context_bundle (round 26)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)
        self.plan = self.root / "stories" / STORY / "impl-plan.md"

    def tearDown(self):
        self._td.cleanup()

    def _main(self, story_id: str, *extra: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            rc = P.main([story_id, "--root", str(self.root),
                         "--sha", self.sha, *extra])
        return rc, err.getvalue()

    def test_traversal_story_id_refused(self):
        rc, err = self._main("../../outside", "--plan", str(self.plan))
        self.assertEqual(rc, 2)
        self.assertIn("refusing story id", err)
        self.assertIn("escapes the stories directory", err)
        outside = Path(self._td.name) / "outside"
        self.assertFalse((outside / "dispatch-prep.md").exists())
        self.assertFalse(outside.exists())

    def test_symlinked_story_dir_refused(self):
        outside = Path(self._td.name) / "elsewhere"
        outside.mkdir()
        link = self.root / "stories" / "STORY-102"
        link.symlink_to(outside)
        (outside / "impl-plan.md").write_text(PLAN_WIDGET, encoding="utf-8")
        rc, err = self._main("STORY-102")
        self.assertEqual(rc, 2)
        self.assertIn("refusing story id", err)
        self.assertIn("escapes the stories directory", err)
        self.assertFalse((outside / "dispatch-prep.md").exists())

    def test_plain_canonical_default_writes(self):
        rc, _ = self._main(STORY)
        self.assertEqual(rc, 0)
        dest = self.root / "stories" / STORY / "dispatch-prep.md"
        self.assertTrue(dest.exists())
        self.assertIn("# ALREADY DECIDED", dest.read_text(encoding="utf-8"))

    def test_explicit_out_still_honored(self):
        out = Path(self._td.name) / "somewhere" / "prep.md"
        rc, _ = self._main(STORY, "--out", str(out))
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())


def _commit(root: Path, msg: str) -> str:
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=test", "-c", "user.email=test@example.test",
         "commit", "-q", "-m", msg)
    return _git(root, "rev-parse", "HEAD")


PLAN_DOTTED = """\
# Impl plan — STORY-101 (dotted project directory)

```contract.yaml
contract_version: 1
files:
  - path: src/app/widget.py
    op: CREATE
tests:
  - ac: AC-1
    name: test_only_in_v2
    project: tests/unit.v1
    asserts: "sibling project test is never evidence"
```
"""


class DottedProjectDirTests(unittest.TestCase):
    """AN1 (round 39): a project DIRECTORY with a dotted name
    (`tests/unit.v1`) is NOT a file — tree-vs-file is decided at the
    PINNED sha (`git cat-file -t`), never by the suffix heuristic, so
    the glob is never reduced to `tests/` (which would report the
    sibling project's tests as EXISTS for the wrong project and pull
    the sibling into the allowed reads)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, _ = make_repo(self._td.name)
        (self.root / "tests" / "unit.v1").mkdir()
        (self.root / "tests" / "unit.v1" / "test_a.py").write_text(
            "def test_only_in_v1():\n    pass\n", encoding="utf-8")
        (self.root / "tests" / "unit.v2").mkdir()
        (self.root / "tests" / "unit.v2" / "test_b.py").write_text(
            "def test_only_in_v2():\n    pass\n", encoding="utf-8")
        self.sha = _commit(self.root, "dotted project dirs")

    def tearDown(self):
        self._td.cleanup()

    def test_dotted_dir_glob_is_not_reduced(self):
        self.assertEqual(
            P._test_glob({"project": "tests/unit.v1"}, self.root, self.sha),
            ("tests/unit.v1/",))

    def test_sibling_test_is_not_evidence_wrong_project(self):
        # test_only_in_v2 lives ONLY in the sibling tests/unit.v2 —
        # declaring project tests/unit.v1 must report MISSING (the old
        # tests/ glob reported EXISTS at tests/unit.v2/test_b.py).
        contract = {"files": [], "tests": [
            {"ac": "AC-1", "name": "test_only_in_v2",
             "project": "tests/unit.v1"}]}
        inv = P.test_inventory(self.root, contract, self.sha)
        self.assertEqual(inv[0]["status"], "MISSING")
        self.assertEqual(inv[0]["at"], [])

    def test_right_scoped_exists_for_own_project(self):
        contract = {"files": [], "tests": [
            {"ac": "AC-1", "name": "test_only_in_v1",
             "project": "tests/unit.v1"}]}
        inv = P.test_inventory(self.root, contract, self.sha)
        self.assertEqual(inv[0]["status"], "EXISTS")
        self.assertTrue(inv[0]["at"][0].startswith(
            "tests/unit.v1/test_a.py:"))

    def test_sibling_never_enters_allowed_reads(self):
        (self.root / "stories" / STORY / "impl-plan.md").write_text(
            PLAN_DOTTED, encoding="utf-8")
        self.sha = _commit(self.root, "dotted plan")
        out = Path(self._td.name) / "prep.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = P.main([STORY, "--root", str(self.root), "--sha", self.sha,
                         "--out", str(out)])
        self.assertEqual(rc, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("| AC-1 | `test_only_in_v2` | `tests/unit.v1` | "
                      "MISSING | CREATE |", text)
        gate = text.split("## 5. Hard read gate")[1]
        self.assertNotIn("tests/unit.v2", gate)
        self.assertIn("- `src/app/widget.py`", gate)

    def test_genuine_project_file_still_reduces(self):
        (self.root / "proj").mkdir()
        (self.root / "proj" / "tests.proj").write_text(
            "a real project FILE\n", encoding="utf-8")
        sha = _commit(self.root, "project file")
        self.assertEqual(
            P._test_glob({"project": "proj/tests.proj"}, self.root, sha),
            ("proj/",))
        # plain directory project unchanged
        self.assertEqual(
            P._test_glob({"project": "tests"}, self.root, sha), ("tests/",))
        # path absent at the sha: treated as a directory (nothing to
        # reduce) — the grep then simply finds no hits
        self.assertEqual(
            P._test_glob({"project": "tests/unit.v9"}, self.root, sha),
            ("tests/unit.v9/",))


class QualifiedTestNameTests(unittest.TestCase):
    """AN2 (round 39): a qualified Python test name `Owner.member`
    resolves the MEMBER within its declared owner (definition-only
    evidence) — the old whole-name pattern reported an existing
    `ParserTests.test_rejects_malformed_input` as MISSING because no
    declaration keyword ever precedes the DOTTED form."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _inv(self, name: str, project: str = "tests") -> dict:
        contract = {"files": [], "tests": [
            {"ac": "AC-1", "name": name, "project": project}]}
        inv = P.test_inventory(self.root, contract, self.sha)
        self.assertEqual(len(inv), 1)
        return inv[0]

    def test_qualified_member_in_owner_exists(self):
        row = self._inv("ParserTests.test_rejects_malformed_input")
        self.assertEqual(row["status"], "EXISTS")
        self.assertTrue(row["at"][0].startswith("tests/test_parser.py:"))

    def test_qualified_member_wrong_owner_missing(self):
        row = self._inv("OtherTests.test_rejects_malformed_input")
        self.assertEqual(row["status"], "MISSING")

    def test_reference_only_is_not_evidence(self):
        (self.root / "tests" / "mentions.py").write_text(
            "class Refs:\n    pass\n\n\nx = Refs.test_rejects_malformed_input"
            "  # reference only\n", encoding="utf-8")
        self.sha = _commit(self.root, "reference only")
        row = self._inv("Refs.test_rejects_malformed_input")
        self.assertEqual(row["status"], "MISSING")

    def test_unqualified_name_behavior_unchanged(self):
        self.assertEqual(
            self._inv("test_rejects_malformed_input")["status"], "EXISTS")
        self.assertEqual(
            self._inv("test_round_trip_preserves_order")["status"], "MISSING")


class OwnerMatchedExtendSeamTests(unittest.TestCase):
    """AN3 (round 39): a qualified EXTEND seam requires an OWNER-MATCHED
    MEMBER DECLARATION — the old bare member grep accepted
    `MissingParser.parse_data` when only `Parser.parse_data` existed,
    and a call site or comment carrying the member token also
    satisfied it."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root, self.sha = make_repo(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _seam(self, symbol: str, sig: str = "def x()",
              f: str = "src/app/parser.py") -> dict:
        contract = {"files": [], "seams": [
            {"symbol": symbol, "signature": sig, "file": f}]}
        rows = P.seam_verify(self.root, contract, self.sha)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["op"], "EXTEND")
        return rows[0]

    def test_wrong_owner_rejected(self):
        # parse_data IS declared — but in `Parser`, not `MissingParser`
        row = self._seam("MissingParser.parse_data")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("does not resolve", row["detail"])
        self.assertIn("MissingParser", row["detail"])
        self.assertEqual(row["evidence"], [])

    def test_reference_only_rejected(self):
        # `load()` calls parser.parse_data(...) — a call site with the
        # member token is not a declaration inside `load`
        row = self._seam("load.parse_data")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("references/calls do not count", row["detail"])

    def test_exact_owner_member_declaration_accepted(self):
        row = self._seam("Parser.parse_data",
                         sig="parse_data(self, raw) -> dict")
        self.assertEqual(row["status"], "OK")
        self.assertEqual(row["detail"],
                         "declared signature: parse_data(self, raw) -> dict")
        self.assertTrue(row["evidence"])
        self.assertRegex(row["evidence"][0],
                         r"^src/app/parser\.py:\d+ — ")
        self.assertIn("def parse_data", row["evidence"][0])

    def test_unqualified_seam_unchanged(self):
        row = self._seam("Parser")
        self.assertEqual(row["status"], "OK")
        row = self._seam("NoSuchType")
        self.assertEqual(row["status"], "FAIL")
        self.assertIn("does not resolve", row["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
