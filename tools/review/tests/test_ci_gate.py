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
import shutil
import subprocess
import sys
import tempfile
import tomllib
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
        self.git("commit", "-q", "-m", "commit two: fix a.txt")
        self.fix_a = self.rev_parse("HEAD")

        (self.repo / "b.txt").write_text("beta file\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "commit three: the reviewed head")
        self.head = self.rev_parse("HEAD")
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
                 records_root: str = "review",
                 base: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(CI_GATE_PY),
             "--repo", str(self.repo), "--pr", str(pr), "--head", head,
             "--base", base or self.base,
             "--records-root", records_root,
             "--panel", str(panel or self.panel)],
            capture_output=True, text=True, env=self.env)

    def build_package(self, head: str | None = None,
                      name: str = "package.md") -> tuple[Path, str]:
        head = head or self.head
        self.pkg = self.repo / name
        self.manifest = self.dir / (name + ".manifest.json")
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", head, "--out", str(self.pkg),
             "--manifest-out", str(self.manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = head
        self.pkg_base = self.base
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
                "base_sha": getattr(self, "pkg_base", self.base),
                "head_sha": self.pkg_head,
                "decision": "CHANGES_REQUESTED" if findings else "APPROVED",
                "findings": findings,
            },
        }
        doc["review"]["base_sha"] = getattr(self, "pkg_base", self.base)
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
            "--package-manifest", str(self.manifest),
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
                      + self.head, proc.stdout)

    def test_record_only_tail_commit_exits_0(self):
        rel, _ = self.render_ok()
        tail = self.commit_record(rel)
        proc = self.run_gate(tail)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK", proc.stdout)

    def test_later_code_commit_exits_1(self):
        rel, _ = self.render_ok()
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


class TestCiGatePanelSlots(CiGateFixture):
    """Duplicate (role, model) reviewer slots are refused — never read as
    extra panel coverage (S3.5b fix round)."""

    def test_duplicate_slot_refused_in_panel_check(self):
        # pure-function level: panel_slot_errors names the duplicate
        record = {"reviewers": [
            {"role": "review-adversary", "model": "test-model-v1"},
            {"role": "review-adversary", "model": "test-model-v1"},
        ]}
        from tools.review.ci_gate import panel_slot_errors
        errs = panel_slot_errors(Path("rec"), record,
                                 ["review-adversary"], ["test-model-v1"])
        self.assertEqual(len(errs), 1)
        self.assertIn("duplicate reviewer slots", errs[0])
        self.assertIn("(review-adversary, test-model-v1)", errs[0])

    def test_duplicate_slot_record_refused_end_to_end(self):
        import tools.review.ci_gate as ci_gate_mod
        rel, data = self.render_ok()
        # a duplicated reviewer entry: the gate must exit 1 naming the
        # duplicate (the record authority refuses it first — either way
        # the duplicate never reads as coverage)
        data["reviewers"].append(dict(data["reviewers"][0]))
        self.rewrite(rel, data)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate", proc.stderr)
        self.assertNotIn("review record gate: OK", proc.stdout)
        # and panel_slot_errors is what names it at the panel step when
        # the record itself is otherwise clean
        errs = ci_gate_mod.panel_slot_errors(
            self.repo / rel, data, ["review-adversary"], ["test-model-v1"])
        self.assertTrue(any("duplicate" in e for e in errs), errs)


class TestCiGateDefaults(CiGateFixture):
    """Bare invocation (no --records-root/--panel) must discover records
    under the default root and use the committed panel defaults."""

    KIT_REPO = Path(__file__).resolve().parents[3]

    def render_full_panel(self) -> str:
        """Render a record covering the COMMITTED panel (every lens once
        per model, all approvals) under the default records root."""
        panel = tomllib.loads(
            (self.KIT_REPO / "kit-v0.1" / "review-panel.toml")
            .read_text(encoding="utf-8"))
        lenses, models = panel["lenses"], panel["models"]
        self.build_package()
        disp = self.write_dispositions([])
        out_rel = "kit-v0.1/reviews/S9.9/record"
        out_dir = self.repo / out_rel
        cmd = [sys.executable, str(RECORD_PY),
               "render", "--repo", str(self.repo), "--story", "S9.9",
               "--pr", "42", "--base", self.base, "--head", self.head,
               "--package", str(self.pkg), "--dispositions", str(disp),
               "--package-manifest", str(self.manifest),
               "--gate", f"lint=0:{self.gate_path}",
               "--out-dir", str(out_dir)]
        for lens in lenses:
            for model in models:
                doc = self.write_findings(
                    lens, [], name=f"findings-{lens}-{model}.yaml")
                cmd += ["--findings", f"{lens}@{model}={doc}"]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return out_rel

    def run_gate_defaults(self, head: str, pr: int = 42):
        return subprocess.run(
            [sys.executable, str(CI_GATE_PY),
             "--repo", str(self.repo), "--pr", str(pr), "--head", head,
             "--base", self.base],
            capture_output=True, text=True, env=self.env)

    def test_bare_invocation_uses_committed_panel_defaults(self):
        self.render_full_panel()
        # the default panel file lives under --repo: copy the COMMITTED
        # kit panel into the temp repo at its default location
        (self.repo / "kit-v0.1").mkdir(parents=True, exist_ok=True)
        shutil.copy(self.KIT_REPO / "kit-v0.1" / "review-panel.toml",
                    self.repo / "kit-v0.1" / "review-panel.toml")
        proc = self.run_gate_defaults(self.head)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK (PR 42, 1 record(s), head "
                      + self.head, proc.stdout)

    def test_bare_invocation_panel_mismatch_refused(self):
        self.render_full_panel()
        (self.repo / "kit-v0.1").mkdir(parents=True, exist_ok=True)
        (self.repo / "kit-v0.1" / "review-panel.toml").write_text(
            'lenses = ["review-adversary"]\nmodels = ["test-model-v1"]\n',
            encoding="utf-8")
        proc = self.run_gate_defaults(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("panel mismatch", proc.stderr)


class TestCiGateManifestRecord(CiGateFixture):
    """A record rendered WITH --package-manifest stays gate-verifiable
    after the package file is deleted (packages are never committed) —
    at the exact head and at a record-only tail head."""

    def render_with_manifest(self, out_rel="review/record",
                             pinned_head=None) -> tuple[str, dict]:
        head = pinned_head or self.head
        manifest = self.dir / "manifest.json"
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", head, "--out", str(self.repo / "package-m.md"),
             "--manifest-out", str(manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg = self.repo / "package-m.md"
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = head
        findings_doc = self.write_findings(
            "review-adversary", [self.finding("RA-001")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        out_dir = self.repo / out_rel
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.base, "--head", head,
            "--package", str(self.pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings_doc}",
            "--model", "review-adversary=test-model-v1",
            "--package-manifest", str(manifest),
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(out_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        data = json.loads(
            (out_dir / "review.json").read_text(encoding="utf-8"))
        self.assertIn("manifest", data["package"])
        return out_rel, data

    def test_manifest_record_package_deleted_passes_at_exact_head(self):
        self.render_with_manifest()
        self.pkg.unlink()  # fresh-clone case: only the manifest remains
        self.assertFalse(self.pkg.exists())
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK", proc.stdout)

    def test_manifest_record_package_deleted_passes_at_record_tail(self):
        rel, _ = self.render_with_manifest()
        self.pkg.unlink()
        tail = self.commit_record(rel)
        proc = self.run_gate(tail)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK", proc.stdout)

    def test_manifest_record_from_foreign_cwd_passes(self):
        """The rebuild must not depend on the caller's cwd: run the gate
        with a cwd outside the repo (relative --repo)."""
        self.render_with_manifest()
        self.pkg.unlink()
        proc = subprocess.run(
            [sys.executable, str(CI_GATE_PY),
             "--repo", str(self.repo), "--pr", "42", "--head", self.head,
             "--base", self.base,
             "--records-root", "review", "--panel", str(self.panel)],
            capture_output=True, text=True, env=self.env,
            cwd=str(self.dir))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("review record gate: OK", proc.stdout)


class TestCiGateCoverage(CiGateFixture):
    """Full-change coverage: a record at the head whose package covered only
    part of the PR is refused (S3.5b external review P1)."""

    def render_narrow(self) -> tuple[str, dict]:
        """Render a record whose package covers only ``fix_a..head``."""
        out_dir = self.repo / "review/record"
        manifest = self.dir / "manifest-narrow.json"
        pkg = self.repo / "package-narrow.md"
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.fix_a,
             "--head", self.head, "--out", str(pkg),
             "--manifest-out", str(manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg = pkg
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = self.head
        self.pkg_base = self.fix_a  # the narrow package's real base
        findings = self.write_findings(
            "review-adversary", [self.finding("RA-001")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.fix_a, "--head", self.head,
            "--package", str(pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings}",
            "--model", "review-adversary=test-model-v1",
            "--package-manifest", str(manifest),
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(out_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        return "review/record", json.loads(
            (out_dir / "review.json").read_text(encoding="utf-8"))

    def test_partial_package_refused_naming_uncovered_path(self):
        self.render_narrow()  # a.txt was never in this package
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("never packaged for review", proc.stderr)
        self.assertIn("a.txt", proc.stderr)

    def test_head_only_package_refused(self):
        """--include one file (or a tail package) cannot prove coverage."""
        out_dir = self.repo / "review/record"
        manifest = self.dir / "manifest-inc.json"
        pkg = self.repo / "package-inc.md"
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", self.head, "--include", "b.txt",
             "--out", str(pkg), "--manifest-out", str(manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg = pkg
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = self.head
        findings = self.write_findings(
            "review-adversary", [self.finding("RA-001")])
        disp = self.write_dispositions(
            [{"id": "RA-001@test-model-v1", "disposition": "fixed",
              "fix_sha": self.fix_a}])
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.base, "--head", self.head,
            "--package", str(pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings}",
            "--model", "review-adversary=test-model-v1",
            "--package-manifest", str(manifest),
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(out_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("a.txt", proc.stderr)

    def test_unresolvable_base_refused_not_treated_as_no_change(self):
        """A well-formed base that git cannot resolve must REFUSE, never
        read as 'nothing to cover' (S3.5b round-5 RA-001)."""
        self.render_ok()
        proc = self.run_gate(self.head, base="0" * 40)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("coverage: cannot diff", proc.stderr)

    def test_record_without_manifest_refused_by_coverage(self):
        """coverage_errors refuses a qualifying record with no manifest
        instead of skipping it (S3.5b round-5 RA-002)."""
        from tools.review import ci_gate as ci_gate_mod
        errs = ci_gate_mod.coverage_errors(
            self.repo, self.base, self.head,
            [(self.repo / "review/record", {"package": {}})], [])
        self.assertTrue(any("carries no package manifest" in e for e in errs),
                        errs)

    def test_source_file_under_records_root_is_not_silently_excluded(self):
        """A source file committed under a records root must not be
        certified without being packaged (S3.5b round-5 RS-001)."""
        (self.repo / "review").mkdir(exist_ok=True)
        (self.repo / "review" / "evil.py").write_text("x = 1\n",
                                                     encoding="utf-8")
        self.git("add", "review/evil.py")
        self.git("commit", "-q", "-m", "source file under the records root")
        self.head = self.rev_parse("HEAD")
        withheld = self.dir / "withheld-root.txt"
        withheld.write_text("review/\n", encoding="utf-8")
        pkg = self.repo / "package-root.md"
        manifest = self.dir / "manifest-root.json"
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", self.head, "--withheld", str(withheld),
             "--out", str(pkg), "--manifest-out", str(manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg = pkg
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = self.head
        self.pkg_base = self.base
        findings = self.write_findings("review-adversary", [])
        disp = self.write_dispositions([])
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.base, "--head", self.head,
            "--package", str(pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings}",
            "--model", "review-adversary=test-model-v1",
            "--package-manifest", str(manifest),
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(self.repo / "review/record"))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("deliberately withheld", proc.stderr)
        self.assertIn("review/evil.py", proc.stderr)

    def test_withheld_changed_path_refused_as_deliberate_not_missing(self):
        """A withheld path is a design decision: the gate must name it as
        deliberately withheld (S3.5b round-4 RA-002), not as a packaging
        bug, and still refuse the record."""
        withheld = self.dir / "withheld.txt"
        withheld.write_text("a.txt\n", encoding="utf-8")
        pkg = self.repo / "package-wh.md"
        manifest = self.dir / "manifest-wh.json"
        proc = subprocess.run(
            [sys.executable, str(PACKAGE_PY), "build",
             "--repo", str(self.repo), "--base", self.base,
             "--head", self.head, "--withheld", str(withheld),
             "--out", str(pkg), "--manifest-out", str(manifest)],
            capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.pkg = pkg
        self.pkg_sha = proc.stdout.splitlines()[1].split(
            "package_sha256: ", 1)[1]
        self.pkg_head = self.head
        self.pkg_base = self.base
        findings = self.write_findings("review-adversary", [])
        disp = self.write_dispositions([])
        proc = self.cli(
            "render", "--repo", str(self.repo), "--story", "S35B",
            "--pr", "42", "--base", self.base, "--head", self.head,
            "--package", str(pkg), "--dispositions", str(disp),
            "--findings", f"review-adversary={findings}",
            "--model", "review-adversary=test-model-v1",
            "--package-manifest", str(manifest),
            "--gate", f"lint=0:{self.gate_path}",
            "--out-dir", str(self.repo / "review/record"))
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        proc = self.run_gate(self.head)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("deliberately withheld", proc.stderr)
        self.assertIn("a.txt", proc.stderr)
        self.assertNotIn("never packaged", proc.stderr)

    def test_record_dir_tail_is_excluded_from_coverage(self):
        """The record commit's own files are not review inputs: a full
        package + record-only tail passes with --base."""
        rel, _ = self.render_ok()
        tail = self.commit_record(rel)
        proc = self.run_gate(tail)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)


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
