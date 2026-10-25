#!/usr/bin/env python3
"""Tests for tools/render_dashboard.py — DASHBOARD.md rendering from the
ledger: frozen generated header, genericized title (workspace.toml →
`workspace` key, else `factory`), process-version line only from
factory/runbook.md (never hardcoded), raw counts + breakdowns with NO
pooled percentage, uninstrumented ≠ zero exclusion, external-review
attribution census, per-story rows, corrections register, dashboard-notes
sidecar, determinism (no wall-clock), --check stale/fresh, and CLI
refusals.

Fixtures: tools/tests/fixtures/render/sample_events.json via
ledger_lib.append_event. No network, no writes outside tempdirs.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import ledger_lib as L
from tools import render_dashboard as RD
from tools import render_state as RS
from tools.tests.test_render_state import build_ledger


class RenderDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        cls.ledger = build_ledger(cls.root)
        cls.events = L.load_events(cls.ledger)
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def render(self, root: Path | None = None) -> str:
        return RD.render(self.events, root or self.root, self.header)

    # ---- title + header genericization
    def test_title_defaults_to_factory_without_workspace_toml(self):
        text = self.render()
        self.assertTrue(text.startswith(self.header))
        self.assertIn("# factory — Factory Dashboard", text)

    def test_title_from_workspace_toml(self):
        (self.root / "workspace.toml").write_text(
            'workspace = "demo-ws"\n', encoding="utf-8")
        try:
            self.assertIn("# demo-ws — Factory Dashboard", self.render())
        finally:
            (self.root / "workspace.toml").unlink()

    def test_process_version_only_from_runbook(self):
        # absent runbook → line omitted, never a hardcoded version
        self.assertNotIn("process-version", self.render())
        rb = self.root / "factory" / "runbook.md"
        rb.parent.mkdir(parents=True, exist_ok=True)
        rb.write_text("# Runbook\n\nprocess-version: v9.9 (commit abc1234)\n",
                      encoding="utf-8")
        try:
            self.assertIn("**process-version: v9.9 (commit abc1234)**",
                          self.render())
        finally:
            rb.unlink()

    # ---- metrics body
    def test_no_pooled_percentage(self):
        body = self.render().split("## Run notes")[0]
        self.assertNotIn("first-try pass rate", body.lower())
        self.assertIn("Correction-free", body)
        self.assertIn("Formal-loop-free", body)
        self.assertIn("never a pooled percentage", body)
        # trial-machinery sections from the reference are gone (asserted
        # without spelling the dropped vocabulary)
        self.assertNotIn("Per-" + "stra" + "tum", body)

    def test_raw_counts_and_breakdowns(self):
        text = self.render()
        self.assertIn("| Stories in ledger | 3 |", text)
        self.assertIn("| Merged (git-verified outcomes) | 2 |", text)
        # STORY-201 merged + instrumented (totals/loops/findings); T-A
        # merged but uninstrumented → excluded, never counted as zero
        self.assertIn("WITHOUT loop/finding instrumentation", text)
        self.assertIn("1 — T-A", text)
        # loops: STORY-201 plan:1, STORY-202 code:1 (not merged → not in
        # merged rollup)
        self.assertIn("| Formal loops recorded | 1 (plan: 1, code: 0) |",
                      text)
        self.assertIn("| Observed external correction rounds | 1 |", text)
        self.assertIn("| Logic-changing nits (schema-forbidden) | 0 |", text)
        self.assertIn("total 1, avoidable 0, pending 0", text)
        self.assertIn("| Reconciliation corrections applied | 1 |", text)
        self.assertIn("MISSED_WITH_CONTEXT 1", text)
        self.assertIn("UNATTRIBUTED 0", text)

    def test_per_story_rows_and_corrections_register(self):
        text = self.render()
        self.assertIn("## Per-story state", text)
        # corrections = 2: 1 formal plan loop (the plan-review-evidenced
        # fixed finding folds into it — v0.1 evidence_path association) +
        # 1 external fixed finding counted once
        self.assertIn("| STORY-201 | agent-a | `MERGED` | 1/0 | 1 | 2 | "
                      "deadbeef1234 | proven |", text)
        # T-A: MERGED by transition without an outcome row
        self.assertIn("| T-A | — | `MERGED` | 0/0 | 0 | 0 | — | — |", text)
        self.assertIn("_MERGED* = merged outcome recorded without controller "
                      "transitions (backfilled history)._", text)
        self.assertIn("## Corrections register", text)
        self.assertIn("correction-STORY-201-1", text)
        self.assertIn("_loop-STORY-201-1_", text)

    def test_repo_tagged_transitions_never_move_state(self):
        # per-repo sub-events must not appear as the story state
        repo_evt = {
            "event": "transition", "event_id": "transition-STORY-203-1",
            "story_id": "STORY-203", "repo": "app",
            "from_state": "CODING", "to_state": "BUILD_GREEN",
            "requested_by": "orchestrator", "checks_run": [],
            "check_results": {}, "approved_by": None,
            "approved_by_reason": "no gate required",
            "recorded_at": "2026-09-01T06:00:00Z", "provenance": "live"}
        events = self.events + [repo_evt]
        text = RD.render(events, self.root, self.header)
        self.assertIn("| STORY-203 | — | `no transitions (events only)`",
                      text)

    # ---- owner fold (B7): claim → handover → release, latest row wins
    def _assign(self, eid: str, **kw):
        row = {"event": "assignment", "event_id": eid,
               "story_id": "STORY-220", "recorded_at":
               "2026-09-01T00:00:00Z", "provenance": "live"}
        row.update(kw)
        return row

    def test_owner_fold_handover_and_release(self):
        claim = self._assign("assignment-STORY-220-1", op="claim",
                             owner="agent-a", host="host-1",
                             session="sess-1", claimed_at="2026-09-01T00:00:00Z",
                             start_sha="a" * 40)
        hand = self._assign("assignment-STORY-220-2", op="handover",
                            **{"from": "agent-a"}, to="agent-b",
                            reason="shift change", flagged=False)
        rel = self._assign("assignment-STORY-220-3", op="release",
                           owner="agent-b")
        text = RD.render([claim, hand], self.root, self.header)
        self.assertIn("| STORY-220 | agent-b |", text)
        text = RD.render([claim, hand, rel], self.root, self.header)
        self.assertIn("| STORY-220 | — |", text)
        # claim alone still shows the claimer
        text = RD.render([claim], self.root, self.header)
        self.assertIn("| STORY-220 | agent-a |", text)

    # ---- attribution membership keyed by (story, finding_id) (B8)
    def test_attribution_keyed_by_story_and_finding_id(self):
        # two stories share the finding id F-1 (schema-permitted: finding
        # ids are unique only within a story); only STORY-221's is
        # attributed → STORY-222's counts UNATTRIBUTED
        def ext_finding(sid, eid):
            return {"event": "finding", "event_id": eid, "story_id": sid,
                    "finding_id": "F-1", "source": "external",
                    "reviewed_sha": "b" * 40, "introduced_sha": "b" * 40,
                    "class": "mechanical", "severity": "minor",
                    "disposition": "fixed", "logic_touched": False,
                    "overlap_id": None, "overlap_id_reason": "independent",
                    "fixer": "orchestrator", "fix_sha": "c" * 40,
                    "validation": None, "validation_reason": "n/a",
                    "evidence_path": f"stories/{sid}/ext.md",
                    "recorded_at": "2026-09-01T00:00:00Z",
                    "provenance": "live"}

        def ctx(sid, eid):
            return {"event": "review_context", "event_id": eid,
                    "story_id": sid, "review_role": "external_review",
                    "reviewed_sha": "b" * 40, "base_sha": "a" * 40,
                    "context_manifest_path": f"stories/{sid}/manifest.json",
                    "context_manifest_digest": "f" * 64,
                    "prompt_version": "external_review-v0.1.0",
                    "dispatch_id": None,
                    "dispatch_id_reason": "external surface",
                    "delivery_surface": "pr-full-diff",
                    "requested_at": "2026-09-01T00:00:00Z",
                    "recorded_at": "2026-09-01T00:00:00Z",
                    "provenance": "live"}
        attr = {"event": "review_attribution",
                "event_id": "review_attribution-STORY-221-1",
                "story_id": "STORY-221", "finding_id": "F-1",
                "internal_context_event_id": "review_context-STORY-221-1",
                "external_context_event_id": "review_context-STORY-221-1",
                "classification": "MISSED_WITH_CONTEXT",
                "classified_by": "orchestrator",
                "evidence_path": "stories/STORY-221/attr.md",
                "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"}
        events = [ctx("STORY-221", "review_context-STORY-221-1"),
                  ctx("STORY-222", "review_context-STORY-222-1"),
                  ext_finding("STORY-221", "finding-STORY-221-1"),
                  ext_finding("STORY-222", "finding-STORY-222-1"),
                  attr]
        text = RD.render(events, self.root, self.header)
        self.assertIn("UNATTRIBUTED 1", text)

    def test_notes_sidecar_inlined(self):
        notes = self.root / "factory" / "dashboard-notes.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text("Current run: fixture.\n", encoding="utf-8")
        try:
            text = self.render()
            self.assertIn("## Run notes (from factory/dashboard-notes.md "
                          "sidecar)", text)
            self.assertIn("Current run: fixture.", text)
        finally:
            notes.unlink()

    def test_notes_sidecar_embedded_verbatim(self):
        """R4-E4: the sidecar is embedded byte-for-byte — leading
        indentation (an indented code block) and trailing whitespace are
        never stripped."""
        notes = self.root / "factory" / "dashboard-notes.md"
        notes.parent.mkdir(parents=True, exist_ok=True)
        raw = "    indented first line\nplain\ntrailing   \n"
        notes.write_text(raw, encoding="utf-8")
        try:
            text = self.render()
            section = text.split(
                "## Run notes (from factory/dashboard-notes.md sidecar)\n\n",
                1)[1]
            self.assertTrue(section.startswith(raw),
                            f"sidecar not verbatim: {section[:80]!r}")
            # determinism unchanged
            self.assertEqual(text, self.render())
        finally:
            notes.unlink()

    def test_determinism_no_wall_clock(self):
        a, b = self.render(), self.render()
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(a.encode()).hexdigest(),
                         hashlib.sha256(b.encode()).hexdigest())
        self.assertTrue(a.endswith("\n"))
        self.assertNotIn("\r", a)

    # ---- CLI
    def test_cli_writes_check_stale_and_fresh(self):
        dash = self.root / "factory" / "DASHBOARD.md"
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        self.assertTrue(dash.read_text(encoding="utf-8").startswith(
            self.header))
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 0)
        dash.write_text(dash.read_text(encoding="utf-8") + "\nstale edit\n",
                        encoding="utf-8")
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 1)
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 0)

    def test_check_before_generation_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            rc = RD.main(["--check", "--root", str(root)])
            self.assertEqual(rc, 0)  # not yet generated → not stale

    def test_alt_ledger_and_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            alt = Path(td) / "alt.jsonl"
            alt.write_bytes(self.ledger.read_bytes())
            self.assertEqual(
                RD.main(["--root", str(self.root), "--ledger", str(alt)]), 0)
            bad = Path(td) / "bad.jsonl"
            bad.write_text("{not json}\n", encoding="utf-8")
            self.assertEqual(
                RD.main(["--root", str(self.root), "--ledger", str(bad)]), 2)


class PlanReviewAssociationTests(unittest.TestCase):
    """v0.1 association: a fixed finding folds into its plan loop when
    its SUPPORTED-schema `evidence_path` names the plan review
    (case-insensitive `plan-review` / `plan review`); a non-plan fixed
    finding counts individually. No out-of-schema fields are read."""

    def test_names_plan_review_helper(self):
        self.assertTrue(RD._names_plan_review("stories/S/plan-review.md"))
        self.assertTrue(RD._names_plan_review("evidence/Plan Review 1.md"))
        self.assertTrue(RD._names_plan_review("PLAN-REVIEW.md"))
        self.assertFalse(RD._names_plan_review("stories/S/code-review.md"))
        self.assertFalse(RD._names_plan_review(None))
        self.assertFalse(RD._names_plan_review(""))

    def test_plan_review_evidence_folds_into_loop(self):
        loop = {"event": "loop", "event_id": "loop-S-1", "story_id": "S",
                "stage": "plan_review", "loop_n": 1, "class": "reasoning",
                "root_cause_one_line": "x", "recorded_at": "t",
                "provenance": "live"}
        plan_fix = {"event": "finding", "event_id": "f-1", "story_id": "S",
                    "finding_id": "F-1", "source": "internal",
                    "class": "reasoning", "severity": "major",
                    "disposition": "fixed",
                    "evidence_path": "stories/S/plan-review.md"}
        code_fix = {"event": "finding", "event_id": "f-2", "story_id": "S",
                    "finding_id": "F-2", "source": "external",
                    "class": "mechanical", "severity": "minor",
                    "disposition": "fixed",
                    "evidence_path": "stories/S/external-round-1.md"}
        events = [loop, plan_fix, code_fix]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            text = RD.render(events, root, "H")
        # 1 formal loop + 1 non-plan fixed finding = 2 corrections; the
        # plan-review-evidenced fix is NOT double-counted on top
        self.assertIn("| S | — | `no transitions (events only)` | 1/0 | 1 "
                      "| 2 | — | — |", text)

    def test_out_of_schema_field_never_read(self):
        """A row carrying an out-of-schema `source_detail` cannot change
        the association — only the supported evidence_path matters.
        R4-E3: with NO plan loop row, the plan-review-evidenced fix can
        never fold into anything — it counts as 1 correction."""
        finding = {"event": "finding", "event_id": "f-1", "story_id": "S",
                   "finding_id": "F-1", "source": "internal",
                   "class": "reasoning", "severity": "major",
                   "disposition": "fixed",
                   "evidence_path": "stories/S/plan-review.md",
                   "source_detail": "code review follow-up"}
        events = [finding]
        with tempfile.TemporaryDirectory() as td:
            text = RD.render(events, Path(td), "H")
        self.assertIn("| S | — | `no transitions (events only)` | 0/0 | 0 "
                      "| 1 | — | — |", text)

    def test_unmatched_plan_fix_counts_individually(self):
        """R4-E3: 0 plan loops + 1 plan-review-evidenced fixed finding →
        1 correction (never folded away to a correction-free story)."""
        plan_fix = {"event": "finding", "event_id": "f-1", "story_id": "S",
                    "finding_id": "F-1", "source": "internal",
                    "class": "reasoning", "severity": "major",
                    "disposition": "fixed",
                    "evidence_path": "stories/S/plan-review.md"}
        with tempfile.TemporaryDirectory() as td:
            text = RD.render([plan_fix], Path(td), "H")
        self.assertIn("| S | — | `no transitions (events only)` | 0/0 | 0 "
                      "| 1 | — | — |", text)

    def test_matched_fold_still_caps_at_loop_count(self):
        """R4-E3 regression pin: 1 plan loop + 1 plan-evidenced fix +
        1 other fix → 2 corrections (fold unchanged when a loop exists)."""
        loop = {"event": "loop", "event_id": "loop-S-1", "story_id": "S",
                "stage": "plan_review", "loop_n": 1, "class": "reasoning",
                "root_cause_one_line": "x", "recorded_at": "t",
                "provenance": "live"}
        plan_fix = {"event": "finding", "event_id": "f-1", "story_id": "S",
                    "finding_id": "F-1", "source": "internal",
                    "class": "reasoning", "severity": "major",
                    "disposition": "fixed",
                    "evidence_path": "stories/S/plan-review.md"}
        other_fix = {"event": "finding", "event_id": "f-2", "story_id": "S",
                     "finding_id": "F-2", "source": "external",
                     "class": "mechanical", "severity": "minor",
                     "disposition": "fixed",
                     "evidence_path": "stories/S/external-round-1.md"}
        with tempfile.TemporaryDirectory() as td:
            text = RD.render([loop, plan_fix, other_fix], Path(td), "H")
        self.assertIn("| S | — | `no transitions (events only)` | 1/0 | 1 "
                      "| 2 | — | — |", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
