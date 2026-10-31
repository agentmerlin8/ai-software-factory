#!/usr/bin/env python3
"""tools/tests/test_docs_consistency.py — S3.5b-T5 docs/template retirement.

Keeps the shipped documentation honest about the review-gate cutover: the
build-plan carries §S3.5b and the review-record gate, the gate catalog names
it, the architecture names the lane/record/manifest vocabulary, the runbook
template carries the exact commands, story-state no longer claims a generic
code-review checkbox, and no shipped template instructs anyone to request a
Copilot/bot review.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SHIP_TEMPLATES = (
    "README.md",
    "onboarding/README.md",
    *sorted(
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "onboarding" / "templates").glob("*.md")
    ),
)

REQUEST_A_BOT_REVIEW = re.compile(
    r"(?i)(request|trigger|ask for)[^.\n]{0,40}copilot[^.\n]{0,40}review")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class TestBuildPlan(unittest.TestCase):
    def test_s35b_section_present(self):
        self.assertIn("### S3.5b", _read("kit-v0.1/build-plan.md"))

    def test_review_record_named(self):
        self.assertIn("review-record", _read("kit-v0.1/build-plan.md"))


class TestGateCatalogDoc(unittest.TestCase):
    def test_review_record_slug(self):
        from tools import gate_catalog as GC
        self.assertIn("review-record", [g["slug"] for g in GC.GATES])

    def test_architecture_gate_row(self):
        self.assertIn("review-record", _read("kit-v0.1/architecture.md"))


class TestArchitecture(unittest.TestCase):
    def test_lane_vocabulary(self):
        text = _read("kit-v0.1/architecture.md")
        for token in ("review-lane", "review-record", "package.manifest"):
            with self.subTest(token=token):
                self.assertIn(token, text)


class TestRunbookTemplate(unittest.TestCase):
    def test_lane_commands_named(self):
        text = _read("onboarding/templates/runbook.md")
        for token in ("record.py check", "ci_gate.py"):
            with self.subTest(token=token):
                self.assertIn(token, text)


class TestStoryStateTemplate(unittest.TestCase):
    def test_review_lane_record_line(self):
        self.assertIn("Review lane record",
                      _read("onboarding/templates/story-state.md"))

    def test_no_generic_code_review_line(self):
        self.assertNotIn("Code review:",
                         _read("onboarding/templates/story-state.md"))


class TestNoRequestABotReview(unittest.TestCase):
    def test_shipped_templates(self):
        for rel in SHIP_TEMPLATES:
            with self.subTest(template=rel):
                self.assertIsNone(
                    REQUEST_A_BOT_REVIEW.search(_read(rel)),
                    f"{rel} still instructs requesting a Copilot/bot review")


if __name__ == "__main__":
    unittest.main()
