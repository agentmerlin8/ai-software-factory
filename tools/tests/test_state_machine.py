#!/usr/bin/env python3
"""Tests for tools/state_machine.py."""
from __future__ import annotations

import unittest

from tools import state_machine as SM


class TestStateSet(unittest.TestCase):
    def test_states_19_plus_blocked(self):
        self.assertEqual(len(SM.STATES), 20)
        self.assertEqual(len(SM.LINEAR), 19)
        self.assertNotIn("BLOCKED", SM.LINEAR)
        self.assertEqual(SM.STATES[0], "BACKLOG")
        self.assertEqual(SM.LINEAR[-1], "CLOSED")
        self.assertEqual(SM.STATES[-1], "BLOCKED")

    def test_linear_order(self):
        self.assertEqual(SM.LINEAR, [
            "BACKLOG", "GRILLED", "TEST_PLAN", "SPEC_FROZEN", "PLANNED",
            "GATE_PASSED", "PLAN_APPROVED", "STORYBOOK_APPROVAL", "CODING",
            "BUILD_GREEN", "DRAFT_OPENED", "REVIEWING", "FIXING",
            "FULL_TESTS_GREEN", "READY_FOR_REVIEW", "MERGE_ELIGIBLE",
            "MERGED", "LIVE_PROVEN", "CLOSED"])

    def test_evidence_keys_cover_exactly_linear_targets_plus_blocked(self):
        expected = set(SM.LINEAR[1:]) | {"BLOCKED"}  # BACKLOG has no inbound
        self.assertEqual(set(SM.EVIDENCE_KEYS), expected)

    def test_check_summary_covers_every_target(self):
        self.assertEqual(set(SM.CHECK_SUMMARY), set(SM.EVIDENCE_KEYS))
        for state, summary in SM.CHECK_SUMMARY.items():
            self.assertIsInstance(summary, str)
            self.assertTrue(summary.strip(), state)

    def test_evidence_keys_non_empty_strings(self):
        for state, keys in SM.EVIDENCE_KEYS.items():
            for k in keys:
                self.assertIsInstance(k, str, state)
                self.assertTrue(k.strip(), (state, k))

    def test_evidence_formats_document_required_keys(self):
        # every documented format key must actually appear somewhere
        all_keys = {k for keys in SM.EVIDENCE_KEYS.values() for k in keys}
        for fk in SM.EVIDENCE_FORMATS:
            self.assertIn(fk, all_keys | {"head_sha"})

    def test_claim_gate(self):
        self.assertEqual(SM.CLAIM_GATE, "CODING")


class TestOrder(unittest.TestCase):
    def test_next_in_sequence(self):
        self.assertTrue(SM.order_ok("BACKLOG", "GRILLED"))
        self.assertTrue(SM.order_ok("PLAN_APPROVED", "STORYBOOK_APPROVAL"))
        self.assertTrue(SM.order_ok("MERGED", "LIVE_PROVEN"))
        self.assertEqual(SM.next_state("BACKLOG"), "GRILLED")
        self.assertIsNone(SM.next_state("CLOSED"))
        self.assertIsNone(SM.next_state("BLOCKED"))

    def test_skip_refused(self):
        self.assertFalse(SM.order_ok("CODING", "DRAFT_OPENED"))
        self.assertFalse(SM.order_ok("BACKLOG", "CODING"))
        self.assertFalse(SM.order_ok("GRILLED", "GRILLED"))

    def test_fixing_reentry_from_three_post_review_states(self):
        for cur in ("FULL_TESTS_GREEN", "READY_FOR_REVIEW", "MERGE_ELIGIBLE"):
            with self.subTest(cur=cur):
                self.assertTrue(SM.order_ok(cur, "FIXING"))
        self.assertEqual(SM.FIXING_REENTRY_FROM,
                         {"FULL_TESTS_GREEN", "READY_FOR_REVIEW",
                          "MERGE_ELIGIBLE"})
        # never from pre-review states
        self.assertFalse(SM.order_ok("BUILD_GREEN", "FIXING"))

    def test_ui_skip_only_with_declaration(self):
        self.assertFalse(SM.order_ok("PLAN_APPROVED", "CODING"))
        self.assertTrue(SM.order_ok("PLAN_APPROVED", "CODING",
                                    non_ui_declared=True))
        self.assertEqual(SM.UI_SKIP, ("PLAN_APPROVED", "CODING"))

    def test_blocked_not_linear_order(self):
        self.assertFalse(SM.order_ok("CODING", "BLOCKED"))


class TestBlockedResume(unittest.TestCase):
    def t(self, to, frm):
        return {"to_state": to, "from_state": frm}

    def test_blocked_origin_and_resume(self):
        trans = [self.t("GRILLED", "BACKLOG"), self.t("BLOCKED", "GRILLED")]
        self.assertEqual(SM.blocked_origin(trans), "GRILLED")
        self.assertTrue(SM.resume_ok("GRILLED", "GRILLED"))
        self.assertFalse(SM.resume_ok("GRILLED", "CODING"))

    def test_resumed_block_clears_origin(self):
        trans = [self.t("BLOCKED", "GRILLED"), self.t("GRILLED", "BLOCKED")]
        self.assertIsNone(SM.blocked_origin(trans))
        self.assertTrue(SM.resume_ok(None, "CODING"))

    def test_no_block(self):
        self.assertIsNone(SM.blocked_origin([self.t("GRILLED", "BACKLOG")]))
        self.assertIsNone(SM.blocked_origin([]))

    def test_latest_block_wins(self):
        trans = [self.t("BLOCKED", "GRILLED"), self.t("GRILLED", "BLOCKED"),
                 self.t("BLOCKED", "TEST_PLAN")]
        self.assertEqual(SM.blocked_origin(trans), "TEST_PLAN")

    # -- repo-tagged sub-events are per-repo moves, not story-level ones
    def st(self, to, frm, repo):
        return {"to_state": to, "from_state": frm, "repo": repo}

    def test_sub_event_after_block_does_not_resume(self):
        # regression: BLOCKED(CODING) then BUILD_GREEN{repo app} used to
        # clear the origin, letting resume_ok accept ANY target state.
        trans = [self.t("CODING", "PLAN_APPROVED"),
                 self.t("BLOCKED", "CODING"),
                 self.st("BUILD_GREEN", "CODING", "app")]
        self.assertEqual(SM.blocked_origin(trans), "CODING")
        self.assertFalse(SM.resume_ok("CODING", "MERGED"))
        self.assertTrue(SM.resume_ok("CODING", "CODING"))

    def test_story_transition_after_block_still_resumes(self):
        trans = [self.t("BLOCKED", "CODING"),
                 self.st("BUILD_GREEN", "CODING", "app"),
                 self.t("CODING", "BLOCKED")]
        self.assertIsNone(SM.blocked_origin(trans))

    def test_sub_events_before_block_do_not_affect_origin(self):
        trans = [self.st("BUILD_GREEN", "CODING", "app"),
                 self.t("BLOCKED", "CODING")]
        self.assertEqual(SM.blocked_origin(trans), "CODING")

    def test_repo_tagged_block_does_not_set_story_block(self):
        # a sub-event is never a story-level BLOCKED either.
        trans = [self.t("CODING", "PLAN_APPROVED"),
                 self.st("BLOCKED", "CODING", "app")]
        self.assertIsNone(SM.blocked_origin(trans))


if __name__ == "__main__":
    unittest.main()
