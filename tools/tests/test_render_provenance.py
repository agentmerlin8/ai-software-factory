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

import hashlib
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
        d = self.stories / "T-A"
        d.mkdir(parents=True, exist_ok=True)
        (d / "PROVENANCE.md").write_text("# hand-written, no header\n",
                                         encoding="utf-8")
        self.assertEqual(
            RP.main(["T-A", "--check", "--root", str(self.root)]), 0)

    def test_all_discovery_uses_verbatim_story_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            root.joinpath("factory", "ledger").mkdir(parents=True)
            (root / "factory" / "ledger" / "events.jsonl").write_bytes(
                self.ledger.read_bytes())
            self.assertEqual(RP.main(["--all", "--root", str(root)]), 0)
            for sid in ("STORY-201", "STORY-202", "T-A"):
                self.assertTrue(
                    (root / "stories" / sid / "PROVENANCE.md").exists(),
                    f"missing render for {sid}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
