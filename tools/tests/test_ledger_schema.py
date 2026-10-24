#!/usr/bin/env python3
"""Tests for tools/ledger_schema.py — per-type validation + ledger invariants."""
from __future__ import annotations

import copy
import unittest

from tools import ledger_schema as S

STORY = "STORY-042"
SHA40 = "a" * 40
SHA40_B = "b" * 40
DIGEST64 = "c" * 64


def env(eid: str, etype: str) -> dict:
    return {"event": etype, "event_id": eid,
            "recorded_at": "2026-10-24T00:00:00Z", "provenance": "live"}


def _null_reasons(evt: dict, fields) -> None:
    for f in fields:
        evt[f] = None
        evt[f + "_reason"] = "not available in this test"


def valid_assignment(op: str = "claim", eid: str = "assignment-1") -> dict:
    evt = env(eid, "assignment")
    evt.update({"story_id": STORY, "op": op})
    if op == "claim":
        evt.update({"owner": "worker-1", "host": "host-a",
                    "session": "sess-1", "start_sha": "0" * 40,
                    "claimed_at": "2026-10-24T00:00:00Z"})
    elif op == "handover":
        evt.update({"from": "worker-1", "to": "worker-2",
                    "reason": "shift change", "flagged": False})
    elif op == "release":
        evt.update({"owner": "worker-1"})
    return evt


def valid_dispatch(eid: str = f"{STORY}-coder-1-1700000000",
                   ended: bool = True) -> dict:
    evt = env(eid, "dispatch")
    evt.update({
        "story_id": STORY, "role": "coder",
        "requested_model": "model-x", "reported_model": "model-x",
        "thinking_setting": "medium", "session_id": "sess-9",
        "parent_session_id": "parent-1", "attempt": 1,
        "started_at": "2026-10-24T00:00:00Z",
        "ended_at": "2026-10-24T01:00:00Z" if ended else None,
        "outcome": "completed" if ended else None,
        "prompt_version": "v1.0.0", "worktree": "/tmp/wt",
        "model_unverified": False,
    })
    if not ended:
        evt["ended_at_reason"] = "dispatch in flight (pre-spawn record)"
        evt["outcome_reason"] = "dispatch in flight"
    for f in S.NULLABLE["dispatch"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_usage(eid: str = "usage-1") -> dict:
    evt = env(eid, "usage")
    evt.update({"dispatch_id": f"{STORY}-coder-1-1700000000",
                "input_tokens": 100, "output_tokens": 50,
                "cached_input_tokens": 10, "thinking_tokens": 5,
                "token_semantics": "provider-reported",
                "usage_source": "harvest"})
    for f in S.NULLABLE["usage"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_stage(eid: str = "stage-1") -> dict:
    evt = env(eid, "stage")
    evt.update({"story_id": STORY, "stage": "build",
                "started_at": "2026-10-24T00:00:00Z",
                "ended_at": "2026-10-24T01:00:00Z",
                "commit_sha": "0" * 40, "tests_passed": 10,
                "tests_failed": 0, "tests_skipped": 0, "tests_excluded": 0,
                "evidence_path": f"stories/{STORY}/evidence/build.log"})
    for f in S.NULLABLE["stage"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_finding(cls: str = "reasoning", eid: str = "finding-1",
                  fid: str = "F-1", source: str = "external",
                  story: str = STORY) -> dict:
    evt = env(eid, "finding")
    evt.update({"finding_id": fid, "story_id": story, "source": source,
                "reviewed_sha": SHA40, "introduced_sha": SHA40_B,
                "class": cls, "severity": "major",
                "disposition": "accepted", "logic_touched": False,
                "overlap_id": None, "fixer": "fixer-1",
                "fix_sha": "d" * 40, "validation": "tests green",
                "evidence_path": f"stories/{story}/evidence/f1.md"})
    evt["overlap_id_reason"] = "no overlapping finding"
    for f in S.NULLABLE["finding"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_loop(eid: str = "loop-1") -> dict:
    evt = env(eid, "loop")
    evt.update({"story_id": STORY, "stage": "code_review", "loop_n": 1,
                "class": "reasoning",
                "root_cause_one_line": "missed null check on parser input"})
    return evt


def valid_recovery(eid: str = "recovery-1") -> dict:
    evt = env(eid, "recovery")
    evt.update({"story_id": STORY, "original_assignment": "model-y",
                "fallback_reason": "tier capacity exhausted",
                "actual_model": "model-x", "remaining_scope": "full",
                "consumed_loop_count": 1, "additional_cost": "0 tokens",
                "human_intervention": False})
    for f in S.NULLABLE["recovery"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_transition(eid: str = "transition-1", to: str = "GRILLED",
                     frm: str = "BACKLOG",
                     repo: str | None = None) -> dict:
    evt = env(eid, "transition")
    evt.update({"story_id": STORY, "from_state": frm, "to_state": to,
                "requested_by": "orchestrator", "checks_run": ["story_md"],
                "check_results": {"story_md": {"ok": True, "detail": "ok"}},
                "approved_by": None, "repo": repo})
    evt["approved_by_reason"] = "no human gate required"
    evt["repo_reason"] = None if repo else "story-level transition"
    return evt


def valid_human_gate(eid: str = "human_gate-1",
                     gate: str = "merge") -> dict:
    """An ANSWERED gate: decision + answered_by are part of the answer."""
    evt = env(eid, "human_gate")
    evt.update({"story_id": STORY, "gate": gate,
                "question": "approve merge?",
                "asked_at": "2026-10-24T00:00:00Z",
                "answered_at": "2026-10-24T01:00:00Z",
                "decision": "approved",
                "answered_by": "an allowlisted human approver"})
    for f in S.NULLABLE["human_gate"]:
        evt.setdefault(f + "_reason", None)
    return evt


def pending_human_gate(eid: str = "human_gate-p",
                       gate: str = "merge") -> dict:
    """A PENDING gate: answered_at, decision, answered_by all null with
    their sibling reasons."""
    evt = valid_human_gate(eid=eid, gate=gate)
    evt["answered_at"] = None
    evt["answered_at_reason"] = "awaiting human response"
    evt["decision"] = None
    evt["decision_reason"] = "gate not yet answered"
    evt["answered_by"] = None
    evt["answered_by_reason"] = "gate not yet answered"
    return evt


def valid_outcome(eid: str = "outcome-1") -> dict:
    evt = env(eid, "outcome")
    evt.update({"story_id": STORY, "merged_sha": "e" * 40,
                "merged_at": "2026-10-24T02:00:00Z",
                "live_proof_status": "proven",
                "observation_end": "2026-10-31T00:00:00Z",
                "escaped_defects": 0,
                "totals": {"input_tokens": 100, "output_tokens": 50},
                "recommendation": "none"})
    for f in S.NULLABLE["outcome"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_correction(eid: str = "correction-1",
                     repo: str | None = None) -> dict:
    evt = env(eid, "correction")
    evt.update({"story_id": STORY,
                "contradicted_claim": "tests_passed=12",
                "corrected_value": "tests_passed=10",
                "source_citation": "ledger usage row 3",
                "corrected_artifact": f"stories/{STORY}/state.md",
                "repo": repo})
    for f in S.NULLABLE["correction"]:
        evt.setdefault(f + "_reason", None)
    evt["repo_reason"] = None if repo else "story-level correction"
    return evt


def valid_deviation(eid: str = "deviation-1") -> dict:
    evt = env(eid, "deviation")
    evt.update({"story_id": STORY, "contract_field": "files",
                "planned": "a.py", "actual": "a.py,b.py",
                "justification": "helper split",
                "approved_by": "an allowlisted human approver"})
    for f in S.NULLABLE["deviation"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_review_context(eid: str = "rc-1",
                         role: str = "behavior_reviewer",
                         reviewed_sha: str = SHA40,
                         story: str = STORY) -> dict:
    evt = env(eid, "review_context")
    evt.update({"story_id": story, "review_role": role,
                "reviewed_sha": reviewed_sha, "base_sha": SHA40_B,
                "context_manifest_path":
                    f"stories/{story}/review/manifest.md",
                "context_manifest_digest": DIGEST64,
                "prompt_version": "v1.0.0",
                "dispatch_id": f"{story}-behavior_reviewer-1-1700000000",
                "delivery_surface": "dispatch-package",
                "requested_at": "2026-10-24T00:00:00Z"})
    for f in S.NULLABLE["review_context"]:
        evt.setdefault(f + "_reason", None)
    return evt


def valid_review_attribution(eid: str = "ra-1",
                             classification: str = "MISSED_WITH_CONTEXT",
                             fid: str = "F-1", story: str = STORY,
                             internal: str = "rc-int",
                             external: str = "rc-ext") -> dict:
    evt = env(eid, "review_attribution")
    evt.update({"story_id": story, "finding_id": fid,
                "internal_context_event_id": internal,
                "external_context_event_id": external,
                "classification": classification,
                "classified_by": "orchestrator",
                "evidence_path": f"stories/{story}/evidence/attr.md"})
    return evt


VALID_BUILDERS = {
    "assignment": valid_assignment,
    "dispatch": valid_dispatch,
    "usage": valid_usage,
    "stage": valid_stage,
    "finding": valid_finding,
    "loop": valid_loop,
    "recovery": valid_recovery,
    "transition": valid_transition,
    "human_gate": valid_human_gate,
    "outcome": valid_outcome,
    "correction": valid_correction,
    "deviation": valid_deviation,
    "review_context": valid_review_context,
    "review_attribution": valid_review_attribution,
}


class TestMinimalValidEvents(unittest.TestCase):
    def test_every_type_has_a_valid_event(self):
        self.assertEqual(len(VALID_BUILDERS), len(S.EVENT_TYPES))
        for etype in S.EVENT_TYPES:
            with self.subTest(event_type=etype):
                errs = S.validate_event(VALID_BUILDERS[etype]())
                self.assertEqual(errs, [])

    def test_all_handover_ops_validate(self):
        for op in S.ASSIGNMENT_OPS:
            with self.subTest(op=op):
                self.assertEqual(
                    S.validate_event(valid_assignment(op=op, eid=f"a-{op}")),
                    [])


class TestEnvelope(unittest.TestCase):
    def test_unknown_event_type(self):
        errs = S.validate_event({"event": "nope", "event_id": "x",
                                 "recorded_at": "t", "provenance": "live"})
        self.assertEqual(errs, ["unknown event type 'nope'"])

    def test_bad_provenance(self):
        evt = valid_loop()
        evt["provenance"] = "reconstructed"
        errs = S.validate_event(evt)
        self.assertTrue(any("provenance must be live|backfilled" in e
                            for e in errs), errs)

    def test_missing_envelope_field(self):
        evt = valid_loop()
        del evt["recorded_at"]
        errs = S.validate_event(evt)
        self.assertIn("loop: missing envelope field `recorded_at`", errs)

    def test_recorded_at_must_be_utc_timestamp(self):
        for bad in ("not-a-date", 123, "2026-10-24 00:00:00",
                    "2026-10-24T00:00:00+00:00"):
            evt = valid_loop()
            evt["recorded_at"] = bad
            errs = S.validate_event(evt)
            self.assertIn("loop: recorded_at must be a UTC timestamp "
                          "(YYYY-MM-DDTHH:MM:SSZ)", errs)

    def test_recorded_at_valid_timestamp_ok(self):
        self.assertEqual(S.validate_event(valid_loop()), [])

    def test_recorded_at_falsy_reports_missing_only(self):
        evt = valid_loop()
        evt["recorded_at"] = ""
        errs = S.validate_event(evt)
        self.assertIn("loop: missing envelope field `recorded_at`", errs)
        self.assertFalse(any("UTC timestamp" in e for e in errs), errs)

    def test_event_id_must_be_a_string(self):
        # truthy non-string ids would bypass the string-keyed uniqueness
        # index in validate_events — rejected at the envelope boundary.
        for bad in (1, 1.5, ["loop-1"], {"id": "loop-1"}, True):
            evt = valid_loop()
            evt["event_id"] = bad
            errs = S.validate_event(evt)
            self.assertIn("loop: event_id must be a string", errs)

    def test_event_id_falsy_reports_missing_only(self):
        for bad in ("", None, 0):
            evt = valid_loop()
            evt["event_id"] = bad
            errs = S.validate_event(evt)
            self.assertIn("loop: missing envelope field `event_id`", errs)
            self.assertNotIn("loop: event_id must be a string", errs)

    def test_string_event_id_unaffected(self):
        self.assertEqual(S.validate_event(valid_loop()), [])


class TestRequiredAndNullReason(unittest.TestCase):
    def test_missing_required_field(self):
        evt = valid_stage()
        del evt["tests_passed"]
        del evt["tests_passed_reason"]  # it was auto-set to None
        errs = S.validate_event(evt)
        self.assertIn("stage: missing required field `tests_passed`", errs)

    def test_null_without_reason_value(self):
        evt = valid_stage()
        evt["tests_passed"] = None
        evt["tests_passed_reason"] = ""
        errs = S.validate_event(evt)
        self.assertTrue(any("`tests_passed` is null but `tests_passed_reason`"
                            in e for e in errs), errs)

    def test_null_without_reason_key(self):
        evt = valid_stage()
        evt["tests_passed"] = None
        del evt["tests_passed_reason"]
        errs = S.validate_event(evt)
        self.assertTrue(any("requires sibling" in e for e in errs), errs)

    def test_reason_present_while_field_has_value(self):
        evt = valid_stage()
        evt["tests_passed_reason"] = "should be null"
        errs = S.validate_event(evt)
        self.assertTrue(any("`tests_passed` has a value so "
                            "`tests_passed_reason` must be null" in e
                            for e in errs), errs)

    def test_reason_for_non_nullable_field_is_violation(self):
        evt = valid_transition()
        evt["story_id_reason"] = "no reason allowed here"
        errs = S.validate_event(evt)
        self.assertTrue(any("`story_id_reason` present but `story_id` is "
                            "not nullable" in e for e in errs), errs)

    def test_reason_for_non_nullable_assignment_op_field_is_violation(self):
        evt = valid_assignment(op="claim")
        evt["owner_reason"] = "no reason allowed here"
        errs = S.validate_event(evt)
        self.assertTrue(any("`owner_reason` present but `owner` is not "
                            "nullable" in e for e in errs), errs)

    def test_exempt_value_fields_never_flagged(self):
        evt = valid_recovery()  # carries fallback_reason as a value field
        self.assertEqual(S.validate_event(evt), [])

    def test_non_nullable_required_field_null_rejected(self):
        evt = valid_assignment(op="claim")
        evt["owner"] = None
        errs = S.validate_event(evt)
        self.assertIn("assignment: required field `owner` must not be null "
                      "(not nullable in this event type)", errs)

    def test_non_nullable_usage_input_tokens_null_rejected(self):
        evt = valid_usage()
        evt["input_tokens"] = None
        errs = S.validate_event(evt)
        self.assertIn("usage: required field `input_tokens` must not be "
                      "null (not nullable in this event type)", errs)

    def test_nullable_required_field_null_with_reason_ok(self):
        evt = valid_dispatch(ended=False)  # ended_at null + reason
        self.assertEqual(S.validate_event(evt), [])


class TestAssignment(unittest.TestCase):
    def test_missing_op(self):
        evt = valid_assignment()
        del evt["op"]
        errs = S.validate_event(evt)
        self.assertIn("assignment: missing required field `op`", errs)

    def test_unknown_op(self):
        evt = valid_assignment()
        evt["op"] = "seize"
        errs = S.validate_event(evt)
        self.assertTrue(any("op 'seize' not in" in e for e in errs), errs)

    def test_claim_requires_exactly_one_start_pin(self):
        evt = valid_assignment(op="claim")
        del evt["start_sha"]
        errs = S.validate_event(evt)
        self.assertIn("assignment: claim requires start_sha or start_shas "
                      "(exactly one)", errs)
        self.assertEqual(S.validate_event(valid_assignment(op="claim")), [])

    def test_handover_flagged_must_be_bool(self):
        evt = valid_assignment(op="handover", eid="a-h")
        evt["flagged"] = "yes"
        errs = S.validate_event(evt)
        self.assertTrue(any("`flagged` must be an explicit boolean" in e
                            for e in errs), errs)

    def test_claim_start_sha_must_be_hex_sha(self):
        for bad in (None, "", "not-a-sha", "ABCDEF1", "0" * 41, 12345):
            evt = valid_assignment(op="claim", eid=f"cs-{abs(hash(str(bad)))}")
            evt["start_sha"] = bad
            errs = S.validate_event(evt)
            self.assertIn("assignment: claim start_sha must be a 7-40 "
                          "lowercase hex SHA", errs)

    def test_claim_start_sha_valid_passes(self):
        for good in ("0" * 40, "abc1234", "deadbeef" * 5):
            evt = valid_assignment(op="claim",
                                   eid=f"cs-ok-{abs(hash(good))}")
            evt["start_sha"] = good
            self.assertEqual(S.validate_event(evt), [])


class TestDispatch(unittest.TestCase):
    def test_bad_event_id_format(self):
        evt = valid_dispatch(eid="not-a-dispatch-id")
        errs = S.validate_event(evt)
        self.assertTrue(any("does not match "
                            "<story>-<role>-<attempt>-<unix>" in e
                            for e in errs), errs)

    def test_reported_model_null_requires_model_unverified(self):
        evt = valid_dispatch()
        evt["reported_model"] = None
        evt["reported_model_reason"] = "runtime cannot confirm"
        evt["model_unverified"] = False
        errs = S.validate_event(evt)
        self.assertTrue(any("model_unverified=true" in e for e in errs), errs)

    def test_reported_model_null_with_unverified_ok(self):
        evt = valid_dispatch()
        evt["reported_model"] = None
        evt["reported_model_reason"] = "runtime cannot confirm"
        evt["model_unverified"] = True
        self.assertEqual(S.validate_event(evt), [])


class TestFinding(unittest.TestCase):
    def test_bad_enums(self):
        for field, bad in (("source", "vendor-x"), ("class", "style"),
                           ("disposition", "maybe")):
            evt = valid_finding()
            evt[field] = bad
            errs = S.validate_event(evt)
            self.assertTrue(any(e.startswith(f"finding: {field} ")
                                for e in errs), (field, errs))

    def test_logic_touched_must_be_bool(self):
        evt = valid_finding()
        evt["logic_touched"] = "false"
        errs = S.validate_event(evt)
        self.assertIn("finding: logic_touched must be an explicit boolean",
                      errs)

    def test_nit_with_logic_touched_forbidden(self):
        evt = valid_finding(cls="nit")
        evt["logic_touched"] = True
        errs = S.validate_event(evt)
        self.assertTrue(any("NEVER class nit" in e for e in errs), errs)
        self.assertTrue(any("logic_touched=true + class=nit is forbidden"
                            in e for e in errs), errs)

    def test_nit_requires_fix_sha_or_validation(self):
        evt = valid_finding(cls="nit")
        evt["fix_sha"] = None
        evt["fix_sha_reason"] = "no fix commit yet"
        evt["validation"] = None
        evt["validation_reason"] = "no validation yet"
        errs = S.validate_event(evt)
        self.assertTrue(any("nit requires verifying diff/commit" in e
                            for e in errs), errs)

    def test_valid_nit(self):
        evt = valid_finding(cls="nit")
        evt["logic_touched"] = False
        self.assertEqual(S.validate_event(evt), [])


class TestOtherTypes(unittest.TestCase):
    def test_human_gate_bad_kind(self):
        evt = valid_human_gate(gate="vibes")
        errs = S.validate_event(evt)
        self.assertTrue(any("gate 'vibes' not in" in e for e in errs), errs)

    # -- human_gate pending/answered contract
    def test_human_gate_answered_valid(self):
        for decision in S.HUMAN_GATE_DECISIONS:
            evt = valid_human_gate()
            evt["decision"] = decision
            with self.subTest(decision=decision):
                self.assertEqual(S.validate_event(evt), [])

    def test_human_gate_answered_missing_decision(self):
        evt = valid_human_gate()
        del evt["decision"]
        errs = S.validate_event(evt)
        self.assertIn("human_gate: missing required field `decision`", errs)

    def test_human_gate_answered_bad_decision_value(self):
        evt = valid_human_gate()
        evt["decision"] = "maybe"
        errs = S.validate_event(evt)
        self.assertIn("human_gate: an answered gate requires decision in "
                      "(approved, rejected)", errs)

    def test_human_gate_answered_missing_answered_by(self):
        evt = valid_human_gate()
        evt["answered_by"] = "   "
        errs = S.validate_event(evt)
        self.assertIn("human_gate: an answered gate requires answered_by "
                      "(the responding human)", errs)

    def test_human_gate_pending_valid(self):
        self.assertEqual(S.validate_event(pending_human_gate()), [])

    def test_human_gate_pending_must_not_carry_decision(self):
        evt = pending_human_gate()
        evt["decision"] = "approved"
        evt["decision_reason"] = None
        errs = S.validate_event(evt)
        self.assertIn("human_gate: a pending gate must not carry "
                      "decision/answered_by", errs)

    def test_human_gate_pending_must_not_carry_answered_by(self):
        evt = pending_human_gate()
        evt["answered_by"] = "some human"
        evt["answered_by_reason"] = None
        errs = S.validate_event(evt)
        self.assertIn("human_gate: a pending gate must not carry "
                      "decision/answered_by", errs)

    # -- dispatch event_id prefix must match the row's story/role/attempt
    def test_dispatch_id_prefix_mismatches(self):
        for label, eid in {
                "story": "STORY-099-coder-1-1700000000",
                "role": f"{STORY}-reviewer-1-1700000000",
                "attempt": f"{STORY}-coder-2-1700000000"}.items():
            evt = valid_dispatch(eid=eid)
            with self.subTest(mismatch=label):
                errs = S.validate_event(evt)
                self.assertTrue(
                    any("event_id must match the row's story/role/attempt"
                        in e for e in errs), errs)

    def test_dispatch_id_prefix_match_valid(self):
        self.assertEqual(S.validate_event(valid_dispatch()), [])

    def test_dispatch_id_bad_shape_not_double_reported(self):
        evt = valid_dispatch(eid="not-a-dispatch-id")
        errs = S.validate_event(evt)
        self.assertEqual(len([e for e in errs if "event_id" in e]), 1)
        self.assertTrue(any("does not match" in e for e in errs), errs)

    def test_transition_checks_types(self):
        evt = valid_transition()
        evt["checks_run"] = "story_md"
        evt["check_results"] = ["not", "an", "object"]
        errs = S.validate_event(evt)
        self.assertIn("transition: checks_run must be an array of strings",
                      errs)
        self.assertIn("transition: check_results must be an object", errs)

    def test_transition_checks_run_elements_must_be_strings(self):
        evt = valid_transition()
        evt["checks_run"] = [None, 42]
        errs = S.validate_event(evt)
        self.assertIn("transition: checks_run must be an array of strings",
                      errs)

    def test_loop_empty_root_cause(self):
        evt = valid_loop()
        evt["root_cause_one_line"] = "   "
        errs = S.validate_event(evt)
        self.assertTrue(any("root_cause_one_line must be a non-empty string"
                            in e for e in errs), errs)

    def test_review_context_bad_role(self):
        evt = valid_review_context(role="vendor-review")
        errs = S.validate_event(evt)
        self.assertTrue(any("review_role 'vendor-review' not in" in e
                            for e in errs), errs)

    def test_review_context_bad_surface(self):
        evt = valid_review_context()
        evt["delivery_surface"] = "email-thread"
        errs = S.validate_event(evt)
        self.assertTrue(any("delivery_surface must identify" in e
                            for e in errs), errs)

    def test_review_context_short_sha(self):
        evt = valid_review_context()
        evt["reviewed_sha"] = "abc123"
        errs = S.validate_event(evt)
        self.assertTrue(any("reviewed_sha must be a full lowercase "
                            "40-character" in e for e in errs), errs)

    def test_review_context_bad_digest(self):
        evt = valid_review_context()
        evt["context_manifest_digest"] = "deadbeef"
        errs = S.validate_event(evt)
        self.assertTrue(any("context_manifest_digest must be full lowercase "
                            "SHA-256" in e for e in errs), errs)

    def test_review_context_path_escaping(self):
        for bad in (f"stories/OTHER/{STORY}/manifest.md",
                    f"stories/{STORY}/../../etc/passwd",
                    "elsewhere/manifest.md"):
            evt = valid_review_context(eid=f"rc-{abs(hash(bad))}")
            evt["context_manifest_path"] = bad
            errs = S.validate_event(evt)
            self.assertTrue(any("context_manifest_path must stay under" in e
                                for e in errs), (bad, errs))

    def test_review_attribution_bad_classification(self):
        evt = valid_review_attribution(classification="SOMETHING_ELSE")
        errs = S.validate_event(evt)
        self.assertTrue(any("classification 'SOMETHING_ELSE' not in" in e
                            for e in errs), errs)


class TestLedgerInvariants(unittest.TestCase):
    def test_duplicate_non_dispatch_ids(self):
        a, b = valid_loop(eid="loop-1"), valid_loop(eid="loop-1")
        b["root_cause_one_line"] = "different cause"
        errs = S.validate_events([a, b])
        self.assertIn("duplicate event_id 'loop-1'", errs)

    def test_non_string_event_id_cannot_bypass_uniqueness(self):
        # regression: two loop events sharing event_id=1 used to slip past
        # the string-only duplicate index — both are now flagged.
        a, b = valid_loop(), valid_loop()
        a["event_id"] = 1
        b["event_id"] = 1
        b["root_cause_one_line"] = "different cause"
        errs = S.validate_events([a, b])
        self.assertEqual(errs.count("loop: event_id must be a string"), 2)

    def test_dispatch_three_rows(self):
        rows = [valid_dispatch(ended=False),
                valid_dispatch(ended=True), valid_dispatch(ended=True)]
        errs = S.validate_events(rows)
        self.assertTrue(any("has 3 rows" in e for e in errs), errs)

    def test_dispatch_double_ended(self):
        errs = S.validate_events([valid_dispatch(ended=True),
                                  valid_dispatch(ended=True)])
        self.assertTrue(any("re-started after end" in e for e in errs), errs)

    def test_dispatch_orphan_second_row(self):
        errs = S.validate_events([valid_dispatch(ended=False),
                                  valid_dispatch(ended=False)])
        self.assertTrue(any("second row lacks ended_at" in e for e in errs),
                        errs)

    def test_dispatch_reversed_order(self):
        # completion first, then an ended_at-null row: the null second row
        # can only be a pre-spawn row, so this is refused (an ended
        # dispatch can never be re-started by a stale notification).
        errs = S.validate_events([valid_dispatch(ended=True),
                                  valid_dispatch(ended=False)])
        self.assertTrue(any("second row lacks ended_at" in e
                            or "completion row precedes" in e
                            for e in errs), errs)

    def test_valid_prespawn_completion_pair(self):
        errs = S.validate_events([valid_dispatch(ended=False),
                                  valid_dispatch(ended=True)])
        self.assertEqual(errs, [])

    def test_single_backfilled_ended_dispatch_ok(self):
        evt = valid_dispatch()
        evt["provenance"] = "backfilled"
        self.assertEqual(S.validate_events([evt]), [])

    def test_single_live_completed_dispatch_rejected(self):
        errs = S.validate_events([valid_dispatch()])  # ended + live
        self.assertTrue(any("single completed row must be "
                            "provenance=backfilled" in e for e in errs), errs)

    def test_single_live_in_flight_dispatch_ok(self):
        self.assertEqual(S.validate_events([valid_dispatch(ended=False)]), [])

    def test_mixed_event_types_sharing_id_rejected(self):
        loop = valid_loop(eid="shared-1")
        dispatch = valid_dispatch(eid="shared-1")
        errs = S.validate_events([loop, dispatch])
        self.assertTrue(any("duplicate event_id 'shared-1' (spans multiple "
                            "event types)" in e for e in errs), errs)

    # -- review_attribution joins
    def _join_ledger(self, **overrides) -> list[dict]:
        internal = valid_review_context(eid="rc-int",
                                        role="behavior_reviewer")
        external = valid_review_context(eid="rc-ext",
                                        role="external_review")
        external["delivery_surface"] = "pr-full-diff"
        finding = valid_finding(fid="F-1", source="external")
        attr = valid_review_attribution(fid="F-1")
        ledger = [internal, external, finding, attr]
        for key, mutate in overrides.items():
            mutate(ledger)
        return ledger

    def test_attribution_join_valid(self):
        self.assertEqual(S.validate_events(self._join_ledger()), [])

    def test_attribution_internal_not_behavior_reviewer(self):
        def m(ledger):
            ledger[0]["review_role"] = "contract_reviewer"
        errs = S.validate_events(self._join_ledger(internal=m))
        self.assertTrue(any("behavior_reviewer review_context" in e
                            for e in errs), errs)

    def test_attribution_external_not_external_review(self):
        def m(ledger):
            ledger[1]["review_role"] = "contract_reviewer"
        errs = S.validate_events(self._join_ledger(external=m))
        self.assertTrue(any("external_review review_context" in e
                            for e in errs), errs)

    def test_attribution_story_mismatch(self):
        def m(ledger):
            ledger[0]["story_id"] = "STORY-099"
            ledger[0]["context_manifest_path"] = "stories/STORY-099/review/m.md"
        errs = S.validate_events(self._join_ledger(internal=m))
        self.assertTrue(any("internal context belongs to another story" in e
                            for e in errs), errs)

    def test_attribution_sha_mismatch_for_capability_classification(self):
        def m(ledger):
            ledger[0]["reviewed_sha"] = "f" * 40
        errs = S.validate_events(self._join_ledger(internal=m))
        self.assertTrue(any("require identical internal and external "
                            "snapshots" in e for e in errs), errs)

    def test_attribution_introduced_after_allows_sha_drift(self):
        def m(ledger):
            ledger[0]["reviewed_sha"] = "f" * 40
            ledger[3]["classification"] = "INTRODUCED_AFTER_INTERNAL"
        errs = S.validate_events(self._join_ledger(internal=m))
        self.assertFalse(any("identical internal and external" in e
                             for e in errs), errs)

    def test_attribution_missing_finding(self):
        def m(ledger):
            del ledger[2]
        errs = S.validate_events(self._join_ledger(finding=m))
        self.assertTrue(any("finding_id must reference a finding" in e
                            for e in errs), errs)

    def test_attribution_finding_from_internal_source(self):
        def m(ledger):
            ledger[2]["source"] = "internal"
        errs = S.validate_events(self._join_ledger(finding=m))
        self.assertTrue(any("external-source finding from the same story"
                            in e for e in errs), errs)

    # -- finding index keyed by (story_id, finding_id)
    def test_two_stories_sharing_finding_id_both_validate(self):
        # regression: finding ids are only unique WITHIN a story — two
        # stories each carrying an external F-1 must both attribute.
        other = "STORY-099"
        ledger = []
        for story, tag in ((STORY, "a"), (other, "b")):
            internal = valid_review_context(eid=f"rc-int-{tag}", story=story)
            external = valid_review_context(eid=f"rc-ext-{tag}", story=story,
                                            role="external_review")
            external["delivery_surface"] = "pr-full-diff"
            ledger += [internal, external,
                       valid_finding(eid=f"f-{tag}", fid="F-1", story=story),
                       valid_review_attribution(eid=f"ra-{tag}", fid="F-1",
                                                story=story,
                                                internal=f"rc-int-{tag}",
                                                external=f"rc-ext-{tag}")]
        self.assertEqual(S.validate_events(ledger), [])

    def test_attribution_referencing_another_story_finding(self):
        # no F-1 under the attributing story, but one exists elsewhere →
        # same-story violation, not a dangling reference.
        other = "STORY-099"
        ledger = self._join_ledger()
        del ledger[2]  # drop the local F-1 finding
        ledger.append(valid_finding(eid="f-other", fid="F-1", story=other))
        errs = S.validate_events(ledger)
        self.assertTrue(any("external-source finding from the same story"
                            in e for e in errs), errs)
        self.assertFalse(any("finding_id must reference a finding event"
                             in e for e in errs), errs)


class TestHelpers(unittest.TestCase):
    def test_events_for_and_current_state(self):
        ledger = [valid_transition(eid="t1", to="GRILLED"),
                  valid_transition(eid="t2", to="TEST_PLAN",
                                   frm="GRILLED"),
                  valid_loop(eid="l1")]
        self.assertEqual(S.current_state(ledger, STORY), "TEST_PLAN")
        self.assertIsNone(S.current_state(ledger, "STORY-099"))
        self.assertEqual(len(S.events_for(ledger, STORY)), 3)
        self.assertEqual(len(S.events_for(ledger, STORY, "transition")), 2)

    def test_latest_findings(self):
        f1 = valid_finding(fid="F-1", eid="f1")
        f1b = copy.deepcopy(f1)
        f1b.update({"event_id": "f1b", "disposition": "fixed"})
        f2 = valid_finding(fid="F-2", eid="f2")
        latest = S.latest_findings([f1, f2, f1b])
        self.assertEqual([f["finding_id"] for f in latest], ["F-1", "F-2"])
        self.assertEqual(latest[0]["disposition"], "fixed")

    def test_latest_findings_same_id_different_stories(self):
        # regression: the lifecycle key includes story_id — two stories
        # sharing F-1 both surface; same-story updates still fold.
        other = "STORY-099"
        f1 = valid_finding(fid="F-1", eid="f1")
        f1b = copy.deepcopy(f1)
        f1b.update({"event_id": "f1b", "disposition": "fixed"})
        g1 = valid_finding(fid="F-1", eid="g1", story=other)
        g1b = copy.deepcopy(g1)
        g1b.update({"event_id": "g1b", "disposition": "dismissed"})
        latest = S.latest_findings([f1, g1, f1b, g1b])
        self.assertEqual(len(latest), 2)
        by_story = {f["story_id"]: f for f in latest}
        self.assertEqual(by_story[STORY]["disposition"], "fixed")
        self.assertEqual(by_story[other]["disposition"], "dismissed")
        # story filter still narrows
        self.assertEqual(len(S.latest_findings([f1, g1, f1b, g1b],
                                               story_id=other)), 1)

    def test_utcnow_format(self):
        import re as _re
        self.assertRegex(S.utcnow(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_validate_events_empty(self):
        self.assertEqual(S.validate_events([]), [])


if __name__ == "__main__":
    unittest.main()
