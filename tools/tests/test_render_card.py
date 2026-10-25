#!/usr/bin/env python3
"""Tests for tools/render_card.py — story-card rendering from the ledger:
markers (identity vs pin), state/owner/next-move/findings/evidence
sections, next-move legality from state_machine helpers (BLOCKED resume,
FIXING reentry, declared non-UI skip), human-gate supersession
(append-only), unknown-story refusal (never pinned), determinism (no
wall-clock), the marker-anchored upsert seam (stub gh_fn — NO network,
NO gh calls), --dry-run payloads, and CLI exit codes.

Fixtures: tools/tests/fixtures/cards/sample_events.json, appended through
the real validated write path (ledger_lib.append_event) in setup.
Fictional STORY-9xx ids only.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tools import ledger_lib as L
from tools import render_card as R
from tools import state_machine as SM

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cards"
STORY = "STORY-901"


def build_ledger(root: Path) -> Path:
    """Seed the sample events through ledger_lib.append_event (the real
    validated write path) into <root>/factory/ledger/events.jsonl."""
    ledger = root / "factory" / "ledger" / "events.jsonl"
    events = json.loads((FIXTURES / "sample_events.json").read_text(
        encoding="utf-8"))
    for e in events:
        L.append_event(ledger, e)
    return ledger


class CardFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "workspace.toml").write_text(
            '[[repos]]\nid = "app"\nrole = "primary"\n'
            'url = "github.test/fixture-org/app"\n', encoding="utf-8")
        cls.ledger = build_ledger(cls.root)
        cls.events = L.load_events(cls.ledger)
        cls.digest = R.ledger_digest(cls.ledger)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def card(self, sid: str = STORY) -> str:
        card, known = R.build_card(sid, self.events, self.root,
                                   self.digest)
        self.assertTrue(known)
        return card

    # ---- markers + sections
    def test_identity_marker_present_pin_absent(self):
        card = self.card()
        self.assertIn(R.CARD_MARKER, card)
        self.assertNotIn(R.PIN_MARKER, card)

    def test_state_owner_and_last_transition(self):
        card = self.card()
        self.assertIn(f"## Factory Card — {STORY}", card)
        self.assertIn("**State:** CODING", card)
        self.assertIn("**Owner:** agent-a", card)
        self.assertIn("**Last transition:** BACKLOG → CODING (by "
                      "orchestrator)", card)

    def test_next_move_uses_installed_cli_form_with_evidence(self):
        card = self.card()
        self.assertIn("advance to **BUILD_GREEN**", card)
        self.assertIn(
            "CLI: `python3 factory/tools/controller.py request "
            f"{STORY} BUILD_GREEN` --evidence commit_sha=… "
            "build_result=… named_tests_result=…", card)

    def test_open_findings_summary(self):
        card = self.card()
        self.assertIn("### Open findings", card)
        self.assertIn("1 finding(s) recorded; 0 closed/fixed; 1 open.",
                      card)
        self.assertIn("- `F-901-1` external / mechanical / major — "
                      "disposition accepted", card)

    def test_evidence_pointers_in_ledger_order(self):
        card = self.card()
        self.assertIn("### Evidence pointers", card)
        self.assertLess(
            card.index("stories/STORY-901/external-round-1.md"),
            card.index("stories/STORY-901/evidence/full-suite.txt"))

    def test_open_human_gate_shown(self):
        self.assertIn("**Pending human gate:** merge — "
                      "“Merge approved diff?”", self.card())

    def test_determinism_no_wall_clock(self):
        a = self.card()
        b = self.card()
        self.assertEqual(a, b)
        self.assertIn(f"`{self.digest}`", a)
        self.assertIn("never hand-edit", a)

    def test_closed_story_has_no_moves(self):
        card = R.render_card_text("STORY-999", [], "CLOSED", None,
                                  self.root / "stories", "digest123456")
        self.assertIn("— none: story CLOSED —", card)


class NextMoveTests(unittest.TestCase):
    """Legal moves come from state_machine helpers — never re-derived."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.stories = Path(cls._td.name) / "stories"
        cls.stories.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def moves(self, state, blocked_from=None, sid="STORY-902"):
        return R.next_moves(sid, state, blocked_from, self.stories)

    def test_blocked_shows_resume_to_origin(self):
        m = self.moves("BLOCKED", "CODING")
        self.assertIn("resolve the blocker, then request the resume "
                      "transition back to **CODING**", m[0])
        self.assertIn("python3 factory/tools/controller.py request "
                      "STORY-902 CODING", m[1])

    def test_fixing_reentry_offered_from_post_review_states(self):
        for s in sorted(SM.FIXING_REENTRY_FROM):
            m = self.moves(s)
            self.assertTrue(any("route back to **FIXING**" in x
                                for x in m), s)
        # not offered pre-review
        m = self.moves("CODING")
        self.assertFalse(any("FIXING" in x for x in m))

    def test_non_ui_skip_only_with_declaration(self):
        (self.stories / "STORY-902").mkdir()
        (self.stories / "STORY-902" / "story.md").write_text(
            "# s\nUI: none\n", encoding="utf-8")
        m = self.moves("PLAN_APPROVED")
        self.assertTrue(any("legal skip" in x for x in m))
        self.assertTrue(any("CODING" in x for x in m))
        # UI story (no declaration) keeps the visual gate route
        m2 = self.moves("PLAN_APPROVED", sid="STORY-903")
        self.assertFalse(any("legal skip" in x for x in m2))
        self.assertIn("advance to **STORYBOOK_APPROVAL**", m2[0])

    def test_next_state_matches_state_machine(self):
        for s in SM.LINEAR[:-1]:
            m = self.moves(s)
            self.assertIn(f"advance to **{SM.next_state(s)}**", m[0], s)


class GateSupersessionTests(unittest.TestCase):
    def test_later_answered_row_supersedes(self):
        ask = {"event": "human_gate", "gate": "merge", "question": "m?",
               "asked_at": "t1", "answered_at": None}
        reply = {**ask, "answered_at": "t2"}
        self.assertEqual(R.pending_gates([ask, reply]), [])

    def test_unanswered_pair_stays_pending(self):
        ask = {"event": "human_gate", "gate": "merge", "question": "m?",
               "asked_at": "t1", "answered_at": None}
        ask2 = {**ask, "asked_at": "t2"}
        self.assertEqual(R.pending_gates([ask, ask2]), [ask, ask2])

    def test_different_gate_still_open(self):
        ask = {"event": "human_gate", "gate": "merge", "question": "m?",
               "asked_at": "t1", "answered_at": None}
        reply = {**ask, "answered_at": "t2"}
        other = {**ask, "gate": "visual", "asked_at": "t3",
                 "answered_at": None}
        self.assertEqual(R.pending_gates([ask, reply, other]), [other])


class OwnerLifecycleTests(unittest.TestCase):
    def test_claim_handover_release(self):
        self.assertEqual(R.owner_line([{"op": "claim", "owner": "a"}]),
                         "a")
        self.assertEqual(R.owner_line([
            {"op": "claim", "owner": "a"},
            {"op": "handover", "from": "a", "to": "b"}]), "b")
        self.assertEqual(R.owner_line([
            {"op": "claim", "owner": "a"},
            {"op": "release", "owner": "a"}]), "(none recorded)")
        self.assertEqual(R.owner_line([]), "(none recorded)")


class UnknownStoryTests(unittest.TestCase):
    def test_unknown_id_never_known(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "stories").mkdir()
            (root / "stories" / "BACKLOG.md").write_text(
                "- STORY-942 candidate\n", encoding="utf-8")
            (root / "stories" / "STORY-936.md").write_text(
                "spec\n", encoding="utf-8")
            events = [{"event": "transition", "story_id": "STORY-935"}]
            self.assertTrue(R.story_known("STORY-935", events, root))
            self.assertTrue(R.story_known("STORY-942", [], root))
            self.assertTrue(R.story_known("STORY-936", [], root))
            self.assertFalse(R.story_known("STORY-999", [], root))
            card, known = R.build_card("STORY-999", events, root, "d")
            self.assertFalse(known)
            self.assertIn("**Nothing was pinned.**", card)
            self.assertIn("unknown", card)


class UpsertSeamTests(unittest.TestCase):
    """Marker-anchored upsert via stub gh_fn — no network, no gh."""

    def setUp(self):
        self.calls: list[tuple] = []

    def fake_gh(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if len(self.calls) == 1:
            return "42\n"  # one existing pinned comment
        return "ok"

    def fake_gh_empty(self, *args, stdin=None):
        self.calls.append((args, stdin))
        return "" if len(self.calls) == 1 else "ok"

    def test_patch_existing_pin(self):
        R.upsert_comment("o/r", 7, "BODY", gh_fn=self.fake_gh)
        self.assertIn("--paginate", self.calls[0][0])
        self.assertIn(R.PIN_MARKER, self.calls[0][0][-1])  # jq selector
        method_idx = self.calls[1][0].index("--method")
        self.assertEqual(self.calls[1][0][method_idx + 1], "PATCH")
        self.assertTrue(any("issues/comments/42" in a
                            for a in self.calls[1][0]))
        self.assertTrue(json.loads(self.calls[1][1])["body"]
                        .startswith(R.PIN_MARKER))

    def test_post_when_no_pin(self):
        R.upsert_comment("o/r", 7, "BODY", gh_fn=self.fake_gh_empty)
        method_idx = self.calls[1][0].index("--method")
        self.assertEqual(self.calls[1][0][method_idx + 1], "POST")
        self.assertTrue(any(a.endswith("/issues/7/comments")
                            for a in self.calls[1][0]))

    def test_pick_target(self):
        self.assertIsNone(R.pick_target([]))
        self.assertEqual(R.pick_target(["11", "22"]), "22")

    def test_dry_run_calls_no_api(self):
        out = io.StringIO()
        with redirect_stdout(out):
            got = R.upsert_comment("o/r", 7, "BODY",
                                   gh_fn=self.fake_gh, dry_run=True)
        self.assertEqual(got, "dry-run")
        self.assertEqual(self.calls, [])  # NO gh call at all
        printed = out.getvalue()
        self.assertIn("POST repos/o/r/issues/7/comments", printed)
        body = json.loads(printed.splitlines()[1])
        self.assertEqual(body["body"], f"{R.PIN_MARKER}\nBODY")

    def test_plain_post_never_pins(self):
        R.post_comment("o/r", 9, "EXPLAIN", gh_fn=self.fake_gh_empty)
        self.assertNotIn(R.PIN_MARKER, json.loads(self.calls[0][1])["body"])


class MainCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "workspace.toml").write_text(
            '[[repos]]\nid = "app"\nrole = "primary"\n'
            'url = "github.test/fixture-org/app"\n', encoding="utf-8")
        cls.ledger = build_ledger(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = R.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def base(self, *extra):
        return [STORY, "--root", str(self.root), *extra]

    def test_render_only(self):
        rc, out, _ = self.run_main(self.base())
        self.assertEqual(rc, 0)
        self.assertIn(R.CARD_MARKER, out)
        self.assertNotIn(R.PIN_MARKER, out)

    def test_noncanonical_id_rc2(self):
        rc, out, _ = self.run_main(["nope", "--root", str(self.root)])
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED: 'nope' is not a canonical STORY-NNN id",
                      out)

    def test_bad_ledger_rc2(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "events.jsonl"
            bad.write_text("{not json\n", encoding="utf-8")
            rc, out, _ = self.run_main(self.base("--ledger", str(bad)))
            self.assertEqual(rc, 2)
            self.assertIn("REFUSED: ledger rejected (fail closed)", out)

    def test_dry_run_upsert_payload(self):
        rc, out, _ = self.run_main(self.base("--issue", "42", "--dry-run"))
        self.assertEqual(rc, 0)
        self.assertIn("POST repos/fixture-org/app/issues/42/comments", out)
        payload = json.loads(
            out.split("POST repos/", 1)[1].splitlines()[1])
        self.assertTrue(payload["body"].startswith(R.PIN_MARKER))
        self.assertIn(R.CARD_MARKER, payload["body"])
        self.assertIn("dry-run: no write performed", out)

    def test_issue_repo_override_dry_run(self):
        rc, out, _ = self.run_main(
            self.base("--issue", "42", "--repo", "o2/r2", "--dry-run"))
        self.assertEqual(rc, 0)
        self.assertIn("POST repos/o2/r2/issues/42/comments", out)

    def test_missing_repo_config_rc2(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = build_ledger(root)
            rc, out, _ = self.run_main(
                [STORY, "--root", str(root), "--ledger", str(ledger),
                 "--issue", "42", "--dry-run"])
            self.assertEqual(rc, 2)
            self.assertIn("REFUSED: repo not configured", out)


if __name__ == "__main__":
    unittest.main()
