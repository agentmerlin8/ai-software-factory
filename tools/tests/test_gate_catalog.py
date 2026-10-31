#!/usr/bin/env python3
"""Tests for tools/gate_catalog.py."""
from __future__ import annotations

import unittest

from tools import gate_catalog as GC

EXPECTED_SLUGS = ("digest-gate", "plan-gate", "build-gate", "gate-selftest",
                  "review-record")


class TestGateCatalog(unittest.TestCase):
    def test_exactly_five_slugs(self):
        self.assertEqual(tuple(g["slug"] for g in GC.GATES), EXPECTED_SLUGS)

    def test_fields_non_empty(self):
        for gate in GC.GATES:
            for field in ("slug", "purpose", "script", "invocation", "when",
                          "exit"):
                with self.subTest(gate=gate["slug"], field=field):
                    self.assertIsInstance(gate[field], str)
                    self.assertTrue(gate[field].strip())

    def test_invocations_name_their_scripts(self):
        expected = {
            "digest-gate": "check_digest.py",
            "plan-gate": "pre_review_gate.py",
            "build-gate": "pre_review_gate.py",
            "gate-selftest": "test_gate.py",
            "review-record": "review/ci_gate.py",
        }
        for gate in GC.GATES:
            with self.subTest(gate=gate["slug"]):
                self.assertIn(expected[gate["slug"]], gate["invocation"])

    def test_plan_and_build_share_pre_review_gate_script(self):
        by_slug = {g["slug"]: g for g in GC.GATES}
        self.assertIn("pre_review_gate.py", by_slug["plan-gate"]["script"])
        self.assertIn("pre_review_gate.py", by_slug["build-gate"]["script"])
        self.assertIn("plan mode", by_slug["plan-gate"]["script"])
        self.assertIn("build mode", by_slug["build-gate"]["script"])

    def test_modes_appear_in_invocations(self):
        by_slug = {g["slug"]: g for g in GC.GATES}
        self.assertIn("pre_review_gate.py plan", by_slug["plan-gate"]["invocation"])
        self.assertIn("pre_review_gate.py build",
                      by_slug["build-gate"]["invocation"])

    def test_no_numeric_gate_ids(self):
        import re
        for gate in GC.GATES:
            text = " ".join(gate.values())
            self.assertIsNone(re.search(r"\bG\d+\b", text), gate["slug"])

    def test_installed_form_paths(self):
        for gate in GC.GATES:
            self.assertIn("factory/", gate["invocation"], gate["slug"])


if __name__ == "__main__":
    unittest.main()
