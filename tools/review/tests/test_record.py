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

SCHEMA = "review.record.v2"


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
        # must stay byte-identical).  It lives INSIDE the repository: records
        # store repo-relative paths and ``check`` confines every stored path
        # to the repo, so a default asset must resolve there.
        self.gate_path = self.repo / "gate-lint.txt"
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
        """Build a review package; returns (path, sha256).

        The default output lives inside the repository so the record stores
        a repo-relative package path that ``check`` can resolve.
        """
        self.pkg = out or (self.repo / "package.md")
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
        # Default location is inside the repo (repo-relative stored path).
        out = path or (self.repo / f"findings-{reviewer}.yaml")
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

    def write_stop_findings(self, reviewer: str,
                            rule: str = "package-hash-mismatch",
                            path: Path | None = None) -> Path:
        """A shape-B findings document: the reviewer STOPPED."""
        if not hasattr(self, "pkg_sha"):
            self.build_package()
        out = path or (self.repo / f"findings-{reviewer}-stop.yaml")
        doc = {
            "schema": "review.findings.v1",
            "review": {
                "reviewer": reviewer,
                "rubric_version": f"{reviewer}-v1",
                "package_sha256": self.pkg_sha,
                "base_sha": self.base,
                "head_sha": self.head,
                "stop": {"rule": rule,
                         "detail": f"reviewer stopped: {rule}"},
            },
        }
        out.write_text(yamlish_dumps(doc), encoding="utf-8")
        return out

    def render(self, **overrides) -> subprocess.CompletedProcess:
        """Run render with a full default asset set; overrides replace keys:
        story, pr, base, head, package, findings (list of (role, path)),
        dispositions, gates (list of str), loop, loop_cap, prior (list),
        out_dir, extra (list of str), models (dict role->model)."""
        if "package" not in overrides:
            overrides["package"] = str(self.build_package()[0])
        panel = overrides.pop("panel", None)
        if "findings" not in overrides:
            doc = self.write_findings("review-adversary",
                                      [self.finding("RA-001")])
            overrides["findings"] = [("review-adversary", str(doc))]
        if "dispositions" not in overrides:
            overrides["dispositions"] = str(self.write_dispositions(
                [{"id": "RA-001@test-model-v1", "disposition": "fixed",
                  "fix_sha": self.fix_a}]))
        models = overrides.pop("models", None)
        if models is None:
            # Auto-models cover every findings role that is a known role;
            # unknown roles must reach collect_reviewers' own refusal.
            models = {role: "test-model-v1" for role, _ in
                      overrides["findings"] if role in record.ROLES}
        kw = {
            "repo": str(self.repo),
            "story": "S35A",
            "pr": 42,
            "base": self.base,
            "head": self.head,
            "dispositions": overrides["dispositions"],
            "findings": overrides["findings"],
            "models": models,
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
            if panel is None:
                args += ["--findings", f"{role}={path}"]
        if panel is not None:
            for role, model, path in panel:
                args += ["--findings", f"{role}@{model}={path}"]
        else:
            for role, model in sorted(kw["models"].items()):
                args += ["--model", f"{role}={model}"]
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
        path = self.repo / f"gate-{name}.txt"
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
        self.assertEqual(data["lane"], {"name": "review-lane-v1", "version": 2})
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
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002@test-model-v1", "disposition": "rejected",
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
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002@test-model-v1", "disposition": "rejected",
             "reason": "no", "evidence": "log"},
            {"id": "RA-003@test-model-v1", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/7"},
        ])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["open_findings"],
                         ["RA-002@test-model-v1", "RA-003@test-model-v1"])
        self.assertEqual(data["undispositioned"], [])

    def test_dispositions_recorded_with_all_keys(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1"),
        ])
        disp = self.write_dispositions([
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002@test-model-v1", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/1"},
        ])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["dispositions"], [
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a,
             "reason": None, "issue": None, "evidence": None},
            {"id": "RA-002@test-model-v1", "disposition": "deferred", "fix_sha": None,
             "reason": None,
             "issue": "https://github.com/org/repo/issues/1",
             "evidence": None},
        ])

    def test_rejected_missing_reason_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "rejected",
              "evidence": "only evidence"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing reason/evidence: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_rejected_missing_evidence_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "rejected",
              "reason": "only reason"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing reason/evidence: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_deferred_bad_issue_url_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "deferred",
              "issue": "https://gitlab.com/r/i/1"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad issue URL: RA-001", proc.stderr)
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "deferred"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad issue URL: RA-001", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_deferred_valid_issue_urls_ok(self) -> None:
        url = "https://github.com/openai/hello/issues/123"
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "deferred", "issue": url}])
        _, data, md = self.render_ok(dispositions=str(disp))
        self.assertEqual(data["dispositions"][0]["issue"], url)
        self.assertEqual(data["open_findings"], ["RA-001@test-model-v1"])
        self.assertIn(f"issue: {url}", md)

    def test_fixed_unresolvable_sha_refused(self) -> None:
        proc = self.render(dispositions=str(self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": "0123456789abcdef"}])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad fixed sha:", proc.stderr)
        self.assertIn("not a commit", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_sha_bad_format_refused(self) -> None:
        for bad in ("xyz1234", "ABCDEF1", "abc", "f" * 41):
            proc = self.render(dispositions=str(self.write_dispositions(
                [{"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": bad}])))
            self.assertEqual(proc.returncode, 1, bad)
            self.assertIn("review record: bad fixed sha:", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_sha_not_touching_artifact_refused(self) -> None:
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
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
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
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

    def test_package_diff_reference_touch_rule_enforced(self) -> None:
        """package:diff@ refs follow the same touch rule as plain paths:
        strip prefix + line suffix; the fix commit must touch the path."""
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001",
                                                artifact="package:diff@a.txt:4")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_other}])
        proc = self.render(findings=[("review-adversary", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad fixed sha:", proc.stderr)
        self.assertIn("does not touch a.txt", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_package_diff_reference_touched_by_fix_accepted(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001",
                                                artifact="package:diff@a.txt:4")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        _, data, _ = self.render_ok(findings=[("review-adversary", str(doc))],
                                    dispositions=str(disp))
        self.assertEqual(data["open_findings"], [])
        self.assertEqual(data["dispositions"][0]["fix_sha"], self.fix_a)

    def test_package_diff_reference_withheld_refused(self) -> None:
        """The withheld rule applies to package:diff@ refs too: the prefix
        is no longer an exemption from withheld checks."""
        withheld = self.dir / "withheld.txt"
        withheld.write_text("a.txt\n", encoding="utf-8")
        package, _ = self.build_package("--withheld", str(withheld))
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001",
                                                artifact="package:diff@a.txt:4")])
        proc = self.render(package=str(package),
                           findings=[("review-adversary", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: withheld artifact: RA-001", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

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
        self.assertIn("review record: duplicate reviewer role/model: "
                      "review-adversary", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_unreadable_findings_doc_refused(self) -> None:
        missing = self.dir / "findings-missing.yaml"
        proc = self.render(findings=[("review-adversary", str(missing))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unreadable findings doc:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_refuses_stopped_review(self) -> None:
        """F6: a stop-block findings document must not render as an empty
        findings list and thereby APPROVE a panel that never reviewed."""
        doc = self.write_stop_findings("review-security",
                                       "package-hash-mismatch")
        proc = self.render(findings=[("review-security", str(doc))])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: stopped review: review-security "
                      "(package-hash-mismatch)", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_stopped_review_lens(self) -> None:
        """F6: check re-parses reviewer documents with the same refusal."""
        out, data, _ = self.render_ok()
        role = "review-adversary"
        doc_path = self.repo / f"findings-{role}.yaml"
        stop_doc = self.write_stop_findings(role, path=doc_path)
        for reviewer in data["reviewers"]:
            if reviewer["role"] == role:
                reviewer["document_sha256"] = hashlib.sha256(
                    stop_doc.read_bytes()).hexdigest()
        (out / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: stopped review: review-adversary "
                      "(package-hash-mismatch)", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_undispositioned_findings_refused(self) -> None:
        doc = self.write_findings("review-adversary", [
            self.finding("RA-001", "minor"),
            self.finding("RA-002", "info", artifact="extra/new.txt:1")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a}])
        proc = self.render(findings=[("review-adversary", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: undispositioned findings: RA-002",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_disposition_unknown_id_refused(self) -> None:
        disp = self.write_dispositions([
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RS-999@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
        ])
        proc = self.render(dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: disposition for unknown id: RS-999",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_duplicate_disposition_refused(self) -> None:
        disp = self.write_dispositions([
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
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
            "evidence_path": record.record_path(str(gate), str(self.repo)),
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
            {"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
            {"id": "RA-002@test-model-v1", "disposition": "rejected",
             "reason": "not now", "evidence": "log 42"},
            {"id": "RA-003@test-model-v1", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/9"}])
        _, _, md = self.render_ok(findings=[("review-adversary", str(doc))],
                                  dispositions=str(disp))
        self.assertIn(f"| RA-001@test-model-v1 | review-adversary | test-model-v1 | minor | "
                      f"behavior | a.txt:2 | 1/1 | fixed | fixed: {self.fix_a} |",
                      md)
        self.assertIn("| RA-002@test-model-v1 | review-adversary | test-model-v1 | info | "
                      "behavior | extra/new.txt:1 | 1/1 | rejected | "
                      "reason: not now evidence: log 42 |", md)
        self.assertIn("| RA-003@test-model-v1 | review-adversary | test-model-v1 | info | "
                      "behavior | src/app.py:1 | 1/1 | deferred | issue: "
                      "https://github.com/org/repo/issues/9 |", md)

    def test_external_source_and_lens(self) -> None:
        doc = self.write_findings("external", [self.finding("EX-001", "info",
                                                           artifact="a.txt:1")],
                                  path=self.repo / "f-ex.yaml")
        contract = self.write_findings("review-contract", [],
                                       path=self.repo / "f-c.yaml")
        disp = self.write_dispositions(
            [{"id": "EX-001@test-model-v1", "disposition": "rejected",
              "reason": "legacy", "evidence": "wire log"}])
        _, data, _ = self.render_ok(
            findings=[("external", str(doc)), ("review-contract", str(contract))],
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
            {"id": "RC-001@test-model-v1", "disposition": "rejected", "reason": "no",
             "evidence": "e"},
            {"id": "RS-001@test-model-v1", "disposition": "fixed", "fix_sha": self.fix_a},
        ])
        _, data, md = self.render_ok(
            findings=[("review-security", str(security)),
                      ("review-contract", str(contract))],
            dispositions=str(disp))
        self.assertEqual([r["role"] for r in data["reviewers"]],
                         ["review-contract", "review-security"])
        self.assertEqual([f["id"] for f in data["findings"]],
                         ["RC-001@test-model-v1", "RS-001@test-model-v1"])
        self.assertIn("| RC-001@test-model-v1 | review-contract |", md)
        self.assertIn("| RS-001@test-model-v1 | review-security |", md)

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

    # -- reviewer models (RC-004) ------------------------------------------

    def test_reviewer_models_recorded_in_json_and_markdown(self) -> None:
        """Dual panel: two models on one lens are two reviewers, both named,
        with namespaced ids and a shared location (agreement 2)."""
        a = self.write_findings(
            "review-contract",
            [dict(self.finding("RC-001", "info", artifact="src/app.py:1",
                               dimension="contract_fidelity"),
                  evidence="Model A: the contract names a file the diff omits.")],
            path=self.repo / "findings-contract-a.yaml")
        b = self.write_findings(
            "review-contract",
            [dict(self.finding("RC-001", "info", artifact="src/app.py:1",
                               dimension="contract_fidelity"),
                  evidence="Model B: same omission, read from the AC text.")],
            path=self.repo / "findings-contract-b.yaml")
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-001@deepseek-v4-pro-0813", "disposition": "rejected",
             "reason": "no", "evidence": "e"}])
        _, data, md = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        self.assertEqual({r["model"] for r in data["reviewers"]},
                         {"qwen3.8-max", "deepseek-v4-pro-0813"})
        self.assertEqual({f["id"] for f in data["findings"]},
                         {"RC-001@qwen3.8-max",
                          "RC-001@deepseek-v4-pro-0813"})
        self.assertTrue(all(f["agreement"] == 2 for f in data["findings"]))
        self.assertEqual(len({f["location_key"] for f in data["findings"]}), 1)
        self.assertIn("| role | model | rubric | document |", md)
        self.assertIn("| review-contract | qwen3.8-max | "
                      "review-contract-v1 |", md)
        self.assertIn("| review-contract | deepseek-v4-pro-0813 | "
                      "review-contract-v1 |", md)
        self.assertIn("models: deepseek-v4-pro-0813, qwen3.8-max", md)

    # -- dual panel (S3.5c) -------------------------------------------------

    def dual_docs(self, findings_a=None, findings_b=None, role="review-contract"):
        a = self.write_findings(role, findings_a if findings_a is not None else [],
                                path=self.repo / f"findings-{role}-a.yaml")
        b = self.write_findings(role, findings_b if findings_b is not None else [],
                                path=self.repo / f"findings-{role}-b.yaml")
        return a, b

    def test_dual_panel_asymmetric_refused(self) -> None:
        a = self.write_findings("review-contract", [],
                                path=self.repo / "findings-asym-a.yaml")
        b = self.write_findings("review-adversary", [],
                                path=self.repo / "findings-asym-b.yaml")
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-adversary", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: incomplete panel:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_dual_panel_duplicate_slot_refused(self) -> None:
        a, _ = self.dual_docs()
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "qwen3.8-max", str(a))],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate reviewer role/model: "
                      "review-contract@qwen3.8-max", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_mixed_findings_forms_refused(self) -> None:
        a, b = self.dual_docs()
        proc = self.render(
            findings=[("review-contract", str(a))],
            extra=["--findings", f"review-contract@qwen3.8-max={b}"])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: mixed --findings forms:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_dual_panel_check_rejects_tampered_agreement(self) -> None:
        a, b = self.dual_docs(
            [dict(self.finding("RC-001", "major", artifact="a.txt:2"),
                  evidence="Model A: the guard is missing.")],
            [dict(self.finding("RC-001", "major", artifact="a.txt:2"),
                  evidence="Model B: guard absent on the same path.")])
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "fixed",
             "fix_sha": self.fix_a},
            {"id": "RC-001@deepseek-v4-pro-0813", "disposition": "fixed",
             "fix_sha": self.fix_a}])
        out, data, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        self.assertTrue(all(f["agreement"] == 2 for f in data["findings"]))

        def weaken(data):
            data["findings"][0]["agreement"] = 1

        self.rewrite_record(out, weaken)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings mismatch:", proc.stderr)
        self.assertIn("agreement", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_refuses_empty_panel(self) -> None:
        proc = self.render(findings=[], models={},
                           dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: empty panel:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_dual_panel_refuses_duplicate_document(self) -> None:
        shared = self.write_findings(
            "review-contract",
            [self.finding("RC-001", "minor", artifact="a.txt:2")],
            path=self.repo / "findings-shared.yaml")
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(shared)),
                   ("review-contract", "deepseek-v4-pro-0813", str(shared))],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate findings document:",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_location_key_separates_artifacts_and_dimensions(self) -> None:
        a, b = self.dual_docs(
            [self.finding("RC-001", "minor", artifact="a.txt:2"),
             self.finding("RC-002", "minor", artifact="src/app.py:1"),
             self.finding("RC-003", "minor", artifact="a.txt:2",
                          dimension="docs_process")],
            [self.finding("RC-001", "minor", artifact="a.txt:2")])
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-002@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-003@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-001@deepseek-v4-pro-0813", "disposition": "rejected",
             "reason": "no", "evidence": "e"}])
        _, data, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        by_id = {f["id"]: f for f in data["findings"]}
        self.assertEqual(by_id["RC-001@qwen3.8-max"]["location_key"],
                         by_id["RC-001@deepseek-v4-pro-0813"]["location_key"])
        self.assertNotEqual(by_id["RC-002@qwen3.8-max"]["location_key"],
                            by_id["RC-001@qwen3.8-max"]["location_key"])
        self.assertNotEqual(by_id["RC-003@qwen3.8-max"]["location_key"],
                            by_id["RC-001@qwen3.8-max"]["location_key"])
        self.assertEqual(by_id["RC-001@qwen3.8-max"]["agreement"], 2)
        self.assertEqual(by_id["RC-002@qwen3.8-max"]["agreement"], 1)
        self.assertEqual(by_id["RC-003@qwen3.8-max"]["agreement"], 1)

    def test_check_refuses_asymmetric_panel_in_stored_record(self) -> None:
        """The check-side symmetry guard is exercised, not just the render one."""
        a_c = self.write_findings("review-contract", [],
                                  path=self.repo / "f-ac.yaml")
        a_a = self.write_findings("review-adversary", [],
                                  path=self.repo / "f-aa.yaml")
        b_c = self.write_findings("review-contract", [],
                                  path=self.repo / "f-bc.yaml")
        b_a = self.write_findings("review-adversary", [],
                                  path=self.repo / "f-ba.yaml")
        out, _, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a_c)),
                   ("review-adversary", "qwen3.8-max", str(a_a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b_c)),
                   ("review-adversary", "deepseek-v4-pro-0813", str(b_a))],
            dispositions=str(self.write_dispositions([])))

        def drop_one_lens(data):
            data["reviewers"] = [
                r for r in data["reviewers"]
                if not (r["role"] == "review-adversary"
                        and r["model"] == "deepseek-v4-pro-0813")]

        self.rewrite_record(out, drop_one_lens)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: incomplete panel:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_model_alias_whitespace_refused(self) -> None:
        a, b = self.dual_docs()
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "qwen3.8-max ", str(b))],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate reviewer role/model: "
                      "review-contract@qwen3.8-max", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_model_flag_with_inline_form_refused(self) -> None:
        a, _ = self.dual_docs()
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(a))],
            extra=["--model", "review-contract=qwen3.8-max"],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: --model is not accepted with "
                      "ROLE@MODEL=FILE", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_single_model_inline_panel_is_namespaced(self) -> None:
        doc = self.write_findings(
            "review-contract",
            [self.finding("RC-001", "minor", artifact="a.txt:2")],
            path=self.repo / "findings-solo.yaml")
        disp = self.write_dispositions([
            {"id": "RC-001@solo-model", "disposition": "rejected",
             "reason": "no", "evidence": "e"}])
        _, data, _ = self.render_ok(
            panel=[("review-contract", "solo-model", str(doc))],
            dispositions=str(disp))
        self.assertEqual([f["id"] for f in data["findings"]],
                         ["RC-001@solo-model"])
        self.assertEqual(data["findings"][0]["agreement"], 1)

    def test_same_location_pair_dispositioned_separately(self) -> None:
        a, b = self.dual_docs(
            [dict(self.finding("RC-001", "minor", artifact="a.txt:2"),
                  evidence="Model A: the guard is missing.")],
            [dict(self.finding("RC-001", "minor", artifact="a.txt:2"),
                  evidence="Model B: guard absent on the same path.")])
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "not a defect here", "evidence": "caller guards it"},
            {"id": "RC-001@deepseek-v4-pro-0813", "disposition": "deferred",
             "issue": "https://github.com/org/repo/issues/11"}])
        _, data, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        by_id = {d["id"]: d for d in data["dispositions"]}
        self.assertEqual(by_id["RC-001@qwen3.8-max"]["disposition"], "rejected")
        self.assertEqual(by_id["RC-001@deepseek-v4-pro-0813"]["disposition"],
                         "deferred")

    def test_union_verdict_from_one_model_major(self) -> None:
        a, b = self.dual_docs(
            [self.finding("RC-001", "major", artifact="a.txt:2")], [])
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "wrong", "evidence": "checked the caller"}])
        out, data, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        self.assertEqual(data["verdict"]["decision"], "CHANGES_REQUESTED")
        self.assertEqual(data["verdict"]["majors"], 1)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_external_document_does_not_break_symmetry(self) -> None:
        a, b = self.dual_docs()
        ext = self.write_findings("external", [],
                                  path=self.repo / "f-ext.yaml")
        proc = self.render(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b)),
                   ("external", "qwen3.8-max", str(ext))],
            dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_check_refuses_duplicate_role_in_v1_record(self) -> None:
        out, data, _ = self.render_ok(
            findings=[("review-contract", str(self.write_findings(
                "review-contract", [], path=self.repo / "f-empty.yaml")))],
            dispositions=str(self.write_dispositions([])))
        data["schema"] = "review.record.v1"
        data["lane"]["version"] = 1
        reviewer = dict(data["reviewers"][0], model="second-model")
        data["reviewers"].append(reviewer)
        data["reviewers"].sort(key=lambda r: (r["role"], r["model"]))
        (out / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings doc mismatch: duplicate role",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_external_only_panel_refused(self) -> None:
        doc = self.write_findings("external", [],
                                  path=self.repo / "f-ext-only.yaml")
        proc = self.render(findings=[("external", str(doc))],
                           models={"external": "legacy-mapper-v0"},
                           dispositions=str(self.write_dispositions([])))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: incomplete panel: no internal lens "
                      "coverage", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_external_document_does_not_shift_agreement_ratio(self) -> None:
        a, b = self.dual_docs(
            [dict(self.finding("RC-001", "minor", artifact="a.txt:2"),
                  evidence="Model A: the guard is missing.")],
            [dict(self.finding("RC-001", "minor", artifact="a.txt:2"),
                  evidence="Model B: guard absent on the same path.")])
        ext = self.write_findings(
            "external", [self.finding("EX-001", "info", artifact="a.txt:2")],
            path=self.repo / "f-ext-ratio.yaml")
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-001@deepseek-v4-pro-0813", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "EX-001@qwen3.8-max", "disposition": "rejected",
             "reason": "legacy", "evidence": "wire log"}])
        _, _, md = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b)),
                   ("external", "qwen3.8-max", str(ext))],
            dispositions=str(disp))
        self.assertIn("models: deepseek-v4-pro-0813, qwen3.8-max", md)
        self.assertIn("| RC-001@qwen3.8-max | review-contract | qwen3.8-max | "
                      "minor | behavior | a.txt:2 | 2/2 |", md)
        self.assertIn("| EX-001@qwen3.8-max | external | qwen3.8-max | info | "
                      "behavior | a.txt:2 | — |", md)

    def test_check_rejects_duplicate_document_in_stored_record(self) -> None:
        a, b = self.dual_docs(
            [self.finding("RC-001", "minor", artifact="a.txt:2")], [])
        disp = self.write_dispositions([
            {"id": "RC-001@qwen3.8-max", "disposition": "rejected",
             "reason": "no", "evidence": "e"}])
        out, data, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(disp))
        # Point the second slot at the first slot's document and keep the
        # stored hash consistent with that document.
        first = next(r for r in data["reviewers"]
                     if r["model"] == "qwen3.8-max")
        second = next(r for r in data["reviewers"]
                      if r["model"] == "deepseek-v4-pro-0813")

        def repoint(data):
            for reviewer in data["reviewers"]:
                if reviewer["role"] == second["role"] and \
                        reviewer["model"] == second["model"]:
                    reviewer["document"] = first["document"]
                    reviewer["document_sha256"] = first["document_sha256"]

        self.rewrite_record(out, repoint)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: duplicate findings document:",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_rejects_padded_model_label(self) -> None:
        a, b = self.dual_docs()
        out, _, _ = self.render_ok(
            panel=[("review-contract", "qwen3.8-max", str(a)),
                   ("review-contract", "deepseek-v4-pro-0813", str(b))],
            dispositions=str(self.write_dispositions([])))

        def pad(data):
            for reviewer in data["reviewers"]:
                if reviewer["model"] == "deepseek-v4-pro-0813":
                    reviewer["model"] = "qwen3.8-max "

        self.rewrite_record(out, pad)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_agreement_counts_models_not_findings(self) -> None:
        doc = self.write_findings(
            "review-contract",
            [dict(self.finding("RC-001", "minor", artifact="a.txt:2"),
                  evidence="first hit on this line"),
             dict(self.finding("RC-002", "minor", artifact="a.txt:2"),
                  evidence="second hit on the same line")],
            path=self.repo / "f-same-model.yaml")
        disp = self.write_dispositions([
            {"id": "RC-001@test-model-v1", "disposition": "rejected",
             "reason": "no", "evidence": "e"},
            {"id": "RC-002@test-model-v1", "disposition": "rejected",
             "reason": "no", "evidence": "e"}])
        _, data, _ = self.render_ok(
            findings=[("review-contract", str(doc))], dispositions=str(disp))
        self.assertEqual(len({f["location_key"] for f in data["findings"]}), 1)
        self.assertTrue(all(f["agreement"] == 1 for f in data["findings"]))

    def test_check_refuses_v1_record_with_namespaced_ids(self) -> None:
        out, data, _ = self.render_ok()
        data["schema"] = "review.record.v1"
        data["lane"]["version"] = 1
        (out / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: schema mismatch: v1 record carries "
                      "namespaced finding id", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_accepts_legacy_v1_record(self) -> None:
        """A stored review.record.v1 record stays verifiable under v1 rules."""
        out, data, _ = self.render_ok()
        data["schema"] = "review.record.v1"
        data["lane"]["version"] = 1
        for finding in data["findings"]:
            finding["id"] = finding["id"].split("@", 1)[0]
            finding.pop("model", None)
            finding.pop("location_key", None)
            finding.pop("agreement", None)
        for disposition in data["dispositions"]:
            disposition["id"] = disposition["id"].split("@", 1)[0]
        data["open_findings"] = [fid.split("@", 1)[0]
                                 for fid in data["open_findings"]]
        (out / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (out / "REVIEW-42.md").write_text(record.render_markdown(data),
                                          encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("review record: OK", proc.stdout)

    def test_external_reviewer_model_recorded(self) -> None:
        doc = self.write_findings("external", [self.finding("EX-001", "info",
                                                           artifact="a.txt:1")],
                                  path=self.repo / "f-ex2.yaml")
        contract = self.write_findings("review-contract", [],
                                       path=self.repo / "f-c2.yaml")
        disp = self.write_dispositions(
            [{"id": "EX-001@legacy-mapper-v0", "disposition": "rejected",
              "reason": "legacy", "evidence": "wire log"}])
        _, data, _ = self.render_ok(findings=[("external", str(doc)),
                                              ("review-contract", str(contract))],
                                    models={"external": "legacy-mapper-v0",
                                            "review-contract": "contract-map-v0"},
                                    dispositions=str(disp))
        self.assertEqual(data["reviewers"][0]["model"], "legacy-mapper-v0")

    def test_render_missing_reviewer_model_refused(self) -> None:
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001", "minor")])
        proc = self.render(findings=[("review-adversary", str(doc))],
                           models={})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: missing reviewer model: "
                      "review-adversary", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_unknown_model_role_refused(self) -> None:
        proc = self.render(models={"review-contract": "m",
                                    "mystery": "m"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unknown model role: mystery",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_model_role_without_findings_refused(self) -> None:
        proc = self.render(models={"review-security": "m"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: model role without findings doc: "
                      "review-security", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_reviewer_without_model(self) -> None:
        out, _, _ = self.render_ok()

        def strip_model(data):
            del data["reviewers"][0]["model"]

        self.rewrite_record(out, strip_model)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: invalid record:", proc.stderr)
        self.assertIn("model must be a non-empty string", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

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
        (self.repo / data["gate_results"][0]["evidence_path"]).unlink()
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: gate evidence path missing: lint",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_detects_removed_findings_doc(self) -> None:
        out, data, _ = self.render_ok()
        (self.repo / data["reviewers"][0]["document"]).unlink()
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

    # -- S3.5b T1: rebuildable records via package manifests -----------------

    def render_with_manifest(self, manifest_path: Path | None = None,
                             build_extra: list[str] | None = None,
                             ) -> tuple[Path, dict, Path]:
        """Build package+manifest, render with --package-manifest.

        Returns (out_dir, review.json data, manifest path).
        """
        manifest = manifest_path or (self.dir / "manifest.json")
        pkg, _ = self.build_package("--manifest-out", str(manifest),
                                    *(build_extra or []))
        out, data, _ = self.render_ok(
            package=str(pkg),
            extra=["--package-manifest", str(manifest)])
        return out, data, manifest

    def test_render_with_manifest_stores_it_in_record(self) -> None:
        out, data, manifest = self.render_with_manifest()
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["package"]["manifest"], expected)
        self.assertEqual(expected["base"], self.base)
        self.assertEqual(expected["head"], self.head)
        self.assertEqual(data["package"]["sha256"], self.pkg_sha)
        # With the package file present, check still passes.
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_check_rebuilds_from_manifest_without_package_file(self) -> None:
        """Fresh-clone case, via subprocess CLI: the package file is gone
        and check still exits 0 by rebuilding from the manifest."""
        out, _, _ = self.render_with_manifest()
        self.pkg.unlink()  # the package was never committed
        self.assertFalse(self.pkg.exists())
        proc = subprocess.run(
            [sys.executable, "tools/review/record.py", "check",
             "--record", str(out), "--repo", str(self.repo)],
            capture_output=True, text=True, env=self.env,
            cwd=str(REVIEW_DIR.parents[1]))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record: OK (1 finding(s),", proc.stdout)

    def test_check_tampered_manifest_include_rebuild_mismatch(self) -> None:
        out, data, _ = self.render_with_manifest()
        self.pkg.unlink()  # force the rebuild path

        def tamper(record):
            # A scope that still builds (extra/new.txt changed) but yields
            # different package bytes than the recorded full diff.
            record["package"]["manifest"]["include"] = ["extra"]

        self.rewrite_record(out, tamper)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package rebuild mismatch: recorded "
                      f"{data['package']['sha256']}, rebuilt ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_dirty_package_file_not_silently_ignored(self) -> None:
        """Manifest present AND a stale package file at the recorded path:
        the dirty file must be refused, not skipped."""
        out, data, _ = self.render_with_manifest()
        self.pkg.write_bytes(self.pkg.read_bytes() + b"X")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package file mismatch: recorded "
                      f"{data['package']['sha256']}, file ", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_without_manifest_missing_package_still_refused(self) -> None:
        out, _, _ = self.render_ok()  # no manifest
        self.assertNotIn("manifest", json.loads(
            (out / "review.json").read_text(encoding="utf-8"))["package"])
        self.pkg.unlink()
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: package unreadable:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_v1_record_carrying_manifest_refused(self) -> None:
        out, data, _ = self.render_with_manifest()
        data["schema"] = "review.record.v1"
        data["lane"]["version"] = 1
        for finding in data["findings"]:
            finding["id"] = finding["id"].split("@", 1)[0]
            finding.pop("model", None)
            finding.pop("location_key", None)
            finding.pop("agreement", None)
        for disposition in data["dispositions"]:
            disposition["id"] = disposition["id"].split("@", 1)[0]
        data["open_findings"] = [fid.split("@", 1)[0]
                                 for fid in data["open_findings"]]
        (out / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("schema mismatch: v1 record carries "
                      "package.manifest", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_withheld_enforcement_derives_from_rebuilt_package(self) -> None:
        withheld = self.dir / "withheld.txt"
        withheld.write_text("extra/new.txt\n", encoding="utf-8")
        out, data, _ = self.render_with_manifest(
            build_extra=["--withheld", str(withheld)])
        self.pkg.unlink()  # rebuild path only

        def point_at_withheld(record):
            record["findings"][0]["artifact_reference"] = "extra/new.txt:1"

        self.rewrite_record(out, point_at_withheld)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: withheld artifact: "
                      "RA-001@test-model-v1", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_refuses_manifest_with_wrong_range(self) -> None:
        manifest = self.dir / "manifest-bad.json"
        pkg, _ = self.build_package("--manifest-out", str(manifest))
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["head"] = self.fix_a  # not the record head
        manifest.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        proc = self.render(package=str(pkg),
                           extra=["--package-manifest", str(manifest)])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: invalid package manifest:", proc.stderr)
        self.assertIn(f"head {self.fix_a} != record head {self.head}",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_render_refuses_malformed_manifest(self) -> None:
        pkg, _ = self.build_package()
        manifest = self.dir / "manifest-bad.json"
        manifest.write_text("{not json", encoding="utf-8")
        proc = self.render(package=str(pkg),
                           extra=["--package-manifest", str(manifest)])
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: invalid package manifest:", proc.stderr)
        # Unknown key / bad version shapes are refused too.
        for mutate in (lambda d: d.update(extra_key=1),
                       lambda d: d.update(version=2)):
            base = json.loads((self.dir / "m2.json").read_text(
                encoding="utf-8")) if (self.dir / "m2.json").exists() else None
            if base is None:
                m2 = self.dir / "m2.json"
                _, _ = self.build_package("--manifest-out", str(m2),
                                          out=self.repo / "package.md")
                base = json.loads(m2.read_text(encoding="utf-8"))
            doc = json.loads(json.dumps(base))
            mutate(doc)
            manifest.write_text(json.dumps(doc, indent=2, sort_keys=True),
                                encoding="utf-8")
            proc = self.render(package=str(pkg),
                               extra=["--package-manifest", str(manifest)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("review record: invalid package manifest:",
                          proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    # -- P2: check re-derives the record -----------------------------------

    def rewrite_record(self, out: Path, mutate) -> None:
        """Apply ``mutate(data)`` to out/review.json and write it back."""
        record_path = out / "review.json"
        data = json.loads(record_path.read_text(encoding="utf-8"))
        mutate(data)
        record_path.write_text(json.dumps(data, indent=2, sort_keys=True),
                               encoding="utf-8")

    def test_check_refuses_dropped_finding(self) -> None:
        out, _, _ = self.render_ok()

        def drop(data):
            data["findings"] = []
            data["dispositions"] = []
            data["open_findings"] = []

        self.rewrite_record(out, drop)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings mismatch:", proc.stderr)
        self.assertIn("0 finding(s) stored but documents derive 1",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_weakened_severity(self) -> None:
        out, _, _ = self.render_ok()

        def weaken(data):
            data["findings"][0]["severity"] = "info"

        self.rewrite_record(out, weaken)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: findings mismatch:", proc.stderr)
        self.assertIn("severity stored 'info' != document 'minor'",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_removed_disposition(self) -> None:
        out, _, _ = self.render_ok()

        def remove(data):
            data["dispositions"] = []

        self.rewrite_record(out, remove)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: undispositioned findings: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_wrong_verdict_decision(self) -> None:
        out, _, _ = self.render_ok()  # only minors -> computed APPROVED

        def flip(data):
            data["verdict"]["decision"] = "CHANGES_REQUESTED"

        self.rewrite_record(out, flip)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: verdict mismatch:", proc.stderr)
        self.assertIn("decision stored 'CHANGES_REQUESTED' "
                      "!= computed 'APPROVED'", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_wrong_verdict_counts(self) -> None:
        out, _, _ = self.render_ok()

        def tamper(data):
            data["verdict"]["minors"] = 7

        self.rewrite_record(out, tamper)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: verdict mismatch:", proc.stderr)
        self.assertIn("minors stored 7 != computed 1", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_nonempty_undispositioned_list(self) -> None:
        out, _, _ = self.render_ok()

        def tamper(data):
            data["undispositioned"] = ["RA-001"]

        self.rewrite_record(out, tamper)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: undispositioned findings: RA-001",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    # -- P5: stored paths are confined at check ----------------------------

    def test_check_refuses_stored_absolute_path(self) -> None:
        out, _, _ = self.render_ok()

        def absolutize(data):
            data["package"]["path"] = "/etc/passwd"

        self.rewrite_record(out, absolutize)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unconfined record path: /etc/passwd",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_check_refuses_stored_escaping_path(self) -> None:
        out, _, _ = self.render_ok()

        def escape(data):
            data["package"]["path"] = "../../secret.md"

        self.rewrite_record(out, escape)
        proc = self.check_proc(out)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: unconfined record path: ../../secret.md",
                      proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    # -- P6: prior round paths stay repo-relative ---------------------------

    def test_prior_round_path_stored_repo_relative(self) -> None:
        loop1_dir = self.repo / "records" / "loop1"
        _, _, _ = self.render_ok(out_dir=str(loop1_dir))
        _, second_data, second_md = self.render_ok(
            out_dir=str(self.dir / "record2"), loop=2,
            prior=[str(loop1_dir)])
        self.assertEqual(second_data["prior_rounds"][0]["path"],
                         record.record_path(str(loop1_dir), str(self.repo)))
        self.assertEqual(second_data["prior_rounds"][0]["path"],
                         "records/loop1")
        self.assertNotIn(str(self.repo), second_md)

    # -- P7: the touch rule is not widened by required_change ----------------

    def test_fixed_refused_when_artifact_exists_and_only_named_path_touched(
            self) -> None:
        """P7: when the cited artifact exists, a commit touching only a path
        named in required_change must be refused."""
        (self.repo / "src" / "app.py").write_text(
            "def main(): pass\n# repair\n", encoding="utf-8")
        self.git("add", "src/app.py")
        self.git("commit", "-q", "-m", "repair src/app.py")
        fix_src = self.rev_parse("HEAD")
        finding = self.finding("RA-001", artifact="a.txt:2")
        finding["required_change"] = "Repair the logic in src/app.py."
        doc = self.write_findings("review-adversary", [finding])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed", "fix_sha": fix_src}])
        proc = self.render(findings=[("review-adversary", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record: bad fixed sha:", proc.stderr)
        self.assertIn("does not touch a.txt", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_fixed_accepted_when_missing_artifact_names_existing_path(
            self) -> None:
        """P7: a finding citing a non-file may name the real target alone."""
        finding = self.finding("RC-001", artifact="docs/missing.md:3")
        finding["required_change"] = ("Add the missing statement to a.txt "
                                      "(the file that must change).")
        doc = self.write_findings("review-contract", [finding])
        disp = self.write_dispositions(
            [{"id": "RC-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        _, data, _ = self.render_ok(
            findings=[("review-contract", str(doc))],
            dispositions=str(disp))
        self.assertEqual(data["dispositions"][0]["fix_sha"], self.fix_a)

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
            [{"id": "RA-001@direct-run-model", "disposition": "fixed", "fix_sha": self.fix_a}])
        gate = self.gate_file()
        out = self.dir / "record-from-repo-cwd"
        proc = self.cli("render", "--repo", str(self.repo), "--story", "S35A",
                        "--pr", "7", "--base", self.base, "--head", self.head,
                        "--package", str(package), "--findings",
                        f"review-adversary={doc}", "--model",
                        "review-adversary=direct-run-model",
                        "--dispositions", str(disp),
                        "--gate", f"lint=0:{gate}", "--out-dir", str(out),
                        cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        check = self.check_proc(out)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn("review record: OK (", check.stdout)

    def test_fixed_accepted_when_commit_touches_a_named_path(self) -> None:
        finding = self.finding("RC-001", artifact="docs/missing.md:3")
        finding["required_change"] = ("Add the missing statement to a.txt "
                                      "(the file that must change).")
        doc = self.write_findings("review-contract", [finding])
        disp = self.write_dispositions(
            [{"id": "RC-001@test-model-v1", "disposition": "fixed",
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
            [{"id": "RC-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a, "evidence": "unrelated commit"}])
        proc = self.render(findings=[("review-contract", str(doc))],
                           dispositions=str(disp))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("does not touch", proc.stderr)

    def validate_doc(self, doc, role="review-adversary", model="test-model-v1",
                     package=None, cwd=None):
        """Run `record.py validate-doc` as a subprocess."""
        return self.cli("validate-doc", "--repo", str(self.repo),
                        "--package", str(package or self.pkg),
                        "--role", role, "--model", model,
                        "--file", str(doc), cwd=cwd)

    def test_validate_doc_ok(self) -> None:
        package, _ = self.build_package()
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout,
            f"review document: OK (review-adversary@test-model-v1, "
            f"1 finding(s), {doc})\n")

    def test_validate_doc_wrong_role_refused(self) -> None:
        package, _ = self.build_package()
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        proc = self.validate_doc(doc, role="review-security", package=package)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("reviewer role mismatch", proc.stderr)
        self.assertIn("review-security", proc.stderr)

    def test_validate_doc_wrong_package_sha_refused(self) -> None:
        package, _ = self.build_package()
        doc = self.write_findings("review-adversary",
                                  [self.finding("RA-001")],
                                  sha="0" * 64)
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("package_sha256", proc.stderr)
        self.assertIn("mismatch", proc.stderr)

    def test_validate_doc_null_findings_refused(self) -> None:
        """`findings: null` must be a refusal, not an empty APPROVED."""
        package, pkg_sha = self.build_package()
        doc = self.repo / "findings-null.yaml"
        doc.write_text(
            "schema: review.findings.v1\n"
            "review:\n"
            "  reviewer: review-adversary\n"
            "  rubric_version: review-adversary-v1\n"
            f"  package_sha256: {pkg_sha}\n"
            f"  base_sha: {self.base}\n"
            f"  head_sha: {self.head}\n"
            "  decision: APPROVED\n"
            "  findings: null\n",
            encoding="utf-8")
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 1)
        # The findings schema itself rejects `findings: null` ("'findings'
        # must be a list") before validate-doc's own guard runs; either way
        # it is a named refusal and never an OK line.
        self.assertIn("review record:", proc.stderr)
        self.assertNotIn("review document: OK", proc.stdout)

    def test_validate_doc_missing_findings_key_refused(self) -> None:
        """A document without a findings key at all is refused too."""
        package, pkg_sha = self.build_package()
        doc = self.repo / "findings-missing.yaml"
        doc.write_text(
            "schema: review.findings.v1\n"
            "review:\n"
            "  reviewer: review-adversary\n"
            "  rubric_version: review-adversary-v1\n"
            f"  package_sha256: {pkg_sha}\n"
            f"  base_sha: {self.base}\n"
            f"  head_sha: {self.head}\n"
            "  decision: APPROVED\n",
            encoding="utf-8")
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record:", proc.stderr)
        self.assertNotIn("review document: OK", proc.stdout)

    def test_validate_doc_empty_model_usage_refusal(self) -> None:
        package, _ = self.build_package()
        doc = self.write_findings("review-adversary", [self.finding("RA-001")])
        proc = self.validate_doc(doc, model="   ", package=package)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--model", proc.stderr)

    def test_validate_doc_malformed_yaml_refused(self) -> None:
        package, _ = self.build_package()
        doc = self.repo / "findings-broken.yaml"
        doc.write_text("review: [unclosed\n  ::: not yaml\n",
                       encoding="utf-8")
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_validate_doc_stop_document_refused(self) -> None:
        package, _ = self.build_package()
        doc = self.write_stop_findings("review-adversary")
        proc = self.validate_doc(doc, package=package)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("stopped review", proc.stderr)

    def test_validate_doc_direct_file_path_invocation(self) -> None:
        """validate-doc must work invoked as a bare file path from any
        working directory (T1 lesson) — a real subprocess, not main()."""
        package, _ = self.build_package()
        doc = self.write_findings("review-contract",
                                  [self.finding("RC-001", artifact="a.txt:2")])
        proc = self.validate_doc(doc, role="review-contract",
                                 model="direct-run-model",
                                 package=package, cwd=self.repo)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertEqual(
            proc.stdout,
            f"review document: OK (review-contract@direct-run-model, "
            f"1 finding(s), {doc})\n")




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
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            repo.mkdir()
            resolved = record.resolve_record_path("a/b.yaml", str(repo))
            self.assertEqual(resolved,
                             os.path.realpath(str(repo / "a" / "b.yaml")))

    def test_resolve_record_path_refuses_absolute(self) -> None:
        repo = str(Path(tempfile.mkdtemp()) / "r")
        with self.assertRaises(SystemExit) as ctx:
            record.resolve_record_path("/etc/passwd", repo)
        self.assertEqual(ctx.exception.code, 1)

    def test_resolve_record_path_refuses_escape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            with self.assertRaises(SystemExit) as ctx:
                record.resolve_record_path("../secret.txt", str(root))
            self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()