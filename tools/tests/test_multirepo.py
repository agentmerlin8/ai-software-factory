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


if __name__ == "__main__":
    unittest.main()