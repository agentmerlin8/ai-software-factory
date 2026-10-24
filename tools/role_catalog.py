#!/usr/bin/env python3
"""tools/role_catalog.py — role catalog + tier matrix (extracted).

Rules:
  * The matrix is static and complete — every canonical role resolves for
    every difficulty (S/M/L). Any role absent from a resolution is a
    process defect, counted (unrouted-dispatch census).
  * TIER → concrete model is PER-ENVIRONMENT adapter configuration
    (environments/*.toml) — never in this module.
  * The orchestrator runs as a lane (not a routed dispatch role).
  * Canonical spelling is snake_case; ALIASES canonicalize hyphen variants
    and historical spellings.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

DIFFICULTIES = ("S", "M", "L")
TIERS = ("T1", "T2", "T3")

# Canonical role → (purpose, (tier S, tier M, tier L)).
CANONICAL_ROLES: dict[str, tuple[str, tuple[str, str, str]]] = {
    "context_acquisition": (
        "Assemble the pinned, digest-validated context bundle for a story",
        ("T1", "T1", "T1")),
    "decomposer": (
        "Break an approved idea into sequenced, independently mergeable stories",
        ("T1", "T1", "T2")),
    "test_plan_generator": (
        "Author the story's test plan before code exists",
        ("T1", "T1", "T2")),
    "planner": (
        "Author the implementation plan (the implementation-plan author)",
        ("T2", "T2", "T3")),
    "plan_reviewer": (
        "Adversarially review the implementation plan against the spec",
        ("T2", "T2", "T3")),
    "plan_reviewer_a": (
        "Plan review lane A (independent reviewer, two-reviewer split)",
        ("T2", "T2", "T3")),
    "plan_reviewer_b": (
        "Plan review lane B (independent reviewer, two-reviewer split)",
        ("T2", "T2", "T3")),
    "coder": (
        "Implement the story on its branch against the frozen plan",
        ("T1", "T2", "T2")),
    "fixer": (
        "Post-review combined fix pass against enumerated findings",
        ("T1", "T2", "T2")),
    "code_reviewer": (
        "Internal code review of the story diff before external review",
        ("T2", "T2", "T2")),
    "behavior_reviewer": (
        "Internal behavior review against acceptance criteria",
        ("T2", "T2", "T2")),
    "contract_reviewer": (
        "Internal contract review: plan contract block vs shipped diff",
        ("T2", "T2", "T2")),
}

TIER_MATRIX: dict[str, tuple[str, str, str]] = {
    role: tiers for role, (_, tiers) in CANONICAL_ROLES.items()
}

# Alias → canonical id (hyphen variants + historical spellings). Lookup
# normalizes `_` → `-`, so snake_case and hyphen spellings both resolve.
ALIASES: dict[str, str] = {
    "context": "context_acquisition",
    "context-acquisition": "context_acquisition",
    "decomposer": "decomposer",
    "story-decomposer": "decomposer",
    "test-plan": "test_plan_generator",
    "test-plan-generator": "test_plan_generator",
    "planner": "planner",
    "implementation-planner": "planner",
    "impl-planner": "planner",
    "plan-reviewer": "plan_reviewer",
    "plan-reviewer-a": "plan_reviewer_a",
    "plan-reviewer-b": "plan_reviewer_b",
    "coder": "coder",
    "fixer": "fixer",
    "code-reviewer": "code_reviewer",
    "behavior-reviewer": "behavior_reviewer",
    "contract-reviewer": "contract_reviewer",
}


def canonical_role(name: str) -> str:
    """Canonicalize a role spelling to its snake_case id.

    Raises ValueError on unknown roles, listing the known canonical ids.
    """
    key = str(name).strip().lower().replace("_", "-")
    if key not in ALIASES:
        raise ValueError(
            f"unknown role {name!r}; known: {sorted(CANONICAL_ROLES)}")
    return ALIASES[key]


def resolve_tier(role: str, difficulty: str) -> str:
    """Return the tier (T1/T2/T3) for (role, difficulty S/M/L)."""
    if difficulty not in DIFFICULTIES:
        raise ValueError(
            f"difficulty must be one of {DIFFICULTIES}, got {difficulty!r}")
    return TIER_MATRIX[canonical_role(role)][DIFFICULTIES.index(difficulty)]
