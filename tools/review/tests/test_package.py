"""Tests for tools/review/package.py (S3.5a T2): deterministic review package builder.

Coverage is CLI-level via subprocess: the exact entry point a dispatcher
uses, with real exit codes, stdout/stderr contracts, and byte-identical
re-builds as the acceptance property.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REVIEW_DIR = Path(__file__).resolve().parents[1]
PACKAGE_PY = REVIEW_DIR / "package.py"

VERSION_LINE = "<!-- review-package v1 -->"
SECTION_HEADINGS = ["## Identity", "## Scope", "## Contract inputs",
                    "## Gate evidence", "## Diff stat", "## Diff"]


class _GitFixtureTest(unittest.TestCase):
    """A two-commit temp repository shared by every package test.

    base: a.txt ("alpha"), sub/b.txt ("beta"), bin.dat (containing NUL).
    head: a.txt modified, c.md added, sub/b.txt moved to root.txt and
    extended, bin.dat modified.  With renames disabled that is five changed
    files: a.txt (M), bin.dat (M, binary), c.md (A), root.txt (A),
    sub/b.txt (D) — insertions 6, deletions 1.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s35a-t2-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "LC_ALL": "C",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_CONFIG_SYSTEM": os.devnull}
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")

        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        sub = self.repo / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("beta\n", encoding="utf-8")
        (self.repo / "bin.dat").write_bytes(b"\x00\x01\x02BLOB" * 8)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "commit one")
        self.base = self.rev_parse("HEAD")

        (self.repo / "a.txt").write_text("alpha\nalpha2\n", encoding="utf-8")
        (self.repo / "c.md").write_text("# C\n\nbody \u00b5 line\n",
                                         encoding="utf-8")
        self.git("mv", "--", "sub/b.txt", "root.txt")
        (self.repo / "root.txt").write_text("beta\nextra\n", encoding="utf-8")
        (self.repo / "bin.dat").write_bytes(b"\x00\x01\x02BLOB2" * 8)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "commit two")
        self.head = self.rev_parse("HEAD")

    # -- helpers ----------------------------------------------------------

    def git(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def cli(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(PACKAGE_PY), *args],
                              capture_output=True, text=True,
                              env=self.env, cwd=cwd)

    def build_proc(self, out: Path, *extra: str) -> subprocess.CompletedProcess:
        return self.cli("build", "--repo", str(self.repo), "--base", self.base,
                        "--head", self.head, "--out", str(out), *extra)

    def build_ok(self, *extra: str, out: Path | None = None) -> str:
        self.out = out or (self.dir / "package.md")
        self.build = self.build_proc(self.out, *extra)
        self.assertEqual(self.build.returncode, 0,
                         msg=self.build.stderr or self.build.stdout)
        self.pkg = self.out.read_text(encoding="utf-8")
        return self.pkg

    def check_proc(self, package: Path, *extra: str) -> subprocess.CompletedProcess:
        return self.cli("check", "--package", str(package),
                        "--repo", str(self.repo), "--base", self.base,
                        "--head", self.head, *extra)

    def pkg_sha(self, text: str) -> str:
        match = re.search(r"^package_sha256: ([0-9a-f]{64})$", text,
                          flags=re.MULTILINE)
        if match is None:
            self.fail("no package_sha256 line found")
        return match.group(1)

    def stat_section(self, pkg: str) -> list[str]:
        lines = pkg.splitlines()
        start = lines.index("## Diff stat") + 1
        end = lines.index("## Diff")
        return lines[start:end]

    def diff_block(self, pkg: str, path: str) -> str:
        """The exact text of one ## Diff block, incl. its trailing newline."""
        start = pkg.index(f"### {path} (")
        tail = pkg[start:]
        stop = tail.find("\n### ")
        return tail if stop == -1 else tail[:stop]

    # -- the tests --------------------------------------------------------

    def test_build_prints_path_and_sha(self) -> None:
        self.build_ok()
        lines = self.build.stdout.splitlines()
        self.assertEqual(len(lines), 2, self.build.stdout)
        self.assertEqual(lines[0], f"package_path: {self.out}")
        sha = self.pkg_sha(self.pkg)
        self.assertEqual(lines[1], f"package_sha256: {sha}")

    def test_package_header_and_hash_covers_body(self) -> None:
        self.build_ok()
        lines = self.pkg.splitlines()
        self.assertEqual(lines[0], VERSION_LINE)
        self.assertEqual(lines[1], f"package_sha256: {self.pkg_sha(self.pkg)}")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3],
                         f"# Review package — repo {self.base[:7]}..{self.head[:7]}")
        body = self.pkg.split("\n\n", 1)[1]
        self.assertEqual(hashlib.sha256(body.encode("utf-8")).hexdigest(),
                         self.pkg_sha(self.pkg))

    def test_rebuild_is_byte_identical(self) -> None:
        self.build_ok()
        first = self.out.read_bytes()
        out2 = self.dir / "package2.md"
        self.build_ok(out=out2)
        self.assertEqual(out2.read_bytes(), first)
        again = self.dir / "package3.md"
        self.build_ok(out=again)
        self.assertEqual(again.read_bytes(), first)

    def test_check_identical_exits_zero(self) -> None:
        self.build_ok()
        proc = self.check_proc(self.out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(),
                         f"review package: identical ({self.pkg_sha(self.pkg)})")

    def test_check_detects_appended_byte_with_offset(self) -> None:
        self.build_ok()
        original = self.out.read_bytes()
        sha = self.pkg_sha(self.pkg)
        mutated = self.dir / "pkg-mutated.md"
        mutated.write_bytes(original + b"X")
        proc = self.check_proc(mutated)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(),
                         f"review package: differs (built {sha}, recorded {sha})"
                         f" at byte {len(original)}")

    def test_check_reports_both_hashes_after_header_mutation(self) -> None:
        self.build_ok()
        sha = self.pkg_sha(self.pkg)
        flipped = "b" if sha[0] == "a" else "a"
        recorded = flipped + sha[1:]
        self.assertNotEqual(recorded, sha)
        mutated = self.dir / "pkg-mutated.md"
        mutated.write_text(self.pkg.replace(f"package_sha256: {sha}",
                                            f"package_sha256: {recorded}"),
                           encoding="utf-8")
        proc = self.check_proc(mutated)
        self.assertEqual(proc.returncode, 1)
        offset = self.pkg.index(sha)
        self.assertEqual(proc.stdout.strip(),
                         f"review package: differs (built {sha}, recorded {recorded})"
                         f" at byte {offset}")

    def test_check_inputs_must_match_build(self) -> None:
        contract = self.dir / "contract.txt"
        contract.write_bytes(b"A" * 3000)
        pkg = self.build_ok("--contract", str(contract),
                            "--max-input-bytes", "50")
        proc = self.check_proc(self.out, "--contract", str(contract),
                               "--max-input-bytes", "50")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.check_proc(self.out, "--contract", str(contract),
                               "--max-input-bytes", "51")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: differs (built ", proc.stdout)

    def test_check_missing_package_refused(self) -> None:
        proc = self.check_proc(self.dir / "nope.md")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: input missing: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_missing_hash_line_refused(self) -> None:
        bad = self.dir / "bad.md"
        bad.write_text("<!-- review-package v1 -->\nnot a hash\n\nbody\n",
                       encoding="utf-8")
        proc = self.check_proc(bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: package hash line missing: ",
                      proc.stderr)

    def test_section_order_fixed(self) -> None:
        self.build_ok()
        lines = self.pkg.splitlines()
        positions = [lines.index(h) for h in SECTION_HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(lines.index("## Identity"), 5)

    def test_identity_section_matches_numstat(self) -> None:
        self.build_ok()
        rows = self.git("-c", "core.quotepath=false", "diff", "--no-renames",
                        "--numstat", self.base, self.head)
        ins = sum(int(r.split("\t")[0]) for r in rows.stdout.splitlines()
                  if r.split("\t")[0].isdigit())
        dele = sum(int(r.split("\t")[1]) for r in rows.stdout.splitlines()
                   if r.split("\t")[0].isdigit())
        self.assertEqual(ins, 6)
        self.assertEqual(dele, 1)
        self.assertIn(
            f"repo: {os.path.abspath(self.repo)}\n"
            f"base_sha: {self.base}\n"
            f"head_sha: {self.head}\n"
            f"changed_files: 5\n"
            f"insertions: {ins}\n"
            f"deletions: {dele}\n",
            self.pkg)

    def test_scope_defaults_to_all_changed_files(self) -> None:
        self.build_ok()
        self.assertIn("allowed_files: all changed files\n"
                      "withheld_files: none\n"
                      "omitted_files: none\n", self.pkg)

    def test_scope_allowed_and_withheld_lists(self) -> None:
        allowed = self.dir / "allowed.txt"
        allowed.write_text("a.txt\nsub/b.txt\n", encoding="utf-8")
        withheld = self.dir / "withheld.txt"
        withheld.write_text("bin.dat\n", encoding="utf-8")
        self.build_ok("--allowed", str(allowed), "--withheld", str(withheld))
        self.assertIn("allowed_files:\n- a.txt\n- sub/b.txt\n"
                      "withheld_files:\n- bin.dat\n"
                      "omitted_files: none\n", self.pkg)

    def test_diff_blocks_statuses_and_rename_as_delete_add(self) -> None:
        self.build_ok()
        self.assertIn("### a.txt (M)\n", self.pkg)
        self.assertIn("### c.md (A)\n", self.pkg)
        self.assertIn("### root.txt (A)\n", self.pkg)
        self.assertIn("### sub/b.txt (D)\n", self.pkg)
        self.assertIn("```diff\n", self.pkg)

    def test_diff_uses_full_index(self) -> None:
        self.build_ok()
        self.assertRegex(self.pkg, r"index [0-9a-f]{40}\.\.[0-9a-f]{40}")

    def test_numstat_rows_and_total_in_stat(self) -> None:
        self.build_ok()
        stat = self.stat_section(self.pkg)
        rows = self.git("-c", "core.quotepath=false", "diff", "--no-renames",
                        "--numstat", self.base, self.head)
        expected = {line for line in rows.stdout.splitlines() if line}
        got = {line for line in stat if line}
        self.assertTrue(expected.issubset(got), f"missing {expected - got}")
        self.assertIn("total: 5 files, +6 -1", stat)
        self.assertIn("-\t-\tbin.dat", stat)

    def test_binary_file_listed_without_diff(self) -> None:
        self.build_ok()
        self.assertIn("### bin.dat (M)\n\n[binary]\n", self.pkg)
        block = self.diff_block(self.pkg, "bin.dat")
        self.assertEqual(block, "### bin.dat (M)\n\n[binary]\n")
        self.assertNotIn("```", block)

    def test_max_diff_bytes_omits_rest_deterministically(self) -> None:
        self.build_ok()
        full_pkg = self.pkg
        size_a = len(self.diff_block(full_pkg, "a.txt").encode("utf-8"))
        self.build_ok("--max-diff-bytes", str(size_a),
                      out=self.dir / "capped.md")
        capped = self.pkg
        self.assertIn("### a.txt (M)\n", capped)
        omitted_paths = ["bin.dat", "c.md", "root.txt", "sub/b.txt"]
        expected = {}
        for path in omitted_paths:
            self.assertNotIn(f"### {path} (", capped)
            size = len(self.diff_block(full_pkg, path).encode("utf-8"))
            expected[path] = size
            self.assertIn(f"- {path} ({size} bytes)", capped)
        positions = [capped.index(f"- {p} ({expected[p]} bytes)")
                     for p in omitted_paths]
        self.assertEqual(positions, sorted(positions))
        again = self.dir / "capped2.md"
        self.build_ok("--max-diff-bytes", str(size_a), out=again)
        self.assertEqual(again.read_bytes(), self.out.read_bytes())

    def test_max_input_bytes_truncation_marker(self) -> None:
        contract = self.dir / "contract.txt"
        contract.write_bytes(b"A" * 3000)
        pkg = self.build_ok("--contract", str(contract),
                            "--max-input-bytes", "100")
        full_digest = hashlib.sha256(b"A" * 3000).hexdigest()
        self.assertIn(f"### contract — {contract} (sha256 {full_digest})", pkg)
        self.assertIn("```txt\n" + "A" * 100
                      + "…[truncated: 2900 bytes omitted]…\n```", pkg)

    def test_inputs_untouched_within_limit(self) -> None:
        contract = self.dir / "contract.txt"
        contract.write_bytes(b"A" * 3000)
        pkg = self.build_ok("--contract", str(contract))
        full_digest = hashlib.sha256(b"A" * 3000).hexdigest()
        self.assertIn(f"### contract — {contract} (sha256 {full_digest})", pkg)
        self.assertIn("```txt\n" + "A" * 3000 + "\n```", pkg)
        self.assertNotIn("truncated", pkg)

    def test_contract_gate_labels_and_fence_languages(self) -> None:
        other = self.dir / "other.md"
        other.write_text("# T\n", encoding="utf-8")
        gate = self.dir / "g.json"
        gate.write_text("{}\n", encoding="utf-8")
        pkg = self.build_ok("--contract", f"{other}:Custom",
                            "--gate", str(gate))
        h_other = hashlib.sha256(b"# T\n").hexdigest()
        h_gate = hashlib.sha256(b"{}\n").hexdigest()
        self.assertIn(f"### Custom — {other} (sha256 {h_other})", pkg)
        self.assertIn(f"### g — {gate} (sha256 {h_gate})", pkg)
        self.assertIn("```md\n# T\n```", pkg)
        self.assertIn("```json\n{}\n```", pkg)

    def test_refusal_not_a_git_worktree(self) -> None:
        plain = self.dir / "notrepo"
        plain.mkdir()
        proc = self.cli("build", "--repo", str(plain), "--base", self.base,
                        "--head", self.head, "--out", str(self.dir / "x.md"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: not a git worktree: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_unresolvable_base_sha(self) -> None:
        bogus = "0" * 40
        proc = self.cli("build", "--repo", str(self.repo), "--base", bogus,
                        "--head", self.head, "--out", str(self.dir / "x.md"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: unresolvable base sha: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_unresolvable_head_sha(self) -> None:
        bogus = "f" * 40
        proc = self.cli("build", "--repo", str(self.repo), "--base", self.base,
                        "--head", bogus, "--out", str(self.dir / "x.md"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: unresolvable head sha: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_base_and_head_same_commit(self) -> None:
        proc = self.cli("build", "--repo", str(self.repo), "--base", self.head,
                        "--head", self.head, "--out", str(self.dir / "x.md"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: base and head are the same commit",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_input_missing(self) -> None:
        proc = self.build_proc(self.dir / "x.md", "--contract",
                               str(self.dir / "nope.md"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: input missing: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_input_unreadable(self) -> None:
        secret = self.dir / "secret.md"
        secret.write_text("s\n", encoding="utf-8")
        secret.chmod(0o000)
        if os.access(secret, os.R_OK):
            self.skipTest("running as root; cannot make a file unreadable")
        proc = self.build_proc(self.dir / "x.md", "--contract", str(secret))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: input unreadable: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_refusal_out_path_not_writable(self) -> None:
        proc = self.build_proc(self.dir / "no-such-dir" / "x.md")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review package: out path not writable: ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_usage_errors_exit_2(self) -> None:
        proc = self.cli("build")
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("frobnicate")
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("check")
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("build", "--repo", str(self.repo),
                        "--base", self.base, "--head", self.head,
                        "--out", str(self.dir / "x.md"),
                        "--max-diff-bytes", "not-a-number")
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)

    def test_build_runs_as_python_file_with_full_set(self) -> None:
        """T1 lesson: the script must work invoked as a file path anywhere."""
        contract = self.dir / "contract.txt"
        contract.write_bytes(b"C" * 100)
        out = self.dir / "pkg.md"
        proc = self.cli("build", "--repo", str(self.repo), "--base", self.base,
                        "--head", self.head, "--out", str(out),
                        "--contract", str(contract), cwd=self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out.read_text(encoding="utf-8").splitlines()[0],
                         VERSION_LINE)
        check = self.cli("check", "--package", str(out),
                         "--repo", str(self.repo), "--base", self.base,
                         "--head", self.head, "--contract", str(contract),
                         cwd=self.repo)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("review package: identical (", check.stdout)

    def test_check_missing_package_argument_is_usage(self) -> None:
        proc = self.cli("check", "--repo", str(self.repo),
                        "--base", self.base, "--head", self.head)
        self.assertEqual(proc.returncode, 2)

    def test_short_sha_input_resolves_to_full(self) -> None:
        pkg = self.build_ok()
        short_base, short_head = self.base[:12], self.head[:12]
        out2 = self.dir / "pkg-short.md"
        proc = self.cli("build", "--repo", str(self.repo),
                        "--base", short_base, "--head", short_head,
                        "--out", str(out2))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(out2.read_bytes(), self.out.read_bytes())
        self.assertIn(f"base_sha: {self.base}\n", pkg)

    def test_obscure_paths_are_not_c_quoted(self) -> None:
        (self.repo / "weird \u00fc.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "obscure")
        head = self.rev_parse("HEAD")
        proc = self.cli("build", "--repo", str(self.repo), "--base", self.head,
                        "--head", head, "--out", str(self.dir / "pkg-weird.md"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pkg = (self.dir / "pkg-weird.md").read_text(encoding="utf-8")
        self.assertIn("### weird \u00fc.txt (A)\n", pkg)
        self.assertNotIn("\\303\\274", pkg)


class IncludeScopeTests(unittest.TestCase):
    """--include narrows the diff, the stat line, and the identity counts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.email", "a@b.c")
        self.git("config", "user.name", "a")
        (self.repo / "docs").mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "docs" / "a.md").write_text("doc\n", encoding="utf-8")
        (self.repo / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.rev("HEAD")
        (self.repo / "docs" / "a.md").write_text("doc changed\n", encoding="utf-8")
        (self.repo / "src" / "a.py").write_text("a = 2\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "head")
        self.head = self.rev("HEAD")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, check=True)

    def rev(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def build(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build", "--repo", str(self.repo),
             "--base", self.base, "--head", self.head,
             "--out", str(Path(self._tmp.name) / "pkg.md"), *extra],
            capture_output=True, text=True)

    def test_include_limits_diff_and_counts(self) -> None:
        result = self.build("--include", "docs")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (Path(self._tmp.name) / "pkg.md").read_text(encoding="utf-8")
        self.assertIn("included_paths:\n- docs\n", text)
        self.assertIn("changed_files: 1", text)
        self.assertIn("### docs/a.md", text)
        self.assertNotIn("### src/a.py", text)

    def test_include_matching_nothing_is_refused(self) -> None:
        result = self.build("--include", "nothing-here")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--include matched no changed files", result.stderr)


if __name__ == "__main__":
    unittest.main()