#!/usr/bin/env python3
"""Tests for tools/review/ci_gate.py — the review-record required CI check
(S3.5b T3).

CLI-level via subprocess (file-path invocation, real exit codes).  The
records are REAL: rendered with record.py inside a temp git repository
using the minimal fixture-builder pattern copied from
tools/tests/test_review_gate.py (test code is never imported), so the
authority, the head rule and the panel check all run for real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.review import record as record_mod
from tools.review.yamlish import dumps as yamlish_dumps

REVIEW_DIR = Path(__file__).resolve().parents[1]
RECORD_PY = REVIEW_DIR / "record.py"
PACKAGE_PY = REVIEW_DIR / "package.py"
CI_GATE_PY = REVIEW_DIR / "ci_gate.py"


class CiGateFixture(unittest.TestCase):
    """Minimal real-record fixture (pattern copied from
    tools/tests/test_review_gate.py, trimmed): a temp git repo with
    base/head commits, a rendered v2 record inside the repo, and a
    declared panel TOML matching the fixture's one-lens/one-model panel.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s35b-t3-cigate-")
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
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit one")
        self.base = self.rev_parse("HEAD")

        (self.repo / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit two")
        self.head = self.rev_parse("HEAD")

        (self.repo / "a.txt").write_text("alpha\nbeta\ngamma\n",
                                         encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit three: fix a.txt")
        self.fix_a = self.rev_parse("HEAD")
        self.pkg_head = self.head  # default package pin until built

        self.gate_path = self.repo / "gate-lint.txt"
        self.gate_path.write_text("lint: ok\n", encoding="utf-8")

        # declared panel matching render_ok's one-lens/one-model panel
        self.panel = self.write_panel(["review-adversary"],
                                      ["test-model-v1"])

    # -- helpers ------------------------------------------------------------

    def git(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def rev_parse(self, ref: str) -> str:
        return self.git("rev-parse", ref).stdout.strip()

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(RECORD_PY), *args],
                              capture_output=True, text=True, env=self.env)

    def write_panel(self, lenses: list[str], models: list[str],
                    name: str = "panel.toml") -> Path:
        out = self.dir / name
        lines = ["lenses = [" + ", ".join(f'"{l}"' for l in lenses) + "]",
                 "models = [" + ", ".join(f'"{m}"' for m in models) + "]"]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out

    def run_gate(self, head: str, pr: int = 42,
                 panel: Path | None = None,
                 records_root: str = "review") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CI_GATE_PY),
             "--repo", str(self.repo), "--pr", str(pr), "--head", head,
             "--records-root", records_root,
             "--panel", str(panel or self.panel)],
            capture_output=True, text=True, env=self.env)

    def build_package(self, head: str | None = None,
                      name: str = "package.md") -> tuple[Path, str]:
        head = head or self.head
        self.pkg = self.repo / name
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", head, "--out", str(self.pkg)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = head
        return self.pkg, self.pkg_sha

    def write_findings(self, reviewer: str, findings: list[dict],
                       name: str | None = None) -> Path:
        if not hasattr(self, "pkg_sha"):
            self.build_package()
        doc = {
            "schema": "review.findings.v1",
            "review": {
                "reviewer": reviewer,
                "rubric_version": f"{reviewer}-v1",
                "package_sha256": self.pkg_sha,
                "base_sha": self.base,
                "head_sha": self.pkg_head,
                "decision": "CHANGES_REQUESTED" if findings else "APPROVED",
                "findings": findings,
            },
        }
        out = self.repo / (name or f"findings-{reviewer}.yaml")
        out.write_text(yamlish_dumps(doc), encoding="utf-8")
        return out

    def finding(self, fid: str, severity: str = "minor") -> dict:
        return {"id": fid, "severity": severity,
                "rubric_dimension": "behavior",
                "artifact_reference": "a.txt:2",
                "evidence": f"Evidence for {fid}.",
                "required_change": f"Fix {fid}.", "actionable": True}

    def write_dispositions(self, entries: list[dict]) -> Path:
        out = self.dir / "dispositions.yaml"
        out.write_text(yamlish_dumps({"dispositions": entries}),
                       encoding="utf-8")
        return out

    def render_ok(self, out_rel: str = "review/record",
                  pinned_head: str | None = None) -> tuple[str, dict]:
        """Render a real, authority-clean record inside the repo.

        `pinned_head` sets the package/record head (default: the fixture
        head commit).  Returns (repo-relative record dir, review.json
        dict).
        """
        if pinned_head is not None:
            self.build_package(head=pinned_head, name="package-pinned.md")
        findings_doc = self.write_findings(
            "review-adversary", [self.finding("RA-001")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        out_dir = self.repo / out_rel
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.base, "--head", self.pkg_head,
            "--package", str(self.pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings_doc}",
            "--model", "review-adversary=test-model-v1",
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(out_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        data = json.loads(
            (out_dir / "review.json").read_text(encoding="utf-8"))
        # sanity: the authority accepts the freshly rendered record
        chk = self.cli("check", "--record", str(out_dir),
                       "--repo", str(self.repo))
        self.assertEqual(chk.returncode, 0, chk.stderr or chk.stdout)
        return out_rel, data

    def commit_record(self, rel: str) -> str:
        """Commit ONLY the record directory; returns the new head."""
        self.git("add", rel)
        self.git("commit", "-q", "-m", "record tail")
        return self.rev_parse("HEAD")

    def rewrite(self, rel: str, data: dict) -> None:
        (self.repo / rel / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")

    def to_v1(self, rel: str, data: dict) -> dict:
        """Degrade a rendered v2 record to a valid v1 record (same shape
        surgery as test_review_gate.py's legacy test)."""
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
        self.rewrite(rel, data)
        (self.repo / rel / "REVIEW-42.md").write_text(
            record_mod.render_markdown(data), encoding="utf-8")
        return data


class TestCiGate(CiGateFixture):
    def test_no_record_for_pr_exits_1(self):
        self.render_ok()
        proc = self.run_gate(self.head, pr=99)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: no review record for PR 99",
                      proc.stderr)

    def test_record_at_wrong_head_exits_1(self):
        self.render_ok()  # pinned at self.head
        proc = self.run_gate(self.base)  # earlier, uncovered head
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: no review record at head "
                      + self.base, proc.stderr)
        self.assertIn(f"(head {self.head})", proc.stderr)

    def test_valid_v2_record_at_exact_head_exits_0(self):
        self.render_ok()
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK (PR 42, 1 record(s), head "
                      + self.head + ")", proc.stdout)

    def test_record_only_tail_commit_exits_0(self):
        rel, _ = self.render_ok(pinned_head=self.fix_a)
        tail = self.commit_record(rel)
        proc = self.run_gate(tail)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK", proc.stdout)

    def test_later_code_commit_exits_1(self):
        rel, _ = self.render_ok(pinned_head=self.fix_a)
        self.commit_record(rel)
        (self.repo / "a.txt").write_text("alpha\nbeta\ngamma\ndelta\n",
                                         encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "code after the record")
        code_head = self.rev_parse("HEAD")
        proc = self.run_gate(code_head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: no review record at head "
                      + code_head, proc.stderr)

    def test_undispositioned_finding_exits_1(self):
        rel, data = self.render_ok()
        # a fabricated undispositioned blocker: the authority re-derives
        # findings and the gate re-asserts open blockers — both bite
        data["findings"].append({
            **self.finding("RA-009", severity="blocker"),
            "id": "RA-009@test-model-v1", "source": "internal",
            "lens": "review-adversary", "model": "test-model-v1",
            "location_key": data["findings"][0]["location_key"],
            "agreement": 1})
        data["verdict"] = {**data["verdict"],
                           "decision": "CHANGES_REQUESTED", "blockers": 1}
        self.rewrite(rel, data)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: ", proc.stderr)
        self.assertIn("review record", proc.stderr)

    def test_verdict_severity_mismatch_exits_1(self):
        rel, data = self.render_ok()
        # decision contradicts the counts (no blocker/major present)
        data["verdict"] = {**data["verdict"],
                           "decision": "CHANGES_REQUESTED"}
        self.rewrite(rel, data)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: ", proc.stderr)

    def test_tampered_record_exits_1(self):
        rel, data = self.render_ok()
        data["findings"][0]["severity"] = "blocker"  # severity lie
        self.rewrite(rel, data)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: ", proc.stderr)

    def test_panel_mismatch_names_missing_slots(self):
        self.render_ok()  # one lens x one model, symmetric
        panel = self.write_panel(["review-adversary"],
                                 ["test-model-v1", "model-b"],
                                 name="panel-wide.toml")
        proc = self.run_gate(self.head, panel=panel)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("panel mismatch", proc.stderr)
        self.assertIn("missing: (review-adversary, model-b)", proc.stderr)

    def test_v1_record_at_exact_head_exits_1(self):
        rel, data = self.render_ok()
        self.to_v1(rel, data)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("legacy record schema review.record.v1 is not "
                      "accepted for a new PR", proc.stderr)


class TestCiGateUsage(CiGateFixture):
    def test_bad_head_is_usage_error_exit_2(self):
        proc = self.run_gate("not-a-sha")
        self.assertEqual(proc.returncode, 2)

    def test_missing_panel_file_is_refusal_not_skip(self):
        self.render_ok()
        proc = self.run_gate(self.head,
                             panel=self.dir / "absent-panel.toml")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: panel file unreadable",
                      proc.stderr)

    def test_malformed_panel_file_is_refusal(self):
        self.render_ok()
        bad = self.dir / "bad-panel.toml"
        bad.write_text('lenses = []\nmodels = ["m"]\n', encoding="utf-8")
        proc = self.run_gate(self.head, panel=bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("review record gate: panel file malformed",
                      proc.stderr)


if __name__ == "__main__":
    unittest.main()
