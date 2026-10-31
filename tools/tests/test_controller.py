#!/usr/bin/env python3
"""Tests for tools/controller.py — the transition engine: evidence-key and
evidence-format refusals, order rules (FIXING reentry, BLOCKED resume,
UI-skip fail-closed), per-state hard checks, claim lifecycle (one open
claim, handover incl. flagged, release at CLOSED), the OVERRIDE fail-closed
matrix, loop caps, the live plan-gate invocation (stub scripts), pre-spawn
dispatch-id allocation, multirepo set-level refusals, and the law that
every refusal leaves the ledger unchanged.

All state lives in tempdir control homes (temp git repos, fixed author
env); the live ledger is never touched. Gate scripts are per-test stubs —
real gate scripts ship separately (tools/gates/).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import controller as CT
from tools import ledger_lib as L
from tools import ledger_schema as S
from tools import state_machine as SM

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "controller"
MR_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "multirepo"
CONTRACT_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "contract"

STORY = "STORY-001"
TS = "2026-10-25T00:00:00Z"
GIT_ENV = {**os.environ,
           "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.test",
           "GIT_COMMITTER_NAME": "test",
           "GIT_COMMITTER_EMAIL": "test@example.test"}

CHAIN: list[tuple[str, str]] = [
    ("BACKLOG", "GRILLED"), ("GRILLED", "TEST_PLAN"),
    ("TEST_PLAN", "SPEC_FROZEN"), ("SPEC_FROZEN", "PLANNED"),
    ("PLANNED", "GATE_PASSED"), ("GATE_PASSED", "PLAN_APPROVED"),
    ("PLAN_APPROVED", "STORYBOOK_APPROVAL"),
    ("STORYBOOK_APPROVAL", "CODING"), ("CODING", "BUILD_GREEN"),
    ("BUILD_GREEN", "DRAFT_OPENED"), ("DRAFT_OPENED", "REVIEWING"),
    ("REVIEWING", "FIXING"), ("FIXING", "FULL_TESTS_GREEN"),
    ("FULL_TESTS_GREEN", "READY_FOR_REVIEW"),
    ("READY_FOR_REVIEW", "MERGE_ELIGIBLE"), ("MERGE_ELIGIBLE", "MERGED"),
    ("MERGED", "LIVE_PROVEN"), ("LIVE_PROVEN", "CLOSED"),
]


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


def transition_row(eid: str, sid: str, frm: str, to: str,
                   repo: str | None = None,
                   check_results: dict | None = None) -> dict:
    return norm({"event": "transition", "event_id": eid,
                 "recorded_at": TS, "provenance": "live",
                 "story_id": sid, "from_state": frm, "to_state": to,
                 "requested_by": "orchestrator", "checks_run": ["order"],
                 "check_results": check_results or {"order": {"ok": True}},
                 "approved_by": None, "repo": repo})


def chain_to(state: str, sid: str = STORY) -> list[dict]:
    """Seeded story-level transitions BACKLOG -> ... -> `state`."""
    rows = []
    for i, (frm, to) in enumerate(CHAIN, 1):
        rows.append(transition_row(f"tr-{sid}-{i}", sid, frm, to))
        if to == state:
            break
    return rows


def gate_row(eid: str, sid: str, gate: str, answered: bool = True,
             by: str = "approver-one") -> dict:
    return norm({"event": "human_gate", "event_id": eid,
                 "recorded_at": TS, "provenance": "live", "story_id": sid,
                 "gate": gate, "question": f"{gate}?", "asked_at": TS,
                 "answered_at": TS if answered else None,
                 "decision": "approved" if answered else None,
                 "answered_by": by if answered else None})


def finding_row(eid: str, fid: str, sid: str, source: str = "external",
                cls: str = "reasoning",
                disposition: str = "fix_in_revision",
                logic_touched: bool = True, **over) -> dict:
    evt = {"event": "finding", "event_id": eid, "recorded_at": TS,
           "provenance": "live", "finding_id": fid, "story_id": sid,
           "source": source, "class": cls, "disposition": disposition,
           "logic_touched": logic_touched, "validation": None}
    evt.update(over)
    return norm(evt)


def dispatch_row(eid: str, sid: str, role: str, attempt: int = 1,
                 closed: bool = True, unix: int = 1758800000) -> dict:
    evt = {"event": "dispatch", "event_id": f"{sid}-{role}-{attempt}-{unix}",
           "recorded_at": TS,
           "provenance": "backfilled" if closed else "live",
           "story_id": sid, "role": role, "attempt": attempt,
           "requested_model": "model-a", "reported_model": None,
           "model_unverified": not closed, "started_at": TS,
           "prompt_version": "v1", "ended_at": None, "outcome": None}
    if closed:
        evt.update({"reported_model": "model-a", "model_unverified": False,
                    "ended_at": TS, "outcome": "success"})
    return norm(evt)


def deviation_row(eid: str, sid: str, contract_field: str,
                  approved_by: str | None = None) -> dict:
    return norm({"event": "deviation", "event_id": eid, "recorded_at": TS,
                 "provenance": "live", "story_id": sid,
                 "contract_field": contract_field, "planned": "planned",
                 "actual": "actual", "justification": "because",
                 "approved_by": approved_by})


def loop_row(eid: str, sid: str, stage: str, n: int) -> dict:
    return norm({"event": "loop", "event_id": eid, "recorded_at": TS,
                 "provenance": "live", "story_id": sid, "stage": stage,
                 "loop_n": n, "class": "reasoning",
                 "root_cause_one_line": "test root cause"})


def stage_row(eid: str, sid: str, stage: str, **over) -> dict:
    evt = {"event": "stage", "event_id": eid, "recorded_at": TS,
           "provenance": "live", "story_id": sid, "stage": stage}
    evt.update(over)
    return norm(evt)


def outcome_row(eid: str, sid: str, merged_sha: str) -> dict:
    return norm({"event": "outcome", "event_id": eid, "recorded_at": TS,
                 "provenance": "live", "story_id": sid,
                 "merged_sha": merged_sha, "merged_at": TS})


def claim_row(eid: str, sid: str, owner: str = "worker-1",
              start_sha: str | None = "a" * 40,
              start_shas: dict | None = None, **over) -> dict:
    evt = {"event": "assignment", "event_id": eid, "recorded_at": TS,
           "provenance": "live", "story_id": sid, "op": "claim",
           "owner": owner, "host": "host-a", "session": "sess-1",
           "claimed_at": TS}
    if start_shas is not None:
        evt["start_shas"] = start_shas
    else:
        evt["start_sha"] = start_sha
    evt.update(over)
    return evt


def handover_row(eid: str, sid: str, frm: str, to: str,
                 flagged: bool = False) -> dict:
    return {"event": "assignment", "event_id": eid, "recorded_at": TS,
            "provenance": "live", "story_id": sid, "op": "handover",
            "from": frm, "to": to, "reason": "test handover",
            "flagged": flagged}


def release_row(eid: str, sid: str, owner: str = "worker-1") -> dict:
    return {"event": "assignment", "event_id": eid, "recorded_at": TS,
            "provenance": "live", "story_id": sid, "op": "release",
            "owner": owner}


def ctx_row(eid: str, sid: str, role: str, sha40: str = "a" * 40) -> dict:
    return norm({"event": "review_context", "event_id": eid,
                 "recorded_at": TS, "provenance": "live", "story_id": sid,
                 "review_role": role, "reviewed_sha": sha40,
                 "base_sha": sha40,
                 "context_manifest_path": f"stories/{sid}/review/manifest.json",
                 "context_manifest_digest": "d" * 64,
                 "prompt_version": "v1", "dispatch_id": None,
                 "delivery_surface": "dispatch-package",
                 "requested_at": TS})


def attribution_row(eid: str, sid: str, fid: str, internal: str,
                    external: str) -> dict:
    return norm({"event": "review_attribution", "event_id": eid,
                 "recorded_at": TS, "provenance": "live", "story_id": sid,
                 "finding_id": fid, "internal_context_event_id": internal,
                 "external_context_event_id": external,
                 "classification": "MISSED_WITH_CONTEXT",
                 "classified_by": "orchestrator",
                 "evidence_path": f"stories/{sid}/review/attribution.md"})


def load_fixture_jsonl(path: Path, sid: str) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            e["story_id"] = sid
            rows.append(e)
    return rows


class ControlHome(unittest.TestCase):
    """Base: a tempdir control home (git repo, fixed author env) with the
    fixture workspace.toml (approvers: approver-one, human-ops)."""

    sid = STORY

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        shutil.copy(FIXTURES / "workspace.toml", self.root / "workspace.toml")
        (self.root / "stories").mkdir()
        self.stories = self.root / "stories"
        self.ledger = self.root / "factory" / "ledger" / "events.jsonl"
        self.git("init", "-q", "-b", "main")
        self.git("add", "workspace.toml")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")

    def tearDown(self):
        self._td.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.root), *args],
                              env=GIT_ENV, check=True, capture_output=True,
                              text=True)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def commit_all(self, msg: str = "test") -> str:
        self.git("add", "-A")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.test",
                 "-c", "commit.gpgsign=false", "commit", "-q",
                 "--allow-empty", "-m", msg)
        return self.head()

    def seed(self, events: list[dict]) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("w", encoding="utf-8", newline="\n") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def ledger_bytes(self) -> bytes:
        return self.ledger.read_bytes() if self.ledger.exists() else b""

    def ctrl(self) -> CT.Controller:
        return CT.Controller(self.root)

    def req(self, to: str, ev: dict | None = None, sid: str | None = None,
            approved_by: str | None = None) -> tuple[int, str]:
        c = self.ctrl()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = c.request(sid or self.sid, to, ev or {}, "orchestrator",
                           approved_by)
        return rc, buf.getvalue()

    def run_main(self, *argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = CT.main(["--root", str(self.root), *argv])
        return rc, buf.getvalue()

    def story_file(self, sid: str | None = None, name: str = "story.md",
                   fixture: str = "story_good.md") -> Path:
        d = self.stories / (sid or self.sid)
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        shutil.copy(FIXTURES / fixture, p)
        return p

    def write_override(self, text: str, commit: bool = True) -> str | None:
        p = self.root / "factory" / "locks" / "OVERRIDE"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if commit:
            self.commit_all("override")
            return self.git("hash-object",
                            "factory/locks/OVERRIDE").stdout.strip()
        return None

    def refused(self, rc: int, out: str, before: bytes, *named: str) -> None:
        """Assert a refusal named every check AND left the ledger untouched."""
        self.assertEqual(rc, 1, out)
        self.assertIn("REFUSED", out)
        for name in named:
            self.assertIn(name, out)
        self.assertEqual(self.ledger_bytes(), before)

    # -- review-record fixtures (S3.5b cutover) ------------------------------
    # The record AUTHORITY (record.py check, a real subprocess inside
    # record_gate) is stubbed here — controller tests already stub gate
    # scripts; the one real-subprocess integration lives in
    # tools/tests/test_review_gate.py.
    def stub_record_authority(self, errors: list[str] | None = None) -> None:
        p = mock.patch.object(CT.record_gate, "check_record",
                              return_value=list(errors or []))
        p.start()
        self.addCleanup(p.stop)

    def fake_record(self, rel: str = "review/record-1", head: str | None = None,
                    schema: str = "review.record.v2", pr: int = 42,
                    findings: list | None = None,
                    dispositions: list | None = None,
                    verdict: dict | None = None) -> tuple[str, str, str]:
        """Write a minimal review record under the control home and stub the
        authority to accept it. Returns (rel, sha256(review.json), head)."""
        head = head if head is not None else self.head()
        self.stub_record_authority()
        d = self.root / rel
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "schema": schema, "story": self.sid, "pr": pr,
            "base_sha": "0" * 40, "head_sha": head,
            "package": {"path": "package.md", "sha256": "f" * 64},
            "lane": {"name": "review-lane-v1", "version": 2},
            "reviewers": [], "gate_results": [],
            "findings": findings or [], "dispositions": dispositions or [],
            "prior_rounds": [], "open_findings": [], "undispositioned": [],
            "verdict": verdict or {"decision": "APPROVED", "blockers": 0,
                                   "majors": 0, "minors": 0, "infos": 0,
                                   "loop": 1, "loop_cap": 2},
        }
        (d / "review.json").write_text(
            json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return rel, CT.record_gate.record_sha256(str(d)), head

    def draft_row(self, pr: str = "42", mech: str = "review-lane",
                  eid: str = "tr-draft",
                  sid: str | None = None) -> dict:
        """A DRAFT_OPENED transition row carrying its evidence detail (the
        controller reads pr_number back out of it at REVIEWING)."""
        return transition_row(eid, sid or self.sid, "BUILD_GREEN",
                              "DRAFT_OPENED", check_results={
                                  "order": {"ok": True, "detail": "ok"},
                                  "evidence_supplied": {
                                      "ok": True,
                                      "detail": {
                                          "pr_number": pr,
                                          "review_request_mechanism": mech}},
                              })


GOOD_OVERRIDE = ("scope: STORY-001\nby: human-ops\n"
                 "reason: stuck claim lock recovery\ndate: 2026-10-25T00:00:00Z\n")


def prep_spec_frozen(home: "ControlHome", sid: str | None = None,
                     commit_plan: bool = True) -> tuple[str, str]:
    """Story dir with test-plan.md + spec.md committed (or not), seeded at
    TEST_PLAN. Returns (sid, spec_freeze_sha candidate)."""
    sid = sid or home.sid
    d = home.stories / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "test-plan.md").write_text("# test plan\n", encoding="utf-8")
    (d / "spec.md").write_text("# spec\n", encoding="utf-8")
    fsha = home.commit_all("plan") if commit_plan else home.head()
    home.seed(chain_to("TEST_PLAN", sid))
    return sid, fsha


# ------------------------------------------------------- evidence intake ----

class TestEvidenceKeyRefusals(ControlHome):
    def test_every_state_refuses_without_its_evidence_keys(self):
        """Spot-check the FULL set against state_machine.EVIDENCE_KEYS:
        each state with required keys refuses (rc 2) naming every key, and
        writes nothing."""
        before = self.ledger_bytes()
        for state, keys in SM.EVIDENCE_KEYS.items():
            if not keys:
                continue
            rc, out = self.req(state, {}, sid="STORY-777")
            self.assertEqual(rc, 2, (state, out))
            self.assertIn("requires evidence", out)
            for k in keys:
                self.assertIn(k, out)
            self.assertEqual(self.ledger_bytes(), before)

    def test_evidence_keys_match_state_machine_exactly(self):
        self.assertIs(CT.EVIDENCE_KEYS, SM.EVIDENCE_KEYS)

    def test_supplied_evidence_persists_into_check_results(self):
        self.story_file()
        rc, out = self.req("GRILLED")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(row["check_results"]["evidence_supplied"]["detail"],
                         {})
        # evidence k=v rides the transition event
        self.seed(chain_to("GATE_PASSED"))
        rc, out = self.req("PLAN_APPROVED", {"verdict": "PASS"})
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(row["check_results"]["evidence_supplied"]["detail"],
                         {"verdict": "PASS"})
        self.assertEqual(row["from_state"], "GATE_PASSED")
        self.assertEqual(row["requested_by"], "orchestrator")


# ------------------------------------------------------ evidence formats ----

class TestEvidenceFormats(ControlHome):
    def test_build_green_commit_sha_shape_and_resolution(self):
        self.seed(chain_to("CODING") + [
            claim_row("cl-1", self.sid),
            dispatch_row("d-1", self.sid, "coder")])
        for bad in ("zzzzzzz", "deadbeef"):
            before = self.ledger_bytes()
            rc, out = self.req("BUILD_GREEN", {
                "commit_sha": bad, "build_result": "green",
                "named_tests_result": "green"})
            self.refused(rc, out, before, "commit_sha")
        # an EMPTY required key is the evidence-intake refusal (rc 2)
        before = self.ledger_bytes()
        rc, out = self.req("BUILD_GREEN", {
            "commit_sha": "", "build_result": "green",
            "named_tests_result": "green"})
        self.assertEqual(rc, 2, out)
        self.assertIn("requires evidence", out)
        self.assertEqual(self.ledger_bytes(), before)
        rc, out = self.req("BUILD_GREEN", {
            "commit_sha": self.head()[:7], "build_result": "green",
            "named_tests_result": "green"})
        self.assertEqual(rc, 0, out)

    def test_build_green_result_enums(self):
        self.seed(chain_to("CODING") + [
            claim_row("cl-1", self.sid),
            dispatch_row("d-1", self.sid, "coder")])
        before = self.ledger_bytes()
        rc, out = self.req("BUILD_GREEN", {
            "commit_sha": self.head(), "build_result": "red",
            "named_tests_result": "green"})
        self.refused(rc, out, before, "build_result")
        rc, out = self.req("BUILD_GREEN", {
            "commit_sha": self.head(), "build_result": "green",
            "named_tests_result": "flaky"})
        self.refused(rc, out, before, "named_tests")

    def test_tested_sha_prefix_match(self):
        head = self.head()
        rel, _, _ = self.fake_record(head=head)
        self.seed(chain_to("READY_FOR_REVIEW") + [
            gate_row("g-1", self.sid, "merge")])
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", {
            "tested_sha": "b" * 7, "head_sha": head,
            "unresolved_threads": "0", "internal_verdict": "PASS",
            "review_record": rel})
        self.refused(rc, out, before, "tested_sha_is_head")
        # a 7-hex prefix of the head passes (prefix match)
        rc, out = self.req("MERGE_ELIGIBLE", {
            "tested_sha": head[:7], "head_sha": head,
            "unresolved_threads": "0", "internal_verdict": "PASS",
            "review_record": rel})
        self.assertEqual(rc, 0, out)

    def test_merge_eligible_zero_threads_and_gate(self):
        head = self.head()
        rel, _, _ = self.fake_record(head=head)
        self.seed(chain_to("READY_FOR_REVIEW"))
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", {
            "tested_sha": head[:7], "head_sha": head,
            "unresolved_threads": "1", "internal_verdict": "PASS",
            "review_record": rel})
        self.refused(rc, out, before, "zero_unresolved_threads",
                     "merge_approval")
        # a pending gate row does not satisfy the human merge gate
        self.seed(chain_to("READY_FOR_REVIEW") + [
            gate_row("g-1", self.sid, "merge", answered=False)])
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", {
            "tested_sha": head[:7], "head_sha": head,
            "unresolved_threads": "0", "internal_verdict": "PASS",
            "review_record": rel})
        self.refused(rc, out, before, "merge_approval")

    def test_pr_number_digits_and_mechanism_enum(self):
        self.seed(chain_to("BUILD_GREEN"))
        before = self.ledger_bytes()
        rc, out = self.req("DRAFT_OPENED", {
            "pr_number": "abc", "review_request_mechanism": "review-lane"})
        self.refused(rc, out, before, "pr_number")
        rc, out = self.req("DRAFT_OPENED", {
            "pr_number": "42", "review_request_mechanism": "cc-mention"})
        self.refused(rc, out, before, "review_request_mechanism")
        # S3.5b cutover: the historical mechanisms are refused as NEW
        # evidence; review-lane is the only accepted value.
        for mech in SM.HISTORICAL_REVIEW_MECHANISMS:
            self.seed(chain_to("BUILD_GREEN"))
            before = self.ledger_bytes()
            rc, out = self.req("DRAFT_OPENED", {
                "pr_number": "42", "review_request_mechanism": mech})
            self.refused(rc, out, before, "review_request_mechanism")
            self.assertIn("review-lane", out)
        self.seed(chain_to("BUILD_GREEN"))
        rc, out = self.req("DRAFT_OPENED", {
            "pr_number": "42", "review_request_mechanism": "review-lane"})
        self.assertEqual(rc, 0, out)

    def test_no_duplicate_draft_opened(self):
        self.seed(chain_to("BUILD_GREEN"))
        rc, out = self.req("DRAFT_OPENED", {
            "pr_number": "42", "review_request_mechanism": "review-lane"})
        self.assertEqual(rc, 0, out)
        # a prior DRAFT_OPENED poisons any re-request: the order check
        # refuses, and the duplicate-trigger check is the named backstop
        self.seed(chain_to("BUILD_GREEN") + [
            transition_row("dup-1", self.sid, "CODING", "DRAFT_OPENED")])
        before = self.ledger_bytes()
        rc, out = self.req("DRAFT_OPENED", {
            "pr_number": "43", "review_request_mechanism": "review-lane"})
        self.assertEqual(rc, 1, out)
        self.assertEqual(self.ledger_bytes(), before)
        results = self.ctrl().check(self.sid, "DRAFT_OPENED", {
            "pr_number": "43", "review_request_mechanism": "review-lane"})
        self.assertIn("no_duplicate_trigger", results)
        self.assertFalse(results["no_duplicate_trigger"]["ok"])

    def test_verdict_enum(self):
        self.seed(chain_to("GATE_PASSED"))
        before = self.ledger_bytes()
        rc, out = self.req("PLAN_APPROVED", {"verdict": "FAIL"})
        self.refused(rc, out, before, "verdict_pass")
        for v in ("PASS", "PASS-WITH-NITS"):
            self.seed(chain_to("GATE_PASSED"))
            rc, out = self.req("PLAN_APPROVED", {"verdict": v})
            self.assertEqual(rc, 0, out)

    def test_ready_for_review_verdict_threads_and_pushes(self):
        base = chain_to("FULL_TESTS_GREEN")
        rel, _, head = self.fake_record()
        ev_ok = {"internal_verdict": "PASS", "threads_state": "all-resolved",
                 "fix_pushes": "1", "r1_arrival": "both-pre-edit",
                 "review_record": rel, "review_head_sha": head}
        before = None
        for bad in ({**ev_ok, "internal_verdict": "FAIL"},
                    {**ev_ok, "threads_state": "resolved"},
                    {**ev_ok, "threads_state": "watchdog-exception"},
                    {**ev_ok, "r1_arrival": "pre-edit"},
                    {**ev_ok, "fix_pushes": "2"},
                    {**ev_ok, "fix_pushes": "x"}):
            self.seed(base)
            before = self.ledger_bytes()
            rc, out = self.req("READY_FOR_REVIEW", bad)
            self.assertEqual(rc, 1, (bad, out))
            self.assertEqual(self.ledger_bytes(), before)
        # watchdog exception WITH the logged marker passes
        self.seed(base + [stage_row("s-1", self.sid, "external_review",
                                    evidence_path=(
                                        "logs/external-review-unavailable.txt"
                                    ))])
        rc, out = self.req("READY_FOR_REVIEW", {
            **ev_ok, "threads_state": "watchdog-exception"})
        self.assertEqual(rc, 0, out)
        # 2 pushes with r1_arrival=other is the target-not-constraint path
        self.seed(base)
        rc, out = self.req("READY_FOR_REVIEW", {
            **ev_ok, "fix_pushes": "2", "r1_arrival": "other"})
        self.assertEqual(rc, 0, out)

    def test_ready_for_review_record_head_rule(self):
        """READY_FOR_REVIEW refuses a v2 record whose pinned head differs
        from review_head_sha (no record-tail cover) and accepts exact."""
        base = chain_to("FULL_TESTS_GREEN")
        rel, _, head = self.fake_record()  # record pinned at the OLD head
        (self.stories / self.sid).mkdir(parents=True, exist_ok=True)
        (self.stories / self.sid / "late.py").write_text("x = 1\n",
                                                         encoding="utf-8")
        later = self.commit_all("code change after the record")
        self.assertNotEqual(later, head)
        ev = {"internal_verdict": "PASS", "threads_state": "all-resolved",
              "fix_pushes": "1", "r1_arrival": "both-pre-edit",
              "review_record": rel, "review_head_sha": later}
        self.seed(base)
        before = self.ledger_bytes()
        rc, out = self.req("READY_FOR_REVIEW", ev)
        self.refused(rc, out, before, "review_head_match",
                     "review_record_valid")
        # exact head passes
        rc, out = self.req("READY_FOR_REVIEW", {**ev,
                                                "review_head_sha": head})
        self.assertEqual(rc, 0, out)

    def test_ready_for_review_refuses_open_blocker_major(self):
        base = chain_to("FULL_TESTS_GREEN")
        rel, _, head = self.fake_record(
            findings=[{"id": "RA-001@m", "severity": "blocker"}],
            verdict={"decision": "CHANGES_REQUESTED", "blockers": 1,
                     "majors": 0, "minors": 0, "infos": 0,
                     "loop": 1, "loop_cap": 2})
        ev = {"internal_verdict": "PASS", "threads_state": "all-resolved",
              "fix_pushes": "1", "r1_arrival": "both-pre-edit",
              "review_record": rel, "review_head_sha": head}
        self.seed(base)
        before = self.ledger_bytes()
        rc, out = self.req("READY_FOR_REVIEW", ev)
        self.refused(rc, out, before, "no_open_blocker_major",
                     "review_record_valid")
        self.assertIn("RA-001@m", out)
        # a matching disposition clears it: open_blocker_majors is empty and
        # the CHANGES_REQUESTED verdict is consistent with blockers=1, so
        # the record gate passes.
        rel2, _, head2 = self.fake_record(
            rel="review/record-2",
            findings=[{"id": "RA-001@m", "severity": "blocker"}],
            dispositions=[{"id": "RA-001@m", "disposition": "fixed",
                           "fix_sha": "a" * 40}],
            verdict={"decision": "CHANGES_REQUESTED", "blockers": 1,
                     "majors": 0, "minors": 0, "infos": 0,
                     "loop": 1, "loop_cap": 2})
        self.seed(base)
        rc, out = self.req("READY_FOR_REVIEW", {
            **ev, "review_record": rel2, "review_head_sha": head2})
        self.assertEqual(rc, 0, out)

    def test_full_tests_green_zero_rules_and_membership(self):
        base = chain_to("FIXING")
        ev_ok = {"projects": "app,lib", "tests_passed": "120",
                 "tests_failed": "0", "tests_skipped": "2",
                 "exclusions": "slow-e2e: needs cluster", "exit_code": "0"}
        self.seed(base)
        before = self.ledger_bytes()
        rc, out = self.req("FULL_TESTS_GREEN", {**ev_ok, "exit_code": "1"})
        self.refused(rc, out, before, "exit_zero")
        rc, out = self.req("FULL_TESTS_GREEN", {**ev_ok, "tests_failed": "3"})
        self.refused(rc, out, before, "failed_zero")
        rc, out = self.req("FULL_TESTS_GREEN", ev_ok)
        self.assertEqual(rc, 0, out)

    def test_merged_sha_must_resolve(self):
        self.seed(chain_to("MERGE_ELIGIBLE"))
        before = self.ledger_bytes()
        rc, out = self.req("MERGED", {
            "merged_sha": "deadbeef", "merged_at": TS, "pr_number": "42"})
        self.refused(rc, out, before, "merged_sha")
        rc, out = self.req("MERGED", {
            "merged_sha": self.head(), "merged_at": TS, "pr_number": "42"})
        self.assertEqual(rc, 0, out)

    def test_live_proof_path_or_url(self):
        self.seed(chain_to("MERGED"))
        before = self.ledger_bytes()
        rc, out = self.req("LIVE_PROVEN", {
            "live_proof_evidence": "stories/STORY-001/proof/missing.md"})
        self.refused(rc, out, before, "live_proof_evidence")
        proof = self.stories / self.sid / "proof"
        proof.mkdir(parents=True)
        (proof / "live.md").write_text("proof\n", encoding="utf-8")
        rc, out = self.req("LIVE_PROVEN", {
            "live_proof_evidence": f"stories/{self.sid}/proof/live.md"})
        self.assertEqual(rc, 0, out)
        self.seed(chain_to("MERGED"))
        rc, out = self.req("LIVE_PROVEN", {
            "live_proof_evidence": "https://example.test/proof"})
        self.assertEqual(rc, 0, out)


# ------------------------------------------- review record gate (S3.5b) ----

class TestReviewingRecordEvidence(ControlHome):
    """REVIEWING requires the record triple: path + sha256 + pinned head,
    with the record authority stubbed (the real subprocess integration is
    tools/tests/test_review_gate.py)."""

    def reviewing_ev(self, rel, sha, head):
        return {"review_record": rel, "review_record_sha": sha,
                "review_head_sha": head}

    def seed_at_draft(self, pr="42"):
        self.seed(chain_to("BUILD_GREEN") + [self.draft_row(pr=pr)])

    def test_reviewing_accepts_valid_record(self):
        rel, sha, head = self.fake_record(pr=42)
        self.seed_at_draft("42")
        rc, out = self.req("REVIEWING", self.reviewing_ev(rel, sha, head))
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        for key in ("review_record_path", "review_record_sha",
                    "review_head_match", "review_record_valid",
                    "review_record_pr"):
            self.assertIn(key, row["checks_run"])
            self.assertTrue(row["check_results"][key]["ok"], key)

    def test_reviewing_refuses_missing_evidence_keys(self):
        rel, sha, head = self.fake_record()
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING", {"review_record": rel})
        self.assertEqual(rc, 2, out)
        self.assertIn("requires evidence", out)
        for key in ("review_record_sha", "review_head_sha"):
            self.assertIn(key, out)
        self.assertEqual(self.ledger_bytes(), before)

    def test_reviewing_refuses_missing_record_path(self):
        rel, sha, head = self.fake_record()
        (self.root / rel / "review.json").unlink()
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING", self.reviewing_ev(rel, sha, head))
        self.refused(rc, out, before, "review_record_path")

    def test_reviewing_refuses_absolute_and_escaping_paths(self):
        rel, sha, head = self.fake_record()
        abs_path = str((self.root / rel).resolve())
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING",
                           self.reviewing_ev(abs_path, sha, head))
        self.refused(rc, out, before, "review_record_path")
        # `..` escape (exists outside the control home)
        outside = self.root.parent / "escaped-record"
        outside.mkdir(exist_ok=True)
        shutil.copy(self.root / rel / "review.json",
                    outside / "review.json")
        rc, out = self.req("REVIEWING",
                           self.reviewing_ev("../escaped-record", sha, head))
        self.refused(rc, out, before, "review_record_path")

    def test_reviewing_refuses_bad_sha(self):
        rel, sha, head = self.fake_record()
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING",
                           self.reviewing_ev(rel, "e" * 64, head))
        self.refused(rc, out, before, "review_record_sha")
        self.assertIn(sha, out)  # names the actual digest

    def test_reviewing_refuses_head_mismatch(self):
        rel, sha, head = self.fake_record()
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING",
                           self.reviewing_ev(rel, sha, "d" * 40))
        self.refused(rc, out, before, "review_head_match")
        # a 7-hex prefix of the record head passes (7-40 hex evidence)
        rc, out = self.req("REVIEWING",
                           self.reviewing_ev(rel, sha, head[:7]))
        self.assertEqual(rc, 0, out)

    def test_reviewing_refuses_pr_mismatch(self):
        rel, sha, head = self.fake_record(pr=42)
        self.seed_at_draft("43")
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING", self.reviewing_ev(rel, sha, head))
        self.refused(rc, out, before, "review_record_pr")

    def test_reviewing_refuses_invalid_record_per_authority(self):
        rel, sha, head = self.fake_record()
        # the authority (record.py check, stubbed) names a failure
        self.stub_record_authority(
            ["review record: package hash mismatch: recorded x, computed y"])
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING", self.reviewing_ev(rel, sha, head))
        self.refused(rc, out, before, "review_record_valid")
        self.assertIn("package hash mismatch", out)

    def test_reviewing_refuses_unparseable_review_json(self):
        rel, sha, head = self.fake_record()
        (self.root / rel / "review.json").write_text("{not json\n",
                                                     encoding="utf-8")
        self.seed_at_draft()
        before = self.ledger_bytes()
        rc, out = self.req("REVIEWING", self.reviewing_ev(rel, sha, head))
        self.refused(rc, out, before, "review_record_valid")


class TestHistoricalMechanismFold(ControlHome):
    """Ledgers whose DRAFT_OPENED row carries a historical mechanism still
    fold forward: the read path never re-judges recorded transitions."""

    def historical_chain(self, mech):
        rows = chain_to("BUILD_GREEN")
        rows.append(self.draft_row(pr="42", mech=mech))
        rows.extend(transition_row(f"tr-h-{i}", self.sid, frm, to)
                    for i, (frm, to) in enumerate(CHAIN[10:15], 1))
        # CHAIN[10] is ("DRAFT_OPENED", "REVIEWING") ... through MERGED
        rows.append(transition_row("tr-h-6", self.sid,
                                   "MERGE_ELIGIBLE", "MERGED"))
        return rows

    def test_ready_push_history_folds_to_merged(self):
        for mech in SM.HISTORICAL_REVIEW_MECHANISMS:
            self.seed(self.historical_chain(mech))
            self.assertEqual(self.ctrl().state(self.sid), "MERGED", mech)

    def test_no_rewrite_of_history(self):
        self.seed(self.historical_chain("ready-push"))
        before = self.ledger_bytes()
        # reading state (and rendering) never touches the ledger
        self.assertEqual(self.ctrl().state(self.sid), "MERGED")
        self.assertEqual(self.ledger_bytes(), before)
        # the historical value survives verbatim in the stored row
        row = [e for e in L.load_events(self.ledger)
               if e.get("to_state") == "DRAFT_OPENED"][0]
        detail = row["check_results"]["evidence_supplied"]["detail"]
        self.assertEqual(detail["review_request_mechanism"], "ready-push")

    def test_later_transitions_from_history_are_not_refused(self):
        # a story parked at REVIEWING under the historical mechanism still
        # moves forward (FIXING is next-in-sequence; no record evidence)
        rows = chain_to("BUILD_GREEN")
        rows.append(self.draft_row(pr="42", mech="draft-mention-opportunistic"))
        rows.append(transition_row("tr-h-1", self.sid,
                                   "DRAFT_OPENED", "REVIEWING"))
        self.seed(rows)
        rc, out = self.req("FIXING")
        self.assertEqual(rc, 0, out)


class TestMergeEligibleRecordHeadRule(ControlHome):
    """MERGE_ELIGIBLE: the record must cover head_sha — exact, a v2
    record-only tail, or a v1 ancestor. Real git commits in the temp
    control-home repo; the record authority is stubbed."""

    def seed_ready(self):
        self.seed(chain_to("READY_FOR_REVIEW") + [
            gate_row("g-1", self.sid, "merge")])

    def merge_ev(self, head, rel):
        return {"tested_sha": head[:7], "head_sha": head,
                "unresolved_threads": "0", "internal_verdict": "PASS",
                "review_record": rel}

    def test_v2_record_tail_commit_accepted(self):
        rel, _, _ = self.fake_record(rel="review/rec-tail")
        pinned = self.head()
        # rewrite the record pinned to the CURRENT head, then add a
        # record-only commit: the only changed path is under the record dir
        data = json.loads(
            (self.root / rel / "review.json").read_text(encoding="utf-8"))
        data["head_sha"] = pinned
        (self.root / rel / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        tail = self.commit_all("record tail: amend REVIEW md")
        self.seed_ready()
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(tail, rel))
        self.assertEqual(rc, 0, out)

    def test_v2_later_code_commit_refused(self):
        rel, _, _ = self.fake_record(rel="review/rec-code")
        pinned = self.head()
        data = json.loads(
            (self.root / rel / "review.json").read_text(encoding="utf-8"))
        data["head_sha"] = pinned
        (self.root / rel / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        (self.stories / self.sid).mkdir(parents=True, exist_ok=True)
        (self.stories / self.sid / "code.py").write_text("x = 1\n",
                                                         encoding="utf-8")
        later = self.commit_all("code change after the record")
        self.seed_ready()
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(later, rel))
        self.refused(rc, out, before, "review_head_match",
                     "review_record_valid")
        self.assertIn("code.py", out)

    def test_v1_ancestor_accepted_and_non_ancestor_refused(self):
        rel, _, ancestor = self.fake_record(
            rel="review/rec-v1", schema="review.record.v1")
        data = json.loads(
            (self.root / rel / "review.json").read_text(encoding="utf-8"))
        data["head_sha"] = ancestor
        data["lane"]["version"] = 1
        (self.root / rel / "review.json").write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        later = self.commit_all("a later commit on top")
        self.seed_ready()
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(later, rel))
        self.assertEqual(rc, 0, out)
        # non-ancestor: an unrelated (orphan) head — the record head is
        # not an ancestor of it, so the v1 rule refuses
        self.git("checkout", "-q", "--orphan", "divergent")
        self.git("-c", "user.name=t", "-c", "user.email=t@t",
                 "-c", "commit.gpgsign=false", "commit", "-q",
                 "--allow-empty", "-m", "divergent root")
        divergent = self.head()
        self.git("checkout", "-q", "main")
        self.seed_ready()
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE",
                           self.merge_ev(divergent, rel))
        self.refused(rc, out, before, "review_head_match")
        self.assertIn("not an ancestor", out)


# ------------------------------------------------------------ order rules ----

class TestOrderRules(ControlHome):
    def test_skipping_states_refused(self):
        self.seed(chain_to("SPEC_FROZEN"))
        before = self.ledger_bytes()
        rc, out = self.req("PLAN_APPROVED", {"verdict": "PASS"})
        self.refused(rc, out, before, "order")

    def test_fixing_reentry_only_from_post_review_states(self):
        for frm in ("GRILLED", "PLAN_APPROVED", "CODING", "DRAFT_OPENED",
                    "REVIEWING"):
            pass  # REVIEWING -> FIXING is next-in-sequence, handled below
        for frm in ("GRILLED", "PLAN_APPROVED", "CODING", "DRAFT_OPENED"):
            self.seed([transition_row("t-1", self.sid, "BACKLOG", frm)])
            before = self.ledger_bytes()
            rc, out = self.req("FIXING")
            self.refused(rc, out, before, "order")
        # legal: next-in-sequence from REVIEWING ...
        self.seed(chain_to("REVIEWING"))
        rc, out = self.req("FIXING")
        self.assertEqual(rc, 0, out)
        # ... and reentry from each post-review state
        for frm in sorted(SM.FIXING_REENTRY_FROM):
            self.seed(chain_to(frm) if frm != "MERGE_ELIGIBLE"
                      else chain_to("MERGE_ELIGIBLE"))
            # re-seed without the trailing rows that overshoot FIXING
            rows = []
            for r in chain_to(frm):
                rows.append(r)
            self.seed(rows)
            rc, out = self.req("FIXING")
            self.assertEqual(rc, 0, (frm, out))

    def test_ui_skip_fail_closed(self):
        claim = claim_row("cl-1", self.sid)
        self.seed(chain_to("PLAN_APPROVED") + [claim])
        before = self.ledger_bytes()
        # no story.md => treated as UI => refused
        rc, out = self.req("CODING")
        self.refused(rc, out, before, "order", "non_ui_declared")
        # explicit UI declaration => refused
        d = self.stories / self.sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "story.md").write_text("# s\nUI: yes\n", encoding="utf-8")
        rc, out = self.req("CODING")
        self.refused(rc, out, before, "order", "non_ui_declared")
        # declared non-UI => skip legal (claim gate satisfied)
        (d / "story.md").write_text("# s\nUI: none\n", encoding="utf-8")
        rc, out = self.req("CODING")
        self.assertEqual(rc, 0, out)

    def test_blocked_resume_at_originating_state(self):
        self.story_file()
        self.seed(chain_to("GRILLED"))
        rc, out = self.run_main("blocked", self.sid,
                                "--reason", "loop cap reached",
                                "--gate", "loop_cap")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.ctrl().state(self.sid), "BLOCKED")
        self.assertEqual(self.ctrl().blocked_from(self.sid), "GRILLED")
        before = self.ledger_bytes()
        # any other target is refused while blocked
        rc, out = self.req("TEST_PLAN")
        self.refused(rc, out, before, "block_resumption")
        # resume targets the originating state
        rc, out = self.req("GRILLED")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-2]  # the BLOCKED transition
        self.assertEqual(row["to_state"], "BLOCKED")
        self.assertEqual(row["check_results"]["escalation_gate"]["detail"],
                         "gate=loop_cap")
        self.assertIn("loop cap reached",
                      row["check_results"]["block_reason"]["detail"])

    def test_blocked_from_any_state_via_request(self):
        self.seed(chain_to("CODING"))
        rc, out = self.req("BLOCKED", {"reason": "dependency slipped"})
        self.assertEqual(rc, 0, out)
        rc, out = self.run_main("state", self.sid)
        self.assertIn("BLOCKED", out)
        self.assertIn("resumes there", out)

    def test_plan_loop_cap_refuses_and_names_escalation(self):
        loops = [loop_row(f"lp-{i}", self.sid, "plan_review", i)
                 for i in (1, 2, 3)]
        self.seed(chain_to("GATE_PASSED") + loops)
        before = self.ledger_bytes()
        rc, out = self.req("PLAN_APPROVED", {"verdict": "PASS"})
        self.refused(rc, out, before, "loop_cap")
        self.seed(chain_to("GATE_PASSED") + loops[:2])
        rc, out = self.req("PLAN_APPROVED", {"verdict": "PASS"})
        self.assertEqual(rc, 0, out)


# ------------------------------------------------- GRILLED / TEST_PLAN ----

class TestStoryAndGrillGates(ControlHome):
    def test_grilled_requires_story_md(self):
        before = self.ledger_bytes()
        rc, out = self.req("GRILLED")
        self.refused(rc, out, before, "story_md")

    def test_grilled_passes_with_valid_story(self):
        self.story_file()
        rc, out = self.req("GRILLED")
        self.assertEqual(rc, 0, out)

    def test_grilled_rejects_bad_stories(self):
        good = (FIXTURES / "story_good.md").read_text(encoding="utf-8")
        bad = {
            "no gwt": good.replace("- **When** it is submitted\n", ""),
            "no signal": good.replace("Structural signal: new module", ""),
            "no acs": "# S\nStructural signal: x\n",
            "seven acs": "Structural signal: x\n" + "".join(
                f"### AC-{i}\nGiven a When b Then c\n" for i in range(1, 8)),
            "duplicate acs": "Structural signal: x\n" + "".join(
                f"### AC-{i}\nGiven a When b Then c\n" for i in (1, 1)),
        }
        d = self.stories / self.sid
        d.mkdir(parents=True, exist_ok=True)
        last_out = ""
        for name, text in bad.items():
            (d / "story.md").write_text(text, encoding="utf-8")
            before = self.ledger_bytes()
            rc, last_out = self.req("GRILLED")
            self.assertEqual(rc, 1, name)
            self.assertIn("story_md", last_out, name)
            self.assertEqual(self.ledger_bytes(), before)
        self.assertIn("duplicate AC ids: AC-1", last_out)

    def test_test_plan_requires_both_grill_records(self):
        self.story_file()
        shutil.copy(FIXTURES / "grill-dev.md",
                    self.stories / self.sid / "grill-dev.md")
        self.seed(chain_to("GRILLED"))
        before = self.ledger_bytes()
        rc, out = self.req("TEST_PLAN")
        self.refused(rc, out, before, "grill_qa")
        shutil.copy(FIXTURES / "grill-qa.md",
                    self.stories / self.sid / "grill-qa.md")
        (self.stories / self.sid / "grill-dev.md").unlink()
        rc, out = self.req("TEST_PLAN")
        self.refused(rc, out, before, "grill_dev")

    def test_test_plan_thresholds_and_signers(self):
        self.story_file()
        d = self.stories / self.sid
        shutil.copy(FIXTURES / "grill-qa.md", d / "grill-qa.md")
        self.seed(chain_to("GRILLED"))
        for rec in ("correct_pct: 79\nmajor_misunderstandings: 0\n"
                    "signed_by: approver-one\n",
                    "correct_pct: 95\nmajor_misunderstandings: 1\n"
                    "signed_by: approver-one\n",
                    "correct_pct: NaN\nmajor_misunderstandings: 0\n"
                    "signed_by: approver-one\n",
                    "correct_pct: 95\nmajor_misunderstandings: 0\n"
                    "signed_by: some-bot[bot]\n",
                    "correct_pct: 95\nmajor_misunderstandings: 0\n"
                    "signed_by: approver-one anything\n",
                    "correct_pct: 95\nmajor_misunderstandings: 0\n"
                    "signed_by: approver-one (unclosed\n"):
            (d / "grill-dev.md").write_text(rec, encoding="utf-8")
            before = self.ledger_bytes()
            rc, out = self.req("TEST_PLAN")
            self.assertEqual(rc, 1, rec)
            self.assertIn("grill_dev", out)
            self.assertEqual(self.ledger_bytes(), before)

    def test_test_plan_reruns_story_md_and_logs_overlap(self):
        p = self.story_file()  # author approver-one == grill signer
        d = self.stories / self.sid
        shutil.copy(FIXTURES / "grill-dev.md", d / "grill-dev.md")
        shutil.copy(FIXTURES / "grill-qa.md", d / "grill-qa.md")
        self.seed(chain_to("GRILLED"))
        # story.md edited after GRILLED must be re-validated
        p.write_text("# s\n", encoding="utf-8")
        before = self.ledger_bytes()
        rc, out = self.req("TEST_PLAN")
        self.refused(rc, out, before, "story_md")
        self.story_file()  # restore; overlap is logged, never blocks
        rc, out = self.req("TEST_PLAN")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        detail = row["check_results"]["signer_author_overlap_logged"]
        self.assertTrue(detail["ok"])
        self.assertIn("overlap=True", detail["detail"])


# ------------------------------------------------------- SPEC_FROZEN ----

class TestSpecFrozen(ControlHome):
    def prep(self, sid: str | None = None, commit_plan: bool = True):
        return prep_spec_frozen(self, sid, commit_plan)

    def freeze_ev(self, sid: str, fsha: str, **over) -> dict:
        ev = {"spec_path": f"stories/{sid}/spec.md",
              "spec_freeze_sha": fsha, "sequence_reason": "test id"}
        ev.update(over)
        return ev

    def test_uncommitted_or_fake_sha_refused(self):
        sid, fsha = self.prep(commit_plan=False)
        before = self.ledger_bytes()
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha),
                           sid=sid, approved_by="approver-one")
        self.refused(rc, out, before, "test_plan_committed")
        sid, fsha = self.prep()
        rc, out = self.req("SPEC_FROZEN",
                           self.freeze_ev(sid, "deadbeef"), sid=sid,
                           approved_by="approver-one")
        self.refused(rc, out, before, "spec_freeze_sha",
                     "test_plan_committed")

    def test_spec_exists_and_alias_rules(self):
        sid, fsha = self.prep()
        before = self.ledger_bytes()
        rc, out = self.req("SPEC_FROZEN",
                           self.freeze_ev(sid, fsha,
                                          spec_path="stories/STORY-001/no.md"),
                           sid=sid, approved_by="approver-one")
        self.refused(rc, out, before, "spec_exists")
        (self.stories / "ALIAS.md").write_text(
            "| historical | note |\n|---|---|\n"
            "| STORY-001 | retired (test) |\n", encoding="utf-8")
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha),
                           sid=sid, approved_by="approver-one")
        self.refused(rc, out, before, "canonical_id")

    def test_human_approval_required(self):
        sid, fsha = self.prep()
        before = self.ledger_bytes()
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha), sid=sid)
        self.refused(rc, out, before, "spec_freeze_approval")
        for bad in ("some-bot[bot]", "superapprover-one",
                    "approver-one anything", "approver-one (unclosed"):
            rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha),
                               sid=sid, approved_by=bad)
            self.assertEqual(rc, 1, bad)
            self.assertIn("spec_freeze_approval", out)
            self.assertEqual(self.ledger_bytes(), before)
        for good in ("approver-one", "HUMAN-OPS",
                     "approver-one (chat 2026-10-25)"):
            rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha),
                               sid=sid, approved_by=good)
            self.assertEqual(rc, 0, (good, out))
            # reset for the next approver form
            self.seed(chain_to("TEST_PLAN", sid))

    def test_answered_gate_row_satisfies(self):
        sid, fsha = self.prep()
        self.seed(chain_to("TEST_PLAN", sid) + [
            gate_row("g-1", sid, "spec_freeze")])
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha), sid=sid)
        self.assertEqual(rc, 0, out)
        # an unanswered gate does not
        self.seed(chain_to("TEST_PLAN", sid) + [
            gate_row("g-1", sid, "spec_freeze", answered=False)])
        before = self.ledger_bytes()
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha), sid=sid)
        self.refused(rc, out, before, "spec_freeze_approval")

    def test_branch_convention(self):
        sid, fsha = self.prep()
        before = self.ledger_bytes()
        for bad in (f"feature/{sid}-x", f"story/{sid}-Bad_Slug",
                    f"story/{sid}-", "story/OTHER-001-slug"):
            rc, out = self.req("SPEC_FROZEN",
                               self.freeze_ev(sid, fsha, branch=bad),
                               sid=sid, approved_by="approver-one")
            self.assertEqual(rc, 1, bad)
            self.assertIn("branch_convention", out)
            self.assertEqual(self.ledger_bytes(), before)
        rc, out = self.req("SPEC_FROZEN",
                           self.freeze_ev(sid, fsha,
                                          branch=f"story/{sid}-good-slug"),
                           sid=sid, approved_by="approver-one")
        self.assertEqual(rc, 0, out)

    def test_empty_approver_list_fails_closed(self):
        (self.root / "workspace.toml").write_text(
            'workspace = "w"\ncontrol_home = "dedicated"\n'
            "[factory]\npin = \"<kit-tag>\"\n"
            "[[repos]]\nid = \"app\"\nrole = \"primary\"\n"
            "url = \"example.test/org/app\"\n[identity]\napprovers = []\n",
            encoding="utf-8")
        sid, fsha = self.prep()
        before = self.ledger_bytes()
        rc, out = self.req("SPEC_FROZEN", self.freeze_ev(sid, fsha), sid=sid,
                           approved_by="approver-one")
        self.refused(rc, out, before, "spec_freeze_approval")


# ------------------------------------------------------------ PLANNED ----

class TestPlanned(ControlHome):
    def plan(self, sid: str | None = None, block: str | None = "fixture"):
        sid = sid or self.sid
        d = self.stories / sid
        d.mkdir(parents=True, exist_ok=True)
        if block == "fixture":
            text = (CONTRACT_FIXTURES / "good_plan.md").read_text(
                encoding="utf-8")
        elif block is None:
            text = "# plan\n\nno contract block here\n"
        else:
            text = block
        (d / "impl-plan.md").write_text(text, encoding="utf-8")
        return sid

    def test_missing_spec_frozen_refused(self):
        sid = self.plan()
        self.seed([transition_row("t-1", sid, "BACKLOG", "GRILLED")])
        before = self.ledger_bytes()
        rc, out = self.req("PLANNED", sid=sid)
        self.refused(rc, out, before, "order", "spec_frozen")

    def test_contract_block_required_and_validated(self):
        sid = self.plan(block=None)
        self.seed(chain_to("SPEC_FROZEN", sid) + [
            dispatch_row("d-1", sid, "planner")])
        before = self.ledger_bytes()
        rc, out = self.req("PLANNED", sid=sid)
        self.refused(rc, out, before, "contract_block")
        sid = self.plan(block="# plan\n\n```contract.yaml\nnot: [valid\n```\n")
        self.seed(chain_to("SPEC_FROZEN", sid) + [
            dispatch_row("d-1", sid, "planner")])
        rc, out = self.req("PLANNED", sid=sid)
        self.refused(rc, out, before, "contract_block")
        sid = self.plan()
        self.seed(chain_to("SPEC_FROZEN", sid) + [
            dispatch_row("d-1", sid, "planner")])
        rc, out = self.req("PLANNED", sid=sid)
        self.assertEqual(rc, 0, out)

    def test_planner_lane_discipline(self):
        sid = self.plan()
        base = chain_to("SPEC_FROZEN", sid)
        cases = [
            ([], 1),  # no dispatch at all
            ([dispatch_row("d-1", sid, "planner", closed=False)], 1),
            ([deviation_row("dv-1", sid, "lane-skip:planner",
                            "orchestrator")], 1),
            ([deviation_row("dv-1", sid, "lane-skip:planner",
                            "superapprover-one")], 1),
            ([deviation_row("dv-1", sid, "files (planner split)",
                            "approver-one")], 1),
            ([deviation_row("dv-1", sid, "lane-skip:planner",
                            "approver-one (chat OK)")], 0),
            ([dispatch_row("d-1", sid, "planner")], 0),
        ]
        for extra, want in cases:
            self.seed(base + extra)
            before = self.ledger_bytes()
            rc, out = self.req("PLANNED", sid=sid)
            self.assertEqual(rc, want, (extra, out))
            if want:
                self.refused(rc, out, before, "planner_dispatch")

    def test_build_green_coder_lane(self):
        self.seed(chain_to("CODING") + [claim_row("cl-1", self.sid)])
        before = self.ledger_bytes()
        ev = {"commit_sha": self.head(), "build_result": "green",
              "named_tests_result": "green"}
        rc, out = self.req("BUILD_GREEN", ev)
        self.refused(rc, out, before, "coder_dispatch")
        self.seed(chain_to("CODING") + [claim_row("cl-1", self.sid),
                                        dispatch_row("d-1", self.sid,
                                                     "coder")])
        rc, out = self.req("BUILD_GREEN", ev)
        self.assertEqual(rc, 0, out)


# --------------------------------------------------------- GATE_PASSED ----

STUB_GATE = """import sys
print("stub-gate argv:", sys.argv[1:])
print("FINDING: AC-3 has no test")
sys.exit({code})
"""


class TestGatePassed(ControlHome):
    def place_gate(self, code: int, where: str = "factory") -> Path:
        d = self.root / where / "gates"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "pre_review_gate.py"
        p.write_text(STUB_GATE.format(code=code), encoding="utf-8")
        return p

    def test_missing_gate_script_refused(self):
        self.seed(chain_to("PLANNED"))
        before = self.ledger_bytes()
        rc, out = self.req("GATE_PASSED")
        self.refused(rc, out, before, "plan_gate")

    def test_gate_exit_zero_passes_and_receives_plan_argv(self):
        self.place_gate(0)
        self.seed(chain_to("PLANNED"))
        rc, out = self.req("GATE_PASSED")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        detail = row["check_results"]["plan_gate"]["detail"]
        self.assertIn("plan", detail)
        self.assertIn(str(self.stories / self.sid), detail)

    def test_gate_exit_one_refuses_and_surfaces_findings(self):
        self.place_gate(1)
        self.seed(chain_to("PLANNED"))
        before = self.ledger_bytes()
        rc, out = self.req("GATE_PASSED")
        self.refused(rc, out, before, "plan_gate")
        self.assertIn("FINDING: AC-3 has no test", out)

    def test_tools_gates_fallback_resolution(self):
        self.place_gate(0, where="tools")
        self.seed(chain_to("PLANNED"))
        rc, out = self.req("GATE_PASSED")
        self.assertEqual(rc, 0, out)
        # factory/gates wins when both exist
        self.place_gate(1, where="factory")
        self.seed(chain_to("PLANNED"))
        before = self.ledger_bytes()
        rc, out = self.req("GATE_PASSED")
        self.refused(rc, out, before, "plan_gate")


# ---------------------------------------------------- STORYBOOK / visual ----

class TestStorybookGate(ControlHome):
    def test_visual_gate_required(self):
        claim = claim_row("cl-1", self.sid)
        base = chain_to("PLAN_APPROVED") + [claim]
        cases = [
            ([], 1),
            ([gate_row("g-1", self.sid, "visual", answered=False)], 1),
            ([gate_row("g-1", self.sid, "visual", by="some-bot[bot]")], 1),
            ([gate_row("g-1", self.sid, "visual")], 0),
        ]
        for extra, want in cases:
            self.seed(base + extra)
            before = self.ledger_bytes()
            rc, out = self.req("STORYBOOK_APPROVAL")
            self.assertEqual(rc, want, (extra, out))
            if want:
                self.refused(rc, out, before, "storybook_visual_gate")
        # after approval, CODING is the normal next state (claim present)
        rc, out = self.req("CODING")
        self.assertEqual(rc, 0, out)


# --------------------------------------------------------- MERGE checks ----

class TestMergeEligibleFindings(ControlHome):
    def base(self, *extra):
        self.seed(chain_to("READY_FOR_REVIEW") + [
            gate_row("g-1", self.sid, "merge"), *extra])

    def ev(self):
        head = self.head()
        if not hasattr(self, "_rec_rel"):
            self._rec_rel, _, _ = self.fake_record(head=head)
        return {"tested_sha": head[:7], "head_sha": head,
                "unresolved_threads": "0", "internal_verdict": "PASS",
                "review_record": self._rec_rel}

    def test_external_findings_must_be_dispositioned(self):
        self.base(finding_row("f-1", "F-1", self.sid,
                              disposition="fix_in_revision"))
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", self.ev())
        self.refused(rc, out, before, "external_dispositioned")

    def test_latest_lifecycle_row_wins(self):
        self.base(finding_row("f-1", "F-1", self.sid,
                              disposition="fix_in_revision"),
                  finding_row("f-2", "F-1", self.sid, disposition="fixed",
                              fix_sha=self.head()[:7],
                              validation="suite green"))
        rc, out = self.req("MERGE_ELIGIBLE", self.ev())
        self.assertEqual(rc, 0, out)

    def test_attribution_required_when_context_present(self):
        self.base(finding_row("f-1", "F-1", self.sid, disposition="fixed",
                              fix_sha=self.head()[:7],
                              validation="suite green"),
                  ctx_row("rc-int", self.sid, "behavior_reviewer"),
                  ctx_row("rc-ext", self.sid, "external_review"))
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", self.ev())
        self.refused(rc, out, before, "external_context_attribution")
        self.base(finding_row("f-1", "F-1", self.sid, disposition="fixed",
                              fix_sha=self.head()[:7],
                              validation="suite green"),
                  ctx_row("rc-int", self.sid, "behavior_reviewer"),
                  ctx_row("rc-ext", self.sid, "external_review"),
                  attribution_row("ra-1", self.sid, "F-1", "rc-int",
                                  "rc-ext"))
        rc, out = self.req("MERGE_ELIGIBLE", self.ev())
        self.assertEqual(rc, 0, out)

    def test_valid_nit_passes(self):
        # a schema-legal nit (logic_touched=false + validation) is fine;
        # logic_touched=true + class=nit is banned at SCHEMA level, so it
        # can never enter a validated ledger to reach the controller check.
        self.base(finding_row("f-1", "F-1", self.sid, source="internal",
                              cls="nit", logic_touched=False,
                              disposition="fixed", validation="typo fixed"))
        rc, out = self.req("MERGE_ELIGIBLE", {**self.ev(),
                                              "internal_verdict":
                                                  "PASS-WITH-NITS"})
        self.assertEqual(rc, 0, out)


# -------------------------------------------------------------- CLOSED ----

class TestClosed(ControlHome):
    def close_ev(self):
        return {"doc_sync": "done", "closeout_checklist": "complete"}

    def test_closed_requires_outcome_and_checklist(self):
        self.seed(chain_to("LIVE_PROVEN"))
        before = self.ledger_bytes()
        rc, out = self.req("CLOSED", self.close_ev())
        self.refused(rc, out, before, "outcome_recorded")
        self.seed(chain_to("LIVE_PROVEN") + [
            outcome_row("oc-1", self.sid, self.head())])
        before = self.ledger_bytes()
        rc, out = self.req("CLOSED", {"doc_sync": "pending",
                                      "closeout_checklist": "partial"})
        self.refused(rc, out, before, "doc_sync", "closeout_checklist")

    def test_accepted_findings_need_validation(self):
        self.seed(chain_to("LIVE_PROVEN") + [
            outcome_row("oc-1", self.sid, self.head()),
            finding_row("f-1", "F-1", self.sid, disposition="accepted",
                        validation=None)])
        before = self.ledger_bytes()
        rc, out = self.req("CLOSED", self.close_ev())
        self.refused(rc, out, before, "findings_dispositioned")

    def test_docs_commit_alternative(self):
        self.seed(chain_to("LIVE_PROVEN") + [
            outcome_row("oc-1", self.sid, self.head())])
        rc, out = self.req("CLOSED", {"doc_sync": "owed",
                                      "docs_commit": "abc1234",
                                      "closeout_checklist": "complete"})
        self.assertEqual(rc, 0, out)

    def test_close_releases_open_claim(self):
        self.seed(chain_to("LIVE_PROVEN") + [
            claim_row("cl-1", self.sid, owner="worker-9"),
            outcome_row("oc-1", self.sid, self.head())])
        rc, out = self.req("CLOSED", self.close_ev())
        self.assertEqual(rc, 0, out)
        self.assertIn("claim released", out)
        events = L.load_events(self.ledger)
        rel = [e for e in events if e.get("event") == "assignment"
               and e.get("op") == "release"]
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel[0]["owner"], "worker-9")
        self.assertEqual(self.ctrl().state(self.sid), "CLOSED")


# ------------------------------------------------------- claim lifecycle ----

class TestClaimLifecycle(ControlHome):
    def test_coding_requires_open_claim(self):
        d = self.stories / self.sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "story.md").write_text("# s\nUI: none\n", encoding="utf-8")
        self.seed(chain_to("PLAN_APPROVED"))
        before = self.ledger_bytes()
        rc, out = self.req("CODING")
        self.refused(rc, out, before, "claim_exists")
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-1",
                                "--host", "host-a", "--session", "sess-1",
                                "--start-sha", "a" * 40)
        self.assertEqual(rc, 0, out)
        rc, out = self.req("CODING")
        self.assertEqual(rc, 0, out)

    def test_second_claim_refused_with_handover_path(self):
        self.seed([claim_row("cl-1", self.sid, owner="worker-1")])
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "host-b", "--session", "sess-2",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("open claim by 'worker-1'", out)
        self.assertIn("handover", out)
        self.assertEqual(self.ledger_bytes(), before)

    def test_handover_transfer(self):
        self.seed([claim_row("cl-1", self.sid, owner="worker-1")])
        # unflagged handover must come from the current owner
        before = self.ledger_bytes()
        rc, out = self.run_main("handover", self.sid, "--from", "ghost",
                                "--to", "worker-2", "--reason", "vacation")
        self.assertEqual(rc, 1, out)
        self.assertIn("--flagged", out)
        self.assertEqual(self.ledger_bytes(), before)
        rc, out = self.run_main("handover", self.sid, "--from", "worker-1",
                                "--to", "worker-2", "--reason", "vacation")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(row["op"], "handover")
        self.assertEqual(row["to"], "worker-2")
        self.assertIs(row["flagged"], False)
        # the claim is now worker-2's: worker-1 cannot release it
        rc, out = self.run_main("release", self.sid, "--owner", "worker-1")
        self.assertEqual(rc, 1, out)
        # flagged = abrupt takeover (from != current owner)
        rc, out = self.run_main("handover", self.sid, "--from", "worker-1",
                                "--to", "worker-3", "--reason", "unresponsive",
                                "--flagged")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertIs(row["flagged"], True)
        # and worker-3 can claim-conflict again... one open claim per story
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-4",
                                "--host", "h", "--session", "s",
                                "--start-sha", "c" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("worker-3", out)
        self.assertEqual(self.ledger_bytes(), before)

    def test_handover_without_claim_refused(self):
        before = self.ledger_bytes()
        rc, out = self.run_main("handover", self.sid, "--from", "a",
                                "--to", "b", "--reason", "x")
        self.assertEqual(rc, 1, out)
        self.assertIn("no open claim", out)
        self.assertEqual(self.ledger_bytes(), before)

    def test_release_and_reclaim(self):
        self.seed([claim_row("cl-1", self.sid, owner="worker-1")])
        rc, out = self.run_main("release", self.sid, "--owner", "worker-1")
        self.assertEqual(rc, 0, out)
        # released: a fresh claim is allowed, CODING gate re-closes
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 0, out)
        d = self.stories / self.sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "story.md").write_text("# s\nUI: none\n", encoding="utf-8")
        self.seed(L.load_events(self.ledger) + chain_to("PLAN_APPROVED"))
        # the chain re-seed dropped the claim rows? no — chain rows appended
        events = L.load_events(self.ledger)
        self.assertEqual(CT.open_claim_owner(events, self.sid), "worker-2")

    def test_claim_start_pin_exactly_one_of(self):
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "w",
                                "--host", "h", "--session", "s")
        self.assertEqual(rc, 2, out)
        rc, out = self.run_main("claim", self.sid, "--owner", "w",
                                "--host", "h", "--session", "s",
                                "--start-sha", "a" * 40,
                                "--start-shas", "app=" + "b" * 40)
        self.assertEqual(rc, 2, out)
        self.assertEqual(self.ledger_bytes(), before)
        # multi-repo map form
        rc, out = self.run_main("claim", self.sid, "--owner", "w",
                                "--host", "h", "--session", "s",
                                "--start-shas",
                                f"app={'a' * 40},contracts={'b' * 40}")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(sorted(row["start_shas"]), ["app", "contracts"])
        self.assertEqual(CT.declared_repos(L.load_events(self.ledger),
                                           self.sid),
                         ["app", "contracts"])
        # malformed pair
        rc, out = self.run_main("claim", "STORY-002", "--owner", "w",
                                "--host", "h", "--session", "s",
                                "--start-shas", "app")
        self.assertEqual(rc, 2, out)

    def test_claim_rejects_bad_start_sha_via_schema(self):
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "w",
                                "--host", "h", "--session", "s",
                                "--start-sha", "not-a-sha")
        self.assertEqual(rc, 1, out)  # schema refusal surfaces as REFUSED
        self.assertIn("REFUSED", out)
        self.assertEqual(self.ledger_bytes(), before)


# ------------------------------------------------------------- override ----

class TestOverride(ControlHome):
    def open_claim(self, owner: str = "worker-1"):
        self.seed([claim_row("cl-1", self.sid, owner=owner)])

    def test_absent_override_refuses_conflicting_claim(self):
        self.open_claim()
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertNotIn("OVERRIDE", out)  # absent file is not consulted
        self.assertEqual(self.ledger_bytes(), before)
        rc, out = self.run_main("override")
        self.assertEqual(rc, 0, out)
        self.assertIn("no override in effect", out)

    def test_malformed_override_fail_closed(self):
        self.open_claim()
        self.write_override("garbage line without colon\nscope: STORY-001\n")
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("malformed field", out)
        for missing in ("by", "reason", "date"):
            self.assertIn(missing, out)
        self.assertEqual(self.ledger_bytes(), before)
        rc, out = self.run_main("override")
        self.assertEqual(rc, 1, out)

    def test_placeholder_override_fail_closed(self):
        self.open_claim()
        self.write_override((FIXTURES / "override_placeholder.txt").read_text(
            encoding="utf-8"))
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("placeholder-filled field `scope`", out)
        self.assertIn("placeholder-filled field `by`", out)
        self.assertEqual(self.ledger_bytes(), before)

    def test_todo_and_empty_values_fail_closed(self):
        self.open_claim()
        for text in ("scope: STORY-001\nby: human-ops\nreason: TODO\n"
                     f"date: {TS}\n",
                     "scope: STORY-001\nby: human-ops\nreason:\n"
                     f"date: {TS}\n",
                     "scope: STORY-001\nby: human-ops\nreason: CHANGEME\n"
                     f"date: {TS}\n"):
            self.write_override(text)
            before = self.ledger_bytes()
            rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                    "--host", "h", "--session", "s",
                                    "--start-sha", "b" * 40)
            self.assertEqual(rc, 1, text)
            self.assertIn("reason", out)
            self.assertEqual(self.ledger_bytes(), before)

    def test_uncommitted_override_fail_closed(self):
        self.open_claim()
        # valid content but never committed
        self.write_override(GOOD_OVERRIDE, commit=False)
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("uncommitted", out)
        self.assertEqual(self.ledger_bytes(), before)
        # committed, then the working tree drifts from HEAD
        blob = self.write_override(GOOD_OVERRIDE)
        self.root.joinpath("factory/locks/OVERRIDE").write_text(
            GOOD_OVERRIDE.replace("STORY-001", "ALL"), encoding="utf-8")
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("uncommitted", out)
        self.assertEqual(self.ledger_bytes(), before)
        self.assertIsNotNone(blob)

    def test_scoped_override_only_covers_its_scope(self):
        self.open_claim()
        self.write_override(GOOD_OVERRIDE.replace("STORY-001", "STORY-002"))
        before = self.ledger_bytes()
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("handover", out)  # plain conflict refusal
        self.assertEqual(self.ledger_bytes(), before)
        # bad scope form is malformed, not scoped
        self.write_override(GOOD_OVERRIDE.replace("STORY-001", "everything"))
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 1, out)
        self.assertIn("bad field `scope`", out)

    def test_valid_committed_scoped_override_permits_with_blob_sha(self):
        self.open_claim()
        blob = self.write_override(GOOD_OVERRIDE)
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 0, out)
        self.assertIn("via OVERRIDE", out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(row["override_used"]["blob_sha"], blob)
        self.assertEqual(row["override_used"]["scope"], "STORY-001")
        self.assertEqual(row["override_used"]["by"], "human-ops")
        self.assertEqual(row["owner"], "worker-2")
        # override subcommand shows the active override + blob sha
        rc, out = self.run_main("override")
        self.assertEqual(rc, 0, out)
        self.assertIn(blob, out)
        self.assertIn("OVERRIDE active", out)

    def test_scope_all_covers_any_story(self):
        self.open_claim()
        self.write_override(GOOD_OVERRIDE.replace("STORY-001", "ALL"))
        rc, out = self.run_main("claim", self.sid, "--owner", "worker-2",
                                "--host", "h", "--session", "s",
                                "--start-sha", "b" * 40)
        self.assertEqual(rc, 0, out)

    def test_override_never_bypasses_order_or_evidence(self):
        """Narrow: an active override does not license transitions."""
        self.write_override(GOOD_OVERRIDE)
        before = self.ledger_bytes()
        rc, out = self.req("BUILD_GREEN", {"commit_sha": "deadbeef",
                                           "build_result": "green",
                                           "named_tests_result": "green"})
        self.refused(rc, out, before, "order", "commit_sha")
        rc, out = self.req("MERGED", {})
        self.assertEqual(rc, 2, out)  # evidence still required
        self.assertEqual(self.ledger_bytes(), before)


# --------------------------------------------------------- dispatch ids ----

class TestDispatchId(ControlHome):
    def test_allocation_and_canonicalization(self):
        rc, out = self.run_main("dispatch-id", self.sid, "coder")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out.strip(),
                         rf"^{self.sid}-coder-1-\d+$")
        # alias canonicalizes (impl-planner -> planner)
        rc, out = self.run_main("dispatch-id", self.sid, "impl-planner")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out.strip(), rf"^{self.sid}-planner-1-\d+$")
        # unknown role is a usage refusal listing the known roles
        rc, out = self.run_main("dispatch-id", self.sid, "wizard")
        self.assertEqual(rc, 2, out)
        self.assertIn("unknown role", out)

    def test_attempt_increments_never_restarts_ended(self):
        self.seed([dispatch_row("d-1", self.sid, "coder", attempt=1),
                   dispatch_row("d-2", self.sid, "coder", attempt=2)])
        rc, out = self.run_main("dispatch-id", self.sid, "coder")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out.strip(), rf"^{self.sid}-coder-3-\d+$")

    def test_invalid_ledger_fails_closed(self):
        """A stale dispatch restart makes the ledger invalid; the
        controller refuses to operate on it (nothing is written)."""
        done = dispatch_row("d-1", self.sid, "coder", attempt=1)
        self.seed([done, done])
        before = self.ledger_bytes()
        rc, out = self.run_main("state", self.sid)
        self.assertEqual(rc, 1, out)
        self.assertIn("ledger rejected", out)
        self.assertEqual(self.ledger_bytes(), before)


# ------------------------------------------------------- multirepo sets ----

class TestMultirepoSetLevel(ControlHome):
    sid = "STORY-100"

    def claim_multi(self):
        return [claim_row("cl-1", self.sid, start_sha=None,
                          start_shas={"app": "a" * 40,
                                      "contracts": "b" * 40})]

    def merge_ev(self):
        head = self.head()
        if not hasattr(self, "_rec_rel"):
            self._rec_rel, _, _ = self.fake_record(head=head)
        return {"tested_sha": head[:7], "head_sha": head,
                "unresolved_threads": "0", "internal_verdict": "PASS",
                "review_record": self._rec_rel}

    def test_merge_eligible_refuses_incomplete_set_naming_repos(self):
        subs = load_fixture_jsonl(
            MR_FIXTURES / "subevents_incomplete.jsonl", self.sid)
        self.seed(chain_to("READY_FOR_REVIEW", self.sid) + self.claim_multi()
                  + [gate_row("g-1", self.sid, "merge")] + subs)
        before = self.ledger_bytes()
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(), sid=self.sid)
        self.refused(rc, out, before, "set_complete")
        self.assertIn("repo 'contracts'", out)
        self.assertIn("READY_FOR_REVIEW", out)

    def test_merge_eligible_passes_complete_set(self):
        subs = load_fixture_jsonl(
            MR_FIXTURES / "subevents_complete_ready.jsonl", self.sid)
        self.seed(chain_to("READY_FOR_REVIEW", self.sid) + self.claim_multi()
                  + [gate_row("g-1", self.sid, "merge")] + subs)
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(), sid=self.sid)
        self.assertEqual(rc, 0, out)

    def test_merged_set_level(self):
        subs = load_fixture_jsonl(MR_FIXTURES / "subevents_merged.jsonl",
                                  self.sid)
        self.seed(chain_to("MERGE_ELIGIBLE", self.sid) + self.claim_multi()
                  + subs)
        rc, out = self.req("MERGED", {"merged_sha": self.head(),
                                      "merged_at": TS, "pr_number": "42"},
                           sid=self.sid)
        self.assertEqual(rc, 0, out)
        # incomplete: only app merged
        self.seed(chain_to("MERGE_ELIGIBLE", self.sid) + self.claim_multi() + [
            transition_row("sub-1", self.sid, "CODING", "MERGED", repo="app"),
        ])
        before = self.ledger_bytes()
        rc, out = self.req("MERGED", {"merged_sha": self.head(),
                                      "merged_at": TS, "pr_number": "42"},
                           sid=self.sid)
        self.refused(rc, out, before, "set_complete")
        self.assertIn("repo 'contracts'", out)

    def test_live_proven_needs_all_merged_plus_proof(self):
        subs = load_fixture_jsonl(MR_FIXTURES / "subevents_merged.jsonl",
                                  self.sid)
        self.seed(chain_to("MERGED", self.sid) + self.claim_multi() + subs)
        before = self.ledger_bytes()
        rc, out = self.req("LIVE_PROVEN", {
            "live_proof_evidence": "stories/STORY-100/proof/missing.md"},
            sid=self.sid)
        self.refused(rc, out, before, "live_proof_evidence")
        proof = self.stories / self.sid / "proof"
        proof.mkdir(parents=True, exist_ok=True)
        (proof / "e2e.md").write_text("cross-repo proof\n", encoding="utf-8")
        rc, out = self.req("LIVE_PROVEN", {
            "live_proof_evidence": f"stories/{self.sid}/proof/e2e.md"},
            sid=self.sid)
        self.assertEqual(rc, 0, out)

    def test_single_repo_claim_skips_set_rules(self):
        sid = STORY  # single-repo story with a start_sha claim
        self.seed(chain_to("READY_FOR_REVIEW", sid) + [
            claim_row("cl-1", sid, start_sha="a" * 40),
            gate_row("g-1", sid, "merge")])
        rc, out = self.req("MERGE_ELIGIBLE", self.merge_ev(), sid=sid)
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertNotIn("set_complete", row["checks_run"])


# ---------------------------------------------------------- story ids ----

class TestStoryIdRules(ControlHome):
    def test_noncanonical_rejected(self):
        for bad in ("T-A", "STORY-023A", "STORY-27", "X-1", "story-001"):
            rc, out = self.run_main("validate-story-id", bad)
            self.assertEqual(rc, 1, bad)
            self.assertIn("not canonical", out)

    def test_next_in_sequence(self):
        rc, out = self.run_main("validate-story-id", "STORY-001")
        self.assertEqual(rc, 0, out)
        self.assertIn("VALID", out)
        (self.stories / "STORY-001.md").write_text("spec\n", encoding="utf-8")
        rc, out = self.run_main("validate-story-id", "STORY-003")
        self.assertEqual(rc, 1, out)
        self.assertIn("next-in-sequence", out)
        rc, out = self.run_main("validate-story-id", "STORY-002")
        self.assertEqual(rc, 0, out)

    def test_alias_table_blocks_reuse(self):
        (self.stories / "ALIAS.md").write_text(
            "| historical | note |\n|---|---|\n"
            "| STORY-001 | retired (split) |\n", encoding="utf-8")
        rc, out = self.run_main("validate-story-id", "STORY-001")
        self.assertEqual(rc, 1, out)
        self.assertIn("alias/retired", out)

    def test_reserved_ids_claimed_in_dependency_order(self):
        for i in range(1, 4):
            (self.stories / f"STORY-{i:03d}.md").write_text("spec\n",
                                                            encoding="utf-8")
        (self.stories / "ALIAS.md").write_text(
            "# alias\n\n| Historical ID | Canonical status |\n|---|---|\n"
            "| `T-A` | historical |\n\n## Transition\n\n"
            "| Historical ID | Reserved canonical ID (applies at spec "
            "freeze) |\n|---|---|\n"
            "| `T-C` | STORY-004 — first |\n"
            "| `T-D` | STORY-005 — second |\n", encoding="utf-8")
        events = L.load_events(self.ledger)
        # 004 = first unfrozen reservation -> VALID
        self.assertEqual(CT.validate_story_id(
            "STORY-004", self.stories, self.stories / "ALIAS.md", events), [])
        # 005 reserved but 004 not frozen -> dependency order
        errs = CT.validate_story_id(
            "STORY-005", self.stories, self.stories / "ALIAS.md", events)
        self.assertTrue(any("first unfrozen" in e for e in errs), errs)
        # once 004 has SPEC_FROZEN, 005 becomes claimable
        ev = [transition_row("t-1", "STORY-004", "TEST_PLAN", "SPEC_FROZEN")]
        self.assertEqual(CT.validate_story_id(
            "STORY-005", self.stories, self.stories / "ALIAS.md", ev), [])
        # 006 (unreserved) is NOT next-in-sequence while 005 is unfrozen-
        # reserved: reserved ids don't poison the plain sequence
        rc, out = self.run_main("validate-story-id", "STORY-006")
        self.assertEqual(rc, 1, out)
        self.assertIn("next-in-sequence", out)

    def test_sequence_reason_escape_applies_at_spec_frozen(self):
        (self.stories / "STORY-001.md").write_text("spec\n", encoding="utf-8")
        sid, fsha = prep_spec_frozen(self, sid="STORY-003")
        before = self.ledger_bytes()
        ev = {"spec_path": f"stories/{sid}/spec.md",
              "spec_freeze_sha": fsha}
        rc, out = self.req("SPEC_FROZEN", ev, sid=sid,
                           approved_by="approver-one")
        self.refused(rc, out, before, "canonical_id")
        rc, out = self.req("SPEC_FROZEN",
                           {**ev, "sequence_reason": "dependency split"},
                           sid=sid, approved_by="approver-one")
        self.assertEqual(rc, 0, out)


# ------------------------------------------------------------------ CLI ----

class TestCLI(ControlHome):
    def test_no_args_is_usage_rc2(self):
        with self.assertRaises(SystemExit) as cm:
            CT.main([])
        self.assertEqual(cm.exception.code, 2)

    def test_direct_run_smoke(self):
        r = subprocess.run([sys.executable, "tools/controller.py"],
                           cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("usage", (r.stdout + r.stderr).lower())

    def test_unknown_state_is_usage_rc2(self):
        rc, out = self.run_main("request", self.sid, "SHIP_IT")
        self.assertEqual(rc, 2, out)
        self.assertIn("not a state", out)

    def test_state_command(self):
        rc, out = self.run_main("state", self.sid)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.strip(), f"{self.sid}: BACKLOG")
        self.seed(chain_to("PLANNED"))
        rc, out = self.run_main("state", self.sid)
        self.assertIn("PLANNED", out)

    def test_request_evidence_parsing(self):
        self.seed(chain_to("MERGED"))
        rc, out = self.run_main(
            "request", self.sid, "LIVE_PROVEN",
            "--by", "orchestrator",
            "--evidence", "live_proof_evidence=https://example.test/p")
        self.assertEqual(rc, 0, out)
        row = L.load_events(self.ledger)[-1]
        self.assertEqual(row["requested_by"], "orchestrator")

    def test_blocked_bad_gate_is_usage(self):
        rc, out = self.run_main("blocked", self.sid, "--reason", "x",
                                "--gate", "not-a-gate")
        self.assertEqual(rc, 2, out)

    def test_refusals_never_write_the_ledger(self):
        """Sweep: every refusal class leaves the ledger byte-identical
        (including when the ledger does not exist yet)."""
        self.assertFalse(self.ledger.exists())
        rc, out = self.req("GRILLED")  # missing story.md, no ledger yet
        self.assertEqual(rc, 1)
        self.assertFalse(self.ledger.exists())
        rc, out = self.req("MERGED", {})
        self.assertEqual(rc, 2)
        self.assertFalse(self.ledger.exists())
        rc, out = self.run_main("release", self.sid, "--owner", "nobody")
        self.assertEqual(rc, 1)
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
