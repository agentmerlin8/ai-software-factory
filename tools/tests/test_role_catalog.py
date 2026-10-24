#!/usr/bin/env python3
"""Tests for tools/role_catalog.py."""
from __future__ import annotations

import unittest

from tools import role_catalog as RC


class TestCatalog(unittest.TestCase):
    def test_twelve_canonical_roles(self):
        self.assertEqual(len(RC.CANONICAL_ROLES), 12)
        for role in ("context_acquisition", "decomposer",
                     "test_plan_generator", "planner", "plan_reviewer",
                     "plan_reviewer_a", "plan_reviewer_b", "coder", "fixer",
                     "code_reviewer", "behavior_reviewer",
                     "contract_reviewer"):
            self.assertIn(role, RC.CANONICAL_ROLES)

    def test_matrix_complete_and_valid(self):
        for role, (purpose, tiers) in RC.CANONICAL_ROLES.items():
            with self.subTest(role=role):
                self.assertIsInstance(purpose, str)
                self.assertTrue(purpose.strip())
                self.assertEqual(len(tiers), len(RC.DIFFICULTIES))
                for t in tiers:
                    self.assertIn(t, RC.TIERS)

    def test_every_role_resolves_for_every_difficulty(self):
        for role in RC.CANONICAL_ROLES:
            for d in RC.DIFFICULTIES:
                with self.subTest(role=role, difficulty=d):
                    self.assertIn(RC.resolve_tier(role, d), RC.TIERS)

    def test_expected_tiers_spot_check(self):
        self.assertEqual(RC.resolve_tier("planner", "L"), "T3")
        self.assertEqual(RC.resolve_tier("coder", "S"), "T1")
        self.assertEqual(RC.resolve_tier("behavior_reviewer", "M"), "T2")
        self.assertEqual(RC.resolve_tier("context-acquisition", "L"), "T1")


class TestAliases(unittest.TestCase):
    def test_every_alias_resolves_to_canonical(self):
        for alias, canonical in RC.ALIASES.items():
            with self.subTest(alias=alias):
                self.assertIn(canonical, RC.CANONICAL_ROLES)
                self.assertEqual(RC.canonical_role(alias), canonical)

    def test_hyphen_and_snake_variants(self):
        pairs = [
            ("implementation-planner", "planner"),
            ("implementation_planner", "planner"),
            ("impl-planner", "planner"),
            ("test-plan-generator", "test_plan_generator"),
            ("test_plan_generator", "test_plan_generator"),
            ("story-decomposer", "decomposer"),
            ("behavior-reviewer", "behavior_reviewer"),
            ("behavior_reviewer", "behavior_reviewer"),
            ("contract-reviewer", "contract_reviewer"),
            ("code-reviewer", "code_reviewer"),
            ("plan-reviewer-a", "plan_reviewer_a"),
            ("context", "context_acquisition"),
        ]
        for alias, canonical in pairs:
            with self.subTest(alias=alias):
                self.assertEqual(RC.canonical_role(alias), canonical)

    def test_case_insensitive(self):
        self.assertEqual(RC.canonical_role("  Coder "), "coder")

    def test_unknown_role_raises_listing_known(self):
        with self.assertRaises(ValueError) as ctx:
            RC.canonical_role("sandwich-artist")
        msg = str(ctx.exception)
        self.assertIn("unknown role", msg)
        for role in RC.CANONICAL_ROLES:
            self.assertIn(role, msg)

    def test_resolve_tier_bad_difficulty(self):
        with self.assertRaises(ValueError):
            RC.resolve_tier("coder", "XL")

    def test_resolve_tier_via_alias(self):
        self.assertEqual(RC.resolve_tier("impl-planner", "M"), "T2")


if __name__ == "__main__":
    unittest.main()
