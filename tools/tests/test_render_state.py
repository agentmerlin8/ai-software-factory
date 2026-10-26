#!/usr/bin/env python3
"""Tests for tools/render_state.py — state.md rendering from the ledger:
generated header (frozen byte contract), section coverage, claim-lifecycle
ownership, null+reason rendering (never invented zeros), narrative.md
sidecar inlining, --all discovery, --check stale/fresh exits, workspace
name resolution, determinism, and CLI refusals.

Fixtures: tools/tests/fixtures/render/sample_events.json — a small
schema-valid sample ledger built through ledger_lib.append_event in setup
(grounding, not mocks; fictional STORY-2xx ids). No network, no writes
outside tempdirs.
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools import ledger_lib as L
from tools import render_state as RS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "render"


def build_ledger(root: Path) -> Path:
    """Build the sample control home: append every fixture event through
    ledger_lib.append_event (the real validated write path) into
    <root>/factory/ledger/events.jsonl."""
    ledger = root / "factory" / "ledger" / "events.jsonl"
    events = json.loads((FIXTURES / "sample_events.json").read_text(
        encoding="utf-8"))
    for e in events:
        L.append_event(ledger, e)
    return ledger


class RenderStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "workspace.toml").write_text(
            'workspace = "fixture-ws"\n', encoding="utf-8")
        cls.ledger = build_ledger(cls.root)
        cls.events = L.load_events(cls.ledger)
        cls.stories = cls.root / "stories"
        cls.stories.mkdir()
        cls.header = RS.generated_header("factory/ledger/events.jsonl")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def render(self, sid: str) -> str:
        return RS.render(self.events, sid, self.header, self.stories)

    # ---- byte contract + sections
    def test_header_is_the_frozen_generated_comment(self):
        self.assertEqual(
            self.header,
            "<!-- GENERATED from factory/ledger/events.jsonl — do not "
            "hand-edit; append events instead -->")

    def test_render_starts_with_header_and_trailing_newline(self):
        text = self.render("STORY-201")
        self.assertTrue(text.startswith(self.header))
        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\r", text)

    def test_full_story_sections(self):
        text = self.render("STORY-201")
        self.assertIn("# Story: STORY-201", text)
        self.assertIn("**Current state:** `MERGED`", text)
        for section in ("## Ownership (claim lifecycle)",
                        "## Transitions (controller-enforced)",
                        "## Stages", "## Revision loops (1)",
                        "## Review contexts", "## Findings (2)",
                        "## Review attribution (same snapshot + context)",
                        "## Human gates", "## Outcome",
                        "## Corrections (correct forward",
                        "## History"):
            self.assertIn(section, text)
        # claim lifecycle ownership
        self.assertIn("**claim** by agent-a on host-1 (session sess-201)",
                      text)
        self.assertIn("start pin: " + "a" * 40, text)
        # transition table rows (Repo column: story-level rows render —)
        self.assertIn("| BACKLOG | **CODING** | — | orchestrator |", text)
        self.assertIn("approver-1", text)
        # stage counts
        self.assertIn("| full_suite | 42 | 0 | 0 | 0 |", text)
        # loop row
        self.assertIn("plan omitted a migration step", text)
        # review context digest truncation
        self.assertIn("(`" + "d" * 12 + "…`)", text)
        # findings latest-lifecycle rows
        self.assertIn("| F-201-1 | internal | reasoning | major | True |",
                      text)
        # human gate answered
        self.assertIn("| merge | Merge approved diff? |", text)
        self.assertIn("2026-09-02T00:30:00Z", text)
        # outcome
        self.assertIn("merged: deadbeef1234", text)
        self.assertIn("live proof: proven", text)
        # correction register
        self.assertIn("one plan-review loop was recorded before merge", text)

    def test_no_wall_clock_content(self):
        # same ledger → byte-identical output (no timestamps injected)
        a = self.render("STORY-201")
        b = self.render("STORY-201")
        self.assertEqual(a, b)
        import hashlib
        self.assertEqual(hashlib.sha256(a.encode()).hexdigest(),
                         hashlib.sha256(b.encode()).hexdigest())

    def test_null_reasons_never_invented(self):
        text = self.render("STORY-202")
        self.assertIn("merged: — (not merged yet)", text)
        self.assertIn("live proof: — (window not opened)", text)
        self.assertNotIn("merged: 0", text)

    def test_events_only_story_derives_state(self):
        text = self.render("T-A")
        self.assertIn("**Current state:** `MERGED`", text)

    def test_backlog_story_has_no_events(self):
        text = self.render("STORY-999")
        self.assertIn("**Current state:** `BACKLOG (no events)`", text)

    def test_narrative_sidecar_inlined_verbatim(self):
        d = self.stories / "STORY-201"
        d.mkdir(parents=True, exist_ok=True)
        (d / "narrative.md").write_text("Prose from the sidecar.\n",
                                        encoding="utf-8")
        try:
            text = self.render("STORY-201")
            self.assertIn("## History (from narrative.md sidecar)", text)
            self.assertIn("Prose from the sidecar.", text)
        finally:
            (d / "narrative.md").unlink()

    def test_narrative_sidecar_not_trimmed(self):
        """R4-E5: a narrative opening with an indented command block is
        preserved byte-for-byte — no .strip() on the hand-written text."""
        d = self.stories / "STORY-201"
        d.mkdir(parents=True, exist_ok=True)
        raw = "    make deploy   # indented first line\nplain prose\n"
        (d / "narrative.md").write_text(raw, encoding="utf-8")
        try:
            text = self.render("STORY-201")
            section = text.split(
                "## History (from narrative.md sidecar)\n\n", 1)[1]
            self.assertTrue(section.startswith(raw),
                            f"narrative not verbatim: {section[:80]!r}")
        finally:
            (d / "narrative.md").unlink()

    def test_workspace_name_resolution(self):
        self.assertEqual(RS.workspace_name(self.root), "fixture-ws")
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(RS.workspace_name(Path(td)), "factory")
            (Path(td) / "workspace.toml").write_text("bogus toml [",
                                                     encoding="utf-8")
            self.assertEqual(RS.workspace_name(Path(td)), "factory")

    # ---- CLI
    def test_cli_renders_and_check_clean(self):
        rc = RS.main(["STORY-201", "--root", str(self.root)])
        self.assertEqual(rc, 0)
        p = self.stories / "STORY-201" / "state.md"
        self.assertTrue(p.read_text(encoding="utf-8").startswith(
            self.header))
        self.assertEqual(
            RS.main(["STORY-201", "--check", "--root", str(self.root)]), 0)

    def test_check_detects_stale(self):
        self.assertEqual(RS.main(["STORY-202", "--root", str(self.root)]), 0)
        p = self.stories / "STORY-202" / "state.md"
        p.write_text(p.read_text(encoding="utf-8").replace(
            "## Outcome", "## Outcome\n\nhand-edited"), encoding="utf-8")
        self.assertEqual(
            RS.main(["STORY-202", "--check", "--root", str(self.root)]), 1)
        self.assertEqual(RS.main(["STORY-202", "--root", str(self.root)]), 0)
        self.assertEqual(
            RS.main(["STORY-202", "--check", "--root", str(self.root)]), 0)

    def test_check_ignores_non_generated_files(self):
        d = self.stories / "STORY-998"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.md").write_text("# hand-written, no header\n",
                                    encoding="utf-8")
        self.assertEqual(
            RS.main(["STORY-998", "--check", "--root", str(self.root)]), 0)

    def test_check_compares_bytes_crlf_fails(self):
        """R14-O4: a CRLF-converted generated file violates the required
        LF format and MUST fail --check — read_text() normalization used
        to hide the difference."""
        self.assertEqual(RS.main(["STORY-203", "--root", str(self.root)]), 0)
        p = self.stories / "STORY-203" / "state.md"
        raw = p.read_bytes()
        self.assertNotIn(b"\r\n", raw)  # renderer output is LF
        p.write_bytes(raw.replace(b"\n", b"\r\n"))
        self.assertEqual(
            RS.main(["STORY-203", "--check", "--root", str(self.root)]), 1)
        # re-render restores the LF bytes and --check passes again
        self.assertEqual(RS.main(["STORY-203", "--root", str(self.root)]), 0)
        self.assertEqual(p.read_bytes(), raw)
        self.assertEqual(
            RS.main(["STORY-203", "--check", "--root", str(self.root)]), 0)

    def test_all_discovery_confines_to_canonical_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            ledger = root / "factory" / "ledger" / "events.jsonl"
            ledger.write_bytes(self.ledger.read_bytes())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RS.main(["--all", "--root", str(root)])
            self.assertEqual(rc, 0)
            for sid in ("STORY-201", "STORY-202"):
                self.assertTrue(
                    (root / "stories" / sid / "state.md").exists(),
                    f"missing render for {sid}")
            # non-canonical ledger ids are data, never path components
            self.assertFalse((root / "stories" / "T-A").exists())
            self.assertIn("skipping non-canonical story id from ledger: T-A",
                          err.getvalue())

    def test_alt_ledger_path(self):
        with tempfile.TemporaryDirectory() as td:
            alt = Path(td) / "alt.jsonl"
            alt.write_bytes(self.ledger.read_bytes())
            rc = RS.main(["STORY-201", "--root", str(self.root),
                          "--ledger", str(alt)])
            self.assertEqual(rc, 0)
            text = (self.stories / "STORY-201" / "state.md").read_text(
                encoding="utf-8")
            self.assertIn(str(alt), text.splitlines()[0])

    def test_usage_errors(self):
        # no story ids and no --all → 2
        self.assertEqual(RS.main(["--root", str(self.root)]), 2)
        # invalid ledger → 2 (fail closed, nothing written)
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.jsonl"
            bad.write_text("{not json}\n", encoding="utf-8")
            self.assertEqual(RS.main(["STORY-201", "--root", str(self.root),
                                      "--ledger", str(bad)]), 2)

    def test_empty_all_discovery_is_successful_noop(self):
        """R16-Q3: --all over an empty/missing ledger discovers no story
        ids — that is a successful no-op (rc 0), not a usage error; the
        usage error stays for invocations WITHOUT --all."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            empty = root / "factory" / "ledger" / "events.jsonl"
            empty.write_bytes(b"")
            self.assertEqual(RS.main(["--all", "--check", "--root",
                                      str(root)]), 0)
            self.assertEqual(RS.main(["--all", "--root", str(root)]), 0)
            # missing ledger file → same no-op
            empty.unlink()
            self.assertEqual(RS.main(["--all", "--check", "--root",
                                      str(root)]), 0)
            # nothing rendered
            self.assertFalse((root / "stories").exists())
        # WITHOUT --all an empty id list is still the usage error
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(RS.main(["--root", str(self.root)]), 2)
        self.assertIn("usage:", err.getvalue())

    # ---- story-id path confinement (B1)
    def _traversal_ledger(self, root: Path) -> Path:
        """Ledger whose rows carry a schema-valid but non-canonical
        story_id (`../outside`) — a real path-traversal attempt through
        the stories/<id>/ join."""
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
                rc = RS.main(["--all", "--root", str(root)])
            self.assertEqual(rc, 0)
            # the canonical story rendered; the traversal id did not
            self.assertTrue((root / "stories" / "STORY-201" /
                             "state.md").exists())
            self.assertFalse((root.parent / "outside" / "state.md").exists())
            self.assertFalse((root / "stories" / "../outside").exists())
            self.assertEqual(
                err.getvalue().count(
                    "skipping non-canonical story id from ledger: ../outside"),
                1)

    def test_explicit_noncanonical_id_refused(self):
        for bad in ("../outside", "/tmp/evil", "T-A"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RS.main([bad, "--root", str(self.root)])
            self.assertEqual(rc, 1, bad)
            self.assertIn(
                f"REFUSED: story id {bad} is not a canonical STORY-NNN",
                err.getvalue())
        # nothing written anywhere (no path was ever built)
        self.assertFalse((self.root.parent / "outside" / "state.md").exists())
        self.assertFalse((self.stories / "T-A" / "state.md").exists())
        self.assertFalse(Path("/tmp/evil/state.md").exists())

    def test_is_canonical_sid(self):
        self.assertTrue(RS.is_canonical_sid("STORY-201"))
        for bad in ("../outside", "/abs", "STORY-21", "story-201",
                    "STORY-2011", "", None, 7):
            self.assertFalse(RS.is_canonical_sid(bad), bad)

    # ---- transitions table repo scope (B3)
    def test_transitions_table_preserves_repo_scope(self):
        events = self.events + [{
            "event": "transition", "event_id": "transition-STORY-201-9",
            "story_id": "STORY-201", "repo": "backend",
            "from_state": "CODING", "to_state": "BUILD_GREEN",
            "requested_by": "orchestrator", "checks_run": [],
            "check_results": {}, "approved_by": None,
            "approved_by_reason": "no gate required",
            "recorded_at": "2026-09-01T07:00:00Z", "provenance": "live"}]
        text = RS.render(events, "STORY-201", self.header, self.stories)
        self.assertIn("| Recorded at (UTC) | From | To | Repo | Requested "
                      "by | Checks | Approved by |", text)
        self.assertIn("|---|---|---|---|---|---|---|", text)
        self.assertIn("| backend | orchestrator |", text)
        # story-level rows (repo null) render the em-dash
        self.assertIn("**CODING** | — | orchestrator", text)


    # ---- gate decision + respondent columns (R15-P3)
    def _gate(self, **over):
        g = {"event": "human_gate", "event_id": "human_gate-STORY-201-1",
             "story_id": "STORY-201", "gate": "merge",
             "question": "Merge approved diff?",
             "asked_at": "2026-09-02T00:00:00Z",
             "answered_at": "2026-09-02T00:30:00Z",
             "decision": "approved", "answered_by": "approver-1",
             "recorded_at": "2026-09-02T00:00:00Z", "provenance": "live"}
        return {**g, **over}

    def test_gate_decision_and_respondent_columns(self):
        text = self.render("STORY-201")
        self.assertIn("| Gate | Question | Asked | Answered | Decision "
                      "| Answered by |", text)
        self.assertIn("| 2026-09-02T00:30:00Z | approved | approver-1 |",
                      text)

    def test_approved_and_rejected_gates_render_distinguishably(self):
        events = self.events
        ok = RS.render(events, "STORY-201", self.header, self.stories)
        bad_events = [self._gate(decision="rejected",
                                 answered_by="approver-2")
                      if e.get("event_id") == "human_gate-STORY-201-1"
                      else e for e in events]
        bad = RS.render(bad_events, "STORY-201", self.header, self.stories)
        self.assertNotEqual(ok, bad)
        self.assertIn("| rejected | approver-2 |", bad)
        self.assertNotIn("rejected", ok)

    def test_pending_gate_row_shows_null_reasons(self):
        pending = self._gate(event_id="human_gate-STORY-201-5",
                             answered_at=None,
                             answered_at_reason="awaiting approver",
                             decision=None, decision_reason="unanswered",
                             answered_by=None,
                             answered_by_reason="unanswered")
        text = RS.render([pending], "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines() if l.startswith("| merge |"))
        self.assertIn("PENDING", row)
        self.assertIn("— (unanswered) | — (unanswered) |", row)

    # ---- R24-Y2: table-cell escaping (mirrors render_provenance._cell)
    def test_gate_question_pipe_is_escaped_and_columns_hold(self):
        """A question containing `|` must not add a Markdown column —
        the approval fields keep their intended positions."""
        g = self._gate(event_id="human_gate-STORY-201-9",
                       question="Approve `producer | consumer`?")
        text = RS.render([g], "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines() if l.startswith("| merge |"))
        self.assertIn("Approve `producer \\| consumer`?", row)
        # exactly the 6 declared columns: 7 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 7)
        self.assertTrue(row.endswith("| approved | approver-1 |"))

    def test_gate_question_newline_stays_in_cell(self):
        g = self._gate(event_id="human_gate-STORY-201-10",
                       question="Merge now\nor later?")
        text = RS.render([g], "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines() if l.startswith("| merge |"))
        self.assertIn("Merge now or later?", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 7)

    def test_root_cause_pipe_and_newline_stay_in_column(self):
        """A root-cause explanation with pipes/newlines must stay in its
        own column — the loop row keeps 5 columns."""
        lp = {"event": "loop", "event_id": "loop-STORY-201-9",
              "story_id": "STORY-201", "stage": "plan_review", "loop_n": 1,
              "class": "reasoning",
              "root_cause_one_line": "plan split a|b across\nservices",
              "recorded_at": "2026-09-01T02:10:00Z", "provenance": "live"}
        text = RS.render([lp], "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| 2026-09-01 | plan_review |"))
        self.assertIn("plan split a\\|b across services", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 6)
        self.assertTrue(row.endswith("across services |"))

    def test_unaffected_fixture_cells_render_unchanged(self):
        """Cell text without pipes/newlines renders byte-identically —
        the fixture question and root cause keep their plain form."""
        text = self.render("STORY-201")
        self.assertIn("| merge | Merge approved diff? |", text)
        self.assertIn("| reasoning | plan omitted a migration step |", text)


    # ---- round 25 Z3: nullable stage-cell escaping
    def _stage(self, **overrides) -> dict:
        st = {"event": "stage", "event_id": "stage-STORY-201-20",
              "story_id": "STORY-201", "stage": "full_suite",
              "started_at": "2026-09-01T03:00:00Z",
              "ended_at": "2026-09-01T03:05:00Z",
              "commit_sha": "e" * 40,
              "tests_passed": None,
              "tests_passed_reason": "not measured | executor stopped",
              "tests_failed": 0, "tests_skipped": 0, "tests_excluded": 0,
              "evidence_path": "stories/STORY-201/evidence/full-suite.txt",
              "recorded_at": "2026-09-01T03:05:00Z", "provenance": "live"}
        st.update(overrides)
        return st

    def test_stage_reason_pipe_stays_in_column(self):
        """A tests_passed_reason containing `|` must not add a column to
        the stage report row."""
        text = RS.render([self._stage()], "STORY-201", self.header,
                         self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| full_suite |"))
        self.assertIn("not measured \\| executor stopped", row)
        # exactly the 7 declared columns: 8 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_stage_reason_newline_stays_in_cell(self):
        text = RS.render(
            [self._stage(tests_failed=None,
                         tests_failed_reason="flaky\nrunner crashed")],
            "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| full_suite |"))
        self.assertIn("flaky runner crashed", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_plain_stage_cells_render_unchanged(self):
        """The fixture's plain stage row keeps its exact form."""
        text = self.render("STORY-201")
        self.assertIn("| full_suite | 42 | 0 | 0 | 0 |", text)
        self.assertNotIn("\\|", next(l for l in text.splitlines()
                                     if l.startswith("| full_suite |")))

    # ---- round 29 AD4: nullable commit/evidence cells keep reasons
    def test_stage_commit_evidence_reasons_rendered_escaped(self):
        """A null commit_sha/evidence_path renders its recorded
        <field>_reason — not a bare `— | —` that loses it — with the
        untrusted text escaped so the row keeps its columns."""
        text = RS.render(
            [self._stage(commit_sha=None,
                         commit_sha_reason="build failed | no commit\n"
                                           "runner stopped",
                         evidence_path=None,
                         evidence_path_reason="no artifact produced")],
            "STORY-201", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| full_suite |"))
        self.assertIn("— (build failed \\| no commit runner stopped)", row)
        self.assertIn("— (no artifact produced)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_stage_commit_evidence_present_values_unchanged(self):
        """Present commit_sha/evidence_path render exactly as before."""
        text = RS.render([self._stage()], "STORY-201", self.header,
                         self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| full_suite |"))
        self.assertIn("| " + "e" * 40 + " |", row)
        self.assertIn("| stories/STORY-201/evidence/full-suite.txt |", row)

    # ---- round 26 AA3: formatted nullable cells in transitions,
    #      findings, and pending human-gate rows
    def _trans(self, **overrides) -> dict:
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

    def test_transition_approval_reason_pipe_and_newline_stay_in_cell(self):
        """approved_by_reason with `|`/newline must not add a column to
        the transitions table or split the row."""
        t = self._trans(approved_by_reason="gate a|b\npending")
        text = RS.render([t], "STORY-216", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| 2026-09-01T07:00:00Z |"))
        self.assertIn("— (gate a\\|b pending)", row)
        # exactly the 7 declared columns: 8 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 8)
        self.assertTrue(row.endswith("| — (gate a\\|b pending) |"))

    def _finding(self, **overrides) -> dict:
        f = {"event": "finding", "event_id": "finding-STORY-216-1",
             "story_id": "STORY-216", "finding_id": "F-216-1",
             "source": "internal", "reviewed_sha": "b" * 40,
             "introduced_sha": "b" * 40, "class": "reasoning",
             "severity": None, "severity_reason": "triage pending",
             "disposition": "deferred", "logic_touched": False,
             "overlap_id": None, "overlap_id_reason": "independent",
             "fixer": None, "fixer_reason": "n/a",
             "fix_sha": None, "fix_sha_reason": "deferred to next story",
             "validation": None, "validation_reason": "n/a",
             "evidence_path": "stories/STORY-216/review.md",
             "recorded_at": "2026-09-01T08:00:00Z", "provenance": "live"}
        f.update(overrides)
        return f

    def test_finding_reason_cells_pipe_and_newline_stay_in_column(self):
        """severity_reason/fix_sha_reason with `|`/newline must not
        corrupt the findings row (8 declared columns hold)."""
        f = self._finding(severity_reason="tri|aged\nlater",
                          fix_sha_reason="not|fixed\nyet")
        text = RS.render([f], "STORY-216", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| F-216-1 |"))
        self.assertIn("— (tri\\|aged later)", row)
        self.assertIn("— (not\\|fixed yet)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 9)

    def test_pending_gate_decision_cells_pipe_and_newline_stay_in_column(
            self):
        """A pending gate's decision_reason/answered_by_reason with
        `|`/newline must not corrupt the human-gates row."""
        g = {"event": "human_gate", "event_id": "human_gate-STORY-216-1",
             "story_id": "STORY-216", "gate": "merge",
             "question": "Merge?", "asked_at": "2026-09-02T00:00:00Z",
             "answered_at": None, "answered_at_reason": "waiting",
             "decision": None, "decision_reason": "un|answered\nx",
             "answered_by": None, "answered_by_reason": "u|nanswered",
             "recorded_at": "2026-09-02T00:00:00Z", "provenance": "live"}
        text = RS.render([g], "STORY-216", self.header, self.stories)
        row = next(l for l in text.splitlines()
                   if l.startswith("| merge |"))
        self.assertIn("— (un\\|answered x)", row)
        self.assertIn("— (u\\|nanswered)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 7)

    def test_plain_nullable_cells_render_unchanged(self):
        """Plain approval/finding reasons keep their exact prior form."""
        text = RS.render([self._trans(), self._finding()], "STORY-216",
                         self.header, self.stories)
        trow = next(l for l in text.splitlines()
                    if l.startswith("| 2026-09-01T07:00:00Z |"))
        self.assertIn("— (no gate required)", trow)
        self.assertNotIn("\\|", trow)
        self.assertEqual(trow.replace("\\|", "").count("|"), 8)
        frow = next(l for l in text.splitlines()
                    if l.startswith("| F-216-1 |"))
        self.assertIn("— (triage pending)", frow)
        self.assertIn("— (deferred to next story)", frow)
        self.assertNotIn("\\|", frow)
        self.assertEqual(frow.replace("\\|", "").count("|"), 9)

    # ---- round 26 AA4: --check treats a MISSING artifact as stale
    def test_check_missing_artifact_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = build_ledger(root)
            self.assertEqual(
                RS.main(["STORY-201", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            p = root / "stories" / "STORY-201" / "state.md"
            self.assertTrue(p.exists())
            p.unlink()  # a deleted artifact is stale, never fresh
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = RS.main(["STORY-201", "--check", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("(missing)", out.getvalue())
            # re-render clears it
            self.assertEqual(
                RS.main(["STORY-201", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            self.assertEqual(
                RS.main(["STORY-201", "--check", "--root", str(root),
                         "--ledger", str(ledger)]), 0)

    def test_check_missing_artifact_never_rendered_is_stale(self):
        """A ledger story whose state.md was NEVER rendered also reports
        stale (rc 1), not silently clean."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = build_ledger(root)
            self.assertEqual(
                RS.main(["STORY-201", "--check", "--root", str(root),
                         "--ledger", str(ledger)]), 1)


class SymlinkConfinementTests(unittest.TestCase):
    """Round 28 AC1: the canonical-id check alone permitted
    `stories/<sid>` as a SYMLINK outward — the renderer then read an
    unrelated narrative.md and OVERWRITES an unrelated state.md. The
    resolved story dir / output file / narrative sidecar must all stay
    under the resolved stories root BEFORE any render; refusal is named
    and writes nothing."""

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
            (outside / "narrative.md").write_text(
                "unrelated prose that must never be read\n",
                encoding="utf-8")
            ledger = self._minimal_ledger(root)
            stories = root / "stories"
            stories.mkdir()
            (stories / "STORY-230").symlink_to(outside)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RS.main(["STORY-230", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertIn("outside the stories directory", err.getvalue())
            # nothing written outside; the unrelated narrative was not read
            self.assertFalse((outside / "state.md").exists())
            self.assertEqual(
                (outside / "narrative.md").read_text(encoding="utf-8"),
                "unrelated prose that must never be read\n")
            # and no state.md inside the stories tree either
            self.assertFalse((stories / "STORY-230" / "state.md").exists())

    def test_symlinked_story_dir_outward_refused_with_all(self):
        """--all applies the same confinement: a symlinked escape refuses
        the whole run BEFORE any sibling is written."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            ledger = self._minimal_ledger(root)
            L.append_event(ledger, {
                "event": "transition",
                "event_id": "transition-STORY-231-1",
                "story_id": "STORY-231", "repo": None,
                "repo_reason": "story-level",
                "from_state": "BACKLOG", "to_state": "CODING",
                "requested_by": "orchestrator", "checks_run": [],
                "check_results": {}, "approved_by": None,
                "approved_by_reason": "no gate required",
                "recorded_at": "2026-09-01T00:00:00Z",
                "provenance": "live"})
            stories = root / "stories"
            stories.mkdir()
            (stories / "STORY-230").symlink_to(outside)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RS.main(["--all", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertFalse((outside / "state.md").exists())
            # the valid sibling was NOT written either — refuse before any
            self.assertFalse((stories / "STORY-231" / "state.md").exists())

    def test_symlinked_narrative_sidecar_outward_refused(self):
        """A confined story dir whose narrative.md is a SYMLINK outward
        still refuses — the sidecar is read only after validation."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            (outside / "narrative.md").write_text("secret prose\n",
                                                  encoding="utf-8")
            ledger = self._minimal_ledger(root)
            sdir = root / "stories" / "STORY-230"
            sdir.mkdir(parents=True)
            (sdir / "narrative.md").symlink_to(outside / "narrative.md")
            err = io.StringIO()
            out = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    contextlib.redirect_stdout(out):
                rc = RS.main(["STORY-230", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertNotIn("secret prose", out.getvalue())
            self.assertFalse((sdir / "state.md").exists())
            self.assertEqual(
                (outside / "narrative.md").read_text(encoding="utf-8"),
                "secret prose\n")

    def test_symlinked_state_file_outward_refused(self):
        """An existing state.md that is a SYMLINK outward refuses too —
        the write would land outside the stories tree."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            outside = Path(td) / "outside"
            outside.mkdir()
            victim = outside / "state.md"
            victim.write_text("unrelated state\n", encoding="utf-8")
            ledger = self._minimal_ledger(root)
            sdir = root / "stories" / "STORY-230"
            sdir.mkdir(parents=True)
            (sdir / "state.md").symlink_to(victim)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = RS.main(["STORY-230", "--root", str(root),
                              "--ledger", str(ledger)])
            self.assertEqual(rc, 1)
            self.assertIn("REFUSED: story id STORY-230", err.getvalue())
            self.assertEqual(victim.read_text(encoding="utf-8"),
                             "unrelated state\n")

    def test_symlink_inside_stories_ok(self):
        """A story dir symlinked to another location UNDER the stories
        root stays confined and renders normally."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            ledger = self._minimal_ledger(root)
            stories = root / "stories"
            real = stories / "real-dir"
            real.mkdir(parents=True)
            (stories / "STORY-230").symlink_to(real)
            self.assertEqual(
                RS.main(["STORY-230", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            self.assertTrue((real / "state.md").exists())
            self.assertTrue((real / "state.md").read_text(
                encoding="utf-8").startswith("<!-- GENERATED from"))

    def test_normal_dir_unchanged(self):
        """A plain directory renders exactly as before the fix."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "home"
            root.mkdir()
            ledger = self._minimal_ledger(root)
            self.assertEqual(
                RS.main(["STORY-230", "--root", str(root),
                         "--ledger", str(ledger)]), 0)
            p = root / "stories" / "STORY-230" / "state.md"
            self.assertTrue(p.exists())
            self.assertTrue(p.read_text(encoding="utf-8")
                            .startswith("<!-- GENERATED from"))


class CellCRTests(unittest.TestCase):
    """Round 28 AC5: `_cell` must flatten CRLF and lone CR as well as
    LF — `not required\r\nfor this state` used to keep `\r`, splitting
    the table row and violating the LF-only byte contract."""

    def test_cell_direct(self):
        self.assertEqual(RS._cell("a\r\nb"), "a b")
        self.assertEqual(RS._cell("a\rb"), "a b")
        self.assertEqual(RS._cell("a\nb"), "a b")
        self.assertEqual(RS._cell("a|b"), "a\\|b")
        self.assertEqual(RS._cell("plain"), "plain")

    def _trans(self, **overrides) -> dict:
        t = {"event": "transition", "event_id": "transition-STORY-232-1",
             "story_id": "STORY-232", "repo": None,
             "repo_reason": "single-repo",
             "from_state": "CODING", "to_state": "BUILD_GREEN",
             "requested_by": "orchestrator", "checks_run": [],
             "check_results": {}, "approved_by": None,
             "approved_by_reason": "no gate required",
             "recorded_at": "2026-09-01T07:00:00Z", "provenance": "live"}
        t.update(overrides)
        return t

    def _row(self, reason: str) -> str:
        text = RS.render([self._trans(approved_by_reason=reason)],
                         "STORY-232", "HEADER", Path("/nonexistent"))
        rows = [l for l in text.splitlines()
                if l.startswith("| 2026-09-01T07:00:00Z |")]
        self.assertEqual(len(rows), 1, text)  # exactly ONE row — no split
        return rows[0]

    def test_crlf_reason_stays_one_line(self):
        row = self._row("not required\r\nfor this state")
        self.assertNotIn("\r", row)
        self.assertIn("— (not required for this state)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_lone_cr_reason_stays_one_line(self):
        row = self._row("not required\rfor this state")
        self.assertNotIn("\r", row)
        self.assertIn("— (not required for this state)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_plain_reason_unchanged(self):
        row = self._row("no gate required")
        self.assertIn("— (no gate required)", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)


class TransitionFreeTextCellEscapeTests(unittest.TestCase):
    """Round 32 AG4: `requested_by` and the `checks_run` entries are
    free-form ledger text that used to render UNESCAPED in the
    transitions table — `ops|review` added a column and a newline in a
    check name split the row."""

    header = RS.generated_header("factory/ledger/events.jsonl")

    def _trans(self, **overrides) -> dict:
        t = {"event": "transition", "event_id": "transition-STORY-217-1",
             "story_id": "STORY-217", "repo": None,
             "repo_reason": "single-repo",
             "from_state": "CODING", "to_state": "BUILD_GREEN",
             "requested_by": "orchestrator", "checks_run": [],
             "check_results": {}, "approved_by": None,
             "approved_by_reason": "no gate required",
             "recorded_at": "2026-09-01T07:00:00Z", "provenance": "live"}
        t.update(overrides)
        return t

    def _row(self, **overrides) -> str:
        with tempfile.TemporaryDirectory() as td:
            text = RS.render([self._trans(**overrides)], "STORY-217",
                             self.header, Path(td))
        return next(l for l in text.splitlines()
                    if l.startswith("| 2026-09-01T07:00:00Z |"))

    def test_requested_by_pipe_escaped_columns_intact(self):
        row = self._row(requested_by="ops|review")
        self.assertIn("ops\\|review", row)
        # exactly the 7 declared columns: 8 unescaped pipe delimiters
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_requested_by_newline_flattened(self):
        row = self._row(requested_by="ops\nreview")
        self.assertIn("ops review", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_requested_by_crlf_and_cr_flattened(self):
        for who in ("ops\r\nreview", "ops\rreview"):
            row = self._row(requested_by=who)
            self.assertNotIn("\r", row)
            self.assertIn("ops review", row)
            self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_checks_run_entries_escaped(self):
        row = self._row(checks_run=["order", "gate a|b", "claim\nc"])
        self.assertIn("order, gate a\\|b, claim c", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)

    def test_plain_transition_row_unchanged(self):
        row = self._row(requested_by="orchestrator",
                        checks_run=["order", "claim"])
        self.assertIn("| orchestrator | order, claim |", row)
        self.assertNotIn("\\|", row)
        self.assertEqual(row.replace("\\|", "").count("|"), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
