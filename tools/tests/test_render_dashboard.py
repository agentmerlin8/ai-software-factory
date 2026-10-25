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
        self.assertIn("| STORY-201 | agent-a | `MERGED` | 1/0 | 1 | 3 | "
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
