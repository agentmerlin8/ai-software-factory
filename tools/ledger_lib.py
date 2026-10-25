#!/usr/bin/env python3
"""tools/ledger_lib.py — event-ledger I/O: append/load + lifecycle laws.

The I/O half of the ledger port (architecture.md §Data model — the ledger,
§Ledger event schemas). The schema half ships in `tools/ledger_schema.py` —
the single validation authority. This module imports and calls it; it never
re-implements a schema rule.

Laws enforced at the I/O boundary:

  * The ledger is append-only JSON Lines: one JSON object per line, UTC
    timestamps (`YYYY-MM-DDTHH:MM:SSZ`), stable event ids, LF endings,
    trailing newline. Corrections are events, never edits.
  * `append_event` validates BEFORE writing: the single-event schema via
    ledger_schema, the stale-restart check, the CANDIDATE ledger (existing
    rows + the new row must pass the same cross-row laws `_all_errors`
    enforces at load), and the file's trailing newline — a refused event
    never touches the file.
  * `load_events` parses and then validates the whole ledger — schema plus
    the cross-row laws ledger_schema enforces on a snapshot: the dispatch
    two-row lifecycle (pre-spawn then completion; reversed order, stale
    re-start, orphan completion refused) and non-dispatch event-id
    uniqueness. Refusals are named `LedgerError`s listing every violation.
  * Field-set strictness (S2): an event may carry only allowed TOP-LEVEL
    keys — envelope ∪ schema tables ∪ `<field>_reason` siblings ∪ claim
    pins ∪ the documented ANNOTATIONS map. Anything else refuses with
    `unknown field: <event-type>.<field>` (e.g. a cost field on a usage
    row); nested objects (`check_results`) stay free-form.
  * Dispatch ids are pre-spawn: `<story>-<role>-<attempt>-<unix>`, supplied
    by the caller; a dispatch already recorded `ended` can never be
    re-started by a stale notification. Other event ids are auto-assigned
    when absent.
  * No default paths, no cwd assumptions: every function takes an explicit
    ledger path.

Determinism: same append order → same generated event ids
(`<type>-<story>-<seq>`). Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any

try:
    from . import ledger_schema as S
except ImportError:
    import ledger_schema as S

# Re-exports: ledger_schema is the validation authority — callers may use
# either import path, but there is exactly one implementation.
EVENT_TYPES = S.EVENT_TYPES
NULLABLE = S.NULLABLE
validate_event = S.validate_event
validate_events = S.validate_events
events_for = S.events_for
current_state = S.current_state
latest_findings = S.latest_findings


class LedgerError(ValueError):
    """Named refusal from the ledger I/O boundary (message lists every
    violation; nothing is written when append refuses)."""


# --------------------------------------------- field-set strictness (S2) ----
#
# architecture.md §Ledger event schemas (the `usage` row): the schema
# defines a tokens-only field set — no cost fields (kit law) — and
# extra-key rejection is controller-side (S2). It is implemented HERE,
# at the I/O boundary; ledger_schema.py stays the frozen validation
# authority for everything it already checks. Only TOP-LEVEL keys are
# policed; nested objects (e.g. transition `check_results`) stay
# free-form.

ENVELOPE_FIELDS = frozenset({"event", "event_id", "recorded_at",
                             "provenance"})

# Claim pins derived by inspecting the schema's exactly-one-of validator:
# `start_sha` | `start_shas` are checked in S._claim_start_errors, not in
# the REQUIRED table — so they are recovered from its source, never
# hand-typed here.
CLAIM_PIN_FIELDS = frozenset(
    re.findall(r'"(start_shas?)"', inspect.getsource(S._claim_start_errors)))

# Sanctioned extras beyond the schema tables (frozen initial entries;
# extend only with a documented reason per key):
ANNOTATIONS: dict[str, frozenset[str]] = {
    # controller.py writes `requested_by` on every assignment row (who
    # asked for the claim/handover/release) and annotates an
    # OVERRIDE-sanctioned claim with `override_used` {scope, by, blob_sha}.
    "assignment": frozenset({"requested_by", "override_used"}),
    # controller.py carries the same override annotation on transitions.
    "transition": frozenset({"override_used"}),
}


def allowed_fields(etype: str) -> frozenset[str]:
    """Allowed TOP-LEVEL keys for one event type: envelope ∪ the schema's
    required/nullable tables ∪ `<field>_reason` siblings ∪ per-op
    assignment fields ∪ claim pins ∪ ANNOTATIONS. Derived from
    ledger_schema — this module never re-types a schema rule."""
    if etype not in S.EVENT_TYPES:
        return frozenset()
    nullable = S.NULLABLE.get(etype, frozenset())
    allowed = (ENVELOPE_FIELDS
               | set(S.REQUIRED.get(etype, ()))
               | set(nullable)
               | {f"{f}_reason" for f in nullable}
               | set(ANNOTATIONS.get(etype, frozenset())))
    if etype == "assignment":
        for op_fields in S.ASSIGNMENT_OP_FIELDS.values():
            allowed |= set(op_fields)
        allowed |= CLAIM_PIN_FIELDS
    return frozenset(allowed)


def unknown_field_errors(evt: Any) -> list[str]:
    """S2 extra-key rejection: one `unknown field: <type>.<field>` per
    disallowed top-level key (sorted — deterministic). Non-dict rows and
    unknown event types are left to the schema's own messages."""
    if not isinstance(evt, dict):
        return []
    etype = evt.get("event")
    if etype not in S.EVENT_TYPES:
        return []
    allowed = allowed_fields(etype)
    return [f"unknown field: {etype}.{key}"
            for key in sorted(evt) if key not in allowed]


def _all_errors(events: list[Any]) -> list[str]:
    """Schema validation + the S2 unknown-field check in one error list
    (the load-side path shared by load_events and validate_ledger)."""
    errs = S.validate_events(events)
    for evt in events:
        errs.extend(unknown_field_errors(evt))
    return errs


def utcnow() -> str:
    """UTC timestamp `YYYY-MM-DDTHH:MM:SSZ` (delegates to ledger_schema)."""
    return S.utcnow()


# ------------------------------------------------------------------ load ----

def load_ledger(ledger_path: Path | str) -> list[dict[str, Any]]:
    """Parse the JSONL ledger WITHOUT validation (raw row access).

    A missing file is an empty ledger. Blank lines are skipped. Invalid
    JSON refuses with `<path>:<line>: invalid JSON: <detail>`. Read
    failures (invalid UTF-8, a directory path) propagate raw here —
    `load_events`/`validate_ledger` wrap or fold them.
    """
    p = Path(ledger_path)
    if not p.exists():
        return []
    events: list[dict[str, Any]] = []
    # Split on LF ONLY: append_event writes ensure_ascii=False, so a
    # schema-valid string can carry literal U+2028/U+2029/U+0085 — and
    # splitlines() would tear that JSON record apart at those boundaries.
    for i, line in enumerate(
            p.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise LedgerError(f"{p}:{i}: invalid JSON: {e}") from e
    return events


def load_events(ledger_path: Path | str) -> list[dict[str, Any]]:
    """Parse + validate the ledger; return the event list.

    Validation is ledger_schema.validate_events: every single-event schema
    rule plus the cross-row laws (dispatch two-row lifecycle order —
    pre-spawn then completion; reversed order, stale re-start, orphan
    completion, and >2 rows refused; non-dispatch event-id uniqueness;
    review-attribution joins). The S2 unknown-field check
    (`unknown_field_errors`) joins the same error list. Refuses with
    LedgerError listing every violation; use `validate_ledger` for a
    non-raising error list.

    R7-H2: unreadable ledgers (invalid UTF-8, a directory passed as the
    path, other read failures) refuse with a named
    `LedgerError("ledger unreadable: …")` so the controller/render/CLI
    LedgerError handlers see them — raw decoding/filesystem exceptions
    never escape this boundary.
    """
    try:
        events = load_ledger(ledger_path)
    except (UnicodeDecodeError, OSError) as e:
        raise LedgerError(f"ledger unreadable: {e}") from e
    errs = _all_errors(events)
    if errs:
        raise LedgerError("ledger rejected:\n  " + "\n  ".join(errs))
    return events


def validate_ledger(ledger_path: Path | str) -> list[str]:
    """Validate every row + the ledger-wide laws; [] = valid. Never raises
    on content: parse failures (invalid JSON, invalid UTF-8) come back as
    the diagnostic list (R7-H3). Only filesystem errors (OSError, e.g. a
    directory passed as the path) propagate."""
    try:
        events = load_ledger(ledger_path)
    except LedgerError as e:
        return [str(e)]
    except UnicodeDecodeError as e:
        return [f"ledger unreadable: {e}"]
    return _all_errors(events)


# ---------------------------------------------------------------- append ----

def _next_seq(events: list[dict], etype: str, story_id: Any) -> int:
    n = sum(1 for e in events
            if isinstance(e, dict) and e.get("event") == etype
            and e.get("story_id") == story_id)
    return n + 1


def seen_ended(events: list[dict], evt: dict) -> str:
    """Return evt['event_id'] if an ENDED dispatch with that id already
    exists (a stale notification can never re-start it), else ''."""
    if evt.get("event") != "dispatch":
        return ""
    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("event") == "dispatch" and \
                e.get("event_id") == evt.get("event_id") and \
                e.get("ended_at") and not evt.get("ended_at"):
            return e["event_id"]
    return ""


def append_event(ledger_path: Path | str, event: dict[str, Any]) -> None:
    """Validate + append exactly one JSON line to the ledger.

    Validation is ledger_schema.validate_event, the stale-restart check
    (`seen_ended`), the CANDIDATE ledger (existing rows + this event must
    satisfy the same cross-row laws load enforces — duplicate
    non-dispatch ids, a second dispatch completion, and orphan live
    completions refuse here, at append time), and the file's trailing
    newline (a terminator-less ledger refuses — repair before appending);
    on refusal nothing is written and LedgerError lists
    every violation. Convenience defaults applied before validation:
    a null-valued nullable field's missing `<field>_reason` sibling is NOT
    invented (the null+reason law stays the caller's job), but a
    value-carrying nullable field gets its reason sibling defaulted to
    null; absent `recorded_at` defaults to `utcnow()`, absent `provenance`
    to `live`. Non-dispatch events without an `event_id` get the
    deterministic `<type>-<story>-<seq>` id; dispatch events MUST carry
    their pre-spawn dispatch_id (`<story>-<role>-<attempt>-<unix>`).
    """
    if not isinstance(event, dict):
        raise LedgerError(f"event must be an object, got "
                          f"{type(event).__name__}")
    p = Path(ledger_path)
    events = load_ledger(p)
    etype = event.get("event")
    if etype not in S.EVENT_TYPES:
        raise LedgerError(f"unknown event type {etype!r}")
    evt = dict(event)
    # when a nullable field carries a value, its reason sibling must be null
    for f in S.NULLABLE.get(etype, frozenset()):
        if evt.get(f) is not None and f"{f}_reason" not in evt:
            evt[f"{f}_reason"] = None
    if etype == "dispatch" and not evt.get("event_id"):
        raise LedgerError("dispatch events must carry their pre-spawn "
                          "dispatch_id (<story>-<role>-<attempt>-<unix>)")
    if not evt.get("event_id"):
        sid = evt.get("story_id") or "global"
        evt["event_id"] = (f"{etype}-{sid}-"
                           f"{_next_seq(events, etype, evt.get('story_id'))}")
    evt.setdefault("recorded_at", utcnow())
    evt.setdefault("provenance", "live")
    unknown = unknown_field_errors(evt)
    if unknown:
        raise LedgerError("event rejected:\n  " + "\n  ".join(unknown))
    ended_id = seen_ended(events, evt)
    if ended_id:
        raise LedgerError(f"event_id {ended_id} already recorded as ended — "
                          "stale notifications cannot re-start it")
    errs = S.validate_event(evt)
    if errs:
        raise LedgerError("event rejected:\n  " + "\n  ".join(errs))
    # Candidate-ledger check (fail closed): the ledger PLUS the new row must
    # satisfy the same cross-row laws load enforces — a duplicate
    # non-dispatch id, a second dispatch completion, or an orphan live
    # completion is refused HERE, at append time, never written.
    cand_errs = _all_errors(events + [evt])
    if cand_errs:
        raise LedgerError("candidate ledger rejected:\n  "
                          + "\n  ".join(cand_errs))
    if p.exists():
        raw = p.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerError(f"ledger {p} is missing its trailing newline "
                              "— repair the file before appending")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(evt, sort_keys=False, ensure_ascii=False) + "\n")
