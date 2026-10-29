"""Tests for tools/review/record.py (S3.5a T3): review record renderer +
disposition ledger.

Coverage is CLI-level via subprocess: real packages built with package.py in
a temp git repository, real findings documents emitted with the yamlish
emitter, and byte-level determinism / refusal assertions as acceptance
criteria.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

from tools.review import record
from pathlib import Path

if __package__ in (None, ""):  # direct CLI: python3 tools/review/tests/test_record.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.review.yamlish import dumps as yamlish_dumps

REVIEW_DIR = Path(__file__).resolve().parents[1]
RECORD_PY = REVIEW_DIR / "record.py"
PACKAGE_PY = REVIEW_DIR / "package.py"

SCHEMA = "review.record.v1"


class _GitFixtureTest(unittest.TestCase):
    """Temp repo: base commit, head commit, two fix commits.

    base: a.txt ("alpha"), src/app.py, docs/readme.md
    head: a.txt modified ("alpha\\nbeta"), extra/new.txt added
    fix-a: further modifies a.txt (touches artifact a.txt)
    fix-other: adds extra/notes.md only (does not touch a.txt)
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s35a-t3-")
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
        (self.repo / "src").mkdir()
        (self.repo / "src" / "app.py").write_text("def main(): pass\n",
                                                  encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "readme.md").write_text("# Readme\n",
                                                       encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit one")
        self.base = self.rev_parse("HEAD")

        (self.repo / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        (self.repo / "extra").mkdir()
        (self.repo / "extra" / "new.txt").write_text("new\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit two")
        self.head = self.rev_parse("HEAD")

        (self.repo / "a.txt").write_text("alpha\nbeta\ngamma\n",
                                         encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit three: fix a.txt")
        self.fix_a = self.rev_parse("HEAD")

        (self.repo / "extra" / "notes.md").write_text("# Notes\n",
                                                      encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit four: notes only")
        self.fix_other = self.rev_parse("HEAD")

        # Gate evidence files are content-addressed into the record, so the
        # default one must be created exactly once and reused (repeat renders
        # must stay byte-identical).
        self.gate_path = self.dir / "gate-lint.txt"
        self.gate_path.write_text("lint: ok\n", encoding="utf-8")

    # -- helpers ----------------------------------------------------------

    def git(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def cli(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(RECORD_PY), *args],
                              capture_output=True, text=True,
                              env=self.env, cwd=cwd)

    def build_package(self, *extra: str, out: Path | None = None):
        """Build a review package; returns (path, sha256)."""
        self.pkg = out or (self.dir / "package.md")
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build", "--repo", str(self.repo),
             "--base", self.base, "--head", self.head, "--out", str(self.pkg),
             *extra],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], f"package_path: {self.pkg}", proc.stdout)
        self.pkg_sha = lines[1].split("package_sha256: ", 1)[1]
        return self.pkg, self.pkg_sha

    def finding(self, fid: str, severity: str = "minor",
                artifact: str = "a.txt:2",
                dimension: str = "behavior") -> dict:
        assert fid[:3] in ("RC-", "RA-", "RS-", "EX-"), fid
        return {
            "id": fid,
            "severity": severity,
            "rubric_dimension": dimension,
            "artifact_reference": artifact,
            "evidence": f"Evidence for {fid}.",
            "required_change": f"Fix {fid}.",
            "actionable": True,
        }

    def findings_doc(self, reviewer: str, findings: list[dict], *,
                     sha: str | None = None, base: str | None = None,
                     head: str | None = None) -> dict:
        return {
            "schema": "review.findings.v1",
            "review": {
                "reviewer": reviewer,
                "rubric_version": f"{reviewer}-v1",
                "package_sha256": sha if sha is not None else self.pkg_sha,
                "base_sha": base if base is not None else self.base,
                "head_sha": head if head is not None else self.head,
                "decision": "CHANGES_REQUESTED" if findings else "APPROVED",
                "findings": findings,
            },
        }

    def write_findings(self, reviewer: str, findings: list[dict], *,
                       path: Path | None = None, **overrides) -> Path:
        if not hasattr(self, "pkg_sha"):
            self.build_package()
        out = path or (self.dir / f"findings-{reviewer}.yaml")
        out.write_text(yamlish_dumps(self.findings_doc(reviewer, findings,
                                                       **overrides)),
                       encoding="utf-8")
        return out

    def write_dispositions(self, entries: list[dict],
                           path: Path | None = None) -> Path:
        out = path or (self.dir / "dispositions.yaml")
        out.write_text(yamlish_dumps({"dispositions": entries}),
                       encoding="utf-8")
        return out

    def render(self, **overrides) -> subprocess.CompletedProcess:
        """Run render with a full default asset set; overrides replace keys:
        story, pr, base, head, package, findings (list of (role, path)),
        dispositions, gates (list of str), loop, loop_cap, prior (list),
        out_dir, extra (list of str)."""
        if "package" not in overrides:
            overrides["package"] = str(self.build_package()[0])
        if "findings" not in overrides:
            doc = self.write_findings("review-adversary",
                                      [self.finding("RA-001")])
            overrides["findings"] = [("review-adversary", str(doc))]
        if "dispositions" not in overrides:
            overrides["dispositions"] = str(self.write_dispositions(
                [{"id": "RA-001", "disposition": "fixed",
                  "fix_sha": self.fix_a}]))
        kw = {
            "repo": str(self.repo),
            "story": "S35A",
            "pr": 42,
            "base": self.base,
            "head": self.head,
            "dispositions": overrides["dispositions"],
            "findings": overrides["findings"],
            "gates": [f"lint=0:{self.gate_path}"],
            "loop": None,
            "loop_cap": None,
            "prior": [],
            "out_dir": str(self.dir / "record"),
            "extra": [],
        }
        kw.update(overrides)
        args = ["render", "--repo", kw["repo"], "--story", kw["story"],
                "--pr", str(kw["pr"]), "--base", kw["base"], "--head", kw["head"],
                "--package", kw["package"], "--dispositions", kw["dispositions"]]
        for role, path in kw["findings"]:
            args += ["--findings", f"{role}={path}"]
        for gate in kw["gates"]:
            args += ["--gate", gate]
        if kw["loop"] is not None:
            args += ["--loop", str(kw["loop"])]
        if kw["loop_cap"] is not None:
            args += ["--loop-cap", str(kw["loop_cap"])]
        for directory in kw["prior"]:
            args += ["--prior", str(directory)]
        args += ["--out-dir", kw["out_dir"]]
        args += [str(item) for item in kw["extra"]]
        return self.cli(*args)

    def gate_file(self, name: str = "lint") -> Path:
        path = self.dir / f"gate-{name}.txt"
        path.write_text(f"{name}: ok\n", encoding="utf-8")
        return path

    def render_ok(self, **overrides) -> tuple[Path, dict, str]:
        """Render successfully; returns (out_dir, review.json dict, md text)."""
        out_dir = Path(overrides.get("out_dir", self.dir / "record"))
        proc = self.render(**overrides)
        self.assertEqual(proc.returncode, 0,
                         msg=proc.stderr or proc.stdout)
        data = json.loads((out_dir / "review.json").read_text(encoding="utf-8"))
        md = (out_dir / "REVIEW-42.md").read_text(encoding="utf-8")
        if overrides.get("pr") is not None:
            md = (out_dir / f"REVIEW-{overrides['pr']}.md").read_text(
                encoding="utf-8")
        return out_dir, data, md

    def check_proc(self, record_dir, repo=None) -> subprocess.CompletedProcess:
        return self.cli("check", "--record", str(record_dir),
                        "--repo", str(repo or self.repo))

    # -- the tests --------------------------------------------------------

    def test_render_writes_json_and_markdown(self) -> None:
        out = Path(self.dir / "rec-a")
        proc = self.render(out_dir=str(out))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertTrue((out / "review.json").is_file())
        self.assertTrue((out / "REVIEW-42.md").is_file())
        self.assertFalse((out / "REVIEW-7.md").exists())

    def test_review_json_canonical_bytes_and_schema(self) -> None:
        out, data, _ = self.render_ok()
        raw = (out / "review.json").read_bytes().decode("utf-8")
        self.assertEqual(raw, json.dumps(data, indent=2, sort_keys=True) + "\n")
        self.assertEqual(data["schema"], SCHEMA)
        self.assertEqual(data["lane"], {"name": "review-lane-v1", "version": 1})
        self.assertEqual(data["undispositioned"], [])
        self.assertEqual(data["story"], "S35A")
        self.assertEqual(data["pr"], 42)
        self.assertEqual(data["base_sha"], self.base)
        self.assertEqual(data["head_sha"], self.head)
        self.assertEqual(data["package"]["sha256"], self.pkg_sha)

    def test_verdict_approved_with_counts(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001", "minor"),
                                   self.finding("RA-002", "info",
                                                artifact="extra/new.txt:1")])
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002", "disposition": "rejected",
             "reason": "out of scope", "evidence": "see plan"}])
        _, data, md = self.render_ok(findings=[("review-adversary", str(doc))],
                                     dispositions=str(disp))
        self.assertEqual(data["verdict"]["decision"], "APPROVED")
        self.assertEqual(data["verdict"]["blockers"], 0)
        self.assertEqual(data["verdict"]["majors"], 0)
        self.assertEqual(data["verdict"]["minors"], 1)
        self.assertEqual(data["verdict"]["infos"], 1)
        self.assertIn("decision: APPROVED", md)

    def test_verdict_blocker_forces_changes_requested(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001", "blocker")])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))])
        self.assertEqual(data["verdict"]["decision"], "CHANGES_REQUESTED")
        self.assertEqual(data["verdict"]["blockers"], 1)

    def test_verdict_major_forces_changes_requested(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001", "major")])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))])
        self.assertEqual(data["verdict"]["decision"], "CHANGES_REQUESTED")
        self.assertEqual(data["verdict"]["majors"], 1)

    def test_open_findings_lists_non_fixed(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1"),
            self.finding("RA-003", "info", artifact="src/app.py:1")])
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002", "disposition": "rejected",
             "reason": "no", "evidence": "log"},
            {"id": "RA-003", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/7"},
        ])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["open_findings"], ["RA-002", "RA-003"])
        self.assertEqual(data["undispositioned"], [])

    def test_dispositions_recorded_with_all_keys(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1"),
        ])
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/1"},
        ])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["dispositions"], [
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a,
             "reason": None, "issue": None, "evidence": None},
            {"id": "RA-002", "disposition": "deferred", "fix_sha": None,
             "reason": None,
             "issue": "https://github.com/org/repo/issues/1",
             "evidence": None},
        ])

    def test_rejected_missing_reason_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001", "disposition": "rejected",
              "evidence": "only evidence"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing reason/evidence: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_rejected_missing_evidence_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001", "disposition": "rejected",
              "reason": "only reason"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing reason/evidence: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_deferred_bad_issue_url_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001", "disposition": "deferred",
              "issue": "https://gitlab.com/r/i/1"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad issue URL: RA-001", proc.stderr)
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001", "disposition": "deferred"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad issue URL: RA-001", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_deferred_valid_issue_urls_ok(self) -> None:
        url = "https://github.com/openai/hello/issues/123"
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "deferred", "issue": url}])
        _, data, md = self.render_ok(dispositions=str(disp))
        self.assertEqual(data["dispositions"][0]["issue"], url)
        self.assertEqual(data["open_findings"], ["RA-001"])
        self.assertIn(f"issue: {url}", md)

    def test_fixed_unresolvable_sha_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed",
              "fix_sha": "0123456789abcdef"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad fixed sha:", proc.stderr)
        self.assertIn("not a commit", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_sha_bad_format_refused(self) -> None:
        for bad in ("xyz1234", "ABCDEF1", "abc", "f" * 41):
            proc = self.render(dispositions=str(self.write_dispositions(
                [{"id": "RA-001", "disposition": "fixed", "fix_sha": bad}])))
            self.assertEqual(proc.returncode, 1, bad)
            self.assertIn("review record: bad fixed sha:", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_sha_not_touching_artifact_refused(self) -> None:
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed",
              "fix_sha": self.fix_other}])
        proc = self.render(dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad fixed sha:", proc.stderr)
        self.assertIn("RA-001", proc.stderr)
        self.assertIn("does not touch a.txt", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_sha_touching_artifact_accepted(self) -> None:
        _, data, _ = self.render_ok()  # default: fix_a touches a.txt
        self.assertEqual(data["dispositions"][0]["fix_sha"], self.fix_a)
        self.assertEqual(data["open_findings"], [])

    def test_fixed_short_sha_accepted(self) -> None:
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed",
              "fix_sha": self.fix_a[:9]}])
        _, data, _ = self.render_ok(dispositions=str(disp))
        self.assertEqual(data["dispositions"][0]["fix_sha"], self.fix_a[:9])

    def test_artifact_path_with_line_suffix_touches_bare_path(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001", artifact="a.txt:7")])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))])
        self.assertEqual(data["verdict"]["decision"], "APPROVED")

    def test_withheld_artifact_refused(self) -> None:
        withheld = self.dir / "withheld.txt"
        withheld.write_text("a.txt\n", encoding="utf-8")
        package, _ = self.build_package("--withheld", str(withheld))
        proc = self.render(package=str(package))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: withheld artifact: RA-001", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_package_diff_reference_exempt_from_withheld_and_touch(self) -> None:
        withheld = self.dir / "withheld.txt"
        withheld.write_text("a.txt\n", encoding="utf-8")
        package, _ = self.build_package("--withheld", str(withheld))
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001",
                                                artifact="package:diff@a.txt:4")])
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed",
              "fix_sha": self.fix_other}])
        proc = self.render(package=str(package),
                           findings=[("review-adversary", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)

    def test_package_hash_mismatch_refused(self) -> None:
        package, sha = self.build_package()
        text = package.read_text(encoding="utf-8")
        tampered = text.replace("changed_files: 2", "changed_files: 9", 1)
        self.assertNotEqual(tampered, text)
        package.write_text(tampered, encoding="utf-8")
        proc = self.render(package=str(package))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package hash mismatch:", proc.stderr)
        self.assertIn(sha, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_package_head_mismatch_refused(self) -> None:
        proc = self.render(head=self.fix_other)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package head/base mismatch:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unresolvable_base_sha_refused(self) -> None:
        proc = self.render(base="0" * 40)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package head/base mismatch:", proc.stderr)
        self.assertIn("base sha does not resolve", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_findings_doc_package_hash_mismatch_refused(self) -> None:
        doc = self.write_findings("review-adversary", [self.finding("RA-001")],
                                  sha="f" * 64)
        proc = self.render(findings=[("review-adversary", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings doc "
                      "package_sha256/head_sha mismatch:", proc.stderr)
        self.assertIn("package_sha256", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_findings_doc_head_mismatch_refused(self) -> None:
        doc = self.write_findings("review-adversary", [self.finding("RA-001")],
                                  head=self.base)
        proc = self.render(findings=[("review-adversary", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings doc "
                      "package_sha256/head_sha mismatch:", proc.stderr)
        self.assertIn("head_sha", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unknown_reviewer_role_refused(self) -> None:
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        proc = self.render(findings=[("mystery", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unknown reviewer role: mystery",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_duplicate_reviewer_role_refused(self) -> None:
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        proc = self.render(findings=[("review-adversary", str(doc)),
                                     ("review-adversary", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate reviewer role: "
                      "review-adversary", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unreadable_findings_doc_refused(self) -> None:
        missing = self.dir / "findings-missing.yaml"
        proc = self.render(findings=[("review-adversary", str(missing))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unreadable findings doc:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_undispositioned_findings_refused(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1")])
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a}])
        proc = self.render(findings=[("review-adversary", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: undispositioned findings: RA-002",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_disposition_unknown_id_refused(self) -> None:
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RS-999", "disposition": "fixed", "fix_sha": self.fix_a},
        ])
        proc = self.render(dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: disposition for unknown id: RS-999",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_duplicate_disposition_refused(self) -> None:
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
        ])
        proc = self.render(dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate disposition: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_missing_dispositions_file_refused(self) -> None:
        proc = self.render(dispositions=str(self.dir / "nope.yaml"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing dispositions file:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_gate_evidence_missing_refused(self) -> None:
        proc = self.render(gates=["lint=0:/no/such/file.txt"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: gate evidence path missing: lint",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_gate_results_recorded(self) -> None:
        gate = self.gate_file("tests")
        _, data, _ = self.render_ok(gates=[f"tests=1:{gate}"])
        self.assertEqual(data["gate_results"], [{
            "gate": "tests",
            "exit": 1,
            "evidence_path": str(gate),
            "evidence_sha256": hashlib.sha256(
                gate.read_bytes()).hexdigest(),
        }])

    def test_prior_rounds_in_json_and_markdown(self) -> None:
        first_dir, first_data, first_md = self.render_ok()
        first_loop = first_data["verdict"]["loop"]
        second_dir, second_data, second_md = self.render_ok(
            out_dir=str(self.dir / "record2"),
            loop=2,
            prior=[str(first_dir)])
        self.assertEqual(second_data["prior_rounds"], [{
            "loop": first_loop,
            "path": str(first_dir),
            "decision": first_data["verdict"]["decision"],
            "findings": len(first_data["findings"]),
            "dispositioned": sum(1 for d in first_data["dispositions"]
                                 if d["disposition"] == "fixed"),
        }])
        self.assertIn("## Prior rounds", second_md)
        self.assertIn(f"| {first_loop} | APPROVED |", second_md)
        self.assertIn("loop 2 of 2", second_md)
        self.assertNotIn("## Prior rounds", first_md)

    def test_invalid_prior_round_refused(self) -> None:
        proc = self.render(prior=[str(self.dir / "no-such-record")])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: invalid prior round:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_repeat_render_identical_bytes(self) -> None:
        out_a = str(self.dir / "record-a")
        out_b = str(self.dir / "record-b")
        self.render_ok(out_dir=out_a)
        self.render_ok(out_dir=out_b)
        json_a = (Path(out_a) / "review.json").read_bytes()
        json_b = (Path(out_b) / "review.json").read_bytes()
        md_a = (Path(out_a) / "REVIEW-42.md").read_bytes()
        md_b = (Path(out_b) / "REVIEW-42.md").read_bytes()
        self.assertEqual(json_a, json_b)
        self.assertEqual(md_a, md_b)

    def test_markdown_sections_and_literal_lines(self) -> None:
        _, _, md = self.render_ok()
        self.assertTrue(md.startswith("# Review record — PR 42 (S35A)\n"))
        positions = [md.index(heading) for heading in
                     ("## Verdict", "## Package", "## Reviewers",
                      "## Gate results", "## Findings", "## Notes")]
        self.assertEqual(positions, sorted(positions))
        verdict = md[md.index("## Verdict"):md.index("## Package")]
        self.assertIn("undispositioned findings: 0\n", verdict)
        self.assertIn("loop 1 of 2\n", verdict)
        self.assertIn("decision: APPROVED\n", verdict)
        for heading in ("### a.txt", "## Prior rounds"):
            self.assertNotIn(heading, md)
        self.assertIn("RA-001", md)
        self.assertIn(self.pkg_sha, md)

    def test_markdown_findings_table_dispositions(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1"),
            self.finding("RA-003", "info", artifact="src/app.py:1")])
        disp = self.write_dispositions([
            {"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002", "disposition": "rejected",
             "reason": "not now", "evidence": "log 42"},
            {"id": "RA-003", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/9"}])
        _, _, md = self.render_ok(findings=[("review-adversary", str(doc))],
                                  dispositions=str(disp))
        self.assertIn(f"| RA-001 | review-adversary | minor | behavior | "
                      f"a.txt:2 | fixed | fixed: {self.fix_a} |", md)
        self.assertIn("| RA-002 | review-adversary | info | behavior | "
                      "extra/new.txt:1 | rejected | reason: not now "
                      "evidence: log 42 |", md)
        self.assertIn("| RA-003 | review-adversary | info | behavior | "
                      "src/app.py:1 | deferred | issue: "
                      "https://github.com/org/repo/issues/9 |", md)

    def test_external_source_and_lens(self) -> None:
        doc = self.write_findings("external", [self.finding("EX-001", "info",
                                                           artifact="a.txt:1")])
        disp = self.write_dispositions(
            [{"id": "EX-001", "disposition": "rejected",
              "reason": "legacy", "evidence": "wire log"}])
        _, data, _ = self.render_ok(findings=[("external", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["findings"][0]["source"], "external")
        self.assertEqual(data["findings"][0]["lens"], "external")
        self.assertEqual(data["reviewers"][0]["role"], "external")

    def test_multiple_reviewers_sorted(self) -> None:
        contract = self.write_findings(
            "review-contract",
            [self.finding("RC-001", "info", artifact="src/app.py:1",
                          dimension="contract_fidelity")])
        security = self.write_findings(
            "review-security",
            [self.finding("RS-001", "minor", artifact="a.txt:1",
                          dimension="security")])
        disp = self.write_dispositions([
            {"id": "RC-001", "disposition": "rejected", "reason": "no",
             "evidence": "e"},
            {"id": "RS-001", "disposition": "fixed", "fix_sha": self.fix_a},
        ])
        _, data, md = self.render_ok(
            findings=[("review-security", str(security)),
                      ("review-contract", str(contract))],
            dispositions=str(disp))
        self.assertEqual([r["role"] for r in data["reviewers"]],
                         ["review-contract", "review-security"])
        self.assertEqual([f["id"] for f in data["findings"]],
                         ["RC-001", "RS-001"])
        self.assertIn("| RC-001 | review-contract |", md)
        self.assertIn("| RS-001 | review-security |", md)

    def test_empty_findings_approved(self) -> None:
        doc = self.write_findings("review-contract", [])
        disp = self.write_dispositions([])
        _, data, md = self.render_ok(findings=[("review-contract", str(doc))],
                                     dispositions=str(disp))
        self.assertEqual(data["verdict"]["decision"], "APPROVED")
        self.assertEqual(data["findings"], [])
        self.assertEqual(data["open_findings"], [])
        self.assertIn("undispositioned findings: 0\n", md)

    def test_reviewer_listed_even_without_findings(self) -> None:
        doc = self.write_findings("review-contract", [])
        disp = self.write_dispositions([])
        _, data, _ = self.render_ok(findings=[("review-contract", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["reviewers"][0]["role"], "review-contract")
        self.assertEqual(data["reviewers"][0]["rubric_version"],
                         "review-contract-v1")

    # -- check subcommand -------------------------------------------------

    def test_check_ok(self) -> None:
        out, data, _ = self.render_ok()
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("review record: OK (1 finding(s),", proc.stdout)

    def test_check_detects_tampered_markdown(self) -> None:
        out, _, _ = self.render_ok()
        md_path = out / "REVIEW-42.md"
        md_path.write_bytes(md_path.read_bytes() + b"# extra\n")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: markdown differs:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_detects_invalid_record(self) -> None:
        out, _, _ = self.render_ok()
        record_path = out / "review.json"
        data = json.loads(record_path.read_text(encoding="utf-8"))
        data["schema"] = "review.record.v9"
        record_path.write_text(json.dumps(data), encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: invalid record:", proc.stderr)
        self.assertIn("invalid schema", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_detects_missing_gate_evidence(self) -> None:
        out, data, _ = self.render_ok()
        Path(data["gate_results"][0]["evidence_path"]).unlink()
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: gate evidence path missing: lint",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_detects_removed_findings_doc(self) -> None:
        out, data, _ = self.render_ok()
        Path(data["reviewers"][0]["document"]).unlink()
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings doc mismatch:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_detects_package_tamper(self) -> None:
        out, _, _ = self.render_ok()
        text = self.pkg.read_text(encoding="utf-8")
        self.pkg.write_text(text.replace("## Identity", "## Identityy", 1),
                            encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package hash mismatch:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    # -- usage / invocation ------------------------------------------------

    def test_usage_errors_exit_2(self) -> None:
        proc = self.cli()
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("frobnicate")
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("render")
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("render", "--repo", str(self.repo))
        self.assertEqual(proc.returncode, 2)
        proc = self.render(extra=["--gate", "garbage"])
        self.assertEqual(proc.returncode, 2)
        proc = self.render(extra=["--findings", "nofile-without-equals"])
        self.assertEqual(proc.returncode, 2)
        proc = self.render(loop=3, loop_cap=2)
        self.assertEqual(proc.returncode, 2)
        proc = self.cli("render", "--repo", str(self.repo), "--base",
                        "not-a-sha", "--head", self.head, "--pr", "x",
                        "--story", "S", "--package", "p", "--dispositions",
                        "d")
        self.assertEqual(proc.returncode, 2)

    def test_direct_file_path_invocation(self) -> None:
        """T1 lesson: record.py must work invoked as a bare file path from
        any working directory."""
        package, _ = self.build_package()
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        disp = self.write_dispositions(
            [{"id": "RA-001", "disposition": "fixed", "fix_sha": self.fix_a}])
        gate = self.gate_file()
        out = self.dir / "record-from-repo-cwd"
        proc = self.cli("render", "--repo", str(self.repo), "--story", "S35A",
                        "--pr", "7", "--base", self.base, "--head", self.head,
                        "--package", str(package), "--findings",
                        f"review-adversary={doc}", "--dispositions", str(disp),
                        "--gate", f"lint=0:{gate}", "--out-dir", str(out),
                        cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        check = self.check_proc(out)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("review record: OK (", check.stdout)


class TestDispositionShaCoercion(unittest.TestCase):
    """An unquoted all-digit short SHA arrives as an int; it is still a SHA."""

    def test_unquoted_numeric_fix_sha_is_coerced_to_str(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "disp.yaml"
            path.write_text(
                "dispositions:\n"
                "  - id: RA-001\n"
                "    disposition: fixed\n"
                "    fix_sha: 6032430\n",
                encoding="utf-8")
            self.assertEqual(record.load_dispositions(str(path))[0]["fix_sha"],
                             "6032430")


class RecordPathTests(unittest.TestCase):
    """Committed records must not leak local absolute paths."""

    def test_path_inside_repo_is_relative(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "stories" / "S1").mkdir(parents=True)
            target = repo / "stories" / "S1" / "review.json"
            target.write_text("{}", encoding="utf-8")
            self.assertEqual(record.record_path(str(target), str(repo)),
                             "stories/S1/review.json")

    def test_path_outside_repo_stays_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            other = Path(td) / "repo-other"
            repo.mkdir()
            other.mkdir()
            target = other / "x.yaml"
            target.write_text("{}", encoding="utf-8")
            self.assertTrue(os.path.isabs(record.record_path(str(target), str(repo))))

    def test_resolve_record_path_joins_relative(self) -> None:
        self.assertEqual(record.resolve_record_path("a/b.yaml", "/tmp/r"),
                         os.path.join("/tmp/r", "a/b.yaml"))


class RequiredChangePathTests(_GitFixtureTest):
    """A doc finding may be fixed in a file named by its required_change."""

    def test_fixed_accepted_when_commit_touches_a_named_path(self) -> None:
        finding = self.finding("RC-001", artifact="docs/missing.md:3")
        finding["required_change"] = ("Add the missing statement to a.txt "
                                      "(the file that must change).")
        doc = self.write_findings("review-contract", [finding])
        disp = self.write_dispositions(
            [{"id": "RC-001", "disposition": "fixed",
              "fix_sha": self.fix_a, "evidence": "a.txt gained the statement"}])
        _, data, _ = self.render_ok(
            findings=[("review-contract", str(doc))],
            dispositions=str(disp))
        self.assertEqual(data["dispositions"][0]["fix_sha"], self.fix_a)

    def test_fixed_refused_when_no_named_path_is_touched(self) -> None:
        finding = self.finding("RC-001", artifact="docs/missing.md:3")
        finding["required_change"] = "Add the missing statement to absent.md."
        doc = self.write_findings("review-contract", [finding])
        disp = self.write_dispositions(
            [{"id": "RC-001", "disposition": "fixed",
              "fix_sha": self.fix_a, "evidence": "unrelated commit"}])
        proc = self.render(findings=[("review-contract", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not touch", proc.stderr)


if __name__ == "__main__":
    unittest.main()