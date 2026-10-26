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
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from tools import ledger_lib as L
from tools import render_card as R
from tools import state_machine as SM
from tools import sync_board as SB

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
            f"{STORY} BUILD_GREEN --evidence commit_sha=… "
            "--evidence build_result=… --evidence named_tests_result=…`",
            card)
        # one --evidence flag per required key (controller append form)
        line = next(x for x in card.splitlines()
                    if "--evidence commit_sha" in x)
        self.assertEqual(
            line.count("--evidence"),
            len(SM.EVIDENCE_KEYS["BUILD_GREEN"]))
        # no stray multi-value form (single flag followed by 2+ values)
        self.assertNotIn("--evidence commit_sha=… build_result=…", card)

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

    def test_evidence_command_parses_under_controller_parser(self):
        """M2 parity: the documented command (flags inside the code span,
        --evidence repeated per key) parses under the controller's REAL
        argparse parser — argparse failure raises SystemExit, so reaching
        any integer return proves parseability."""
        import shlex
        from contextlib import redirect_stdout, redirect_stderr
        from tools import controller as Ctl
        m = self.moves("CODING")
        cli = next(x for x in m if x.startswith("CLI:"))
        span = cli.split("`", 1)[1].rsplit("`", 1)[0]
        argv = shlex.split(span)
        self.assertEqual(argv[:5], [
            "python3", "factory/tools/controller.py", "request",
            "STORY-902", "BUILD_GREEN"])
        tail = argv[5:]
        self.assertEqual(tail.count("--evidence"),
                         len(SM.EVIDENCE_KEYS["BUILD_GREEN"]))
        with tempfile.TemporaryDirectory() as td:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = Ctl.main(["--root", td, "request", *argv[3:]])
        self.assertIsInstance(rc, int)  # parsed; refused later, not usage-exit
        self.assertNotIn("usage:", err.getvalue())


class FormattedMentionTests(unittest.TestCase):
    """M1: intake mentions normalize on insertion — backtick-wrapped or
    colon-suffixed ids yield a bare STORY-NNN in the known set."""

    def test_formatted_intake_ids_are_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "stories").mkdir()
            (root / "stories" / "BACKLOG.md").write_text(
                "- `STORY-951`: candidate (backticked, colon-suffixed)\n"
                "- STORY-952 — bare mention\n", encoding="utf-8")
            ids = R.known_story_ids([], root)
            self.assertIn("STORY-951", ids)
            self.assertIn("STORY-952", ids)
            self.assertNotIn("`STORY-951`:", ids)
            self.assertTrue(R.story_known("STORY-951", [], root))
            # a pre-ledger formatted mention still receives a pinned card
            card, known = R.build_card("STORY-951", [], root, "digest123456")
            self.assertTrue(known)
            self.assertIn("## Factory Card — STORY-951", card)
            self.assertNotIn("appears nowhere", card)


class StoryScopeTransitionTests(unittest.TestCase):
    """M3: the last-transition line is story scope — repo-tagged
    sub-events never display as the story's last transition."""

    def test_repo_sub_event_not_shown_as_last_transition(self):
        events = [
            {"event": "transition", "story_id": "STORY-961", "repo": None,
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator"},
            {"event": "transition", "story_id": "STORY-961", "repo": "app",
             "from_state": "CODING", "to_state": "MERGED",
             "requested_by": "orchestrator"},
        ]
        card = R.render_card_text("STORY-961", events, "CODING", None,
                                  Path("/nonexistent"), "digest123456")
        self.assertIn("**Last transition:** BACKLOG → CODING (by "
                      "orchestrator)", card)
        self.assertNotIn("MERGED", card.split("###")[0])

    def test_only_repo_events_falls_back_to_none_line(self):
        events = [
            {"event": "transition", "story_id": "STORY-962", "repo": "app",
             "from_state": "CODING", "to_state": "MERGED",
             "requested_by": "orchestrator"},
        ]
        card = R.render_card_text("STORY-962", events, "CODING", None,
                                  Path("/nonexistent"), "digest123456")
        self.assertIn("**Last transition:** none — no recorded transitions "
                      "yet", card)

    def test_story_scope_picks_unchanged(self):
        events = [
            {"event": "transition", "story_id": "STORY-963", "repo": None,
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator"},
            {"event": "transition", "story_id": "STORY-963", "repo": None,
             "from_state": "CODING", "to_state": "BUILD_GREEN",
             "requested_by": "agent-a"},
        ]
        card = R.render_card_text("STORY-963", events, "BUILD_GREEN", None,
                                  Path("/nonexistent"), "digest123456")
        self.assertIn("**Last transition:** CODING → BUILD_GREEN (by "
                      "agent-a)", card)


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


class Round39GateIdentityTests(unittest.TestCase):
    """AN4 (round 39): gate supersession matches the REQUEST IDENTITY
    (story_id, gate, question) — identical to render_state.py and
    render_dashboard.py. An answer to scope question A no longer removes
    a distinct pending question B on the same gate from the card."""

    @staticmethod
    def _gate(gate, question, asked, answered=None, sid="STORY-901"):
        return {"event": "human_gate", "story_id": sid, "gate": gate,
                "question": question, "asked_at": asked,
                "answered_at": answered}

    def test_answer_to_A_leaves_B_pending(self):
        a = self._gate("scope", "scope A?", "t1")
        b = self._gate("scope", "scope B?", "t2")
        a_reply = self._gate("scope", "scope A?", "t3", answered="t4")
        self.assertEqual(R.pending_gates([a, b, a_reply]), [b])

    def test_card_shows_B_as_pending_gate(self):
        events = [
            {"event": "transition", "story_id": "STORY-901", "repo": None,
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator"},
            self._gate("scope", "scope A?", "t1"),
            self._gate("scope", "scope B?", "t2"),
            self._gate("scope", "scope A?", "t3", answered="t4"),
        ]
        card = R.render_card_text("STORY-901", events, "CODING", None,
                                  Path("/nonexistent"), "digest123456")
        self.assertIn("**Pending human gate:** scope — “scope B?”", card)
        self.assertNotIn("scope A?", card.split("**Pending human gate:**")[1]
                         .splitlines()[0])

    def test_exact_repeat_still_folds(self):
        # identical identity (story, gate, question) re-asked + answered:
        # both earlier asks fold exactly as before
        g1 = self._gate("merge", "merge?", "t1")
        g2 = self._gate("merge", "merge?", "t2")
        g3 = self._gate("merge", "merge?", "t3", answered="t4")
        self.assertEqual(R.pending_gates([g1, g2, g3]), [])

    def test_normal_solo_lifecycle_unchanged(self):
        ask = self._gate("merge", "merge?", "t1")
        reply = self._gate("merge", "merge?", "t3", answered="t4")
        self.assertEqual(R.pending_gates([ask, reply]), [])
        self.assertEqual(R.pending_gates([ask]), [ask])

    def test_different_story_answer_does_not_supersede(self):
        # same gate+question text but a different story's answer must
        # never supersede this story's pending ask (defensive: callers
        # filter by story, but the identity term is compared anyway)
        mine = self._gate("merge", "merge?", "t1")
        theirs = self._gate("merge", "merge?", "t2", answered="t3",
                            sid="STORY-902")
        self.assertEqual(R.pending_gates([mine, theirs]), [mine])


class OwnerLifecycleTests(unittest.TestCase):
    """R18-S2: the card's owner line delegates to
    controller.open_claim_owner over the STORY events — a repo-less
    CLOSED transition clears ownership IMPLICITLY (no release row)."""

    @staticmethod
    def _assign(sid: str, **kw) -> dict:
        return {"event": "assignment", "story_id": sid, **kw}

    @staticmethod
    def _trans(sid: str, to_state: str, repo=None) -> dict:
        return {"event": "transition", "story_id": sid, "repo": repo,
                "from_state": "X", "to_state": to_state,
                "requested_by": "orchestrator"}

    def test_claim_handover_release(self):
        self.assertEqual(
            R.owner_line([self._assign("STORY-970", op="claim",
                                       owner="a")], "STORY-970"), "a")
        self.assertEqual(R.owner_line([
            self._assign("STORY-970", op="claim", owner="a"),
            self._assign("STORY-970", op="handover", **{"from": "a"},
                         to="b")], "STORY-970"), "b")
        self.assertEqual(R.owner_line([
            self._assign("STORY-970", op="claim", owner="a"),
            self._assign("STORY-970", op="release", owner="a")],
            "STORY-970"), "(none recorded)")
        self.assertEqual(R.owner_line([], "STORY-970"), "(none recorded)")

    def test_implicit_closed_clears_owner(self):
        """A claim followed by a story-level CLOSED transition with NO
        separate release row displays `(none recorded)`."""
        events = [self._assign("STORY-971", op="claim", owner="agent-a"),
                  self._trans("STORY-971", "CLOSED")]
        self.assertEqual(R.owner_line(events, "STORY-971"),
                         "(none recorded)")
        card = R.render_card_text("STORY-971", events, "CLOSED", None,
                                  Path("/nonexistent"), "digest123456")
        self.assertIn("**Owner:** (none recorded)", card)

    def test_repo_tagged_closed_keeps_owner(self):
        """A repo-tagged CLOSED sub-event is NOT an implicit release —
        the story claim stays open."""
        events = [self._assign("STORY-972", op="claim", owner="agent-a"),
                  self._trans("STORY-972", "CLOSED", repo="app")]
        self.assertEqual(R.owner_line(events, "STORY-972"), "agent-a")

    def test_non_terminal_transition_keeps_owner(self):
        """A claim followed by a NON-terminal transition keeps its
        owner."""
        events = [self._assign("STORY-973", op="claim", owner="agent-a"),
                  self._trans("STORY-973", "MERGE_ELIGIBLE")]
        self.assertEqual(R.owner_line(events, "STORY-973"), "agent-a")

    def test_other_story_events_ignored(self):
        events = [self._assign("STORY-974", op="claim", owner="agent-a"),
                  self._assign("STORY-999", op="claim", owner="agent-z"),
                  self._trans("STORY-999", "CLOSED")]
        self.assertEqual(R.owner_line(events, "STORY-974"), "agent-a")


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


class OutcomeOnlyStateTests(unittest.TestCase):
    """Round 28 AC3: a backfilled merged story with NO transition rows
    used to display `BACKLOG` + the start-of-lifecycle move. The state
    must derive from the outcome (merged / live-proven), matching
    render_state/dashboard semantics; a true no-data story keeps the
    BACKLOG fallback; a normal story is unchanged."""

    def _outcome_row(self, sid: str, **over) -> dict:
        o = {"event": "outcome", "event_id": f"outcome-{sid}-1",
             "story_id": sid, "merged_sha": "deadbeef1234",
             "merged_at": "2026-09-02T01:00:00Z",
             "live_proof_status": None,
             "live_proof_status_reason": "window not opened",
             "observation_end": None,
             "observation_end_reason": "window not opened",
             "escaped_defects": None,
             "escaped_defects_reason": "none known",
             "totals": None, "totals_reason": "not instrumented",
             "recommendation": None, "recommendation_reason": "n/a",
             "recorded_at": "2026-09-02T01:30:00Z",
             "provenance": "backfilled"}
        o.update(over)
        return o

    def _card(self, sid: str, events: list[dict]) -> str:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "stories").mkdir()
            card, known = R.build_card(sid, events, root, "digest123456")
            self.assertTrue(known)
            return card

    def test_outcome_only_story_shows_merged(self):
        card = self._card("STORY-981", [self._outcome_row("STORY-981")])
        self.assertIn("**State:** MERGED (derived from outcome)", card)
        self.assertNotIn("**State:** BACKLOG", card)
        # no start-of-lifecycle move hint (BACKLOG → GRILLED)
        self.assertNotIn("advance to **GRILLED**", card)

    def test_derived_state_suppresses_executable_move(self):
        """Round 29 AD1: the derived label must not advertise a move the
        controller would reject (BACKLOG state, no transitions — the
        order check refuses `request <derived successor>`). No CLI line,
        no `advance to …`, and a reconciliation note in its place."""
        card = self._card("STORY-987", [self._outcome_row("STORY-987")])
        self.assertIn("**State:** MERGED (derived from outcome)", card)
        self.assertNotIn("CLI:", card)
        self.assertNotIn("advance to", card)
        moves = card.split("### Next legal move(s)\n", 1)[1]
        moves = moves.split("\n\n", 1)[0]
        self.assertIn("— none (state derived from outcome; reconcile "
                      "transition history before the controller can "
                      "accept moves) —", moves)

    def test_derived_live_proven_suppresses_executable_move(self):
        card = self._card("STORY-988", [
            self._outcome_row("STORY-988", live_proof_status="proven")])
        self.assertIn("**State:** LIVE_PROVEN (derived from outcome)", card)
        self.assertNotIn("CLI:", card)
        self.assertNotIn("advance to", card)
        self.assertIn("state derived from outcome", card)

    def test_transitioned_story_moves_section_unchanged(self):
        """A story WITH transitions keeps the exact executable-moves
        output — the suppression applies only to derived state."""
        events = [
            {"event": "transition", "story_id": "STORY-989", "repo": None,
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator"},
            self._outcome_row("STORY-989"),
        ]
        card = self._card("STORY-989", events)
        self.assertIn("**State:** CODING", card)
        self.assertNotIn("derived from outcome", card)
        self.assertIn("advance to **BUILD_GREEN**", card)
        self.assertIn("CLI: `python3 factory/tools/controller.py request "
                      "STORY-989 BUILD_GREEN", card)
        self.assertNotIn("state derived from outcome", card)

    def test_outcome_only_live_proven_shows_live_proven(self):
        card = self._card("STORY-982", [
            self._outcome_row("STORY-982", live_proof_status="proven")])
        self.assertIn("**State:** LIVE_PROVEN (derived from outcome)", card)

    def test_unmerged_outcome_only_story_keeps_backlog(self):
        """An outcome row WITHOUT merged_sha is not delivery evidence —
        the no-data fallback stays (render_state derives only from a
        merged outcome too)."""
        card = self._card("STORY-983", [
            self._outcome_row("STORY-983", merged_sha=None,
                          merged_sha_reason="not merged yet")])
        self.assertIn("**State:** BACKLOG", card)

    def test_true_no_data_story_backlog_fallback(self):
        """A story known only from intake surfaces (no events at all)
        keeps the BACKLOG fallback unchanged."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "stories").mkdir()
            (root / "stories" / "BACKLOG.md").write_text(
                "- STORY-984 candidate\n", encoding="utf-8")
            card, known = R.build_card("STORY-984", [], root,
                                       "digest123456")
            self.assertTrue(known)
            self.assertIn("**State:** BACKLOG", card)
            self.assertIn("advance to **GRILLED**", card)
            self.assertNotIn("derived from outcome", card)

    def test_transitioned_story_unchanged(self):
        """A story WITH story-level transitions keeps the transition
        state even when an outcome row exists — derivation applies only
        when transitions are absent."""
        events = [
            {"event": "transition", "story_id": "STORY-985", "repo": None,
             "from_state": "BACKLOG", "to_state": "CODING",
             "requested_by": "orchestrator"},
            {"event": "transition", "story_id": "STORY-985", "repo": None,
             "from_state": "CODING", "to_state": "MERGED",
             "requested_by": "orchestrator"},
            self._outcome_row("STORY-985"),
        ]
        card = self._card("STORY-985", events)
        self.assertIn("**State:** MERGED", card)
        self.assertNotIn("derived from outcome", card)
        self.assertIn("**Last transition:** CODING → MERGED", card)

    def test_outcome_state_helper(self):
        self.assertEqual(
            R.outcome_state([self._outcome_row("STORY-986")], "STORY-986"),
            "MERGED")
        self.assertEqual(
            R.outcome_state([self._outcome_row("STORY-986",
                                           live_proof_status="proven")],
                            "STORY-986"),
            "LIVE_PROVEN")
        self.assertIsNone(
            R.outcome_state([self._outcome_row("STORY-986", merged_sha=None)],
                            "STORY-986"))
        self.assertIsNone(R.outcome_state([], "STORY-986"))


class UpsertSeamTests(unittest.TestCase):
    """Marker-anchored upsert via stub gh_fn — no network, no gh."""

    TRUSTED = "factory-bot"

    def setUp(self):
        self.calls: list[tuple] = []
        self.user_calls: list[tuple] = []

    def fake_gh(self, *args, stdin=None):
        # R35-AJ2: login resolution rides the SAME transport; kept out of
        # `calls` so the listing/write call indices stay stable.
        if args[:2] == ("api", "user"):
            self.user_calls.append(args)
            return f"{self.TRUSTED}\n"
        self.calls.append((args, stdin))
        if len(self.calls) == 1:
            return "42\n"  # one existing pinned comment
        return "ok"

    def fake_gh_empty(self, *args, stdin=None):
        if args[:2] == ("api", "user"):
            self.user_calls.append(args)
            return f"{self.TRUSTED}\n"
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

    def test_selector_anchored_to_body_start(self):
        """R14-O3: the listing selector must use startswith, NOT
        contains — a contains selector also matches a reply QUOTING the
        card and would PATCH that reply."""
        R.upsert_comment("o/r", 7, "BODY", gh_fn=self.fake_gh)
        jq = self.calls[0][0][-1]
        self.assertIn(f'startswith("{R.PIN_MARKER}")', jq)
        self.assertNotIn("contains(", jq)

    def test_quoted_reply_not_selected_by_jq(self):
        """R14-O3 grounded check: run the ACTUAL jq selector emitted by
        upsert_comment over a comment list where a later reply quotes the
        card mid-body (blockquote + code fence). Only the genuine card
        comment (marker at start) is selected."""
        captured: dict = {}

        def gh(*args, stdin=None):
            if args[:2] == ("api", "user"):
                return f"{self.TRUSTED}\n"
            if len(captured) == 0 and "--jq" in args:
                captured["jq"] = args[args.index("--jq") + 1]
                return "101\n"
            return "ok"

        R.upsert_comment("o/r", 7, "BODY", gh_fn=gh)
        if not shutil.which("jq"):
            self.skipTest("jq not installed — selector string verified by "
                          "test_selector_anchored_to_body_start")
        comments = [
            {"id": 55, "user": {"login": self.TRUSTED},
             "body": f"As seen in:\n> {R.PIN_MARKER}\n"},
            {"id": 77, "user": {"login": self.TRUSTED},
             "body": f"```\n{R.PIN_MARKER}\nCARD\n```\n"},
            {"id": 101, "user": {"login": self.TRUSTED},
             "body": f"{R.PIN_MARKER}\nCARD BODY\n"},
        ]
        out = subprocess.run(
            ["jq", "-r", captured["jq"]],
            input=json.dumps(comments), capture_output=True,
            text=True, check=True)
        self.assertEqual(out.stdout.split(), ["101"])

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


class Round35AuthorTrustTests(unittest.TestCase):
    """R35-AJ2: pin selection restricted to the trusted writer identity.
    A marker-at-start comment by ANOTHER author must never capture the
    PATCH target — the selector requires BOTH startswith(marker) AND
    user.login == the authenticated login. Fake transport only: no
    network, no gh calls."""

    TRUSTED = "factory-bot"
    INTRUDER = "someone-else"

    def setUp(self):
        self.user_calls: list[tuple] = []
        self.calls: list[tuple] = []

    def transport(self, listing: str):
        """Fake gh: resolves the login once, serves `listing` for the
        comment listing, records the write call."""
        def gh(*args, stdin=None):
            if args[:2] == ("api", "user"):
                self.user_calls.append(args)
                return f"{self.TRUSTED}\n"
            self.calls.append((args, stdin))
            if "--jq" in args:
                return listing
            return "ok"
        return gh

    def test_selector_string_has_both_clauses(self):
        """Grounded jq-selector check: startswith(marker) AND user.login
        equality with the resolved trusted login."""
        R.upsert_comment("o/r", 7, "BODY", gh_fn=self.transport("42\n"))
        jq = self.calls[0][0][-1]
        self.assertIn(f'startswith("{R.PIN_MARKER}")', jq)
        self.assertIn(f'.user.login == "{self.TRUSTED}"', jq)
        self.assertNotIn("contains(", jq)
        # the login was resolved through the transport exactly once
        self.assertEqual(self.user_calls,
                         [("api", "user", "--jq", ".login")])

    def test_forged_marker_by_other_author_ignored_genuine_patched(self):
        """Grounded jq check over a listing where a LATER comment by
        another author starts with a copied pin marker: only the genuine
        same-author card is selected, and the PATCH targets it."""
        jq_box: dict = {}

        def gh(*args, stdin=None):
            if args[:2] == ("api", "user"):
                return f"{self.TRUSTED}\n"
            if "--jq" in args and "--paginate" in args:
                jq_box["jq"] = args[args.index("--jq") + 1]
                return "101\n"
            self.calls.append((args, stdin))
            return "ok"

        R.upsert_comment("o/r", 7, "BODY", gh_fn=gh)
        if not shutil.which("jq"):
            self.skipTest("jq not installed — selector string verified by "
                          "test_selector_string_has_both_clauses")
        comments = [
            {"id": 101, "user": {"login": self.TRUSTED},
             "body": f"{R.PIN_MARKER}\nGENUINE CARD\n"},
            {"id": 202, "user": {"login": self.INTRUDER},
             "body": f"{R.PIN_MARKER}\nFORGED COPY BY ANOTHER AUTHOR\n"},
        ]
        out = subprocess.run(
            ["jq", "-r", jq_box["jq"]],
            input=json.dumps(comments), capture_output=True,
            text=True, check=True)
        self.assertEqual(out.stdout.split(), ["101"])
        method_idx = self.calls[0][0].index("--method")
        self.assertEqual(self.calls[0][0][method_idx + 1], "PATCH")
        self.assertTrue(any("issues/comments/101" in a
                            for a in self.calls[0][0]))

    def test_no_same_author_pin_posts(self):
        """All marker-at-start comments belong to another author → the
        selector finds nothing → POST a fresh card (existing behavior)."""
        gh = self.transport("")  # jq filter matched nothing
        R.upsert_comment("o/r", 7, "BODY", gh_fn=gh)
        method_idx = self.calls[1][0].index("--method")
        self.assertEqual(self.calls[1][0][method_idx + 1], "POST")
        self.assertTrue(any(a.endswith("/issues/7/comments")
                            for a in self.calls[1][0]))

    def test_injected_login_skips_resolution(self):
        """`login=` is injectable: no `api user` call, and the selector
        carries the injected identity."""
        gh = self.transport("42\n")
        R.upsert_comment("o/r", 7, "BODY", gh_fn=gh, login="pinned-id")
        self.assertEqual(self.user_calls, [])
        self.assertIn('.user.login == "pinned-id"',
                      self.calls[0][0][-1])

    def test_unresolvable_login_refuses(self):
        """Empty login → BoardError (fail closed): no listing, no write."""
        def gh(*args, stdin=None):
            if args[:2] == ("api", "user"):
                return "\n"
            self.calls.append((args, stdin))
            return "ok"
        with self.assertRaises(SB.BoardError):
            R.upsert_comment("o/r", 7, "BODY", gh_fn=gh)
        self.assertEqual(self.calls, [])

    def test_dry_run_never_resolves_login(self):
        out = io.StringIO()
        with redirect_stdout(out):
            got = R.upsert_comment("o/r", 7, "BODY",
                                   gh_fn=self.transport("42\n"),
                                   dry_run=True)
        self.assertEqual(got, "dry-run")
        self.assertEqual(self.user_calls, [])
        self.assertEqual(self.calls, [])


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

    # ---- round 21 V2: unknown story exits nonzero on BOTH CLI paths
    def test_unknown_story_render_only_rc1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)  # no stories, empty ledger → STORY-999 unknown
            rc, out, _ = self.run_main(["STORY-999", "--root", str(root)])
            self.assertEqual(rc, 1)
            # refusal output intact — explanatory card, never pinned
            self.assertIn(R.CARD_MARKER, out)
            self.assertIn("**Nothing was pinned.**", out)
            self.assertIn("unknown", out)
            self.assertNotIn(R.PIN_MARKER, out)

    def test_unknown_story_issue_dry_run_rc1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "workspace.toml").write_text(
                '[[repos]]\nid = "app"\nrole = "primary"\n'
                'url = "github.test/fixture-org/app"\n', encoding="utf-8")
            rc, out, _ = self.run_main(
                ["STORY-999", "--root", str(root), "--issue", "42",
                 "--dry-run"])
            self.assertEqual(rc, 1)
            # explanation payload still shown (no pin marker)
            self.assertIn("POST repos/fixture-org/app/issues/42/comments",
                          out)
            payload = json.loads(
                out.split("POST repos/", 1)[1].splitlines()[1])
            self.assertIn("**Nothing was pinned.**", payload["body"])
            self.assertNotIn(R.PIN_MARKER, payload["body"])

    def test_known_story_rc0_both_paths(self):
        rc, out, _ = self.run_main(self.base())
        self.assertEqual(rc, 0)
        rc, out, _ = self.run_main(self.base("--issue", "42", "--dry-run"))
        self.assertEqual(rc, 0)
        self.assertIn("dry-run: no write performed", out)


class Round34LFNormalizationTests(unittest.TestCase):
    """Round 34 sweep: the card embeds free-form ledger values OUTSIDE
    tables (owner line, gate question, evidence paths) — no `_cell` — so
    a CR carried by a ledger value used to land verbatim in the posted
    comment body. render_card_text now normalizes the ASSEMBLED card
    (CRLF→LF, then lone CR→LF)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = Path(cls._td.name)
        (cls.root / "workspace.toml").write_text(
            '[[repos]]\nid = "app"\nrole = "primary"\n'
            'url = "github.test/fixture-org/app"\n', encoding="utf-8")
        cls.ledger = build_ledger(cls.root)
        cls.digest = R.ledger_digest(cls.ledger)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_owner_and_gate_cr_crlf_normalize_to_lf(self):
        claim = {"event": "assignment", "event_id": f"assignment-{STORY}-90",
                 "story_id": STORY, "op": "claim",
                 "owner": "agent\r\nx\ry", "host": "host-1",
                 "session": "sess-901",
                 "claimed_at": "2026-09-01T00:00:00Z",
                 "start_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                 "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"}
        gate = {"event": "human_gate", "event_id": f"human_gate-{STORY}-90",
                "story_id": STORY, "gate": "merge",
                "question": "Merge\r\nnow\ror later?",
                "asked_at": "2026-09-01T00:00:00Z",
                "answered_at": None, "answered_at_reason": "awaiting",
                "decision": None, "decision_reason": "pending",
                "answered_by": None, "answered_by_reason": "pending",
                "recorded_at": "2026-09-01T00:00:00Z", "provenance": "live"}
        events = L.load_events(self.ledger) + [claim, gate]
        card, known = R.build_card(STORY, events, self.root, self.digest)
        self.assertTrue(known)
        self.assertNotIn("\r", card)
        self.assertIn("**Owner:** agent\nx\ny", card)
        self.assertIn("“Merge\nnow\nor later?”", card)

    def test_normal_card_unchanged_and_lf_only(self):
        events = L.load_events(self.ledger)
        card, known = R.build_card(STORY, events, self.root, self.digest)
        self.assertTrue(known)
        self.assertNotIn("\r", card)
        self.assertIn(R.CARD_MARKER, card)
        # byte-identical for the same ledger (determinism preserved)
        card2, _ = R.build_card(STORY, L.load_events(self.ledger),
                                self.root, self.digest)
        self.assertEqual(card, card2)


if __name__ == "__main__":
    unittest.main()
