#!/usr/bin/env python3
"""Tests for tools/review/record_gate.py — the shared review-record gate
module (S3.5b T2).

The record AUTHORITY runs for real here (record.py check as a
subprocess): these tests build REAL records with record.py render inside a
temp git repository using the minimal fixture builder copied from
tools/review/tests/test_record.py (test code is never imported).  The
controller tests stub the authority; this module keeps the one real
integration.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.review import record as record_mod
from tools.review import record_gate
from tools.review.yamlish import dumps as yamlish_dumps

REVIEW_DIR = Path(__file__).resolve().parents[1] / "review"
RECORD_PY = REVIEW_DIR / "record.py"
PACKAGE_PY = REVIEW_DIR / "package.py"


class RecordFixture(unittest.TestCase):
    """Minimal real-record fixture (copied from test_record.py, trimmed):
    a temp git repo with base/head commits and a rendered v2 record whose
    directory lives INSIDE the repo (records store repo-relative paths).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="s35b-t2-gate-")
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
        surgery as test_record.py's legacy acceptance test)."""
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


class TestEvaluateRealRecords(RecordFixture):
    """evaluate() against REAL records — the authority is a real
    subprocess here (never stubbed)."""

    def test_valid_record_passes(self):
        rel, _ = self.render_ok()
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=self.head)
        self.assertEqual(errs, [])
        # pr_head=None (the REVIEWING pin path) also passes
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=None)
        self.assertEqual(errs, [])

    def test_record_dir_relative_to_repo_passes(self):
        rel, _ = self.render_ok()
        errs = record_gate.evaluate(rel, str(self.repo), pr_head=self.head)
        self.assertEqual(errs, [])

    def test_tampered_review_json_fails(self):
        rel, data = self.render_ok()
        # severity lie: the authority re-derives findings from the reviewer
        # documents and recomputes the verdict — both bite.
        data["findings"][0]["severity"] = "blocker"
        self.rewrite(rel, data)
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=self.head)
        self.assertTrue(errs)
        self.assertTrue(any("review record" in e for e in errs), errs)

    def test_unparseable_record_returns_named_error_never_raises(self):
        rel, _ = self.render_ok()
        (self.repo / rel / "review.json").write_text("{oops\n",
                                                     encoding="utf-8")
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=self.head)
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith("review record gate:"), errs)

    def test_missing_record_returns_named_error(self):
        errs = record_gate.evaluate(str(self.repo / "nope"), str(self.repo),
                                    pr_head=self.head)
        self.assertEqual(len(errs), 1)
        self.assertIn("review record gate:", errs[0])

    def test_record_tail_commit_passes_code_commit_fails(self):
        # pin the record at fix_a (current HEAD) so the only later commits
        # are ours: the record-dir commit is the tail, the code commit is
        # outside it.
        rel, _ = self.render_ok(pinned_head=self.fix_a)
        # committing ONLY the record dir is the record-tail: every changed
        # path between the record head and the new head is under <rel>/
        tail = self.commit_record(rel)
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=tail)
        self.assertEqual(errs, [])
        # a later CODE commit breaks the tail rule
        (self.repo / "a.txt").write_text("alpha\nbeta\ngamma\ndelta\n",
                                         encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "code after the record")
        code_head = self.rev_parse("HEAD")
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=code_head)
        self.assertTrue(errs)
        self.assertTrue(any("head rule" in e and "a.txt" in e
                            for e in errs), errs)

    def test_v1_ancestor_head_only_with_flag(self):
        rel, data = self.render_ok(pinned_head=self.fix_a)
        self.to_v1(rel, data)
        tail = self.commit_record(rel)  # later than the record head
        # with the flag: merge-base --is-ancestor passes
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=tail, allow_v1_ancestor=True)
        self.assertEqual(errs, [])
        # without the flag: exact only — refused
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=tail, allow_v1_ancestor=False)
        self.assertTrue(any("head rule" in e for e in errs), errs)
        # exact head still passes without the flag
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=self.fix_a)
        self.assertEqual(errs, [])

    def test_open_blocker_major_fails_evaluate(self):
        rel, data = self.render_ok()
        # an undispositioned blocker: verdict recomputed consistently so
        # the authority's own verdict rule passes but the gate rule bites
        data["findings"].append({
            **self.finding("RA-009", severity="blocker"),
            "id": "RA-009@test-model-v1", "source": "internal",
            "lens": "review-adversary", "model": "test-model-v1",
            "location_key": data["findings"][0]["location_key"],
            "agreement": 1})
        data["verdict"] = {**data["verdict"], "decision":
                           "CHANGES_REQUESTED", "blockers": 1}
        self.rewrite(rel, data)
        errs = record_gate.evaluate(str(self.repo / rel), str(self.repo),
                                    pr_head=self.head)
        self.assertTrue(any("open blocker/major" in e for e in errs), errs)
        self.assertTrue(any("RA-009@test-model-v1" in e for e in errs), errs)


class TestRecordPrimitives(RecordFixture):
    def test_record_sha256_is_review_json_digest(self):
        rel, _ = self.render_ok()
        raw = (self.repo / rel / "review.json").read_bytes()
        self.assertEqual(record_gate.record_sha256(str(self.repo / rel)),
                         hashlib.sha256(raw).hexdigest())

    def test_load_record_named_error(self):
        rel, _ = self.render_ok()
        data = record_gate.load_record(str(self.repo / rel))
        self.assertEqual(data["schema"], "review.record.v2")
        (self.repo / rel / "review.json").write_text("[1,2]",
                                                     encoding="utf-8")
        with self.assertRaises(ValueError) as cm:
            record_gate.load_record(str(self.repo / rel))
        self.assertIn("review record gate:", str(cm.exception))

    def test_check_record_names_the_authority_error(self):
        rel, data = self.render_ok()
        data["pr"] = 99  # markdown REVIEW-99.md does not exist
        self.rewrite(rel, data)
        errs = record_gate.check_record(str(self.repo / rel),
                                        str(self.repo))
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith("review record: "), errs)
        self.assertIn("markdown missing", errs[0])

    def test_check_record_clean_passes(self):
        rel, _ = self.render_ok()
        self.assertEqual(
            record_gate.check_record(str(self.repo / rel),
                                     str(self.repo)), [])


class TestOpenBlockerMajors(unittest.TestCase):
    """Pure unit cases — no repo needed."""

    def record(self, findings, dispositions):
        return {"findings": findings, "dispositions": dispositions}

    def f(self, fid, severity):
        return {"id": fid, "severity": severity}

    def d(self, fid):
        return {"id": fid, "disposition": "fixed"}

    def test_empty_record(self):
        self.assertEqual(record_gate.open_blocker_majors({}), [])
        self.assertEqual(
            record_gate.open_blocker_majors(
                self.record([], [])), [])

    def test_blocker_and_major_without_disposition(self):
        rec = self.record([self.f("B-1", "blocker"),
                           self.f("M-1", "major"),
                           self.f("N-1", "minor"),
                           self.f("I-1", "info")], [])
        self.assertEqual(record_gate.open_blocker_majors(rec),
                         ["B-1", "M-1"])

    def test_matching_disposition_clears(self):
        rec = self.record([self.f("B-1", "blocker"),
                           self.f("M-1", "major")],
                          [self.d("B-1")])
        self.assertEqual(record_gate.open_blocker_majors(rec), ["M-1"])
        rec = self.record([self.f("B-1", "blocker")], [self.d("B-1")])
        self.assertEqual(record_gate.open_blocker_majors(rec), [])

    def test_disposition_id_must_match_exactly(self):
        rec = self.record([self.f("RA-1@m1", "blocker")],
                          [self.d("RA-1@m2")])  # different model slot
        self.assertEqual(record_gate.open_blocker_majors(rec),
                         ["RA-1@m1"])

    def test_minor_and_info_never_reported(self):
        rec = self.record([self.f("N-1", "minor"), self.f("I-1", "info")],
                          [])
        self.assertEqual(record_gate.open_blocker_majors(rec), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
