#!/usr/bin/env python3
"""Tests for tools/render_provenance.py — PROVENANCE.md trace rendering:
frozen generated header (no wall-clock stamp — byte contract), ownership
(claim lifecycle), state timeline, loops + findings tallies, human gates,
review contexts & attribution, delivery, tokens-only usage join via
dispatch_id (two-row dispatch lifecycle merge), evidence index, null +
reason house rule (never invented zeros), --all verbatim story ids,
--check stale/fresh, alt-ledger support, determinism, and CLI refusals.

Fixtures: tools/tests/fixtures/render/sample_events.json via
ledger_lib.append_event. No network, no writes outside tempdirs.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from tools import ledger_lib as L
from tools import render_provenance as RP
from tools import render_state as RS
from tools.tests.test_render_state import build_ledger


class RenderProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        cls.ledger = build_ledger(cls.root)
        cls.events = L.load_events(cls.ledger)
        cls.stories = cls.root / "stories"
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def render(self, sid: str) -> str:
        return RP.render(self.events, sid, self.header)

    # ---- byte contract + sections
    def test_header_and_trailing_newline(self):
        text = self.render("STORY-201")
        self.assertTrue(text.startswith(RP.HEADER_START))
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_full_story_sections(self):
        text = self.render("STORY-201")
        self.assertIn("# Provenance: STORY-201", text)
        for section in ("## Identity & ownership", "## State timeline",
                        "## Review & loops", "## Delivery",
                        "## Token usage", "## Evidence index"):
            self.assertIn(section, text)
        # ownership: claim fields
        self.assertIn("- **owner:** agent-a", text)
        self.assertIn("- **start_sha:** " + "a" * 40, text)
        # timeline: both transitions; human-gated one shows approver
        self.assertIn("BACKLOG → **CODING**", text)
        self.assertIn("MERGE_ELIGIBLE → **MERGED**", text)
        self.assertIn("approver-1", text)
        self.assertIn("1 human-gated", text)
        # loops + findings tally + human gate
        self.assertIn("plan omitted a migration step", text)
        self.assertIn("### Findings tally (2", text)
        self.assertIn("| internal | 1 |", text)
        self.assertIn("| major | 1 |", text)
        self.assertIn("Merge approved diff?", text)
        # review contexts + attribution
        self.assertIn("review_context events: 2 "
                      "(behavior_reviewer, external_review)", text)
        self.assertIn("review_attribution events: 1 — "
                      "MISSED_WITH_CONTEXT 1", text)
        # delivery
        self.assertIn("**merged_sha:** deadbeef1234", text)
        self.assertIn("**live_proof_status:** proven", text)
        self.assertIn("**escaped_defects:** 0", text)
        # tokens-only usage (no cost, no API-call column)
        self.assertIn("| Input tokens | 1000 |", text)
        self.assertIn("| Output tokens | 200 |", text)
        self.assertIn("| Cached input tokens | 50 |", text)
        self.assertNotIn("API calls", text)
        self.assertNotIn("cost", text.lower().replace("correct", ""))
        self.assertIn("| STORY-201-coder-1-1789000000 | coder | model-x |",
                      text)
        # evidence index: sorted distinct
        self.assertIn("`stories/STORY-201/attribution-F-201-2.md`", text)
        self.assertIn("`stories/STORY-201/plan-review.md`", text)

    def test_dispatch_lifecycle_merge_uses_completion_row(self):
        # pre-spawn row had reported_model null; completion row carries
        # model-x — the merged lifecycle row must show the served model
        text = self.render("STORY-201")
        self.assertIn("model-x |", text)
        self.assertIn("Dispatches recorded: 1 (distinct dispatch_id "
                      "lifecycles)", text)

    def test_null_reasons_never_invented(self):
        text = self.render("STORY-202")
        for frag in ("not recorded: not merged yet",
                     "not recorded: window not opened",
                     "not recorded: none known"):
            self.assertIn(frag, text)
        # null totals render no totals line at all — never an invented zero
        self.assertNotIn("**totals:**", text)
        self.assertNotIn("**merged_sha:** 0", text)
        self.assertNotIn("**escaped_defects:** 0", text)

    def test_no_usage_story(self):
        text = self.render("STORY-202")
        self.assertIn("No usage events recorded for this story.", text)
        self.assertIn("compile error at a seam", text)

    def test_usage_join_does_not_leak_across_stories(self):
        for sid in ("STORY-202", "T-A"):
            self.assertEqual(RP.usage_for_story(self.events, sid), [])
        self.assertEqual(len(RP.usage_for_story(self.events, "STORY-201")), 1)

    def test_determinism_no_wall_clock(self):
        a = self.render("STORY-201")
        b = self.render("STORY-201")
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(a.encode()).hexdigest(),
                         hashlib.sha256(b.encode()).hexdigest())

    # ---- CLI
    def test_cli_renders_and_check_clean(self):
        rc = RP.main(["STORY-201", "STORY-202", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        p = self.stories / "STORY-201" / "PROVENANCE.md"
        self.assertTrue(p.read_text(encoding="utf-8").startswith(
            RP.HEADER_START))
        self.assertEqual(
            RP.main(["STORY-201", "--check", "--root", str(self.root)]), 0)

    def test_check_detects_stale(self):
        self.assertEqual(RP.main(["STORY-202", "--root", str(self.root)]), 0)
        p = self.stories / "STORY-202" / "PROVENANCE.md"
        p.write_text(p.read_text(encoding="utf-8").replace(
            "## Delivery", "## Delivery\n\nhand-edited"), encoding="utf-8")
        self.assertEqual(
            RP.main(["STORY-202", "--check", "--root", str(self.root)]), 1)
        self.assertEqual(RP.main(["STORY-202", "--root", str(self.root)]), 0)
        self.assertEqual(
            RP.main(["STORY-202", "--check", "--root", str(self.root)]), 0)

    def test_check_ignores_non_generated_files(self):
        d = self.stories / "STORY-997"
        d.mkdir(parents=True, exist_ok=True)
        (d / "PROVENANCE.md").write_text("# hand-written, no header\n",
                                         encoding="utf-8")
        self.assertEqual(
            RP.main(["STORY-997", "--check", "--root", str(self.root)]), 0)

    def test_check_compares_bytes_crlf_fails(self):
        """R14-O4: a CRLF-converted generated file violates the required
        LF format and MUST fail --check — read_text() normalization used
        to hide the difference."""
        self.assertEqual(RP.main(["STORY-203", "--root", str(self.root)]), 0)
        p = self.stories / "STORY-203" / "PROVENANCE.md"
        raw = p.read_bytes()
        self.assertNotIn(b"\r\n", raw)  # renderer output is LF
        p.write_bytes(raw.replace(b"\n", b"\r\n"))
        self.assertEqual(
            RP.main(["STORY-203", "--check", "--root", str(self.root)]), 1)
        # re-render restores the LF bytes and --check passes again
        self.assertEqual(RP.main(["STORY-203", "--root", str(self.root)]), 0)
        self.assertEqual(p.read_bytes(), raw)
        self.assertEqual(
            RP.main(["STORY-203", "--check", "--root", str(self.root)]), 0)

    def test_all_discovery_confines_to_canonical_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RP.main(["--all", "--root", str(root)])
            self.assertEqual(rc, 0)
            for sid in ("STORY-201", "STORY-202"):
                self.assertTrue(
                    (root / "stories" / sid / "PROVENANCE.md").exists(),
                    f"missing render for {sid}")
            # non-canonical ledger ids are data, never path components
            self.assertFalse((root / "stories" / "T-A").exists())
            self.assertIn("skipping non-canonical story id from ledger: T-A",
                          err.getvalue())

    def test_alt_ledger_and_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            alt = Path(td) / "alt.jsonl"
            alt.write_bytes(self.ledger.read_bytes())
            self.assertEqual(
                RP.main(["STORY-201", "--root", str(self.root),
                         "--ledger", str(alt)]), 0)
            bad = Path(td) / "bad.jsonl"
            bad.write_text("{not json}\n", encoding="utf-8")
            self.assertEqual(
                RP.main(["STORY-201", "--root", str(self.root),
                         "--ledger", str(bad)]), 2)
        # no story ids and no --all → 2
        self.assertEqual(RP.main(["--root", str(self.root)]), 2)

    # ---- story-id path confinement (B2)
    def _traversal_ledger(self, root: Path) -> Path:
        ledger = root / "factory" / "ledger" / "events.jsonl"
        rows = [
            {"event": "transition",
             "event_id": "transition-STORY-201-1", "story_id": "STORY-201",
             "repo": None, "repo_reason": "story-level",
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator", "checks_run": [],
             "check_results": {}, "approved_by": None,
             "approved_by_reason": "no gate required",
             "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"},
            {"event": "transition",
             "event_id": "transition-outside-1", "story_id": "../outside",
             "repo": None, "repo_reason": "story-level",
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator", "checks_run": [],
             "check_results": {}, "approved_by": None,
             "approved_by_reason": "no gate required",
             "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"},
        ]
        for row in rows:
            L.append_event(ledger, row)
        return ledger

    def test_traversal_id_from_ledger_never_builds_a_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            self._traversal_ledger(root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RP.main(["--all", "--root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue((root / "stories" / "STORY-201" /
                             "PROVENANCE.md").exists())
            self.assertFalse((root.parent / "outside" /
                              "PROVENANCE.md").exists())
            self.assertFalse((root / "stories" / "../outside").exists())
            self.assertEqual(
                err.getvalue().count(
                    "skipping non-canonical story id from ledger: ../outside"),
                1)

    def test_explicit_noncanonical_id_refused(self):
        for bad in ("../outside", "/tmp/evil", "T-A"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RP.main([bad, "--root", str(self.root)])
            self.assertEqual(rc, 1, bad)
            self.assertIn(
                f"REFUSED: story id {bad} is not a canonical STORY-NNN",
                err.getvalue())
        self.assertFalse((self.root.parent / "outside" /
                          "PROVENANCE.md").exists())
        self.assertFalse((self.stories / "T-A" / "PROVENANCE.md").exists())
        self.assertFalse(Path("/tmp/evil/PROVENANCE.md").exists())

    # ---- state timeline repo column (B4)
    def test_state_timeline_preserves_repo_scope(self):
        events = self.events + [{
            "event": "transition", "event_id": "transition-STORY-201-9",
            "story_id": "STORY-201", "repo": "backend",
            "from_state": "CODING", "to_state": "BUILD_GREEN",
            "requested_by": "orchestrator", "checks_run": [],
            "check_results": {}, "approved_by": None,
            "approved_by_reason": "no gate required",
            "recorded_at": "2026-09-01T07:00:00Z", "provenance": "live"}]
        text = RP.render(events, "STORY-201", self.header)
        self.assertIn("| Recorded at (UTC) | From → To | Repo | Requested "
                      "by | Approved by (human gate) | Checks |", text)
        self.assertIn("| backend | orchestrator |", text)
        self.assertIn("**CODING** | — | orchestrator", text)

    # ---- gate supersede (B5)
    def _gate_rows(self):
        ask = {"event": "human_gate", "event_id": "human_gate-STORY-210-1",
               "story_id": "STORY-210", "gate": "merge",
               "question": "Merge approved diff?",
               "asked_at": "2026-09-02T00:00:00Z", "answered_at": None,
               "answered_at_reason": "awaiting the allowlisted approver",
               "decision": None, "decision_reason": "unanswered",
               "answered_by": None, "answered_by_reason": "unanswered",
               "recorded_at": "2026-09-02T00:00:00Z", "provenance": "live"}
        answer = {**ask, "event_id": "human_gate-STORY-210-2",
                  "answered_at": "2026-09-02T00:30:00Z",
                  "answered_at_reason": None, "decision": "approved",
                  "decision_reason": None, "answered_by": "approver-1",
                  "answered_by_reason": None,
                  "recorded_at": "2026-09-02T00:30:00Z"}
        lone = {**ask, "event_id": "human_gate-STORY-210-3", "gate": "ui",
                "question": "Approve UI change?",
                "asked_at": "2026-09-03T00:00:00Z",
                "recorded_at": "2026-09-03T00:00:00Z"}
        return ask, answer, lone

    def test_gate_answered_by_later_row_is_superseded(self):
        ask, answer, lone = self._gate_rows()
        text = RP.render([ask, answer, lone], "STORY-210", self.header)
        self.assertIn("superseded by human_gate-STORY-210-2", text)
        self.assertIn("| 2026-09-02T00:30:00Z |", text)  # answer row
        # the still-unanswered gate keeps PENDING + its reason
        self.assertIn("PENDING — awaiting the allowlisted approver", text)

    def test_gate_pending_without_reason(self):
        ask, _answer, _lone = self._gate_rows()
        ask = {**ask, "answered_at_reason": None}
        text = RP.render([ask], "STORY-210", self.header)
        self.assertIn("PENDING — no reason recorded", text)

    # ---- gate decision + respondent in the trace (R15-P2)
    def test_gate_decision_and_respondent_rendered(self):
        _ask, answer, _lone = self._gate_rows()
        text = RP.render([answer], "STORY-210", self.header)
        self.assertIn("| Gate | Question | Asked | Answered | Decision "
                      "| Answered by |", text)
        self.assertIn("|---|---|---|---|---|---|", text)
        self.assertIn("| approved | approver-1 |", text)

    def test_approved_and_rejected_gates_render_differently(self):
        _ask, answer, _lone = self._gate_rows()
        rejected = {**answer, "event_id": "human_gate-STORY-210-4",
                    "decision": "rejected", "answered_by": "approver-2"}
        ok = RP.render([answer], "STORY-210", self.header)
        bad = RP.render([rejected], "STORY-210", self.header)
        self.assertNotEqual(ok, bad)
        self.assertIn("| rejected | approver-2 |", bad)
        self.assertNotIn("rejected", ok)

    def test_pending_gate_decision_and_respondent_show_null_reasons(self):
        ask, _answer, _lone = self._gate_rows()
        text = RP.render([ask], "STORY-210", self.header)
        self.assertIn("not recorded: unanswered", text)
        row = next(l for l in text.splitlines()
                   if l.startswith("| merge |"))
        self.assertIn("not recorded: unanswered | not recorded: unanswered |",
                      row)

    # ---- nullable token totals with reasons (B6)
    def test_null_token_totals_render_reasons(self):
        dispatch = {
            "event": "dispatch", "event_id": "STORY-211-coder-1-1789002000",
            "story_id": "STORY-211", "role": "coder",
            "requested_model": "model-x", "reported_model": "model-x",
            "thinking_setting": None, "thinking_setting_reason": "n/a",
            "session_id": "sess-1", "parent_session_id": None,
            "parent_session_id_reason": "top-level", "attempt": 1,
            "started_at": "2026-09-01T01:00:00Z",
            "ended_at": "2026-09-01T02:00:00Z", "outcome": "success",
            "prompt_version": "coder-v0.1.0", "worktree": "/tmp/wt",
            "model_unverified": False,
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        usage = {
            "event": "usage", "event_id": "usage-STORY-211-coder-1",
            "dispatch_id": "STORY-211-coder-1-1789002000",
            "input_tokens": None,
            "input_tokens_reason": "executor reported no counts",
            "output_tokens": None,
            "output_tokens_reason": "executor reported no counts",
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "n/a",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        text = RP.render([dispatch, usage], "STORY-211", self.header)
        self.assertIn("| Input tokens | not recorded: executor reported "
                      "no counts |", text)
        self.assertIn("| Output tokens | not recorded: executor reported "
                      "no counts |", text)
        self.assertIn("| Cached input tokens | not recorded: n/a |", text)
        # per-row cells too — never an invented zero
        self.assertNotIn("| None |", text)
        self.assertNotIn(" 0 |", text.split("## Token usage")[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
