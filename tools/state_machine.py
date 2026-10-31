#!/usr/bin/env python3
"""tools/state_machine.py — pipeline states + transition rules (extracted).

The ported current set: 19 linear pipeline states + BLOCKED (a flag, not a
column — it resumes at the originating state). Evidence keys are the
controller's required inputs per target state; values are strings as passed
`k=v` on a transition request and persisted into the transition event's
check_results. The controller refuses a transition without its evidence —
it never invents values.

Pure declarative data + order helpers; the enforcing controller ports at
S2. Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

STATES = [
    "BACKLOG", "GRILLED", "TEST_PLAN", "SPEC_FROZEN", "PLANNED",
    "GATE_PASSED", "PLAN_APPROVED", "STORYBOOK_APPROVAL",
    "CODING", "BUILD_GREEN", "DRAFT_OPENED", "REVIEWING", "FIXING",
    "FULL_TESTS_GREEN", "READY_FOR_REVIEW", "MERGE_ELIGIBLE", "MERGED",
    "LIVE_PROVEN", "CLOSED", "BLOCKED",
]
LINEAR = [s for s in STATES if s != "BLOCKED"]

# FIXING may re-enter only from the post-review states (a FAIL verdict
# routes back to FIXING) — never from pre-review states, which would skip
# every earlier gate.
FIXING_REENTRY_FROM = frozenset({"FULL_TESTS_GREEN", "READY_FOR_REVIEW",
                                 "MERGE_ELIGIBLE"})

# STORYBOOK_APPROVAL is UI-only: PLAN_APPROVED → CODING without it is legal
# ONLY when the story declares non-UI. Missing/ambiguous declaration is
# treated as UI — fail closed.
UI_SKIP = ("PLAN_APPROVED", "CODING")

# The controller-enforced ownership gate — the first gated transition: an
# assignment/claim must exist before CODING.
CLAIM_GATE = "CODING"

# Review-request mechanism domain (S3.5b cutover): NEW DRAFT_OPENED
# transitions accept ONLY the owned review lane. The historical values
# remain readable in ledger history (the fold never re-judges a recorded
# transition) but are refused as new evidence.
REVIEW_MECHANISM = "review-lane"
REVIEW_MECHANISMS = (REVIEW_MECHANISM,)
HISTORICAL_REVIEW_MECHANISMS = ("ready-push", "draft-mention-opportunistic")

# Required evidence per target state (empty tuple = none). BACKLOG has no
# inbound transition.
EVIDENCE_KEYS: dict[str, tuple[str, ...]] = {
    "GRILLED": (),
    "TEST_PLAN": (),
    "SPEC_FROZEN": ("spec_path", "spec_freeze_sha"),
    "PLANNED": (),
    "GATE_PASSED": (),
    "PLAN_APPROVED": ("verdict",),
    "STORYBOOK_APPROVAL": (),
    "CODING": (),
    "BUILD_GREEN": ("commit_sha", "build_result", "named_tests_result"),
    "DRAFT_OPENED": ("pr_number", "review_request_mechanism"),
    "REVIEWING": ("review_record", "review_record_sha", "review_head_sha"),
    "FIXING": (),
    "FULL_TESTS_GREEN": ("projects", "tests_passed", "tests_failed",
                         "tests_skipped", "exclusions", "exit_code"),
    "READY_FOR_REVIEW": ("internal_verdict", "threads_state",
                         "fix_pushes", "r1_arrival",
                         "review_record", "review_head_sha"),
    "MERGE_ELIGIBLE": ("tested_sha", "head_sha", "unresolved_threads",
                       "review_record"),
    "MERGED": ("merged_sha", "merged_at", "pr_number"),
    "LIVE_PROVEN": ("live_proof_evidence",),
    "CLOSED": ("doc_sync", "closeout_checklist"),
    "BLOCKED": ("reason",),
}

# Documented per-key format expectations (evidence values are strings).
EVIDENCE_FORMATS: dict[str, str] = {
    "spec_freeze_sha": "7-40 hex; must resolve in the repo object DB",
    "commit_sha": "7-40 hex; must resolve in the repo object DB",
    "merged_sha": "7-40 hex; must resolve in the repo object DB",
    "tested_sha": "must equal head_sha (prefix match)",
    "head_sha": "current PR/story head SHA",
    "pr_number": "digits",
    "verdict": "PASS | PASS-WITH-NITS",
    "internal_verdict": "PASS | PASS-WITH-NITS",
    "build_result": "green",
    "named_tests_result": "green",
    "exit_code": "0",
    "tests_failed": "0",
    "unresolved_threads": "0",
    "threads_state": "all-resolved | watchdog-exception",
    "review_request_mechanism": "review-lane",
    "review_record": "repo-relative review record directory containing "
                     "review.json",
    "review_record_sha": "sha256 of review.json",
    "review_head_sha": "7-40 hex; the record's pinned head",
    "live_proof_evidence": "existing path or http(s) URL",
    "fix_pushes": "digit count; <=1 where r1_arrival == both-pre-edit "
                  "(target, not safety constraint)",
    "r1_arrival": "both-pre-edit | other",
}

# One-line summaries of the controller-enforced checks per target state
# (descriptive names — no numeric gate ids).
CHECK_SUMMARY: dict[str, str] = {
    "GRILLED": "story.md exists; Given/When/Then acceptance criteria; "
               "<=6 top-level ACs; one structural signal",
    "TEST_PLAN": "story.md re-checked; DEV and QA grill records exist, each "
                 "signed by its persona owner; >=80% correct; zero major "
                 "misunderstandings; signer/author overlap logged (never "
                 "blocks)",
    "SPEC_FROZEN": "test-plan.md committed at spec_freeze_sha; canonical "
                   "story id (next-in-sequence unless sequence_reason); "
                   "spec file exists; spec_freeze_sha resolves in git; "
                   "branch convention story/<id>-<slug> when supplied; "
                   "spec-freeze human approval (answered human_gate "
                   "gate=spec_freeze, or an allowlisted human via "
                   "approved_by)",
    "PLANNED": "SPEC_FROZEN reached; impl-plan contract block present and "
               "validates; planner lane closed dispatch, or a lane-skip "
               "deviation with human approval",
    "GATE_PASSED": "plan-gate exit 0 (run live)",
    "PLAN_APPROVED": "plan-review verdict recorded; verdict in {PASS, "
                     "PASS-WITH-NITS}; plan loops <=2",
    "STORYBOOK_APPROVAL": "answered human_gate gate=visual signed by an "
                          "allowlisted human",
    "CODING": "PLAN_APPROVED reached; claim (assignment) exists; non-UI "
              "skip requires the declared non-UI line",
    "BUILD_GREEN": "commit_sha resolves; build green; named tests green; "
                   "coder lane closed dispatch or lane-skip deviation",
    "DRAFT_OPENED": "pr_number recorded; review_request_mechanism is "
                    "review-lane (ONE authenticated request); no duplicate "
                    "DRAFT_OPENED",
    "REVIEWING": "DRAFT_OPENED reached; a valid review record at the pinned "
                 "head (path, sha256, head)",
    "FIXING": "REVIEWING reached (or post-review reentry)",
    "FULL_TESTS_GREEN": "exact suite membership (projects, skip/exclusion "
                        "reasons, exit code); exit code 0; failed 0",
    "READY_FOR_REVIEW": "internal verdict in {PASS, PASS-WITH-NITS}; "
                        "threads all resolved OR an "
                        "external-review-unavailable watchdog exception "
                        "logged (absence is never recorded as PASS); <=1 "
                        "fix push where both first-round sets arrived "
                        "pre-edit; a valid review record at the pinned head "
                        "(record-tail commits allowed) with no open "
                        "blocker/major findings",
    "MERGE_ELIGIBLE": "tested SHA == current head; verdict eligible; no "
                      "logic-touched nits; external findings dispositioned; "
                      "external-unique findings attributed when "
                      "instrumentation present; zero unresolved threads; "
                      "human merge approval recorded; a valid review record "
                      "at the current head (record-tail commits allowed) "
                      "with no open blocker/major findings",
    "MERGED": "merge evidence (merged_sha + merged_at + pr_number)",
    "LIVE_PROVEN": "live-proof evidence path/URL recorded",
    "CLOSED": "doc-sync obligations checked; closeout checklist complete; "
              "outcome recorded",
    "BLOCKED": "any state; reason required; resumes at the originating state",
}


def next_state(state: str) -> str | None:
    """The next state in linear order (None at CLOSED / BLOCKED / unknown)."""
    if state not in LINEAR:
        return None
    i = LINEAR.index(state)
    return LINEAR[i + 1] if i + 1 < len(LINEAR) else None


def order_ok(cur: str, to: str, non_ui_declared: bool = False) -> bool:
    """True when cur → to is a legal linear move.

    Legal: next-in-sequence; FIXING reentry from the post-review states;
    the UI skip PLAN_APPROVED → CODING when the story declares non-UI
    (missing/ambiguous declaration ⇒ treated as UI — fail closed).
    BLOCKED moves are handled by the controller, not here.
    """
    if cur not in LINEAR or to not in LINEAR:
        return False
    if LINEAR.index(to) == LINEAR.index(cur) + 1:
        return True
    if to == "FIXING" and cur in FIXING_REENTRY_FROM:
        return True
    if (cur, to) == UI_SKIP and non_ui_declared:
        return True
    return False


def blocked_origin(transitions: list[dict]) -> str | None:
    """From-state of the last BLOCKED transition, if nothing after it
    resumed. `transitions` = transition events in ledger order.
    None when not blocked. Repo-tagged sub-events are excluded: they are
    per-repo moves, so they never set or resume the story-level block."""
    story = [t for t in transitions if not t.get("repo")]
    last_block_idx = None
    for i, t in enumerate(story):
        if t.get("to_state") == "BLOCKED":
            last_block_idx = i
    if last_block_idx is None:
        return None
    if len(story) > last_block_idx + 1:
        return None  # resumed
    return story[last_block_idx].get("from_state")


def resume_ok(origin: str | None, to: str) -> bool:
    """True when a move out of BLOCKED targets the originating state
    (or no block origin is known)."""
    return origin is None or origin == to
