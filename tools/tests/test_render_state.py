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
        # transition table rows
        self.assertIn("| BACKLOG | **CODING** | orchestrator |", text)
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
        d = self.stories / "T-A"
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.md").write_text("# hand-written, no header\n",
                                    encoding="utf-8")
        self.assertEqual(
            RS.main(["T-A", "--check", "--root", str(self.root)]), 0)

    def test_all_discovery_uses_verbatim_story_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            ledger = root / "factory" / "ledger" / "events.jsonl"
            ledger.write_bytes(self.ledger.read_bytes())
            rc = RS.main(["--all", "--root", str(root)])
            self.assertEqual(rc, 0)
            for sid in ("STORY-201", "STORY-202", "T-A"):
                self.assertTrue(
                    (root / "stories" / sid / "state.md").exists(),
                    f"missing render for {sid}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
