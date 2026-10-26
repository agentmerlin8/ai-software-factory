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

import contextlib
import hashlib
import io
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
        return RD.render(self.events, root or self.root, self.header,
                         ledger_path=self.ledger)

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

    def test_falsy_corrected_values_render(self):
        # round-17 R5: `or ""` collapsed a recorded 0/False to empty
        def corr(eid, value):
            return {"event": "correction", "event_id": eid,
                    "story_id": None, "repo": None,
                    "repo_reason": "run-level",
                    "contradicted_claim": "c", "corrected_value": value,
                    "source_citation": "src-1", "corrected_artifact": None,
                    "recorded_at": "2026-09-01T00:00:00Z",
                    "provenance": "live"}
        text = RD.render([corr("correction-falsy-1", 0),
                          corr("correction-falsy-2", False),
                          corr("correction-falsy-3", "text value"),
                          corr("correction-falsy-4", None)],
                         self.root, self.header)
        self.assertIn("`correction-falsy-1` **run-level**: 0 (_src-1_)", text)
        self.assertIn("`correction-falsy-2` **run-level**: False "
                      "(_src-1_)", text)
        self.assertIn("`correction-falsy-3` **run-level**: text value "
                      "(_src-1_)", text)
        # None keeps the existing empty form
        self.assertIn("`correction-falsy-4` **run-level**:  (_src-1_)", text)

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

    # ---- R18-S2: a story-level CLOSED transition is an implicit release
    def _trans(self, eid: str, sid: str, to_state: str, repo=None) -> dict:
        row = {"event": "transition", "event_id": eid, "story_id": sid,
               "repo": repo, "from_state": "CODING", "to_state": to_state,
               "requested_by": "orchestrator", "checks_run": [],
               "check_results": {}, "approved_by": None,
               "approved_by_reason": "no gate required",
               "recorded_at": "2026-09-01T06:00:00Z", "provenance": "live"}
        return row

    def test_implicit_closed_clears_owner(self):
        claim = self._assign("assignment-STORY-220-1", op="claim",
                             owner="agent-a", host="host-1",
                             session="sess-1",
                             claimed_at="2026-09-01T00:00:00Z",
                             start_sha="a" * 40)
        closed = self._trans("transition-STORY-220-2", "STORY-220",
                             "CLOSED")
        text = RD.render([claim, closed], self.root, self.header)
        self.assertIn("| STORY-220 | — | `CLOSED`", text)
        self.assertNotIn("| STORY-220 | agent-a |", text)

    def test_repo_tagged_closed_keeps_owner(self):
        claim = self._assign("assignment-STORY-220-1", op="claim",
                             owner="agent-a", host="host-1",
                             session="sess-1",
                             claimed_at="2026-09-01T00:00:00Z",
                             start_sha="a" * 40)
        repo_closed = self._trans("transition-STORY-220-2", "STORY-220",
                                  "CLOSED", repo="app")
        text = RD.render([claim, repo_closed], self.root, self.header)
        self.assertIn("| STORY-220 | agent-a |", text)

    def test_non_terminal_transition_keeps_owner(self):
        claim = self._assign("assignment-STORY-220-1", op="claim",
                             owner="agent-a", host="host-1",
                             session="sess-1",
                             claimed_at="2026-09-01T00:00:00Z",
                             start_sha="a" * 40)
        merged = self._trans("transition-STORY-220-2", "STORY-220",
                             "MERGE_ELIGIBLE")
        text = RD.render([claim, merged], self.root, self.header)
        self.assertIn("| STORY-220 | agent-a | `MERGE_ELIGIBLE`", text)

    def test_explicit_release_still_clears(self):
        claim = self._assign("assignment-STORY-220-1", op="claim",
                             owner="agent-a", host="host-1",
                             session="sess-1",
                             claimed_at="2026-09-01T00:00:00Z",
                             start_sha="a" * 40)
        rel = self._assign("assignment-STORY-220-2", op="release",
                           owner="agent-a")
        text = RD.render([claim, rel], self.root, self.header)
        self.assertIn("| STORY-220 | — |", text)

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

    # ---- R7-H5: overlap findings excluded from the external census
    def test_overlap_findings_excluded_from_census(self):
        """An external finding carrying an `overlap_id` is excluded from
        BOTH census populations (UNATTRIBUTED and without-instrumentation)
        — matching the controller's attribution requirement; one without
        it is still counted."""
        def ext_finding(sid, eid, fid, overlap):
            row = {"event": "finding", "event_id": eid, "story_id": sid,
                   "finding_id": fid, "source": "external",
                   "reviewed_sha": "b" * 40, "introduced_sha": "b" * 40,
                   "class": "mechanical", "severity": "minor",
                   "disposition": "fixed", "logic_touched": False,
                   "overlap_id": overlap,
                   "overlap_id_reason": ("duplicate of F-0" if overlap
                                         else "independent"),
                   "fixer": "orchestrator", "fix_sha": "c" * 40,
                   "validation": None, "validation_reason": "n/a",
                   "evidence_path": f"stories/{sid}/ext.md",
                   "recorded_at": "2026-09-01T00:00:00Z",
                   "provenance": "live"}
            return row

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
        # S1 has context (activated); S2 does not (without-instrumentation)
        events = [ctx("S1", "review_context-S1-1"),
                  ext_finding("S1", "finding-S1-1", "F-1", None),
                  ext_finding("S1", "finding-S1-2", "F-2", "F-1"),
                  ext_finding("S2", "finding-S2-1", "F-3", "F-9"),
                  ext_finding("S2", "finding-S2-2", "F-4", None)]
        text = RD.render(events, self.root, self.header)
        # F-2 (overlap, activated) excluded → UNATTRIBUTED 1 (only F-1);
        # F-3 (overlap, uninstrumented) excluded → without-instrumentation 1
        self.assertIn("UNATTRIBUTED 1", text)
        self.assertIn("without-instrumentation 1", text)

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

    # ---- R7-H4: ledger-derived freshness stamp (never repo HEAD)
    def test_stamp_is_ledger_digest_not_head(self):
        text = self.render()
        self.assertNotIn("repo HEAD", text)
        digest = hashlib.sha256(self.ledger.read_bytes()).hexdigest()[:12]
        self.assertIn(f"_{len(self.events)} ledger events · ledger digest "
                      f"{digest}_", text)

    def test_render_stable_across_commit_boundary(self):
        """Render at commit A, commit the dashboard (HEAD → B): the
        second render must be byte-identical (ledger unchanged) and
        --check rc 0 — the old HEAD stamp looped forever."""
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            ledger = root / "factory" / "ledger" / "events.jsonl"
            ledger.write_bytes(self.ledger.read_bytes())
            subprocess.run(["git", "-C", str(root), "init", "-q"],
                           check=True, capture_output=True)
            self.assertEqual(RD.main(["--root", str(root)]), 0)
            first = (root / "factory" / "DASHBOARD.md").read_bytes()
            for args in (["add", "-A"],
                         ["-c", "user.email=t@t", "-c", "user.name=t",
                          "commit", "-qm", "dashboard"]):
                subprocess.run(["git", "-C", str(root), *args], check=True,
                               capture_output=True)
            # HEAD moved; the render is unchanged
            self.assertEqual(RD.main(["--check", "--root", str(root)]), 0)
            self.assertEqual(RD.main(["--root", str(root)]), 0)
            self.assertEqual(
                (root / "factory" / "DASHBOARD.md").read_bytes(), first)

    def test_stamp_changes_when_ledger_changes(self):
        before = RD.ledger_digest(self.events, self.ledger)
        alt = self.root / "factory" / "ledger" / "alt.jsonl"
        alt.write_bytes(self.ledger.read_bytes()
                        + b'{"event": "noop-changes-bytes"}\n')
        after = RD.ledger_digest(self.events, alt)
        self.assertNotEqual(before, after)
        alt.unlink()

    def test_ledger_digest_helper_matches_render_card_convention(self):
        from tools import render_card as RC
        self.assertEqual(RC.ledger_digest(self.ledger),
                         RD.ledger_digest(self.events, self.ledger))

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

    def test_check_missing_dashboard_is_stale(self):
        """Round 28 AC4: a MISSING factory/DASHBOARD.md is STALE (rc 1,
        `(missing)` message) — deleting it after generation used to set
        `cur` to empty bytes and take the success branch. Covers both
        the never-generated and the deleted-after-generation cases."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            dash = root / "factory" / "DASHBOARD.md"
            self.assertFalse(dash.exists())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = RD.main(["--check", "--root", str(root)])
            self.assertEqual(rc, 1)  # missing artifact is stale
            self.assertIn(f"OUT OF DATE: {dash} (missing)", out.getvalue())
            # deleted after generation → same refusal
            self.assertEqual(RD.main(["--root", str(root)]), 0)
            self.assertTrue(dash.exists())
            dash.unlink()
            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                rc2 = RD.main(["--check", "--root", str(root)])
            self.assertEqual(rc2, 1)
            self.assertIn("(missing)", out2.getvalue())
            # re-render clears it
            self.assertEqual(RD.main(["--root", str(root)]), 0)
            self.assertEqual(RD.main(["--check", "--root", str(root)]), 0)

    def test_check_handwritten_dashboard_exemption_retained(self):
        """Round 28 AC4: the handwritten exemption stays — an EXISTING
        file WITHOUT the generated marker is prose the renderer never
        owned, so --check skips it (rc 0)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            dash = root / "factory" / "DASHBOARD.md"
            dash.write_text("# hand-written dashboard prose\n",
                            encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = RD.main(["--check", "--root", str(root)])
            self.assertEqual(rc, 0)
            self.assertIn("dashboard not yet generated", out.getvalue())
            self.assertEqual(dash.read_text(encoding="utf-8"),
                             "# hand-written dashboard prose\n")

    def test_check_compares_bytes_crlf_fails(self):
        """R16-Q2: a CRLF-converted DASHBOARD.md violates the frozen LF
        byte contract and MUST fail --check — read_text() normalization
        used to hide the difference."""
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        dash = self.root / "factory" / "DASHBOARD.md"
        raw = dash.read_bytes()
        self.assertNotIn(b"\r\n", raw)  # renderer output is LF
        dash.write_bytes(raw.replace(b"\n", b"\r\n"))
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 1)
        # re-render restores the LF bytes and --check passes again
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        self.assertEqual(dash.read_bytes(), raw)
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 0)

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

    def test_fold_requires_plan_review_loop(self):
        """R5-F1: a CODE-review loop is not a fold target — 0 plan loops +
        1 code loop + 1 plan-review-evidenced fixed finding → 2 corrections
        (1 code loop + 1 unmatched fix); the fold must not over-credit."""
        code_loop = {"event": "loop", "event_id": "loop-S-1", "story_id": "S",
                     "stage": "code_review", "loop_n": 1,
                     "class": "reasoning", "root_cause_one_line": "x",
                     "recorded_at": "t", "provenance": "live"}
        plan_fix = {"event": "finding", "event_id": "f-1", "story_id": "S",
                    "finding_id": "F-1", "source": "internal",
                    "class": "reasoning", "severity": "major",
                    "disposition": "fixed",
                    "evidence_path": "stories/S/plan-review.md"}
        with tempfile.TemporaryDirectory() as td:
            text = RD.render([code_loop, plan_fix], Path(td), "H")
        self.assertIn("| S | — | `no transitions (events only)` | 0/1 | 0 "
                      "| 2 | — | — |", text)


class AggregateLoopTotalsTests(unittest.TestCase):
    """R8-I5: an outcome recording `totals: {"plan_loops": 2}` with NO
    loop rows is instrumented, yet both loop calculations returned zero
    — the story was listed correction-free / formal-loop-free despite
    recorded loops. The aggregate now counts when there are no rows."""

    HEADER = "H"

    def _outcome_row(self, totals):
        return {"event": "outcome", "event_id": "outcome-S-1",
                "story_id": "S", "merged_sha": "deadbeef",
                "merged_at": "2026-09-02T01:00:00Z",
                "live_proof_status": None, "totals": totals,
                "recorded_at": "2026-09-02T01:30:00Z", "provenance": "live"}

    def _render(self, events):
        with tempfile.TemporaryDirectory() as td:
            return RD.render(events, Path(td), self.HEADER)

    def test_totals_only_plan_loops_not_loop_free(self):
        text = self._render([self._outcome_row({"plan_loops": 2})])
        # aggregate counts as formal loops AND its stage breakdown (R9-J1:
        # totals-only stories no longer render `2 (plan: 0, code: 0)`)
        self.assertIn("| Formal loops recorded | 2 (plan: 2, code: 0) |",
                      text)
        # per-story P/I cell uses the same fallback (R9-J1)
        self.assertIn("| S | — | `MERGED*` | 2/0 | 0 | 2 | deadbeef | — |",
                      text)
        # NOT listed as correction-free or formal-loop-free
        self.assertNotIn("Formal-loop-free deliveries (0 plan/code loops; "
                         "external rounds counted separately; instrumented "
                         "stories only) | 1", text)
        merged_line = next(l for l in text.splitlines()
                           if l.startswith("| Formal-loop-free deliveries"))
        self.assertIn("0 — none", merged_line)
        cf_line = next(l for l in text.splitlines()
                       if l.startswith("| Correction-free deliveries"))
        self.assertIn("0 — none", cf_line)

    def test_totals_code_loops_sum_too(self):
        text = self._render([self._outcome_row({"plan_loops": 1,
                                                "code_loops": 3})])
        self.assertIn("| Formal loops recorded | 4 (plan: 1, code: 3) |",
                      text)
        self.assertIn("| S | — | `MERGED*` | 1/3 | 0 | 4 | deadbeef | — |",
                      text)

    def test_bool_and_negative_values_ignored(self):
        text = self._render([self._outcome_row({"plan_loops": True,
                                                "code_loops": -3})])
        self.assertIn("| Formal loops recorded | 0 (plan: 0, code: 0) |",
                      text)
        # R9-J3: a totals dict carrying NO valid supported counter is not
        # instrumentation — the story is excluded and listed, never
        # presented as a measured zero
        uninstr_line = next(l for l in text.splitlines()
                            if l.startswith("| Merged stories WITHOUT"))
        self.assertIn("1 — S", uninstr_line)

    def test_unsupported_total_keys_ignored(self):
        text = self._render([self._outcome_row({"review_loops": 5,
                                                "plan_loops": 1})])
        self.assertIn("| Formal loops recorded | 1 (plan: 1, code: 0) |",
                      text)

    def test_loop_rows_win_over_totals(self):
        """A story WITH loop rows is unchanged — the rows are the count,
        not the aggregate (STORY-201 fixture behavior preserved)."""
        loop = {"event": "loop", "event_id": "loop-S-1", "story_id": "S",
                "stage": "plan_review", "loop_n": 1, "class": "reasoning",
                "root_cause_one_line": "x", "recorded_at": "t",
                "provenance": "live"}
        text = self._render([loop, self._outcome_row({"plan_loops": 9})])
        self.assertIn("| Formal loops recorded | 1 (plan: 1, code: 0) |",
                      text)

    def test_fixture_201_rendering_unchanged(self):
        """The sample fixture's STORY-201 (1 loop row + totals
        {plan_loops: 1}) renders identically to the pre-fix expectation."""
        text = self._render(self.events_201())
        self.assertIn("| Formal loops recorded | 1 (plan: 1, code: 0) |",
                      text)
        self.assertIn("| STORY-201 | agent-a | `MERGED` | 1/0 | 1 | 2 | "
                      "deadbeef1234 | proven |", text)

    @staticmethod
    def events_201():
        from tools.tests.test_render_state import build_ledger
        with tempfile.TemporaryDirectory() as td:
            ledger = build_ledger(Path(td))
            return L.load_events(ledger)


class InstrumentedTotalsValidityTests(unittest.TestCase):
    """R9-J3: a `totals` dict counts as instrumentation ONLY when it
    carries at least one VALID supported counter (`plan_loops`/
    `code_loops`, non-negative int, not bool). `totals: {}` or only
    unrelated counters with no loop/finding rows is a MISSING
    measurement — the story belongs on the 'WITHOUT instrumentation'
    list, never in the loop-free/correction-free measures as a zero."""

    HEADER = "H"

    def _outcome_row(self, totals):
        return {"event": "outcome", "event_id": "outcome-S-1",
                "story_id": "S", "merged_sha": "deadbeef",
                "merged_at": "2026-09-02T01:00:00Z",
                "live_proof_status": None, "totals": totals,
                "recorded_at": "2026-09-02T01:30:00Z", "provenance": "live"}

    def _render(self, events):
        with tempfile.TemporaryDirectory() as td:
            return RD.render(events, Path(td), self.HEADER)

    def _lists(self, text):
        uninstr = next(l for l in text.splitlines()
                       if l.startswith("| Merged stories WITHOUT"))
        cf = next(l for l in text.splitlines()
                  if l.startswith("| Correction-free deliveries"))
        ff = next(l for l in text.splitlines()
                  if l.startswith("| Formal-loop-free deliveries"))
        return uninstr, cf, ff

    def test_empty_totals_not_instrumented(self):
        text = self._render([self._outcome_row({})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("1 — S", uninstr)
        self.assertIn("0 — none", cf)
        self.assertIn("0 — none", ff)

    def test_unrelated_counter_only_not_instrumented(self):
        text = self._render([self._outcome_row({"review_loops": 5,
                                                "duration_s": 12})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("1 — S", uninstr)
        self.assertIn("0 — none", cf)
        self.assertIn("0 — none", ff)

    def test_bool_and_negative_counters_not_instrumented(self):
        text = self._render([self._outcome_row({"plan_loops": True,
                                                "code_loops": -3})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("1 — S", uninstr)
        self.assertIn("0 — none", cf)
        self.assertIn("0 — none", ff)

    def test_explicit_recorded_zero_is_instrumented(self):
        text = self._render([self._outcome_row({"plan_loops": 0})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("0 — none", uninstr)
        self.assertIn("1 — S", cf)
        self.assertIn("1 — S", ff)

    def test_explicit_zero_code_loops_is_instrumented(self):
        text = self._render([self._outcome_row({"code_loops": 0})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("0 — none", uninstr)
        self.assertIn("1 — S", ff)

    def test_findings_or_loops_still_instrumented_without_valid_totals(self):
        """Findings/loops behavior is unchanged — a loop row makes the
        story instrumented even when totals is empty."""
        loop = {"event": "loop", "event_id": "loop-S-1", "story_id": "S",
                "stage": "code_review", "loop_n": 1, "class": "reasoning",
                "root_cause_one_line": "x", "recorded_at": "t",
                "provenance": "live"}
        text = self._render([loop, self._outcome_row({})])
        uninstr, cf, ff = self._lists(text)
        self.assertIn("0 — none", uninstr)
        self.assertIn("0 — none", ff)  # 1 loop row → not loop-free


class ExternalRoundsFullHistoryTests(unittest.TestCase):
    """R23-X1: the external-round counter reads the RAW (unfolded)
    finding history — a finding first cited in round 1 and updated in
    round 2 spans TWO distinct rounds. Dispositions keep latest-row
    folding; only the round COUNT sees every lifecycle row."""

    HEADER = "H"

    def _finding(self, eid, fid, evidence, disposition, ts):
        return {"event": "finding", "event_id": eid, "story_id": "S",
                "finding_id": fid, "source": "external",
                "reviewed_sha": "b" * 40, "introduced_sha": "b" * 40,
                "class": "mechanical", "severity": "minor",
                "disposition": disposition, "logic_touched": False,
                "overlap_id": None, "overlap_id_reason": "independent",
                "fixer": "orchestrator", "fix_sha": "c" * 40,
                "validation": None, "validation_reason": "n/a",
                "evidence_path": evidence, "recorded_at": ts,
                "provenance": "live"}

    def _outcome_row(self):
        return {"event": "outcome", "event_id": "outcome-S-1",
                "story_id": "S", "merged_sha": "deadbeef",
                "merged_at": "2026-09-02T01:00:00Z",
                "live_proof_status": None,
                "totals": {"plan_loops": 0, "code_loops": 0},
                "recorded_at": "2026-09-02T01:30:00Z", "provenance": "live"}

    def _render(self, events):
        with tempfile.TemporaryDirectory() as td:
            return RD.render(events, Path(td), self.HEADER)

    def test_update_across_two_rounds_counts_both(self):
        r1 = self._finding("finding-S-1", "F-1",
                           "stories/S/external-round-1.md", "open",
                           "2026-09-01T00:00:00Z")
        r2 = self._finding("finding-S-2", "F-1",
                           "stories/S/external-round-2.md", "fixed",
                           "2026-09-01T01:00:00Z")
        text = self._render([r1, r2, self._outcome_row()])
        # total (merged rollup) AND the per-story Ext rounds cell = 2
        self.assertIn("| Observed external correction rounds | 2 |", text)
        self.assertIn("| S | — | `MERGED*` | 0/0 | 2 |", text)

    def test_single_round_unchanged(self):
        r1 = self._finding("finding-S-1", "F-1",
                           "stories/S/external-round-1.md", "fixed",
                           "2026-09-01T00:00:00Z")
        text = self._render([r1, self._outcome_row()])
        self.assertIn("| Observed external correction rounds | 1 |", text)
        self.assertIn("| S | — | `MERGED*` | 0/0 | 1 |", text)

    def test_same_round_repeated_evidence_counts_once(self):
        # two distinct findings cited in the SAME round file = one round
        r1 = self._finding("finding-S-1", "F-1",
                           "stories/S/external-round-1.md", "fixed",
                           "2026-09-01T00:00:00Z")
        r2 = self._finding("finding-S-2", "F-2",
                           "stories/S/external-round-1.md", "fixed",
                           "2026-09-01T00:30:00Z")
        text = self._render([r1, r2, self._outcome_row()])
        self.assertIn("| Observed external correction rounds | 1 |", text)
        self.assertIn("| S | — | `MERGED*` | 0/0 | 1 |", text)

    def test_dispositions_still_fold_to_latest_row(self):
        # the round-2 update flips disposition to `fixed`; the correction
        # count still sees ONE fixed finding (latest-row fold intact)
        r1 = self._finding("finding-S-1", "F-1",
                           "stories/S/external-round-1.md", "open",
                           "2026-09-01T00:00:00Z")
        r2 = self._finding("finding-S-2", "F-1",
                           "stories/S/external-round-2.md", "fixed",
                           "2026-09-01T01:00:00Z")
        text = self._render([r1, r2, self._outcome_row()])
        # Corrections cell = 1 (one fixed finding after the fold, no loops)
        self.assertIn("| S | — | `MERGED*` | 0/0 | 2 | 1 | deadbeef | — |",
                      text)

    def test_internal_findings_not_counted_as_external_rounds(self):
        row = self._finding("finding-S-1", "F-1",
                            "stories/S/internal-round-1.md", "fixed",
                            "2026-09-01T00:00:00Z")
        row["source"] = "internal"
        text = self._render([row, self._outcome_row()])
        self.assertIn("| Observed external correction rounds | 0 |", text)


class GateLifecycleTotalsTests(unittest.TestCase):
    """R23-X2: gate totals/avoidable count LIFECYCLES, not rows — an
    unanswered ask row superseded by its appended answer is the SAME
    human intervention and must not be counted twice (the treatment the
    pending calculation already applied)."""

    HEADER = "H"

    def _gate(self, eid, gate, answered, ts):
        return {"event": "human_gate", "event_id": eid, "story_id": "S",
                "gate": gate, "question": "q?", "asked_at": ts,
                "answered_at": ts if answered else None,
                "decision": "approved" if answered else None,
                "answered_by": "human-1" if answered else None,
                "recorded_at": ts, "provenance": "live"}

    def _render(self, events):
        with tempfile.TemporaryDirectory() as td:
            return RD.render(events, Path(td), self.HEADER)

    def _gate_line(self, text):
        return next(l for l in text.splitlines()
                    if l.startswith("| Human gates"))

    def test_ask_answer_pair_counted_once(self):
        ask = self._gate("human_gate-S-1", "avoidable", False,
                         "2026-09-01T00:00:00Z")
        ans = self._gate("human_gate-S-2", "avoidable", True,
                         "2026-09-01T00:30:00Z")
        line = self._gate_line(self._render([ask, ans]))
        self.assertIn("total 1, avoidable 1, pending 0", line)

    def test_unanswered_ask_counted_once_and_pending(self):
        ask = self._gate("human_gate-S-1", "avoidable", False,
                         "2026-09-01T00:00:00Z")
        line = self._gate_line(self._render([ask]))
        self.assertIn("total 1, avoidable 1, pending 1", line)

    def test_answered_gate_alone_counted(self):
        ans = self._gate("human_gate-S-1", "merge", True,
                         "2026-09-01T00:00:00Z")
        line = self._gate_line(self._render([ans]))
        self.assertIn("total 1, avoidable 0, pending 0", line)

    def test_distinct_gates_both_counted(self):
        # supersession matches on (story, gate) — a different gate is a
        # distinct lifecycle and is NOT folded away
        ask = self._gate("human_gate-S-1", "avoidable", False,
                         "2026-09-01T00:00:00Z")
        other = self._gate("human_gate-S-2", "merge", True,
                           "2026-09-01T00:30:00Z")
        line = self._gate_line(self._render([ask, other]))
        self.assertIn("total 2, avoidable 1, pending 1", line)


class OwnerCellEscapeTests(unittest.TestCase):
    """Round 32 AG3: a free-form owner like `ops | backup` used to add
    a Markdown column and shift every following cell of the per-story
    row; newline variants split the row. The owner cell (and its
    ledger-sourced siblings) are escaped before insertion."""

    def _claim(self, owner: str) -> dict:
        return {"event": "assignment", "op": "claim", "story_id": "STORY-201",
                "owner": owner, "host": "h", "session": "s",
                "claimed_at": "2026-09-01T00:00:00Z", "start_sha": "a" * 40,
                "event_id": "asg-1", "recorded_at": "2026-09-01T00:00:00Z",
                "provenance": "live"}

    def _row(self, owner: str) -> str:
        with tempfile.TemporaryDirectory() as td:
            text = RD.render([self._claim(owner)], Path(td), "H")
        return next(l for l in text.splitlines()
                    if l.startswith("| STORY-201 |"))

    def test_pipe_owner_escaped_columns_intact(self):
        row = self._row("ops | backup")
        self.assertIn("ops \\| backup", row)
        # exactly the 8 declared columns: 9 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 9)

    def test_newline_owner_flattened_columns_intact(self):
        row = self._row("ops\nbackup")
        self.assertNotIn("\n", row)
        self.assertIn("ops backup", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 9)

    def test_crlf_and_lone_cr_owner_flattened(self):
        for owner in ("ops\r\nbackup", "ops\rbackup"):
            row = self._row(owner)
            self.assertNotIn("\r", row)
            self.assertIn("ops backup", row)
            self.assertEqual(row.replace("\\|", "").count("|"), 9)

    def test_plain_owner_unchanged(self):
        row = self._row("agent-a")
        self.assertIn("| agent-a |", row)
        self.assertNotIn("\\|", row)

    def test_cell_helper_identical_to_render_state(self):
        for v in ("a|b", "a\r\nb", "a\rb", "a\nb", "plain"):
            self.assertEqual(RD._cell(v), RS._cell(v), v)


class Round34LFNormalizationTests(unittest.TestCase):
    """Round 34 AI1: the corrections register embeds free-form
    corrected_value / source_citation OUTSIDE any table (no _cell), so
    CRLF / lone CR in them used to land verbatim in DASHBOARD.md —
    `newline="\\n"` on write does not remove it and --check reported the
    CR-carrying artifact fresh. The renderer now normalizes the ASSEMBLED
    output (CRLF→LF, then lone CR→LF) before returning."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        cls.ledger = build_ledger(cls.root)
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @staticmethod
    def _corr(eid, value, citation):
        return {"event": "correction", "event_id": eid,
                "story_id": "STORY-201", "repo": None,
                "repo_reason": "control-home level correction",
                "contradicted_claim": "totals said zero plan loops",
                "corrected_value": value,
                "source_citation": citation,
                "corrected_artifact": "stories/STORY-201/state.md",
                "recorded_at": "2026-09-01T00:00:00Z",
                "provenance": "live"}

    def test_correction_crlf_and_lone_cr_normalize_to_lf(self):
        events = L.load_events(self.ledger) + [
            self._corr("correction-STORY-201-90",
                       "alpha\r\nbeta\rgamma", "cite\r\nX\ry")]
        text = RD.render(events, self.root, self.header,
                         ledger_path=self.ledger)
        self.assertNotIn("\r", text)
        self.assertIn("alpha\nbeta\ngamma (_cite\nX\ny_)", text)

    def test_cli_generated_dashboard_has_no_cr_and_check_is_clean(self):
        L.append_event(self.ledger, self._corr(
            "correction-STORY-201-91", "a\r\nb\rc", "s\r1"))
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        dash = self.root / "factory" / "DASHBOARD.md"
        self.assertNotIn(b"\r", dash.read_bytes())
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 0)

    def test_crlf_mangled_dashboard_still_stale(self):
        dash = self.root / "factory" / "DASHBOARD.md"
        raw = dash.read_bytes()
        dash.write_bytes(raw.replace(b"\n", b"\r\n"))
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 1)
        self.assertEqual(RD.main(["--root", str(self.root)]), 0)
        self.assertEqual(RD.main(["--check", "--root", str(self.root)]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
