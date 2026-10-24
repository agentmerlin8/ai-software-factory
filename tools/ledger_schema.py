#!/usr/bin/env python3
"""tools/ledger_schema.py — event-ledger schema (extracted, genericized).

The schema is the contract: required fields per event type, the null+reason
law (both directions), enums, ID formats, and the ledger-wide invariants
(dispatch lifecycle, review-attribution joins). Pure validation functions —
no file I/O, no network, deterministic. Append/load I/O ports connect this
schema to the control plane at S2.

Core rules enforced here:
  * Every event carries the envelope: event_id, recorded_at, provenance
    (live | backfilled).
  * Every event type has a required-field set; missing fields are rejected.
    A required field's value may be None only when the field is nullable.
  * Missing values are `null` PLUS a sibling `<field>_reason` (non-empty
    string) — never invented zeros. The reason must be null whenever the
    field has a value (both directions enforced). A `<field>_reason` for a
    NON-nullable required field is itself a violation. Event-specific value
    fields (`blocked_reason`, `fallback_reason`, `root_cause_one_line`) are
    the value itself and are exempt from the sibling convention.
  * `assignment` is ONE event type with a per-op field set (claim ·
    handover · release). A claim pins its start with EXACTLY ONE of
    `start_sha` (single-repo) or `start_shas` (multi-repo map repo id →
    7-40 hex, non-empty). "One open claim per story" and refusal
    semantics are CONTROLLER rules (S2), not schema rules.
  * Multi-repo: a `transition` event with a non-null `repo` is a per-repo
    sub-event — it lives on the per-repo ladder (tools/multirepo.py) and
    NEVER changes the story-level state (current_state excludes it).
  * A finding with class "nit" must carry logic_touched=false AND a
    verifying diff/commit (fix_sha) or validation evidence;
    logic_touched=true can never be class nit (schema-level ban).
  * dispatch_id format: <story>-<role>-<attempt>-<unix>, recorded BEFORE
    the dispatch is spawned; a dispatch already recorded `ended` can never
    be re-started by a stale notification.
  * human_gate pending/answered contract: an answered gate
    (`answered_at` set) requires `decision` ∈ {approved, rejected} AND
    `answered_by` (the responding human); a pending gate carries neither.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------- schema ----

EVENT_TYPES = (
    "assignment", "dispatch", "usage", "stage", "finding", "loop",
    "recovery", "transition", "human_gate", "outcome", "correction",
    "deviation", "review_context", "review_attribution",
)

# assignment ops: one event type, per-op field sets. `handover` is an
# assignment op — it supersedes the reference's separate telemetry-laden
# assignment rows; claim-lifecycle rules (uniqueness, refusal) live in the
# controller (S2).
ASSIGNMENT_OPS = ("claim", "handover", "release")
ASSIGNMENT_OP_FIELDS: dict[str, tuple[str, ...]] = {
    # claim's start pin is the exactly-one-of rule: start_sha | start_shas
    # (validated in _claim_start_errors, not as a plain required field)
    "claim": ("owner", "host", "session", "claimed_at"),
    "handover": ("from", "to", "reason", "flagged"),
    "release": ("owner",),
}

# Required fields per event type. Envelope fields (event, event_id,
# recorded_at, provenance) are required on EVERY event and checked
# separately.
REQUIRED: dict[str, tuple[str, ...]] = {
    "assignment": ("story_id", "op"),
    "dispatch": (
        "story_id", "role", "requested_model", "reported_model",
        "thinking_setting", "session_id", "parent_session_id", "attempt",
        "started_at", "ended_at", "outcome", "prompt_version", "worktree",
        "model_unverified",
    ),
    "usage": (
        "dispatch_id", "input_tokens", "output_tokens", "cached_input_tokens",
        "thinking_tokens", "token_semantics", "usage_source",
    ),
    "stage": (
        "story_id", "stage", "started_at", "ended_at", "commit_sha",
        "tests_passed", "tests_failed", "tests_skipped", "tests_excluded",
        "evidence_path",
    ),
    "finding": (
        "finding_id", "story_id", "source", "reviewed_sha", "introduced_sha",
        "class", "severity", "disposition", "logic_touched", "overlap_id",
        "fixer", "fix_sha", "validation", "evidence_path",
    ),
    "loop": ("story_id", "stage", "loop_n", "class", "root_cause_one_line"),
    "recovery": (
        "story_id", "original_assignment", "fallback_reason", "actual_model",
        "remaining_scope", "consumed_loop_count", "additional_cost",
        "human_intervention",
    ),
    "transition": (
        "story_id", "from_state", "to_state", "requested_by", "checks_run",
        "check_results", "approved_by",
    ),
    "human_gate": ("story_id", "gate", "question", "asked_at", "answered_at",
                   "decision", "answered_by"),
    "outcome": (
        "story_id", "merged_sha", "merged_at", "live_proof_status",
        "observation_end", "escaped_defects", "totals", "recommendation",
    ),
    "correction": (
        "story_id", "contradicted_claim", "corrected_value",
        "source_citation", "corrected_artifact",
    ),
    "deviation": (
        "story_id", "contract_field", "planned", "actual", "justification",
        "approved_by",
    ),
    "review_context": (
        "story_id", "review_role", "reviewed_sha", "base_sha",
        "context_manifest_path", "context_manifest_digest", "prompt_version",
        "dispatch_id", "delivery_surface", "requested_at",
    ),
    "review_attribution": (
        "story_id", "finding_id", "internal_context_event_id",
        "external_context_event_id", "classification", "classified_by",
        "evidence_path",
    ),
}

# Fields that may legitimately be null. For each, the schema REQUIRES a
# sibling `<field>_reason`: non-empty string when the field is null, null
# when the field has a value. Both directions are enforced.
NULLABLE: dict[str, frozenset[str]] = {
    "assignment": frozenset(),
    "dispatch": frozenset({"reported_model", "thinking_setting", "session_id",
                           "parent_session_id", "ended_at", "outcome",
                           "worktree", "requested_model"}),
    "usage": frozenset({"cached_input_tokens", "thinking_tokens",
                        "dispatch_id"}),
    "stage": frozenset({"started_at", "ended_at", "commit_sha", "tests_passed",
                        "tests_failed", "tests_skipped", "tests_excluded",
                        "evidence_path"}),
    "finding": frozenset({"reviewed_sha", "introduced_sha", "overlap_id",
                          "fixer", "fix_sha", "validation", "evidence_path",
                          "severity"}),
    "loop": frozenset(),
    "recovery": frozenset({"actual_model", "remaining_scope",
                           "additional_cost", "original_assignment"}),
    "transition": frozenset({"approved_by", "repo"}),
    "human_gate": frozenset({"answered_at", "story_id", "decision",
                             "answered_by"}),
    "outcome": frozenset({"merged_sha", "merged_at", "live_proof_status",
                          "observation_end", "escaped_defects", "totals",
                          "recommendation"}),
    "correction": frozenset({"story_id", "repo"}),
    "deviation": frozenset({"approved_by"}),
    "review_context": frozenset({"dispatch_id"}),
    "review_attribution": frozenset(),
}

# ----------------------------------------------------------------- enums ----

FINDING_SOURCES = frozenset({"internal", "external", "human", "orchestrator",
                             "script_gate"})
FINDING_CLASSES = frozenset({"mechanical", "reasoning", "contract", "nit",
                             "design-invalidating"})
FINDING_DISPOSITIONS = frozenset({"accepted", "dismissed", "deferred",
                                  "fixed", "fix_in_revision", "record_only"})
HUMAN_GATE_KINDS = frozenset({"visual", "merge", "loop_cap", "scope",
                              "safety", "meta", "avoidable", "spec_freeze"})
HUMAN_GATE_DECISIONS = ("approved", "rejected")
REVIEW_ROLES = frozenset({"behavior_reviewer", "contract_reviewer",
                          "external_review"})
REVIEW_DELIVERY_SURFACES = frozenset({"dispatch-package", "pr-full-diff"})
REVIEW_ATTRIBUTIONS = frozenset({"MISSED_WITH_CONTEXT", "CONTEXT_ABSENT",
                                 "INTRODUCED_AFTER_INTERNAL"})
PROVENANCES = frozenset({"live", "backfilled"})

# ------------------------------------------------------------- id formats ---

STORY_ID_RE = re.compile(r"^STORY-\d{3}$")           # canonical form
DISPATCH_ID_RE = re.compile(r"^.+-[a-z_]+-\d+-\d+$")  # <story>-<role>-<attempt>-<unix>
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")  # workspace binding id (pins, start_shas keys)
PIN_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")   # pin SHAs (repo@sha[:path], start_shas values)

# Value fields that ARE the reason — no `<field>_reason` sibling convention.
_NULL_REASON_EXEMPT = {"blocked_reason", "fallback_reason",
                       "root_cause_one_line"}


def _claim_start_errors(evt: dict[str, Any]) -> list[str]:
    """Exactly-one-of `start_sha` | `start_shas` on a claim + map shape.

    `start_sha` (single-repo) is the classic pin; `start_shas` is the
    multi-repo map repo-id → 7-40 lowercase hex commit (non-empty). Each
    refusal names the broken part. `start_sha` is deliberately NOT a plain
    required field — this exactly-one rule replaces that check.
    """
    has_sha = "start_sha" in evt
    has_shas = "start_shas" in evt
    if not has_sha and not has_shas:
        return ["assignment: claim requires start_sha or start_shas "
                "(exactly one)"]
    if has_sha and has_shas:
        return ["assignment: claim takes exactly one of start_sha, "
                "start_shas"]
    if has_sha:
        sha = evt.get("start_sha")
        if not isinstance(sha, str) or not PIN_SHA_RE.fullmatch(sha):
            return ["assignment: claim start_sha must be a 7-40 lowercase "
                    "hex SHA"]
        return []
    shas = evt.get("start_shas")
    if not isinstance(shas, dict):
        return ["assignment: claim start_shas must be an object mapping "
                "repo ids to commit SHAs"]
    if not shas:
        return ["assignment: claim start_shas must be non-empty"]
    errs: list[str] = []
    for repo_id, sha in shas.items():
        if not isinstance(repo_id, str) or not REPO_ID_RE.fullmatch(repo_id):
            errs.append(f"assignment: claim start_shas key {repo_id!r} "
                        "is not a valid repo id")
        if not isinstance(sha, str) or not PIN_SHA_RE.fullmatch(sha):
            errs.append(f"assignment: claim start_shas[{repo_id!r}] value "
                        "must be a 7-40 lowercase hex SHA")
    return errs


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ validation ----

def validate_event(evt: dict[str, Any]) -> list[str]:
    """Return a list of schema errors ([] = valid). Pure function."""
    errs: list[str] = []
    etype = evt.get("event")
    if etype not in EVENT_TYPES:
        return [f"unknown event type {etype!r}"]

    # envelope
    for f in ("event_id", "recorded_at", "provenance"):
        if not evt.get(f):
            errs.append(f"{etype}: missing envelope field `{f}`")
    recorded_at = evt.get("recorded_at")
    if recorded_at:
        try:
            datetime.strptime(str(recorded_at), "%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            errs.append(f"{etype}: recorded_at must be a UTC timestamp "
                        "(YYYY-MM-DDTHH:MM:SSZ)")
    if evt.get("provenance") and evt["provenance"] not in PROVENANCES:
        errs.append(f"{etype}: provenance must be live|backfilled")

    # required fields present (value may be None only if nullable)
    required = list(REQUIRED[etype])
    if etype == "assignment":
        op = evt.get("op")
        if op in ASSIGNMENT_OP_FIELDS:
            required.extend(ASSIGNMENT_OP_FIELDS[op])
    for f in required:
        if f not in evt:
            errs.append(f"{etype}: missing required field `{f}`")
        elif evt[f] is None and f not in NULLABLE[etype]:
            errs.append(f"{etype}: required field `{f}` must not be null "
                        "(not nullable in this event type)")

    # null ⇄ reason convention, both directions
    nullable = NULLABLE[etype]
    for f in sorted(nullable):
        reason_key = f"{f}_reason"
        if reason_key not in evt:
            errs.append(f"{etype}: nullable field `{f}` requires sibling "
                        f"`{reason_key}`")
            continue
        val, reason = evt.get(f), evt.get(reason_key)
        if val is None:
            if not isinstance(reason, str) or not reason.strip():
                errs.append(f"{etype}: `{f}` is null but `{reason_key}` is "
                            "not a non-empty string (never invent zeros)")
        else:
            if reason is not None:
                errs.append(f"{etype}: `{f}` has a value so `{reason_key}` "
                            "must be null")
    # any *_reason field for a NON-nullable required field is a violation
    # (assignment op-fields also count as required fields here)
    required_bases = set(REQUIRED.get(etype, ()))
    if etype == "assignment" and evt.get("op") in ASSIGNMENT_OP_FIELDS:
        required_bases |= set(ASSIGNMENT_OP_FIELDS[evt["op"]])
    for key in evt:
        if key.endswith("_reason") and key not in _NULL_REASON_EXEMPT:
            base = key[: -len("_reason")]
            if base not in nullable and base in required_bases:
                errs.append(f"{etype}: `{key}` present but `{base}` is not "
                            "nullable in this event type")

    # type-specific rules
    if etype == "assignment":
        op = evt.get("op")
        if op not in ASSIGNMENT_OPS:
            errs.append(f"assignment: op {op!r} not in {list(ASSIGNMENT_OPS)}")
        elif op == "handover" and not isinstance(evt.get("flagged"), bool):
            errs.append("assignment: handover `flagged` must be an explicit "
                        "boolean (abrupt takeover = flagged handover)")
        elif op == "claim":
            errs.extend(_claim_start_errors(evt))
    if etype == "dispatch":
        did = evt.get("event_id", "")
        if not DISPATCH_ID_RE.match(str(did)):
            errs.append(f"dispatch: event_id {did!r} does not match "
                        "<story>-<role>-<attempt>-<unix> (pre-spawn ID)")
        else:
            prefix = f"{evt.get('story_id')}-{evt.get('role')}-" \
                     f"{evt.get('attempt')}-"
            if not str(did).startswith(prefix):
                errs.append("dispatch: event_id must match the row's "
                            f"story/role/attempt (id {did!r})")
        if evt.get("reported_model") is None and not evt.get("model_unverified"):
            errs.append("dispatch: unconfirmed reported_model requires "
                        "model_unverified=true")
    if etype == "finding":
        cls = evt.get("class")
        if cls not in FINDING_CLASSES:
            errs.append(f"finding: class {cls!r} not in "
                        f"{sorted(FINDING_CLASSES)}")
        if evt.get("source") not in FINDING_SOURCES:
            errs.append(f"finding: source {evt.get('source')!r} not in "
                        f"{sorted(FINDING_SOURCES)}")
        if evt.get("disposition") not in FINDING_DISPOSITIONS:
            errs.append(f"finding: disposition {evt.get('disposition')!r} "
                        f"not in {sorted(FINDING_DISPOSITIONS)}")
        lt = evt.get("logic_touched")
        if not isinstance(lt, bool):
            errs.append("finding: logic_touched must be an explicit boolean")
        if cls == "nit":
            if lt is not False:
                errs.append("finding: a fix that changes logic is NEVER "
                            "class nit (logic_touched must be false)")
            if not evt.get("fix_sha") and not evt.get("validation"):
                errs.append("finding: nit requires verifying diff/commit "
                            "(fix_sha) or validation evidence")
        if lt is True and cls == "nit":
            errs.append("finding: logic_touched=true + class=nit is forbidden")
    if etype == "human_gate":
        if evt.get("gate") not in HUMAN_GATE_KINDS:
            errs.append(f"human_gate: gate {evt.get('gate')!r} not in "
                        f"{sorted(HUMAN_GATE_KINDS)}")
        # pending/answered contract: an answered gate records its decision
        # and responding human; a pending gate carries neither.
        if evt.get("answered_at") is not None:
            if evt.get("decision") not in HUMAN_GATE_DECISIONS:
                errs.append("human_gate: an answered gate requires decision "
                            "in (approved, rejected)")
            ab = evt.get("answered_by")
            if not isinstance(ab, str) or not ab.strip():
                errs.append("human_gate: an answered gate requires "
                            "answered_by (the responding human)")
        elif evt.get("decision") is not None or evt.get("answered_by") is not None:
            errs.append("human_gate: a pending gate must not carry "
                        "decision/answered_by")
    if etype == "transition":
        checks_run = evt.get("checks_run")
        if not isinstance(checks_run, list) or \
                not all(isinstance(c, str) for c in checks_run):
            errs.append("transition: checks_run must be an array of strings")
        if not isinstance(evt.get("check_results"), dict):
            errs.append("transition: check_results must be an object")
    if etype == "loop":
        if not isinstance(evt.get("root_cause_one_line"), str) or \
                not evt["root_cause_one_line"].strip():
            errs.append("loop: root_cause_one_line must be a non-empty "
                        "string (tagged at the moment it happens — never "
                        "backfilled)")
    if etype == "review_context":
        if evt.get("review_role") not in REVIEW_ROLES:
            errs.append(f"review_context: review_role "
                        f"{evt.get('review_role')!r} not in "
                        f"{sorted(REVIEW_ROLES)}")
        if evt.get("delivery_surface") not in REVIEW_DELIVERY_SURFACES:
            errs.append("review_context: delivery_surface must identify the "
                        "exact dispatch-package or PR full-diff surface")
        for field in ("reviewed_sha", "base_sha"):
            if not FULL_SHA_RE.fullmatch(str(evt.get(field, ""))):
                errs.append(f"review_context: {field} must be a full "
                            "lowercase 40-character commit SHA")
        digest = str(evt.get("context_manifest_digest", ""))
        if not SHA256_RE.fullmatch(digest):
            errs.append("review_context: context_manifest_digest must be "
                        "full lowercase SHA-256")
        expected_prefix = f"stories/{evt.get('story_id')}/"
        manifest_path = str(evt.get("context_manifest_path", ""))
        if not manifest_path.startswith(expected_prefix) or \
                ".." in manifest_path:
            errs.append("review_context: context_manifest_path must stay "
                        f"under {expected_prefix}")
    if etype == "review_attribution":
        if evt.get("classification") not in REVIEW_ATTRIBUTIONS:
            errs.append(f"review_attribution: classification "
                        f"{evt.get('classification')!r} not in "
                        f"{sorted(REVIEW_ATTRIBUTIONS)}")
    return errs


def validate_events(events: list[dict[str, Any]]) -> list[str]:
    """Validate each event + the ledger-wide invariants.

    Dispatch lifecycle: the dispatch_id is the MATCHING KEY, so a dispatch
    legitimately occupies up to TWO rows sharing one event_id — the
    pre-spawn record (ended_at null) and its completion record (ended_at
    set), in that order. A backfilled dispatch may be a single ended row.
    Anything else sharing an event_id is an error: >2 rows, a second
    completion (stale re-start), an orphan completion, or reversed order.
    Non-dispatch event_ids are strictly unique.

    Review-attribution joins: internal_context_event_id must reference a
    behavior_reviewer review_context; external_context_event_id must
    reference an external_review review_context; both from the same story;
    classifications other than INTRODUCED_AFTER_INTERNAL require identical
    reviewed_sha on both contexts; finding_id must reference an
    external-source finding of the same story.
    """
    errs: list[str] = []
    seen_ids: set[str] = set()
    rows_by_id: dict[str, list[dict[str, Any]]] = {}
    for evt in events:
        errs.extend(validate_event(evt))
        eid = evt.get("event_id")
        if isinstance(eid, str):
            etype = evt.get("event")
            rows_by_id.setdefault(eid, []).append(evt)
            if etype != "dispatch":
                if eid in seen_ids:
                    errs.append(f"duplicate event_id {eid!r}")
                seen_ids.add(eid)
    for eid, rows in rows_by_id.items():
        if len({r.get("event") for r in rows}) > 1:
            errs.append(f"duplicate event_id {eid!r} (spans multiple event "
                        "types)")
            continue
        if rows[0].get("event") != "dispatch":
            continue
        if len(rows) > 2:
            errs.append(f"dispatch {eid} has {len(rows)} rows — lifecycle is "
                        "at most pre-spawn + completion")
            continue
        ended = [r for r in rows if r.get("ended_at")]
        if len(ended) > 1:
            errs.append(f"dispatch {eid} re-started after end "
                        "(stale-notification class)")
        elif len(rows) == 1 and rows[0].get("ended_at") and \
                rows[0].get("provenance") != "backfilled":
            errs.append(f"dispatch {eid} single completed row must be "
                        "provenance=backfilled (a live dispatch requires "
                        "its pre-spawn row)")
        elif len(rows) == 2 and not rows[1].get("ended_at"):
            errs.append(f"dispatch {eid} second row lacks ended_at — a "
                        "pre-spawn row may only be followed by its "
                        "completion")
        elif len(rows) == 2 and rows[0].get("ended_at"):
            errs.append(f"dispatch {eid} completion row precedes its "
                        "pre-spawn row — only pre-spawn (ended_at null) may "
                        "be followed by a completion")

    context_by_id = {e.get("event_id"): e for e in events
                     if e.get("event") == "review_context"}
    # finding index keyed by (story_id, finding_id) — finding ids are only
    # unique WITHIN a story, so two stories may both carry an `F-1`.
    finding_by_id = {(e.get("story_id"), e.get("finding_id")): e
                     for e in events if e.get("event") == "finding"}
    for evt in events:
        if evt.get("event") != "review_attribution":
            continue
        internal = context_by_id.get(evt.get("internal_context_event_id"))
        external = context_by_id.get(evt.get("external_context_event_id"))
        if internal is None or internal.get("review_role") != "behavior_reviewer":
            errs.append("review_attribution: internal_context_event_id must "
                        "reference a behavior_reviewer review_context event")
        if external is None or external.get("review_role") != "external_review":
            errs.append("review_attribution: external_context_event_id must "
                        "reference an external_review review_context event")
        if internal and internal.get("story_id") != evt.get("story_id"):
            errs.append("review_attribution: internal context belongs to "
                        "another story")
        if external and external.get("story_id") != evt.get("story_id"):
            errs.append("review_attribution: external context belongs to "
                        "another story")
        if internal and external and \
                evt.get("classification") != "INTRODUCED_AFTER_INTERNAL" and \
                internal.get("reviewed_sha") != external.get("reviewed_sha"):
            errs.append("review_attribution: capability/context "
                        "classifications require identical internal and "
                        "external snapshots")
        finding = finding_by_id.get((evt.get("story_id"),
                                     evt.get("finding_id")))
        if finding is None:
            # Precise miss reporting: the same finding_id under another
            # story (external source) is a same-story violation, not a
            # dangling reference.
            elsewhere = any(f.get("finding_id") == evt.get("finding_id")
                            for f in finding_by_id.values())
            if elsewhere:
                errs.append("review_attribution: finding must be an "
                            "external-source finding from the same story")
            else:
                errs.append("review_attribution: finding_id must reference "
                            "a finding event in the ledger")
        elif finding.get("source") != "external":
            errs.append("review_attribution: finding must be an "
                        "external-source finding from the same story")
    return errs


# ---------------------------------------------------------------- helpers ----

def events_for(events: list[dict], story_id: str,
               etype: str | None = None) -> list[dict]:
    return [e for e in events if e.get("story_id") == story_id
            and (etype is None or e.get("event") == etype)]


def current_state(events: list[dict], story_id: str) -> str | None:
    """Story-level state = latest to_state among transition events WITHOUT a
    repo tag (None if never transitioned). Repo-tagged transition events are
    per-repo sub-events (tools/multirepo.py) — they never change the story
    state."""
    state = None
    for e in events_for(events, story_id, "transition"):
        if e.get("repo"):
            continue
        state = e["to_state"]
    return state


def latest_findings(events: list[dict],
                    story_id: str | None = None) -> list[dict]:
    """Return one latest lifecycle row per (story_id, finding_id), in
    first-seen order — finding ids are only unique within a story, so the
    story is part of the lifecycle key and cross-story rows never fold
    together."""
    order: list[tuple[str, str]] = []
    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        if event.get("event") != "finding" or \
                (story_id is not None and event.get("story_id") != story_id):
            continue
        key = (str(event.get("story_id")),
               str(event.get("finding_id") or event.get("event_id")))
        if key not in latest:
            order.append(key)
        latest[key] = event
    return [latest[key] for key in order]
