#!/usr/bin/env python3
"""tools/gate_catalog.py — mechanical gate catalog (extracted).

Notes:
  * Slugs are descriptive — no numeric gate ids.
  * plan-gate and build-gate are MODES of one script (the pre-review
    gate).
  * Invocations are the INSTALLED control-home form (`factory/...`). In
    this kit repo the scripts live under `tools/` (e.g.
    `python3 tools/gates/pre_review_gate.py ...`) — see §Canonical
    layouts in kit-v0.1/architecture.md.
  * Mechanical gates run BEFORE any LLM review (mechanical-before-
    judgment law).
  * Fixtures policy: seeded-invalid fixtures must be rejected,
    valid-but-unusual ones accepted — a gate change is not deployed
    until the acceptance harness is green.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

GATES: tuple[dict[str, str], ...] = (
    {
        "slug": "digest-gate",
        "purpose": "Validates the context digest (completeness, anchors at "
                   "a pinned base SHA) before the planner dispatches on it",
        "script": "check_digest.py",
        "invocation": "python3 factory/tools/check_digest.py <STORY-ID> "
                      "[BASE_SHA]",
        "when": "After context acquisition, before the planner dispatch",
        "exit": "0 = planner may dispatch; non-zero = re-dispatch context "
                "acquisition, never hand-patch the digest",
    },
    {
        "slug": "plan-gate",
        "purpose": "Plan-lane mechanical checks: contract block schema, AC "
                   "coverage, cross-story collisions, anchor resolution, "
                   "seam verification, coverage set-diffs, snippet "
                   "annotations",
        "script": "pre_review_gate.py (plan mode)",
        "invocation": "python3 factory/gates/pre_review_gate.py plan "
                      "<story-dir> [--spec <path>] [--inflight auto|none]",
        "when": "PLANNED → GATE_PASSED (run live by the controller) and "
                "before any LLM plan review",
        "exit": "0 = pass; non-zero = findings printed, transition refused",
    },
    {
        "slug": "build-gate",
        "purpose": "Build-lane mechanical checks: manifest↔diff, AC↔test "
                   "discovery, pinned command execution, api-shape, UI DOM "
                   "assertions, fidelity checks",
        "script": "pre_review_gate.py (build mode)",
        "invocation": "python3 factory/gates/pre_review_gate.py build "
                      "<story-dir> [--base main] [--diff-file <name-status "
                      "file>] [--discovery <project>=<list-tests file>]... "
                      "[--run-discovery] [--run-verify]",
        "when": "At BUILD_GREEN, before any LLM code review",
        "exit": "0 = pass; non-zero = findings printed, transition refused",
    },
    {
        "slug": "gate-selftest",
        "purpose": "Gate acceptance harness — seeded-invalid fixtures must "
                   "be rejected, valid-but-unusual accepted",
        "script": "test_gate.py",
        "invocation": "python3 factory/gates/test_gate.py",
        "when": "CI and before any gate change deploys",
        "exit": "non-zero if any fixture behaves wrongly",
    },
)
