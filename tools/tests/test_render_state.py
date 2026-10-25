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


if __name__ == "__main__":
    unittest.main(verbosity=2)
