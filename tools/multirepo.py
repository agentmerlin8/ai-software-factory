#!/usr/bin/env python3
"""tools/multirepo.py — multi-repo ledger schema (architecture §Multi-repo ledger schema).

Multi-repo stories touch N bound repos under ONE workspace story. This
module is the machine-readable form of the frozen §Multi-repo ledger schema
section: `repo@sha[:path]` pins, per-repo sub-events and their fold,
set-level completeness rules (which REFUSE — they never silently advance),
and corrections with repo scope.

Core rules:

  * Pins are `repo@sha[:path]`: repo = a workspace binding id
    (`^[a-z][a-z0-9-]*$`), sha = 7-40 lowercase hex, optional `:path` = a
    repo-relative path (no `..` segment, no leading `/`, non-empty after
    `:`). Resolution is an existence check against the bound repo's local
    checkout (`git cat-file -e <sha>^{commit}`) — never a fetch.
  * Sub-events are `transition` events carrying a non-null `repo`. They
    are per-repo local progress markers on SUB_STATE_LADDER; they NEVER
    change the story-level state
    (tools/ledger_schema.current_state excludes them).
  * Folding is per-repo max on the ladder (order-independent — duplicates
    and backtracks are fine). A `correction` event with a `repo` and a
    ladder `corrected_value` overrides that repo's folded value and sets
    corrected=true; a non-ladder corrected_value is ignored. Whether a
    correction is CORRECT is the controller's business, not a schema rule.
  * Set-level completeness: MERGE_ELIGIBLE requires every declared repo
    folded to >= READY_FOR_REVIEW; MERGED requires every repo >= MERGED;
    LIVE_PROVEN requires every repo >= MERGED plus recorded proof
    evidence. An incomplete set is refused with a named missing
    (repo, requirement) list — never silently advanced.

Pure functions except pin resolution, which shells out to git (stdlib
subprocess, no network). Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import subprocess
from typing import Any

from tools import ledger_schema
from tools import state_machine

# ---------------------------------------------------------------- pins ----

REPO_ID_RE = ledger_schema.REPO_ID_RE
PIN_SHA_RE = ledger_schema.PIN_SHA_RE


def parse_pin(pin: str) -> dict:
    """Parse a `repo@sha[:path]` pin into {"repo", "sha", "path"} (path None
    when absent). Raises ValueError("malformed repo@sha pin: <pin> (<reason>)")
    on any violation; the reason names the broken part: missing @, bad repo
    id, bad sha, path escapes via '..', absolute path, empty path."""
    if "@" not in pin:
        raise ValueError(f"malformed repo@sha pin: {pin} (missing @)")
    repo_part, rest = pin.split("@", 1)
    if not REPO_ID_RE.fullmatch(repo_part):
        raise ValueError(f"malformed repo@sha pin: {pin} "
                         f"(bad repo id '{repo_part}')")
    if ":" in rest:
        sha_part, path = rest.split(":", 1)
    else:
        sha_part, path = rest, None
    if not PIN_SHA_RE.fullmatch(sha_part):
        raise ValueError(f"malformed repo@sha pin: {pin} "
                         f"(bad sha '{sha_part}')")
    if path is not None and not path:
        raise ValueError(f"malformed repo@sha pin: {pin} (empty path)")
    if path is not None and path.startswith("/"):
        raise ValueError(f"malformed repo@sha pin: {pin} (absolute path)")
    if path is not None and ".." in path.split("/"):
        raise ValueError(f"malformed repo@sha pin: {pin} (path escapes via "
                         f"'..')")
    return {"repo": repo_part, "sha": sha_part, "path": path}


def validate_pin(pin: str) -> list[str]:
    """[] when the pin parses; otherwise the parse error as a one-item
    list (shape only — no resolution)."""
    try:
        parse_pin(pin)
    except ValueError as exc:
        return [str(exc)]
    return []


def resolve_pin(pin: str, repo_paths: dict[str, str]) -> list[str]:
    """Resolve `repo@sha[:path]` against the workspace bindings.

    Returns [] on success; otherwise a one-item error list. Shape errors
    pass through unchanged; an unknown repo names the workspace binding; a
    sha absent from the bound repo's checkout names the pin and the
    checkout path. Resolution is a local existence check
    (`git cat-file -e <sha>^{commit}`) — never a fetch.
    """
    errs = validate_pin(pin)
    if errs:
        return errs
    parsed = parse_pin(pin)
    repo = parsed["repo"]
    checkout = repo_paths.get(repo)
    if checkout is None:
        return [f"unknown repo '{repo}' — not in the workspace binding"]
    probe = subprocess.run(
        ["git", "-C", checkout, "cat-file", "-e",
         f"{parsed['sha']}^{{commit}}"],
        capture_output=True, text=True,
    )
    if probe.returncode != 0:
        return [f"unresolvable pin: {repo}@{parsed['sha']} "
                f"(not found in {checkout})"]
    return []


# ------------------------------------------------------- sub-events ----

# The only legal sub-event to_states — per-repo local progress markers.
# Story-level states (tools/state_machine.STATES) are unchanged; a
# sub-event records repo-local evidence, never a story transition.
SUB_STATE_LADDER = (
    "BUILD_GREEN", "DRAFT_OPENED", "FULL_TESTS_GREEN",
    "READY_FOR_REVIEW", "MERGED",
)


def validate_sub_event(evt: dict[str, Any]) -> list[str]:
    """Sub-event shape errors ([] = valid): event must be a transition with
    a non-null binding-id `repo`, a story-level from_state, and a ladder
    to_state."""
    errs: list[str] = []
    if evt.get("event") != "transition":
        return [f"sub-event: event {evt.get('event')!r} is not a transition"]
    repo = evt.get("repo")
    if not repo:
        errs.append("sub-event: repo must be a non-null binding id slug")
    elif not isinstance(repo, str) or not REPO_ID_RE.fullmatch(repo):
        errs.append(f"sub-event: repo {repo!r} is not a binding id slug")
    from_state = evt.get("from_state")
    if from_state not in state_machine.STATES:
        errs.append(f"sub-event: from_state {from_state!r} is not a "
                    "story-level state")
    to_state = evt.get("to_state")
    if to_state not in SUB_STATE_LADDER:
        errs.append(f"sub-event: to_state {to_state!r} is not a per-repo "
                    "ladder state")
    return errs


def sub_events(events: list[dict], story_id: str,
               repo: str | None = None) -> list[dict]:
    """Transition events for the story carrying a non-null `repo` (per-repo
    sub-events), in ledger order; optionally filtered to one repo.
    Sub-events never change the story-level state."""
    result: list[dict] = []
    for e in events:
        if e.get("event") != "transition" or e.get("story_id") != story_id \
                or not e.get("repo"):
            continue
        if repo is None or e["repo"] == repo:
            result.append(e)
    return result


def fold_repo_status(events: list[dict], story_id: str) -> dict[str, dict]:
    """Fold per-repo sub-event status: for each repo id, {"reached": highest
    ladder state reached or None, "sub_events": count, "corrected": bool}.

    Order-independent — the max ladder index wins, so unordered input,
    duplicates, and a lower state after a higher one are all fine. Then
    corrections with repo scope apply: a `correction` event with a `repo`
    and a ladder `corrected_value` overrides that repo's reached value and
    sets corrected=true; a non-ladder corrected_value is ignored (corrected
    stays false). Repos with no sub-events are absent from the result.
    """
    folded: dict[str, dict] = {}
    for e in sub_events(events, story_id):
        repo = e["repo"]
        entry = folded.setdefault(
            repo, {"reached": None, "sub_events": 0, "corrected": False})
        entry["sub_events"] += 1
        to_state = e.get("to_state")
        if to_state in SUB_STATE_LADDER:
            idx = SUB_STATE_LADDER.index(to_state)
            if entry["reached"] is None or \
                    idx > SUB_STATE_LADDER.index(entry["reached"]):
                entry["reached"] = to_state
    for e in events:
        if e.get("event") != "correction" or \
                e.get("story_id") != story_id or not e.get("repo"):
            continue
        repo = e["repo"]
        value = e.get("corrected_value")
        if repo not in folded or value not in SUB_STATE_LADDER:
            continue
        folded[repo]["reached"] = value
        folded[repo]["corrected"] = True
    return folded


# ------------------------------------------------- set completeness ----

SET_LEVEL_RULES: dict[str, dict] = {
    "MERGE_ELIGIBLE": {"min_reached": "READY_FOR_REVIEW"},
    "MERGED": {"min_reached": "MERGED"},
    "LIVE_PROVEN": {"min_reached": "MERGED", "requires_proof": True},
}


def set_completeness(events: list[dict], story_id: str,
                     repos: list[str], target_state: str, *,
                     proof_evidence: str = "") -> list[str]:
    """The refusal list for a set-level transition; [] = complete (pass).

    repos = the story's declared repo-id set, checked in the given order.
    Every repo must have folded to >= the target's minimum ladder state;
    LIVE_PROVEN additionally requires recorded proof evidence. Every
    missing (repo, requirement) is NAMED — a set-level transition refuses
    on an incomplete set and never silently advances. target_state outside
    SET_LEVEL_RULES raises ValueError.
    """
    rules = SET_LEVEL_RULES.get(target_state)
    if rules is None:
        raise ValueError(f"target_state {target_state!r} is not a set-level "
                         f"state; expected one of {list(SET_LEVEL_RULES)}")
    errors: list[str] = []
    folded = fold_repo_status(events, story_id)
    min_state = rules["min_reached"]
    min_idx = SUB_STATE_LADDER.index(min_state)
    for repo in repos:
        entry = folded.get(repo)
        reached = entry["reached"] if entry else None
        reached_idx = SUB_STATE_LADDER.index(reached) if reached else -1
        if reached_idx < min_idx:
            reached_label = reached if reached else "none"
            errors.append(f"repo '{repo}': reached {reached_label}, "
                          f"needs >= {min_state}")
    if rules.get("requires_proof") and not proof_evidence:
        errors.append("live proof evidence not recorded")
    return errors
