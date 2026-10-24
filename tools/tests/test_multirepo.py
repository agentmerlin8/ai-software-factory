#!/usr/bin/env python3
"""Tests for tools/multirepo.py — repo@sha[:path] pins, per-repo sub-events
+ folding, set-level completeness refusals, corrections with repo scope,
and the tools/ledger_schema.py integration (repo nullables on transition /
correction, the claim exactly-one-of start_sha|start_shas rule, and
current_state excluding sub-events)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import ledger_schema as S
from tools import multirepo as M

STORY = "STORY-100"
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "multirepo")


def load_jsonl(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def sub_event(eid: str, repo: str, to_state: str,
              frm: str = "CODING", story: str = STORY) -> dict:
    return {"event": "transition", "event_id": eid,
            "recorded_at": "2026-10-25T00:00:00Z", "provenance": "live",
            "story_id": story, "from_state": frm, "to_state": to_state,
            "requested_by": "orchestrator", "checks_run": ["build"],
            "check_results": {"build": {"ok": True}},
            "approved_by": None, "approved_by_reason": "no human gate required",
            "repo": repo, "repo_reason": None}


def story_transition(eid: str, to_state: str, frm: str = "BACKLOG",
                     repo: str | None = None, story: str = STORY) -> dict:
    return {"event": "transition", "event_id": eid,
            "recorded_at": "2026-10-25T00:00:00Z", "provenance": "live",
            "story_id": story, "from_state": frm, "to_state": to_state,
            "requested_by": "orchestrator", "checks_run": ["story_md"],
            "check_results": {"story_md": {"ok": True}},
            "approved_by": None, "approved_by_reason": "no human gate required",
            "repo": repo,
            "repo_reason": None if repo else "story-level transition"}


def correction(eid: str, repo: str | None, value: str,
               story: str = STORY) -> dict:
    return {"event": "correction", "event_id": eid,
            "recorded_at": "2026-10-25T01:00:00Z", "provenance": "live",
            "story_id": story, "story_id_reason": None,
            "repo": repo,
            "repo_reason": None if repo else "story-level correction",
            "contradicted_claim": "folded value record",
            "corrected_value": value,
            "source_citation": "ledger rows",
            "corrected_artifact": f"stories/{story}/state.md"}


def claim(eid: str = "assign-1", **overrides) -> dict:
    evt = {"event": "assignment", "event_id": eid,
           "recorded_at": "2026-10-25T00:00:00Z", "provenance": "live",
           "story_id": STORY, "op": "claim",
           "owner": "worker-1", "host": "host-a", "session": "sess-1",
           "claimed_at": "2026-10-25T00:00:00Z", "start_sha": "0" * 40}
    evt.update(overrides)
    return evt


class TestParseAndValidatePin(unittest.TestCase):
    def test_valid_simple(self):
        self.assertEqual(M.parse_pin("app@abc1234"),
                         {"repo": "app", "sha": "abc1234", "path": None})
        self.assertEqual(M.validate_pin("app@abc1234"), [])

    def test_valid_with_path(self):
        self.assertEqual(M.parse_pin("app@abc1234:src/main"),
                         {"repo": "app", "sha": "abc1234", "path": "src/main"})

    def test_valid_repo_sha_bounds(self):
        self.assertEqual(M.parse_pin("app@abcdef0")["sha"], "abcdef0")      # 7 hex
        self.assertEqual(M.parse_pin("app@" + "a" * 40)["sha"], "a" * 40)   # 40 hex
        self.assertEqual(M.parse_pin("my-app2@" + "a" * 40)["repo"], "my-app2")

    def test_missing_at(self):
        errs = M.validate_pin("app")
        self.assertEqual(errs, ["malformed repo@sha pin: app (missing @)"])
        with self.assertRaises(ValueError):
            M.parse_pin("app")

    def test_bad_repo_id(self):
        for bad in ("App@abc1234", "1app@abc1234", "app!x@abc1234"):
            with self.subTest(pin=bad):
                errs = M.validate_pin(bad)
                self.assertTrue(any("bad repo id" in e for e in errs), errs)
        self.assertEqual(M.validate_pin("App@abc1234"),
                         ["malformed repo@sha pin: App@abc1234 "
                          "(bad repo id 'App')"])

    def test_bad_sha(self):
        for bad in ("app@abc12", "app@ABC1234", "app@abc123g",
                    "app@" + "a" * 41, "app@abc123-"):
            with self.subTest(pin=bad):
                errs = M.validate_pin(bad)
                self.assertTrue(any("bad sha" in e for e in errs), (bad, errs))

    def test_path_dotdot_escape(self):
        for bad in ("app@abc1234:../etc", "app@abc1234:src/../main"):
            with self.subTest(pin=bad):
                errs = M.validate_pin(bad)
                self.assertTrue(any("path escapes via '..'" in e
                                    for e in errs), errs)

    def test_absolute_path(self):
        errs = M.validate_pin("app@abc1234:/etc/passwd")
        self.assertTrue(any("absolute path" in e for e in errs), errs)

    def test_empty_path(self):
        errs = M.validate_pin("app@abc1234:")
        self.assertTrue(any("empty path" in e for e in errs), errs)


class TestResolvePin(unittest.TestCase):
    def _init_repo(self) -> tuple[str, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = dict(os.environ, GIT_AUTHOR_NAME="lint-t",
                   GIT_AUTHOR_EMAIL="t@t.invalid",
                   GIT_COMMITTER_NAME="lint-t",
                   GIT_COMMITTER_EMAIL="t@t.invalid")
        subprocess.run(["git", "-C", tmp.name, "init", "-q"],
                       check=True, env=env)
        subprocess.run(["git", "-C", tmp.name, "commit", "--allow-empty",
                        "-q", "-m", "first commit"], check=True, env=env)
        sha = subprocess.run(["git", "-C", tmp.name, "rev-parse", "HEAD"],
                             check=True, capture_output=True, text=True,
                             env=env).stdout.strip()
        return tmp.name, sha

    def test_resolvable_sha(self):
        path, sha = self._init_repo()
        self.assertEqual(M.resolve_pin(f"app@{sha}", {"app": path}), [])

    def test_resolvable_with_path_suffix(self):
        path, sha = self._init_repo()
        self.assertEqual(M.resolve_pin(f"app@{sha}:src/main",
                                       {"app": path}), [])

    def test_unknown_repo(self):
        path, sha = self._init_repo()
        errs = M.resolve_pin(f"mystery@{sha}", {"app": path})
        self.assertEqual(errs,
                         ["unknown repo 'mystery' — not in the workspace "
                          "binding"])

    def test_unresolvable_absent_sha(self):
        path, _ = self._init_repo()
        absent = "f" * 40
        errs = M.resolve_pin(f"app@{absent}", {"app": path})
        self.assertEqual(errs, [f"unresolvable pin: app@{absent} "
                                f"(not found in {path})"])

    def test_malformed_passthrough(self):
        errs = M.resolve_pin("App@xyz", {"app": "/tmp/none"})
        self.assertEqual(errs, ["malformed repo@sha pin: App@xyz "
                                "(bad repo id 'App')"])


class TestValidateSubEvent(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(
            M.validate_sub_event(sub_event("s1", "app", "BUILD_GREEN")), [])

    def test_wrong_event_type(self):
        evt = {"event": "stage", "repo": "app",
               "from_state": "CODING", "to_state": "BUILD_GREEN"}
        self.assertEqual(M.validate_sub_event(evt),
                         ["sub-event: event 'stage' is not a transition"])

    def test_repo_null(self):
        evt = sub_event("s1", "app", "BUILD_GREEN")
        evt["repo"] = None
        errs = M.validate_sub_event(evt)
        self.assertIn("sub-event: repo must be a non-null binding id slug",
                      errs)

    def test_repo_not_slug(self):
        evt = sub_event("s1", "app", "BUILD_GREEN")
        evt["repo"] = "App"
        errs = M.validate_sub_event(evt)
        self.assertIn("sub-event: repo 'App' is not a binding id slug", errs)

    def test_non_ladder_to_state(self):
        evt = sub_event("s1", "app", "CODING")
        self.assertEqual(M.validate_sub_event(evt),
                         ["sub-event: to_state 'CODING' is not a per-repo "
                          "ladder state"])

    def test_unknown_from_state(self):
        evt = sub_event("s1", "app", "BUILD_GREEN", frm="NOPE")
        errs = M.validate_sub_event(evt)
        self.assertIn("sub-event: from_state 'NOPE' is not a story-level "
                      "state", errs)

    def test_errors_accumulate(self):
        evt = sub_event("s1", "app", "CODING", frm="NOPE")
        evt["repo"] = None
        errs = M.validate_sub_event(evt)
        self.assertEqual(len(errs), 3)


class TestSubEventsAndFold(unittest.TestCase):
    def test_fixture_events_validate_clean(self):
        for name in ("subevents_unordered.jsonl", "subevents_complete_ready.jsonl",
                     "subevents_incomplete.jsonl", "subevents_merged.jsonl",
                     "corrections.jsonl"):
            with self.subTest(fixture=name):
                for evt in load_jsonl(name):
                    self.assertEqual(S.validate_event(evt), [], evt)
                    if evt["event"] == "transition":
                        self.assertEqual(M.validate_sub_event(evt), [], evt)

    def test_fold_max_wins_unordered_and_duplicates(self):
        folded = M.fold_repo_status(load_jsonl("subevents_unordered.jsonl"),
                                    STORY)
        self.assertEqual(folded, {
            "app": {"reached": "READY_FOR_REVIEW", "sub_events": 4,
                    "corrected": False},
            "contracts": {"reached": "MERGED", "sub_events": 3,
                          "corrected": False},
        })

    def test_repo_without_sub_events_absent(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(set(folded), {"app", "contracts"})
        only_app = M.fold_repo_status(
            [e for e in events if e["repo"] == "app"], STORY)
        self.assertEqual(list(only_app), ["app"])
        self.assertNotIn("contracts", only_app)

    def test_other_story_events_excluded(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        events.append(sub_event("s-x", "app", "MERGED", story="STORY-101"))
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded["app"]["sub_events"], 1)

    def test_sub_events_filter_and_order(self):
        events = [sub_event("s1", "app", "BUILD_GREEN"),
                  story_transition("t1", "TEST_PLAN"),   # no repo → excluded
                  sub_event("s2", "contracts", "DRAFT_OPENED"),
                  sub_event("s3", "app", "DRAFT_OPENED")]
        self.assertEqual([e["event_id"] for e in M.sub_events(events, STORY)],
                         ["s1", "s2", "s3"])
        self.assertEqual([e["event_id"] for e in
                          M.sub_events(events, STORY, "app")], ["s1", "s3"])

    def test_correction_overrides_and_flags(self):
        folded = M.fold_repo_status(load_jsonl("corrections.jsonl"), STORY)
        self.assertEqual(folded, {
            "app": {"reached": "DRAFT_OPENED", "sub_events": 1,
                    "corrected": True},
            "contracts": {"reached": "FULL_TESTS_GREEN", "sub_events": 1,
                          "corrected": False},
        })

    def test_non_ladder_and_story_level_corrections_ignored(self):
        folded = M.fold_repo_status(load_jsonl("corrections.jsonl"), STORY)
        # corr-2 (non-ladder value) left contracts uncorrected; corr-3
        # (no repo) did not appear at all.
        self.assertFalse(folded["contracts"]["corrected"])
        self.assertEqual(set(folded), {"app", "contracts"})

    def test_correction_without_sub_events_is_absent(self):
        folded = M.fold_repo_status([correction("corr-x", "contracts",
                                                "MERGED")], STORY)
        self.assertEqual(folded, {})


class TestCorrectionScopedFold(unittest.TestCase):
    """F1 regression: corrections are applied AT their ledger position —
    a rollback never permanently overrides later progress, and MERGED
    completeness no longer refuses on a folded-back-then-forward repo."""

    def test_rollback_then_progress_folds_to_merged(self):
        events = [
            sub_event("s1", "app", "READY_FOR_REVIEW"),
            correction("corr-1", "app", "DRAFT_OPENED"),
            sub_event("s2", "app", "MERGED"),
        ]
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded, {
            "app": {"reached": "MERGED", "sub_events": 2, "corrected": True},
        })
        self.assertEqual(M.set_completeness(events, STORY, ["app"], "MERGED"),
                         [])

    def test_rollback_without_later_progress_refuses(self):
        events = [
            sub_event("s1", "app", "READY_FOR_REVIEW"),
            correction("corr-1", "app", "DRAFT_OPENED"),
        ]
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded, {
            "app": {"reached": "DRAFT_OPENED", "sub_events": 1,
                    "corrected": True},
        })
        got = M.set_completeness(events, STORY, ["app"], "MERGE_ELIGIBLE")
        self.assertEqual(got, ["repo 'app': reached DRAFT_OPENED, "
                               "needs >= READY_FOR_REVIEW"])

    def test_correction_after_progress_still_rolls_back(self):
        events = [
            sub_event("s1", "app", "MERGED"),
            correction("corr-1", "app", "DRAFT_OPENED"),
        ]
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded["app"]["reached"], "DRAFT_OPENED")
        self.assertTrue(folded["app"]["corrected"])

    def test_multiple_rollbacks_and_advances_last_position_wins(self):
        events = [
            sub_event("s1", "app", "MERGED"),
            correction("c1", "app", "BUILD_GREEN"),
            sub_event("s2", "app", "FULL_TESTS_GREEN"),
            correction("c2", "app", "DRAFT_OPENED"),
            sub_event("s3", "app", "READY_FOR_REVIEW"),
        ]
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded["app"]["reached"], "READY_FOR_REVIEW")
        self.assertTrue(folded["app"]["corrected"])
        self.assertEqual(folded["app"]["sub_events"], 3)

    def test_non_dict_rows_skipped(self):
        events = [None, "x", 123,
                  sub_event("s1", "app", "BUILD_GREEN")]
        folded = M.fold_repo_status(events, STORY)
        self.assertEqual(folded, {
            "app": {"reached": "BUILD_GREEN", "sub_events": 1,
                    "corrected": False},
        })
        self.assertEqual([e["event_id"]
                          for e in M.sub_events(events, STORY)], ["s1"])


class TestSetCompleteness(unittest.TestCase):
    def test_complete_merge_eligible(self):
        events = load_jsonl("subevents_complete_ready.jsonl")
        self.assertEqual(M.set_completeness(events, STORY,
                                            ["app", "contracts"],
                                            "MERGE_ELIGIBLE"), [])

    def test_incomplete_named_missing_repo_line(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        got = M.set_completeness(events, STORY, ["app", "contracts"],
                                 "MERGE_ELIGIBLE")
        self.assertEqual(got, ["repo 'contracts': reached DRAFT_OPENED, "
                               "needs >= READY_FOR_REVIEW"])

    def test_declared_repo_with_no_sub_events(self):
        got = M.set_completeness([], STORY, ["app"], "MERGE_ELIGIBLE")
        self.assertEqual(got, ["repo 'app': reached none, "
                               "needs >= READY_FOR_REVIEW"])

    def test_merged_target(self):
        merged = load_jsonl("subevents_merged.jsonl")
        ready = load_jsonl("subevents_complete_ready.jsonl")
        self.assertEqual(M.set_completeness(merged, STORY,
                                            ["app", "contracts"], "MERGED"),
                         [])
        self.assertEqual(M.set_completeness(ready, STORY, ["app", "contracts"],
                                            "MERGED"),
                         ["repo 'app': reached READY_FOR_REVIEW, "
                          "needs >= MERGED"])

    def test_live_proven_requires_proof(self):
        merged = load_jsonl("subevents_merged.jsonl")
        self.assertEqual(M.set_completeness(merged, STORY,
                                            ["app", "contracts"],
                                            "LIVE_PROVEN"),
                         ["live proof evidence not recorded"])
        self.assertEqual(M.set_completeness(merged, STORY,
                                            ["app", "contracts"],
                                            "LIVE_PROVEN",
                                            proof_evidence="evidence/live.md"),
                         [])

    def test_live_proven_repo_lines_then_proof(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        got = M.set_completeness(events, STORY, ["app", "contracts"],
                                 "LIVE_PROVEN")
        self.assertEqual(got, [
            "repo 'app': reached READY_FOR_REVIEW, needs >= MERGED",
            "repo 'contracts': reached DRAFT_OPENED, needs >= MERGED",
            "live proof evidence not recorded",
        ])

    def test_repos_checked_in_declared_order(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        got = M.set_completeness(events, STORY, ["contracts", "app"],
                                 "MERGED")
        self.assertEqual(got[0],
                         "repo 'contracts': reached DRAFT_OPENED, "
                         "needs >= MERGED")

    def test_non_set_level_target_raises(self):
        with self.assertRaises(ValueError) as ctx:
            M.set_completeness([], STORY, ["app"], "CODING")
        self.assertIn("CODING", str(ctx.exception))

    def test_deterministic(self):
        events = load_jsonl("subevents_incomplete.jsonl")
        first = M.set_completeness(events, STORY, ["app", "contracts"],
                                   "LIVE_PROVEN")
        second = M.set_completeness(events, STORY, ["app", "contracts"],
                                    "LIVE_PROVEN")
        self.assertEqual(first, second)
        self.assertEqual(M.fold_repo_status(
            load_jsonl("subevents_unordered.jsonl"), STORY),
            M.fold_repo_status(
                load_jsonl("subevents_unordered.jsonl"), STORY))


class TestLedgerSchemaIntegration(unittest.TestCase):
    def test_transition_with_repo_valid(self):
        self.assertEqual(
            S.validate_event(story_transition("t1", "GRILLED", repo="app")),
            [])

    def test_transition_repo_reason_must_be_null_when_repo_set(self):
        evt = story_transition("t1", "GRILLED", repo="app")
        evt["repo_reason"] = "unexpected"
        errs = S.validate_event(evt)
        self.assertTrue(any("`repo` has a value so `repo_reason` must be "
                            "null" in e for e in errs), errs)

    def test_transition_repo_null_requires_reason_key(self):
        evt = story_transition("t1", "GRILLED")
        del evt["repo_reason"]
        errs = S.validate_event(evt)
        self.assertTrue(any("nullable field `repo` requires sibling "
                            "`repo_reason`" in e for e in errs), errs)

    def test_transition_repo_null_with_reason_valid(self):
        self.assertEqual(S.validate_event(story_transition("t1", "GRILLED")),
                         [])

    def test_correction_with_repo_valid(self):
        self.assertEqual(S.validate_event(correction("c1", "app", "MERGED")),
                         [])

    def test_correction_repo_reason_must_be_null_when_repo_set(self):
        evt = correction("c1", "app", "MERGED")
        evt["repo_reason"] = "unexpected"
        errs = S.validate_event(evt)
        self.assertTrue(any("`repo` has a value so `repo_reason` must be "
                            "null" in e for e in errs), errs)

    def test_correction_without_repo_valid(self):
        self.assertEqual(S.validate_event(correction("c1", None, "X")), [])

    def test_claim_start_sha_only_valid(self):
        self.assertEqual(S.validate_event(claim()), [])

    def test_claim_start_shas_only_valid(self):
        evt = claim()
        del evt["start_sha"]
        evt["start_shas"] = {"app": "a" * 40, "contracts": "b" * 7}
        self.assertEqual(S.validate_event(evt), [])

    def test_claim_neither(self):
        evt = claim()
        del evt["start_sha"]
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim requires start_sha or start_shas "
                      "(exactly one)", errs)

    def test_claim_both(self):
        evt = claim()
        evt["start_shas"] = {"app": "a" * 40}
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim takes exactly one of start_sha, "
                      "start_shas", errs)

    def test_claim_start_shas_not_dict(self):
        evt = claim()
        del evt["start_sha"]
        evt["start_shas"] = "app@abc"
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim start_shas must be an object "
                      "mapping repo ids to commit SHAs", errs)

    def test_claim_start_shas_empty(self):
        evt = claim()
        del evt["start_sha"]
        evt["start_shas"] = {}
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim start_shas must be non-empty", errs)

    def test_claim_start_shas_bad_key(self):
        evt = claim()
        del evt["start_sha"]
        evt["start_shas"] = {"App": "a" * 40, "app": "a" * 40}
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim start_shas key 'App' is not a "
                      "valid repo id", errs)

    def test_claim_start_shas_bad_value(self):
        evt = claim()
        del evt["start_sha"]
        evt["start_shas"] = {"app": "xyz"}
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim start_shas['app'] value must be a "
                      "7-40 lowercase hex SHA", errs)

    def test_current_state_ignores_sub_events(self):
        ledger = [story_transition("t1", "GRILLED"),
                  story_transition("t2", "TEST_PLAN", frm="GRILLED"),
                  sub_event("s1", "app", "MERGED"),
                  story_transition("t3", "SPEC_FROZEN", frm="TEST_PLAN")]
        self.assertEqual(S.current_state(ledger, STORY), "SPEC_FROZEN")
        self.assertEqual(S.current_state(
            [sub_event("s1", "app", "MERGED")], STORY), None)


class TestPinHelperHardening(unittest.TestCase):
    """C2 regression: the pin helpers refuse where they used to crash."""

    def test_parse_pin_non_string_raises_named_valueerror(self):
        for bad in (123, None, ["app@abc1234"], 3.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as ctx:
                    M.parse_pin(bad)
                self.assertEqual(
                    str(ctx.exception),
                    f"malformed repo@sha pin: {bad!r} (not a string)")

    def test_validate_pin_non_string_named_refusal(self):
        self.assertEqual(M.validate_pin(123),
                         ["malformed repo@sha pin: 123 (not a string)"])
        self.assertEqual(M.validate_pin(None),
                         ["malformed repo@sha pin: None (not a string)"])

    def test_resolve_pin_non_string_passthrough(self):
        self.assertEqual(M.resolve_pin(123, {"app": "/tmp/none"}),
                         ["malformed repo@sha pin: 123 (not a string)"])

    def test_resolve_pin_git_missing_is_named_unresolvable(self):
        # git absent (or unusable) = the sha cannot be verified ⇒ the same
        # named unresolvable refusal, never a crash, never a silent pass.
        with mock.patch("tools.multirepo.subprocess.run",
                        side_effect=FileNotFoundError("git")):
            errs = M.resolve_pin("app@" + "a" * 40, {"app": "/some/checkout"})
        self.assertEqual(errs, ["unresolvable pin: app@" + "a" * 40 +
                                " (not found in /some/checkout)"])


class TestSetCompletenessEmptyRepos(unittest.TestCase):
    """C3 regression: an EMPTY declared-repo set never silently passes."""

    def test_empty_repos_refused_at_every_set_level(self):
        for target in list(M.SET_LEVEL_RULES):
            with self.subTest(target=target):
                got = M.set_completeness([], STORY, [], target,
                                         proof_evidence="evidence/live.md")
                self.assertEqual(got, ["set-level transition refused: no "
                                       "declared repos for the story"])

    def test_empty_repos_refused_even_with_complete_sub_events(self):
        events = load_jsonl("subevents_merged.jsonl")
        got = M.set_completeness(events, STORY, [], "MERGED")
        self.assertEqual(got, ["set-level transition refused: no declared "
                               "repos for the story"])

    def test_non_empty_repos_behavior_unchanged(self):
        events = load_jsonl("subevents_complete_ready.jsonl")
        self.assertEqual(M.set_completeness(events, STORY,
                                            ["app", "contracts"],
                                            "MERGE_ELIGIBLE"), [])

    def test_non_set_level_target_still_raises_before_empty_check(self):
        with self.assertRaises(ValueError) as ctx:
            M.set_completeness([], STORY, [], "CODING")
        self.assertIn("CODING", str(ctx.exception))


class TestRepoSlugValidation(unittest.TestCase):
    """Non-null `repo` on transition/correction must be a binding-id slug
    (^[a-z][a-z0-9-]*$); fold/sub_events must SKIP non-string repo rows
    instead of raising."""

    def test_transition_repo_list_named_error(self):
        evt = story_transition("t1", "GRILLED", repo="app")
        evt["repo"] = ["app"]
        errs = S.validate_event(evt)
        self.assertTrue(any("transition: repo must be a binding-id slug "
                            "(^[a-z][a-z0-9-]*$) or null" in e
                            for e in errs), errs)

    def test_transition_repo_empty_string_named_error(self):
        evt = story_transition("t1", "GRILLED", repo="app")
        evt["repo"] = ""
        evt["repo_reason"] = None
        errs = S.validate_event(evt)
        self.assertTrue(any("transition: repo must be a binding-id slug" in e
                            for e in errs), errs)

    def test_correction_repo_list_named_error(self):
        evt = correction("c1", "app", "MERGED")
        evt["repo"] = ["app"]
        errs = S.validate_event(evt)
        self.assertTrue(any("correction: repo must be a binding-id slug "
                            "(^[a-z][a-z0-9-]*$) or null" in e
                            for e in errs), errs)

    def test_correction_repo_empty_string_named_error(self):
        evt = correction("c1", "app", "MERGED")
        evt["repo"] = ""
        evt["repo_reason"] = None
        errs = S.validate_event(evt)
        self.assertTrue(any("correction: repo must be a binding-id slug" in e
                            for e in errs), errs)

    def test_valid_slug_still_ok(self):
        self.assertEqual(
            S.validate_event(story_transition("t1", "GRILLED",
                                              repo="app-2")), [])
        self.assertEqual(S.validate_event(correction("c1", "app", "MERGED")),
                         [])

    def test_null_repo_with_reason_still_ok(self):
        self.assertEqual(S.validate_event(story_transition("t1", "GRILLED")),
                         [])
        self.assertEqual(S.validate_event(correction("c1", None, "X")), [])

    def test_sub_events_skips_non_string_repo(self):
        bad = story_transition("t1", "MERGED", repo="app")
        bad["repo"] = ["app"]
        good = story_transition("t2", "BUILD_GREEN", repo="app")
        got = M.sub_events([bad, good], STORY)
        self.assertEqual(got, [good])

    def test_fold_skips_non_string_repo_row(self):
        bad = story_transition("t1", "BUILD_GREEN", repo="app")
        bad["repo"] = ["app"]
        bad_corr = correction("c1", "app", "MERGED")
        bad_corr["repo"] = ""
        good = story_transition("t2", "BUILD_GREEN", repo="app")
        folded = M.fold_repo_status([bad, bad_corr, good], STORY)  # no raise
        self.assertEqual(folded, {"app": {"reached": "BUILD_GREEN",
                                          "sub_events": 1,
                                          "corrected": False}})


class TestInstalledLayoutImports(unittest.TestCase):
    """N4: multirepo.py uses package-relative imports, so it must also work
    in the documented INSTALLED layout (`factory/tools/`), not just from the
    repo root. Simulate that layout in a temp dir on sys.path and import
    `factory.tools.multirepo`."""

    MODULES = ("ledger_schema.py", "state_machine.py", "multirepo.py")

    def test_import_under_simulated_installed_layout(self):
        import importlib
        import shutil
        import sys
        src_dir = os.path.dirname(os.path.abspath(S.__file__))
        with tempfile.TemporaryDirectory(prefix="installed-layout-") as tmp:
            pkg = os.path.join(tmp, "factory")
            tools_pkg = os.path.join(pkg, "tools")
            os.makedirs(tools_pkg)
            for init in (os.path.join(pkg, "__init__.py"),
                         os.path.join(tools_pkg, "__init__.py")):
                open(init, "w").close()
            for name in self.MODULES:
                shutil.copy(os.path.join(src_dir, name),
                            os.path.join(tools_pkg, name))
            sys.path.insert(0, tmp)
            loaded = []
            try:
                mod = importlib.import_module("factory.tools.multirepo")
                loaded = [m for m in sys.modules
                          if m.startswith("factory.tools") or m == "factory"]
                # basic calls work through the installed-layout import
                self.assertEqual(mod.fold_repo_status([], STORY), {})
                self.assertEqual(mod.validate_pin("app@abc1234"), [])
                self.assertTrue(mod.validate_pin("nope"))
            finally:
                sys.path.remove(tmp)
                for name in loaded:
                    sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
