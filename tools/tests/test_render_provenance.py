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
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(RP.main(["--root", str(self.root)]), 2)
        self.assertIn("usage:", err.getvalue())

    def test_empty_all_discovery_is_successful_noop(self):
        """R19-T2: --all over an empty/missing ledger discovers no story
        ids — that is a successful no-op (rc 0), not a usage error; the
        usage error stays for invocations WITHOUT --all."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            empty = root / "factory" / "ledger" / "events.jsonl"
            empty.write_bytes(b"")
            self.assertEqual(
                RP.main(["--all", "--check", "--root", str(root)]), 0)
            self.assertEqual(RP.main(["--all", "--root", str(root)]), 0)
            # missing ledger file → same no-op
            empty.unlink()
            self.assertEqual(
                RP.main(["--all", "--check", "--root", str(root)]), 0)
            self.assertEqual(RP.main(["--all", "--root", str(root)]), 0)
            # nothing rendered
            self.assertFalse((root / "stories").exists())

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

    # ---- round 37 AL4: question-aware supersession
    def test_answer_supersedes_only_its_own_question(self):
        """An answer to question A on a gate supersedes ONLY A's ask row;
        a distinct question B on the same (story, gate) stays PENDING —
        the (story_id, gate, question) identity rule of render_state.py."""
        ask, answer, _lone = self._gate_rows()
        ask_b = {**ask, "event_id": "human_gate-STORY-210-9",
                 "question": "Second scope question B?",
                 "asked_at": "2026-09-02T00:05:00Z",
                 "recorded_at": "2026-09-02T00:05:00Z"}
        text = RP.render([ask, ask_b, answer], "STORY-210", self.header)
        row_a = next(l for l in text.splitlines()
                     if l.startswith("| merge | Merge approved diff? |"))
        row_b = next(l for l in text.splitlines()
                     if l.startswith("| merge | Second scope question B? |"))
        self.assertIn("superseded by human_gate-STORY-210-2", row_a)
        self.assertIn("PENDING — awaiting the allowlisted approver", row_b)
        self.assertNotIn("superseded", row_b)

    def test_exact_question_repeat_still_supersedes(self):
        """Same gate + same question + answered → the ask row is still
        superseded (the identity combination matches exactly)."""
        ask, answer, _lone = self._gate_rows()
        text = RP.render([ask, answer], "STORY-210", self.header)
        self.assertIn("superseded by human_gate-STORY-210-2", text)
        self.assertNotIn("PENDING", text)

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
        # all rows unknown → 'unknown (<reasons>)', mirroring tok_cell —
        # never an unqualified number, never a zero
        self.assertIn("| Input tokens | unknown (executor reported "
                      "no counts) |", text)
        self.assertIn("| Output tokens | unknown (executor reported "
                      "no counts) |", text)
        self.assertIn("| Cached input tokens | unknown (n/a) |", text)
        # per-row cells too — never an invented zero
        self.assertNotIn("| None |", text)
        self.assertNotIn(" 0 |", text.split("## Token usage")[1])

    # ---- round 21 V3: mixed known/unknown rows render a PARTIAL total
    def test_partial_token_totals_mark_unknown_rows(self):
        known = {
            "event": "usage", "event_id": "usage-STORY-212-coder-1",
            "dispatch_id": "STORY-212-coder-1-1789003000",
            "input_tokens": 1000, "output_tokens": 200,
            "cached_input_tokens": 50,
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        unknown = {
            "event": "usage", "event_id": "usage-STORY-212-reviewer-1",
            "dispatch_id": "STORY-212-reviewer-1-1789004000",
            "input_tokens": None,
            "input_tokens_reason": "executor reported no counts",
            "output_tokens": None,
            "output_tokens_reason": "executor reported no counts",
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "n/a",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T03:00:00Z", "provenance": "live"}
        text = RP.render([known, unknown], "STORY-212", self.header)
        self.assertIn("| Input tokens | 1,000 partial (1 unknown rows) |",
                      text)
        self.assertIn("| Output tokens | 200 partial (1 unknown rows) |",
                      text)
        self.assertIn("| Cached input tokens | 50 partial "
                      "(1 unknown rows) |", text)
        # all-known rows are unqualified (unchanged behavior)
        solo = RP.render([known], "STORY-212", self.header)
        self.assertIn("| Input tokens | 1000 |", solo)
        self.assertNotIn("partial", solo.split("## Token usage")[1])

    # ---- round 21 V4: two usage rows for ONE dispatch count once
    def test_dispatches_with_usage_counts_distinct_ids(self):
        usage_a = {
            "event": "usage", "event_id": "usage-STORY-213-coder-1-a",
            "dispatch_id": "STORY-213-coder-1-1789005000",
            "input_tokens": 10, "output_tokens": 2,
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "n/a",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        usage_b = {**usage_a, "event_id": "usage-STORY-213-coder-1-b",
                   "input_tokens": 5, "output_tokens": 1}
        text = RP.render([usage_a, usage_b], "STORY-213", self.header)
        self.assertIn("| Dispatches with usage | 1 |", text)
        self.assertIn("usage events: 2.", text)
        # totals still sum BOTH rows
        self.assertIn("| Input tokens | 15 |", text)
        # two distinct dispatches stay 2
        usage_c = {**usage_a, "event_id": "usage-STORY-213-reviewer-1",
                   "dispatch_id": "STORY-213-reviewer-1-1789006000"}
        text2 = RP.render([usage_a, usage_c], "STORY-213", self.header)
        self.assertIn("| Dispatches with usage | 2 |", text2)


    # ---- round 25 Z2: token-cell escaping (Measure + per-dispatch rows)
    def test_reason_with_pipe_and_newline_stays_in_its_cell(self):
        """A schema-valid reason containing `|`/newline must not add
        columns or split rows in either token table."""
        usage = {
            "event": "usage", "event_id": "usage-STORY-214-coder-1",
            "dispatch_id": "STORY-214-coder-1-1789007000",
            "input_tokens": None,
            "input_tokens_reason": "executor said a|b\nc",
            "output_tokens": None,
            "output_tokens_reason": "executor said a|b\nc",
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "executor said a|b\nc",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        text = RP.render([usage], "STORY-214", self.header)
        # Measure table: escaped pipe, newline flattened to a space
        row = next(l for l in text.splitlines()
                   if l.startswith("| Input tokens |"))
        self.assertIn("unknown (executor said a\\|b c)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 3)  # 2 columns
        # Per-dispatch table: fmt_null reason cell escaped, 6 columns hold
        drow = next(l for l in text.splitlines()
                    if l.startswith("| STORY-214-coder-1-1789007000 |"))
        self.assertIn("not recorded: executor said a\\|b c", drow)
        self.assertEqual(drow.replace("\\|", "").count("|"), 7)
        # the row was not split by the embedded newline
        self.assertNotIn("c |", drow.replace("a\\|b c", ""))

    def test_plain_token_cells_render_unchanged(self):
        """Reasons without pipes/newlines render byte-identically."""
        usage = {
            "event": "usage", "event_id": "usage-STORY-215-coder-1",
            "dispatch_id": "STORY-215-coder-1-1789008000",
            "input_tokens": 1000, "output_tokens": 200,
            "cached_input_tokens": None,
            "cached_input_tokens_reason": "no cache in use",
            "thinking_tokens": None, "thinking_tokens_reason": "n/a",
            "token_semantics": "tokens", "usage_source": "executor-report",
            "recorded_at": "2026-09-01T02:00:00Z", "provenance": "live"}
        text = RP.render([usage], "STORY-215", self.header)
        self.assertIn("| Input tokens | 1000 |", text)
        drow = next(l for l in text.splitlines()
                    if l.startswith("| STORY-215-coder-1-1789008000 |"))
        self.assertIn("| not recorded: no cache in use |", drow)
        self.assertNotIn("\\|", drow)
        self.assertEqual(drow.replace("\\|", "").count("|"), 7)

    # ---- round 26 AA2: formatted null reasons in timeline + gate rows
    def _transition(self, **overrides) -> dict:
        t = {"event": "transition", "event_id": "transition-STORY-216-1",
             "story_id": "STORY-216", "repo": None,
             "repo_reason": "single-repo",
             "from_state": "CODING", "to_state": "BUILD_GREEN",
             "requested_by": "orchestrator", "checks_run": [],
             "check_results": {}, "approved_by": None,
             "approved_by_reason": "no gate required",
             "recorded_at": "2026-09-01T07:00:00Z", "provenance": "live"}
        t.update(overrides)
        return t

    def test_timeline_null_reason_pipe_and_newline_stay_in_cell(self):
        """approved_by_reason with `|`/newline must not add columns or
        split the timeline row (the emphasis wrapper is escaped too)."""
        t = self._transition(approved_by_reason="gate a|b\npending")
        text = RP.render([t], "STORY-216", self.header)
        row = next(l for l in text.splitlines()
                   if l.startswith("| 2026-09-01T07:00:00Z |"))
        self.assertIn("_not recorded: gate a\\|b pending_", row)
        # exactly the 6 declared columns: 7 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 7)
        self.assertTrue(row.endswith("|  |"))

    def test_pending_gate_reasons_pipe_and_newline_stay_in_cell(self):
        """A pending gate's decision_reason/answered_by_reason with
        `|`/newline must not corrupt the human-gate table."""
        g = {"event": "human_gate", "event_id": "human_gate-STORY-216-1",
             "story_id": "STORY-216", "gate": "merge",
             "question": "Merge?", "asked_at": "2026-09-02T00:00:00Z",
             "answered_at": None, "answered_at_reason": "waiting",
             "decision": None, "decision_reason": "un|answered\nx",
             "answered_by": None, "answered_by_reason": "u|nanswered",
             "recorded_at": "2026-09-02T00:00:00Z", "provenance": "live"}
        text = RP.render([g], "STORY-216", self.header)
        row = next(l for l in text.splitlines()
                   if l.startswith("| merge |"))
        self.assertIn("not recorded: un\\|answered x", row)
        self.assertIn("not recorded: u\\|nanswered", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 7)

    def test_plain_reason_cells_render_unchanged(self):
        """Plain reasons keep their exact prior form."""
        t = self._transition()
        text = RP.render([t], "STORY-216", self.header)
        row = next(l for l in text.splitlines()
                   if l.startswith("| 2026-09-01T07:00:00Z |"))
        self.assertIn("_not recorded: no gate required_", row)
        self.assertNotIn("\\|", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 7)

    # ---- round 26 AA4: --check treats a MISSING artifact as stale
    def test_check_missing_artifact_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = build_ledger(root)
            self.assertEqual(
                RP.main(["STORY-201", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            p = root / "stories" / "STORY-201" / "PROVENANCE.md"
            self.assertTrue(p.exists())
            p.unlink()  # a deleted artifact is stale, never fresh
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = RP.main(["STORY-201", "--check", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("(missing)", out.getvalue())
            # re-render clears it
            self.assertEqual(
                RP.main(["STORY-201", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            self.assertEqual(
                RP.main(["STORY-201", "--check", "--root", str(root),
                         "--ledger", str(ledger)]), 0)

    def test_check_missing_artifact_never_rendered_is_stale(self):
        """A ledger story whose PROVENANCE.md was NEVER rendered also
        reports stale (rc 1), not silently clean."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = build_ledger(root)
            self.assertEqual(
                RP.main(["STORY-201", "--check", "--root", str(root),
                         "--ledger", str(ledger)]), 1)


class OrphanUsageStoryComponentTests(unittest.TestCase):
    """Round 38 AM4: orphan-usage recovery (dispatch row missing) used a
    bare `startswith(f"{sid}-")` prefix match — `STORY-201-followup-
    coder-1-1789000000` (story component `STORY-201-followup`) was
    attributed to `STORY-201`, corrupting its token totals. Recovery now
    splits off the final role/attempt/timestamp components and compares
    the REMAINING story component EXACTLY."""

    CANON = "STORY-201-coder-1-1789000000"
    FOLLOWUP = "STORY-201-followup-coder-1-1789000000"

    def setUp(self):
        self.events = [
            {"event": "usage", "dispatch_id": self.CANON,
             "input_tokens": 10},
            {"event": "usage", "dispatch_id": self.FOLLOWUP,
             "input_tokens": 99},
            {"event": "usage", "dispatch_id": "too-short",
             "input_tokens": 5},
        ]

    def test_followup_not_attributed_to_prefix_story(self):
        got = RP.usage_for_story(self.events, "STORY-201")
        self.assertEqual([u["dispatch_id"] for u in got], [self.CANON])

    def test_followup_attributed_to_its_own_story(self):
        # chosen behavior: the orphan IS recoverable for its own story
        # (exact component match), just never for a shorter prefix
        got = RP.usage_for_story(self.events, "STORY-201-followup")
        self.assertEqual([u["dispatch_id"] for u in got], [self.FOLLOWUP])

    def test_short_id_never_recovers(self):
        # fewer than 4 components → no story component, no attribution
        self.assertEqual(RP._orphan_story_component("too-short"), None)
        self.assertEqual(RP.usage_for_story(self.events, "too"), [])

    def test_canonical_recovery_unchanged(self):
        self.assertEqual(
            RP._orphan_story_component(self.CANON), "STORY-201")
        got = RP.usage_for_story(
            [{"event": "usage", "dispatch_id": self.CANON,
              "input_tokens": 10}], "STORY-201")
        self.assertEqual(len(got), 1)

    def test_dispatch_row_join_still_wins(self):
        # a dispatch row present → its story_id governs, exact match
        # irrelevant (join path, not recovery)
        events = self.events + [
            {"event": "dispatch", "event_id": self.FOLLOWUP,
             "story_id": "STORY-202", "role": "coder"}]
        self.assertEqual(
            [u["dispatch_id"]
             for u in RP.usage_for_story(events, "STORY-202")],
            [self.FOLLOWUP])
        self.assertEqual(RP.usage_for_story(events, "STORY-201-followup"),
                         [])


class SymlinkConfinementTests(unittest.TestCase):
    """Round 28 AC1 sibling: render_provenance had the identical
    canonical-ID-only pattern — `stories/<sid>` as a SYMLINK outward let
    the renderer OVERWRITE an unrelated PROVENANCE.md. The resolved
    story dir / output file / sidecar must stay under the resolved
    stories root BEFORE rendering; refusal is named, nothing written."""

    def _minimal_ledger(self, root: Path, sid: str = "STORY-230") -> Path:
        ledger = root / "factory" / "ledger" / "events.jsonl"
        L.append_event(ledger, {
            "event": "transition", "event_id": f"transition-{sid}-1",
            "story_id": sid, "repo": None, "repo_reason": "story-level",
            "from_state": "BACKLOG", "to_state": "CODING",
            "requested_by": "orchestrator", "checks_run": [],
            "check_results": {}, "approved_by": None,
            "approved_by_reason": "no gate required",
            "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"})
        return ledger

    def test_symlinked_story_dir_outward_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            victim = outside / "PROVENANCE.md"
            victim.write_text("unrelated provenance\n", encoding="utf-8")
            ledger = self._minimal_ledger(root)
            stories = root / "stories"
            stories.mkdir()
            (stories / "STORY-230").symlink_to(outside)
            err = io.StringIO()
            out = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(out):
                rc = RP.main(["STORY-230", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertIn("outside the stories directory", err.getvalue())
            self.assertNotIn("rendered", out.getvalue())
            self.assertEqual(victim.read_text(encoding="utf-8"),
                             "unrelated provenance\n")

    def test_symlinked_story_dir_outward_refused_with_all(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            ledger = self._minimal_ledger(root)
            stories = root / "stories"
            stories.mkdir()
            (stories / "STORY-230").symlink_to(outside)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RP.main(["--all", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertFalse((outside / "PROVENANCE.md").exists())

    def test_symlink_inside_stories_ok_and_normal_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            ledger = self._minimal_ledger(root)
            stories = root / "stories"
            real = stories / "real-dir"
            real.mkdir(parents=True)
            (stories / "STORY-230").symlink_to(real)
            self.assertEqual(
                RP.main(["STORY-230", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            self.assertTrue((real / "PROVENANCE.md").read_text(
                encoding="utf-8").startswith(RP.HEADER_START))
            # a plain directory renders as before
            self.assertEqual(
                RP.main(["STORY-231", "--root", str(root),
                         "--ledger", str(self._minimal_ledger(
                             root, "STORY-231"))]), 0)
            self.assertTrue((stories / "STORY-231" / "PROVENANCE.md")
                            .exists())


class ProvenanceCellSweepTests(unittest.TestCase):
    """Round 32 S2: render_provenance._cell is IDENTICAL to
    render_state._cell (CR/CRLF flattened too), and the remaining
    free-text cells (loop stage/class/co_classes, gate, asked_at) are
    escaped before insertion."""

    header = RP.RSt.generated_header("factory/ledger/events.jsonl")

    def test_cell_handles_cr_variants(self):
        self.assertEqual(RP._cell("a\r\nb"), "a b")
        self.assertEqual(RP._cell("a\rb"), "a b")
        self.assertEqual(RP._cell("a\nb"), "a b")
        self.assertEqual(RP._cell("a|b"), "a\\|b")
        self.assertEqual(RP._cell("plain"), "plain")
        for v in ("a|b", "a\r\nb", "a\rb", "a\nb", "plain"):
            self.assertEqual(RP._cell(v), RP.RSt._cell(v), v)

    def test_loop_stage_class_cells_escaped(self):
        lp = {"event": "loop", "event_id": "loop-STORY-218-1",
              "story_id": "STORY-218", "stage": "plan|review",
              "loop_n": 1, "class": "reason|ing", "co_classes": ["a|b"],
              "root_cause_one_line": "x", "occurred_on": "2026-09-01",
              "recorded_at": "2026-09-01T02:10:00Z", "provenance": "live"}
        text = RP.render([lp], "STORY-218", self.header)
        row = next(l for l in text.splitlines()
                   if l.startswith("| 2026-09-01 |"))
        self.assertIn("plan\\|review", row)
        self.assertIn("reason\\|ing", row)
        self.assertIn("(+a\\|b)", row)
        # exactly the 5 declared columns: 6 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 6)

    def test_gate_and_asked_at_cells_escaped(self):
        g = {"event": "human_gate", "event_id": "human_gate-STORY-218-1",
             "story_id": "STORY-218", "gate": "mer|ge",
             "question": "Merge?", "asked_at": "2026-09-02|00:00",
             "answered_at": None, "answered_at_reason": "waiting",
             "decision": None, "decision_reason": "waiting",
             "answered_by": None, "answered_by_reason": "waiting",
             "recorded_at": "2026-09-02T00:00:00Z", "provenance": "live"}
        text = RP.render([g], "STORY-218", self.header)
        row = next(l for l in text.splitlines()
                   if l.startswith("| mer\\|ge |"))
        self.assertIn("2026-09-02\\|00:00", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 7)


class Round34LFNormalizationTests(unittest.TestCase):
    """Round 34 sweep: identity/ownership bullets embed free-form ledger
    values (owner, host, session, reason) OUTSIDE tables — no `_cell` —
    so a CR used to land verbatim in PROVENANCE.md. The renderer now
    normalizes the ASSEMBLED output (CRLF→LF, then lone CR→LF)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        cls.ledger = build_ledger(cls.root)
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_owner_cr_crlf_normalizes_to_lf(self):
        claim = {"event": "assignment", "event_id": "assignment-STORY-201-90",
                 "story_id": "STORY-201", "op": "claim",
                 "owner": "agent\r\nx\ry", "host": "host\r1",
                 "session": "sess-201",
                 "claimed_at": "2026-09-01T00:00:00Z",
                 "start_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"}
        text = RP.render(L.load_events(self.ledger) + [claim],
                         "STORY-201", self.header)
        self.assertNotIn("\r", text)
        self.assertIn("- **owner:** agent\nx\ny", text)
        self.assertIn("- **host:** host\n1", text)

    def test_cli_generated_provenance_has_no_cr_and_check_is_clean(self):
        L.append_event(self.ledger, {
            "event": "assignment", "event_id": "assignment-STORY-203-90",
            "story_id": "STORY-203", "op": "claim", "owner": "o\rx",
            "host": "host-1", "session": "sess-203",
            "claimed_at": "2026-09-01T00:00:00Z",
            "start_sha": "cccccccccccccccccccccccccccccccccccccccc",
            "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"})
        self.assertEqual(
            RP.main(["STORY-203", "--root", str(self.root)]), 0)
        p = self.root / "stories" / "STORY-203" / "PROVENANCE.md"
        self.assertNotIn(b"\r", p.read_bytes())
        self.assertEqual(
            RP.main(["STORY-203", "--check", "--root", str(self.root)]), 0)

    def test_crlf_mangled_provenance_still_stale(self):
        p = self.root / "stories" / "STORY-203" / "PROVENANCE.md"
        raw = p.read_bytes()
        p.write_bytes(raw.replace(b"\n", b"\r\n"))
        self.assertEqual(
            RP.main(["STORY-203", "--check", "--root", str(self.root)]), 1)
        self.assertEqual(
            RP.main(["STORY-203", "--root", str(self.root)]), 0)
        self.assertEqual(
            RP.main(["STORY-203", "--check", "--root", str(self.root)]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
