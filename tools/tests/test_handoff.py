#!/usr/bin/env python3
"""Tests for tools/build_handoff.py — deterministic role context
packages: coder / behavior_reviewer / contract_reviewer (canonical
snake_case via role_catalog).

Key properties tested:
  * deterministic: same inputs → byte-identical package + same digest
  * plan-blindness is structural: the behavior_reviewer package never
    reads or embeds impl-plan.md content (or the contract)
  * coder package carries contract slice, scope, commands, resume
    preamble, prompt_version; withheld list explicit
  * missing contract → STOP-and-report package, never an improvised one
  * review_context event rows pass ledger_schema validation and append
    through ledger_lib (the ONLY write path)

All state lives in tempdir control homes + temp git repos (fixed author
env); no network, live ledger never touched. Fictional STORY-101.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import build_handoff as BH
from tools import ledger_lib as L
from tools import ledger_schema as S

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "prep"
GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.test",
           "GIT_COMMITTER_NAME": "test",
           "GIT_COMMITTER_EMAIL": "test@example.test"}
TS = "2026-10-25T00:00:00Z"


def norm(evt: dict) -> dict:
    """Fill the null+reason siblings the schema requires (test helper)."""
    for f in sorted(S.NULLABLE.get(evt["event"], frozenset())):
        if f not in evt:
            evt[f] = None
        if evt[f] is None and not evt.get(f + "_reason"):
            evt[f + "_reason"] = "test: unavailable"
        elif evt[f] is not None:
            evt[f + "_reason"] = None
    return evt


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], env=GIT_ENV,
                          check=True, capture_output=True,
                          text=True).stdout.strip()


class TempStoryMixin(unittest.TestCase):
    sid = "STORY-101"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "control"          # control home
        self.repo = self.tmp / "repo"             # work repo (diffs/SHAs)
        (self.root / "stories" / self.sid).mkdir(parents=True)
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        self.commit_all("base")

    def tearDown(self):
        self._td.cleanup()

    def commit_all(self, msg: str = "test") -> str:
        _git(self.repo, "add", "-A")
        _git(self.repo, "-c", "user.name=test",
             "-c", "user.email=test@example.test",
             "commit", "-q", "--allow-empty", "-m", msg)
        return _git(self.repo, "rev-parse", "HEAD")

    # -- ledger seeding (all rows schema-valid via ledger_lib) ----------

    def append(self, evt: dict) -> None:
        L.append_event(self.root / "factory" / "ledger" / "events.jsonl",
                       norm(evt))

    def seed_claim(self, start_sha: str) -> None:
        self.append({"event": "assignment", "story_id": self.sid,
                     "op": "claim", "recorded_at": TS, "provenance": "live",
                     "owner": "worker-1", "host": "h1", "session": "s1",
                     "claimed_at": TS, "start_sha": start_sha})

    def seed_dispatch(self, worktree: str,
                      eid: str | None = None) -> None:
        eid = eid or f"{self.sid}-coder-1-1758800000"
        # a live dispatch occupies TWO rows (pre-spawn + completion); the
        # single completed row is the backfilled form.
        self.append({"event": "dispatch", "event_id": eid,
                     "story_id": self.sid, "role": "coder",
                     "recorded_at": TS, "provenance": "live",
                     "requested_model": "model-a", "attempt": 1,
                     "started_at": TS,
                     "prompt_version": "coder-v0.1.1",
                     "worktree": worktree, "model_unverified": True})
        self.append({"event": "dispatch", "event_id": eid,
                     "story_id": self.sid, "role": "coder",
                     "recorded_at": TS, "provenance": "live",
                     "requested_model": "model-a",
                     "reported_model": "model-a", "attempt": 1,
                     "started_at": TS, "ended_at": TS, "outcome": "success",
                     "prompt_version": "coder-v0.1.1",
                     "worktree": worktree, "model_unverified": False})

    def seed_build_green(self, commit_sha: str) -> None:
        self.append({"event": "transition", "story_id": self.sid,
                     "recorded_at": TS, "provenance": "live",
                     "from_state": "CODING", "to_state": "BUILD_GREEN",
                     "requested_by": "orchestrator",
                     "checks_run": ["commit_sha"],
                     "check_results": {
                         "evidence_supplied": {"ok": True, "detail": {
                             "commit_sha": commit_sha}}},
                     "repo": None,
                     "repo_reason": "story-level transition"})

    # -- artifacts -------------------------------------------------------

    def write_plan(self, contract_yaml: str | None) -> None:
        if contract_yaml is None:
            (self.root / "stories" / self.sid / "impl-plan.md").write_text(
                "# plan\n\nSECRET-PLAN-PROSE that must not leak to pass "
                "1.\n", encoding="utf-8")
        else:
            (self.root / "stories" / self.sid / "impl-plan.md").write_text(
                "# plan\n\nSECRET-PLAN-PROSE that must not leak to pass "
                "1.\n\n```contract.yaml\n" + contract_yaml + "\n```\n",
                encoding="utf-8")


GOOD_CONTRACT = (FIXTURES / "impl-plan.md").read_text(
    encoding="utf-8").split("```contract.yaml\n")[1].split("\n```")[0]


class CoderPackageTests(TempStoryMixin):
    def test_coder_package_contents(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        self.seed_dispatch(str(self.tmp / "wt-101"))
        text = BH.build_coder(self.root, self.repo, self.sid, "40")
        self.assertIn("prompt_version: `coder-v0.1.1`", text)
        self.assertIn("## Resume protocol (DEFAULT preamble)", text)
        self.assertIn(str(self.tmp / "wt-101"), text)
        self.assertIn("story/101", text)
        self.assertIn(f"- start_sha: `{base}`", text)
        self.assertIn("- CREATE `src/app/cache.py`", text)
        self.assertIn("- EXTEND `src/app/parser.py`", text)
        self.assertIn("`python3 -m unittest`", text)
        self.assertIn("| AC-1 | `tests` | `test_rejects_malformed_input` |",
                      text)
        self.assertIn("**INV-1**: parse errors never write state", text)
        self.assertIn("Withheld from you", text)
        # prose never embedded — only the contract slice
        self.assertNotIn("SECRET-PLAN-PROSE", text)

    def test_missing_contract_stops(self):
        self.write_plan(None)
        text = BH.build_coder(self.root, self.repo, self.sid, "40")
        self.assertIn("CONTRACT MISSING — STOP", text)
        self.assertIn("specific retrieval request", text)

    def test_missing_claim_shows_placeholder_not_invention(self):
        self.write_plan(GOOD_CONTRACT)
        text = BH.build_coder(self.root, self.repo, self.sid, "40")
        self.assertIn("<worktree — pin it in the dispatch event>", text)
        self.assertIn("(no open claim)", text)


class BehaviorReviewerTests(TempStoryMixin):
    def test_plan_blind_by_construction(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        self.seed_build_green(base)
        text = BH.build_behavior_reviewer(self.root, self.repo, self.sid,
                                          "50", base_ref="main")
        self.assertNotIn("SECRET-PLAN-PROSE", text)
        self.assertNotIn("contract_version", text)  # contract withheld too
        self.assertIn("PLAN-BLIND", text)
        self.assertIn("counterexample", text.lower())
        self.assertIn("prompt_version: `behavior_reviewer-v0.1.1`", text)
        self.assertIn("scope-dimension table", text)
        self.assertIn("pageSize", text)
        self.assertIn("Withheld from you", text)

    def test_missing_build_green_shows_honest_placeholder(self):
        self.write_plan(GOOD_CONTRACT)
        text = BH.build_behavior_reviewer(self.root, self.repo, self.sid,
                                          "50", base_ref="main")
        self.assertIn("<BUILD_GREEN commit_sha — record it in the ledger "
                      "first>", text)

    def test_reviewed_sha_from_ledger(self):
        self.write_plan(GOOD_CONTRACT)
        # a real second commit so the diff range resolves
        (self.repo / "src.py").write_text("print('x')\n", encoding="utf-8")
        commit = self.commit_all("work")
        self.seed_claim(commit)
        self.seed_build_green(commit)
        text = BH.build_behavior_reviewer(self.root, self.repo, self.sid,
                                          "50", base_ref="main")
        self.assertIn(commit, text)

    def test_review_context_manifest_lists_exact_files_and_hunks(self):
        self.write_plan(GOOD_CONTRACT)
        _git(self.repo, "checkout", "-q", "-b", "work")
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir(exist_ok=True)
        (self.repo / "tests" / "test_a.py").write_text("t = 1\n",
                                                       encoding="utf-8")
        target = self.commit_all("work")
        self.seed_claim(target)
        self.seed_build_green(target)
        manifest = BH.build_review_context_manifest(
            self.root, self.repo, self.sid, "behavior_reviewer",
            target_sha=target, base_ref="main")
        self.assertEqual(manifest["reviewed_sha"], target)
        self.assertTrue(manifest["base_sha"])
        self.assertEqual(manifest["delivery_surface"], "dispatch-package")
        self.assertEqual([f["path"] for f in manifest["files"]],
                         ["src/a.py", "tests/test_a.py"])
        self.assertEqual(manifest["files"][0]["hunks"][0]["new_start"], 1)
        self.assertEqual(len(BH.manifest_digest(manifest)), 64)
        # behavior reviewer gets NO additional supplied paths (plan-blind)
        self.assertEqual(manifest["additional_supplied_paths"], [])

    def test_review_context_event_validates_and_appends(self):
        self.write_plan(GOOD_CONTRACT)
        (self.repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        target = self.commit_all("work")
        self.seed_claim(target)
        self.seed_build_green(target)
        manifest = BH.build_review_context_manifest(
            self.root, self.repo, self.sid, "behavior_reviewer",
            target_sha=target, base_ref="main")
        did = f"{self.sid}-behavior_reviewer-1-1758800000"
        event = BH.review_context_event(
            self.root, self.sid, "behavior-reviewer", manifest,
            Path(f"stories/{self.sid}/context-manifest-behavior_"
                 "reviewer.json"), did)
        self.assertEqual(event["review_role"], "behavior_reviewer")
        self.assertEqual(event["reviewed_sha"], target)
        self.assertEqual(event["delivery_surface"], "dispatch-package")
        self.assertEqual(event["dispatch_id_reason"], None)
        # append through the ONLY write path — schema-validates on write
        L.append_event(self.root / "factory" / "ledger" / "events.jsonl",
                       event)
        events = L.load_events(
            self.root / "factory" / "ledger" / "events.jsonl")
        rc = [e for e in events if e["event"] == "review_context"]
        self.assertEqual(len(rc), 1)
        self.assertEqual(rc[0]["event_id"], f"review_context-{self.sid}-1")

    def test_review_context_requires_dispatch_id(self):
        self.write_plan(GOOD_CONTRACT)
        (self.repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        target = self.commit_all("work")
        self.seed_claim(target)
        self.seed_build_green(target)
        manifest = BH.build_review_context_manifest(
            self.root, self.repo, self.sid, "behavior_reviewer",
            target_sha=target, base_ref="main")
        with self.assertRaises(ValueError):
            BH.review_context_event(self.root, self.sid,
                                    "behavior_reviewer", manifest,
                                    Path("stories/x.json"), None)

    def test_inline_diff_block_embeds_patch(self):
        self.write_plan(GOOD_CONTRACT)
        _git(self.repo, "checkout", "-q", "-b", "work")
        (self.repo / "f.txt").write_text("line\n", encoding="utf-8")
        target = self.commit_all("work")
        self.seed_claim(target)
        self.seed_build_green(target)
        manifest = BH.build_review_context_manifest(
            self.root, self.repo, self.sid, "behavior_reviewer",
            target_sha=target, base_ref="main")
        block = BH.inline_diff_block(self.repo, manifest)
        self.assertIn("## Branch diff (INLINED", block[0])
        self.assertIn("+line", "\n".join(block))


class ContractReviewerTests(TempStoryMixin):
    def test_contract_pass_contents(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        self.seed_build_green(base)
        text = BH.build_contract_reviewer(self.root, self.repo, self.sid,
                                          "50", base_ref="main")
        self.assertIn("contract_version", text)
        self.assertIn("## Approved contract (binding)", text)
        self.assertIn("Manifest reconciliation input", text)
        self.assertIn("- declared CREATE: ['src/app/cache.py']", text)
        self.assertIn("- declared EXTEND: ['src/app/parser.py']", text)
        self.assertIn("## Deviations list (ledger)", text)
        self.assertIn("(none recorded)", text)
        self.assertIn("pass 1's raw reasoning", text)  # withheld statement
        self.assertNotIn("SECRET-PLAN-PROSE", text)

    def test_contract_manifest_supplies_plan_paths(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        manifest = BH.build_review_context_manifest(
            self.root, self.repo, self.sid, "contract_reviewer",
            target_sha="<BUILD_GREEN commit_sha — record it in the ledger "
                      "first>", base_ref="main")
        self.assertEqual(manifest["additional_supplied_paths"], [
            f"stories/{self.sid}/impl-plan.md",
            f"stories/{self.sid}/test-plan.md"])
        self.assertIn("error", manifest)  # honest placeholder → error field

    def test_deviations_listed(self):
        self.write_plan(GOOD_CONTRACT)
        self.append({"event": "deviation", "story_id": self.sid,
                     "recorded_at": TS, "provenance": "live",
                     "contract_field": "tests", "planned": "2 tests",
                     "actual": "3 tests",
                     "justification": "extra edge case"})
        text = BH.build_contract_reviewer(self.root, self.repo, self.sid,
                                          "50", base_ref="main")
        self.assertIn("`tests`: planned '2 tests' → actual '3 tests' — "
                      "extra edge case", text)


class CliTests(TempStoryMixin):
    def test_cli_writes_and_digests(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        out = self.tmp / "pkg.md"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = BH.main([self.sid, "coder", "--root", str(self.root),
                          "--repo", str(self.repo), "--budget", "40",
                          "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        self.assertIn("prompt_version", out.read_text(encoding="utf-8"))
        printed = buf.getvalue()
        self.assertIn("prompt_version: coder-v0.1.1", printed)
        self.assertRegex(printed, r"package_sha256: [0-9a-f]{64}")
        self.assertTrue(out.read_bytes().endswith(b"\n"))

    def test_cli_hyphen_role_canonicalizes(self):
        self.write_plan(GOOD_CONTRACT)
        out = self.tmp / "pkg.md"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = BH.main([self.sid, "behavior-reviewer",
                          "--root", str(self.root), "--repo",
                          str(self.repo), "--out", str(out),
                          "--base-ref", "main"])
        self.assertEqual(rc, 0)
        # the manifest file uses the canonical snake_case name
        self.assertTrue((self.root / "stories" / self.sid /
                         "context-manifest-behavior_reviewer.json").exists())

    def test_cli_unknown_role_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = BH.main([self.sid, "planner", "--root", str(self.root),
                          "--repo", str(self.repo)])
        self.assertEqual(rc, 2)

    def test_cli_missing_story_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            rc = BH.main(["STORY-999", "coder", "--root", str(self.root),
                          "--repo", str(self.repo)])
        self.assertEqual(rc, 2)

    def test_cli_record_context_on_coder_exits_two(self):
        self.write_plan(GOOD_CONTRACT)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = BH.main([self.sid, "coder", "--root", str(self.root),
                          "--repo", str(self.repo), "--record-context"])
        self.assertEqual(rc, 2)

    def test_cli_record_context_without_base_sha_exits_two(self):
        """No BUILD_GREEN recorded → the manifest has no exact base/diff
        SHA → graceful `error:` refusal, rc 2, no traceback; the package
        and manifest files (written before the record step) remain."""
        self.write_plan(GOOD_CONTRACT)
        did = f"{self.sid}-behavior_reviewer-1-1758800000"
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            rc = BH.main([self.sid, "behavior_reviewer", "--root",
                          str(self.root), "--repo", str(self.repo),
                          "--record-context", "--dispatch-id", did])
        self.assertEqual(rc, 2)
        printed = err.getvalue()
        self.assertIn("error: cannot record review context without an "
                      "exact base/diff SHA", printed)
        self.assertNotIn("Traceback", printed)
        self.assertTrue((self.root / "stories" / self.sid /
                         "handoff-behavior_reviewer.md").exists())
        self.assertTrue((self.root / "stories" / self.sid /
                         "context-manifest-behavior_reviewer.json").exists())

    def test_cli_record_context_without_dispatch_id_exits_two(self):
        """Valid BUILD_GREEN SHA but missing --dispatch-id → graceful
        refusal, rc 2, no traceback, no ledger event written."""
        self.write_plan(GOOD_CONTRACT)
        (self.repo / "src.py").write_text("x = 1\n", encoding="utf-8")
        target = self.commit_all("work")
        self.seed_claim(target)
        self.seed_build_green(target)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            rc = BH.main([self.sid, "behavior_reviewer", "--root",
                          str(self.root), "--repo", str(self.repo),
                          "--record-context"])
        self.assertEqual(rc, 2)
        printed = err.getvalue()
        self.assertIn("error: internal review_context requires its "
                      "pre-spawn dispatch_id", printed)
        self.assertNotIn("Traceback", printed)
        ledger = self.root / "factory" / "ledger" / "events.jsonl"
        events = L.load_ledger(ledger)
        self.assertFalse(any(e.get("event") == "review_context"
                             for e in events))


class DeterminismTests(TempStoryMixin):
    def test_same_inputs_same_bytes(self):
        self.write_plan(GOOD_CONTRACT)
        base = _git(self.repo, "rev-parse", "HEAD")
        self.seed_claim(base)
        orig = L.utcnow
        L.utcnow = lambda: "2026-10-25T00:00:00Z"
        try:
            a = BH.build_coder(self.root, self.repo, self.sid, "40")
            b = BH.build_coder(self.root, self.repo, self.sid, "40")
        finally:
            L.utcnow = orig
        self.assertEqual(a, b)
        # UTC ...Z timestamp format in the header
        self.assertIn("generated 2026-10-25T00:00:00Z", a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
